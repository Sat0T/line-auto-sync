/** New, independent Apps Script project only. Never paste into personal GAS. */
function lasConfig_() {
  var p = PropertiesService.getScriptProperties(), c = {
    token: p.getProperty('LAS_TOKEN'), source: p.getProperty('LAS_SOURCE'),
    owner: p.getProperty('LAS_OWNER'), sheet: p.getProperty('LAS_SHEET'), folder: p.getProperty('LAS_FOLDER')
  };
  if (!c.token || c.token.length < 32 || !/^[A-Za-z0-9_.-]{1,64}$/.test(c.source || '') ||
      !c.owner || !c.sheet || !c.folder) throw new Error('setup_required');
  return c;
}
function lasHash_(s) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s, Utilities.Charset.UTF_8)
    .map(function (b) { return ('0' + ((b + 256) % 256).toString(16)).slice(-2); }).join('');
}
function lasCell_(s) {
  s = String(s == null ? '' : s);
  return /^[=+\-@\t\r]/.test(s) ? "'" + s : s;
}
function lasStore_(cfg) {
  var book = SpreadsheetApp.openById(cfg.sheet), folder = DriveApp.getFolderById(cfg.folder);
  function table(name) {
    var s = book.getSheetByName(name);
    if (!s) throw new Error('missing_store_table');
    return s;
  }
  function get(name, id) {
    var rows = table(name).getDataRange().getValues(), at = name === 'tasks' ? 9 : 1;
    for (var i = 1; i < rows.length; i++) if (String(rows[i][0]) === id) {
      var value = JSON.parse(rows[i][at]);
      if (name === 'tasks' && ['open', 'completed', 'cancelled', 'needs_review'].indexOf(rows[i][8]) >= 0)
        value.owner_state = rows[i][8];
      return value;
    }
    return null;
  }
  return {
    hash: lasHash_, bytes: function (s) { return Utilities.newBlob(s).getBytes().length; }, get: get,
    put: function (name, id, value) {
      var s = table(name), rows = s.getDataRange().getValues(), row = rows.length + 1;
      for (var i = 1; i < rows.length; i++) if (String(rows[i][0]) === id) { row = i + 1; break; }
      var data = name === 'tasks' ? [id, lasCell_(value.title), value.due || '',
        value.owner_state || value.state, value.certainty, lasCell_(value.sender), lasCell_(value.chat),
        value.evidence.join(', '), value.owner_state || '', JSON.stringify(value)] : [id, JSON.stringify(value)];
      s.getRange(row, 1, 1, data.length).setValues([data]);
      SpreadsheetApp.flush();
    },
    saveBody: function (id, body) {
      var files = folder.getFilesByName(id + '.jsonl');
      if (files.hasNext()) {
        var f = files.next();
        if (f.getBlob().getDataAsString('UTF-8') !== body || files.hasNext()) throw new Error('payload_file_conflict');
        return f.getId();
      }
      return folder.createFile(id + '.jsonl', body, MimeType.PLAIN_TEXT).getId();
    },
    loadBody: function (id) { return DriveApp.getFileById(id).getBlob().getDataAsString('UTF-8'); },
    batchIds: function () { return table('batches').getDataRange().getValues().slice(1).map(function (r) { return String(r[0]); }); }
  };
}
function lasHeaders_(m, name) {
  return ((m.payload || {}).headers || []).filter(function (h) { return h.name.toLowerCase() === name; })
    .map(function (h) { return h.value; });
}
function lasMailbox_(s) {
  var m = /<([^<>]+)>\s*$/.exec(s || ''); return (m ? m[1] : String(s || '').trim()).toLowerCase();
}
function lasOwnMail_(m, cfg) {
  return (m.labelIds || []).indexOf('SENT') >= 0 &&
    ['from', 'to'].every(function (name) {
      var h = lasHeaders_(m, name); return h.length === 1 && lasMailbox_(h[0]) === cfg.owner.toLowerCase();
    });
}
function lasMailer_(cfg) {
  return {
    send: function (subject, body) {
      // Recipient is fixed by setup, never accepted from an incoming message.
      MailApp.sendEmail({to: cfg.owner, subject: subject, body: body, name: 'LINE Auto Sync MVP'});
      return null; // MailApp has no durable message ID; a Sent lookup reconciles ambiguity.
    },
    find: function (subject) {
      var found = Gmail.Users.Messages.list('me', {q: 'in:sent from:me to:me subject:"' + subject + '"', maxResults: 20});
      for (var i = 0; i < (found.messages || []).length; i++) {
        var m = Gmail.Users.Messages.get('me', found.messages[i].id, {format: 'metadata', metadataHeaders: ['Subject', 'From', 'To']});
        if (lasOwnMail_(m, cfg) && lasHeaders_(m, 'subject')[0] === subject) return m.id;
      }
      return null;
    }
  };
}
function doPost(e) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return ContentService.createTextOutput('BUSY');
  try {
    if (!e || !e.postData || !e.postData.contents || Utilities.newBlob(e.postData.contents).getBytes().length > 200000)
      throw new Error('invalid_request');
    var cfg = lasConfig_(), store = lasStore_(cfg);
    var accepted = LasMvp.accept(JSON.parse(e.postData.contents), cfg, store);
    try { LasMvp.forward(accepted.batch_id, store, lasMailer_(cfg)); } catch (_) { /* Durable outbox remains. */ }
    return ContentService.createTextOutput('OK');
  } catch (_) { return ContentService.createTextOutput('ERROR'); }
  finally { lock.releaseLock(); }
}
function doGet() { return ContentService.createTextOutput('LINE Auto Sync receiver. No private data is exposed here.'); }
function lasPlain_(part) {
  if (part.mimeType === 'text/plain' && part.body && part.body.data)
    return Utilities.newBlob(Utilities.base64DecodeWebSafe(part.body.data)).getDataAsString('UTF-8');
  for (var i = 0; i < (part.parts || []).length; i++) { var text = lasPlain_(part.parts[i]); if (text !== null) return text; }
  return null;
}
function mvpMaintenance() {
  var lock = LockService.getScriptLock(); if (!lock.tryLock(5000)) return {busy: true};
  try {
    var cfg = lasConfig_(), store = lasStore_(cfg), report = {sent: 0, held: 0, results: 0, invalid: 0};
    if (Gmail.Users.getProfile('me').emailAddress.toLowerCase() !== cfg.owner.toLowerCase()) throw new Error('wrong_google_account');
    store.batchIds().slice(-30).forEach(function (id) {
      var b = store.get('batches', id); if (b.state !== 'accepted' || b.mail_state === 'sent') return;
      try { var r = LasMvp.forward(id, store, lasMailer_(cfg)); if (r.sent) report.sent++; if (r.held) report.held++; }
      catch (_) { report.held++; }
    });
    var page, next;
    // Read complete recent result pages up to a bounded MVP limit. No deletion.
    for (var n = 0; n < 3; n++) {
      page = Gmail.Users.Messages.list('me', {q: 'in:sent from:me to:me subject:"[LINE-TASK-RESULT]" newer_than:30d -in:trash -in:spam', maxResults: 50, pageToken: next});
      (page.messages || []).forEach(function (entry) {
        try {
          var m = Gmail.Users.Messages.get('me', entry.id, {format: 'full'});
          if (!lasOwnMail_(m, cfg) || !/^\[LINE-TASK-RESULT\] /.test(lasHeaders_(m, 'subject')[0] || '')) return;
          var text = lasPlain_(m.payload);
          if (!text || Utilities.newBlob(text).getBytes().length > 180000) throw new Error('invalid_result_mail');
          var value = JSON.parse(text);
          if (value.source !== cfg.source) return;
          var result = LasMvp.result(value, cfg, store); if (!result.duplicate) report.results++;
        } catch (_) { report.invalid++; }
      });
      next = page.nextPageToken; if (!next) break;
    }
    console.log(JSON.stringify(report)); return report;
  } finally { lock.releaseLock(); }
}
function setupMvp() {
  var p = PropertiesService.getScriptProperties(), owner = Session.getEffectiveUser().getEmail();
  if (!owner || !/^[A-Za-z0-9_.-]{1,64}$/.test(p.getProperty('LAS_SOURCE') || '')) throw new Error('set_LAS_SOURCE_first');
  if (!p.getProperty('LAS_TOKEN')) p.setProperty('LAS_TOKEN', Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, ''));
  if (!p.getProperty('LAS_OWNER')) p.setProperty('LAS_OWNER', owner);
  if (p.getProperty('LAS_OWNER').toLowerCase() !== owner.toLowerCase()) throw new Error('wrong_google_account');
  if (!p.getProperty('LAS_SHEET')) {
    var book = SpreadsheetApp.create('LINE Auto Sync MVP — private tasks');
    var headers = {batches: ['id', 'data_json'], messages: ['id', 'data_json'], results: ['id', 'data_json'],
      tasks: ['id', 'title', 'due_jst', 'state', 'certainty', 'sender', 'chat', 'evidence', 'owner_state', 'data_json']};
    book.getSheets()[0].setName('batches');
    Object.keys(headers).forEach(function (name) {
      var sheet = book.getSheetByName(name) || book.insertSheet(name);
      sheet.appendRow(headers[name]); sheet.setFrozenRows(1);
      sheet.getRange(1, 1, sheet.getMaxRows(), headers[name].length).setNumberFormat('@');
    });
    book.setSpreadsheetTimeZone('Asia/Tokyo');
    p.setProperty('LAS_SHEET', book.getId());
  }
  if (!p.getProperty('LAS_FOLDER')) p.setProperty('LAS_FOLDER', DriveApp.createFolder('LINE Auto Sync MVP — private batches').getId());
  // No sharing/public link, no trigger creation and no secret logging here.
  return {configured: true, automatic: false, sheetReady: true};
}
function enableMvpMaintenance() {
  lasConfig_();
  var triggers = ScriptApp.getProjectTriggers().filter(function (t) { return t.getHandlerFunction() === 'mvpMaintenance'; });
  if (triggers.length > 1) throw new Error('duplicate_mvp_trigger');
  if (!triggers.length) ScriptApp.newTrigger('mvpMaintenance').timeBased().everyMinutes(5).create();
  return {mvpMaintenanceTriggers: 1};
}
