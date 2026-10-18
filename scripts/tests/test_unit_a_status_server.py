import importlib.util
import json
import io
import stat
import tempfile
from email.message import Message
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-status-server.py"
SPEC = importlib.util.spec_from_file_location("status_server", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VoiceProgressTests(unittest.TestCase):
    def test_progress_stage_is_allowlisted(self):
        for stage in ("starting", "transcribing", "recalling", "photo_analysis", "generating", "answering", "speaking", "untrusted-stage", None):
            with self.subTest(stage=stage), patch.object(Path, "read_text", return_value=json.dumps({"phase": "processing", "progress_stage": stage})):
                self.assertEqual(MODULE._voice()["progress_stage"], None if stage == "untrusted-stage" else stage)


class StatusCollectionTests(unittest.TestCase):
    def test_touch_detection_accepts_direct_input_and_legacy_panel_names(self):
        devices = ['/sys/class/input/event3/device', '/sys/class/input/event4/device']
        values = {
            '/sys/class/input/event3/device/name': 'Waveshare  Waveshare -079-HD',
            '/sys/class/input/event3/device/properties': '2',
            '/sys/class/input/event4/device/name': 'Logitech USB Keyboard',
            '/sys/class/input/event4/device/properties': '0',
        }
        with patch.object(MODULE, '_read', side_effect=lambda path: values.get(path, '')), \
                patch('glob.glob', return_value=devices):
            value = MODULE._touch()
        self.assertTrue(value['present'])
        self.assertEqual(value['detail'], 'Waveshare  Waveshare -079-HD')
        with patch.object(MODULE, '_read', side_effect=lambda path: {
                '/sys/class/input/event4/device/name': 'Logitech USB Keyboard',
                '/sys/class/input/event4/device/properties': '0'}.get(path, '')), \
                patch('glob.glob', return_value=['/sys/class/input/event4/device']):
            self.assertFalse(MODULE._touch()['present'])
        legacy = {'/sys/class/input/event7/device/name': 'WS170120 touch',
                  '/sys/class/input/event7/device/properties': ''}
        with patch.object(MODULE, '_read', side_effect=lambda path: legacy.get(path, '')), \
                patch('glob.glob', return_value=['/sys/class/input/event7/device']):
            self.assertTrue(MODULE._touch()['present'])

    def test_audio_presence_does_not_claim_live_readiness_or_capture(self):
        binding = {'audio': {'stable_path': '/dev/snd/by-id/test-audio', 'usb_serial': 'test-unit'}}
        for present, operational in ((False, True), (True, False), (True, True)):
            with self.subTest(present=present, operational=operational), \
                    patch.object(Path, 'exists', return_value=present), \
                    patch.object(MODULE, '_audio_operational', return_value=operational) as probe:
                value = MODULE._audio(binding)
            self.assertIs(value['ready'], present and operational)
            self.assertFalse(value['capture_verified'])
            self.assertEqual(probe.call_count, int(present))

    def test_real_sensor_collectors_work_with_no_hardware_or_voice_state(self):
        with patch('glob.glob', return_value=[]), \
                patch.object(MODULE, '_read', return_value='10.5 2.0'), \
                patch.object(MODULE, '_audio', return_value={'present': False, 'detail': 'absent'}), \
                patch.object(MODULE, '_ptt', return_value={'present': False, 'verified': False}), \
                patch.object(MODULE, '_radio', return_value={'present': False, 'detail': 'absent'}), \
                patch.object(MODULE, '_network', return_value={'present': False, 'detail': 'Offline'}), \
                patch.object(MODULE, '_service_state', return_value='inactive'), \
                patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            result = MODULE.collect_status()
        self.assertEqual(result['schema'], MODULE.SCHEMA)
        self.assertIsNone(result['temperature_millicelsius'])
        self.assertFalse(result['display']['present'])
        self.assertFalse(result['touch']['present'])
        self.assertEqual(result['voice']['phase'], 'unavailable')
        self.assertEqual(result['voice']['transcript'], '')
        self.assertEqual(result['services']['voice_loop'], 'inactive')
        json.dumps(result)


class SageTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.cache = MODULE.SageTelemetry()
        self.health = {
            "memories": {"total_memories": 0, "by_status": {}},
            "memory_projection": {"complete": True, "partial": False},
            "chain": {"block_height": "4487", "peers": "0", "mempool_txs": "0",
                      "catching_up": False, "idle": True, "stuck": False,
                      "chain_id": "private", "block_time": "2026-09-05T01:09:46.899952879Z"},
            "rest_addr": "private", "agents": ["secret"],
        }

    def poll(self, ready=None, validators=None):
        responses = {
            "/v1/dashboard/health": self.health,
            "/ready": ready if ready is not None else {"status": "ready", "cometbft": True},
            "/v1/dashboard/chain/validators": validators if validators is not None else {"count": 1},
        }
        with patch.object(MODULE, "_sage_get", side_effect=lambda path: responses[path]):
            self.cache.poll()
        return self.cache.snapshot()

    def test_healthy_zero_and_allowlist(self):
        value = self.poll()
        self.assertTrue(value["available"])
        self.assertNotIn("reason", value)
        self.assertTrue(value["fresh"])
        self.assertEqual(value["committed_memories"], 0)
        self.assertEqual(value["block_height"], 4487)
        self.assertEqual(value["chain_peers"], 0)
        self.assertNotIn("private", json.dumps(value))
        self.assertNotIn("secret", json.dumps(value))
        self.assertIsNone(value["validator_connections"])
        self.assertIsNone(value["federation_enabled"])
        self.assertIsNone(value["validator_roster_complete"])

    def test_unknown_committed_not_zero(self):
        self.health["memories"]["total_memories"] = 4
        self.assertIsNone(self.poll()["committed_memories"])

    def test_projection_missing_or_partial_not_empty(self):
        for projection in (None, {}, {"complete": False, "partial": True},
                           {"complete": True, "partial": False, "stale": True},
                           {"complete": True, "partial": False, "refreshing": True}):
            self.health["memory_projection"] = projection
            value = self.poll()
            self.assertFalse(value["available"])
            self.assertIsNone(value["total_memories"])
            self.assertIsNone(value["committed_memories"])

    def test_stats_failure_omits_memories(self):
        del self.health["memories"]
        self.assertFalse(self.poll()["available"])

    def test_numeric_validation(self):
        for value in (True, False, -1, 1.0, float("nan"), float("inf"), "-1", "1.1", "1e3", " 2", 2**53, "9007199254740992", "9" * 5000):
            with self.subTest(value=repr(value)[:40]):
                self.assertIsNone(MODULE._sage_integer(value))
        self.assertEqual(MODULE._sage_integer("9007199254740991"), 9007199254740991)

    def test_bad_chain_values(self):
        self.health["chain"] = {"block_height": "bad", "peers": True, "catching_up": "false"}
        value = self.poll()
        self.assertFalse(value["available"])
        self.assertIsNone(value["chain_peers"])
        self.assertIsNone(value["catching_up"])

    def test_degraded_readiness(self):
        self.assertFalse(self.poll(ready={"status": "degraded", "cometbft": True})["available"])

    def test_partial_roster_never_connectivity(self):
        value = self.poll(validators={"count": "100", "validators": [{"pub_key": "secret"}]})
        self.assertEqual(value["validator_count"], 100)
        self.assertEqual(value["validator_reason"], "roster_completeness_unknown")
        self.assertIsNone(value["validator_roster_complete"])

    def test_timeout_clears_previous_values(self):
        self.poll()
        with patch.object(MODULE, "_sage_get", side_effect=TimeoutError):
            self.cache.poll()
        value = self.cache.snapshot()
        self.assertFalse(value["available"])
        self.assertFalse(value["fresh"])
        self.assertIsNone(value["committed_memories"])
        self.assertIsNone(value["validator_count"])

    def test_stale_and_cache_only_reads(self):
        self.poll()
        with patch.object(MODULE, "_sage_get") as fetch:
            for _ in range(20):
                self.cache.snapshot()
            fetch.assert_not_called()
        with patch.object(MODULE.time, "monotonic", return_value=self.cache.observed_monotonic + 26):
            value = self.cache.snapshot()
        self.assertEqual(value["reason"], "stale")
        self.assertFalse(value["available"])
        self.assertIsNone(value["block_height"])
        self.assertIsNotNone(value["observed_at"])


class TransportTests(unittest.TestCase):
    def fetch(self, payload, status=200):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = status
        response.read1.side_effect = [payload, b""]
        with patch.object(MODULE.http.client, "HTTPConnection", return_value=connection) as factory:
            try:
                return MODULE._sage_get("/ready")
            finally:
                factory.assert_called_once_with("127.0.0.1", 8080, timeout=2.0)
                connection.close.assert_called_once()
                connection.request.assert_called_once_with("GET", "/ready", headers={"Accept": "application/json"})

    def test_valid(self):
        self.assertEqual(self.fetch(b'{"status":"ready"}'), {"status": "ready"})

    def test_http_errors_and_redirects(self):
        for status in (301, 302, 401, 500, 503):
            with self.assertRaises(ValueError):
                self.fetch(b"{}", status)

    def test_error_200_malformed_and_oversized(self):
        for payload in (b'{"error":"private diagnostic"}', b"not json", b"[]", b"x" * (MODULE.SAGE_MAX_BODY + 1)):
            with self.assertRaises(ValueError):
                self.fetch(payload)

    def test_other_paths_forbidden(self):
        with patch.object(MODULE.http.client, "HTTPConnection") as factory:
            with self.assertRaises(ValueError):
                MODULE._sage_get("http://external.invalid")
            factory.assert_not_called()

    def test_timeout_closes_connection(self):
        connection = MagicMock()
        connection.getresponse.side_effect = TimeoutError
        with patch.object(MODULE.http.client, "HTTPConnection", return_value=connection):
            with self.assertRaises(TimeoutError):
                MODULE._sage_get("/ready")
        connection.close.assert_called_once()


class StatusSessionTests(unittest.TestCase):
    def setUp(self):
        legacy = patch.object(MODULE, "_context_required", return_value=False)
        legacy.start()
        self.addCleanup(legacy.stop)

    def handler(self, path="/status", method="GET", headers=()):
        handler = MODULE.StatusHandler.__new__(MODULE.StatusHandler)
        handler.path = path
        handler.command = method
        handler.requestline = method + " " + path + " HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.headers = Message()
        handler.headers["Host"] = "127.0.0.1:3199"
        for name, value in headers:
            handler.headers[name] = value
        handler.wfile = io.BytesIO()
        handler.log_message = lambda *args: None
        return handler

    def test_protected_status_fences_prior_or_unstamped_voice_content(self):
        from sage_lantern.session_context import SessionContext
        current = SessionContext("a" * 32, "b" * 64)
        old = SessionContext("c" * 32, "d" * 64)
        for stamp in (None, old.to_dict(), current.to_dict()):
            handler = self.handler(headers=(("X-Lantern-Session", "synthetic"),))
            value = {"audio": {"present": False, "ready": False},
                     "voice": {"phase": "complete", "transcript": "private-canary", "response": "answer",
                               "session_context": stamp}}
            with patch.object(MODULE, "_context_required", return_value=True), \
                    patch.object(MODULE, "_session_authorized", return_value=True), \
                    patch.object(MODULE, "_session_context", return_value=current), \
                    patch.object(MODULE, "collect_status", return_value=value), patch("builtins.print"):
                handler.do_GET()
            body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)[1]
            self.assertEqual(b"private-canary" in body, stamp == current.to_dict())
            self.assertEqual(json.loads(body)["session_context"], current.to_dict())

    def test_protected_status_context_failure_or_rotation_denies(self):
        from sage_lantern.session_context import SessionContext
        current = SessionContext("a" * 32, "b" * 64)
        replacement = SessionContext("c" * 32, "d" * 64)
        for contexts in ([None], [current, replacement], [current, current, replacement]):
            handler = self.handler(headers=(("X-Lantern-Session", "synthetic"),))
            with patch.object(MODULE, "_context_required", return_value=True), \
                    patch.object(MODULE, "_session_authorized", return_value=True), \
                    patch.object(MODULE, "_session_context", side_effect=contexts), \
                    patch.object(MODULE, "collect_status", return_value={"voice": {"session_context": current.to_dict(),
                         "response": "private-canary"}}) as collect:
                handler.do_GET()
            self.assertNotIn(b"private-canary", handler.wfile.getvalue())
            self.assertEqual(collect.call_count, 0 if contexts == [None] else 1)

    def test_denied_before_collect_and_after_collect(self):
        for decisions in ([False], [True, False], [True, True, False]):
            handler = self.handler()
            with patch.object(MODULE, "_session_authorized", side_effect=decisions):
                with patch.object(MODULE, "collect_status", return_value={"voice": "private-canary"}) as collect:
                    handler.do_GET()
            self.assertNotIn(b"private-canary", handler.wfile.getvalue())
            self.assertEqual(collect.call_count, 0 if decisions == [False] else 1)

    def test_health_is_minimal_without_auth_or_collection(self):
        handler = self.handler("/health")
        with patch.object(MODULE, "_session_authorized") as auth:
            with patch.object(MODULE, "collect_status") as collect:
                handler.do_GET()
        auth.assert_not_called()
        collect.assert_not_called()
        self.assertEqual(json.loads(handler.wfile.getvalue().split(b"\r\n\r\n")[1]), {"ok": True})

    def test_readiness_diagnostic_excludes_private_payload(self):
        handler = self.handler()
        payload = {"audio": {"present": True, "ready": False}, "voice": "private-canary"}
        with patch.object(MODULE, "_session_authorized", return_value=True):
            with patch.object(MODULE, "collect_status", return_value=payload):
                with patch("builtins.print") as output:
                    handler.do_GET()
        output.assert_called_once_with(json.dumps({"event": "audio_status", "present": True, "ready": False}), flush=True)

    def test_duplicates_denied_even_when_legacy(self):
        handler = self.handler(headers=(("X-Lantern-Session", "one"), ("X-Lantern-Session", "two")))
        with patch.object(MODULE, "collect_status") as collect:
            handler.do_GET()
        collect.assert_not_called()
        self.assertIn(b"400", handler.wfile.getvalue().split(b"\r\n")[0])

    def test_preflight_exact_headers(self):
        for requested, code in (("X-Lantern-Session", b"204"), ("x-lantern-session, other", b"403"),
                                ("x-lantern-session, x-lantern-session", b"403")):
            handler = self.handler(method="OPTIONS", headers=(("Origin", "http://127.0.0.1:3100"),
                ("Access-Control-Request-Method", "GET"), ("Access-Control-Request-Headers", requested)))
            handler.do_OPTIONS()
            self.assertIn(code, handler.wfile.getvalue().split(b"\r\n")[0])

    def test_enabled_helper_errors_fail_closed(self):
        metadata = SimpleNamespace(st_mode=0o40755, st_uid=0)
        with patch.object(MODULE.Path, "lstat", return_value=metadata):
            with patch.dict("sys.modules", {"sage_lantern.device_access": None}):
                self.assertFalse(MODULE._session_authorized("secret"))

    def test_preflight_trusted_hosts_and_origins_only(self):
        headers = (("Origin", "http://localhost:3100"), ("Access-Control-Request-Method", "GET"),
                   ("Access-Control-Request-Headers", "X-Lantern-Session"))
        for host, expected in (("127.0.0.1:3199", b"204"), ("localhost:3199", b"204"),
                               ("attacker.example:3199", b"403"), ("127.0.0.1:3100", b"403")):
            handler = self.handler(method="OPTIONS", headers=headers)
            handler.headers.replace_header("Host", host)
            with patch.object(MODULE, "collect_status") as collect:
                handler.do_OPTIONS()
            collect.assert_not_called()
            self.assertIn(expected, handler.wfile.getvalue().split(b"\r\n")[0])
        for origin in ("null", "http://attacker.example", "http://localhost:31000"):
            handler = self.handler(method="OPTIONS", headers=headers)
            handler.headers.replace_header("Origin", origin)
            handler.do_OPTIONS()
            self.assertIn(b"403", handler.wfile.getvalue().split(b"\r\n")[0])

    def test_missing_duplicate_host_rejected_before_private_collection(self):
        for method in ("GET", "OPTIONS"):
            for duplicate in (False, True):
                handler = self.handler(method=method)
                if duplicate:
                    handler.headers["Host"] = "localhost:3199"
                else:
                    del handler.headers["Host"]
                with patch.object(MODULE, "collect_status") as collect:
                    getattr(handler, "do_" + method)()
                collect.assert_not_called()
                self.assertIn(b"403", handler.wfile.getvalue().split(b"\r\n")[0])


class UnitBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve() / 'bindings.json'
        self.binding = dict(schema='sage.lantern.unit-plan.v1', unit_id='unit-b',
                            audio=dict(stable_path='/dev/snd/by-id/usb-B-AUDIO', usb_serial='B-AUDIO'),
                            radio=dict(stable_path='/dev/serial/by-id/usb-B-RADIO', usb_serial='B-RADIO'))
        self.path.write_text(json.dumps(self.binding))

    def load(self, uid=0):
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=uid, st_nlink=1, st_size=self.path.stat().st_size)
        with patch.object(MODULE.os, 'fstat', return_value=info):
            return MODULE.load_unit_binding(self.path)

    def test_b_binding_and_no_format_readiness_claim(self):
        binding = self.load()
        with patch.object(Path, 'exists', return_value=True):
            audio = MODULE._audio(binding)
        self.assertTrue(audio['present'])
        self.assertFalse(audio['capture_verified'])
        self.assertEqual(audio['serial'], 'B-AUDIO')
        self.assertNotIn('16 kHz', audio['detail'])
        self.assertNotIn('ready', audio['detail'])

    def test_fleet_status_uses_explicit_unit_and_rejects_a_audio(self):
        for unit in ('unit-c', 'unit-d', 'unit-e', 'unit-f'):
            with self.subTest(unit=unit):
                binding = {**self.binding, 'unit_id': unit}
                self.path.write_text(json.dumps(binding))
                self.assertEqual(self.load()['unit_id'], unit)
                binding['audio'] = dict(stable_path='/dev/snd/by-id/usb-SAGE-LANTERN-A-AUDIO',
                    usb_serial='SAGE-LANTERN-A-AUDIO')
                self.path.write_text(json.dumps(binding))
                with self.assertRaises(ValueError):
                    self.load()
        for unit in ('unit-g', 'unit-C', 'unit-cc'):
            self.path.write_text(json.dumps({**self.binding, 'unit_id': unit}))
            with self.assertRaises(ValueError):
                self.load()

    def test_missing_binding_never_defaults_to_a(self):
        self.assertFalse(MODULE._audio()['present'])
        self.assertFalse(MODULE._radio(self.path)['present'])
        self.assertIsNone(MODULE._audio()['serial'])

    def test_absent_radio_and_platform_audio_are_explicit_not_fabricated(self):
        self.binding['radio'] = {'present': False, 'reason': 'operator-declared-absent'}
        self.binding['audio'] = {'present': True, 'source': 'platform',
                                 'stable_path': '/dev/snd/by-path/platform-es8388-sound',
                                 'id_path': 'platform-es8388-sound', 'platform_device': 'es8388-sound'}
        self.path.write_text(json.dumps(self.binding))
        binding = self.load()
        self.assertIs(binding['radio']['present'], False)
        radio = MODULE._radio(self.path, binding)
        self.assertFalse(radio['present'])
        self.assertFalse(radio['link_verified'])
        self.assertEqual(radio['detail'], 'Radio not fitted (explicitly declared absent)')
        self.assertEqual(radio['name'], 'unit-b radio')
        with patch.object(Path, 'exists', return_value=True), \
                patch.object(MODULE, '_audio_operational', return_value=False):
            audio = MODULE._audio(binding)
        self.assertTrue(audio['present'])
        self.assertFalse(audio['ready'])
        self.assertIsNone(audio['serial'])
        self.assertIn('platform capture device', audio['detail'])

    def test_platform_audio_binding_mismatches_are_refused(self):
        for audio in (
            {'present': True, 'source': 'platform', 'stable_path': '/dev/snd/by-path/platform-wrong',
             'id_path': 'platform-es8388-sound', 'platform_device': 'es8388-sound'},
            {'present': True, 'source': 'platform', 'stable_path': '/dev/snd/by-path/platform-es8388-sound',
             'id_path': 'platform-es8388-sound', 'platform_device': 'hdmi0-sound'},
            {'present': True, 'source': 'platform', 'stable_path': '/dev/snd/by-id/platform-es8388-sound',
             'id_path': 'platform-es8388-sound', 'platform_device': 'es8388-sound'},
            {'present': True, 'source': 'platform', 'stable_path': '/dev/snd/by-path/platform-es8388-sound',
             'id_path': 'usb-es8388-sound', 'platform_device': 'es8388-sound'},
        ):
            with self.subTest(audio=audio):
                self.binding['audio'] = audio
                self.path.write_text(json.dumps(self.binding))
                with self.assertRaises(ValueError):
                    self.load()

    def test_wrong_owner_duplicate_and_a_audio_rejected(self):
        with self.assertRaises(ValueError):
            self.load(uid=123)
        self.path.write_text('{"unit_id":"unit-b","unit_id":"unit-a"}')
        with self.assertRaises(ValueError):
            self.load()
        self.binding['audio'] = dict(stable_path='/dev/snd/by-id/usb-SAGE-LANTERN-A-AUDIO', usb_serial='SAGE-LANTERN-A-AUDIO')
        self.path.write_text(json.dumps(self.binding))
        with self.assertRaises(ValueError):
            self.load()

    def test_status_radio_presence_never_proves_link(self):
        self.path.write_text(json.dumps(dict(unit_id='unit-a', firmware=dict(version='wrong unit'))))
        with patch.object(Path, 'exists', return_value=True):
            radio = MODULE._radio(self.path, self.binding)
        self.assertTrue(radio['present'])
        self.assertFalse(radio['link_verified'])
        self.assertEqual(radio['name'], 'unit-b radio')
        self.assertNotIn('firmware', radio)


if __name__ == "__main__":
    unittest.main()
def test_local_unit_reports_audio_from_admission_not_the_absent_broker(tmp_path, monkeypatch):
    """No mesh runtime means no broker to ask; the durable admission answers."""
    import sys, types

    calls = {'admission': 0, 'broker': 0}

    def admission_verified():
        calls['admission'] += 1
        return True

    def operational_ready():
        calls['broker'] += 1
        return False

    module = types.ModuleType('sage_lantern.audio_readiness')
    module.admission_verified = admission_verified
    module.operational_ready = operational_ready
    monkeypatch.setitem(sys.modules, 'sage_lantern.audio_readiness', module)
    monkeypatch.setattr(MODULE, 'MESH_RUNTIME', tmp_path / 'mesh-runtime.json')
    assert MODULE._audio_operational() is True
    assert calls == {'admission': 1, 'broker': 0}
    # A mesh-managed unit keeps the broker as the authority.
    (tmp_path / 'mesh-runtime.json').write_text('{}\n')
    assert MODULE._audio_operational() is False
    assert calls == {'admission': 1, 'broker': 1}


def test_audio_readiness_failure_is_not_reported_as_ready(tmp_path, monkeypatch):
    import sys, types

    module = types.ModuleType('sage_lantern.audio_readiness')
    module.admission_verified = lambda: False
    module.operational_ready = lambda: False
    monkeypatch.setitem(sys.modules, 'sage_lantern.audio_readiness', module)
    monkeypatch.setattr(MODULE, 'MESH_RUNTIME', tmp_path / 'absent.json')
    assert MODULE._audio_operational() is False
