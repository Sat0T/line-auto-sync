# 1件限定の配送証拠を読むための検証器

`tools/verify_locked_delivery.py` はローカルの私用exportだけを読み、ネット送信・GAS実行・端末操作・保存データ更新をしない。コードや合成fixtureの成功は実機成功ではない。実データのexport、ログ、ID、ハッシュ、メール、owner、非公開設定はリポジトリの外で保持する。

## 入力

単一の私用JSONを指定する場合は `python tools/verify_locked_delivery.py PRIVATE_EVIDENCE.json`。実ファイル名へ置換する。入力schemaは次のとおり。

- `version`: `1`。
- `expected`: `source`、許可トークの`chat_pk`、採用した`target_pk`（文字列）、固定`text`、本人メールの`owner`。
- `device`: 変換済みの完全な`body`と、実機workerが書いた`events`配列。bodyにはmanifestとLF区切りB/C1ペイロードが必要。event名はsnapshot、target_adopted、http_started、http_finished、application_acceptance。全行のrun_idを同じ実行へ結ぶ。対象未検出ならtarget_pk/bodyはnullでよい。
- `deployment`: `protocol`は`legacy_token_body_OK`。実際の配備/応答契約を確認した場合だけ`response_contract_verified: true`。`expected_endpoint_sha256`と`used_endpoint_sha256`は、既存設定と実際に使用した送信先から私用に計算した値。HTTPイベント側の実値とも一致が必要。公開ソースの読取りだけで配備契約を確認済みにしない。
- `gas.batches`と`gas.messages`: 既存台帳の対象行のdata_jsonをJSONへ展開した配列、または`{id, data_json}`の配列。`gas.stored_body`は取得できれば追加可能。新しい監査表や未配備フィールドには依存しない。
- `gmail.messages`: 本人宛入力メールのGmail API full表現。id、internalDate、labelIds、payload内のFrom/To/Subjectとtext/plain本文が必要。body.dataはbase64url文字列とAdvanced Serviceのbyte配列の両方を扱う。添付ファイルを本文にはしない。
- `lock_window`: 独立した動画等を実際に照合した後の`independent_record_reviewed: true`、不確実性を考慮した`certain_start`/`certain_end`（timezone付きISO時刻）、`device_uncertainty_seconds`と`gmail_uncertainty_seconds`。未取得の誤差を0と推定せず、根拠がなければ未取得のままにする。

workerの私用ディレクトリを回収した場合は、同じ処理を次で実行できる。

```text
python tools/verify_locked_delivery.py PRIVATE_EXTERNAL_EXPORTS.json --run-dir PRIVATE_RECOVERED_RUN
```

external側には`owner`、`deployment`の実確認済み情報、`gas`、`gmail`、`lock_window`を置く。plan.json、events.jsonl、target-private.json、payload-private.jsonは回収ディレクトリから読み取る。plan内の未確認フラグを自動で確認済みにせず、元ファイルを変更しない。

## 合格に必要な対応

1. manifest・復元したB/C1レコードのsource、対象ID、許可トーク、固定本文が一致し、1件だけであること。canonicalレコードのchecksum・batch IDを再計算する。
2. 同じrunの最後の非検出と最初の検出を読む。検出時のファイルアクセスと同一性検査、対象ID採用、HTTP対象が一致すること。非検出読取りの開始から検出読取りの終了までを「可視化の観測区間」とし、正確なDB挿入時刻とはしない。
3. HTTP開始は1回だけ。HTTP 2xx単独で受理とせず、確認済み配備のstrict OK応答と、対象batch/messageの既存台帳ハッシュ一致を要求する。既存台帳に受理時刻がないため、検証器はHTTP開始から成功応答までを受理の保守的区間として扱い、accepted_atを創作しない。
4. Gmailの本人From/To・SENT・実メッセージID・内部時刻・復元本文・source・対象ID・batch IDを照合する。件名や時刻の近さだけでは一致としない。同じメールのexport重複は取り除くが、異なる実メールIDで同内容が複数届いた場合は重複配送として保留する。
5. 最後の非検出read窓・最初の検出read窓・HTTP区間・Gmail内部時刻の粒度と誤差を含む区間が、すべて確実なロック区間内に収まること。read窓は開始・終了の両方を判定する。未校正ロック信号をロック証拠へ変換しない。

## 出力と限界

最終確認段階と、DB可視化・アプリケーション受理・Gmail配送・ロック中配送を分けて出力する。対象未検出、採用後未凍結、HTTP未開始、応答不明、台帳なし、メールなし、時刻対応不足は部分結果のまま保持する。矛盾・対象混在・別トーク・複数POST・本文/台帳不一致は無効。診断に実ID・本文・owner・実ハッシュを出さないが、時刻の派生情報は私用として扱う。

exit codeはロック中配送の証拠が揃った場合のみ0、それ以外は2。0でもChatGPTの自然な定期解析は常に未証明であり、別途実行起点と結果作成を結ぶ証拠が必要。この検証器はジョブ登録解除や端末の安全停止を代行・証明しない。それらは管理スクリプトと端末の終了記録で別に確認する。

HTTPが成否不明で早期停止した場合は、再送せず既存の台帳・Gmailを読み取る。現在の検証器は成功応答区間が確認できない実行をロック中配送の合格にしない。これは受理されていないことの証明ではない。
