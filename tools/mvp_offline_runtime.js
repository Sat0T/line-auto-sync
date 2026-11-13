/* Disposable Google-service doubles. No Google/LINE network calls. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const clone = x => JSON.parse(JSON.stringify(x));

function createRuntime(saved) {
  const state = saved || {properties: {}, books: {}, folders: {}, files: {}, mails: [], triggers: [], next: 1};
  let sendFailure = false, lockBusy = false;
  function id(prefix) { return prefix + state.next++; }
  function file(key) {
    const f = state.files[key]; if (!f) throw Error('missing_fake_file');
    return {getId: () => key, getBlob: () => blob(f.body), getSharingAccess: () => 'PRIVATE'};
  }
  function iterator(list) { let i = 0; return {hasNext: () => i < list.length, next: () => list[i++]}; }
  function blob(data) {
    const b = Buffer.isBuffer(data) ? data : Array.isArray(data) ? Buffer.from(data) : Buffer.from(String(data), 'utf8');
    return {getBytes: () => [...b], getDataAsString: () => b.toString('utf8')};
  }
  function sheet(book, name) {
    const rows = state.books[book].sheets[name];
    if (!rows) return null;
    return {
      setName: next => {state.books[book].sheets[next] = rows; delete state.books[book].sheets[name];},
      appendRow: row => rows.push(clone(row)), setFrozenRows: () => {}, getMaxRows: () => 1000,
      getDataRange: () => ({getValues: () => clone(rows)}),
      getRange: (r, c) => ({setNumberFormat: () => {}, setValues: values => {
        values.forEach((row, offset) => {
          while (rows.length < r + offset) rows.push([]);
          row.forEach((v, col) => { rows[r - 1 + offset][c - 1 + col] = v; });
        });
      }})
    };
  }
  function book(key) {
    if (!state.books[key]) throw Error('missing_fake_book');
    return {getId: () => key, getSheetByName: name => sheet(key, name),
      getSheets: () => Object.keys(state.books[key].sheets).map(name => sheet(key, name)),
      insertSheet: name => {state.books[key].sheets[name] = []; return sheet(key, name);},
      setSpreadsheetTimeZone: () => {}};
  }
  const context = vm.createContext({console: {log: () => {}},
    PropertiesService: {getScriptProperties: () => ({
      getProperty: k => state.properties[k] || null,
      setProperty: (k, v) => {state.properties[k] = v;}
    })},
    Session: {getEffectiveUser: () => ({getEmail: () => 'owner@example.invalid'})},
    Utilities: {DigestAlgorithm: {SHA_256: 'sha256'}, Charset: {UTF_8: 'utf8'},
      computeDigest: (_, s) => [...crypto.createHash('sha256').update(s, 'utf8').digest()],
      newBlob: blob, getUuid: crypto.randomUUID,
      base64DecodeWebSafe: s => [...Buffer.from(s, 'base64url')]},
    SpreadsheetApp: {
      create: () => {const key = id('book'); state.books[key] = {sheets: {Sheet1: []}}; return book(key);},
      openById: book, flush: () => {}
    },
    DriveApp: {Access: {PRIVATE: 'PRIVATE'}, getFileById: file,
      createFolder: () => {const key = id('folder'); state.folders[key] = []; return {getId: () => key};},
      getFolderById: key => {
        if (!state.folders[key]) throw Error('missing_fake_folder');
        return {getFilesByName: name => iterator(state.folders[key].filter(k => state.files[k].name === name).map(file)),
          createFile: (name, body) => {const f = id('file'); state.files[f] = {name, body}; state.folders[key].push(f); return file(f);},
          getSharingAccess: () => 'PRIVATE'};
      }},
    MimeType: {PLAIN_TEXT: 'text/plain'},
    ContentService: {createTextOutput: value => value},
    LockService: {getScriptLock: () => ({tryLock: () => !lockBusy, releaseLock: () => {}})},
    MailApp: {sendEmail: message => {
      const m = makeMail(id('mail'), message.subject, message.body);
      state.mails.push(m);
      if (sendFailure) throw Error('simulated_send_result_lost');
    }},
    Gmail: {Users: {getProfile: () => ({emailAddress: 'owner@example.invalid'}), Messages: {
      list: (_, options) => {
        let mails = state.mails;
        if (options.q.includes('[LINE-TASK-RESULT]')) mails = mails.filter(m => m.subject.startsWith('[LINE-TASK-RESULT] '));
        else {const match = /subject:"(.*)"/.exec(options.q); if (match) mails = mails.filter(m => m.subject === match[1]);}
        return {messages: mails.slice().reverse().slice(0, options.maxResults).map(m => ({id: m.id}))};
      },
      get: (_, key) => {const m = state.mails.find(m => m.id === key); if (!m) throw Error('missing_fake_mail'); return clone(m);}
    }}},
    ScriptApp: {getProjectTriggers: () => state.triggers.map(name => ({getHandlerFunction: () => name})),
      newTrigger: name => ({timeBased: () => ({everyMinutes: () => ({create: () => {state.triggers.push(name);}})})})}
  });
  for (const name of ['Core.gs', 'App.gs'])
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../cloud/gas', name), 'utf8'), context, {filename: name});
  function makeMail(key, subject, body) {
    return {id: key, subject, labelIds: ['SENT', 'INBOX'], payload: {
      mimeType: 'text/plain', headers: [{name: 'From', value: 'owner@example.invalid'},
        {name: 'To', value: 'owner@example.invalid'}, {name: 'Subject', value: subject}],
      body: {data: Buffer.from(body, 'utf8').toString('base64url')}
    }};
  }
  return {state, context,
    bootstrap(source, token) {state.properties.LAS_SOURCE = source; state.properties.LAS_TOKEN = token; context.setupMvp();},
    receive(request) {return context.doPost({postData: {contents: JSON.stringify(request)}});},
    addResult(value) {state.mails.push(makeMail(id('result'), '[LINE-TASK-RESULT] ' + value.source + ' offline synthetic', JSON.stringify(value)));},
    maintenance() {return context.mvpMaintenance();},
    tasks() {
      const rows = state.books[state.properties.LAS_SHEET].sheets.tasks;
      return rows.slice(1).map(row => JSON.parse(row[9]));
    },
    setOwnerState(key, value) {
      const row = state.books[state.properties.LAS_SHEET].sheets.tasks.find(row => row[0] === key);
      if (!row) throw Error('missing_fake_task'); row[8] = value;
    },
    loseSendResponse(value) {sendFailure = value;}, busy(value) {lockBusy = value;}
  };
}

module.exports = {createRuntime};
if (require.main === module) {
  const input = JSON.parse(fs.readFileSync(0, 'utf8')), statePath = process.argv[2];
  const runtime = createRuntime(fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath, 'utf8')) : null);
  let result;
  if (input.action === 'receive') {
    if (!runtime.state.properties.LAS_SOURCE) runtime.bootstrap(input.source, input.request.token);
    result = {reply: runtime.receive(input.request)};
  } else if (input.action === 'result') {
    runtime.addResult(input.value); result = runtime.maintenance();
  } else if (input.action === 'maintenance') result = runtime.maintenance();
  else throw Error('unknown_offline_action');
  const temporary = statePath + '.next';
  fs.writeFileSync(temporary, JSON.stringify(runtime.state), {mode: 0o600});
  fs.renameSync(temporary, statePath);
  process.stdout.write(JSON.stringify({result, tasks: runtime.tasks(),
    mailCount: runtime.state.mails.filter(m => m.subject.startsWith('[LINE-TASK-BATCH] ')).length}));
}
