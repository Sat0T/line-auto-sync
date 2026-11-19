# 初回接続の本人による公開鍵登録

管理スクリプトとは別の、本人がiPadのNewTermで一度だけ使う補助。既存のSSH環境を確認し、PCで既に用意した専用公開鍵だけを登録する。SSHサーバのインストール・起動、鍵の生成、旧鍵の変更・削除、パスコードや秘密鍵の読み取りは行わない。実機では未実行。

今回のPCでは `.las-private/ipad-enrollment-n1/` に `pc.pub`、`enroll_ipad_key.py`、照合用 `SHA256.json` の3ファイルを新規作成済み。秘密鍵は含めていない。このフォルダだけを本人の方法で転送し、元の鍵ディレクトリごと転送しない。同じ出力先への再生成・上書きは不要。これらは私用ファイルで、Git管理しない。

1. PCの専用鍵の **`.pub`だけ** と `tools/enroll_ipad_key.py` を、本人の方法でiPadのホーム配下の新しい私用ディレクトリへ渡す。秘密鍵を渡さない。取得元と公開鍵の対応はPC側で確認する。
2. NewTermで `python3 /var/mobile/las-ipad-enrollment/enroll_ipad_key.py doctor` を実行する。既存sshd実行ファイルと公開ホスト鍵、mobileユーザー、JSTを確認する読み取りだけ。`ready_for_owner_enrollment:true`でも接続可能・サーバ稼働中・接続先の本人確認まで証明したものではない。
3. doctorが準備可の場合にだけ、`python3 /var/mobile/las-ipad-enrollment/enroll_ipad_key.py enroll --public-key-file /var/mobile/las-ipad-enrollment/pc.pub` を実行する。Ed25519公開鍵1行のみを受け付ける。新規ディレクトリは700、新規authorized_keysは600で作り、`restrict`付きの1行を追記する。既存bytesは保全し、重複は追加しない。
4. 同じ鍵が既存の制限なし行にある場合は、既存行を変えず `requires_review:true` とする。既存権限が広い、symlink・hardlink・別owner等の場合も自動修復せず本人レビューで止める。ファイルを削除して再実行しない。
5. PC側から、既存のホスト鍵信頼または本人が別経路で確認した公開ホスト鍵で照合して接続する。ホスト鍵未信頼を自動承諾しない。登録成功は、正しいiPadへ接続できたことの証明ではない。

`restrict`はOpenSSHで転送・PTY等を制限する鍵オプション。対象の既存SSHサーバが対応している必要がある。ログイン失敗時にオプションを外したりパスワードを推測したりしない。既存サーバがない場合は変更前に保留し、別途接続方式を選ぶ。

doctorの出力は可否のみで、ホスト鍵本文・fingerprint・端末識別子を公開ログへ出さない。具体的な接続先・鍵・信頼情報はPC側の私用ファイルに保持する。iPad側の既存秘密ホスト鍵をPCへコピーしない。
