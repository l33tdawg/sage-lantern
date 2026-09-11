import importlib.util
from contextlib import contextmanager
import io
import json
from email.message import Message
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-camera-server.py"
SPEC = importlib.util.spec_from_file_location("unit_a_camera_server", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
JPEG = b"\xff\xd8preview\xff\xd9"
HEADERS = {
    "Host": "127.0.0.1:3198",
    "Origin": "http://127.0.0.1:3100",
    "X-Lantern-Camera": "capture",
    "Content-Length": "0",
}


class RequestTests(unittest.TestCase):
    def setUp(self):
        authorization = patch.object(MODULE, "_session_authorized", return_value=True)
        authorization.start()
        self.addCleanup(authorization.stop)
        self.camera = Mock()
        self.camera.device.exists.return_value = True
        self.camera.capture.return_value = JPEG

    def request(self, method="POST", path="/capture", headers=None, duplicates=(), writer=None):
        handler = MODULE.CameraHandler.__new__(MODULE.CameraHandler)
        handler.headers = Message()
        for name, value in (HEADERS if headers is None else headers).items():
            handler.headers[name] = value
        for name, value in duplicates:
            handler.headers[name] = value
        handler.server = SimpleNamespace(camera=self.camera)
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.connection = Mock()
        handler.wfile = writer if writer is not None else io.BytesIO()
        getattr(handler, f"do_{method}")()
        header_block, body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        lines = header_block.decode().split("\r\n")
        response_headers = dict(line.split(": ", 1) for line in lines[1:])
        self.assertTrue(handler.close_connection)
        self.assertEqual(response_headers["Cache-Control"], "no-store")
        if response_headers["Content-Type"] == "application/x-lantern-jpeg-stream":
            self.assertNotIn("Content-Length", response_headers)
            handler.connection.settimeout.assert_called_once_with(1)
        else:
            self.assertEqual(int(response_headers["Content-Length"]), len(body))
        return int(lines[0].split()[1]), response_headers, body

    def test_capture_exact_origins_and_hosts(self):
        for origin in MODULE.ORIGINS:
            for host in MODULE.HOSTS:
                with self.subTest(origin=origin, host=host):
                    status, headers, body = self.request(headers={**HEADERS, "Origin": origin, "Host": host})
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["Content-Type"], "image/jpeg")
                    self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
                    self.assertEqual(body, JPEG)

    def test_untrusted_requests_never_capture(self):
        cases = [
            ("Origin", None), ("Origin", "null"), ("Origin", "https://evil.example"),
            ("Origin", "http://127.0.0.1:3101"), ("Origin", "http://localhost:3100/"),
            ("Host", None), ("Host", "192.168.1.2:3198"),
            ("Host", "127.0.0.1:3199"), ("Host", "localhost.evil:3198"),
            ("Host", "127.0.0.1"), ("X-Lantern-Camera", None),
            ("X-Lantern-Camera", "other"), ("Content-Length", None),
            ("Content-Length", "1"), ("Content-Length", "-1"),
            ("Content-Length", "00"), ("Content-Length", "bad"),
            ("Transfer-Encoding", "chunked"), ("Transfer-Encoding", ""),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                headers = dict(HEADERS)
                if value is None:
                    headers.pop(name, None)
                else:
                    headers[name] = value
                status, response_headers, _ = self.request(headers=headers)
                self.assertIn(status, (400, 403))
                self.assertNotEqual(response_headers.get("Access-Control-Allow-Origin"), "*")
                if name == "Origin":
                    self.assertNotIn("Access-Control-Allow-Origin", response_headers)
        self.camera.capture.assert_not_called()

    def test_duplicate_security_headers_rejected(self):
        for name in HEADERS:
            with self.subTest(name=name):
                status, _, _ = self.request(duplicates=[(name, HEADERS[name])])
                self.assertIn(status, (400, 403))
        self.camera.capture.assert_not_called()

    def test_get_health_only_metadata_and_never_capture(self):
        status, _, body = self.request("GET", "/health", {"Host": "127.0.0.1:3198"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"camera_present": True, "vision_ready": False})
        self.assertEqual(self.request("GET", "/capture")[0], 404)
        self.assertEqual(self.request("GET", "/health?capture=1")[0], 404)
        self.assertEqual(self.request("POST", "/health")[0], 404)
        self.assertEqual(self.request("POST", "/capture?device=/dev/video0")[0], 404)
        self.camera.capture.assert_not_called()

    def test_preflight_exact_method_origin_and_headers(self):
        headers = {
            "Host": "127.0.0.1:3198", "Origin": "http://localhost:3100",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-lantern-camera",
        }
        status, response, _ = self.request("OPTIONS", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(response["Access-Control-Allow-Methods"], "POST")
        self.assertEqual(response["Access-Control-Allow-Headers"], "X-Lantern-Camera")
        for name, value in (
            ("Origin", "null"), ("Origin", ""), ("Host", "evil:3198"),
            ("Access-Control-Request-Method", "GET"),
            ("Access-Control-Request-Headers", ""),
            ("Access-Control-Request-Headers", "x-lantern-camera, authorization"),
            ("Transfer-Encoding", "chunked"), ("Content-Length", "1"),
        ):
            with self.subTest(name=name, value=value):
                status, response, _ = self.request("OPTIONS", headers={**headers, name: value})
                self.assertIn(status, (400, 403))
                self.assertNotIn("Access-Control-Allow-Methods", response)
        self.camera.capture.assert_not_called()

    def test_capture_errors_are_bounded_safe_responses(self):
        for status, code in ((409, "camera_busy"), (409, "voice_active"), (504, "capture_timeout"), (502, "invalid_jpeg")):
            self.camera.capture.side_effect = MODULE.CaptureError(status, code)
            actual, _, body = self.request()
            self.assertEqual(actual, status)
            self.assertEqual(json.loads(body), {"error": code})

    def test_preview_security_and_preflight(self):
        for name, value in (("Origin", "null"), ("Origin", None), ("Host", "evil:3198"), ("X-Lantern-Camera", "capture"), ("Content-Length", "1"), ("Content-Length", None), ("Transfer-Encoding", "chunked")):
            headers = {**HEADERS, "X-Lantern-Camera": "preview"}
            if value is None:
                headers.pop(name)
            else:
                headers[name] = value
            self.assertIn(self.request(path="/preview", headers=headers)[0], (400, 403))
        self.assertEqual(self.request("GET", "/preview")[0], 404)
        self.camera.preview.assert_not_called()
        headers = {
            "Host": HEADERS["Host"], "Origin": HEADERS["Origin"],
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-lantern-camera",
        }
        self.assertEqual(self.request("OPTIONS", "/preview", headers)[0], 200)

    def test_preview_frames_and_disconnect_cleanup(self):
        for disconnect in (False, True):
            cleaned = Mock()
            stream = SimpleNamespace(expires=float("inf"), frame=Mock(side_effect=[JPEG, JPEG, MODULE.CaptureError(504, "preview_timeout")]))

            @contextmanager
            def preview():
                try:
                    yield stream
                finally:
                    cleaned()

            class Writer(io.BytesIO):
                def write(self, data):
                    if disconnect and data.startswith(struct.pack("!I", len(JPEG))):
                        raise BrokenPipeError()
                    return super().write(data)

            self.camera.preview.side_effect = preview
            status, _, body = self.request(path="/preview", headers={**HEADERS, "X-Lantern-Camera": "preview"}, writer=Writer())
            self.assertEqual(status, 200)
            self.assertEqual(body, b"" if disconnect else (struct.pack("!I", len(JPEG)) + JPEG) * 2)
            cleaned.assert_called_once()

    def test_preview_first_frame_failure_is_not_200(self):
        @contextmanager
        def preview():
            yield SimpleNamespace(frame=Mock(side_effect=MODULE.CaptureError(504, "preview_timeout")))

        self.camera.preview.side_effect = preview
        status, headers, _ = self.request(path="/preview", headers={**HEADERS, "X-Lantern-Camera": "preview"})
        self.assertEqual(status, 504)
        self.assertEqual(headers["Content-Type"], "application/json")


class SessionRequestTests(RequestTests):
    def test_session_denied_direct_ports(self):
        with patch.object(MODULE, "_session_authorized", return_value=False):
            for path, action in (("/capture", "capture"), ("/preview", "preview")):
                self.assertEqual(self.request(path=path, headers={**HEADERS, "X-Lantern-Camera": action})[0], 403)
        self.camera.capture.assert_not_called()
        self.camera.preview.assert_not_called()

    def test_capture_relocks_before_response(self):
        allowed = [True]

        def capture():
            allowed[0] = False
            return JPEG

        self.camera.capture.side_effect = capture
        with patch.object(MODULE, "_session_authorized", side_effect=lambda token: allowed[0]):
            status, _, body = self.request(headers={**HEADERS, "X-Lantern-Session": "token"})
        self.assertEqual(status, 403)
        self.assertNotIn(JPEG, body)

    def test_preview_relocks_between_frames_and_closes_source(self):
        allowed = [True]
        cleaned = Mock()
        calls = [0]

        def frame():
            calls[0] += 1
            if calls[0] == 2:
                allowed[0] = False
            return JPEG

        @contextmanager
        def preview():
            try:
                yield SimpleNamespace(expires=float("inf"), frame=frame)
            finally:
                cleaned()

        self.camera.preview.side_effect = preview
        with patch.object(MODULE, "_session_authorized", side_effect=lambda token: allowed[0]):
            status, _, body = self.request(path="/preview", headers={**HEADERS, "X-Lantern-Camera": "preview", "X-Lantern-Session": "token"})
        self.assertEqual(status, 200)
        self.assertEqual(body, struct.pack("!I", len(JPEG)) + JPEG)
        cleaned.assert_called_once()

    def test_duplicate_session_and_extended_preflight(self):
        self.assertEqual(self.request(duplicates=(("X-Lantern-Session", "one"), ("X-Lantern-Session", "two")))[0], 400)
        headers = {"Host": "127.0.0.1:3198", "Origin": "http://127.0.0.1:3100",
                   "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "x-lantern-session, x-lantern-camera"}
        status, response, _ = self.request("OPTIONS", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn("X-Lantern-Session", response["Access-Control-Allow-Headers"])


class CameraAccessHelperTests(unittest.TestCase):
    def test_safe_absence_is_legacy_but_marker_errors_deny(self):
        for allowed in (True, False):
            helper = SimpleNamespace(authorized=Mock(return_value=allowed))
            with patch.dict("sys.modules", {"sage_lantern.device_access": helper}):
                with patch.object(MODULE.Path, "lstat", side_effect=AssertionError("use helper marker policy")):
                    self.assertIs(MODULE._session_authorized(None), allowed)
            helper.authorized.assert_called_once_with(None)

    def test_enabled_import_failure_and_authorizer_exception_deny(self):
        metadata = SimpleNamespace(st_mode=0o40755, st_uid=0)
        with patch.object(MODULE.Path, "lstat", return_value=metadata):
            with patch.dict("sys.modules", {"sage_lantern.device_access": None}):
                self.assertFalse(MODULE._session_authorized("token"))
            helper = SimpleNamespace(authorized=Mock(side_effect=RuntimeError("private")))
            with patch.dict("sys.modules", {"sage_lantern.device_access": helper}):
                self.assertFalse(MODULE._session_authorized("token"))
                helper.authorized.assert_called_once_with("token")


class CaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.device = self.root / "camera"
        self.device.touch()
        self.voice = self.root / "voice.json"
        self.camera = MODULE.Camera(self.device, self.voice)
        self.outputs = []

    def runner(self, payload=JPEG, error=None):
        def run(arguments, **kwargs):
            self.assertEqual(arguments[:6], [
                "/usr/bin/v4l2-ctl", f"--device={self.device}",
                "--set-fmt-video=width=1280,height=720,pixelformat=MJPG",
                "--stream-mmap=3", "--stream-skip=15", "--stream-count=1",
            ])
            self.assertEqual(len(arguments), 7)
            self.assertEqual(kwargs, {"check": True, "timeout": 15, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL})
            output = Path(arguments[-1].split("=", 1)[1])
            self.outputs.append(output)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
            output.write_bytes(payload)
            if error:
                raise error
        return run

    def assert_clean(self):
        for output in self.outputs:
            self.assertFalse(output.exists())
            self.assertFalse(output.parent.exists())
        self.assertFalse(self.camera.lock.locked())

    def test_success_and_cleanup(self):
        with patch.object(MODULE.subprocess, "run", side_effect=self.runner()) as run:
            self.assertEqual(self.camera.capture(), JPEG)
            run.assert_called_once()
        self.assert_clean()

    def test_malformed_oversized_timeout_and_process_failure_cleanup(self):
        cases = [
            (b"", None, 502), (b"not jpeg", None, 502),
            (b"\xff\xd8missing end", None, 502),
            (b"missing start\xff\xd9", None, 502),
            (b"\xff\xd8" + b"x" * MODULE.MAX_JPEG_BYTES + b"\xff\xd9", None, 502),
            (JPEG, subprocess.TimeoutExpired("v4l2-ctl", 15), 504),
            (JPEG, subprocess.CalledProcessError(1, "v4l2-ctl"), 503),
            (JPEG, FileNotFoundError(), 503),
        ]
        for payload, error, status in cases:
            with self.subTest(error=type(error).__name__, size=len(payload)):
                with patch.object(MODULE.subprocess, "run", side_effect=self.runner(payload, error)):
                    with self.assertRaises(MODULE.CaptureError) as caught:
                        self.camera.capture()
                self.assertEqual(caught.exception.status, status)
                self.assert_clean()

    def test_busy_and_voice_activity_do_not_capture(self):
        with patch.object(MODULE.subprocess, "run") as run:
            self.camera.lock.acquire()
            try:
                with self.assertRaises(MODULE.CaptureError) as caught:
                    self.camera.capture()
                self.assertEqual(caught.exception.code, "camera_busy")
            finally:
                self.camera.lock.release()
            for phase in ("listening", "processing"):
                self.voice.write_text(json.dumps({"phase": phase}))
                with self.assertRaises(MODULE.CaptureError) as caught:
                    self.camera.capture()
                self.assertEqual(caught.exception.code, "voice_active")
                self.assert_clean()
            run.assert_not_called()

    def test_voice_guard_skips_missing_malformed_and_inaccessible_state(self):
        self.assertFalse(self.camera.voice_active())
        for state in ("bad json", "[]", '{"phase":"idle"}', "x" * 65537):
            self.voice.write_text(state)
            self.assertFalse(self.camera.voice_active())
        with patch.object(Path, "open", side_effect=PermissionError()):
            self.assertFalse(self.camera.voice_active())

    def test_missing_camera_does_not_run_subprocess(self):
        self.device.unlink()
        with patch.object(MODULE.subprocess, "run") as run:
            with self.assertRaises(MODULE.CaptureError) as caught:
                self.camera.capture()
            self.assertEqual(caught.exception.code, "camera_unavailable")
            run.assert_not_called()
        self.assert_clean()

    def test_public_and_alternate_binds_rejected_without_socket(self):
        for address in (("0.0.0.0", 3198), ("127.0.0.1", 3100), ("::", 3198)):
            with self.assertRaises(ValueError):
                MODULE.CameraServer(address, self.camera)

    def test_service_is_separate_and_least_privileged(self):
        service = SCRIPT.parents[2] / "software/systemd/sage-lantern-camera.service"
        text = service.read_text()
        for setting in ("DynamicUser=yes", "SupplementaryGroups=video", "PrivateTmp=true", "ProtectHome=true", "ProtectSystem=strict", "IPAddressDeny=any", "SocketBindAllow=ipv4:tcp:3198"):
            self.assertIn(setting, text)
        self.assertNotIn("SupplementaryGroups=audio", text)
        self.assertNotIn("sage-lantern-voice-loop.service", text)

    def test_preview_process_cleanup_and_shared_lock(self):
        for failure in (None, BrokenPipeError(), MODULE.CaptureError(504, "preview_timeout"), MODULE.CaptureError(502, "invalid_jpeg")):
            process = Mock()
            process.poll.return_value = None
            selector = MagicMock()
            operations = Mock()
            with patch.object(MODULE.subprocess, "run") as configure, patch.object(MODULE.subprocess, "Popen", return_value=process) as popen, patch.object(MODULE.selectors, "DefaultSelector", return_value=selector), patch.object(MODULE.tempfile, "TemporaryDirectory") as files:
                operations.attach_mock(configure, "configure")
                operations.attach_mock(popen, "stream")
                configure.side_effect = lambda *args, **kwargs: self.assertTrue(self.camera.lock.locked())
                try:
                    with self.camera.preview():
                        self.assertTrue(self.camera.lock.locked())
                        with self.assertRaises(MODULE.CaptureError) as caught:
                            self.camera.capture()
                        self.assertEqual(caught.exception.code, "camera_busy")
                        if failure:
                            raise failure
                except MODULE.CaptureError:
                    self.assertIsNotNone(failure)
                arguments = popen.call_args.args[0]
                configure.assert_called_once_with(
                    ["/usr/bin/v4l2-ctl", f"--device={self.device}", "--set-fmt-video=width=1280,height=720,pixelformat=MJPG", "--set-parm=15"],
                    check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self.assertEqual([entry[0] for entry in operations.mock_calls[:2]], ["configure", "stream"])
                self.assertEqual(arguments, ["/usr/bin/v4l2-ctl", f"--device={self.device}", "--stream-mmap=3", "--stream-count=0", "--stream-to=-"])
                self.assertEqual(popen.call_args.kwargs, {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "bufsize": 0})
                files.assert_not_called()
                process.terminate.assert_called_once()
                process.wait.assert_called_once_with(timeout=1)
                process.stdout.close.assert_called_once()
                self.assertFalse(self.camera.lock.locked())

    def test_preview_kills_unresponsive_process_and_releases_lock(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("camera", 1), None]
        with patch.object(MODULE.subprocess, "run"), patch.object(MODULE.subprocess, "Popen", return_value=process), patch.object(MODULE.selectors, "DefaultSelector", return_value=MagicMock()):
            with self.camera.preview():
                pass
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertFalse(self.camera.lock.locked())

    def test_preview_configuration_failure_never_starts_stream(self):
        for error, status in (
            (subprocess.TimeoutExpired("v4l2-ctl", 5), 504),
            (subprocess.CalledProcessError(1, "v4l2-ctl"), 503),
            (FileNotFoundError(), 503),
        ):
            with self.subTest(error=type(error).__name__):
                with patch.object(MODULE.subprocess, "run", side_effect=error), patch.object(MODULE.subprocess, "Popen") as popen:
                    with self.assertRaises(MODULE.CaptureError) as caught:
                        with self.camera.preview():
                            self.fail("Configuration failure must not yield a stream")
                    self.assertEqual(caught.exception.status, status)
                    popen.assert_not_called()
                self.assertFalse(self.camera.lock.locked())


class PreviewParserTests(unittest.TestCase):
    def stream(self):
        selector = Mock()
        selector.select.return_value = [True]
        return MODULE.PreviewStream(Mock(), selector)

    def test_split_and_concatenated_jpegs(self):
        stream = self.stream()
        with patch.object(MODULE.os, "read", side_effect=[JPEG[:1], JPEG[1:] + JPEG]):
            self.assertEqual(stream.frame(), JPEG)
            self.assertEqual(stream.frame(), JPEG)

    def test_malformed_truncated_and_oversized_streams(self):
        for chunks in ([b"garbage"], [JPEG[:3], b""], [b"\xff\xd8" + b"x" * (MODULE.MAX_JPEG_BYTES - 2)]):
            stream = self.stream()
            with patch.object(MODULE.os, "read", side_effect=chunks):
                with self.assertRaises(MODULE.CaptureError) as caught:
                    stream.frame()
            self.assertEqual(caught.exception.status, 502)
            self.assertLessEqual(len(stream.buffer), MODULE.MAX_JPEG_BYTES)

    def test_first_interframe_and_hard_deadlines(self):
        with patch.object(MODULE.time, "monotonic", return_value=0):
            stream = self.stream()
            stream.selector.select.return_value = []
            with self.assertRaises(MODULE.CaptureError):
                stream.frame()
            stream.selector.select.assert_called_with(10)
            stream.first = False
            with self.assertRaises(MODULE.CaptureError):
                stream.frame()
            stream.selector.select.assert_called_with(5)
        with patch.object(MODULE.time, "monotonic", return_value=61):
            stream.buffer.extend(JPEG)
            with self.assertRaises(MODULE.CaptureError):
                stream.frame()


if __name__ == "__main__":
    unittest.main()
