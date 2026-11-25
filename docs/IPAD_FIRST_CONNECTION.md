# 初回接続の実行順序 — 2026-11-25

本人の継続承認内で進める。今回、本人は「今は接続できない」と回答。PC画面操作は本人のEscで停止したため、その後は使っていない。実機接続・公開鍵登録・GASへの試験送信は未実施。同じ無送信DB観測は繰り返さない。

## 今回選んだ接続と転送の経路

PCにはWindows OpenSSH clientとiCloud Driveの既存フォルダがある。現在の調査では接続中のApple USB機器や、動作するUSB SSH proxyは確認できない。USBケーブルの接続だけでSSHが成立するとは扱わない。

今回、転送bundleのpayload22ファイル＋manifestをPCの新しいiCloudフォルダへコピーし、PC上の読戻しhash一致を確認済み。私用NewTermコマンド集（6コマンド）も固定コード版・実manifestへ結び付けて作成し、埋込みPythonの構文を検証した。端末で実行しておらず、iPad到達や認証は未確認。

優先経路は**iCloud Driveで公開鍵セットと固定コードを渡し、同じ信頼できるLAN上の既存OpenSSHへ鍵認証**する方法。iPad自身のWi-Fiアドレスと公開ホスト鍵を私用offerに出し、本人の独立したfingerprint照合後にだけPC設定を生成する。IP推測・LAN走査・初回鍵の自動承認・既定パスワードは使わない。PCにiCloudフォルダがあることと、対象iPadまで同期済みであることは別の確認。

Appleの[WindowsのiCloud Driveの説明](https://support.apple.com/en-ng/guide/icloud-windows/icwddbc813bd/icloud)に従い、iPadの「ファイル → iCloud Drive」で転送フォルダを探す。PC側の実パス、manifestの実hash、具体的な一括コマンドは私用ファイルに保持し、公開文書へ載せない。

## PC側で用意するもの

- 修正後の新キット：`.las-private/n1-kit-20261125-stopfix/`。旧2キットは保持。
- 元の専用鍵：`.las-private/ipad-auth-n1/`。再生成しない。秘密鍵をiCloudへ置かない。
- 元の公開鍵登録セット：`.las-private/ipad-enrollment-n1/`。内容を再生成せず、新しい転送bundleへ照合してコピーする。
- 転送bundle名：`LAS-Private-Onboarding-20261125`。enrollmentの公開鍵・補助コードと修正版キットのみ。旧キュー・設定・認証token・実会話は入れない。
- 本人向けの私用コマンド集：`.las-private/OWNER_NEWTERM_20261125.txt`。固定版取得、manifest照合、新規ホーム配置、probe、公開鍵登録、offer作成を順番に記載する。

`tools/prepare_ipad_connection.py package`は元セットのSHAと、キットが現在のソース/生成bootstrapと一致することを確認し、既存出力先を拒否する。コピーしたことを端末登録・SSH成功とは記録しない。

## 本人がiPadで行う操作

1. iPadを解除し、PCと同じ信頼できるWi-Fiへ接続。USBも接続する場合、信頼画面が出れば本人が操作する。Wi-Fi SSHの成立にはUSBは必須ではない。公衆ネットワークでSSHを開かない。
2. 「ファイル → iCloud Drive → LAS-Private-Onboarding-20261125」を開き、必要なファイルを端末へダウンロードする。フォルダが見えなければ転送未確認として止める。LINEのKeepへ操作コマンドを送らない。
3. 私用コマンド集の固定版取得・配置をNewTermで順に実行。`owner_bundle_verified:true`、`new_home_staged:true`、`job_started:false`を確認する。既存ホームがあれば削除・上書きせず、その状態を確認する。
4. 次を実行し、`mobile_uid:true`と準備可を確認する。サーバの可用性と、接続/認証成功は別である。

```sh
python3 /var/mobile/las-ipad-onboarding-20261125/enrollment/enroll_ipad_key.py doctor
python3 /var/mobile/las-ipad-onboarding-20261125/enrollment/ipad_connection_offer.py probe
```

5. 準備できている場合だけ、同じ私用コマンド集の公開鍵登録を1回実行する。

```sh
python3 /var/mobile/las-ipad-onboarding-20261125/enrollment/enroll_ipad_key.py enroll --public-key-file /var/mobile/las-ipad-onboarding-20261125/enrollment/pc.pub
```

正常時は`key_present:true`、`restricted:true`、`requires_review:false`。既存鍵は保持。エラー・広すぎる権限・別ownerは自動で直さず、その診断から進める。

6. 私用コマンド集のoffer作成を実行する。出力先はiCloudの同じ転送フォルダ内の新しい`device-offer.json`。`offer_written:true`とiPad画面の`device_fingerprint_private`を確認する。このfingerprintを別経路でWorkへ伝え、PCが受信したofferの値と照合する。秘密鍵やパスコードは伝えない。offerは端末の公開ホスト鍵・LANアドレスを含むので公開しない。書込み済みofferを削除して再作成しない。
7. WorkがPC側`accept-offer`で別経路fingerprintと公開鍵からの計算値を照合し、新規known_hosts/connection.jsonを作り、doctorを1回実行する。鍵登録や設定ファイル生成だけでは接続成功にしない。失敗時は同じ接続を繰り返さず、診断する。
8. 接続成功後、Workが新キットを配置・照合する。本人はNewTermで次を1回実行し、sudoパスワードは端末内だけで入力する。root bridgeも未導入の新規状態を前提とし、既存なら上書きしない。

```sh
sudo /var/jb/usr/bin/python3 /var/mobile/las-managed/n1-20261120/tools/root_bootstrap.py
```

正常時は`fixed_root_bridge_installed:true`、`sudoers_syntax_valid:true`。Workがdoctorの`noninteractive_root_available:true`を確認した後、専用ジョブprepareへ進む。既存プランのbase `/var/mobile/las-lock-n1`と専用ラベルを変更しない。

## OpenSSHが足りなかった場合

先にprobeの不足項目を確認する。[Procursusのpackage定義](https://github.com/ProcursusTeam/Procursus/blob/main/build_info/openssh-server.control)と[配置定義](https://github.com/ProcursusTeam/Procursus/blob/main/makefiles/openssh.mk)から、rootless環境向けの候補は既存のProcursus repoの`openssh-server`。rootlessのprefixは`/var/jb`で、実機のarchitectureと候補を確認してから最小インストールする。旧パッケージを削除・更新する計画なら進めない。

```sh
dpkg --print-architecture
apt-cache policy openssh-server
sudo apt-get --no-remove --no-upgrade --simulate install openssh-server
```

`iphoneos-arm64`、既存のProcursus候補、削除/更新なしの計画を確認できた場合だけ、同じ条件で`--simulate`を外して導入する。通常の可逆な導入は本人の事前承認内だが、現在は実機のpackage状態が未読なので実行していない。無意味な再導入や無関係な更新をしない。

既存serverがあって公開ホスト鍵だけ未生成なら、既存のrootless ssh-keygenと設定パスを確認後に`sudo /var/jb/usr/bin/ssh-keygen -A`で不足分だけを生成する。[OpenSSHの-A仕様](https://man.openbsd.org/ssh-keygen#A)は既存host keyの上書きを行わない。生成済みPC専用鍵とは別物で、こちらを再生成しない。

サーバ登録の有無は`/var/jb/Library/LaunchDaemons/com.openssh.sshd.plist`とlaunchctlの実読取りで確認する。[Procursusのplist](https://github.com/ProcursusTeam/Procursus/blob/main/build_misc/openssh/com.openssh.sshd.plist)にはSSH socketがあるが、公開ソースだけで当該端末の起動・待受けと判断しない。停止していると確認した場合にのみ、実在するplistを使う最小のbootstrapを選ぶ。登録済みへの同じbootstrapの反復、OS再起動はしない。

[palera1nの一次資料](https://github.com/palera1n/palera1n/blob/main/docs/palera1n.1)にある組込みport44はiPadではloopback用であり、Windowsから同じportへ直接届く保証ではない。USBを使う代替案には対象機器とusbmux/proxyの確認が別途必要。現状のPCではこの経路の不足部品を確認できていないため、既定パスワードでの接続やUSB SSH成立の推定は行わない。

## 接続後の試験と画面操作

Workがdoctor・修正版キット照合・root bridge probe・prepareを行い、実GAS配備/strict OK契約/本人転送先/台帳とGmailの読取り経路を確認してから、[N1手順](LOCK_N1_TRIAL.md)へ進む。文はコード・手順・キットで固定し、日付だけ手で変えない。

LINE前景化・ロック用の端末既存コマンドは接続後に存在/実動作を確認し、使えるものだけ実装する。現在のui-statusは命令機能ではない。命令成功、実画面、未校正の信号を分離し、動画照合を維持する。解除は本人認証に残す。新着の送信は最大1回・再試行0、切断後は同じ実行のstatus/fetch/stopへ進む。
