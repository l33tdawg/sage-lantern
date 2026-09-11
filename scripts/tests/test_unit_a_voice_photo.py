import argparse
import base64
from contextlib import ExitStack
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("voice_photo", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def jpeg(width=640, height=480, entropy=b"mock-entropy"):
    frame = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    scan = b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00"
    return b"\xff\xd8\xff\xc0\x00\x11" + frame + b"\xff\xda\x00\x0c" + scan + entropy + b"\xff\xd9"


class PhotoTests(unittest.TestCase):
    def setUp(self):
        lock_policy = patch.object(MODULE, "lock_required", return_value=False)
        lock_policy.start()
        self.addCleanup(lock_policy.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.photo_dir = self.root / "photos"
        self.control = MODULE.VoiceControl(30, photo_ready=True, photo_dir=self.photo_dir)

    def start(self, image=None):
        return self.control.start(str(uuid.uuid4()), jpeg() if image is None else image)[1]

    def request(self, path="/start", payload=None, changes=None, method="POST", duplicate=None, raw=None):
        if payload is None:
            payload = {"request_id": str(uuid.uuid4()), "image_base64": base64.b64encode(jpeg()).decode()}
        body = json.dumps(payload).encode() if raw is None else raw
        values = {"Host": "127.0.0.1:3197", "Origin": "http://127.0.0.1:3100", "X-Lantern-Voice": "control", "Content-Type": "application/json", "Content-Length": str(len(body))}
        values.update(changes or {})
        handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
        handler.headers = Message()
        for key, value in values.items():
            if value is not None:
                handler.headers[key] = value
        if duplicate:
            handler.headers[duplicate] = values[duplicate]
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.server = SimpleNamespace(control=self.control)
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        getattr(handler, "do_" + method)()
        headers, response = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        self.assertIn(b"Cache-Control: no-store", headers)
        self.assertNotIn(b"Access-Control-Allow-Origin: *", headers)
        return int(headers.split()[1]), json.loads(response)

    def test_opt_in_gate_is_off_without_explicit_flag(self):
        self.control = MODULE.VoiceControl(30)
        self.assertEqual(self.request(), (503, {"error": "photo_unavailable"}))
        self.assertFalse(self.control.busy())
        self.assertFalse(self.photo_dir.exists())
        status, metadata = self.request("/health", {}, {"Content-Length": "0"}, "GET")
        self.assertEqual(status, 200)
        self.assertFalse(metadata["photo_ready"])

    def test_jpeg_dimension_and_encoding_bounds(self):
        for image in (jpeg(), jpeg(480, 640), jpeg(1, 1)):
            self.assertEqual(MODULE.decode_photo(base64.b64encode(image).decode()), image)
        invalid = [b"not jpeg", b"\xff\xd8\xff\xd9", jpeg(641, 480), jpeg(640, 481), jpeg(0, 480), jpeg()[:-2], jpeg(entropy=b"x" * MODULE.MAX_PHOTO_BYTES), b"\xff\xd8\xff\xe0\xff\xff\xff\xd9"]
        for image in invalid:
            with self.assertRaises(MODULE.ControlError):
                MODULE.validate_photo(image)
        for encoded in (None, "data:image/jpeg;base64,AAAA", "invalid!", "\u2603", "A" * (4 * ((MODULE.MAX_PHOTO_BYTES + 2) // 3) + 1)):
            with self.assertRaises(MODULE.ControlError):
                MODULE.decode_photo(encoded)

    def test_body_limit_only_start_and_exact_security(self):
        for key, value in (("Origin", None), ("Origin", "null"), ("Origin", "https://evil.example"), ("Host", "192.168.1.2:3197"), ("X-Lantern-Voice", None), ("Content-Type", "text/plain"), ("Transfer-Encoding", "chunked"), ("Content-Length", str(MODULE.MAX_PHOTO_BODY + 1))):
            self.assertIn(self.request(changes={key: value})[0], (400, 403))
        self.assertEqual(self.request(duplicate="Content-Length")[0], 400)
        self.assertEqual(self.request("/clear", {"image_base64": "x" * 513})[0], 400)
        payload = {"request_id": str(uuid.uuid4()), "image_base64": base64.b64encode(jpeg(entropy=b"x" * 2000)).decode()}
        self.assertEqual(self.request(payload=payload)[0], 201)

    def test_private_file_duplicate_retry_and_attachment_immutability(self):
        request_id = str(uuid.uuid4())
        status, credentials = self.control.start(request_id, jpeg())
        path = self.control.owner["image_path"]
        self.assertEqual(status, 201)
        self.assertEqual(path.read_bytes(), jpeg())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.photo_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.control.start(request_id, jpeg()), (200, credentials))
        self.assertEqual(len(list(self.photo_dir.iterdir())), 1)
        for replacement in (None, jpeg(entropy=b"different")):
            with self.assertRaises(MODULE.ControlError) as caught:
                self.control.start(request_id, replacement)
            self.assertEqual(caught.exception.code, "request_conflict")
        self.control.finish(credentials["session_id"])
        self.assertFalse(path.exists())
        with self.assertRaises(MODULE.ControlError):
            self.control.start(request_id, jpeg())

    def test_original_is_separate_bounded_and_bound_to_retry(self):
        request_id = str(uuid.uuid4())
        original = jpeg(1920, 1080)
        payload = {"request_id": request_id, "image_base64": base64.b64encode(jpeg()).decode(),
                   "original_base64": base64.b64encode(original).decode()}
        status, credentials = self.request(payload=payload)
        self.assertEqual(status, 201)
        self.assertEqual(self.control.owner["image_path"].read_bytes(), jpeg())
        self.assertEqual(self.control.owner["original"], original)
        self.assertEqual(self.request(payload=payload), (200, credentials))
        self.assertEqual(self.request(payload={**payload, "original_base64": base64.b64encode(jpeg(1280, 720)).decode()})[0], 409)
        self.assertIsNone(self.control.original_bytes(str(uuid.uuid4())))
        self.control.take(False)
        self.control.command("stop", credentials["session_id"], credentials["token"])
        self.control.begin_processing(credentials["session_id"])
        self.assertEqual(self.control.original_bytes(credentials["session_id"]), original)
        self.control.finish(credentials["session_id"])
        self.assertIsNone(self.control.original_bytes(credentials["session_id"]))

    def test_original_requires_inference_and_valid_headers(self):
        for original in (jpeg(1921, 1080), jpeg(1920, 1081), jpeg(entropy=b"x" * MODULE.MAX_ORIGINAL_BYTES), b"bad"):
            payload = {"request_id": str(uuid.uuid4()), "image_base64": base64.b64encode(jpeg()).decode(),
                       "original_base64": base64.b64encode(original).decode()}
            self.assertEqual(self.request(payload=payload)[0], 400)
            self.assertFalse(self.control.busy())
        self.assertEqual(self.request(payload={"request_id": str(uuid.uuid4()), "original_base64": "AA=="}),
                         (400, {"error": "original_requires_image"}))

    def test_upload_reservation_excludes_physical_and_disk_error_cleans(self):
        original = MODULE.tempfile.mkstemp

        def upload(*args, **kwargs):
            self.assertTrue(self.control.lock.acquire(blocking=False))
            self.control.lock.release()
            self.assertIsNone(self.control.take(True))
            with self.assertRaises(MODULE.ControlError):
                self.control.start(str(uuid.uuid4()))
            return original(*args, **kwargs)

        with patch.object(MODULE.tempfile, "mkstemp", side_effect=upload):
            credentials = self.start()
        self.control.finish(credentials["session_id"])
        with patch.object(MODULE.os, "fchmod", side_effect=OSError("disk error")):
            with self.assertRaises(MODULE.ControlError) as caught:
                self.start()
        self.assertEqual(caught.exception.code, "photo_storage_unavailable")
        self.assertFalse(self.control.busy())
        self.assertFalse(list(self.photo_dir.iterdir()))

    def loop(self):
        loop = MODULE.VoiceLoop(argparse.Namespace(
            max_seconds=30, photo_ready=True, photo_dir=self.photo_dir,
            audio_dir=self.root / "audio", state=self.root / "state.json", ptt_state=self.root / "missing-ptt",
            runtime=Path("/mock/runtime"), config=Path("/mock/config"), processing_timeout=20,
        ))
        loop.control = self.control
        loop.write_state = Mock()
        return loop

    def test_cancel_and_lease_expiry_delete_without_recording(self):
        for action in ("cancel", "expiry"):
            with patch.object(MODULE.time, "monotonic", return_value=0):
                credentials = self.start()
                if action == "cancel":
                    self.control.command("cancel", credentials["session_id"], credentials["token"])
            loop = self.loop()
            loop.capture = Mock()
            loop.process = Mock()
            with patch.object(MODULE.time, "monotonic", return_value=5):
                loop.step()
            loop.capture.assert_not_called()
            loop.process.assert_not_called()
            self.assertFalse(list(self.photo_dir.iterdir()))
            self.assertFalse(self.control.busy())

    def test_recorder_error_deletes_photo_and_audio_before_releasing_owner(self):
        self.start()
        loop = self.loop()

        def capture(session_id, path):
            path.write_bytes(b"partial audio")
            raise RuntimeError("mock capture failure")

        loop.capture = Mock(side_effect=capture)
        loop.process = Mock()
        original = Path.unlink

        def unlink(path, **kwargs):
            if path.suffix == ".jpg":
                self.assertTrue(self.control.busy())
                self.assertTrue(self.control.lock.acquire(blocking=False))
                self.control.lock.release()
            return original(path, **kwargs)

        with patch.object(Path, "unlink", unlink):
            loop.step()
        loop.process.assert_not_called()
        self.assertFalse(list(self.photo_dir.iterdir()))
        self.assertFalse(list(loop.args.audio_dir.iterdir()))
        self.assertFalse(self.control.busy())

    def test_runtime_image_only_for_attached_session_and_cleanup_on_success_or_error(self):
        final = b'{"type":"result","result":{"transcript":"what?","response":"answer"}}\n'
        for mode, returncode, output in (("success", 0, final), ("nonzero", 1, final), ("decoder_failure", 1, b""), ("bad_json", 0, b"not JSON\n")):
            original = jpeg(1920, 1080)
            credentials = self.control.start(str(uuid.uuid4()), jpeg(), original)[1]
            image_path = self.control.owner["image_path"]
            self.assertIsNone(self.control.image_path(str(uuid.uuid4())))
            loop = self.loop()
            loop.history = Mock()
            loop.history.record.return_value = None

            def capture(session_id, path):
                path.write_bytes(b"mock audio")
                self.control.command("stop", session_id, credentials["token"])
                return True

            loop.capture = Mock(side_effect=capture)
            child = Mock(pid=12345)
            child.wait.return_value = returncode
            selector = MagicMock()
            selector.__enter__.return_value = selector
            selector.select.return_value = [True]
            events = iter([output, b""])

            def launch(*args, **kwargs):
                self.assertEqual(kwargs["stderr"], MODULE.subprocess.DEVNULL)
                return child

            with ExitStack() as stack:
                popen = stack.enter_context(patch.object(MODULE.subprocess, "Popen", side_effect=launch))
                stack.enter_context(patch.object(MODULE.selectors, "DefaultSelector", return_value=selector))
                stack.enter_context(patch.object(MODULE.os, "set_blocking"))
                stack.enter_context(patch.object(MODULE.os, "read", side_effect=lambda *args: next(events)))
                stack.enter_context(patch.object(MODULE.os, "killpg"))
                loop.step()
                self.assertEqual(popen.call_args.args[0][-2:], ["--image", str(image_path)])
            self.assertFalse(image_path.exists())
            self.assertFalse(self.control.busy())
            self.assertFalse(list(loop.args.audio_dir.iterdir()))
            self.assertEqual(loop.write_state.call_args.args[0], "complete" if mode == "success" else "error")
            self.assertNotIn("private decoder diagnostic", str(loop.write_state.call_args_list))
            if mode == "success":
                self.assertEqual(loop.history.record.call_args.args[2], {"transcript": "what?", "response": "answer"})
                self.assertEqual(loop.history.record.call_args.kwargs, {"original": original})
            else:
                loop.history.record.assert_not_called()

    def test_photo_deleted_before_history_failure_without_releasing_owner(self):
        credentials = self.start()
        session_id = credentials["session_id"]
        image_path = self.control.owner["image_path"]
        self.control.take(False)
        self.control.command("stop", session_id, credentials["token"])
        self.assertTrue(self.control.begin_processing(session_id))
        loop = self.loop()
        loop.history_ticket = (session_id, "opted-in")
        loop.history = Mock()

        def persist(*args, **kwargs):
            self.assertFalse(image_path.exists())
            self.assertIsNone(self.control.image_path(session_id))
            self.assertFalse(self.control.has_photo(session_id))
            self.assertTrue(self.control.busy())
            self.assertTrue(self.control.lock.acquire(blocking=False))
            self.control.lock.release()
            with self.assertRaises(MODULE.ControlError):
                self.control.start(str(uuid.uuid4()))
            raise MODULE.ControlError(503, "history_unavailable")

        loop.history.record.side_effect = persist
        child = Mock(pid=12345)
        child.wait.return_value = 0
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [True]
        output = iter([b'{"type":"result","result":{"transcript":"question","response":"answer"}}\n', b""])
        with ExitStack() as stack:
            stack.enter_context(patch.object(MODULE.subprocess, "Popen", return_value=child))
            stack.enter_context(patch.object(MODULE.selectors, "DefaultSelector", return_value=selector))
            stack.enter_context(patch.object(MODULE.os, "set_blocking"))
            stack.enter_context(patch.object(MODULE.os, "read", side_effect=lambda *args: next(output)))
            stack.enter_context(patch.object(MODULE.os, "killpg"))
            loop.process(session_id, Path("mock.wav"))
        loop.history.record.assert_called_once()
        self.assertEqual(loop.write_state.call_args.args[0], "complete")
        self.assertTrue(self.control.busy())
        self.control.finish(session_id)
        self.assertFalse(self.control.busy())

    def test_has_photo_only_identifies_matching_active_session(self):
        credentials = self.start()
        loop = self.loop()
        loop.write_state = MODULE.VoiceLoop.write_state.__get__(loop)
        loop.input_source = "touch"
        self.control.take(False)
        for phase in ("listening", "processing"):
            if phase == "processing":
                self.control.command("stop", credentials["session_id"], credentials["token"])
                self.assertTrue(self.control.begin_processing(credentials["session_id"]))
            loop.write_state(phase, session_id=credentials["session_id"])
            raw = loop.args.state.read_text()
            state = json.loads(raw)
            self.assertTrue(state["has_photo"])
            self.assertTrue(state["photo_ready"])
            self.assertNotIn(credentials["token"], raw)
            self.assertNotIn(str(self.control.owner["image_path"]), raw)
        loop.write_state("processing", session_id=str(uuid.uuid4()))
        self.assertFalse(json.loads(loop.args.state.read_text())["has_photo"])
        loop.write_state("complete", session_id=credentials["session_id"])
        self.assertFalse(json.loads(loop.args.state.read_text())["has_photo"])
        self.control.finish(credentials["session_id"])
        loop.write_state("idle")
        self.assertFalse(json.loads(loop.args.state.read_text())["has_photo"])

    def test_large_start_body_rejects_deep_json_without_reserving_owner(self):
        raw = b'{"request_id":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}"
        status, response = self.request(raw=raw)
        self.assertEqual(status, 400)
        self.assertIn(response["error"], {"invalid_json", "invalid_request_id"})
        self.assertFalse(self.control.busy())
        self.assertFalse(self.photo_dir.exists())

    def test_clear_is_idle_reserved_main_writer_and_never_clears_history(self):
        loop = self.loop()
        loop.history = Mock()
        loop.history_maintenance = float("inf")
        self.assertEqual(self.request("/clear", {}), (202, {"status": "clearing"}))
        self.assertEqual(self.request("/clear", {}), (202, {"status": "clearing"}))
        loop.write_state.assert_not_called()
        with self.assertRaises(MODULE.ControlError):
            self.control.start(str(uuid.uuid4()))

        def write(phase, **values):
            self.assertTrue(self.control.busy())
            self.assertEqual((phase, values["transcript"], values["response"]), ("idle", "", ""))

        loop.write_state.side_effect = write
        loop.step()
        loop.write_state.assert_called_once()
        self.assertFalse(self.control.busy())
        loop.history.request.assert_not_called()
        self.control.take(True)
        self.assertEqual(self.request("/clear", {})[0], 409)

    def test_clear_requires_empty_body_and_secure_preflight(self):
        self.assertEqual(self.request("/clear", {"session_id": "any"})[0], 400)
        self.assertEqual(self.request("/clear", {}, {"Origin": "null"})[0], 403)
        self.assertEqual(self.request("/clear", {}, {"Content-Length": "0"}, "GET")[0], 404)
        headers = {"Content-Length": "0", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-lantern-voice"}
        self.assertEqual(self.request("/clear", {}, headers, "OPTIONS")[0], 200)


if __name__ == "__main__":
    unittest.main()
