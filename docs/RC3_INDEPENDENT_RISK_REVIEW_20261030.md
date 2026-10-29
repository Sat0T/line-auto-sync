# Independent RC3 / PR #8–#9 review (2026-10-30)

## Scope and evidence
Read source files on the PR #9 branch: `sender.py`, `delivery_state.py`, `normal_hold.py`, `runtime_paths.py`. Latest checked PR #9 CI: 50 synthetic tests across Linux/Windows Python 3.9/3.12 and history audit, all passed at https://github.com/Sat0T/line-auto-sync/actions/runs/40936994368 . This is a source-focused review, **not** a complete independent audit of every file or an iOS/GAS verification.

## Findings — unresolved
1. **Credential rotation while HTTP is in flight (medium/high):** `DeliveryState.bind` binds source DB, source_id and receiver endpoint, but not the receiver token. `sender.send_once` snapshots token before HTTP; the post-HTTP validation re-binds latest config but does not explicitly compare the token. If the token is rotated mid-flight, an earlier accepted request may still be acknowledged. Define whether this is intentional; if not, compare a secure in-memory snapshot before ACK and fail closed, with synthetic regression tests. Do not log tokens.
2. **Oversized first message (availability limitation):** `normal_hold.select_prefix` deliberately refuses a first item exceeding its 60KB limit, preserving cursor rather than silently dropping data. Define an owner-approved strategy (fragmentation, redaction, or explicit skip with audit) before claiming unlimited-message support. Fragmentation changes protocol and receiver dedupe; no silent truncation.
3. **Unbounded queue growth (availability/privacy):** `messages` has no demonstrated retention/size quota in the reviewed methods. Introduce capacity alerts and safe fail-closed behavior before retention or deletion; never prune unacknowledged messages automatically.
4. **Lock file race and permissions (deployment gate):** `sender_lock` checks symlink before open and uses `O_NOFOLLOW` when supported; verifies regular file and single link, restricts Unix permissions. On platforms without `O_NOFOLLOW`, pre-check/open race needs explicit threat-model treatment. Verify rootless iOS kernel lock semantics and ownership/permissions on device. Same-UID hostile modification is outside guarantees.
5. **Receiver exactly-once not established:** Frozen retry and local ACK cannot guarantee exactly-once processing if the receiver lacks persistent dedupe. A successful HTTP request followed by a crash before ACK can resend. Require receiver-side persistent batch ID dedupe, with an explicit atomicity model and retention window.
6. **Schema/integrity limits:** `validate_existing` checks schema and metadata, not every queue row. `verify_frozen` checks only the frozen request. Full historical integrity or malicious tamper resistance is not claimed.
7. **Source / rights / privacy gates:** No real LINE messages, identifiers, credentials, real GAS URLs, or device filesystem paths may be published. Prior history review identified private-path patterns and public emails requiring owner review. Distribution rights and app/OS compatibility remain unverified.

## Proposed next offline test matrix
- Receiver token rotation mid-flight: never ACK without a defined policy.
- Receiver endpoint/source changes mid-flight: no ACK; frozen state retained.
- First message over limit: no cursor advance or silent loss.
- 501+ pending rows: prefix boundary and frozen manifest correctness.
- HTTP succeeds, ACK transaction fails: frozen retry, dedupe requirement documented.
- State DB busy and lock acquisition failure: deterministic fail-closed result.
- Stale process exit and lock reopening: no permanent deadlock.
- State directory and lock-file filesystem attack cases: reject safely without external network.
- Repeated collector runs during sender wait: new messages queued, frozen IDs unchanged.
- Full Windows/Linux matrix, then independent isolated rootless device and receiver tests.

## Release gates
Do not merge #9 into #8 or other stacked PRs without owner authorization. Do not enable live LINE/GAS or share packages. Synthetic CI green is necessary but insufficient for beta release. Maintain explicit distinction between code review, synthetic test, and real-device proof.

## Work handoff status
This is **not** a final Work handoff: offline Chat work remains possible (credential rotation regression, capacity/size test design, receiver mock tests, full PR review). Prepare final handoff only when those are exhausted or blocked by missing device access/approval.
