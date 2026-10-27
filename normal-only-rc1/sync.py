from runtime_paths import STATE_DIR as _las_state_dir, require_enabled as _las_require_enabled
_las_require_enabled()
from checkpoint import scan_low as _las_scan_low
from pathlib import Path
from urllib.parse import quote
import sqlite3,csv

p = _las_state_dir
db=(p/"dbpath").read_text().strip()
c=sqlite3.connect("file:"+quote(db)+"?mode=ro",uri=True)
high=c.execute("SELECT COALESCE(MAX(Z_PK),0) FROM ZMESSAGE").fetchone()[0]
state=p/"lastid"
low = _las_scan_low(p, int(state.read_text()) if state.exists() else high)
rows=c.execute("SELECT Z_PK,ZTIMESTAMP,ZCHAT,ZSENDER,ZTEXT FROM ZMESSAGE WHERE Z_PK>? AND Z_PK<=? AND length(ZTEXT)>0 ORDER BY Z_PK",(low,high)).fetchall()
if rows:
    with (p/"new.csv").open("w",newline="",encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
(p/"lastid.tmp").write_text(str(high))
(p/"lastid.tmp").replace(state)
print("NEW",len(rows),"LAST_ID",high)
# hourly sender
import subprocess,sys
subprocess.run([sys.executable, str(Path(__file__).with_name("sender.py"))], check=True)

# LINE_SQUARE_HOOK_V1
import os as _las_os
if _las_os.environ.get("LINE_AUTO_SYNC_OPENCHAT_ENABLED") == "1":
    raise RuntimeError("OpenChat is unavailable in dev3")
