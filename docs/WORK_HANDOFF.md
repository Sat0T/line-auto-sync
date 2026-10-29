# Work再開チェックポイント — 2026-10-29 / 状態DBの破損防御

この節を過去の記録より優先する。
1. 完了: PR #7 HEAD `fac5f22e8f5e139b14c50e55843694ea9747c35f` と最新CI成功を確認。既存DBの非破壊スキーマ/必須メタデータ検査と新規DBの原子的初期化を実装し、ローカル45件の合成テスト成功。
2. 作業ブランチ: `fix/rc3-offline-queue-guards`。Draft PR #8 https://github.com/Sat0T/line-auto-sync/pull/8 → PR #7 `audit/chat-rc3-reliability-gates`。Chat側と衝突しない。最初のコード保存SHA `69556909fbc6c5daa49afffb50a4f32323a1dc63`。以後の最新SHAは本書のGitHub履歴で確認。統合は #8→#7→#6→#5→main、各段階で本人承認。
3. 今回の変更: `normal-only-rc1/delivery_state.py`、`tests/test_delivery.py`、`docs/BACKUP_RESTORE_TEST_PLAN_JA.md`、本書。既存DBではCREATE/修復しない。新規DBの3テーブルとapplication_idは同一トランザクションで確定。
4. 基点の最新CI: https://github.com/Sat0T/line-auto-sync/actions/runs/40723686017 （completed/success）。最初の42件版 `6955690` もCI成功: https://github.com/Sat0T/line-auto-sync/actions/runs/40842917407 。続くメタデータ検査を含むローカルPython3.12の45件PASS。追加計8ケース: テーブル欠落と隔離バックアップ復元、制約欠落、不明trigger、壊れたヘッダ、初期化中断、必須キー欠落、空metaと既存行、不正カーソル/identity/成功時刻。初回テスト追加時の引用符構文エラーは修正済み。45件版のリモートCIはこれから確認する。
5. 修正した問題: 不足テーブルの自動再作成、および欠落した取得位置/送信先bindingを再初期化する可能性。既存DBは読み取り専用で既知スキーマと必須metaを照合する。identity/取得/ACK/初期位置と成功時刻の形式・範囲を検査。初回収集前の完全な空DBは許可し、旧CSV由来でACKがcollectedを超える状態は互換性のため拒否しない。全ページ/全内容検査ではない。既にメッセージもmetaも完全消去された空状態と真正な新規空DBは区別できない。同一権限攻撃者への防御は保証しない。将来スキーマ変更には明示的移行が必要。実GASdedupe・独立実機・履歴PII/権利のP0は未解決。
6. 再開位置: スキーマ版はGitHub保存/CI成功。メタデータ追補もコードと本書を同時保存しCI確認する。破損DBは削除・修復せず保全、別の非公開スナップショットで隔離復元する。
7. 本番影響: なし。iPad/実LINE/GAS/通知/カレンダー/タスクには未アクセス。インストール/配布/マージなし。
8. 次: 45件版CI確認→本書に結果保存。次の開発候補はHTTP中のSQLite書込ロック軽減（排他/クラッシュ/重複再送の設計を先に精査）。P0は受信側の秘密除去済みコピーと独立実機・本人判断が必要。長文/容量/後完成・編集行追跡/ダウングレード/GUIは未解決。
9. 本人承認: PRマージ・実機導入・バイナリ配布・公開履歴改変・権利判断は必要。本番受信側コピーも許可なしに取得しない。
10. 次セッション指示: 本節とブランチ最新差分/CI/利用枠を確認し、途中の状態DB防御から継続。残り30%以下は大規模実装なし、20%以下はコード修正停止、10%以下は保存/報告のみ。GitHub保存を最後に先送りしない。

---

# 最新再開情報 — 2026-10-29 / Chatの追加レビュー・PR #7

**この節を以下の古い引き継ぎより優先。** 作業内容はGitHubでのみ保存。Workの利用枠を消費する実機・GAS・PC操作は行っていない。

## 完了したこと
- Draft PR #7: https://github.com/Sat0T/line-auto-sync/pull/7
- PR #7 ブランチ `audit/chat-rc3-reliability-gates` → PR #6 `audit/rc3-chat-hardening`。PR #6 → PR #5 `fix/beta-delivery-safety`、PR #5 → main。**3つともDraft・未マージを維持**。
- 受信モデルのPK昇順必須という誤った前提を除去。実送信形式は時刻・PKでソートされるため、逆転ケースを含む模擬受信テストを追加。
- `DeliveryState.verify_frozen` を追加。送信直前に凍結済みmanifest・source・PK一覧・バッチのハッシュ・キュー本文ハッシュ・未ACK状態を照合。整形式でも誤った凍結済みIDs、本文改ざん、キュー改ざんは送信を止め、ACKを更新しない。
- `sender.py` は凍結後に有効設定を読み直し、トークン更新後は現在のトークンで送信。
- `cli.py` doctorと `delivery_state.py` 収集時のUnixミリ秒検査をResolverと一致させる。非整数・範囲外日時を拒否し、収集カーソルを誤進行させない。
- `tests/test_delivery.py`, `tests/test_receiver_contract.py`, `tests/test_offline.py` に凍結IDs改ざん・内容改ざん・認証更新・逆転タイムスタンプ・SQLite REAL拒否・収集失敗後の回復の回帰テスト追加。
- `docs/CHAT_PREMERGE_REVIEW_20261029.md` を作成。 `docs/RECEIVER_PROTOCOL.md`、`docs/BETA_RELEASE_CHECKLIST.md` も修正。
- 作業対象はすべて公開GitHubの合成データ/候補ソースのみ。本番iPad・LINE DB・GAS・Gmail通知・カレンダー・タスク台帳は**無変更・未アクセス**。PRのマージ、実機導入、バイナリ配布も行わない。

## 検証状況
- PR #6のWork引き継ぎ時点の30件テスト＋Git履歴監査が全環境成功： https://github.com/Sat0T/line-auto-sync/actions/runs/40720726909
- PR #7の初期コード（凍結整合性・PK時刻順対策）も5ジョブ成功：https://github.com/Sat0T/line-auto-sync/actions/runs/40722362213
- 追加時刻検査や改ざんケースを含むコードコミット `3fdd227d` のCIも成功：https://github.com/Sat0T/line-auto-sync/actions/runs/40722767872
- 整形式だが誤った凍結IDsの試験を含む**37件**をWindows/Linux × Python3.9/3.12の全4環境でPASS。履歴監査もPASS（計5ジョブ）：https://github.com/Sat0T/line-auto-sync/actions/runs/40723331727 （検証時コードと引き継ぎ更新HEAD `8f70b839`）。後続の文書更新はソース変更を含まない。次回は引き続き最新HEADのCIを照合する。
- CI成功は合成SQLite、模擬HTTP、rootless .debのオフライン構造検証のみ。実LINE/GAS/独立端末は未検証。

## 残る重大な問題
1. 実GAS受信側のバッチ永続dedupeとACK保証、Googleサービスへの副作用の再試行（本番の安全なコピー未入手）。
2. 独立したrootless脱獄端末の実機インストール・Python/SSL/SQLite依存/権限/実LINEスキーマ/アンインストール・復旧。
3. 包括的権利・再配布・個人情報・同意確認。過去GitバイナリのWindows個人パス2件、作者メール2件の本人判断。歴史書換えはしない。
4. 送信中のSQLite書込ロックとHTTPタイムアウト、巨大メッセージの先頭詰まり、キュー保持上限、秘密情報暗号化、旧版ダウングレード阻止、GUI実装。
5. LINE DBの高水位PK方式では後からテキストが完成・編集された既存行を確実に同期できる保証がない。

## 次のWorkへの一行指示
**PR #7の最新HEADとGitHub Actionsを確認→差分レビュー・追加異常系の精査→本書を更新・コミット→必要に応じ独立した別ブランチで修正。** PR #7を先にPR #6へ、PR #6をPR #5へ統合するには本人承認。mainへの統合とバイナリ配布は別の承認。稼働中iPad/GASは触らず、実GASの確認は本人が提供する秘密除去済みコピーのみで実施する。作業の途中にも最新引き継ぎ書を保存する。

---

# 最新再開情報 — 2026-10-29 JST

この節を以下の過去記録より優先する。**作業開始時・重要変更/テスト完了時に本書を作業ブランチへコミットし、最後まで保存を先送りしない。**

## 1. 最後に完了した作業
2026-10-29にGitHubでPR #5/#6、本書、PR #6最新HEADのCIを確認。利用枠が残り約5%のため、新規実装・再テストを開始せず、最新再開情報の保存に限定した。

## 2. ブランチ・PR・コミット
- PR #5: https://github.com/Sat0T/line-auto-sync/pull/5
  - `fix/beta-delivery-safety` → `main`、Draft/open/unmerged
  - 確認済みHEAD: `c9ad70b9adbc02f28fc434543579974e3ceb803e`
- PR #6: https://github.com/Sat0T/line-auto-sync/pull/6
  - `audit/rc3-chat-hardening` → `fix/beta-delivery-safety`、Draft/open/unmerged
  - 本更新直前の確認済み最新HEAD: `b9536d5daedccc700382a3b3ad6c5b28c08a55d2`
- 本書更新コミットのSHAはGitHub上の本ファイルの最新履歴から取得する。自己参照によるSHA更新の繰り返しは行わない。更新コミット後の最新CI状態はActionsで確認する。
- 作業再開ブランチはPR #6側。PR #5には既に包含される変更を重複実装しない。マージは承認が必要。

## 3. 変更・追加ファイル
今回の変更は `docs/WORK_HANDOFF.md` のみ。ソース・設定・テスト・バイナリは変更しない。
PR #6の既存変更: runtime_paths.py / legacy_names.py / sender.py の安全性強化、tests/test_delivery.py、tests/test_receiver_contract.py、tools/audit_git_history.py、CI、監査・受信設計・復旧・β配布条件・GUI仕様の文書。実際の全差分はPR #6を参照。

## 4. テスト・CI
- PR #6確認済みHEAD b9536d5 の最新CI: **completed / success**。
  https://github.com/Sat0T/line-auto-sync/actions/runs/40666027834
- 30件の合成テストがWindows/Linux × Python3.9/3.12で通過した履歴:
  https://github.com/Sat0T/line-auto-sync/actions/runs/40665036714
- 今回は既存テストを再実行していない。合成テスト成功は実LINE/GAS/iOS導入の証明ではない。

## 5. 未解決の重大課題
1. 実GAS受信側の永続dedupeとACK耐障害性は未検証・未実装確認。本番コピーを無断で取得/公開しない。模擬SQLite受信モデルは本番実装ではない。
2. 独立したrootless端末でのPython提供パッケージ、所有者/権限、導入/更新/削除/復旧、LINEスキーマ互換は未検証。
3. 過去のバイナリ2blobのWindows個人パス痕跡、作者メタデータのnon-noreplyメール2種類が要本人判断。実値は本書へ転載しない。履歴書換え・鍵交換・露出是正は未実施。
4. 包括的な秘密/PII/権利/再配布許諾の審査は未完了。履歴の既知パターン検査成功だけで配布を許可しない。
5. 長文先頭は保持して後続も停止する。分割回復、キュー容量/保存期間、実際のダウングレード防止は未完成。
6. GUIは仕様のみ。バックグラウンドサービスはこのβ版にない。手動実行のみ。

## 6. 実装途中・再開位置
今回の未保存コード・途中実装はない。GitHub PR #6が再開元。
ローカル作業コピーは古いPR #5 HEADのままの可能性があるため、必ずGitHubの最新HEADを取得して照合する。クリーン確認なしのreset/上書きを行わない。過去のローカルprivateビルドをPR #6最新版と同一扱いしない。

## 7. 本番への影響
なし。iPad・LINE DB・本番GAS・通知・Googleカレンダー・タスク台帳にはアクセス/変更/停止していない。PRマージ、実機インストール、バイナリ配布はしていない。

## 8. 次回の最優先作業
まず利用枠とPR #6最新CIを確認し、本書を更新・コミットする。その後PR #6の重大課題を差分レビューで絞り込む。隔離された受信側の実装/検証へ進める前に、公開可能な汎用受信契約と実装対象を確認する。本番GASの調査は本人承認・秘密情報除去済みコピーが必要。
実機/権利判断待ちの間は、承認不要のオフライン安全性課題を小さな単位で修正し、その都度テスト→本書更新→コミット。既に修正済みのsymlink/redirect防御を再実装しない。

## 9. 人間の承認・操作が必要な事項
PRマージ、実機への導入、バイナリ配布、公開履歴の書換え/プライバシー方針、権利/同意の判断、隔離実機テスター確保、本番受信側コピーの取扱い。
利用枠: 残り30%以下は大規模実装を始めない。20%以下はコード修正を停止。10%以下は保存と最終報告のみ。取得不能でも作業単位で保存する。枠を使い切ることを目標にしない。

## 10. 次のWorkへの再開指示
LINE Auto Syncのクローズドβ準備を継続してください。まず本書の最新節、PR #5/#6、最新HEADとCI、利用枠を確認し、本書を更新してPR #6の作業ブランチへコミットしてください。既存30件の合成検証と実環境未検証を区別し、重大課題から小さく作業してください。重要変更や検証のたびに本書を保存・コミットし、終了前はCI確認→最終引き継ぎ更新→PR/引き継ぎURLと次の指示を報告してください。本番環境は触らず、マージ・実機導入・配布は事前承認まで行わないでください。

---

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

## 2026-10-28 追加：通常Chatの追加監査・復旧/負荷テスト

- 作業ブランチ `audit/rc3-chat-hardening`（Draft PR #6）に追加。ベースは引き続きDraft PR #5の `fix/beta-delivery-safety`、mainには未反映。
- `tools/audit_git_history.py` を追加。CIで到達可能なGit履歴のblob・作者メタデータを検査し、鍵の値やメールアドレスを表示せずカテゴリ・件数だけ報告。歴史上のバイナリにもバイト列パターン検査を行う。
- 監査サンプルで43コミット/70blob/バイナリ2を調査。認証用高確度パターン未検出だが、過去のバイナリ2blobで個人用Windows絶対パスの痕跡を検出。noreply以外のコミットメール2種類あり。公開履歴のプライバシー問題を人間が確認し、強制書き換えはしない。
- `tests/test_delivery.py` に4テスト追加：SQLiteバックアップ→隔離復元で固定body/ids・pending保持、模擬受信後ACK喪失と安定再送、600件キュー、破損した固定idsで送信停止。既存26件から合計30件に拡充、Windows/Linux×Python3.9/3.12でPASS（コミット `db2e47c2` のCIに根拠）。
- `docs/BACKUP_RESTORE_TEST_PLAN_JA.md` に復旧・WAL/権限・同一時点のバックアップとダウングレード禁止を明記。
- 個人用ローカルファイル`PERSONAL_PRACTICAL_HANDOFF_20261028.md` / `ipad-v2/PROGRESS.md` はこのChat実行環境から取得できず、接続済みDriveにも同名ファイルを見つけられなかった。本番GASは**未監査**として維持。
- 稼働中のiPad/GAS/通知/カレンダー/タスクは無変更。本番iPadにRC3を入れず、バイナリ公開・Draft PRマージもしない。
- 次の判断：歴史的なパス/作者メール公開の扱いをオーナーが決める。受信側の**コピー**を監査し、独立したrootless端末でインストールと復旧を検証する。β版配布は引き続き未承認。

## 2026-10-28 追加：GUIの仕様案
- `docs/GUI_SPEC_JA.md` を追加。Sileoでの導入UIと設定GUIを分離。設定ウィザード、診断、キュー件数、手動実行、停止、復旧案内、エラー表示、秘密情報の表示禁止、固定操作だけを受けるバックエンド契約を設計。
- GUIは**まだ実装せず**、betaの安全確認・権利/本人同意・独立rootless端末での検証後に別PRで取り組む。
