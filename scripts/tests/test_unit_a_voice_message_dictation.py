import argparse
from contextlib import ExitStack, redirect_stdout
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch
import uuid
import wave


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("voice_message_tests", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MessageVoiceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(MODULE, "lock_required", return_value=False))
        self.valid = True
        self.stack.enter_context(patch.object(MODULE, "token_authorized", side_effect=lambda token: self.valid and token == "device-session"))
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.loop = MODULE.VoiceLoop(argparse.Namespace(
            max_seconds=30, min_seconds=0.01, message_audio_dir=root / "dictation",
            audio_dir=root / "must-not-exist", config=root / "absent.toml",
            processing_timeout=1, alsa_device="fixture",
        ))
        self.loop.write_state = Mock()
        self.loop.process = Mock(side_effect=AssertionError("LLM voice pipeline forbidden"))
        self.loop.begin_history_session = Mock(side_effect=AssertionError("history forbidden"))
        self.loop.history = Mock()
        self.loop.history.record.side_effect = AssertionError("history write forbidden")
        self.loop.message_transcribe = Mock(return_value="Exact private dictated text.")

    def start(self):
        return self.loop.control.start(str(uuid.uuid4()), device_token="device-session", purpose="message")[1]

    def capture(self, session_id, path, *, message=False):
        self.assertTrue(message)
        self.assertEqual(path.parent, self.loop.args.message_audio_dir)
        with wave.open(str(path), "wb") as recording:
            recording.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
            recording.writeframes(b"\0" * 640)
        self.loop.control.owner["action"] = "stop"
        return True

    def run_segment(self):
        credentials = self.start()
        self.loop.capture = self.capture
        self.loop.run_session(self.loop.control.take(False))
        return credentials

    def status(self, credentials):
        return self.loop.control.message_status(credentials["session_id"], credentials["token"], "device-session")

    def request(self, path, payload, device_token="device-session"):
        raw = json.dumps(payload).encode()
        handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
        handler.path = path
        handler.headers = Message()
        for key, value in {"Host": "127.0.0.1:3197", "Origin": "http://127.0.0.1:3100",
                           "Content-Type": "application/json", "X-Lantern-Voice": "control",
                           "Content-Length": str(len(raw)), "X-Lantern-Session": device_token}.items():
            handler.headers[key] = value
        handler.rfile = io.BytesIO(raw)
        handler.server = SimpleNamespace(control=self.loop.control)
        handler.respond = Mock()
        handler.do_POST()
        return handler.respond.call_args.args

    def test_message_requires_auth_even_without_lock_marker(self):
        code, _ = self.request("/start", {"request_id": str(uuid.uuid4()), "purpose": "message"}, "")
        self.assertEqual(code, 403)
        self.assertFalse(self.loop.control.busy())

    def test_preview_only_no_shared_text_history_or_llm(self):
        credentials = self.run_segment()
        result = self.status(credentials)
        self.assertEqual(result["phase"], "review")
        self.assertEqual(result["text"], "Exact private dictated text.")
        self.loop.process.assert_not_called()
        self.loop.begin_history_session.assert_not_called()
        self.loop.history.record.assert_not_called()
        self.assertNotIn(result["text"], str(self.loop.write_state.call_args_list))
        self.assertEqual(list(self.loop.args.message_audio_dir.iterdir()), [])
        self.assertFalse(self.loop.args.audio_dir.exists())
        self.assertFalse(self.loop.control.busy())

    def test_auth_and_credentials_required_for_status_and_cancel(self):
        credentials = self.run_segment()
        body = {key: credentials[key] for key in ("session_id", "token")}
        self.assertEqual(self.request("/message/status", body, "wrong")[0], 403)
        self.assertEqual(self.request("/cancel", body, "wrong")[0], 403)
        self.assertEqual(self.request("/message/status", dict(body, token="wrong"))[0], 409)
        self.assertEqual(self.request("/cancel", body)[0], 200)
        self.assertEqual(self.status(credentials)["phase"], "cancelled")
        self.assertNotIn("text", self.status(credentials))

    def test_arbitration_and_replay_purpose_binding(self):
        credentials = self.start()
        request_id = self.loop.control.owner["request_id"]
        with self.assertRaises(MODULE.ControlError):
            self.loop.control.start(request_id, device_token="device-session")
        with self.assertRaises(MODULE.ControlError):
            self.loop.control.start(str(uuid.uuid4()))
        session = self.loop.control.take(True)
        self.assertEqual(session["session_id"], credentials["session_id"])
        self.assertEqual(session["purpose"], "message")

    def test_photos_unknown_purpose_and_extra_fields_rejected(self):
        for extra in ({"image_base64": "bad"}, {"original_base64": "bad"}, {"text": "private"}):
            self.assertEqual(self.request("/start", {"request_id": str(uuid.uuid4()), "purpose": "message", **extra})[0], 400)
        self.assertEqual(self.request("/start", {"request_id": str(uuid.uuid4()), "purpose": "other"})[0], 400)

    def test_stt_error_music_or_lock_never_publishes_text(self):
        for mode in ("error", "music", "lock", "suspend", "cancel"):
            with self.subTest(mode=mode):
                def transcribe(*args):
                    if mode == "error":
                        raise RuntimeError("private diagnostic")
                    if mode == "lock":
                        self.valid = False
                    if mode == "suspend":
                        self.loop.request_stop()
                    if mode == "cancel":
                        owner = self.loop.control.owner
                        result = self.loop.control.command("cancel", owner["session_id"], owner["token"])
                        self.assertEqual(result["status"], "cancelling")
                    return "[MUSIC]" if mode == "music" else "must not escape"
                self.loop.message_transcribe.side_effect = transcribe
                credentials = self.run_segment()
                if mode in {"lock", "suspend"}:
                    self.assertIsNone(self.loop.control.message_result)
                else:
                    self.assertNotIn("text", self.status(credentials))
                self.assertEqual(list(self.loop.args.message_audio_dir.iterdir()), [])
                self.assertNotIn("must not escape", str(self.loop.write_state.call_args_list))
                self.valid = True
                self.loop.stop_requested.clear()

    def test_cancel_before_capture_and_review_expiry(self):
        credentials = self.start()
        self.loop.control.command("stop", credentials["session_id"], credentials["token"])
        self.loop.capture = Mock(side_effect=AssertionError("already stopped"))
        self.loop.run_session(self.loop.control.take(False))
        self.loop.capture.assert_not_called()
        self.assertEqual(self.status(credentials)["phase"], "cancelled")
        self.loop.control.message_result["expires"] = 0
        with self.assertRaises(MODULE.ControlError):
            self.status(credentials)

    def test_worker_constructs_only_configured_whisper(self):
        from sage_lantern import adapters, config, message_dictation
        settings = SimpleNamespace(whisper_binary=Path("fixture-whisper"), whisper_model=Path("fixture-model"), whisper_threads=6)
        output = io.StringIO()
        with patch.object(message_dictation, "_validate_wav"), patch.object(config.RuntimeConfig, "load", return_value=settings), patch.object(adapters, "WhisperCppSTT") as stt, patch.object(adapters, "LlamaCppHTTP", side_effect=AssertionError("LLM forbidden")), redirect_stdout(output):
            stt.return_value.transcribe.return_value = "Exact text"
            self.assertEqual(MODULE.message_stt_worker(Path("config"), Path("audio")), 0)
            stt.assert_called_once_with(settings.whisper_binary, settings.whisper_model, 6)
            stt.return_value.transcribe.assert_called_once_with(Path("audio"))
        self.assertEqual(json.loads(output.getvalue()), {"text": "Exact text"})

    def test_supervisor_is_bounded_and_reaps_stt_group_on_cancel(self):
        credentials = self.start()
        session_id = credentials["session_id"]
        self.loop.device_session = (session_id, "device-session", True)
        process = Mock(pid=98765)
        process.stdout.fileno.return_value = 123
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.side_effect = lambda *args: (self.loop.request_stop(), [True])[1]
        with patch.object(MODULE.subprocess, "Popen", return_value=process) as launch, patch.object(MODULE.os, "set_blocking"), patch.object(MODULE.os, "read", return_value=b""), patch.object(MODULE.selectors, "DefaultSelector", return_value=selector), patch.object(MODULE.os, "killpg") as kill:
            with self.assertRaises(MODULE.DeviceLocked):
                MODULE.VoiceLoop.message_transcribe(self.loop, session_id, Path("/fixture.wav"))
        arguments = launch.call_args.args[0]
        self.assertIn("--message-stt-worker", arguments)
        self.assertNotIn("voice", arguments)
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        self.assertNotIn("LANTERN_DEVICE_TOKEN", launch.call_args.kwargs["env"])
        kill.assert_any_call(process.pid, signal.SIGTERM)
        kill.assert_any_call(process.pid, signal.SIGKILL)
        process.stdout.close.assert_called_once()

    def test_http_start_and_status_contract(self):
        code, credentials = self.request("/start", {"request_id": str(uuid.uuid4()), "purpose": "message"})
        self.assertEqual(code, 201)
        body = {key: credentials[key] for key in ("session_id", "token")}
        self.assertEqual(self.request("/message/status", body), (200, {"session_id": credentials["session_id"], "purpose": "message", "phase": "queued"}))
        self.loop.capture = self.capture
        self.loop.run_session(self.loop.control.take(False))
        code, result = self.request("/message/status", body)
        self.assertEqual(code, 200)
        self.assertEqual(result["phase"], "review")
        self.assertNotIn("sent", result)
        self.assertNotIn("delivered", result)

    def test_real_capture_branch_skips_history_and_uses_same_owner(self):
        credentials = self.start()
        session_id = credentials["session_id"]
        self.loop.control.take(False)
        self.loop.control.owner["action"] = "stop"
        self.loop.device_session = (session_id, "device-session", True)
        path = self.loop.args.message_audio_dir.parent / "capture.wav"
        with wave.open(str(path), "wb") as recording:
            recording.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
            recording.writeframes(b"\0" * 640)
        process = Mock(returncode=0, stderr=io.BytesIO(b""))
        process.poll.return_value = 0
        with patch.object(MODULE.subprocess, "Popen", return_value=process) as launch, patch.object(MODULE.time, "monotonic", side_effect=[0, 1]):
            self.assertTrue(self.loop.capture(session_id, path, message=True))
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        self.loop.begin_history_session.assert_not_called()

    def test_output_overflow_reaps_child_and_never_returns_text(self):
        credentials = self.start()
        self.loop.device_session = (credentials["session_id"], "device-session", True)
        process = Mock(pid=98765)
        process.stdout.fileno.return_value = 123
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [True]
        with patch.object(MODULE.subprocess, "Popen", return_value=process), patch.object(MODULE.os, "set_blocking"), patch.object(MODULE.os, "read", return_value=b"x" * 16385), patch.object(MODULE.selectors, "DefaultSelector", return_value=selector), patch.object(MODULE.os, "killpg") as kill:
            with self.assertRaisesRegex(RuntimeError, "output_limit"):
                MODULE.VoiceLoop.message_transcribe(self.loop, credentials["session_id"], Path("/fixture.wav"))
        kill.assert_any_call(process.pid, signal.SIGKILL)

    def test_quiesce_discards_cached_preview_and_rejects_new_message(self):
        self.run_segment()
        self.assertIsNotNone(self.loop.control.message_result)
        self.loop.control.quiesce()
        self.assertIsNone(self.loop.control.message_result)
        with self.assertRaises(MODULE.ControlError):
            self.start()

    def test_ptt_release_stops_only_the_active_message_capture(self):
        credentials = self.start()
        session_id = credentials["session_id"]
        self.loop.control.take(False)
        self.loop.device_session = (session_id, "device-session", True)
        path = self.loop.args.message_audio_dir.parent / "ptt.wav"
        with wave.open(str(path), "wb") as recording:
            recording.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
            recording.writeframes(b"\0" * 640)
        process = Mock(returncode=0, stderr=io.BytesIO(b""))
        process.poll.return_value = None
        self.loop.ptt = Mock(side_effect=["down", "up"])
        clock = [MODULE.time.monotonic()]

        def tick():
            clock[0] += 0.02
            return clock[0]

        with patch.object(MODULE.subprocess, "Popen", return_value=process), patch.object(self.loop, "stop_recording", return_value=True), patch.object(MODULE.time, "monotonic", side_effect=tick), patch.object(MODULE.time, "sleep"):
            self.assertTrue(self.loop.capture(session_id, path, message=True))
        self.assertEqual(self.loop.control.owner["action"], "stop")
        self.loop.process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
