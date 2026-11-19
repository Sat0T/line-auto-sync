# iPad管理CLIと初回接続の再開条件

実装は `tools/manage_ipad.py`。既存のSSH鍵認証と信頼済みホスト鍵だけを使い、接続先・秘密鍵・パスワードを推測しない。実機の変更、ジョブ起動、LINE送信はこの接続調査では行っていない。

## 今回確認できた範囲

Windowsにはssh/scpがある。確認したユーザープロファイルにSSH設定・known_hosts・標準秘密鍵はなく、信頼・認証済みの接続先を確立できなかった。USB関連アプリとペアリング記録は存在するが、対象iPadとの対応は未確認。使用可能なiproxy/ideviceクライアントや実行中のUSB mux経路も確認できていない。IP推測、LAN走査、既定パスワード試行、同じ失敗の再試行はしていない。

Windows LINEはインストールとプロセスの存在を確認したが、認証済み画面は未観察。撮影端末と試験文入力端末を分離する候補に留め、ログイン済みやKeep送信可能とは扱わない。端末識別子・接続先・ペアリング内容・調査の生出力は公開しない。

## 最小の本人操作

1. 対象iPadをWindows PCへケーブル接続し、必要なら本人が解除して「このコンピュータを信頼」を確認する。既存のApple管理アプリで、その機器が対象iPadであることを確認する。
2. 既にiPadへのSSH接続方法・公開鍵認証がある場合は、それを再利用する。鍵がない場合はPC側generate-keyで専用鍵を準備できる。本人が行うのは公開鍵のiPad登録、iPad側のホスト鍵との照合、接続先の確定。秘密鍵やパスコードを送信・公開しない。接続経路やSSHサーバがなければ、その設定だけを次の実機作業として扱い、既定パスワードで接続を試さない。
3. スクリプトで初回ホスト鍵の自動承認やパスワード入力は行わない。管理用known_hostsは本人がホスト鍵を照合して作成し、鍵認証が成立した後にdoctorを1回実行する。
4. ジョブ登録用の固定root bridgeは、本人のsudo認証で一度だけ導入する。通常管理経路は `/var/jb/usr/bin/python3 /var/root/las-managed-control.py` の厳密なコマンド引数だけを許す。任意のroot Python `-c` やALLコマンド権限は与えない。SSH鍵・ホスト鍵の確認とは別の初回作業であり、今回は未適用。

## 私用設定と実行順序

設定、journal、取得ログ、配備キットは `.las-private/` 等の私用ディレクトリへ置く。具体的な値を公開リポジトリへ保存しない。設定例は構造だけで、実在の接続先ではない。

鍵がない場合のローカル専用準備。既存ssh-keygenを使い、新しい保存先の所有者限定ACLを検証してから生成する。ACLを確保できなければ秘密鍵生成前に停止する。既存の鍵・フォルダは上書きしない。結果には保存先と未登録状態だけを返し、鍵の内容はconsoleやリポジトリへ出さない。

```sh
py tools/manage_ipad.py generate-key --output .las-private/ipad-management-key
```

専用の無人接続用鍵として私用ACL内に保持し、iPadへ登録するのは公開鍵のみ。パスワードやiPadのパスコードを保存する仕組みではない。Windowsの所有者限定ディレクトリ・ファイルACLは、秘密鍵を作らない一時ファイルで実際に確認した。鍵の実生成と端末登録は別の操作である。

生成前のフォルダACL失敗は `private_directory_acl_not_verified_key_not_generated`、生成後のファイルACL失敗は `generated_key_candidate_retained_file_acl_not_verified` と区別する。後者では既存の鍵候補2ファイルを保持し、generate-keyを再実行・削除しない。ローカルの既存候補を読むだけの確認は次のコマンド。

```sh
py tools/manage_ipad.py verify-local-key --output PRIVATE_EXISTING_KEY_DIRECTORY
```

この確認は所有者限定ACLを検査し、既存ssh-keygenの公開部導出をcaptureして、既存公開鍵ファイルの先頭2フィールドと比較する。秘密鍵・公開鍵の本文や実fingerprintは表示しない。pair_verifiedがtrueでも、iPadへの登録・ホスト鍵照合・SSH認証成功は別なのでauthentication_readyはfalseのまま。

所有者が一致している既存候補のACLだけを修復する場合は、親の作業判断で次を1回適用した後にverify-local-keyで確認する。再生成や鍵本文の置換は行わない。各対象の所有者・非reparseを確認してからDACLだけを変更し、ownerや監査SACLを再設定しない。

```sh
py tools/manage_ipad.py protect-existing-key --output PRIVATE_EXISTING_KEY_DIRECTORY
```

WindowsのOpenSSH生成ファイルと同様に、所有者Read＋System/Adminの合成ACLで、通常のSet-Aclが監査権限を要求して失敗する状態を再現した。変更したAccess sectionだけを.NET APIで永続化する修正が、鍵を使わない合成ファイルで成功した。実鍵の自動再生成・無断移動・削除は行わない。

この修正後、保持したPC専用鍵候補について、鍵本文を表示せず既存鍵のDACL限定修復と読取り照合を行い、所有者限定ACL・公開部の一致を確認した。再生成はしていない。iPadへの公開鍵登録・ホスト鍵の本人照合・実際のSSH接続成功は未確認のまま。ACL修復ではhardlinkを変更前に拒否し、Windowsのleaf junctionをresolveで隠さずnative側のreparse検査へ渡す。

```json
{
  "destination": "mobile@IPAD_SSH_ALIAS",
  "port": 22,
  "known_hosts": "PRIVATE_KNOWN_HOSTS_PATH",
  "identity_file": "PRIVATE_EXISTING_KEY_PATH"
}
```

CLIは次の順に使用する。`RUN_ID` はこの試験用の英小文字・数字・ハイフンの新しい名称。同じjournal/run/baseを維持する。本人が接続を確認するまでは、package・generate-key・export-root-setup以外を実行しない。

```sh
py tools/manage_ipad.py package --repository . --output .las-private/n1-kit-final
py tools/manage_ipad.py doctor --config .las-private/ipad-connection.json --journal .las-private/n1-journal.json --run RUN_ID
py tools/manage_ipad.py deploy --config .las-private/ipad-connection.json --journal .las-private/n1-journal.json --run RUN_ID --bundle .las-private/n1-kit-final
```

doctorで `root_bridge_setup_required:true` の場合、deployしたキットの固定root setupを本人がNewTermで一度だけ実行する。以下のRUN_IDは上と同じものに置き換える。sudoのパスワードは端末で入力し、保存しない。

```sh
sudo /var/jb/usr/bin/python3 /var/mobile/las-managed/RUN_ID/tools/root_bootstrap.py
```

setupは埋め込みbridgeのコードSHAを照合し、root所有600のbridgeと、固定command引数だけのsudoers drop-inを新規排他作成する。active sudoersと既存のincludedirを確認し、visudoで候補と全体の構文を検証する。候補は読み込まれない一時名で完成・検証した後に原子的に有効化し、部分書込みのルールを有効化しない。既存のbridge・ルール・sudoers本体を上書きしない。visudo不在や構文確認不能なら導入を保留し、広いPython権限へfallbackしない。途中失敗は自動再実行せず残った新規ファイルを確認する。

ファイルを別経路で私用転送する場合は、ローカルの `export-root-setup --output PRIVATE_NEW_FILE.py` でも同じsetupを新規生成できる。setup自体を実行するのは本人の初回sudo認証時のみ。

setup後、doctorで `noninteractive_root_available:true` を確認してからprepare以降へ進む。

```sh
py tools/manage_ipad.py doctor --config .las-private/ipad-connection.json --journal .las-private/n1-journal.json --run RUN_ID
py tools/manage_ipad.py prepare --config .las-private/ipad-connection.json --journal .las-private/n1-journal.json --run RUN_ID
py tools/manage_ipad.py ui-status --config .las-private/ipad-connection.json --journal .las-private/n1-journal.json --run RUN_ID
py tools/manage_ipad.py start --config .las-private/ipad-connection.json --journal .las-private/n1-journal.json --run RUN_ID
py tools/manage_ipad.py status --config .las-private/ipad-connection.json --journal .las-private/n1-journal.json --run RUN_ID
py tools/manage_ipad.py fetch --config .las-private/ipad-connection.json --journal .las-private/n1-journal.json --run RUN_ID --output .las-private/n1-logs
py tools/manage_ipad.py stop --config .las-private/ipad-connection.json --journal .las-private/n1-journal.json --run RUN_ID
```

- packageは確認したhelper・既存観測2ファイルとruntimeのPythonソース、本人の一度の認証で使うroot_bootstrap.pyを、新しい私用キットへまとめる。ジョブを起動しない。
- deployは新規の管理用stageへ配置し、各ファイルのハッシュを照合。同じ版の再接続照合は許すが、既存内容は上書きしない。
- prepareは新規専用試験を準備し、worker.plistのラベル・mobile実行・固定supervisorと、planの送信1回/再試行0/観測180秒/hard limit240秒/停止上限250秒/legacy-OKを検査。root側plistは排他作成し、既存なら完全一致のみを確認する。
- startはSSHの前に私用journalへ試行済みを永続化し、端末側にも排他的開始markerを残す。PC切断・応答不明・異なるjournalからの同一run再起動でも、送信のやり直しをしない。
- 再接続ではstatus/fetch/stopへ進む。statusで登録なしになっていても、それを理由にstartを再実行しない。start命令の受理は処理完了や配送成功の証明ではない。
- PC接続が切れても停止の基準は端末側の独立supervisor。managerの接続タイムアウトを、端末ジョブの停止完了とは扱わない。
- fetchは新しい私用ディレクトリへevents等のログ、plan、payload、target、HTTP結果、終了・開始記録を保存する。これは実ID・ハッシュ・送信ペイロードを含み得る私用の証拠で、公開しない。既存ログは上書きせず、認証設定やキューの一括持ち出しはしない。helperの対象ID等を含むstatusはjournalだけに残し、consoleへ複製しない。
- stopはhelperへ停止を要求し、その成否にかかわらず観測専用ラベルの登録解除を試み、両ドメインを再確認する。問い合わせ失敗を「登録なし」と解釈しない。キューやログは削除しない。
- ロック中に対象が見えなかった場合のpost-unlock-onceは、停止・登録解除確認と本人による解除後にのみ使う別コマンド。helperの1回制限に加え、PC側journalにも接続前に試行を記録する。接続失敗から再読取りをやり直さず、既存証拠を回収する。新しい送信は行わない。

## 画面操作の限界

ui-statusは端末自身のJST時刻と、既存のDarwin信号の生値を取得するだけ。未校正の信号や命令の成功を、実際のロック状態の証明にはしない。doctorは既存uiopen/activator等の存在を読むが、存在だけでLINE起動・ロックが動くとは扱わない。

現時点でLINE前景化・ロック命令のnative adapterは未検証のためunsupported。ロック解除は本人の既存認証が必要で、パスコード保存・回避・OS再起動を実装していない。接続ができればジョブ管理とログ回収を先に使い、画面操作は本人の連続動画記録を維持する。

## 検証の範囲

関連テスト26件で、信頼済みホスト必須、非対話認証、切断時の開始記録、二重起動拒否、journalの対象固定、配備版変更の拒否、ログのハッシュ照合・上書き防止、全ての_private出力の私用journal限定保持、未校正信号を証明にしないこと、固定root command限定、bridgeのroot所有・600・非symlink/hardlink検査、visudo不在時の保留、鍵生成前後のACL失敗の区別、既存鍵候補の読取り照合・保全、ACL書換前のhardlink拒否、未解決leafのnative検査引渡しを検証。Windowsの所有者限定ACLとOpenSSH類似ACLの修復は、鍵を使わない合成ファイルでネイティブ確認した。SSH・sudoers導入・launchd・iPad native APIでの実動作は未確認。CI成功を実機成功に数えない。
