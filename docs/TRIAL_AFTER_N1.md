# N1の期限を過ぎた場合の次の1件

N1の固定文にある期限は2026-11-25 18:00 JST。実際の試験開始時に過ぎていれば、旧N1の本文・profile・キュー・ACK・kit・証拠を変更せず、新しい専用状態を生成する。既存の公開鍵、初回接続セット、本人用6コマンドを作り直す必要はない。最初の接続と認証は既存セットで行い、新しい試験キットの配置は接続後にWorkが行う。旧キットのまま期限だけを手編集しない。

```sh
py tools/prepare_fresh_lock_trial.py --repository . --output .las-private/fresh-trial-NEW --due-jst FUTURE_JST_ISO
```

FUTURE_JST_ISOはその時点より1時間以上後の、日本時間・分単位のISO時刻を指定。例は2026-11-27T18:00:00+09:00だが、実試験時に再確認する。保存先は新規のみ。同名の既存成果は上書きしない。

生成物は`trial-plan.json`、`TRIAL_STEPS.md`、`kit/`とそのソース。生成時の秒付き識別子を使い、固定文・専用base・label・run・期限を1つのplanに結び付ける。元の1件限定helperを再利用し、TEXT/LABELと期限検査だけを生成する。prepare、worker開始、HTTP子の開始と実dispatch直前で期限を再確認し、過ぎていたら送信しない。1POST・再試行0、変換後payload検査、対象ID固定、最後の非検出/最初の検出、応答不明保留、期限停止は維持する。

生成は端末やLINE DBへ接続せず、送信・起動しない。新しいキットのruntimeは現在の修正版をコピーする。初回公開鍵セットは旧版のまま保全し、登録後のWork側で新しいrunと新しいjournalを使ってdeployする。managerの`--base`には生成planのbaseをそのまま渡す。接続先・鍵・known_hostsは本人照合後の既存私用設定を再利用。

以後のprepare/start/status/fetch/stopは[配送手順](LOCK_N1_TRIAL.md)のrun/base/journal/kitだけを生成planから機械的に読み替え、本文は生成ファイルをそのまま使用する。既存GASへのbindingと、台帳/Gmailの証拠取得経路の確認が先。実ロックから300秒・動画と端末JST・終了/登録解除確認・未検出時の解除後読取り1回・成否不明時の再送禁止を維持する。

期限付き試験は日常送信サービスではない。結果がGmailに届いても、自然なChatGPT定期解析・長時間運用・実通信断復旧の証拠は別判定。
