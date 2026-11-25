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
| 異常終了後の再開 | 通常送信workerの既存pending/frozenを伴う再開には`--send --resume-pending`が必要。既存worker自身が死んだ後の恒久的な再起動は通常runtimeには未導入。N1の期限付きsupervisorと日常用サービスを混同しない |
| キューの分割 | 未ACK行を最大500行ずつ取り、送信サイズ上限でprefixを固定。既存600行の合成backlog試験あり。先頭の長文や破損は保留し、カーソルを進めて捨てない |
| 二重起動と変更検出 | worker/senderのOS lock、source/DB同一性/送信先binding、凍結body/対象行/manifest/hash照合を使う。既存状態を初期化して復旧しない |
| 停止・スナップショット | disableとrun_id付き停止要求、停止後にworker/sender lockを取った専用SQLite backupを使う。回復元は別の私用保存先で比較し、元のキューを上書きしない |

既存の関連試験は`tests/test_delivery.py`、`tests/test_receiver_contract.py`。新規は`tests/test_operational_status.py`。保存済み対象を再送してこれらの証拠を作り直す必要はない。

## キュー・ログの増加と未検証の制約

通常の`messages`のACK済み行と`named-cache`は保持され続ける。これらは重複除外・内容整合性の記録でもあるため、今回のデータ保全条件で自動削除やretentionを追加していない。空き容量不足や大規模時の実時間・必要容量は未測定で、600件の合成試験を長時間運用の保証にしない。空き容量不足時に送信済み・未送信の行を自動消去して続行する仕組みはない。

通常worker内で追記し続ける履歴ファイルは作っていない。`worker-status.json`は最新状態1件を原子的に置換し、標準出力は周期ごとの短い段階名。外側でstdout/stderrを無期限のファイルへ保存する場合のrotationは未実装。期限付きN1証拠ログは実行上限を持つ別物であり、日常用の無期限ログ設定へ転用しない。既存の証拠ログは削除しない。

通常HTTPの`timeout=30`はurllibの通信待ち設定で、連続redirectや断続的な応答を含む全処理の絶対wall-clock上限の証明ではない。N1は別プロセスと端末supervisorの期限で制限している。日常用サービスで同じ終了上限を保証するには、対象端末上の監視・停止・再開を別途確認する必要がある。

## 再開条件

### collect-only日常サービスの導入準備

`tools/prepare_daily_worker.py`を追加した。停止・無効化済みの単一許可トークprofileと、既存管理CLIで版照合して配置したstageのmanifest/runtimeを読み、新しい保存先へ次の3ファイルを生成するだけ。インストール、enable、launchd起動、LINE DBの読み取りや外部送信は実行しない。

- `daily-collect.py`：無効profileまたは準備後の設定変更は正常終了0とし、再起動ループにしない。runtime全ファイルのhashを照合してから、通常runnerを**send=False固定**で呼ぶ。
- `daily-collect.plist`：mobile実行、RunAtLoad、`KeepAlive: {SuccessfulExit: false}`、ThrottleInterval60秒。通常停止/disable/設定変更の終了0は再起動対象にせず、未捕捉の異常終了だけをlaunchdの再起動対象にする構成。
- `plan.json`：collect-only、未配備・未起動、照合版を私用記録する。実設定やtokenを複製しない。

```sh
python3 prepare_daily_worker.py --profile PRIVATE_DAILY_PROFILE --stage VERIFIED_MANAGED_STAGE --output NEW_PRIVATE_SERVICE_DIRECTORY
```

出力先・profile・stage・LINE DBディレクトリの重なり、DBポインター不一致、稼働中marker、旧版runtime、既存出力先を拒否する。既存N1や旧profileへ日常サービスを起動しない。新しい日常用profileの準備と許可範囲確認後に使う。

標準出力/標準エラーの無制限ファイル保存をplistへ追加していない。通常runnerの最新`worker-status.json`1件が収集段階・例外クラスを保持する。entryで起動前に失敗した場合は例外クラスだけをstderrと利用可能なOS syslogへ出す。OSログ機構の実機可用性・保存期間は未確認で、ロック状態や停止の証明にはしない。

ここでの通常停止はCLIのrun_id付き停止要求による協調停止を指す。単独のSIGTERM/killと登録解除は別で、異常終了扱いのsignalだけを送れば再起動対象になり得る。実導入時は停止要求の正常終了と、専用ラベルの登録解除を両方確認する。

変更直結の合成7件で生成entryを実行し、無効状態でruntimeをimportせず0、通常runnerの停止/disable/設定変更で0、未捕捉異常で非0となること、送信関数が呼ばれないこと、版・境界・上書き拒否を確認した。実際のlaunchdによる再起動は未確認。既存の固定root bridgeはN1専用であり、この日常用ラベルの登録を許すものではない。日常サービスのroot登録・解除経路は未適用・未整備、**送信を伴う日常用サービスも未完成**として残す。

旧profile/旧キューへ通常workerを起動する手順は今回の試験案に含めない。新しい日常用profileを実際に許可範囲と照合した後、コード・状態schema・送信先・受信側dedupeを確認し、監視サービスの起動/停止/異常終了後の再開を実測する。接続断や状態表示だけを理由にstartを繰り返さず、既存runの状態・凍結request・台帳・メールを照合する。

長時間運用、ディスク逼迫、実機のプロセス強制終了からの自動復帰、実通信断復旧は未検証として残す。今回の表示修正と合成試験でMVP完成率を増やさない。
