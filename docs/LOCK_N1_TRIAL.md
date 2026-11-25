# N1：新しい固定文1件のロック中配送試験

11/25更新：停止処理を修正した新キットを使う。初回接続は [IPAD_FIRST_CONNECTION.md](IPAD_FIRST_CONNECTION.md) を参照。現在は本人が端末を接続できず、実配備GASとのbindingも未確認なので未起動・未送信。固定文・既定base・ラベルはそのまま。日付だけを手で変更しない。

本人の継続開発承認に含まれる試験。再承認待ちではないが、現在は対象iPadへの信頼・鍵認証済み接続が未確立のため**未実行**。初回接続・本人認証と撮影の準備後、以下を実施する。旧未配送文や既送文は使わず、今回の新規状態だけを操作する。

## 実行前に記録する固定条件

- 対象トーク：停止済みの既存プロファイルで許可済みのKeepメモ1トーク。トーク範囲を変更しない。
- 新しい文は次の1件だけ。準備前や開始前には入力しない。

```text
LAS-LOCK-20261120-N1 2026年11月25日18時までに配送試験の結果を確認する
```

- 外部へ送る情報：この架空試験文、LINE上のメッセージ時刻、既存形式のsource/対象ID・参照ID、許可Keepと送信者の表示情報、1件のbatch manifest。実会話や他のメッセージを送信対象にしない。既存の名前解決とB/C1変換を使い、変換後も固定内容・トーク・対象ID・件数・manifest整合性を確認する。
- 送信先：既存の独立GAS設定と完全一致するHTTPS endpoint/tokenを専用状態へコピー。準備時の既存設定fingerprintと毎回の比較、送信直前の再検査を行う。実URL/tokenは公開・console出力しない。
- 専用状態：`/var/mobile/las-lock-n1`。ラベルは`com.sat0t.line-auto-sync.lock-n1`。既存状態や同名ファイルがあれば上書きしない。
- 実行時間：観測・新規送信開始の期限180秒。HTTP子プロセスの完了待ち最大30秒。端末側supervisor240秒、TERM/KILLの猶予を含む停止待ち構成上限250秒、電源アサーションの自動期限255秒。
- 送信上限：POST最大1回、再試行0。親と子の両方で送信開始期限・停止状態を確認。開始予約と実際のHTTP開始境界は区別する。
- 成否不明：未ACKの凍結状態とログを保持し、停止。再起動・再送せず、既存台帳・メールを読み取り照合する。
- 解除待ち：**実際のロック操作から300秒（5分）**。動画開始から数えない。管理PCで終了・登録解除を確認できることも必要。終了確認が取れなければジョブのstop/statusを先に行い、接続断だけで停止完了とは判断しない。

## 接続と準備

[初回接続・認証と管理CLI](IPAD_CONNECTION_LOCAL.md)を先に実施する。接続先・鍵・ホスト鍵・journal・kitは私用ディレクトリに置く。現時点ではネイティブSSH/launchd/root bridgeの実機検証は未了。初回認証を自動承認しない。

PC側の私用キット `.las-private/n1-kit-20261125-stopfix` は今回作成済み（未配備・未起動）。同じ出力先でpackageを再実行せず、この作業環境ではdoctorから再開する。別の未準備環境でのみpackage→doctor→deploy→prepare→ui-statusの順に進む。新規run名は`n1-20261120`、baseは`/var/mobile/las-lock-n1`を一貫して使う。prepareは、既存baselineの停止・対象不在を確認し、新規キューを初期位置で作る。worker開始時にも対象不在を確認し、存在していたら送らず停止する。

```sh
py tools/manage_ipad.py package --repository . --output .las-private/n1-kit-20261125-stopfix
py tools/manage_ipad.py doctor --config .las-private/ipad-connection-20261125/connection.json --journal .las-private/n1-journal.json --run n1-20261120
py tools/manage_ipad.py deploy --config .las-private/ipad-connection-20261125/connection.json --journal .las-private/n1-journal.json --run n1-20261120 --bundle .las-private/n1-kit-20261125-stopfix
py tools/manage_ipad.py prepare --config .las-private/ipad-connection-20261125/connection.json --journal .las-private/n1-journal.json --run n1-20261120
py tools/manage_ipad.py ui-status --config .las-private/ipad-connection-20261125/connection.json --journal .las-private/n1-journal.json --run n1-20261120
```

doctorやprepareが失敗したらstatusと原因確認へ移り、startしない。既存runがある場合も新しいrunへ逃げて再送せず、その状態を照合する。stage版と端末側ファイルのhash検査が合格しても、配送の合格とは数えない。既存GASの配備/strict OK応答契約は別途実際に確認し、公開ソースだけで確認済みにしない。計画の初期値は未確認。実際の配備先との一致・応答契約・本人宛転送先を読み取りで確認できるまでは、startと試験文入力へ進まない。既存batches/messages台帳とGmail fullを回収できる経路も先に確保し、証拠取得を未配備の監査機能で代用しない。

## 撮影・起動・入力・解除

役割は、iPad＝対象端末、撮影端末＝連続動画専用、入力端末＝別の端末の本人LINEとする。Windows LINEがログイン済みでKeepメモを開ける場合は入力端末に使えるが、現在は認証未確認。カメラとLINE入力を同じ端末で切り替えて撮影を中断しない。別の入力端末を確保できなければ試験を開始しない。

1. 操作コマンドはKeepメモへ送らず、管理PCまたはiPadでこの手順から直接コピーする。
2. 撮影を開始して経過時間が進むことを確認。iPadのNewTermで`date '+BEFORE %Y-%m-%d %H:%M:%S %Z %z'`を表示し、秒付きJSTとタイムゾーンを動画に残す。LINEを前景化する場合はその操作・表示も記録し、途中の画面復帰も隠さない。
3. 管理PCで次を1回だけ実行。命令成功はロック・配送成功の証拠ではない。PC時計は基準にせず、端末ログとiPad自身の時刻を使う。

```sh
py tools/manage_ipad.py start --config .las-private/ipad-connection-20261125/connection.json --journal .las-private/n1-journal.json --run n1-20261120
```

4. iPadで`date '+START %Y-%m-%d %H:%M:%S %Z %z'`を動画に残し、20秒以内を目安に物理ロック。ボタン操作と画面状態が分かるよう撮影し、ロック操作から5分タイマーを開始する。iPadを操作し続けて開始が遅れた場合も、計測を動画冒頭へ戻さない。
5. ロックから30秒後、入力担当端末から上記固定文だけを本人Keepへ1回送る。入力端末にも秒付き時刻を表示する場合はiPadとの差を試験前後に確認する。入力画面と送信操作も同じ連続撮影へ収められる配置を優先する。
6. iPadには触れずに撮影を継続。管理PCはstatus/fetch/stopで進捗・停止を確認する。切断してもstartを再実行しない。
7. 実ロックから5分後かつ専用ジョブの終了・登録解除確認後、解除操作・前景アプリ・iPadの`date '+AFTER %Y-%m-%d %H:%M:%S %Z %z'`を撮影する。途中で画面が復帰した場合は、その時点で確実なロック区間を分け、隠さず記録する。

## 確認・停止・未検出時の解除後読取り

```sh
py tools/manage_ipad.py status --config .las-private/ipad-connection-20261125/connection.json --journal .las-private/n1-journal.json --run n1-20261120
py tools/manage_ipad.py fetch --config .las-private/ipad-connection-20261125/connection.json --journal .las-private/n1-journal.json --run n1-20261120 --output .las-private/n1-logs
py tools/manage_ipad.py stop --config .las-private/ipad-connection-20261125/connection.json --journal .las-private/n1-journal.json --run n1-20261120
```

stopは専用ジョブだけを停止・登録解除し、両ドメインを問い合わせる。問い合わせ失敗を登録なしと見なさない。finished記録・worker終了理由・config disabledも私用journal/ログで確認する。途中で異常があれば同じstopを使い、再送しない。取得結果は上書きせず、新しく回収する場合は別の私用保存先へ置く。解除後の1回読取りを実施した場合は、その後のevents等も別の私用保存先へfetchし、元の回収結果と両方を保持する。

ロック中に対象を検出しなかった場合だけ、停止・登録解除確認後に解除し、解除操作・前景アプリ・時刻を記録してから次を1回実行する。取得後に対象が現れていても送信処理を再起動しない。

```sh
py tools/manage_ipad.py post-unlock-once --config .las-private/ipad-connection-20261125/connection.json --journal .las-private/n1-journal.json --run n1-20261120
```

読取りはPCとiPad側の両方で1回を予約する。接続失敗を理由に再読取りしない。結果は「解除後の1回の読取りで存在/非存在」であり、解除操作が同期を起こした、あるいはその秒にDBへ挿入されたとは断定しない。

## 証拠の照合と合格基準

[配送証拠検証器](LOCK_DELIVERY_EVIDENCE.md)で、同一source・トーク・固定文・採用ID・batch ID・復元body/hashを照合する。GASは既存batches/messagesの対象行を読み取る。Gmailは実メッセージID・internalDate・本人From/To・SENT・本文を読む。件名や時刻近接だけでは一致にしない。未配備の監査表は前提にしない。

最後の非検出から最初の検出までを可視化の観測区間として扱う。GAS台帳に受理時刻がなければ創作せず、確認済み契約のstrict OK成功応答と対応台帳に基づくHTTP区間を受理の保守的な範囲とする。OKでもメール転送が成功するとは限らず、Gmail到着は独立に照合する。異常・応答不明は最後に確認した段階で保留する。

合格は、時計誤差と動画の確実なロック区間を考慮しても、対象のDB可視化、GAS受理、Gmail到着が解除前のロック区間に収まる場合のみ。UI命令や未校正信号はロックの証明ではない。Gmailまで成功しても、ChatGPT自然実行の結果保存・長時間運用・通信断復旧は別判定のまま。
