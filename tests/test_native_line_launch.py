"""Native CLI launch adapter with disposable logs, never an actual device/app."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('manager', Path(__file__).resolve().parents[1] / 'tools/manage_ipad.py')
manager = importlib.util.module_from_spec(spec); spec.loader.exec_module(manager)


class NativeLaunchTests(unittest.TestCase):
    def case(self, tool, run):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        function = next(n for n in ast.parse(manager.REMOTE).body
                        if isinstance(n, ast.FunctionDef) and n.name == 'native_line_launch')
        space = {'base': base, 'os': os, 'json': json,
                 'shutil': SimpleNamespace(which=lambda _: tool),
                 'subprocess': SimpleNamespace(run=run, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
                                               TimeoutExpired=subprocess.TimeoutExpired)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<native-launch>', 'exec'), space)
        return space['native_line_launch'], base

    def test_missing_or_incompatible_existing_tool_never_launches(self):
        launch, base = self.case(None, lambda *_a, **_k: self.fail('no tool'))
        self.assertFalse(launch('16.7.14')['supported']); self.assertEqual(list(base.iterdir()), [])
        self.assertEqual(launch('17.0')['reason'], 'native_os_not_reviewed')
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, b'unknown interface', b'')
        launch, base = self.case('existing-uiopen', run)
        self.assertFalse(launch('16.7.14')['supported']); self.assertEqual(len(calls), 1)
        self.assertEqual(list(base.iterdir()), [])

    def test_request_is_durable_before_command_but_zero_exit_is_not_foreground_proof(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if '--help' in argv: return subprocess.CompletedProcess(argv, 0, b'--bundleid <id>', b'')
            self.assertEqual(argv, ['existing-uiopen', '--bundleid', 'jp.naver.line'])
            rows = [json.loads(x) for x in (base / 'ui-actions.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['event'], 'line_launch_request')
            self.assertEqual(len(rows), 1)
            self.assertEqual(kwargs['timeout'], 10)
            return subprocess.CompletedProcess(argv, 0)
        launch, base = self.case('existing-uiopen', run)
        result = launch('16.7.14')
        self.assertTrue(result['command_completed'])
        self.assertFalse(result['foreground_verified']); self.assertFalse(result['lock_state_verified'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len((base / 'ui-actions.jsonl').read_text().splitlines()), 2)

    def test_launch_timeout_is_unknown_and_not_retried(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if '--help' in argv:return subprocess.CompletedProcess(argv, 0, b'--bundleid', b'')
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        launch, base = self.case('existing-uiopen', run)
        result = launch('16.7.14')
        self.assertEqual(result['outcome'], 'unknown_no_retry')
        self.assertEqual(len(calls), 2)
        self.assertFalse(result['foreground_verified'])


if __name__ == '__main__': unittest.main()
