from runtime_paths import STATE_DIR as _las_state_dir, require_enabled as _las_require_enabled, read_credential as _las_read_credential
_las_require_enabled()
# NORMAL_HOLD_PATCH_V1
# LINE_NAMES_ADAPTER_V1
import importlib.util as _line_import
import sys as _line_sys
import legacy_names as _line_names
_line_names.acquire_sender_lock()
import normal_hold as _normal_hold
from pathlib import Path
import csv,json,time,urllib.request
p = _las_state_dir
f=p/'new.csv'
if not f.exists(): quit()

def read(n):
    if n in ("endpoint", "token"):
        return _las_read_credential(n)
    return (p / n).read_text().strip()
t=p/'sent_at'
if t.exists() and time.time()-float(read('sent_at'))<3600: quit()
last=int(read('sentid')) if (p/'sentid').exists() else 0
with f.open(newline='',encoding='utf-8') as h:
    rows=[r for r in csv.reader(h) if len(r)==5 and r[0].isdigit() and int(r[0])>last]
if not rows: quit()
lines=[]
_normal_selection = _normal_hold.select_prefix(rows, _line_names.enrich_legacy)
lines = _normal_selection['lines']
if not lines: _normal_hold.finish_only_held(_normal_selection, p)
payload=dict(token=read('token'),maxid=_normal_selection['last_id'],body=_line_names.finalize_body(''.join(lines)))
data=json.dumps(payload,ensure_ascii=False).encode()
req=urllib.request.Request(read('endpoint'),data,{'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=30) as res:
    ans=res.read().decode().strip()
if ans!='OK': raise RuntimeError('SEND_FAILED '+ans[:50])
(p/'sentid').write_text(payload['maxid'])
t.write_text(str(time.time()))
print('SENT',len(lines))
