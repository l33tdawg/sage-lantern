import argparse
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("voice_control", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlTests(unittest.TestCase):
    def setUp(self):
        lock_policy = patch.object(MODULE, "lock_required", return_value=False)
        lock_policy.start()
        self.addCleanup(lock_policy.stop)
        self.now = 0
        clock = patch.object(MODULE.time, "monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.control = MODULE.VoiceControl(30)

    def start(self):
        request_id = str(uuid.uuid4())
        status, response = self.control.start(request_id)
        self.assertEqual(status, 201)
        return request_id, response

    def command(self, action, response):
        return self.control.command(action, response["session_id"], response["token"])

    def test_duplicate_start_is_same_session_without_renewing_lease(self):
        request_id, response = self.start()
        self.now = 3
        self.assertEqual(self.control.start(request_id), (200, response))
        self.now = 4
        with self.assertRaises(MODULE.ControlError) as caught:
            self.control.start(request_id)
        self.assertEqual(caught.exception.code, "stale_request")

    def test_expired_disconnected_touch_cannot_stop_or_process(self):
        _, response = self.start()
        self.now = 4
        session = self.control.take(False)
        self.assertEqual(self.control.action(session["session_id"]), "cancel")
        with self.assertRaises(MODULE.ControlError):
            self.command("stop", response)
        self.assertFalse(self.control.begin_processing(session["session_id"]))

    def test_keepalive_cannot_extend_hard_duration(self):
        _, response = self.start()
        for instant in range(1, 30):
            self.now = instant
            self.command("keepalive", response)
        self.now = 30
        with self.assertRaises(MODULE.ControlError):
            self.command("stop", response)
        self.assertEqual(self.control.action(response["session_id"]), "cancel")

    def test_stop_commits_processing_and_retains_owner(self):
        _, response = self.start()
        self.control.take(False)
        self.command("stop", response)
        self.now = 10
        self.assertTrue(self.control.begin_processing(response["session_id"]))
        self.assertTrue(self.control.busy())
        with self.assertRaises(MODULE.ControlError) as caught:
            self.start()
        self.assertEqual(caught.exception.code, "voice_busy")
        with self.assertRaises(MODULE.ControlError) as caught:
            self.command("cancel", response)
        self.assertEqual(caught.exception.code, "processing_busy")
        self.assertIsNone(self.control.take(True))

    def test_cancel_wins_before_processing_and_is_idempotent(self):
        _, response = self.start()
        self.control.take(False)
        self.command("stop", response)
        self.command("cancel", response)
        self.command("cancel", response)
        self.assertFalse(self.control.begin_processing(response["session_id"]))

    def test_stale_request_and_token_never_affect_new_session(self):
        request_id, old = self.start()
        self.control.finish(old["session_id"])
        with self.assertRaises(MODULE.ControlError):
            self.control.start(request_id)
        _, current = self.start()
        for action in ("stop", "cancel", "keepalive"):
            with self.assertRaises(MODULE.ControlError):
                self.command(action, old)
            with self.assertRaises(MODULE.ControlError):
                self.control.command(action, current["session_id"], old["token"])
        self.control.finish(old["session_id"])
        self.assertTrue(self.control.busy())
        self.assertIsNone(self.control.action(current["session_id"]))

    def test_bounded_replay_tombstones(self):
        for _ in range(MODULE.MAX_REQUESTS + 4):
            _, response = self.start()
            self.control.finish(response["session_id"])
        self.assertEqual(len(self.control.requests), MODULE.MAX_REQUESTS)
        self.assertTrue(all(isinstance(value, str) for value in self.control.requests.values()))

    def test_touch_and_physical_share_exclusive_owner(self):
        _, response = self.start()
        session = self.control.take(True)
        self.assertEqual(session, {"session_id": response["session_id"], "source": "touch"})
        self.assertIsNone(self.control.take(True))
        self.control.finish(session["session_id"])
        physical = self.control.take(True)
        self.assertEqual(physical["source"], "physical")
        with self.assertRaises(MODULE.ControlError):
            self.start()


class LoopTests(unittest.TestCase):
    def setUp(self):
        lock_policy = patch.object(MODULE, "lock_required", return_value=False)
        lock_policy.start()
        self.addCleanup(lock_policy.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.loop = MODULE.VoiceLoop(argparse.Namespace(
            max_seconds=30, audio_dir=self.root / "audio", state=self.root / "state.json",
            ptt_state=self.root / "missing-ptt.json",
        ))
        self.loop.write_state = Mock()
        self.loop.process = Mock()
        self.loop.capture = Mock(return_value=True)

    def reserve(self):
        return self.loop.control.start(str(uuid.uuid4()))[1]

    def test_touch_works_without_ptt_and_cleans_audio_after_stop(self):
        credentials = self.reserve()

        def capture(session_id, path):
            path.write_bytes(b"private audio")
            self.loop.control.command("stop", session_id, credentials["token"])
            return True

        self.loop.capture.side_effect = capture
        self.assertEqual(self.loop.ptt(), "unknown")
        self.loop.step()
        self.loop.capture.assert_called_once()
        self.loop.process.assert_called_once()
        self.assertFalse(list(self.loop.args.audio_dir.iterdir()))
        self.assertFalse(self.loop.control.busy())
        self.assertFalse(self.loop.args.ptt_state.exists())

    def test_queued_cancel_or_lease_expiry_never_opens_microphone(self):
        for cancel in (True, False):
            with patch.object(MODULE.time, "monotonic", return_value=0):
                credentials = self.reserve()
                if cancel:
                    self.loop.control.command("cancel", credentials["session_id"], credentials["token"])
            with patch.object(MODULE.time, "monotonic", return_value=5):
                self.loop.step()
            self.assertFalse(self.loop.control.busy())
        self.loop.capture.assert_not_called()
        self.loop.process.assert_not_called()

    def test_cancel_expiry_capture_error_and_processing_error_delete_audio(self):
        for failure in ("cancel", "expiry", "capture_error", "processing_error"):
            with self.subTest(failure=failure):
                self.loop.process.reset_mock(side_effect=True)
                with patch.object(MODULE.time, "monotonic", return_value=0):
                    credentials = self.reserve()

                    def capture(session_id, path):
                        path.write_bytes(b"private audio")
                        if failure == "capture_error":
                            raise RuntimeError("recorder failed")
                        if failure == "expiry":
                            with patch.object(MODULE.time, "monotonic", return_value=5):
                                self.loop.control.action(session_id)
                        else:
                            self.loop.control.command("cancel" if failure == "cancel" else "stop", session_id, credentials["token"])
                        return True

                    self.loop.capture.side_effect = capture
                    if failure == "processing_error":
                        self.loop.process.side_effect = RuntimeError("inference failed")
                    self.loop.step()
                self.assertFalse(list(self.loop.args.audio_dir.iterdir()))
                self.assertFalse(self.loop.control.busy())
                if failure != "processing_error":
                    self.loop.process.assert_not_called()

    def test_simultaneous_gpio_press_does_not_start_second_recorder(self):
        self.reserve()
        self.loop.ptt = Mock(return_value="down")
        self.loop.step()
        self.loop.step()
        self.loop.capture.assert_called_once()
        self.assertEqual(self.loop.last_ptt, "down")

    def test_state_advertises_source_but_never_token(self):
        self.loop.write_state = MODULE.VoiceLoop.write_state.__get__(self.loop)
        credentials = self.reserve()
        self.loop.touch_ready = True
        self.loop.input_source = "touch"
        self.loop.write_state("listening", session_id=credentials["session_id"])
        raw = self.loop.args.state.read_text()
        state = json.loads(raw)
        self.assertTrue(state["touch_ready"])
        self.assertEqual(state["input_source"], "touch")
        self.assertNotIn(credentials["token"], raw)
        self.loop.write_state("idle")
        self.assertNotIn("input_source", json.loads(self.loop.args.state.read_text()))

    def test_bind_failure_fails_service_without_advertising_ready(self):
        with patch.object(MODULE, "VoiceHTTPServer", side_effect=OSError("address in use")):
            with self.assertRaises(OSError):
                self.loop.run()
        self.assertFalse(self.loop.touch_ready)
        self.loop.capture.assert_not_called()
        self.assertEqual(self.loop.write_state.call_args.args[0], "error")


class HttpTests(unittest.TestCase):
    def setUp(self):
        lock_policy = patch.object(MODULE, "lock_required", return_value=False)
        lock_policy.start()
        self.addCleanup(lock_policy.stop)
        self.control = MODULE.VoiceControl(30)

    def request(self, path="/start", payload=None, *, method="POST", changes=None, duplicate=None, raw=None):
        if payload is None:
            payload = {"request_id": str(uuid.uuid4())}
        body = json.dumps(payload).encode() if raw is None else raw
        values = {
            "Host": "127.0.0.1:3197", "Origin": "http://127.0.0.1:3100",
            "X-Lantern-Voice": "control", "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        values.update(changes or {})
        handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
        handler.headers = Message()
        for name, value in values.items():
            if value is not None:
                handler.headers[name] = value
        if duplicate:
            handler.headers[duplicate] = values[duplicate]
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.server = SimpleNamespace(control=self.control)
        getattr(handler, f"do_{method}")()
        headers, response = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        lines = headers.decode().split("\r\n")
        response_headers = dict(line.split(": ", 1) for line in lines[1:])
        self.assertEqual(response_headers["Cache-Control"], "no-store")
        self.assertEqual(response_headers["Connection"], "close")
        return int(lines[0].split()[1]), json.loads(response), response_headers

    def test_start_duplicate_keepalive_stop_cancel(self):
        payload = {"request_id": str(uuid.uuid4())}
        status, response, _ = self.request(payload=payload)
        self.assertEqual(status, 201)
        self.assertEqual(self.request(payload=payload)[:2], (200, response))
        credentials = {key: response[key] for key in ("session_id", "token")}
        for path in ("/keepalive", "/stop", "/cancel"):
            self.assertEqual(self.request(path, credentials)[0], 200)

    def test_origin_host_headers_and_lengths_fail_closed(self):
        cases = [
            ("Origin", None), ("Origin", "null"), ("Origin", "https://evil.example"),
            ("Origin", "http://localhost:3101"), ("Host", "0.0.0.0:3197"),
            ("Host", "127.0.0.1:3198"), ("Host", None),
            ("X-Lantern-Voice", None), ("X-Lantern-Voice", "start"),
            ("Content-Type", "text/plain"), ("Content-Type", None),
            ("Content-Length", None), ("Content-Length", "0"),
            ("Content-Length", "513"), ("Content-Length", "-1"),
            ("Content-Length", "bad"), ("Content-Length", "512"),
            ("Transfer-Encoding", "chunked"), ("Transfer-Encoding", ""),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                status, _, headers = self.request(changes={name: value})
                self.assertIn(status, (400, 403))
                self.assertNotEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertFalse(self.control.busy())
        for name in ("Origin", "Host", "Content-Length", "Content-Type", "X-Lantern-Voice"):
            self.assertIn(self.request(duplicate=name)[0], (400, 403))

    def test_bad_payloads_unknown_paths_never_reserve(self):
        for raw in (b"[]", b"null", b"{}", b"bad json", b'{}\xff', b'{"request_id":42}', b'{"request_id":"not-uuid"}'):
            self.assertEqual(self.request(raw=raw)[0], 400)
        self.assertEqual(self.request("/start?anything=1")[0], 404)
        self.assertFalse(self.control.busy())
        self.assertEqual(self.request("/cancel", {"session_id": "x", "token": "\u2603"})[0], 400)

    def test_duplicate_json_keys_rejected_without_changing_owner(self):
        request_id = str(uuid.uuid4())
        duplicate_start = ('{"request_id":"%s","request_id":"%s"}' % (request_id, request_id)).encode()
        self.assertEqual(self.request(raw=duplicate_start)[0], 400)
        self.assertFalse(self.control.busy())
        _, credentials, _ = self.request(payload={"request_id": request_id})
        for field in ("session_id", "token"):
            raw = ('{"session_id":"%s","token":"%s","%s":"%s"}' % (
                credentials["session_id"], credentials["token"], field, credentials[field],
            )).encode()
            for path in ("/keepalive", "/stop", "/cancel"):
                self.assertEqual(self.request(path, raw=raw)[0], 400)
                self.assertIsNone(self.control.action(credentials["session_id"]))

    def test_health_is_metadata_only_and_get_never_starts(self):
        changes = {"Content-Length": "0", "Origin": None}
        status, response, _ = self.request("/health", method="GET", changes=changes)
        self.assertEqual((status, response), (200, {"touch_ready": True, "busy": False, "photo_ready": False, "correction_ready": False, "review_ready": False}))
        self.assertEqual(self.request("/start", method="GET", changes=changes)[0], 404)
        self.assertFalse(self.control.busy())

    def test_preflight_requires_exact_origin_method_and_header_set(self):
        headers = {
            "Content-Length": "0", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, x-lantern-voice",
        }
        for path in ("/start", "/stop", "/cancel", "/keepalive"):
            status, _, response = self.request(path, method="OPTIONS", changes=headers)
            self.assertEqual(status, 200)
            self.assertEqual(response["Access-Control-Allow-Headers"], "Content-Type, X-Lantern-Voice")
        for name, value in (("Origin", "null"), ("Access-Control-Request-Method", "GET"), ("Access-Control-Request-Headers", "content-type, x-lantern-voice, authorization")):
            self.assertEqual(self.request(method="OPTIONS", changes={**headers, name: value})[0], 403)
        self.assertFalse(self.control.busy())

    def test_listener_and_service_remain_local_without_ptt_requirement(self):
        with self.assertRaises(ValueError):
            MODULE.VoiceHTTPServer(("0.0.0.0", 3197), self.control)
        service = (SCRIPT.parents[2] / "software/systemd/sage-lantern-voice-loop.service").read_text()
        self.assertIn("Wants=sage-lantern-ptt.service", service)
        self.assertNotIn("Requires=sage-lantern-ptt.service", service)
        self.assertNotIn("ConditionPathExists=/var/lib/sage-lantern/PTT_VERIFIED", service)
        self.assertIn("ConditionPathExists=/var/lib/sage-lantern/LOCAL_VOICE_READY", service)
        self.assertIn("ConditionPathExists=!/run/sage-lantern-sleep.fenced", service)
        self.assertIn("SupplementaryGroups=audio", service)


if __name__ == "__main__":
    unittest.main()
