"""A dated trial uses one generated text/code/plan and preserves old N1."""
import ast
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import test_delivery as fixtures
from test_single_lock_delivery import SETUP

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fresh', REPO / 'tools/prepare_fresh_lock_trial.py')
fresh = importlib.util.module_from_spec(spec); spec.loader.exec_module(fresh)


class FreshTrialTests(unittest.TestCase):
    def test_generated_payload_uses_only_the_new_text_and_pinned_target(self):
        fixture = fixtures.DurableDeliveryTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'new'
            fresh.build(REPO, out, datetime(2099, 11, 27, 18, tzinfo=fresh.JST),
                        now=datetime(2026, 11, 25, 19, tzinfo=fresh.JST))
            code = 'import sys;sys.path.insert(0,' + repr(str(out / 'kit/tools')) + ')\n' + SETUP
            fixture.case(code + '''
assert 'N2' in trial.TEXT and '20261120-N1' not in trial.TEXT
manifest=queue_target()
body=trial.read_json(root/'payload-private.json')['body']
assert trial.validate_payload(config,plan,'103',body,103)==manifest
assert manifest['count']==1
import cli
assert cli.queue_status()['pending']==2
''')

    def test_build_is_consistent_exclusive_and_leaves_original_template_unchanged(self):
        original = (REPO / 'tools/keep_lock_delivery.py').read_bytes()
        now = datetime(2026, 11, 25, 19, 0, tzinfo=fresh.JST)
        due = datetime(2026, 11, 27, 18, 0, tzinfo=fresh.JST)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'new'
            plan = fresh.build(REPO, out, due, now=now)
            helper = out / 'kit/tools/keep_lock_delivery.py'
            tree = ast.parse(helper.read_text(encoding='utf-8'))
            constants = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
                         if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                         and n.targets[0].id in ('TEXT', 'LABEL', 'TRIAL_DUE_JST')}
            self.assertEqual(constants['TEXT'], plan['text'])
            self.assertIn(plan['text'], (out / 'TRIAL_STEPS.md').read_text(encoding='utf-8'))
            self.assertEqual(constants['LABEL'], plan['label'])
            self.assertEqual(plan['send_max'], 1); self.assertFalse(plan['started'])
            self.assertNotIn('lock-n1', plan['base'])
            self.assertEqual(json.loads((out / 'trial-plan.json').read_text(encoding='utf-8')), plan)
            with self.assertRaises(ValueError): fresh.build(REPO, out, due, now=now)
        self.assertEqual((REPO / 'tools/keep_lock_delivery.py').read_bytes(), original)

    def test_expired_or_unzoned_due_is_rejected_before_creating_output(self):
        now = datetime(2026, 11, 25, 19, tzinfo=fresh.JST)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'new'
            for due in (now, now + timedelta(minutes=30), now.replace(tzinfo=timezone.utc)):
                with self.assertRaises(ValueError): fresh.build(REPO, out, due, now=now)
            self.assertFalse(out.exists())

    def test_generated_expiry_guard_and_actual_http_boundary(self):
        raw = fresh.variant((REPO / 'tools/keep_lock_delivery.py').read_text(encoding='utf-8'),
                            'synthetic new text', 'synthetic label', '2026-11-25T18:00:00+09:00')
        tree = ast.parse(raw)
        selected = [n for n in tree.body if isinstance(n, (ast.Assign, ast.FunctionDef))
                    and (getattr(n, 'name', '') == 'ensure_trial_unexpired'
                         or isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                         and n.targets[0].id == 'TRIAL_DUE_JST')]
        namespace = {'datetime': datetime, 'timezone': timezone}
        exec(compile(ast.Module(body=selected, type_ignores=[]), '<expiry>', 'exec'), namespace)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return datetime(2026, 11, 25, 9, tzinfo=timezone.utc)
        namespace['datetime'] = Clock
        with self.assertRaisesRegex(RuntimeError, 'deadline_passed'): namespace['ensure_trial_unexpired']()
        child = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'http_child')
        block = next(n for n in child.body if isinstance(n, ast.Try))
        at = next(i for i, n in enumerate(block.body) if isinstance(n, ast.With))
        self.assertEqual(ast.unparse(block.body[at - 1]), 'ensure_trial_unexpired()')
        for name in ('prepare', 'worker', 'http_child'):
            function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertEqual(ast.unparse(function.body[0]), 'ensure_trial_unexpired()')


if __name__ == '__main__': unittest.main()
