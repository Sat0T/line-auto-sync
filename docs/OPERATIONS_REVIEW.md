# 日常運用の確認範囲と今回の最小修正 — 2026-11-25

端末・既存キュー・GAS・Gmailへ操作していない。今回の対象は通常ランタイムの運用確認であり、新着のロック中配送や自然なChatGPT定期実行の成功証拠ではない。旧2件、旧未配送文、旧キューを再送していない。

## 修正した不具合

通常workerは、送信しなかった`retry_wait`、`hourly_gate`、`empty`等も、errorがないだけで`last_result:ok`と記録していた。通信復旧待ちと実応答の区別がつかないため、`runner.py`の状態表示だけを最小修正した。

| 新しいlast_result | 確認できた段階 |
| --- | --- |
| `delivery_unconfirmed` | 応答不明または失敗。未ACK・凍結状態を保持 |
| `retry_wait` | 再試行の時刻前。今回のHTTPは行っていない |
| `hourly_gate` | 通常送信間隔の待機。今回のHTTPは行っていない |
| `empty` | 今回送る未ACK行なし |
| `already_acknowledged` | 送信開始前の再確認で固定中のrequestなし |
| `application_accepted` | senderがstrict legacy OKを受信。実GAS台帳・Gmail到着は別確認 |
| `delivery_not_attempted` | 上記以外の送信結果。未知のreason文字列をログへ複製しない |

送信契約、再試行間隔、送信件数、ACK操作、キューのスキーマや保持期間は変更していない。`application_accepted`をメール配送完了とは扱わない。

新規の変更直結試験3件を実施し成功した。合成SQLiteと実sender/workerを使い、通信失敗→期限前待機→同一bodyの再試行成功→通常間隔待機を区別して記録すること、待機中に停止しても未ACKの凍結body/試行回数/次回時刻が残ること、不明なreasonや矛盾したreportを成功・生ログにしないことを確認した。外部接続は禁止した試験であり、実GASや実機通信断を再現したものではない。

## 再利用する既存実装・検証

既存179件の全体試験を調査のためだけに再実行していない。次の実装と既存の合成coverageを読み直し、同じ機能を作り直していない。

| 項目 | 現在の動作と限界 |
| --- | --- |
| 通信断後の再試行 | 通常senderは未ACKのrequestを固定し、試行回数と次回時刻をSQLiteへ保存。60秒から最大3600秒のbackoff。動き続けている通常workerが、通常の周期で同じbodyを再試行する |
| 受理後の応答消失 | ACKされない同一batch/body/対象IDを保持。模擬receiverでは永続重複排除を確認済み。実配備の受信契約・台帳との対応は別途必要で、exactly-onceを保証しない |
| 収集・送信中の異常終了 | SQLite取引は行と収集位置を一緒にcommit/rollback。送信はHTTP中にSQLite書込み取引を保持しない。OSプロセス終了でlockは解放され、固定requestが残る |
| 異常終了後の再開 | 通常送信workerの既存pending/frozenを伴う再開には`--send --resume-pending`が必要。今回の日常用launchd entryは同じ承認済みprofileの再試行を明示して再開する準備を追加。実機への導入・再起動は未確認。N1の期限付きsupervisorと日常用サービスを混同しない |
| キューの分割 | 未ACK行を最大500行ずつ取り、送信サイズ上限でprefixを固定。既存600行の合成backlog試験あり。先頭の長文や破損は保留し、カーソルを進めて捨てない |
| 二重起動と変更検出 | worker/senderのOS lock、source/DB同一性/送信先binding、凍結body/対象行/manifest/hash照合を使う。既存状態を初期化して復旧しない |
| 停止・スナップショット | disableとrun_id付き停止要求、停止後にworker/sender lockを取った専用SQLite backupを使う。回復元は別の私用保存先で比較し、元のキューを上書きしない |

既存の関連試験は`tests/test_delivery.py`、`tests/test_receiver_contract.py`。新規は`tests/test_operational_status.py`。保存済み対象を再送してこれらの証拠を作り直す必要はない。

## キュー・ログの増加と未検証の制約

通常の`messages`のACK済み行と`named-cache`は保持され続ける。これらは重複除外・内容整合性の記録でもあるため、今回のデータ保全条件で自動削除やretentionを追加していない。空き容量不足や大規模時の実時間・必要容量は未測定で、600件の合成試験を長時間運用の保証にしない。空き容量不足時に送信済み・未送信の行を自動消去して続行する仕組みはない。

通常worker内で追記し続ける履歴ファイルは作っていない。`worker-status.json`は最新状態1件を原子的に置換し、標準出力は周期ごとの短い段階名。外側でstdout/stderrを無期限のファイルへ保存する場合のrotationは未実装。期限付きN1証拠ログは実行上限を持つ別物であり、日常用の無期限ログ設定へ転用しない。既存の証拠ログは削除しない。

通常HTTPの`timeout=30`はurllibの通信待ち設定で、連続redirectや断続的な応答を含む全処理の絶対wall-clock上限の証明ではない。N1は別プロセスと端末supervisorの期限で制限している。日常用サービスで同じ終了上限を保証するには、対象端末上の監視・停止・再開を別途確認する必要がある。

## 再開条件

### 日常用collect/sendサービスの導入準備

`tools/prepare_daily_worker.py`は、停止・無効化済みの単一許可トークprofileと、既存管理CLIで版照合して配置したstageのmanifest/runtimeを読み、新しい保存先へentry/plist/planを生成する。生成だけではインストール・enable・launchd起動・LINE DB読み取り・外部送信を行わない。

通常はcollect-only。送信を設定する場合だけ、**`--send --confirm-delivery-binding`**を付ける。この確認flagは、Workが実配備GASの受信先・本人宛転送先と対象profileを照合したというoperatorの記録で、メール配送やGAS受理の実証ではない。独立GAS HTTPS設定、`MVP_IPAD_LIVE`、許可Keep1トークを要求する。

初回の送信準備・起動は旧queue/legacy/cacheのない新しい日常profileだけを受け付け、旧profileや未配送試験文を取り込まない。最初のentryが`daily-activation.json`を排他作成し、以後は同じ設定bindingの承認済み日常stateを再利用する。異常終了後にfresh stateを作り直さず、既存senderの固定request・backoff・ACKを`resume_pending=True`で再開する。設定の変更は正常停止させ、勝手にbindingを更新しない。

```sh
python3 prepare_daily_worker.py --profile PRIVATE_DAILY_PROFILE --stage VERIFIED_MANAGED_STAGE --output /var/mobile/las-daily-send-service --send --confirm-delivery-binding
```

送信モードの出力は`daily-send.py`、`daily-send.plist`、`plan.json`、`daily-send-control.py`、`daily-root-bootstrap.py`。デフォルトcollect-onlyは従来の3ファイル。runtime全ファイルのhash、DBポインター、profile/stage/LINE DB/output境界を照合し、既存出力を上書きしない。生成時は既存の`manage_ipad.py`を同じtoolsディレクトリに置く。生成後のentry/controller/bootstrapは自己完結している。

plistはmobile実行、RunAtLoad、`KeepAlive: {SuccessfulExit: false}`、ThrottleInterval60秒。無効状態での起動・通常協調停止・disable・設定変更は終了0なので再起動ループにしない。未捕捉の異常終了だけをlaunchdの再起動対象にする構成。通常停止はCLIのrun_id付き停止要求を指す。SIGTERM単独は異常終了扱いになり得るため、終了要求に加えて専用サービスの登録解除を確認する。

### 日常用の固定root操作経路

N1のroot bridgeは変更していない。送信日常用には固定ラベル`com.sat0t.line-auto-sync.daily-send`と、固定root helper `/var/root/las-daily-send-control.py`を別に生成する。初回だけ本人が生成済みbootstrapへsudo認証を行う。

```sh
sudo /var/jb/usr/bin/python3 /var/mobile/las-daily-send-service/daily-root-bootstrap.py
```

このbootstrapは既存の検証済みROOT_SETUPを固定helper名へ限定して再利用し、root所有600のhelperと、その**正確なPython/helperコマンドだけ**のsudoers ruleを新規作成する。visudo・既存includedir・所有者・コードhashを検証し、N1ルールや既存ファイルを上書きしない。以後のWorkからのroot呼出しはstdinの`{"action":"probe|install|start|status|stop"}`だけ。任意のsource、command、labelやroot Pythonコードを受け付けない。

固定controllerは生成時のmobile entry/plistをhashでpinし、root側の固定plistへ登録する。`install`と`start`は別操作。`start`命令の成功を配送成功とはしない。状態不明でstartを繰り返さず、同じ専用ラベルのstatusと私用stateを照合する。stop/statusは既存の独立した両ドメイン処理を再利用し、一方のtimeoutでも残りを確認する。unknownを未登録と見なさず、両方が明示的に未登録の場合だけ解除完了。stop40秒/status10秒の構成予算を維持する。

### 状態・ログ容量と停止

データを削除せず、収集前後にqueue/cache本体・WAL/SHM/journal・legacy状態のファイル合計とディスク空きを観測する。既定はstate256MiB、空き64MiBの境界で正常停止する設定。`--max-state-bytes`と`--min-free-bytes`で準備時に指定できる。容量停止でACKやキューを初期化せず、`daily-health.json`の最新1件へ`capacity_paused`と件数ではなくbyte数を記録する。これは**各収集呼出しの前後の閾値**であり、1回の収集中の増加やOS全体の空き容量に対する絶対hard limitではない。膨張を検出したら続行せず、保存・容量方針を確認する。

標準出力/標準エラーの無制限ファイルをplistへ追加していない。最新`worker-status.json`と`daily-health.json`だけを原子的に置換する。entryの起動失敗は例外クラスだけをstderrと利用可能なOS syslogへ出す。OSログの実機可用性・保存期間は未確認。既存の証拠ログは削除しない。

### 今回の検証と未確認

変更直結の合成15件が成功。生成entryを実行して無効起動・協調停止・disable・設定変更で0、未捕捉異常で非0となることを確認した。送信opt-in無しの動作維持、初回旧queue拒否、承認済み日常stateの保持、異常終了した同じentryの再実行での再開、容量時に送信せず全データを保持する停止を確認。固定root controllerのmobile版hash検査、独立した解除処理、bootstrapのコードhashと厳密なsudo commandも合成確認した。通常sender自体の通信断・凍結request・同一body再試行の新規状態表示テスト3件と、既存の合成coverageを併用する。

実iPadへの日常サービス配備・root認証・launchd再起動・実通信断復旧は未実施。実GASとの同一対象照合、LINEのロック中同期、長時間運用、容量逼迫からの実復帰も未確認。旧profile/旧キューへ送信workerを起動する手順は含めない。新しい日常用profileの範囲・送信先・受信dedupeを実際に確認してから導入し、設定作成やCIだけで完成率を増やさない。
