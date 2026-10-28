# RC3 source review — 2026-10-28

Scope: Public GitHub RC3 candidate in PR #5, source files and synthetic tests.
Excluded: running iPad, personal launchd/GAS, real LINE DB, Gmail, Calendar, tasks,
credentials, real endpoint requests and installation.

This is a LIMITED REVIEW, not a comprehensive security or legal sign-off.

## Reviewed properties
- Queue rows and collected cursor are committed together to private SQLite.
- Payload/body/IDs are frozen before HTTP and remain stable on retry.
- ACK is the legacy plain text OK, not the separate HMAC/v2 protocol.
- A receiver accepting a batch before the sender records ACK causes at-least-once
  replay; the sender alone cannot promise duplicate-free downstream side effects.
- LINE DB is opened in SQLite read-only/query-only mode.
- Package payload includes allowlisted Python code and launcher, but excludes
  runtime personal state. Installable operation on a separate device is untested.

## Specific issues addressed by PR #6
1. Name cache path could be a symlink, allowing a local attacker or faulty
   filesystem layout to direct writes outside the intended private state.
   Refuse cache symlinks before opening SQLite.
2. Direct runtime credential reads did not explicitly reject symlinked config
   and state paths; add fail-closed checks (TOCTOU still requires separate review).
3. HTTPS redirect validation permitted same host but DIFFERENT PORT, which is
   a different origin. Pin scheme+host+port except the documented GAS redirect
   from script.google.com:443 to script.googleusercontent.com:443.
4. Existing receiver mock lacked a persisted reference ledger. Add offline
   SQLite-only receiver-contract tests for strict manifest/hash/ID/token checks,
   replay after simulated lost ACK, and content conflicts. This mock is NOT
   actual GAS and cannot prove exactly-once Gmail/Calendar effects.

## Release-blocking findings
| Issue | Status |
|---|---|
| Real receiver durable dedupe (batch_id, source-qualified message ID, hash) | Not verified |
| Receiver acceptance and subsequent Gmail/Calendar side effects | Not verified |
| Real LINE tables, milliseconds, names, authorization | Not verified |
| Rootless Python dependency, iOS sandbox permissions, installation/upgrade/removal | Not verified |
| Live pipeline and recipient privacy/consent review | Not performed |
| Full Git history/commit metadata/artifact secret scan | Not performed by this pass |
| Third-party code rights, notices, and applicable terms | Owner review required |
| State capacity, long messages and corrupt queue recovery | Still limited |
| Symlink race/ancestor path hardening, encrypted backups/retention | Still limited |
| Native GUI | Not implemented |

Availability caveat: sender keeps an SQLite write transaction open during up to
30 seconds of HTTPS, so a blocked receiver may delay another collector. This is
availability/lock contention to test with synthetic fixtures, not a claim of data loss.

## Separation of concerns
The iPad beta includes collector/queue/manual sender only. Never package the
owner's personal cloud code, Gmail, Calendar or task credentials, current LINE DB,
message histories, contacts, private launchd state or backups. The independent
receiver owns its authentication, persistent dedupe and downstream idempotency.

The receiver should store acceptance durably BEFORE returning OK. After OK, an
independently restartable processing state machine must handle failures and
duplicate downstream effects. An in-memory set or a successful sendEmail call
is not a durable idempotency mechanism.

## Boundaries and decision
Synthetic CI passing means code-level behavior only. Keep PR #5 and #6 as drafts
until reviewed; neither should be interpreted as a third-party installation approval.
See BETA_RELEASE_CHECKLIST.md and RECEIVER_PROTOCOL.md.
