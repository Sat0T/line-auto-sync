# iPadの画面操作：実装と未検証範囲

PCの画面操作は再開していない。以下は接続後に端末上で実行するCLIの準備であり、実機成功の記録ではない。

## LINE前景化の要求

`manage_ipad.py launch-line`を追加した。認証済み接続・stage照合・専用profileの準備後、iPadOS 16.7.14と既存uiopenのhelpに`--bundleid`がある場合だけ、固定の`jp.naver.line`へ起動要求する。新しいpackage導入、別URL、トーク入力、メッセージ送信を行う機能ではない。

[Procursusのuiopen実装](https://github.com/ProcursusTeam/uikittools-ng/blob/main/uiopen.m)はbundle IDによる起動を実装している一方、openApplicationの戻り値をプロセス終了コードへ反映していない。このためexit 0をLINE前景化の成功とは扱わない。今回のadapterも`command_completed`と`foreground_verified:false`を分ける。target OSで実際に起動できるかは未確認。

専用profileの私用`ui-actions.jsonl`へ、画面を変える要求前の時刻と命令終了後のJST・結果を記録する。命令前の記録を永続化してから実行し、失敗/timeoutは未知として保持して自動再試行しない。既存fetchで回収する。端末の実画面・途中の画面復帰は従来の連続動画で記録する。

## ロックと時刻

ui-statusは端末のJSTと未校正信号を読む機能のまま。ロックを実行したり、信号値からロック済みと判定したりしない。既存doctorでnative commandの有無を読んでから、その版の公開一次資料と実機の動作を確認する。存在や命令の終了だけを機能した根拠にしない。

現時点では対象端末上で使えるロック実行手段は未特定。未確認のActivatorイベントやprivate ABIを推測して命令しない。respring/OS再起動をロック操作の代わりにしない。別packageの導入も行っていない。初回接続後に既存の手段を調べ、校正できるまで物理ロック＋動画照合を使う。本人認証を要する解除は本人操作に残す。

合成試験は、tool/OS未適合で起動しないこと、命令前の私用記録、固定bundleのみ、命令timeout時に再試行しないこと、exit 0から実画面/ロック成功を創作しないことを確認。これはiPadのnative APIや実画面の検証ではない。
