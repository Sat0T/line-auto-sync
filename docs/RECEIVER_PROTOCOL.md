# Normal-LINE receiver contract (RC3)

The default sender uses **legacy-token/OK**, not the signed HMAC protocol in
`transport.py`. The latter remains a separately configured utility; its record
validation/canonical encoding is reused, but its network protocol is not selected
by `cli run`. Do not point one protocol at a receiver implementing the other.

## Request and acceptance

HTTPS POST JSON: `{"token":"<local secret>","maxid":"<last selected PK>","body":"<frozen UTF-8 text>"}`.
`body` begins with a v2 `line_batch` manifest, then self-contained B/JSONL or C1
records. The **body format version is not the HTTP authentication protocol**.
Manifest keys include source, batch_id, part/parts, count, sha256. SHA256 covers
restored canonical B records, each followed by LF, not the compact C1 text.
**Records are sorted by message timestamp, then PK, not necessarily by ascending PK.**
A compatible receiver must check uniqueness, source, hashes and `maxid=max(PK)`
without requiring the transmitted record order to be PK-ascending.

The sender freezes the complete body before HTTP. Timeout, error or crash retries
the same body, batch_id and selected IDs; new arrivals stay in a different queued
batch. Authentication is read from private config at send time, not stored in the
package. Non-HTTPS redirects, unrelated-host redirects, URL credentials and server
response logging are rejected. GAS's HTTPS content redirect is supported.

Success is a bounded text response whose trimmed bytes are exactly `OK`.
`OK extra`, JSON ACKs and errors are not accepted. The receiver must authenticate,
validate the complete manifest/body, and **durably accept all selected records**
before replying. Returning OK for partial acceptance violates this contract and
cannot be detected with this legacy response alone.

## Required receiver idempotency

Deduplicate stable batch_id and source-qualified message IDs, with a content hash
conflict check. Never dedupe by only maxid, name, timestamp, or unread state.
Replaying an already durably accepted identical request should return OK without
repeating its downstream work. Conflicting content must fail and remain reviewable.

This is at-least-once transport. If the receiver accepts then the client crashes
before committing its local ACK, the request is repeated. **Exactly-once delivery
or duplicate-free Gmail forwarding is not guaranteed by this client.** A receiver
without durable dedupe is unsuitable for unattended beta use. A GAS sendEmail call
by itself is not durable idempotency, and Gmail arrival is not AI completion.

No actual GAS was deployed or contacted by these changes. The offline tests use
an in-memory mock receiver to verify stable retry identity after acceptance-before-
crash; they do not certify a real receiver implementation or GAS quotas.

## Client persistence

`normal-delivery.sqlite`: rows + collected cursor commit together; a frozen body
commits before HTTP; OK commits acknowledged IDs, success clock and deletion of
the frozen request together. A failed attempt retains both rows and request with
60-second exponential backoff capped at one hour. The successful-send hourly gate
does not prevent retrying an already frozen request. Competing processes serialize
via SQLite. Database/source/destination changes fail closed instead of silently
redirecting pending data.

The baseline deliberately skips existing messages on a brand-new state directory.
Legacy sentid/initial_scan_id and pending CSV are imported once transactionally;
ambiguous/corrupt legacy state is rejected. Legacy checkpoint files are not updated
after adoption. A message already collected survives removal from the source DB;
messages removed *before collection*, attachments, edits and non-text content are
outside this guarantee. Oversized heads remain queued and require future recovery.
