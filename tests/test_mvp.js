'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createRuntime} = require('../tools/mvp_offline_runtime');
const SOURCE = 'MVP_DEMO', TOKEN = 'SYNTHETIC_MVP_TOKEN_NOT_A_REAL_SECRET_ONLY';
function fixture(compact = false) {
  const runtime = createRuntime(); runtime.bootstrap(SOURCE, TOKEN);
  const core = runtime.context.LasMvp, hash = runtime.context.lasHash_;
  const r = {id: SOURCE + ':102', time: '2026-11-04T09:01:00+09:00', chat: 'Demo Group', sender: 'Demo User',
    text: 'Synthetic form request', refs: {message_pk: '102', chat_pk: '2', sender_pk: '1'}};
  const digest = hash(core.canonical(r) + '\n');
  const manifest = {v: 2, kind: 'line_batch', source: SOURCE, part: 1, parts: 1, count: 1,
    sha256: digest, batch_id: hash(SOURCE + '\n' + digest)};
  const payload = compact ? core.canonical({format: 'C1', source: SOURCE, chats: [[r.chat, '2']], senders: [[r.sender, '1']]}) +
    '\n' + core.canonical(['102', r.time, 0, 0, r.text]) + '\n' : core.canonical(r) + '\n';
  const request = {token: TOKEN, maxid: '102', body: core.canonical(manifest) + '\n' + payload};
  const result = {kind: 'line_task_result', status: 'extraction_complete', source: SOURCE,
    processedMessageIDs: [r.id], items: [{item_id: r.id, kind: 'task', title: 'Submit synthetic form',
      certainty: 'confirmed', applies_to_user: true, due: '2026-11-06T17:00:00+09:00', evidence_ids: [r.id]}]};
  return {runtime, request, result, manifest};
}

test('real GAS entry points accept B/C1, authenticate, persist once and forward once', () => {
  for (const compact of [false, true]) {
    const {runtime, request} = fixture(compact);
    assert.equal(runtime.receive({...request, token: 'WRONG'}), 'ERROR');
    assert.equal(runtime.state.mails.length, 0);
    assert.equal(runtime.receive(request), 'OK');
    assert.equal(runtime.receive(request), 'OK');
    assert.equal(runtime.state.mails.length, 1);
    assert.equal(runtime.receive({...request, body: request.body.replace('Synthetic', 'Tampered')}), 'ERROR');
    assert.equal(runtime.state.mails.length, 1);
    assert.equal(runtime.context.doGet().includes(TOKEN), false);
  }
});

test('a lost mail-send result reconciles Sent without a second send', () => {
  const {runtime, request} = fixture(); runtime.loseSendResponse(true);
  assert.equal(runtime.receive(request), 'OK');
  assert.equal(runtime.state.mails.length, 1);
  runtime.loseSendResponse(false);
  runtime.maintenance(); runtime.maintenance();
  assert.equal(runtime.state.mails.length, 1);
});

test('existing result schema creates visible tasks once and preserves owner completion', () => {
  const {runtime, request, result} = fixture(); runtime.receive(request);
  runtime.addResult(result); assert.equal(runtime.maintenance().results, 1);
  assert.equal(runtime.tasks().length, 1);
  runtime.setOwnerState(SOURCE + ':102', 'completed');
  runtime.addResult(result); assert.equal(runtime.maintenance().results, 0);
  assert.equal(runtime.tasks().length, 1);
  assert.equal(runtime.context.lasStore_(runtime.context.lasConfig_()).get('tasks', SOURCE + ':102').owner_state, 'completed');
  const invalid = JSON.parse(JSON.stringify(result)); invalid.processedMessageIDs = [SOURCE + ':999'];
  runtime.addResult(invalid); assert.equal(runtime.maintenance().invalid, 1);
  assert.equal(runtime.tasks().length, 1);
  const conflict = JSON.parse(JSON.stringify(result)); conflict.items[0].title = 'Different result for the same evidence';
  runtime.addResult(conflict); assert.equal(runtime.maintenance().invalid, 2);
  assert.equal(runtime.tasks()[0].title, result.items[0].title);
});

test('candidate stays pending, sender/chat come from evidence, sheet formulas are escaped', () => {
  const {runtime, request, result} = fixture(); runtime.receive(request);
  result.items[0].certainty = 'candidate'; result.items[0].title = '=IMPORTDATA("example.invalid")';
  result.items[0].sender = 'fabricated by a result'; result.items[0].chat = 'fabricated';
  runtime.addResult(result); assert.equal(runtime.maintenance().results, 1);
  const task = runtime.tasks()[0];
  assert.equal(task.state, 'needs_review'); assert.equal(task.sender, 'Demo User'); assert.equal(task.chat, 'Demo Group');
  const row = runtime.state.books[runtime.state.properties.LAS_SHEET].sheets.tasks[1];
  assert.equal(row[1].startsWith("'="), true);
  runtime.context.enableMvpMaintenance(); runtime.context.enableMvpMaintenance();
  assert.deepEqual(runtime.state.triggers, ['mvpMaintenance']);
});
