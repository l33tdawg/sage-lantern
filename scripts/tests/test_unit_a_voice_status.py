import email.message
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT / 'software/runtime/src'))
from sage_lantern.session_context import SessionContext


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, SOURCE_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


server = load('latency_candidate_status', 'scripts/lantern/unit-a-status-server.py')
CONTEXT = SessionContext('a' * 32, 'b' * 64)
OTHER = SessionContext('c' * 32, 'd' * 64)
VOICE = {'phase': 'processing', 'session_context': CONTEXT.to_dict(), 'session_id': 'turn-1',
         'transcript': 'Synthetic test question', 'response': 'Synthetic streamed answer',
         'touch_ready': True, 'correction_ready': True, 'review_ready': True}


def handler(path='/voice', token=True):
    value = server.StatusHandler.__new__(server.StatusHandler)
    value.path = path
    value.headers = email.message.Message()
    value.headers['Host'] = '127.0.0.1:3199'
    if token:
        value.headers['X-Lantern-Session'] = 'synthetic-token'
    value.wfile = io.BytesIO()
    value.statuses = []
    value.sent_headers = []
    value.send_response = value.statuses.append
    value.send_error = value.statuses.append
    value.send_header = lambda name, item: value.sent_headers.append((name, item))
    value.end_headers = lambda: None
    return value


class VoiceStatusTests(unittest.TestCase):
    def run_get(self, value, *, auth=True, contexts=None, voice=None):
        with patch.object(server, '_session_authorized', side_effect=auth if isinstance(auth, list) else None,
                          return_value=auth if not isinstance(auth, list) else None), \
             patch.object(server, '_context_required', return_value=True), \
             patch.object(server, '_session_context', side_effect=contexts or [CONTEXT, CONTEXT, CONTEXT]), \
             patch.object(server, 'collect_status', side_effect=AssertionError('hot path ran full telemetry')), \
             patch.object(server, '_voice', return_value=voice if voice is not None else VOICE):
            value.do_GET()
        return value

    def test_collector_reads_content_without_any_probe_or_telemetry(self):
        with patch.object(server.Path, 'read_text', return_value=json.dumps(VOICE)), \
             patch.object(server, '_voice_correction_ready', side_effect=AssertionError('controller probe')), \
             patch.object(server, '_command', side_effect=AssertionError('subprocess')), \
             patch.object(server, 'collect_status', side_effect=AssertionError('telemetry')):
            value = server.collect_voice_status()
        self.assertEqual(value['voice']['response'], VOICE['response'])
        self.assertEqual(value['schema'], 'sage.lantern.voice-status.v1')
        self.assertNotIn('review_ready', value['voice'])
        self.assertNotIn('correction_ready', value['voice'])
        self.assertNotIn('services', value)

    def test_authorized_same_context(self):
        value = self.run_get(handler())
        self.assertEqual(value.statuses, [200])
        payload = json.loads(value.wfile.getvalue())
        self.assertEqual(payload['voice']['response'], VOICE['response'])
        self.assertEqual(payload['session_context'], CONTEXT.to_dict())
        self.assertIn(('Cache-Control', 'no-store'), value.sent_headers)

    def test_unauthorized_cannot_read(self):
        value = self.run_get(handler(), auth=False)
        self.assertEqual(value.statuses, [403])
        self.assertEqual(value.wfile.getvalue(), b'')

    def test_duplicate_session_header_rejected(self):
        value = handler()
        value.headers['X-Lantern-Session'] = 'another'
        self.run_get(value)
        self.assertEqual(value.statuses, [400])

    def test_untrusted_host_rejected(self):
        value = handler()
        value.headers.replace_header('Host', 'untrusted.example')
        self.run_get(value)
        self.assertEqual(value.statuses, [403])

    def test_missing_broker_context_rejected(self):
        value = self.run_get(handler(), contexts=[None])
        self.assertEqual(value.statuses, [403])

    def test_other_account_and_unstamped_voice_redacted(self):
        for stamp in [OTHER.to_dict(), None, {'schema': 'invalid'}]:
            with self.subTest(stamp=stamp):
                value = self.run_get(handler(), voice={**VOICE, 'session_context': stamp})
                payload = json.loads(value.wfile.getvalue())
                self.assertEqual(payload['voice']['response'], '')
                self.assertEqual(payload['voice']['transcript'], '')

    def test_revocation_before_headers_rejects(self):
        value = self.run_get(handler(), auth=[True, False])
        self.assertEqual(value.statuses, [403])
        self.assertEqual(value.wfile.getvalue(), b'')

    def test_revocation_after_headers_suppresses_content(self):
        value = self.run_get(handler(), auth=[True, True, False])
        self.assertEqual(value.statuses, [200])
        self.assertEqual(value.wfile.getvalue(), b'')
        self.assertTrue(value.close_connection)

    def test_rotation_before_headers_rejects(self):
        value = self.run_get(handler(), contexts=[CONTEXT, OTHER])
        self.assertEqual(value.statuses, [403])
        self.assertEqual(value.wfile.getvalue(), b'')

    def test_rotation_after_headers_suppresses_content(self):
        value = self.run_get(handler(), contexts=[CONTEXT, CONTEXT, OTHER])
        self.assertEqual(value.statuses, [200])
        self.assertEqual(value.wfile.getvalue(), b'')

    def test_voice_preflight_has_existing_exact_origin_header_policy(self):
        for origin, expected in [('http://127.0.0.1:3100', 204), ('https://untrusted.example', 403)]:
            value = handler()
            value.headers['Origin'] = origin
            value.headers['Access-Control-Request-Method'] = 'GET'
            value.headers['Access-Control-Request-Headers'] = 'X-Lantern-Session'
            value.do_OPTIONS()
            self.assertEqual(value.statuses, [expected])

    def test_health_stays_unprotected_without_content(self):
        value = handler('/health', token=False)
        with patch.object(server, '_session_authorized', side_effect=AssertionError('health authority change')), \
             patch.object(server, 'collect_voice_status', side_effect=AssertionError('health exposed voice')):
            value.do_GET()
        self.assertEqual(json.loads(value.wfile.getvalue()), {'ok': True})


if __name__ == '__main__':
    unittest.main()
