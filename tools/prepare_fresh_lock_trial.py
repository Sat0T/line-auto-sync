"""Generate a new dated, isolated trial kit without changing old N1 artifacts.

No device access, message input, deployment, job start or HTTP send.
"""
import argparse
import ast
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

JST = timezone(timedelta(hours=9))


def fresh_guard_source(deadline):
    return ast.parse('TRIAL_DUE_JST = ' + repr(deadline) + '\n'
        'def ensure_trial_unexpired():\n'
        '    if datetime.now(timezone.utc) >= datetime.fromisoformat(TRIAL_DUE_JST):\n'
        '        raise RuntimeError("trial_deadline_passed_generate_fresh_trial")\n')


def variant(source, text, label, deadline):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == 'LOCK_N1_PREPARED_NOT_STARTED':
            node.value = 'LOCK_FRESH_PREPARED_NOT_STARTED'
    guard = ast.Expr(value=ast.Call(func=ast.Name(id='ensure_trial_unexpired', ctx=ast.Load()), args=[], keywords=[]))
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ('TEXT', 'LABEL'):
                node.value = ast.Constant(value=text if name == 'TEXT' else label); found.add(name)
        if isinstance(node, ast.FunctionDef) and node.name in ('prepare', 'worker', 'http_child'):
            node.body.insert(0, guard)
            found.add(node.name)
            if node.name == 'http_child':
                # Also reject expiry at the actual network boundary, after fsync.
                for child in ast.walk(node):
                    if isinstance(child, ast.Try):
                        for i, statement in enumerate(child.body):
                            if isinstance(statement, ast.With) and any(isinstance(x.context_expr, ast.Call)
                                    and isinstance(x.context_expr.func, ast.Attribute)
                                    and x.context_expr.func.attr == 'open' for x in statement.items):
                                child.body.insert(i, guard); found.add('http_boundary'); break
    if found != {'TEXT', 'LABEL', 'prepare', 'worker', 'http_child', 'http_boundary'}:
        raise ValueError('review_changed_trial_template_before_generation')
    # The original template imports datetime/timezone; insert helpers after imports.
    tree.body[1:1] = fresh_guard_source(deadline).body
    raw = (ast.unparse(ast.fix_missing_locations(tree)) + '\n').encode('utf-8')
    compile(raw, '<fresh-trial>', 'exec')
    return raw


def build(repository, output, due, *, now=None):
    now = datetime.now(JST) if now is None else now
    if now.utcoffset() != timedelta(hours=9) or due.utcoffset() != timedelta(hours=9):
        raise ValueError('explicit_jst_times_required')
    if due <= now + timedelta(hours=1) or due.second or due.microsecond:
        raise ValueError('deadline_must_be_more_than_one_hour_ahead_at_whole_minute')
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('new_trial_output_required_preserve_old_state')
    repository = Path(repository).resolve()
    suffix = now.strftime('%Y%m%d-%H%M%S')
    tag = 'LAS-LOCK-' + suffix + '-N2'
    base = '/var/mobile/las-lock-n2-' + suffix
    label = 'com.sat0t.line-auto-sync.lock-n2-' + suffix
    text = '{} {}年{}月{}日{}時{}分までに配送試験の結果を確認する'.format(
        tag, due.year, due.month, due.day, due.hour, due.minute)
    raw = variant((repository / 'tools/keep_lock_delivery.py').read_text(encoding='utf-8'), text, label, due.isoformat())
    spec = importlib.util.spec_from_file_location('fresh_manager', repository / 'tools/manage_ipad.py')
    manager = importlib.util.module_from_spec(spec); spec.loader.exec_module(manager)
    output.mkdir(mode=0o700)
    source = output / 'source'; (source / 'tools').mkdir(parents=True)
    (source / 'normal-only-rc1').mkdir()
    for path in (repository / 'normal-only-rc1').glob('*.py'):
        shutil.copyfile(path, source / 'normal-only-rc1' / path.name)
    for name in ('keep_power_lock.py', 'observe_line_db.py'):
        shutil.copyfile(repository / 'tools' / name, source / 'tools' / name)
    (source / 'tools/keep_lock_delivery.py').write_bytes(raw)
    manager.package(source, output / 'kit')
    plan = dict(version=1, text=text, base=base, label=label, run='n2-' + suffix,
                due_jst=due.isoformat(), generated_jst=now.isoformat(), old_n1_preserved=True,
                helper_code_sha256=hashlib.sha256(raw).hexdigest(), send_max=1, retries=0,
                started=False, external_send=False, deployment_binding_verified=False)
    with (output / 'trial-plan.json').open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
    steps = ('# 新しい試験1件 — 未起動\n\n固定文（この1件だけ、開始前は入力しない）：\n\n'
             + text + '\n\nbase: ' + base + '\nlabel: ' + label + '\nrun: ' + plan['run']
             + '\n\n同じフォルダのkitを使う。旧N1のprofile/queue/ACK/kit/ログを変更しない。\n'
             '本人の初回接続後、実GAS配備・応答契約・転送先のbinding確認が先。\n'
             'prepare/worker/HTTP直前は期限超過を拒否。期限が過ぎたらこのkitを書き換えず、新規生成する。\n'
             '専用journalを使い、startは1回。観測180秒・HTTP30秒・supervisor240秒・停止上限250秒。\n'
             '実ロックから300秒かつ終了/登録解除確認後に解除。動画とiPadの秒付きJSTで照合。\n'
             '送信最大1回・再試行0。切断後は同じrunのstatus/fetch/stopへ進む。\n'
             '未検出時は停止確認後の解除・DB読取り1回のみ。再起動送信しない。\n'
             '成功判定は同一対象のDB可視化窓・GAS台帳/strict OK・本人Gmail fullが確実なロック区間内。\n')
    with (output / 'TRIAL_STEPS.md').open('x', encoding='utf-8') as stream: stream.write(steps)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--due-jst', required=True)
    args = parser.parse_args()
    plan = build(args.repository, args.output, datetime.fromisoformat(args.due_jst))
    print(json.dumps({'fresh_trial_prepared': True, 'text': plan['text'], 'base': plan['base'],
                      'started': False, 'external_send': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
