# iPad復旧後のMVP確認

現時点はPCでの合成試験とパッケージ検証まで。実機の脱獄方式、rootlessの配置、Python、LINE DBのスキーマ、前景動作は復旧後に確認する。

## 最初に保全する

1. 本人が従来の方法で脱獄環境を再有効化したら、従来のアプリとファイルが見えるか確認する。機種・iPadOS・従来の脱獄方式と、以前使っていたcollectorの配置・起動方式を記録する。
2. 以前のcollectorが動いている場合は、従来の方法で停止してから私有領域へバックアップする。従来の実行ディレクトリ、設定、SQLiteキュー、WAL/SHM、旧カーソルを保全する。旧版のパスや形式が不明なら、その環境へのインストールや置換は保留する。
3. 新版と従来のcollectorは別の設定・キューで運用する。既存のキューを新sourceや新URLへ向け直すと、バインディング検査が停止する。過去の未送信データは、同じ設定・同じ保存先へ再送するか本人が判断する。

## 私用パッケージの確認

`python tools/build_private_beta.py --output-dir dist/ipad-mvp-rc6` で現行ソースから構築する。付属のRC6_BUILD_REPORT.jsonでSHA-256と内容の検証結果を確認する。RC4の展開ディレクトリ不足はRC5で修正し、実機で確認した。RC6の導入は別途承認と実機確認が必要。パージや強制オプションは使わない。インストール先はrootless用の `/var/jb`。Python 3.9以上とsqlite3、sslが必要。mobileユーザーで動かす。

## 復旧確認と新sourceの準備

対象の新版設定とキューがすでにあるときは、`line-auto-sync recovery-check` でDBのスキーマ・時刻、未送信数、凍結バッチ、直近ワーカー状態を読む。doctorはLINE DBを読み取り専用で開き、メッセージ本文を表示しない。別のテスト環境を作る場合は、シェルごとに次を設定する。

```sh
unset LINE_AUTO_SYNC_CONFIG_DIR LINE_AUTO_SYNC_STATE_DIR
export LINE_AUTO_SYNC_BASE_DIR="$HOME/Library/Application Support/LineAutoSync-MVP-Test"
line-auto-sync init
line-auto-sync configure
line-auto-sync doctor
line-auto-sync status
```

configureでは、権限を持つDBの絶対パス、専用のsource ID、承認済みHTTPS URLとトークンを入力する。トークンは非表示。合成専用のMVP_DEMOと実機sourceは別。新しい実機sourceを使う場合、独立した受信先のLAS_SOURCEと一致させ、既存ChatGPTがそのsourceを読む変更は別途承認を得る。初回の新規キューは既存履歴を基準位置にし、その後の新着を収集する。

日常運転用の `MVP_IPAD_LIVE` では、configureの直後、最初の収集より前に `line-auto-sync restrict-chats CHAT_PK [CHAT_PK ...]` を使う。本人が承認した通常LINEの内部トークIDだけを指定する。初回は収集専用で確認する。範囲を変える場合は別の新規プロファイルとキューを作る。既存の試験キューと旧カーソルは保全する。

## 起動、状態、停止

```sh
line-auto-sync enable
line-auto-sync start
```

startは前景で既定120秒ごとに収集する。2つ目のターミナルで同じ環境変数を設定し、`line-auto-sync status` でキュー、`line-auto-sync worker-status` で直近の状態、前景ターミナルでWORKERログを見る。時刻が古いrunningは異常終了後に残る場合がある。二重起動はOSの排他ロックで拒否する。

送信が本人承認済みの場合は、収集専用ワーカーを停止した後に `line-auto-sync start --send`。保留・凍結バッチや旧状態ファイルがあると起動を拒否する。保留の内容と送信先を本人が確認して再送するときだけ `line-auto-sync start --send --resume-pending`。手動のrunも送信する。送信失敗時は同一バッチを保持し、既存senderの再試行時刻に従う。

`line-auto-sync stop` または前景でCtrl+C。送信中なら現在のリクエストの完了を待ち、`worker-status` のstoppedを確認する。続けて `line-auto-sync disable`。無効化した新版の設定とキューは `line-auto-sync snapshot NEW_PRIVATE_DIRECTORY` で、既存ランタイム外の新規私有ディレクトリへ保存できる。snapshot.jsonのハッシュをバックアップ確認に使う。復元は元の構成とDBの同一性を確認し、停止中に元データを別保管した上で本人が行う。秘密を含むためGitHubや配布パッケージへ入れない。

前景プロセスのため、ロック中・再起動後に自動で続くかは実機検証が必要。失敗時はまずdoctor、statusと時刻、バッチの次回試行時刻、独立GASの受信台帳を確認する。

## タスクの扱い

独立Spreadsheetのtasksで確認する。本人が完了・取消したらowner_stateをcompleted/cancelledにする。同じAI結果の再取込では、行も本人状態も維持する。確認待ちはneeds_review。実LINEが受理された場合も、候補の判断は本人が行う。

## Keepメモ専用のバックグラウンド運転（実機検証中）

RC6の既存CLIを一時的なlaunchdジョブからmobileユーザーで起動する。rootlessのPythonとCLIを使う。試験ごとの古いキューと稼働中の既存ジョブには触れない。専用プロファイルは `/var/mobile/las-keep-daily`。一時登録なので、脱獄の再有効化後は手動で再登録する。

1. 同じディレクトリにある `keep_launchd.py` と既存 `rc6_keep_check.py` をmobileで使う。初回のみ `python3 keep_launchd.py prepare`。確認済みのKeepメモだけを設定し、履歴を初期位置とした新しいキューを作り、無効の状態で終える。
2. ロック診断は `python3 keep_launchd.py probe ./las-probe.plist`。専用ログ `/var/mobile/las-keep-daily/lock-probe.jsonl` には時刻とKeepメモの最大メッセージ番号だけを5秒間隔で記録する。診断中は外部送信なし。時刻の連続性と、別端末から追加した新着がロック中にDBへ反映されたかを別々に判定する。前回の診断ログがある場合は別名に保全してから再試験する。
3. ジョブは `sudo install -m 644 ./las-probe.plist /var/root/las-probe.plist` でroot管理の一時ファイルへコピーし、`sudo launchctl bootstrap system /var/root/las-probe.plist` で開始する。停止は `sudo launchctl bootout system/com.sat0t.line-auto-sync.keep`。実行状態は `sudo launchctl print system/com.sat0t.line-auto-sync.keep`。別のモードは同じラベルを使うので、先に前のジョブを停止する。
4. 収集・送信運転では先に `LINE_AUTO_SYNC_BASE_DIR=/var/mobile/las-keep-daily` を設定し、既存CLIの `recovery-check` と `enable` を使う。設定・状態の上書き用環境変数は解除する。手順3と同じ方法で `collect` または承認済みの `send` モードのplistを登録する。キューが保留・凍結していると通常の送信起動は拒否する。本人がそのキューの同一宛先への送信を確認済みの場合のみ `resume` を使う。
5. 通信断試験は承認済みのKeepメモ1件を対象とし、再接続前に保留・凍結・試行回数を、再接続後に送信時刻とGASの1件だけの受理を確認する。停止時は既存CLIの `stop` でネットワーク処理の終了と `worker-status` の `stopped` を待ち、ジョブを解除して `disable`。必要なら `snapshot` で未送信キューを保全する。
6. 再起動で脱獄が無効化されたときは、従来の手順で再有効化し、同一の専用プロファイルで `recovery-check`。保留を確認してから同じジョブを再登録する。再起動時に `prepare` や `init` は不要。
