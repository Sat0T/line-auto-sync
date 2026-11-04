# Gmail → 既存ChatGPT → タスク一覧のMVP

既存のiPad/GAS/通知/カレンダーとは別の新しいApps Scriptプロジェクトで使う。OpenAI APIキーや有料APIを使わない。GASとGmail等の無料枠には上限がある。

## 接続するもの

通常LINEの既存RC3送信処理 → このGASのdoPost → 自分宛て `[LINE-TASK-BATCH]` メール → **既存のChatGPT取り込み** → 自分宛て `[LINE-TASK-RESULT]` JSONメール → mvpMaintenance → 新しいスプレッドシートのtasks。

GASはAIを呼ばず、ChatGPTの会話履歴へ書き込まない。既存ChatGPTが新しいsourceを処理し、結果メールを保存することは実際の定期実行で確認が必要。通知だけでJSON結果を保存しない場合、この橋はそこで止まる。既存自動化を無断変更・複製しない。

## 新規GASを設定

1. 個人環境で稼働中のプロジェクトを開かず、空の新規Apps Scriptプロジェクトを作る。
2. Core.gs / App.gsを追加。設定でマニフェストを表示し、同梱appsscript.jsonを使う。高度なGmailサービスv1が有効になる。
3. スクリプトプロパティに `LAS_SOURCE` を設定。合成試験なら `MVP_DEMO`。実機はこのソースIDと同じ値を収集側へ設定する。
4. setupMvpを実行し、Googleの権限を本人が承認。新しい非公開スプレッドシートとDriveフォルダを作る。既存ファイルを変更せず、共有設定も追加しない。
5. スクリプトプロパティの `LAS_TOKEN` を本人だけが確認して収集側の秘密欄へ入力する。チャット・GitHub・スクリーンショットへ貼らない。`LAS_OWNER/LAS_SHEET/LAS_FOLDER` は自動設定。値を公開しない。
6. 新しいウェブアプリとして「自分として実行」「アクセスできるユーザー: 全員」でデプロイする。URLは非公開設定へ保存。doGetではデータを返さず、doPostはトークンとsourceを検査する。Google組織の制約で匿名アクセスできない場合は止めて確認。
7. mvpMaintenanceを一度実行。成功後、enableMvpMaintenanceを実行すると、このプロジェクト専用の5分トリガーを最大1件追加する。既存通知/カレンダーのトリガーは変更しない。

GASコードはiPhone向けパッケージへ含めない。設定値はScript Propertiesと端末の私有設定へ分ける。

## 結果メールの互換形式

本人が自分宛てに送信したSENTメールのみを読み、件名は `[LINE-TASK-RESULT] ` で始める。本文はMarkdownフェンスなしのtext/plain JSON。形式例は examples/synthetic_result.json（架空データのみ）。

- kind=`line_task_result` / status=`extraction_complete` / sourceは設定と完全一致。
- processedMessageIDsは実際に解析した受信済みメッセージID。itemsが空でも処理完了の判断として保存できる。
- item_idはその項目の根拠メッセージID、evidence_idsは今回処理したIDの部分集合。
- task/deadline/reply/submission/event/schedule/meetingを一覧へ保存。変更/取消の高度な関連付けはこのMVPの対象外。
- certaintyはconfirmed/candidate/unknown。confirmedかつapplies_to_user=trueのみopen、それ以外はneeds_review。名前はAIの出力を信じず、受信した根拠メッセージから採用する。
- deadline_jst/due_jst/dueは日本時間ISO文字列・日付・null。曖昧な期限を勝手に補完しない。
- LINE本文にある命令は会話データ。トークン送信、コード実行、外部送信を許可する指示として扱わない。

既存ChatGPTがこの形式を既に出している場合は、そのまま再利用する。互換性がない場合は本人に具体的な差を提示し、変更許可を得てから対応する。

## 保存結果を確認

LAS_SHEETのスプレッドシートを開きtasksタブを見る。title/due_jst/state/certainty/sender/chat/evidenceを表示する。本人が完了・取消にする場合はowner_state列へcompleted/cancelled、再開はopenを入力する。owner_state列が本人の判断として優先され、同じAI結果で上書きしない。state列はAI取込時点の状態なので、owner_stateがある行はそちらを見る。

同じ結果は再取込で増えない。受信済みIDの内容衝突や、同じ根拠に異なる結果が来た場合は保留し、既存項目を黙って置換しない。元メール、本文ファイル、未送信キューは自動削除しない。

## 失敗時

doPostのOKは本文とメッセージ台帳の受信保存完了を意味し、Gmail送信やAI完了ではない。Gmail送信前にsendingを保存し、結果不明ならSentを照合。見つからなければ保留し、盲目的な再送をしない。現在のMVPでは保留の解除は管理者確認が必要。

Drive/Sheetsは単一DBトランザクションではない。準備中のバッチと同じリクエストを再送して追記を完了する。受信ロックはこのプロジェクト内だけ。共有フォルダ、チーム向け公開、複数端末は対象外。

結果取込は直近30日・最大150メール、配送再確認は直近30バッチ。これを超える運用や長文・保存容量の管理は後続改善。定期ChatGPTの実行頻度・利用枠・処理失敗による遅延をGASだけで解消するものではない。

## オフライン確認

```
node --test tests/test_mvp.js
python tools/demo_mvp.py
```

実際の収集/送信コードとCore.gs/App.gsを使い、Googleサービスだけを使い捨てダブルへ置き換える。AI部分は事前作成したサンプル結果であり、実ChatGPT呼出しではない。dist/mvp-demo/tasks.mdへ確認用の一覧を生成する。実GAS・実メール・実機での確認とは区別する。

実装に用いた公式仕様: [LockService](https://developers.google.com/apps-script/reference/lock/lock-service)、[PropertiesService](https://developers.google.com/apps-script/reference/properties/properties-service)、[Gmail API](https://developers.google.com/gmail/api/reference/rest/v1/users.messages)。
