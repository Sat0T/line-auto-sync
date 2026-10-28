"""Stop on an oversized head; a valid preceding prefix may be sent first."""
import json

LIMIT = 60000


def retain(row, record):
    raise RuntimeError("Oversized message: delivery is paused; cursor not advanced")


def select_prefix(rows, enrich):
    lines, last_id, previous, seen = [], None, -1, {}
    for row in rows:
        pk = int(row[0])
        if pk < previous:
            raise ValueError('normal_rows_not_ordered')
        previous = pk
        if pk in seen:
            if seen[pk] != row:
                raise ValueError('normal_rows_duplicate_conflict')
            continue
        seen[pk] = row
        record = enrich(dict(id=row[0], time_ms=row[1], chat=row[2], sender=row[3], text=row[4]))
        message = json.dumps(record, ensure_ascii=False) + '\n'
        if len(message.encode('utf-8')) > LIMIT:
            if lines:
                break
            retain(row, record)
        if len((''.join(lines) + message).encode('utf-8')) > LIMIT:
            break
        lines.append(message)
        last_id = str(row[0])
    return {'lines': lines, 'last_id': last_id, 'held': 0}


def finish_only_held(selection, root):
    raise RuntimeError('Unsent held rows cannot advance an acknowledged cursor')
