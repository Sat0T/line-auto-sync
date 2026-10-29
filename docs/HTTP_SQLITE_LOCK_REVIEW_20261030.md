# HTTP送信中のSQLite書込ロック — 調査と修正設計 (2026-10-30)

## 確認した現状
対象: `normal-only-rc1/sender.py` の `send_once()` と `normal-only-rc1/delivery_state.py`。
`send_once` は二度目の `with queue.transaction():` 内で `post(...)` を呼ぶ。`transaction()` は `BEGIN IMMEDIATE` を発行し、HTTP成功/失敗後までCOMMITしない。HTTPタイムアウトは30秒、SQLite接続のbusy timeoutは5秒。したがって、同じ状態DBへの別プロセスの書込みはHTTP待機中に失敗し得る。これはコードから確認できる問題で、実機での再現確認はまだない。

## 安全な修正方針
単純にHTTP呼出しをトランザクション外へ移すだけでは不十分。送信者が二重起動すると、同一バッチの並行送信、ACKとretry更新の競合、送信中の設定変更などが起こり得る。

1. 送信者間だけを直列化する、状態DBの書込トランザクションとは独立したプロセス間ロックを設ける。ロックは取得できない場合にfail-closed。iOS/rootlessでのロック実装・権限・異常終了時の自動解放を独立検証する。単なるロックファイル存在判定はクラッシュ後のstale lockを作るため不可。
2. 送信者ロック取得後、短いBEGIN IMMEDIATEでbind、凍結、verify_frozen、retry判定を行いCOMMITする。送信前の本文・IDs・batch ID・attempt情報を不変スナップショットとして保持。
3. DBトランザクションを閉じた状態でHTTPを呼ぶ。送信者ロックは保持し、別の収集プロセスによるキュー追加は許す。
4. HTTP後、短いBEGIN IMMEDIATEで設定と凍結状態を再検証し、送信前と同一バッチであることを確認した場合だけACKまたはretry/backoffを更新。異常ならfail-closedで、別バッチをACKしない。
5. ロック取得不能、クラッシュ、ACK喪失、HTTPタイムアウト時は、既存の凍結バッチを保持。受信側の永続dedupeがなければexactly-onceは保証できない。
6. 収集側が送信者ロックを取らない場合でも、状態DBへの変更が凍結バッチを破壊しないことを検証。外部の無承認書換えや同一権限攻撃者への耐性は保証しない。

## 必須のオフライン回帰テスト
- モックHTTPを30秒相当ブロックしている間、別接続から状態DBに書込み・COMMITできること（実時間待機は不要）。
- 同時にsend_onceを二つ起動してもHTTP送信が並列化しないこと。
- HTTP成功直後・ACK前のクラッシュ、ACK後・終了前のクラッシュ、タイムアウト後の再送で固定本文とIDsが変わらないこと。
- 送信中の新着収集でcollectedが進んでもACK対象が拡大しないこと。
- 送信中に凍結バッチ/送信先/認証設定が変化したら別バッチをACKしないこと。
- SQLiteロック競合、ロック取得失敗、プロセス異常終了、壊れた状態DBでfail-closed。
- Windows/Linux合成テストと独立rootless実機検証を分けて報告する。

## 今回の変更範囲
調査と設計文書のみ。ソース変更・テスト実行・PR作成・マージ・実機導入・実GASアクセス・配布なし。PR #8の上に積む独立ブランチで保存し、先行PRを変更しない。

## 次の実装単位
送信者ロックとトランザクション分離を同じ修正単位として実装する。送信者ロックなしでHTTPだけを外へ出す変更は採用しない。

## 実装・検証追記（2026-10-30）
- Draft PR #9: https://github.com/Sat0T/line-auto-sync/pull/9
- `sender.py`: 送信者専用のOSロックを導入し、HTTP前にSQLiteトランザクションをCOMMIT。応答後は凍結済み本文/IDs/batch ID/試行状態を再照合してからACKまたは再試行情報を確定する。
- `tests/test_delivery.py`: 送信中の状態DB別接続書込み、二重送信防止、送信中の凍結状態変更、送信中の収集を検証する4ケースを追加。既存の並行送信テストは新しいfail-closed仕様に合わせた。
- 最終ソース・テストコミット `5eafe7a26e7957982a0d5668cfdef9b5fc7323cc` のCI: https://github.com/Sat0T/line-auto-sync/actions/runs/40936313137 。Windows/Linux × Python3.9/3.12 の4テストジョブとGit履歴監査の計5ジョブすべて成功。合成テスト49件。初回の失敗は修正済み。
- 実機/rootlessでのロック動作、実GAS、実LINE、権利、配布は未検証。senderロックの同一権限攻撃者に対する耐性は保証しない。
- この文書追記以降の最新CIは別途照合する。上記成功は記載したコードコミットに対する結果。
