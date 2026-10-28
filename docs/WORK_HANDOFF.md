# ChatGPT Work 引き継ぎ書 — LINE Auto Sync

## 2026-10-28 追加：通常ChatによるRC3安全レビュー（PR #6）

- RC3元ブランチ: fix/beta-delivery-safety / Draft PR #5（mainには未反映）。
- 追補ブランチ: audit/rc3-chat-hardening / Draft PR #6（PR #5への追加修正として提出）。PR #6も未マージのままレビューすること。
- runtime_paths.require_enabledでシンボリックリンクの設定・状態参照を拒否。
- legacy_names.Sessionで名前キャッシュのシンボリックリンクと不適切な状態ディレクトリを拒否。
- sender.SafeRedirectを同じHTTPS origin（ホスト名・ポートを含む）に制限し、GAS標準のscript.google.comからscript.googleusercontent.comへの応答用redirectはデフォルト443番のみ許可。
- tests/test_delivery.pyにリンク防御・ポート変更の回帰テストを追加。
- tests/test_receiver_contract.pyに、**本番GASではない**永続SQLite受信モデルを追加。完全受信、固定ACK前の重複再送、メッセージIDの競合、manifest/checksum、B/C1の検証。メール・カレンダー側のexactly-onceは保証しない。
- docs/RC3_CHAT_REVIEW.md（限定コードレビュー）、docs/GAS_RECEIVER_DESIGN.md（受信側の分離・耐障害設計）、docs/BETA_RELEASE_CHECKLIST.md（配布ゲート）、docs/LIMITED_SOURCE_SCAN.md（29 tracked filesの既知パターン限定調査）を追加。
- 既存の個人iPad/launchd/GAS/通知/カレンダー/タスク台帳は操作せず、公開ソースだけ調査・修正した。
- 次回はPR #6の最新CI・差分レビューを行い、問題なければ**PR #6を先にPR #5へ統合**、その後PR #5の独立レビューを行う。配布や実機テスト、mainへの統合は自動で行わない。
- 受信側GASの実装確認、第三者端末、包括的秘密/権利監査、リリース判断は依然未完了。

---

## 最新の再開位置：2026-10-28 / RC3 delivery safety

以下の古い作業履歴より、この節を優先する。

- 基点main: `609e25d1e1915350830835583c5c05923d93e6da`。取得時にユーザー引き継ぎと一致。
- 作業ブランチ: `fix/beta-delivery-safety`。個人用iPad・実LINE DB・GAS・既存通知・カレンダー・タスク台帳にはアクセス/変更していない。
- `delivery_state.py`を追加。自身のapplication_idを持つprivate SQLiteへ、取得済み行とcollectedを同一トランザクションで保存。送信前に本文/batch ID/対象PKを固定・確定し、受信OK後にACK対象/成功時刻/固定バッチ削除をまとめて確定。再送は同じ内容、失敗時は保持＋バックオフ。
- source_id/DBパスと実体/送信先をキューへ結び付け、変更を検出したら停止。LINE DBはmode=ro/query_only/読み取りスナップショット。取得済みの行は元DBから消えてもローカルに保持。
- 旧CSV/sentid/initial_scan_idは初回だけ採用し、以後は更新しない。新規環境は既存履歴を遡らない。曖昧・破損状態は停止。旧版との並行実行・新キューを無視するダウングレードは禁止。
- 長文より前の正常なprefixは送れるが、長文自体と後続は保持して停止。分割・自動回復は未実装。旧finish_only_heldで未送信位置をACK済みにする経路は拒否へ変更。
- 実通信はlegacy token/body/OKのまま。transport.pyのHMAC/構造化ACKとは別。タイムアウト後の重複防止は受信側の永続dedupeが必要で、クライアントだけでexactly-onceを保証しない。
- `status`を追加。URL内認証情報・不正なHTTPS URL・危険なredirectを拒否、HTTP応答や例外の本文をログへ出さない。
- 新ビルダー `tools/build_private_beta.py`：RC3 PRIVATE `0.1.2~rc3`。12モジュール＋launcher＋汎用READMEのみ。Python3.9/sqlite3/TLSのlauncher事前検査、control内容の読戻し検査を追加。旧build_private_rc2.pyは互換入口で、出力はRC3。
- `firmware >=15.0`を宣言。Python依存のパッケージID/バージョンは実機未確認なので憶測でDependsへ追加していない。バックグラウンドサービスなし、manual only、実機インストール未実施。
- 追加テストは合成SQLite/差し替えたHTTPのみ。取得/ACK前後のクラッシュ、再送固定、同時送信、CSV採用、長文prefix、偽ACK、送信先変更、redirect、foreign DB、再現可能なarchive/control検査を対象とする。
- 参照：BETA_TESTER_GUIDE.md、RECEIVER_PROTOCOL.md、PACKAGE_PLAN.md、SECURITY_AND_RIGHTS.md、GUI_OPTIONS.md。
- ローカル最終検証：20件PASS、RC3 PRIVATEのビルド/アーカイブ照合PASS。SHA256 `cd13e83a07ae136075d4b3823c81d1d8374df87b23174b962b4cd9b98bb43416`。これは今回の作業コピー由来の非公開成果物で、実機インストール・配布はしていない。
- 限定的な既知パターン検査：作業ツリー29ファイルと既存履歴31 blobで該当0。未知の秘密、コミットメタデータ、権利、運用環境まで保証する包括監査ではない。詳細レポートはignored dist内だけに保存。

### 次回も残す制限

独立rootless端末での権限/依存/実スキーマ/名前/導入削除、受信側GASの永続dedupe、包括的なソース・履歴・秘密情報審査、権利者の再配布許諾、実機での更新/ダウングレード防止は未完了。GUI比較資料のみ追加し、ネイティブGUI実装は始めていない。配布バイナリを公開しない。既存4件のテストを含む最終テスト/CI結果はPRを参照し、ビルド成功を実機保証にしない。

---

作成日: 2026-10-28 JST
Repository: https://github.com/Sat0T/line-auto-sync
作業ブランチ: `fix/rc1-review-readiness`
目的: 通常LINEのみのrootless脱獄iOS向け実験的同期コードを、技術者レビュー可能な状態に整理する。正式公開・実機での利用保証は**しない**。

## ユーザーの意図
- ユーザーは個人iPad上でLINE→外部HTTPS/GAS連携を利用中。
- rootless `.deb` 化し、実機テスターや脱獄開発者からレビューを得たい。
- OpenChatは対象外。別端末テスターは現時点でいない。
- Windows側での既存iPad操作・既存データの上書き・実メッセージ送信は禁止。
- 現在のGitHubはPublic。すべてGitHub上で編集できるなら自動実行を希望。
- 秘密情報、実LINEログ、トークン、GAS URLは公開しないこと。
- 機能・セキュリティ・再配布条件の未確認事項は隠さず記載する。

## 前史と成果物
- 開発版DEV7/DEV8からWindows側でプライベートRC1パッケージを構築済み。
- 当初の私人用ファイル名: `line-auto-sync_0.1.0-rc1_PRIVATE.deb`、18,356バイト。
- 当該古いアーカイブSHA256:
  `efa65aeb5c2e5551a9712b478df812a03ca91754ca2ef6bc6448d5558e240591`
- 古いパッケージは独立したテスト結果があり、CLI初期化/有効無効切替/HTTP再試行の模擬テストおよびアーカイブ整合性がPASS。ただし名前解決は模擬、実機未検証。
- 重要: GitHub上で修正中のソースは古い私人RC1 `.deb` と**一致しない**。GitHubには古いバイナリを公開しないこと。
- 以前「パッケージング100%」と呼んだのはWindowsオフライン構築の終了に限る。デバイス互換、ライセンス、プライバシーの保証ではない。

## 2026-10-28 GitHub 作業
Branch: `fix/rc1-review-readiness`
- `normal-only-rc1/cli.py`: 設定と名前解決の不足項目 `line_db` / `group_db` / `timestamp_unit` を揃え、ZMESSAGE必要列とUnixミリ秒のサンプルをdoctorで検査。データベースは読み取り専用。
- `normal-only-rc1/legacy_names.py`: `named-cache.sqlite` をコードディレクトリではなくSTATE_DIRに配置。名前変換失敗時は生データ送信へ黙ってフォールバックせず停止する。 
- `__pycache__` の2ファイルを削除、`.gitignore` にバイトコード/認証情報/ローカルDB等の除外を追加。
- `tests/test_offline.py`: 完全なダミーSQLiteによるCLIライフサイクル、名前変換、スキーマ異常、長文停止テスト（ネットワークなし）。
- `.github/workflows/offline-checks.yml`: Python3.9/3.12で構文チェックとオフラインテスト。
- `README.md`: テスト範囲/未検証/責任/権利の説明を明確化。

## 既知の重要な技術論点
1. `sync.py` は実LINE DBを `ZMESSAGE(Z_PK,ZTIMESTAMP,ZCHAT,ZSENDER,ZTEXT)` とみなす。LINEバージョン差、SQLite取得権限、その他のテーブル、DBロックは実機未検証。
2. 初回の既定動作は既存メッセージを遡らず高水位から開始する。同期済み `sentid` の扱いと `lastid`/ `initial_scan_id` の相互作用は厳密に監査すること。
3. `legacy_names` は `Resolver` と `transport`/ `presentation` を使用。名前マッピングは合成スキーマでのみテスト。日時は `unix_ms` 想定。
4. 送信成功判定は既存の受信側がテキスト `OK` を返すことを想定。リトライ後の重複、トークン/HTTPS送信先、GAS側の実装は未検証。v2 `transport.py` のHMAC/ACKとは別の送信プロトコルが存在する。
5. `normal_hold.retain` は長文で必ず例外を出す「fail-closed」。長文を回収・送信する機能はない。元コードの `finish_only_held` のカーソル移動経路は別途監査対象。
6. rootless `.deb` にPython依存やlaunchdは同梱なし。手動CLI。iOSの実環境でのパーミッション、サンドボックス、所有者は未検証。
7. 公開済みソースの個人情報・秘密情報を検査したが、網羅的な監査・再配布権限の法的確認は未完了。
8. このブランチの修正は**未テストの可能性がある**。GitHub Actions実行状況/失敗内容を必ず確認し、失敗時に修正する。
9. `README_TESTING.txt` は古いRC1用で、一部手順/制約が変更済み。更新版が確認されるまで正式な導入説明として扱わない。

## Workで次にやること（優先順）
1. GitHubブランチ/PR/Actions実行結果を取得し、合成DBオフラインテストを成功させる。lint/ASTや内容の重複も検査する。
2. `cli.py` の `configure`→`doctor`→`enable` と `legacy_names` が正常連携することを確認する。失敗時のカーソル非進行、同期/送信中の異常終了をチェックする。
3. `sync.py` のメッセージカーソルのデータ消失・重複リスクを重点監査。決して実メッセージを送信しない。
4. 受信側のプロトコルを仕様書に分離し、既存 `sender.py` とv2 `transport.py` の違いを明示。
5. Pythonのrootless実行パス、.deb control/deps/ownershipの設計をレビュー。最新版ソースから再現可能なリリースビルド・構造検証・hash生成手順を整備する。
6. すべてのコードを秘密情報・ライセンス面で監査し、不明点は事実として残す。
7. ソースコードレビューを優先し、`.deb` は実機安全確認まで配布しない。必要ならドラフト/プレリリース。
8. GitHub Issuesに技術レビュー項目のテンプレートを作り、外部へ公開する前に削除可能なダミーデータだけを入れる。

## 安全ルール
- iPad本番環境にインストールしない。LINE DBを更新しない。実GASへ送信しない。
- LINE本体の機密コードや第三者のコードを権限なしに含めない。
- 公開Git履歴から誤って秘密情報が出た場合、履歴削除だけに依存せず、認証情報の失効・再発行を案内する。
- 変更をしたらブランチ名/コミット/PR URL/CI状態/未解決事項をユーザーへ報告する。
- 他のiPadで動作したと偽らない。実機テスト担当者の明示的な協力が必要。

## 依頼する作業
以上を引き継いでリポジトリの状態とテストを確認し、残る実装問題を安全に改善して、技術者レビュー可能なPublic GitHubドキュメントを維持する。ユーザーが改めて承認するまで、既存iPadへの操作や実メッセージ送信はしない。


## 2026-10-28 追加：非公開RC2の再現可能なWindowsビルド
- ブランチ `build/reproducible-private-rc2` / Pull Request #4 に、`tools/build_private_rc2.py` を追加した。
- Windows、Python 3.9以降のPCで `py tools/build_private_rc2.py` を実行すると、合成SQLiteのオフラインテスト後、`dist/line-auto-sync_0.1.1-rc2_PRIVATE.deb` と `dist/RC2_BUILD_REPORT.json` が作られる。
- ビルドは11モジュールの許可リスト、Python 3.9構文検査、既知の秘密パターン、重要安全ガードの存在確認、生成アーカイブの展開照合を含む。SHA-256を表示する。
- GitHub ActionsではPython 3.9/3.12で同じビルドを試し、生成物は一般公開用アーティファクトとしてアップロードしない。
- RC2は古いRC1のバイナリを再利用しない。なお、再現可能なビルドの成功はiOS互換性・実データ送信・再配布権限・包括的秘密情報審査を証明しない。インストール・外部配布は保留する。
- Workへの指示：PR #4のCI状態を確認し、マージ後に最新版でビルドを再実施。受信側プロトコル・実LINE DBスキーマ・所有者権限・プライバシー/ライセンス確認を優先する。
