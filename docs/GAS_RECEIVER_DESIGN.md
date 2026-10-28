# Separate receiver design — GAS interoperability (NO live deployment)

This is a design and a reference OFFLINE receiver test, not a validated
deployment of the owner's GAS or a turnkey copy/paste replacement.

## Scope and isolation

The rootless iPad package contains the collector, local queue and HTTPS sender.
The receiver is a separately managed cloud service. Do not bundle GAS source,
OAuth credentials, calendar IDs, email addresses, Google account identifiers,
token strings, deployment URLs or private messages inside a distributed .deb.

The default RC3 protocol is legacy token/body/maxid POST, strict text OK.
It is NOT the HMAC-authenticated v2 transport. See RECEIVER_PROTOCOL.md.

## Receiver algorithm (conceptual)

1. Authenticate the request with a separately provisioned secret. Reject
   non-HTTPS access and bound raw request size before decoding.
2. Require exactly the permitted fields; verify text encoding and strict JSON.
3. Decode both canonical B records and C1 compact representation, if supported.
4. Require one source, unambiguous source-qualified message IDs, unique PKs,
   manifest hash, count, part=parts=1 and expected stable batch ID.
   Verify maxid equals the maximum included PK. Validate source authorization.
5. Under a storage transaction or an equivalent linearizable critical section,
   inspect (source,batch_id) and per-message hashes. Reject conflicting content.
   If an identical batch is already durably accepted, respond OK.
6. Otherwise save the full accepted batch, individual message IDs, raw/normalized
   hashes and a processing state in durable storage. Only respond OK AFTER commit.
7. Process calendar/task/mail deliveries from a separately retryable outbox.
   Assign stable downstream operation keys. Track per-effect state and failed
   attempts; reconcile ambiguity rather than silently dropping records.

## Failure handling

| Failure | Required behavior |
|---|---|
| Wrong token, invalid body/manifest, mismatched PK/hash | Reject; never return OK |
| Request accepted; HTTP response lost | Identical replay returns OK without duplicating acceptance |
| Same batch ID but different body | Reject conflicting data; operator review |
| Same source-qualified message ID but new body hash | Reject conflict; never overwrite silently |
| Incomplete durable insert | Roll back and never reply OK |
| External Calendar succeeds but status write fails | Reconcile using stable event/task ID; do not assert exactly once |
| Gmail send accepted but write fails | Ambiguous outcome; Gmail send is not transactional with GAS storage |
| Simultaneous duplicate requests | Serialize compare-and-insert or use an atomic unique constraint |

Google Apps Script LockService can serialize some concurrent executions and
PropertiesService/Sheets can track state, but these alone are NOT a transactional
database covering Gmail/Calendar operations. Check quotas, concurrent access,
crash/retry atomicity and storage limits before calling such a design safe.
A durable external store with uniqueness constraints may be necessary.

## Synthetic verification

tests/test_receiver_contract.py implements a disposable SQLite receiver model,
strict checks, persistent batch and message ledgers, and fake side effects inside
one transaction. It demonstrates replay *within the model only*; real GAS and
Google APIs are not tested and no network calls are made.

After the owner supplies a sanitized copy or interface contract of their GAS,
review it separately. Do NOT inspect or change the live GAS deployment without
explicit authorization. Keep receiver secrets and logs out of GitHub.
