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

## 追補検査の結果（2026-10-28）

- [x] GitHub Actionsに過去の到達可能コミット・blob検査を追加。
- [x] 既知トークン等の高確度パターン検査を実施（該当なし。ただし網羅的ではない）。
- [ ] 過去のコンパイル済みバイナリに残る利用者絶対パス2件と、2種類の公開作者メールを本人が確認・対応方針決定。強制的なGit履歴改変はしない。
- [x] 合成DBのバックアップ→別の状態ディレクトリでの復元、固定リクエストの保持、600件のキュー処理、破損時の停止をテスト。
- [x] 本番GASを変更しない受信側模擬モデルで、ACK消失・受信側永続重複排除を検証。
- [ ] 本番GASのコピーまたは安全な仕様書が未入手。実受信側の重複防止は未確認。
- [ ] 実機のWAL・暗号化バックアップ・権限・インストール/削除/復元試験は未実施。

参照：`docs/BACKUP_RESTORE_TEST_PLAN_JA.md`、`docs/LIMITED_SOURCE_SCAN.md`。既存のチェックリストで未完了となっている項目は引き続き配布ゲート。

## 2026-10-29 Chat追補：Draft PR #7の新たな安全策

- [x] 配布候補の凍結済み送信本文とIDを、実送信前にmanifest・本文ハッシュ・キュー原文・未ACK行に照合する実装を追加。
- [x] 受信モデルの「PKは必ず昇順」という誤った制限を除去し、タイムスタンプ順によるPK逆転のテストを追加。
- [x] 凍結後のトークン更新を送信時に反映する実装・回帰テストを追加。
- [x] SQLite REALのタイムスタンプをdoctor/collect双方で拒否し、カーソル非進行の回帰テストを追加。
- [ ] Draft PR #7の最新コミットCIと独立コードレビューを確認し、必要な承認後のみPR #6へ統合。
- [ ] 本番GAS・rootless実機・権利/プライバシーは依然未検証。現段階で**配布禁止**。

根拠・未解決事項は `docs/CHAT_PREMERGE_REVIEW_20261029.md` を参照。
