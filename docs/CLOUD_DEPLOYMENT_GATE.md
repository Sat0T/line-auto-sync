# 独立GASの実配備確認・監査適用・復旧

## 現在の正規アクセスで確認できた範囲

個人用Drive/Sheets/Gmailの読取り経路はある。既存候補台帳に、過去のbatch保存ファイルと候補フォルダの対応があることを新しいmetadata経路で確認した。ただし、今のGAS配備project/version/endpoint、ScriptPropertiesのowner/sheet/folderとの直接のbindingは未取得。名前や過去の保存関係から現在の配備先と断定しない。旧2件の結果メール・タスク・解析を再処理していない。

新しいDrive追加metadata・セルのリンク/note・台帳のfield名・GAS公式通知への限定検索から、実配備の識別情報を取得できなかった。同じ検索は繰り返さない。現在のツールにはApps Script APIを直接使う認証済みconnectorがなく、ローカルのclasp設定/Google ADC認証設定も確認範囲にない。ブラウザの認証情報を取り出したり、Driveの接続資格情報を別用途へ転用したりしない。PCのGUIは禁止されたまま。

## 次に必要な正規経路

1. 実物のscript IDとApps Script APIの既存の認証済みアクセス、または本人が許可した認証済みエディタ経路を確立する。これはiPadとは独立したアクセス条件で、追加の有料APIや新しいAIキーは不要。
2. [deployments.list](https://developers.google.com/apps-script/api/reference/rest/v1/projects.deployments/list)からdeployment IDとversionNumberを取得し、そのversionNumberの[getContent](https://developers.google.com/apps-script/api/reference/rest/v1/projects/getContent)を読む。HEADだけを読んで実配備とみなさない。取得結果と現行deployment metadataを私用保存し、比較できる形で退避する。
3. [ScriptProperties](https://developers.google.com/apps-script/guides/properties)はソースとは別の実行時設定。LAS_SOURCE/OWNER/SHEET/FOLDERと受信token/endpointの一致を、認証済みの設定読取りで確認する。token自体はチャット/公開ログ/監査表へ出さない。未確認項目はfalse/nullのまま。
4. 現行の受信・転送・保存を止めずに、候補版の差分とscopeを比較する。既存プロジェクトを新規setupMvpで作り直さず、4表・旧行・トリガー・本人の完了状態を保持する。配備前snapshotが取得できなければ、復旧可能と決めつけて更新しない。

## 準備済み変更と適用境界

候補版には既存の将来分の結果監査に加え、曖昧な送信後の同件名・別本文の誤回復防止とflowed本文の互換修正を含む。変更は合成試験済みで、旧形式・既存完了タスク・重複取込時の状態を保持する。現在は未配備。

監査開始境界より古いメールに遡及監査を付けない。新規監査開始が書けなければ保存へ進まず、終了監査が書けなければ成功にしない。保存と監査は非原子的で、部分保存/開始のみは不明のまま保持し、同じ結果hashの再開でタスクを増やさない。旧2件へ監査情報を後付けしない。

候補版の配備後に異常があれば、更新前に退避した**実配備のversion**へ戻すことを検討する。リポジトリの古いcommitを実環境の旧版と仮定しない。新しい監査表/境界は証拠として保持し、削除・初期化・境界の巻戻しをしない。旧版へ戻した場合の監査欠落区間を私用記録し、同じ入力を一括再処理しない。根拠がある部分失敗の再開だけを同一結果として照合する。

## Cloudだけの次の1件

実binding確認後、未使用のcloud-synthetic由来1件に限定し、受理source/宛先/期限/試行上限/停止条件を実行前に記録する。実LINE IDを偽装せず、旧2件や旧キューを再送しない。入力受理と本人Gmail到着、自然なChatGPT起動と結果作成、GAS取込とタスク保存を別々の実記録で結ぶ。未配備の監査を取得できる前提にしない。現時点でbindingが未確定なので、実送信・配備・手動起動・別の定期タスク作成は行っていない。
