from runtime_paths import STATE_DIR as _las_state_dir, CONFIG_FILE as _las_config_file
"""Durably retain oversized normal messages while sending the usable prefix."""
import csv,hashlib,json,os,sqlite3,time,uuid
from pathlib import Path

STAGE = _las_state_dir
APP_ID=0x4C4E4831
LIMIT=60000
csv.field_size_limit(max(csv.field_size_limit(),16*1024*1024))

def retain(row, record):
    raise RuntimeError("Oversized message: delivery is paused; cursor not advanced")

def select_prefix(rows,enrich):
    lines=[];last_id=None;held=0;previous=-1;seen={}
    for row in rows:
        pk=int(row[0])
        if pk<previous:raise ValueError('normal_csv_not_ordered')
        previous=pk
        if pk in seen:
            if seen[pk]!=row:raise ValueError('normal_csv_duplicate_conflict')
            continue
        seen[pk]=row
        record=enrich(dict(id=row[0],time_ms=row[1],chat=row[2],sender=row[3],text=row[4]))
        msg=json.dumps(record,ensure_ascii=False)+'\n'
        if len(msg.encode())>LIMIT:
            retain(row,record)
            held+=1;last_id=str(row[0]);continue
        if len((''.join(lines)+msg).encode())>LIMIT:break
        lines.append(msg);last_id=str(row[0])
    if held:print('NORMAL_LONG_MESSAGES_RETAINED',held)
    return {'lines':lines,'last_id':last_id,'held':held}

def finish_only_held(selection,root):
    if selection['lines'] or not selection['held'] or selection['last_id'] is None:
        raise ValueError('invalid_empty_normal_selection')
    root=Path(root)
    temp=root/('.sentid-held-'+uuid.uuid4().hex)
    fd=os.open(str(temp),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as stream:
            stream.write(selection['last_id']);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,root/'sentid')
    finally:
        if temp.exists():temp.unlink()
    # This checkpoint covers sent OR durably held rows; held.delivered_at stays NULL.
    # Do not update the successful-mail clock when no mail was sent.
    raise SystemExit(0)
