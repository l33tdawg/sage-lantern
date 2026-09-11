import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4

from sage_lantern.conversation_service import ConversationService
from sage_lantern.conversation_store import ConversationStore


SPEC = importlib.util.spec_from_file_location("conversation_voice_loop", Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ConversationHistoryTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.store = ConversationStore(self.root / "memory")
        self.service = ConversationService(self.store, None)
        MODULE.LocalHistory(self.root / "history").request("list", {})
        self.history = MODULE.LocalHistory(self.root / "history", self.service)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def save_question(self):
        session_id = str(uuid4())
        self.history.record(self.history.consent(), session_id, {"transcript": "My test topic is cedar", "response": "We discussed cedar."})
        return session_id

    def test_history_projects_durable_conversation_queue_not_json(self):
        session_id = self.save_question()
        response = self.history.request("list", {})
        self.assertTrue(response["sage_backed"])
        self.assertEqual(response["items"][0]["session_id"], session_id)
        self.assertEqual(response["items"][0]["memory_state"], "queued")
        self.assertEqual(self.history.load()["items"], [])
        self.assertEqual(len(self.store.pending()), 2)

    def test_reboot_preserves_queue_and_history(self):
        session_id = self.save_question()
        self.store.close()
        self.store = ConversationStore(self.root / "memory")
        self.service = ConversationService(self.store, None)
        self.history = MODULE.LocalHistory(self.root / "history", self.service)
        self.history.consent()
        self.assertEqual(self.history.request("list", {})["items"][0]["session_id"], session_id)
        self.assertEqual(len(self.store.pending()), 2)

    def test_off_does_not_remove_history_and_blocks_inflight(self):
        session_id = self.save_question()
        ticket = self.history.consent()
        self.history.request("settings", {"enabled": False})
        self.history.record(ticket, str(uuid4()), {"transcript": "unsaved", "response": "unsaved"})
        self.assertEqual([item["session_id"] for item in self.history.request("list", {})["items"]], [session_id])
        self.assertEqual(self.store.pending(), [])

    def test_remove_persists_suppression_and_blocks_inflight(self):
        session_id = self.save_question()
        ticket = self.history.consent()
        self.history.request("delete", {"session_id": session_id})
        self.assertEqual(self.history.request("list", {})["items"], [])
        self.assertIn(session_id, self.store.suppressed_ids())
        self.history.record(ticket, str(uuid4()), {"transcript": "late", "response": "late"})
        self.assertEqual(self.store.pending(), [])

    def test_clear_suppresses_every_saved_conversation(self):
        identifiers = {self.save_question(), self.save_question()}
        self.history.request("clear", {})
        self.assertEqual(self.store.suppressed_ids(), identifiers)
        self.assertEqual(self.history.request("list", {})["items"], [])

    def test_missing_preferences_fail_closed_without_resetting_optout(self):
        self.history.request("settings", {"enabled": False})
        self.history.path.unlink()
        with self.assertRaises(MODULE.ControlError):
            self.history.consent()
        self.assertIsNone(self.store.consent())
