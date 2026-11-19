# 次の試験：新規送信なしのDB読み取り観測

**準備とオフライン検証のみ完了。以下は本人が実機操作を許可した後の手順で、今回は実行しない。** 旧試験ワーカー、GAS、メール、自動化、パッケージを動かさない。

## 仮説・前回との差・判別の限界

ロック時にDB/WAL/SHMの可読性・参照先が変化する可能性を、LINE側の同期遅延と分ける。既存の読み取り観測を、mobileの独立launchdジョブ・既存と同じ時間制限付き電源アサーションで180秒実行する。新しいトーク本文や未配送文を引数に渡さず、許可済みトークの行数・空本文数・収集位置より後の本文行数・DB全体のコミット変化・ファイル状態・読み取り時間・任意のロック信号を記録する。旧収集ワーカーやsenderをimportしない。

この試験は新着を入力しないため、新着のDB反映時刻やLINEサーバ同期の成立を証明できない。同じDBの既存データをロック中も読めるかという、前回不足した証拠を取るための試験。収集位置の修正を配備する試験ではない。

## 本人の一括操作（許可後のみ）

NewTermで各コマンドの成功を確認してから次へ進む。失敗時はそこで止める。作成先が既にあれば上書き・削除せず、既存の観測結果を確認する。

1. 次の1行で、確認済みコミットの3ファイルだけを新しい個人用ディレクトリへ取得し、コードのSHA-256を照合する。ネット接続はこの取得時だけ。取得後の観測にはネット送信機能がない。

```sh
python3 -c 'from pathlib import Path; from urllib.request import urlopen; import hashlib,os; os.umask(0o077); p=Path.home()/"las-observation-kit"; p.mkdir(mode=0o700); base="https://raw.githubusercontent.com/Sat0T/line-auto-sync/69d17b7d874ce715b2ef475c4f110c9bf757881d/tools/"; expected={"prepare_db_observation.py":"f7dd9ea3da3d7b2786db63f5dd6c3fe2050fa9e66849675b7135a47ebcb10fc1","observe_line_db.py":"105b480d1c03dee1221a074a18d6a9f922740825a6760e7ec9e83fce6b907190","keep_power_lock.py":"54a28c93e635fda16fb4a957f87addf8968ad44abfe1ef6a737fecee074d06ca"}; data={n:urlopen(base+n,timeout=20).read() for n in expected}; assert all(hashlib.sha256(data[n]).hexdigest()==expected[n] for n in expected),"code_hash_mismatch"; [(p/n).write_bytes(data[n]) for n in data]; print("KIT_VERIFIED_NOT_STARTED")'
```

2. 停止済みの既存プロファイルを読み取り、観測専用の起動ファイルを作る。設定・キューは変更しない。準備後にプロファイルが変わった場合も起動を拒否する。

```sh
python3 ~/las-observation-kit/prepare_db_observation.py prepare --profile /var/mobile/las-keep-lock-power-r3 --bundle /var/mobile/las-db-observation-once
```

`OBSERVATION_PREPARED_NOT_STARTED`が出たら準備完了。この時点ではジョブは動かない。

3. 以下で専用plistをroot用の新規ファイルへ排他作成する。既存ファイルは上書きしない。sudoのパスワードは端末で入力し、共有しない。

```sh
sudo /var/jb/usr/bin/python3 -c 'from pathlib import Path; import os; f=os.fdopen(os.open("/var/root/las-db-observation-only.plist",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"wb"); f.write(Path("/var/mobile/las-db-observation-once/observation.plist").read_bytes()); f.close()'
```

4. 次の1行が成功してSTART時刻が出たら、20秒以内を目安にiPadをロックし、別端末で4分のタイマーを開始。LINE文の入力・送信はしない。ロック開始と解除の時刻を記録する。観測は約3分で終わるので、解除は4分後とする。解除時にLINEが表示されても、それ自体を失敗扱いにしない。

```sh
sudo /var/jb/bin/launchctl bootstrap system /var/root/las-db-observation-only.plist && date '+START %Y-%m-%d %H:%M:%S %z'
```

5. 解除後、以下で終了とエラー数を確認する。`stopped:true`に加え、`read_errors`と`fatal_errors`を確認する。エラーがある終了を成功とは数えない。生ログは端末内だけで保持し、公開GitHubへ貼らない。

```sh
python3 ~/las-observation-kit/prepare_db_observation.py status --bundle /var/mobile/las-db-observation-once
```

## 判定

- 読み取り環境の観測成立：ロック区間に複数のsnapshotが進み、既存の許可トークの件数を読めており、DBと存在するWAL/SHMへのアクセスエラーがなく、停止記録がある。起動前後の同一性検査も通る。scope-onlyではmarker_trackingはfalseで、マーカー0を新着不在とは判定しない。
- ロック中だけ可読性が落ちる／参照先・ファイル同一性が変わる／SQLエラー：読み取り環境側の問題として、その段階を追う。エラーは行数0と区別する。WAL/SHMの不存在だけでは異常と断定しない。
- 同じデータを読めていても新着同期は未証明。data_versionは他トークを含むDB全体で、特定メッセージのコミット時刻ではない。ロック信号は非公開APIの生値であり、取得・校正不能時は未確認のまま。LINEの背景維持フックの実効性も、この試験だけでは立証しない。
- 観測が途切れる／起動しない：新着0の根拠にせず、観測自体を不成立とする。私用console.jsonlのbootstrap_errorと時刻で切り分ける。

## 終了・途中停止

180秒の期限で停止し、電源アサーションを解放する。手動で中止する場合や終了後の登録解除は、以下の**観測専用ラベルだけ**を対象にする。rootlessのドメイン案内が出る場合に備え、同じラベルのuser/foreground側も確認・解除する。旧ワーカーのラベルやプロセスへは操作しない。

```sh
sudo /var/jb/bin/launchctl bootout system/com.sat0t.line-auto-sync.db-observation-only
sudo /var/jb/bin/launchctl bootout user/foreground/com.sat0t.line-auto-sync.db-observation-only
```

登録が既になければNo such process等となる。停止要求から終了待ちは最大15秒のlaunchd設定。終了記録がない場合は正常終了を断定せず、必要なら同じラベルをprintして残存を確認する。OS再起動・パッケージ変更・ファイル削除は不要。同じ開始コマンドは再実行しない。実機のネイティブAPIとlaunchdの挙動は今回未検証。

ロック中配送の合格には、別途許可を得た同一新着について、ロック継続中のDB可視化・GAS受理・本人宛Gmail到着を解除前の記録で結ぶ必要がある。本試験だけでその合格やMVP完成とはしない。
