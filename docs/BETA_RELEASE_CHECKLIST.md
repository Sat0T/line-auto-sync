# Private beta release gate — candidate RC3 (not yet satisfied)

The public repository is available for source review; NO open-source license,
third-party binary distribution clearance, or device compatibility guarantee
has been granted. These tasks require independent sign-off.

## 1. Provenance, privacy and secrets
- [ ] Confirm author rights, licensing, notices and redistribution permissions.
- [ ] Full source + Git history + build artifact manual and automated secret
  inspection, including unknown tokens, commit metadata and private identifiers.
- [ ] Verify allowlist excludes local settings, DB path, chat, queue, name cache,
  endpoint, credentials, emails, logs and backups.
- [ ] Document personal/conversation-data consent, storage retention, deletion,
  privacy notice and relevant platform terms.
- [ ] No sensitive test outputs in public GitHub, issue or screenshots.

## 2. Offline reliability
- [x] PR #5: persisted queue + frozen retry/ACK synthetic regression coverage.
- [x] PR #5: Windows/Linux x Python 3.9/3.12 CI succeeded at reviewed SHA.
- [ ] PR #6: additional config/cache/redirect/receiver model tests pass in CI.
- [ ] Independent code review of both PRs (not just passing CI).
- [ ] Test large backlog, timeout/write locks, capacity and corrupted state.

## 3. Separate receiver (without touching personal GAS)
- [ ] Inspect a copy of real receiver source, NOT live production deployment.
- [ ] Verify strict legacy request contract and complete B/C1 decoding.
- [ ] Persist stable batch and source-qualified message IDs plus content hashes.
- [ ] Conflicts fail and are reviewable; identical retries return OK once accepted.
- [ ] Store complete batch acceptance BEFORE returning OK.
- [ ] Track Gmail/Calendar/task processing separately with durable downstream
  dedupe and restart/retry behavior. Do NOT promise exactly-once side effects.

## 4. Independent rootless test device (requires explicit approval)
- [ ] Identify Python provider, version, sqlite3, ssl, architecture and jailbreak.
- [ ] Validate code installation, file mode/ownership, app sandbox read rights,
  schema-only diagnosis, no automatic launchd/networking and disabled-by-default.
- [ ] Start with disposable synthetic LINE schema and mock HTTPS receiver.
- [ ] Validate manual enable/run/status/disable, retries, resumed frozen batch,
  read-only source, cache failures and end-to-end bounds.
- [ ] Test uninstall without private state deletion and consistent backup/restore,
  safe upgrades, downgrade prevention and no concurrent legacy worker.
- [ ] Preserve evidence as sanitized PASS/FAIL/NOT TESTED matrix.

## 5. Release decision
- [ ] Resolve or formally accept critical loss, privacy, legal and security risks.
- [ ] Pin source commit, package checksum and exact supported environment.
- [ ] Provide clear install/uninstall/recovery/consent docs to each invitee.
- [ ] Obtain explicit owner approval and consented recipient list.
- [ ] Treat general publication and GUI installer as SEPARATE later milestones.

Status: PRIVATE SOURCE REVIEW ONLY. No iPad, GAS or real data was changed by
this review. No public binary publication is authorized.
