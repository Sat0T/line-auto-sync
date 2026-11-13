/** Normal LINE MVP. Pure logic shared by Apps Script and offline integration. */
var LasMvp = (function () {
  'use strict';
  function fail(code) { throw new Error(code); }
  function object(x) { return x !== null && typeof x === 'object' && !Array.isArray(x); }
  function keys(x, expected) {
    return object(x) && Object.keys(x).sort().join(',') === expected.slice().sort().join(',');
  }
  function canonical(x) {
    if (Array.isArray(x)) return '[' + x.map(canonical).join(',') + ']';
    if (object(x)) return '{' + Object.keys(x).sort().map(function (k) {
      return JSON.stringify(k) + ':' + canonical(x[k]);
    }).join(',') + '}';
    return JSON.stringify(x);
  }
  function pk(x) {
    return typeof x === 'string' && /^[1-9][0-9]{0,18}$/.test(x) &&
      (x.length < 19 || x <= '9223372036854775807');
  }
  function source(x) { return typeof x === 'string' && /^[A-Za-z0-9_.-]{1,64}$/.test(x); }
  function jst(x) {
    if (typeof x !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+09:00$/.test(x)) return false;
    var d = new Date(x), shifted = new Date(d.getTime() + 9 * 3600000);
    return Number.isFinite(d.getTime()) && shifted.toISOString().slice(0, 19) === x.slice(0, 19);
  }
  function due(x) {
    if (x === null) return true;
    if (jst(x)) return true;
    return typeof x === 'string' && /^\d{4}-\d\d-\d\d$/.test(x) &&
      Number.isFinite(Date.parse(x + 'T00:00:00Z')) &&
      new Date(x + 'T00:00:00Z').toISOString().slice(0, 10) === x;
  }
  function record(r, s) {
    if (!keys(r, ['id', 'time', 'chat', 'sender', 'text', 'refs']) ||
        !object(r.refs) || !pk(r.refs.message_pk) || r.id !== s + ':' + r.refs.message_pk ||
        !jst(r.time) || typeof r.chat !== 'string' || !r.chat ||
        typeof r.sender !== 'string' || !r.sender ||
        !(r.text === null || typeof r.text === 'string')) fail('invalid_record');
    var refs = r.refs;
    if (!['message_pk', 'chat_pk', 'sender_pk'].every(function (k) { return k in refs; }) ||
        Object.keys(refs).some(function (k) {
          return ['message_pk', 'chat_pk', 'sender_pk', 'chat_mid', 'sender_mid'].indexOf(k) < 0;
        })) fail('invalid_refs');
    ['chat_pk', 'sender_pk'].forEach(function (k) {
      if (refs[k] !== null && !pk(refs[k])) fail('invalid_refs');
    });
    ['chat_mid', 'sender_mid'].forEach(function (k) {
      if (k in refs && refs[k] !== null && typeof refs[k] !== 'string') fail('invalid_refs');
    });
    try { encodeURIComponent(canonical(r)); } catch (_) { fail('invalid_unicode'); }
    return r;
  }
  function decode(body, hash) {
    if (typeof body !== 'string' || !body.endsWith('\n') || body.indexOf('\r') >= 0) fail('invalid_body');
    var lines = body.trimEnd().split('\n'), m = JSON.parse(lines.shift());
    if (!keys(m, ['v', 'kind', 'source', 'batch_id', 'part', 'parts', 'count', 'sha256']) ||
        m.v !== 2 || m.kind !== 'line_batch' || !source(m.source) ||
        m.part !== 1 || m.parts !== 1 || !Number.isInteger(m.count) || m.count < 1 || m.count > 500) fail('invalid_manifest');
    var first = JSON.parse(lines[0]), records;
    if (object(first) && first.format === 'C1') {
      if (!keys(first, ['format', 'source', 'chats', 'senders']) || first.source !== m.source) fail('invalid_c1');
      ['chats', 'senders'].forEach(function (k) {
        var seen = new Set();
        if (!Array.isArray(first[k]) || !first[k].length || first[k].length > 500) fail('invalid_dictionary');
        first[k].forEach(function (v) {
          if (!Array.isArray(v) || (v.length !== 2 && v.length !== 3) ||
              typeof v[0] !== 'string' || !v[0] || !(v[1] === null || pk(v[1])) ||
              (v.length === 3 && v[2] !== null && typeof v[2] !== 'string') || seen.has(canonical(v))) fail('invalid_dictionary');
          seen.add(canonical(v));
        });
      });
      var chats = new Set(), senders = new Set();
      records = lines.slice(1).map(function (line) {
        var a = JSON.parse(line);
        if (!Array.isArray(a) || a.length !== 5 || !pk(a[0]) ||
            !Number.isInteger(a[2]) || !Number.isInteger(a[3]) ||
            a[2] < 0 || a[2] >= first.chats.length || a[3] < 0 || a[3] >= first.senders.length) fail('invalid_c1_row');
        var c = first.chats[a[2]], u = first.senders[a[3]], refs = {message_pk: a[0], chat_pk: c[1], sender_pk: u[1]};
        if (c.length === 3) refs.chat_mid = c[2];
        if (u.length === 3) refs.sender_mid = u[2];
        chats.add(a[2]); senders.add(a[3]);
        return {id: m.source + ':' + a[0], time: a[1], chat: c[0], sender: u[0], text: a[4], refs: refs};
      });
      if (chats.size !== first.chats.length || senders.size !== first.senders.length) fail('unused_dictionary');
    } else records = lines.map(function (line) { return JSON.parse(line); });
    var seenIds = new Set();
    records.forEach(function (r) {
      record(r, m.source);
      if (seenIds.has(r.id)) fail('duplicate_message');
      seenIds.add(r.id);
    });
    var digest = hash(records.map(function (r) { return canonical(r) + '\n'; }).join(''));
    if (records.length !== m.count || m.sha256 !== digest || m.batch_id !== hash(m.source + '\n' + digest)) fail('checksum_mismatch');
    return {manifest: m, records: records};
  }
  function accept(request, cfg, store) {
    if (!keys(request, ['token', 'body', 'maxid']) || typeof request.token !== 'string' ||
        typeof cfg.token !== 'string' || cfg.token.length < 32 ||
        store.hash(request.token) !== store.hash(cfg.token)) fail('denied');
    if (typeof request.body !== 'string' || store.bytes(request.body) > 90000) fail('body_too_large');
    var decoded = decode(request.body, store.hash), m = decoded.manifest, records = decoded.records;
    if (m.source !== cfg.source) fail('source_not_allowed');
    var ids = records.map(function (r) { return r.refs.message_pk; });
    ids.sort(function (a, b) { return a.length - b.length || a.localeCompare(b); });
    if (String(request.maxid) !== ids[ids.length - 1]) fail('maxid_mismatch');
    var bodyHash = store.hash(request.body), old = store.get('batches', m.batch_id);
    if (old && old.body_hash !== bodyHash) fail('batch_conflict');
    records.forEach(function (r) {
      var previous = store.get('messages', r.id);
      if (previous && previous.hash !== store.hash(canonical(r))) fail('message_conflict');
    });
    if (old && old.state === 'accepted') return {accepted: true, duplicate: true, batch_id: m.batch_id};
    var batch = old || {id: m.batch_id, source: m.source, body_hash: bodyHash,
      file_id: store.saveBody(m.batch_id, request.body), state: 'preparing', mail_state: 'pending', analysis: 'pending'};
    store.put('batches', batch.id, batch);
    records.forEach(function (r) {
      if (!store.get('messages', r.id)) store.put('messages', r.id,
        {id: r.id, hash: store.hash(canonical(r)), batch_id: batch.id, result_hash: null});
    });
    batch.state = 'accepted';
    store.put('batches', batch.id, batch);
    return {accepted: true, duplicate: false, batch_id: batch.id};
  }
  function forward(id, store, mailer) {
    var b = store.get('batches', id);
    if (!b || b.state !== 'accepted') fail('not_accepted');
    if (b.mail_state === 'sent') return {sent: false, duplicate: true};
    var subject = '[LINE-TASK-BATCH] ' + b.source + ' ' + b.id;
    if (b.mail_state === 'sending') {
      var found = mailer.find(subject);
      if (!found) return {held: true, reason: 'mail_result_unknown'};
      b.mail_state = 'sent'; b.mail_id = found;
      store.put('batches', id, b);
      return {recovered: true};
    }
    var body = store.loadBody(b.file_id);
    if (store.hash(body) !== b.body_hash) fail('stored_body_corrupt');
    b.mail_state = 'sending'; store.put('batches', id, b);
    // A send and the state write are not atomic. Ambiguous sends are held,
    // reconciled with Sent, never blindly repeated.
    b.mail_id = mailer.send(subject, body);
    b.mail_state = 'sent'; store.put('batches', id, b);
    return {sent: true};
  }
  function result(value, cfg, store) {
    if (!object(value) || value.kind !== 'line_task_result' || value.status !== 'extraction_complete' ||
        value.source !== cfg.source || !Array.isArray(value.processedMessageIDs) ||
        !value.processedMessageIDs.length || value.processedMessageIDs.length > 500 ||
        new Set(value.processedMessageIDs).size !== value.processedMessageIDs.length ||
        !Array.isArray(value.items) || value.items.length > 100) fail('invalid_result');
    var known = Object.create(null), batches = Object.create(null);
    value.processedMessageIDs.forEach(function (id) {
      var entry = typeof id === 'string' && store.get('messages', id);
      if (!entry) fail('unknown_evidence');
      var b = batches[entry.batch_id] || store.get('batches', entry.batch_id);
      if (!b || b.state !== 'accepted' || b.source !== cfg.source) fail('unknown_evidence');
      batches[b.id] = b;
      if (!b.records) {
        var body = store.loadBody(b.file_id);
        if (store.hash(body) !== b.body_hash) fail('stored_body_corrupt');
        b.records = decode(body, store.hash).records;
      }
      var r = b.records.find(function (r) { return r.id === id; });
      if (!r || store.hash(canonical(r)) !== entry.hash) fail('unknown_evidence');
      known[id] = r;
    });
    var itemIds = new Set(), tasks = value.items.map(function (item) {
      var evidence = item && (item.evidence_ids || item.evidenceMessageIDs);
      var title = item && (item.action || item.title || item.summary);
      if (!object(item) || !known[item.item_id] || itemIds.has(item.item_id) ||
          !Array.isArray(evidence) || !evidence.length || !evidence.every(function (id) { return !!known[id]; }) ||
          typeof title !== 'string' || !title.trim() || title.length > 800 ||
          ['task', 'deadline', 'reply', 'submission', 'event', 'schedule', 'meeting'].indexOf(item.kind) < 0 ||
          ['confirmed', 'candidate', 'unknown'].indexOf(item.certainty) < 0) fail('invalid_item');
      var deadline = item.deadline_jst || item.due_jst || item.due || null;
      if (!due(deadline)) fail('invalid_due');
      itemIds.add(item.item_id);
      var r = known[item.item_id], previous = store.get('tasks', item.item_id);
      return {id: item.item_id, title: title, kind: item.kind, due: deadline, certainty: item.certainty,
        sender: r.sender, chat: r.chat, evidence: evidence,
        state: item.certainty === 'confirmed' && item.applies_to_user === true ? 'open' : 'needs_review',
        owner_state: previous && previous.owner_state || null,
        uncertainty: typeof item.uncertainty === 'string' ? item.uncertainty.slice(0, 1000) : ''};
    });
    var hash = store.hash(canonical({source: value.source,
      processedMessageIDs: value.processedMessageIDs.slice().sort(), items: value.items}));
    value.processedMessageIDs.forEach(function (id) {
      var entry = store.get('messages', id);
      if (entry.result_hash && entry.result_hash !== hash) fail('result_conflict');
    });
    var prior = store.get('results', hash);
    if (prior && prior.state === 'complete') return {duplicate: true, saved: 0};
    store.put('results', hash, {id: hash, state: 'preparing', count: tasks.length});
    tasks.forEach(function (t) { store.put('tasks', t.id, t); });
    value.processedMessageIDs.forEach(function (id) {
      var entry = store.get('messages', id); entry.result_hash = hash; store.put('messages', id, entry);
    });
    Object.keys(batches).forEach(function (id) {
      var b = batches[id], complete = b.records.every(function (r) { return !!store.get('messages', r.id).result_hash; });
      delete b.records;
      b.analysis = complete ? 'complete' : 'partial';
      store.put('batches', id, b);
    });
    store.put('results', hash, {id: hash, state: 'complete', count: tasks.length});
    return {duplicate: false, saved: tasks.length};
  }
  return {canonical: canonical, decode: decode, accept: accept, forward: forward, result: result};
}());
