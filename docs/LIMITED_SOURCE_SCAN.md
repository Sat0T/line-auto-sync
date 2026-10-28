# Limited public source content scan — 2026-10-28

Target: PR #5 head commit c9ad70b9adbc02f28fc434543579974e3ceb803e
(the RC3 draft), not the private iPad or GAS. Only the 29 tracked text
files in that one Git tree were checked with known-pattern searches.

Patterns searched: obvious deployed GAS /macros/s/.../exec URLs, email address
shapes, common GitHub personal-access tokens, Google API key shapes, private key
PEM headers, OpenAI API keys, and personal Windows user-home paths.

Result: no matching obvious credentials or personal Windows-home paths in
28 files; one email-shaped match in tests/test_delivery.py is the intentionally
synthetic string secret@example.invalid inside a URL-credential rejection
test. It is not a real email address. No real-looking GAS deployment URL,
GitHub PAT, Google API key, private-key marker or OpenAI key was detected.

IMPORTANT: This does NOT certify secrets absent. The check did not inspect
all Git history commits, commit author metadata, release/artifact archives,
environment configuration, untracked files, old backups, iPad data or any
external GAS/Calendar/Gmail systems. Regexes cannot reliably detect unknown
token formats or copied conversation text. No rights or privacy attestation.

Next independent release checks:
- Scan all Git history and GitHub release assets with an established scanner,
  review findings privately, and rotate any exposed secret rather than merely
  deleting code.
- Confirm code provenance, licenses, supported recipient permissions and
  consent. A public repository is not permission to redistribute third-party
  code or other people's communications.
- Inspect package archive and build logs for private messages, config,
  endpoint identifiers and names. Work with synthetic data only.
- Repeat these reviews at a pinned release commit and document sign-offs.

See SECURITY_AND_RIGHTS.md and BETA_RELEASE_CHECKLIST.md. No production
configuration or private data was accessed by this scan.

## 追加検査：全参照Git履歴（2026-10-28）

GitHub Actionsで `tools/audit_git_history.py` を実行。到達可能なGit履歴を横断する自動監査を導入し、コミットメタデータと、過去に削除されたファイルを含むblobの既知パターンを調査する。値はCIログへ出さない。

**基準の実行結果（コミット `1b0767e7`）：** 43コミット、70blob（うちバイナリ2）、16MiB超の未検査blob 0。既知の認証トークン・秘密鍵・Google Apps Script公開URLパターンは検出されなかった。

**未解決のプライバシー注意点：** 過去のバイナリに、Windows利用者ホーム配下の絶対パスを示唆する文字列を2blobで検出。コミットメタデータにもGitHubの非公開用noreply形式ではないメールアドレス2種類が含まれる。実際の値はレポートに記載しない。現行配布物の混入ではないが、公開Git履歴から直ちに消えるわけではないため、本人が履歴公開範囲・メール設定・必要な削除手段を検討する必要がある。履歴の強制書き換えは**行っていない**。

CIは認証情報の高確度パターンを阻止し、パスやメールといった履歴上のプライバシー検討項目は警告に分離している。**CI成功は、履歴に個人情報がないことや配布を承認することではない。** バイナリ内部の意味解析、未知形式のキー、第三者のメッセージ、外部環境、完全なライセンス検証は引き続き別途必要。
