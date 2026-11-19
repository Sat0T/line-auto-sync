"""Offline tests for the PC synthetic HTTPS check."""
import sys
from pathlib import Path
import unittest
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import verify_mvp_https as smoke


class HttpsProbeTests(unittest.TestCase):
    def test_wrong_token_then_identical_valid_replay_and_sanitized_redirect(self):
        calls = []
        endpoint = 'https://script.google.com' + '/macros/s/test-fixture/exec'
        token = 'synthetic-fixture-value-longer-than-32-characters'

        def exchange(url, key, body, maxid, trace):
            calls.append((url, key, body, maxid))
            request = Request(url, b'fixture', {'Content-Type': 'application/json'})
            redirected = trace.redirect_request(
                request, None, 302, '', {}, 'https://script.googleusercontent.com/fixture?private=redacted')
            self.assertIsNone(redirected.data)
            self.assertEqual(redirected.get_method(), 'GET')
            return b'OK' if key == token else b'ERROR'

        result = smoke.check(endpoint, token, exchange)
        self.assertTrue(result['passed'])
        self.assertEqual(calls[1], calls[2])
        self.assertNotEqual(calls[0][1], token)
        self.assertEqual(calls[0][2], calls[1][2])
        self.assertEqual(result['checks'][0]['redirects'], [
            {'status': 302, 'host': 'script.googleusercontent.com', 'method': 'GET'}])
        self.assertNotIn(token, str(result))
        self.assertNotIn('private=', str(result))
        self.assertFalse(smoke.check(endpoint, token, lambda *args: b'OK')['passed'])
        with self.assertRaises(ValueError):
            smoke.check('https://example.invalid/exec', token, exchange)
