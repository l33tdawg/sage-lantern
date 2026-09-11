import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from sage_lantern.conversation_service import ConversationService, _DeferredConversationStore


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("voice_locked_startup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LockedStartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "history"
        self.directory.mkdir(mode=0o700)
        self.generation = str(uuid.uuid4())
        self.backend = Mock()
        self.store = Mock()
        self.factory = Mock(return_value=self.store)
        self.memory = ConversationService(None, self.backend, media_store=Mock())
        self.memory.protected_storage = True
        self.memory._owner = Mock()
        self.memory._owner.valid.return_value = True
        self.memory._epoch_reader = Mock(return_value=None)
        self.memory.store = _DeferredConversationStore(self.factory, activity=self.memory._activity)
        self.loop = MODULE.VoiceLoop(
            argparse.Namespace(max_seconds=1, history_dir=self.directory), history_memory=self.memory)

    def preferences(self, enabled):
        raw = json.dumps(dict(version=2, enabled=enabled, photos_enabled=False,
                              generation=self.generation, items=[])).encode()
        self.loop.history.path.write_bytes(raw)
        self.loop.history.path.chmod(0o600)
        return raw

    def run_once(self):
        server = Mock()
        thread = Mock()
        thread.is_alive.return_value = True
        with patch.object(MODULE, "VoiceHTTPServer", return_value=server) as listener, \
                patch.object(MODULE.threading, "Thread", return_value=thread), \
                patch.dict(MODULE.os.environ, {"SAGE_LANTERN_MESH_BROKER": "0"}), \
                patch.object(self.memory, "start") as start, \
                patch.object(self.loop, "write_state"), \
                patch.object(self.loop, "drain_shutdown"), \
                patch.object(self.loop, "step", side_effect=self.loop.request_stop), \
                patch.object(MODULE.time, "sleep"):
            self.loop.run()
        listener.assert_called_once_with(("127.0.0.1", 3197), self.loop.control, self.loop.history)
        start.assert_called_once_with()

    def test_locked_start_reaches_listener_without_personal_io_or_preference_changes(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.loop.stop_requested.clear()
                raw = self.preferences(enabled)
                with patch.object(self.loop.history, "load", side_effect=AssertionError("personal read")):
                    self.run_once()
                self.assertEqual(self.loop.history.path.read_bytes(), raw)
                self.factory.assert_not_called()
                self.assertEqual(self.store.mock_calls, [])
                self.assertEqual(self.backend.mock_calls, [])

    def test_missing_preferences_are_not_created_or_approved_at_start(self):
        self.run_once()
        self.assertFalse(self.loop.history.path.exists())
        self.memory._epoch_reader.return_value = "a" * 32
        self.loop.begin_history_session("session")
        self.assertEqual(self.loop.history_start_failed, "session")
        self.assertEqual(self.loop.history_ticket, ("session", None))
        self.factory.assert_not_called()

    def test_deferred_consent_keeps_saved_enabled_generation_and_rejects_relock(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                raw = self.preferences(enabled)
                self.store.reset_mock()
                self.memory._epoch_reader.return_value = None
                with self.assertRaises(MODULE.ControlError):
                    self.loop.history.consent()
                self.store.approve.assert_not_called()
                self.memory._epoch_reader.return_value = "a" * 32
                self.store.consent.return_value = self.generation if enabled else None
                self.loop.begin_history_session("session")
                self.store.approve.assert_called_once_with(enabled, history_generation=self.generation)
                self.assertEqual(self.loop.history_ticket, ("session", self.generation if enabled else None))
                self.assertEqual(self.loop.history.path.read_bytes(), raw)
                self.memory._epoch_reader.return_value = None
                self.store.reset_mock()
                self.loop.begin_history_session("relocked")
                self.assertEqual(self.loop.history_start_failed, "relocked")
                self.assertEqual(self.loop.history_ticket, ("relocked", None))
                self.store.approve.assert_not_called()

    def test_locked_memory_worker_does_not_initialize_or_authorize_storage(self):
        with patch.object(self.memory.stopped, "wait", side_effect=lambda timeout: self.memory.stopped.set()):
            self.memory._run()
        self.factory.assert_not_called()
        self.assertEqual(self.backend.mock_calls, [])
        self.assertEqual(self.store.mock_calls, [])

    def test_legacy_start_preserves_existing_eager_consent(self):
        self.memory.protected_storage = False
        with patch.object(self.loop.history, "consent") as consent:
            self.run_once()
        consent.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
