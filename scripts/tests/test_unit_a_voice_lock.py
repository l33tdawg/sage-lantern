import argparse
from contextlib import ExitStack
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import uuid
import hashlib
from sage_lantern.session_context import SessionContext


SPEC = importlib.util.spec_from_file_location("voice_lock", Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VoiceLockTests(unittest.TestCase):
    def setUp(self):
        self.live_token = "synthetic-device-token"
        self.original_token = self.live_token
        self.enabled = True
        self.epoch = "a" * 32
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(MODULE.device_access, "lock_enabled", side_effect=lambda: self.enabled))
        self.authorize = self.stack.enter_context(patch.object(MODULE.device_access, "authorized", side_effect=lambda token: token == self.live_token))
        self.stack.enter_context(patch.object(MODULE.device_access, "device_unlocked", side_effect=lambda: self.live_token is not None))
        self.context = self.stack.enter_context(patch.object(MODULE.device_access, "session_context", side_effect=
            lambda token: SessionContext(self.epoch, hashlib.sha256(token.encode()).hexdigest())
            if token == self.live_token and token is not None else None))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.control = MODULE.VoiceControl(30, photo_ready=True, photo_dir=self.root / "photos")
        self.history = Mock()
        self.history.request.return_value = {"items": ["private-history"]}

    def request(self, path, payload=None, *, token=None, duplicate=False, method="POST", requested=None):
        body = json.dumps(payload or {}).encode()
        handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
        handler.headers = Message()
        for key, value in {"Host": "127.0.0.1:3197", "Origin": "http://127.0.0.1:3100",
                           "Content-Type": "application/json", "X-Lantern-Voice": "control",
                           "Content-Length": str(len(body)) if method == "POST" else "0"}.items():
            handler.headers[key] = value
        if token is not None:
            handler.headers["X-Lantern-Session"] = token
        if duplicate:
            handler.headers["X-Lantern-Session"] = token
        if requested is not None:
            handler.headers["Access-Control-Request-Method"] = "POST"
            handler.headers["Access-Control-Request-Headers"] = requested
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.server = SimpleNamespace(control=self.control, history=self.history)
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        getattr(handler, "do_" + method)()
        headers, content = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        self.assertIn(b"Cache-Control: no-store", headers)
        self.assertNotIn(self.original_token.encode(), content)
        result = json.loads(content)
        context = result.pop("session_context", None)
        if self.enabled and method == "POST" and int(headers.split()[1]) < 400:
            self.assertIsNotNone(context)
        if context is not None:
            self.assertEqual(context, SessionContext(self.epoch, hashlib.sha256(token.encode()).hexdigest()).to_dict())
        return int(headers.split()[1]), result

    def loop(self):
        loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, min_seconds=0.1, alsa_device="mock",
            runtime=Path("/mock/runtime"), config=Path("/mock/config"), processing_timeout=20,
            audio_dir=self.root / "audio", state=self.root / "state.json"))
        loop.control = self.control
        loop.write_state = Mock()
        return loop

    def admit(self, loop):
        _, credentials = self.control.start(str(uuid.uuid4()), device_token=self.original_token)
        session = self.control.take(False)
        self.control.command("stop", session["session_id"], credentials["token"])
        self.assertTrue(self.control.begin_processing(session["session_id"]))
        loop.device_session = (session["session_id"], self.original_token, True)
        return session

    def test_every_post_denies_missing_bad_and_duplicate_token(self):
        paths = MODULE.HISTORY_PATHS | MODULE.QUESTION_PATHS | {"/start", "/start/confirm", "/clear", "/stop", "/cancel", "/keepalive", "/session/link"}
        for path in paths:
            for token in (None, "old-token"):
                self.assertEqual(self.request(path, token=token), (403, {"error": "device_locked"}))
            self.assertEqual(self.request(path, token=self.live_token, duplicate=True),
                             (400, {"error": "invalid_device_session"}))
        self.history.request.assert_not_called()
        self.assertFalse(self.control.busy())

    def test_link_start_and_minimal_health(self):
        self.assertEqual(self.request("/health", method="GET"), (200, {"ok": True, "correction_ready": False,
                                                                       "review_ready": False, "typed_ready": False}))
        self.assertEqual(self.request("/session/link", token=self.live_token), (200, {"linked": True}))
        self.assertEqual(self.control.device_token, self.live_token)
        self.assertEqual(self.request("/session/link", {"extra": True}, token=self.live_token)[0], 400)
        self.assertEqual(self.request("/start", {"request_id": str(uuid.uuid4())}, token=self.live_token)[0], 201)
        self.assertEqual(self.control.owner["device_token"], self.live_token)

    def test_missing_context_denies_admission_even_when_token_is_authorized(self):
        self.context.side_effect = None
        self.context.return_value = None
        self.assertEqual(self.request("/start", {"request_id": str(uuid.uuid4())}, token=self.live_token),
                         (403, {"error": "device_locked"}))
        self.assertFalse(self.control.busy())

    def test_same_token_epoch_rotation_cannot_confirm_or_command_old_work(self):
        request_id = str(uuid.uuid4())
        _, session = self.request("/start", {"request_id": request_id}, token=self.live_token)
        self.epoch = "c" * 32
        payload = {"request_id": request_id, "session_id": session["session_id"], "token": session["token"]}
        self.assertEqual(self.request("/start/confirm", payload, token=self.live_token),
                         (403, {"error": "device_locked"}))
        self.assertEqual(self.request("/stop", {"session_id": session["session_id"], "token": session["token"]}, token=self.live_token),
                         (403, {"error": "device_locked"}))

    def test_publication_keeps_admission_context_and_redacts_after_rotation(self):
        loop = self.loop()
        session = self.admit(loop)
        expected = self.control.session_context_for(session["session_id"]).to_dict()
        loop.write_state = MODULE.VoiceLoop.write_state.__get__(loop)
        loop.write_state("processing", session_id=session["session_id"], transcript="private question", response="")
        self.assertEqual(json.loads(loop.args.state.read_text())["session_context"], expected)
        self.epoch = "c" * 32
        loop.write_state("complete", session_id=session["session_id"], transcript="late question", response="late answer")
        state = json.loads(loop.args.state.read_text())
        self.assertEqual(state["phase"], "idle")
        self.assertNotIn("session_context", state)
        self.assertNotIn("late", json.dumps(state))

    def test_rotation_after_handler_mutation_suppresses_reply(self):
        def query(*args):
            self.epoch = "c" * 32
            return {"items": ["private-canary"]}
        self.history.request.side_effect = query
        self.assertEqual(self.request("/history/list", token=self.live_token), (403, {"error": "device_locked"}))

    def test_same_token_rotation_clears_cached_conversation_on_relink(self):
        self.control.link_device(self.live_token)
        self.control.completed_source = {"source_id": "old", "question": "private-canary"}
        self.control.source_session_id = hashlib.sha256(self.live_token.encode()).hexdigest()
        old_revision = self.control.conversation.revision
        self.epoch = "c" * 32
        self.control.link_device(self.live_token)
        self.assertIsNone(self.control.completed_source)
        self.assertIsNone(self.control.source_session_id)
        self.assertGreater(self.control.conversation.revision, old_revision)

    def test_confirmation_is_bound_to_the_starting_device_session(self):
        request_id = str(uuid.uuid4())
        _, session = self.request("/start", {"request_id": request_id}, token=self.original_token)
        payload = {"request_id": request_id, "session_id": session["session_id"], "token": session["token"]}
        self.assertEqual(self.request("/start/confirm", payload, token=self.original_token)[0], 200)
        self.live_token = "replacement-authorized-device-token"
        self.assertEqual(self.request("/start/confirm", payload, token=self.live_token),
                         (403, {"error": "device_locked"}))
        self.assertEqual(self.request("/start/confirm", payload, token=self.original_token),
                         (403, {"error": "device_locked"}))

    def test_options_allow_only_legacy_or_session_header_set(self):
        for requested in ("content-type,x-lantern-voice", "content-type,x-lantern-voice,x-lantern-session"):
            self.assertEqual(self.request("/session/link", method="OPTIONS", requested=requested)[0], 200)
        for requested in ("x-lantern-session", "content-type,x-lantern-voice,x-arbitrary", "content-type,x-lantern-voice,x-lantern-session,x-lantern-session"):
            self.assertEqual(self.request("/start", method="OPTIONS", requested=requested)[0], 403)

    def test_relock_during_history_query_suppresses_response(self):
        self.control.link_device(self.live_token)

        def query(*args):
            self.live_token = "new-unlock-token"
            return {"context": ["private-history"]}

        self.history.request.side_effect = query
        self.assertEqual(self.request("/history/context", {"query": "q"}, token=self.original_token),
                         (403, {"error": "device_locked"}))
        self.assertIsNone(self.control.device_token)

    def test_physical_requires_link_and_old_work_cannot_use_new_link(self):
        self.assertIsNone(self.control.take(True))
        self.control.link_device(self.live_token)
        session = self.control.take(True)
        self.live_token = "new-unlock-token"
        self.control.link_device(self.live_token)
        loop = self.loop()
        loop.capture = Mock()
        loop.run_session(session)
        loop.capture.assert_not_called()
        self.assertFalse(self.control.busy())
        self.assertIsNone(self.control.device_token)
        self.assertIsNone(loop.device_session)

    def test_capture_revocation_stops_recorder(self):
        loop = self.loop()
        session = self.admit(loop)
        process = Mock(stderr=io.BytesIO(), returncode=0)

        def poll():
            self.live_token = None
            return None

        process.poll.side_effect = poll
        loop.stop_recording = Mock(return_value=True)
        with patch.object(MODULE.subprocess, "Popen", return_value=process):
            with self.assertRaises(MODULE.DeviceLocked):
                loop.capture(session["session_id"], self.root / "audio.wav")
        loop.stop_recording.assert_called_once_with(process)
        self.assertTrue(process.stderr.closed)

    def pipeline(self, *, revoke=False, eof_revoke=False):
        loop = self.loop()
        session = self.admit(loop)
        loop.history = Mock()
        loop.history_ticket = (session["session_id"], "synthetic-consent")
        child = Mock(pid=12345)
        child.wait.return_value = 0
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [True]
        chunks = iter([b'{"type":"result","result":{"transcript":"private question","response":"private answer"}}\n', b""])

        def read(*args):
            if revoke:
                self.live_token = "new-unlock-token"
                self.control.link_device(self.live_token)
            return next(chunks)

        def wait(*args, **kwargs):
            if eof_revoke:
                self.live_token = None
                raise subprocess.TimeoutExpired("runtime", 0.2)
            return 0

        child.wait.side_effect = wait
        with ExitStack() as stack:
            spawn = stack.enter_context(patch.object(MODULE.subprocess, "Popen", return_value=child))
            stack.enter_context(patch.object(MODULE.selectors, "DefaultSelector", return_value=selector))
            stack.enter_context(patch.object(MODULE.os, "read", side_effect=read))
            stack.enter_context(patch.object(MODULE.os, "set_blocking"))
            stop = stack.enter_context(patch.object(loop, "stop_pipeline"))
            if revoke or eof_revoke:
                with self.assertRaises(MODULE.DeviceLocked):
                    loop.process(session["session_id"], self.root / "input.wav")
                stop.assert_called_once_with(child)
                loop.history.record.assert_not_called()
                self.assertNotIn("private answer", str(loop.write_state.call_args_list))
            else:
                loop.process(session["session_id"], self.root / "input.wav")
                loop.history.record.assert_called_once()
                self.assertEqual(loop.write_state.call_args.args, ("complete",))
            self.assertEqual(spawn.call_args.kwargs["env"]["LANTERN_DEVICE_TOKEN"], self.original_token)
            self.assertNotIn(self.original_token, str(spawn.call_args.args))
            self.assertNotIn(self.original_token, str(loop.write_state.call_args_list))
        self.assertTrue(child.stdout.close.called)

    def test_pipeline_env_and_valid_completion(self):
        self.pipeline()

    def test_revoked_pipeline_cancels_and_discards_events(self):
        self.pipeline(revoke=True)

    def test_revocation_after_eof_cancels_waiting_process(self):
        self.pipeline(eof_revoke=True)

    def test_revocation_cleanup_and_late_state_redaction(self):
        loop = self.loop()
        _, credentials = self.control.start(str(uuid.uuid4()), device_token=self.original_token)
        session = self.control.take(False)
        photo = self.root / "synthetic.jpg"
        photo.write_bytes(b"synthetic photo")
        self.control.owner.update(image_path=photo, original=b"synthetic original")

        def capture(session_id, path):
            path.write_bytes(b"synthetic audio")
            self.live_token = None
            self.control.command("stop", session_id, credentials["token"])
            return True

        loop.capture = Mock(side_effect=capture)
        loop.run_session(session)
        self.assertFalse(photo.exists())
        self.assertEqual(list(loop.args.audio_dir.iterdir()), [])
        self.assertIsNone(self.control.owner)
        self.assertIsNone(self.control.device_token)
        loop.write_state = MODULE.VoiceLoop.write_state.__get__(loop)
        loop.write_state("complete", session_id=session["session_id"], transcript="late private", response="late answer")
        state = json.loads(loop.args.state.read_text())
        self.assertEqual(state["phase"], "idle")
        self.assertEqual(state["response"], "")
        self.assertNotIn("late", json.dumps(state))
        self.assertNotIn(self.original_token, json.dumps(state))

    def test_history_guard_revokes_before_metadata_commit(self):
        history = MODULE.LocalHistory(self.root / "history")
        consent = history.consent()
        before = history.path.read_bytes()
        calls = 0

        def check():
            nonlocal calls
            calls += 1
            if calls == 4:
                raise MODULE.DeviceLocked()

        with self.assertRaises(MODULE.DeviceLocked):
            history.record(consent, str(uuid.uuid4()), {"transcript": "q", "response": "a"}, access_check=check)
        self.assertEqual(history.path.read_bytes(), before)

    def test_legacy_mode_preserves_post_and_physical_without_token(self):
        self.enabled = False
        self.assertEqual(self.request("/history/list")[0], 200)
        self.assertIsNotNone(self.control.take(True))
        self.authorize.assert_not_called()
        self.assertIn("photo_ready", self.request("/health", method="GET")[1])

    def test_daemon_failure_denies_browser_and_physical_admission(self):
        self.control.link_device(self.live_token)
        with patch.object(MODULE.device_access, "authorized", side_effect=OSError("private daemon detail")):
            self.assertEqual(self.request("/history/list", token=self.live_token), (403, {"error": "device_locked"}))
            self.assertIsNone(self.control.take(True))
        self.history.request.assert_not_called()
        self.assertIsNone(self.control.device_token)

    def test_locked_steps_write_once_and_first_lock_clears_private_state(self):
        loop = self.loop()
        loop.ptt = Mock(return_value="up")
        loop.write_state = MODULE.VoiceLoop.write_state.__get__(loop)
        loop.args.state.write_text(json.dumps({"response": "previous private answer", "transcript": "private question"}))
        self.live_token = None
        with patch.object(MODULE.os, "fsync", wraps=MODULE.os.fsync) as synced:
            loop.step()
            first = loop.args.state.read_bytes()
            self.assertNotIn(b"private", first)
            self.assertEqual(json.loads(first)["detail"], "Device locked.")
            self.assertEqual(synced.call_count, 1)
            for _ in range(50):
                loop.step()
            self.assertEqual(synced.call_count, 1)
            self.assertEqual(loop.args.state.read_bytes(), first)

    def test_unlock_refreshes_empty_idle_once_and_relock_clears_again(self):
        loop = self.loop()
        loop.ptt = Mock(return_value="up")
        loop.touch_ready = True
        loop.write_state = MODULE.VoiceLoop.write_state.__get__(loop)
        self.live_token = None
        with patch.object(MODULE.os, "fsync", wraps=MODULE.os.fsync) as synced:
            loop.step()
            self.live_token = "new-unlock-token"
            self.control.link_device(self.live_token)
            loop.step()
            state = json.loads(loop.args.state.read_text())
            self.assertEqual((state["phase"], state["detail"], state["response"]), ("idle", "", ""))
            self.assertTrue(state["touch_ready"])
            self.assertEqual(self.control.device_token, self.live_token)
            self.assertFalse(loop.locked_state_written)
            for _ in range(20):
                loop.step()
            self.assertEqual(synced.call_count, 2)
            self.live_token = None
            loop.step()
            loop.step()
            self.assertEqual(synced.call_count, 3)
            self.assertTrue(loop.locked_state_written)

    def test_failed_lock_state_write_retries_until_private_state_cleared(self):
        loop = self.loop()
        loop.ptt = Mock(return_value="up")
        loop.write_state.side_effect = [OSError("synthetic write failure"), None]
        self.live_token = None
        with self.assertRaises(OSError):
            loop.step()
        self.assertFalse(loop.locked_state_written)
        loop.step()
        loop.step()
        self.assertEqual(loop.write_state.call_count, 2)
        self.assertTrue(loop.locked_state_written)
