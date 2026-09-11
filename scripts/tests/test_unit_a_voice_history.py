import argparse
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("voice_history", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HistoryFixture:
    def setUp(self):
        lock_policy = patch.object(MODULE, "lock_required", return_value=False)
        lock_policy.start()
        self.addCleanup(lock_policy.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve() / "history"
        self.history = MODULE.LocalHistory(self.directory)
        self.result = {"transcript": "Private question", "response": "Private answer"}

    def enable(self):
        return self.history.request("settings", {"enabled": True})

    def add(self, **extra):
        session_id = str(uuid.uuid4())
        self.history.record(self.history.consent(), session_id, {**self.result, **extra})
        return session_id


class HistoryTests(HistoryFixture, unittest.TestCase):
    def test_default_on_durable_opt_out_and_allowlisted_fields(self):
        self.assertEqual(self.history.request("list", {}), {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})
        self.history.request("settings", {"enabled": False})
        self.add()
        self.assertEqual(self.history.request("list", {})["items"], [])
        self.assertEqual(self.enable(), {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})
        session_id = self.add(token="secret", audio="recording.wav", photo="image.jpg")
        response = MODULE.LocalHistory(self.directory).request("list", {})
        self.assertTrue(response["enabled"])
        self.assertEqual(response["items"][0]["session_id"], session_id)
        self.assertEqual(set(response["items"][0]), {"session_id", "created_at", "transcript", "response", "has_photo"})
        self.assertEqual(self.history.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("secret", self.history.path.read_text())

    def test_preservation_capability_is_boolean_and_response_only(self):
        for action, payload in (
            ("list", {}), ("settings", {"enabled": True}),
            ("settings", {"enabled": False}),
            ("delete", {"session_id": str(uuid.uuid4())}), ("clear", {}),
        ):
            with self.subTest(action=action, payload=payload):
                response = self.history.request(action, payload)
                self.assertIs(response["saving_off_keeps_items"], True)
        stored = json.loads(self.history.path.read_text())
        self.assertEqual(set(stored), {"version", "enabled", "photos_enabled", "generation", "items"})
        self.assertEqual(stored["version"], 2)

    def test_enable_is_not_retroactive_and_disable_rotates_consent(self):
        self.history.request("settings", {"enabled": False})
        old_consent = self.history.consent()
        self.enable()
        self.history.record(old_consent, str(uuid.uuid4()), self.result)
        self.assertEqual(self.history.request("list", {})["items"], [])
        consent = self.history.consent()
        self.add()
        items = self.history.request("list", {})["items"]
        self.assertEqual(self.history.request("settings", {"enabled": False}), {"enabled": False, "items": items, "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})
        self.assertIn("Private question", self.history.path.read_text())
        self.assertIsNone(self.history.consent())
        self.assertNotEqual(json.loads(self.history.path.read_text())["generation"], consent)
        self.history.record(consent, str(uuid.uuid4()), self.result)
        self.add()
        self.assertEqual(self.history.request("list", {})["items"], items)
        self.enable()
        self.assertNotEqual(self.history.consent(), consent)
        self.history.record(consent, str(uuid.uuid4()), self.result)
        self.assertEqual(self.history.request("list", {})["items"], items)
        self.add()
        self.assertEqual(len(self.history.request("list", {})["items"]), 2)

    def test_disabled_history_survives_restart_and_repeated_opt_out(self):
        self.enable()
        self.add()
        expected = self.history.request("settings", {"enabled": False})
        self.assertEqual(len(expected["items"]), 1)
        original = self.history.path.read_bytes()
        restarted = MODULE.LocalHistory(self.directory)
        self.assertEqual(restarted.request("list", {}), expected)
        self.assertIsNone(restarted.consent())
        self.assertEqual(restarted.request("settings", {"enabled": False}), expected)
        self.assertEqual(self.history.path.read_bytes(), original)

    def test_explicit_delete_and_clear_work_while_off(self):
        self.enable()
        first = self.add()
        second = self.add()
        consent = self.history.consent()
        self.history.request("settings", {"enabled": False})
        for _ in range(2):
            deleted = self.history.request("delete", {"session_id": first})
            self.assertFalse(deleted["enabled"])
            self.assertEqual([item["session_id"] for item in deleted["items"]], [second])
        generation = json.loads(self.history.path.read_text())["generation"]
        self.assertEqual(self.history.request("clear", {}), {"enabled": False, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})
        self.assertNotEqual(json.loads(self.history.path.read_text())["generation"], generation)
        self.assertNotIn("Private question", self.history.path.read_text())
        self.assertEqual(MODULE.LocalHistory(self.directory).request("list", {}), {"enabled": False, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})
        self.enable()
        self.history.record(consent, str(uuid.uuid4()), self.result)
        self.assertEqual(self.history.request("list", {})["items"], [])

    def test_disabled_history_still_applies_existing_age_retention(self):
        self.enable()
        self.add()
        self.history.request("settings", {"enabled": False})
        with patch.object(MODULE.time, "time", return_value=(datetime.now(UTC) + timedelta(days=31)).timestamp()):
            self.assertEqual(self.history.request("list", {}), {"enabled": False, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})

    def test_delete_clear_and_duplicate_record_are_idempotent(self):
        self.enable()
        session_id = self.add()
        self.history.record(self.history.consent(), session_id, self.result)
        self.assertEqual(len(self.history.request("list", {})["items"]), 1)
        for _ in range(2):
            self.assertEqual(self.history.request("delete", {"session_id": session_id}), {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})
        self.add()
        consent = self.history.consent()
        for _ in range(2):
            self.assertEqual(self.history.request("clear", {}), {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})
        self.history.record(consent, str(uuid.uuid4()), self.result)
        self.assertEqual(self.history.request("list", {})["items"], [])

    def test_entry_and_byte_bounds_remove_oldest(self):
        self.enable()
        first = self.add()
        for _ in range(MODULE.HISTORY_ENTRIES):
            newest = self.add()
        items = self.history.request("list", {})["items"]
        self.assertEqual(len(items), 100)
        self.assertEqual(items[0]["session_id"], newest)
        self.assertNotIn(first, [item["session_id"] for item in items])
        self.history.request("clear", {})
        self.result = {"transcript": "x" * (1024 * 1024), "response": "y" * (1024 * 1024)}
        self.add()
        newest = self.add()
        self.assertLessEqual(self.history.path.stat().st_size, 4 * 1024 * 1024)
        self.assertEqual([item["session_id"] for item in self.history.request("list", {})["items"]], [newest])

    def test_expired_records_are_removed_from_disk(self):
        self.enable()
        old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        with patch.object(MODULE, "stamp", return_value=old):
            self.add()
        self.assertIn("Private question", self.history.path.read_text())
        self.assertEqual(self.history.request("list", {})["items"], [])
        self.assertNotIn("Private question", self.history.path.read_text())

    def test_corrupt_oversized_and_inaccessible_storage_are_503(self):
        self.history.request("list", {})
        for raw in (b"not json", b"[]", b"x" * (MODULE.HISTORY_BYTES + 1)):
            self.history.path.write_bytes(raw)
            with self.assertRaises(MODULE.ControlError) as caught:
                self.history.request("list", {})
            self.assertEqual((caught.exception.status, caught.exception.code), (503, "history_unavailable"))
            self.assertEqual(self.history.path.read_bytes(), raw)
        with patch.object(Path, "mkdir", side_effect=PermissionError("private disk failure")):
            with self.assertRaises(MODULE.ControlError) as caught:
                self.history.request("settings", {"enabled": True})
            self.assertEqual(caught.exception.code, "history_unavailable")

    def test_atomic_failure_preserves_old_document_and_cleans_temporary(self):
        self.enable()
        self.add()
        original = self.history.path.read_bytes()
        with patch.object(MODULE.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(MODULE.ControlError):
                self.history.request("settings", {"enabled": False})
        self.assertEqual(self.history.path.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [self.history.path])

    def test_symlinks_and_public_permissions_rejected(self):
        self.history.request("list", {})
        self.history.path.chmod(0o644)
        with self.assertRaises(MODULE.ControlError):
            self.history.request("list", {})
        self.history.path.unlink()
        target = self.directory.parent / "private-target"
        target.write_text("untouched")
        self.history.path.symlink_to(target)
        with self.assertRaises(MODULE.ControlError):
            self.history.request("clear", {})
        self.assertEqual(target.read_text(), "untouched")


class HistoryHttpTests(HistoryFixture, unittest.TestCase):
    def request(self, action="list", payload=None, *, changes=None, method="POST", raw=None, duplicate=None):
        body = json.dumps({} if payload is None else payload).encode() if raw is None else raw
        headers = {
            "Host": "127.0.0.1:3197", "Origin": "http://127.0.0.1:3100",
            "X-Lantern-Voice": "control", "Content-Type": "application/json", "Content-Length": str(len(body)),
        }
        headers.update(changes or {})
        handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
        handler.headers = Message()
        for key, value in headers.items():
            if value is not None:
                handler.headers[key] = value
        if duplicate:
            handler.headers[duplicate] = headers[duplicate]
        handler.server = SimpleNamespace(history=self.history, control=MODULE.VoiceControl(30))
        handler.path = "/history/" + action
        handler.command = method
        handler.requestline = f"{method} {handler.path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        getattr(handler, "do_" + method)()
        header, content = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        self.assertIn(b"Cache-Control: no-store", header)
        self.assertNotIn(b"Access-Control-Allow-Origin: *", header)
        return int(header.split()[1]), json.loads(content)

    def test_exact_contract(self):
        self.assertEqual(self.request(), (200, {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True}))
        self.assertEqual(self.request("settings", {"enabled": True}), (200, {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True}))
        session_id = self.add()
        self.assertEqual(self.request()[1]["items"][0]["session_id"], session_id)
        self.assertEqual(self.request("delete", {"session_id": session_id}), (200, {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True}))
        self.assertEqual(self.request("clear"), (200, {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True}))
        self.assertEqual(self.request("settings", {"enabled": False}), (200, {"enabled": False, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True}))

    def test_http_opt_out_preserves_items_until_explicit_delete(self):
        self.enable()
        session_id = self.add()
        items = self.request()[1]["items"]
        self.assertEqual(self.request("settings", {"enabled": False}), (200, {"enabled": False, "items": items, "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True}))
        self.assertEqual(self.request(), (200, {"enabled": False, "items": items, "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True}))
        self.assertEqual(self.request("delete", {"session_id": session_id}), (200, {"enabled": False, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True}))

    def test_security_and_payload_validation(self):
        for key, value in (("Origin", None), ("Origin", "null"), ("Origin", "https://evil.example"), ("Host", "192.168.1.2:3197"), ("X-Lantern-Voice", None), ("Content-Type", "text/plain"), ("Transfer-Encoding", "chunked"), ("Content-Length", "513")):
            self.assertIn(self.request(changes={key: value})[0], (400, 403))
        for key in ("Origin", "Host", "Content-Length"):
            self.assertIn(self.request(duplicate=key)[0], (400, 403))
        for action, payload in (("list", {"extra": 1}), ("clear", {"all": True}), ("settings", {"enabled": 1}), ("settings", {}), ("delete", {"session_id": "bad"})):
            self.assertEqual(self.request(action, payload)[0], 400)
        self.assertEqual(self.request("settings", raw=b'{"enabled":true,"enabled":false}')[0], 400)
        self.assertFalse(self.directory.exists())

    def test_preflight_get_and_unavailable(self):
        changes = {"Content-Length": "0", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-lantern-voice"}
        for action in ("list", "settings", "delete", "clear"):
            self.assertEqual(self.request(action, method="OPTIONS", changes=changes)[0], 200)
            self.assertEqual(self.request(action, method="GET", changes={"Content-Length": "0"})[0], 404)
        with patch.object(Path, "mkdir", side_effect=PermissionError()):
            self.assertEqual(self.request(), (503, {"error": "history_unavailable"}))


class HistoryVoiceTests(HistoryFixture, unittest.TestCase):
    def process(self, *, returncode=0, during=None, after_recording_starts=None, on_state=None):
        loop = MODULE.VoiceLoop(argparse.Namespace(
            max_seconds=30, runtime=Path("/mock/runtime"), config=Path("/mock/config"),
            processing_timeout=20, history_dir=self.directory,
        ))
        loop.history = self.history
        loop.write_state = Mock(side_effect=on_state)
        session_id = str(uuid.uuid4())
        loop.begin_history_session(session_id)
        if after_recording_starts:
            after_recording_starts()
        child = Mock(pid=12345)
        child.wait.return_value = returncode
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [True]
        chunks = iter([json.dumps({"type": "result", "result": self.result}).encode() + b"\n", b""])

        def read(*args):
            if during:
                during(loop)
            return next(chunks)

        with ExitStack() as stack:
            stack.enter_context(patch.object(MODULE.subprocess, "Popen", return_value=child))
            stack.enter_context(patch.object(MODULE.selectors, "DefaultSelector", return_value=selector))
            stack.enter_context(patch.object(MODULE.os, "set_blocking"))
            stack.enter_context(patch.object(MODULE.os, "killpg"))
            stack.enter_context(patch.object(MODULE.os, "read", side_effect=read))
            if returncode:
                with self.assertRaises(RuntimeError):
                    loop.process(session_id, Path("input.wav"))
            else:
                loop.process(session_id, Path("input.wav"))
                self.assertEqual(loop.write_state.call_args.args[0], "complete")
        return session_id

    def test_only_future_successful_results_are_saved(self):
        self.history.request("settings", {"enabled": False})
        self.process()
        self.enable()
        self.assertEqual(self.history.request("list", {})["items"], [])
        self.process(returncode=1)
        self.assertEqual(self.history.request("list", {})["items"], [])
        session_id = self.process()
        self.assertEqual(self.history.request("list", {})["items"][0]["session_id"], session_id)

    def test_enabling_after_recording_starts_does_not_save_answer(self):
        self.history.request("settings", {"enabled": False})
        self.process(after_recording_starts=self.enable)
        self.assertEqual(self.history.request("list", {}), {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})
        self.history.request("settings", {"enabled": False})
        self.process(during=lambda loop: self.enable())
        self.assertEqual(self.history.request("list", {}), {"enabled": True, "items": [], "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})

    def test_capture_snapshots_consent_before_spawning_recorder(self):
        self.enable()
        consent = self.history.consent()
        session_id = str(uuid.uuid4())
        loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, history_dir=self.directory, alsa_device="mock"))

        def recorder(*args, **kwargs):
            self.assertEqual(loop.history_ticket, (session_id, consent))
            raise OSError("mock recorder did not start")

        with patch.object(MODULE.subprocess, "Popen", side_effect=recorder):
            with self.assertRaises(OSError):
                loop.capture(session_id, Path("unused.wav"))

    def test_disabling_during_inference_drops_answer_without_control_lock(self):
        self.enable()
        self.add()
        items = self.history.request("list", {})["items"]

        def disable(loop):
            self.assertTrue(loop.control.lock.acquire(blocking=False))
            loop.control.lock.release()
            self.history.request("settings", {"enabled": False})

        self.process(during=disable)
        self.assertEqual(self.history.request("list", {}), {"enabled": False, "items": items, "saving_off_keeps_items": True, "photo_saving_supported": True, "photos_enabled": True})

    def test_history_disk_failures_do_not_fail_successful_voice(self):
        def warned(phase, **values):
            if phase == "complete":
                self.assertEqual(values["detail"], "Could not confirm history saving. Check Past questions.")

        with patch.object(self.history, "load", side_effect=OSError("private history disk error")):
            self.process(on_state=warned)
        self.enable()
        with patch.object(self.history, "save", side_effect=OSError("private history disk error")):
            self.process(on_state=warned)
        self.assertEqual(self.history.request("list", {})["items"], [])

    def test_complete_published_only_after_history_is_readable(self):
        def completed(phase, **values):
            if phase == "complete":
                items = self.history.request("list", {})["items"]
                self.assertEqual(items[0]["session_id"], values["session_id"])
                self.assertNotIn("detail", values)

        self.process(on_state=completed)

    def test_idle_maintenance_prunes_without_microphone(self):
        self.enable()
        with patch.object(MODULE, "stamp", return_value=(datetime.now(UTC) - timedelta(days=31)).isoformat()):
            self.add()
        loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, history_dir=self.directory))
        loop.ptt = Mock(return_value="unknown")
        loop.run_session = Mock()
        loop.step()
        loop.run_session.assert_not_called()
        self.assertNotIn("Private question", self.history.path.read_text())


if __name__ == "__main__":
    unittest.main()
