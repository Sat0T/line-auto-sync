/** Run from the isolated Apps Script editor to test the real Gmail handoff. */
function sendMvpSyntheticBatch() {
  var cfg = lasConfig_();
  if (cfg.source !== 'MVP_DEMO') throw new Error('synthetic_source_required');
  var pk = '20261111001';
  var record = {
    id: cfg.source + ':' + pk,
    time: '2026-11-11T09:00:00+09:00',
    chat: 'MVP合成試験',
    sender: '架空の試験担当者',
    text: '【合成試験・実際の依頼ではありません】試験用チェックリストを確認してください。期限はありません。',
    refs: {message_pk: pk, chat_pk: '2', sender_pk: '1'}
  };
  var payload = LasMvp.canonical(record) + '\n';
  var digest = lasHash_(payload);
  var manifest = {
    v: 2, kind: 'line_batch', source: cfg.source, part: 1, parts: 1, count: 1,
    sha256: digest, batch_id: lasHash_(cfg.source + '\n' + digest)
  };
  var body = LasMvp.canonical(manifest) + '\n' + payload;
  var reply = doPost({postData: {contents: JSON.stringify({token: cfg.token, body: body, maxid: pk})}});
  if (reply.getContent() !== 'OK') throw new Error('synthetic_batch_not_accepted');
  var batch = lasStore_(cfg).get('batches', manifest.batch_id);
  console.log(JSON.stringify({batch_id: manifest.batch_id, accepted: batch.state === 'accepted', mail_state: batch.mail_state}));
}
