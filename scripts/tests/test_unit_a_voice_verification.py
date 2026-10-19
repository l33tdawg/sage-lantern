import importlib.util
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("voice_verification_tests", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Verdict:
    def __init__(self, status, text, seconds=0.5):
        self.status, self.text, self.seconds = status, text, seconds


class FakeVerifier:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = []

    def verify(self, question, answer):
        self.calls.append((question, answer))
        return self.verdict


class VerificationTests(unittest.TestCase):
    def loop(self, *, owner=None, source_id="s-1", session_id="s-1", stop=False):
        loop = MODULE.VoiceLoop.__new__(MODULE.VoiceLoop)
        loop.control = SimpleNamespace(lock=threading.Lock(), owner=owner,
                                       completed_source={"source_id": source_id} if source_id else None,
                                       source_photo=lambda _sid: None)
        loop.device_session = (session_id, "token", False) if session_id else None
        loop.stop_requested = threading.Event()
        if stop:
            loop.stop_requested.set()
        loop.states = []
        loop.write_state = lambda phase, **values: loop.states.append((phase, values))
        return loop

    def test_a_correction_rewrites_the_published_answer(self):
        loop = self.loop()
        loop.verifier = FakeVerifier(Verdict("corrected", "About 500 km."))
        loop.verify_answer("s-1", "How far?", "130 km", ["Why?"])
        self.assertEqual(loop.states, [("complete", {"session_id": "s-1", "transcript": "How far?",
                                                      "response": "About 500 km.", "follow_up_questions": ["Why?"],
                                                      "detail": ""})])

    def test_nothing_is_rewritten_when_the_check_agrees_or_is_unsure(self):
        for status in ("ok", "unsure", "unavailable"):
            loop = self.loop()
            loop.verifier = FakeVerifier(Verdict(status, "130 km"))
            loop.verify_answer("s-1", "How far?", "130 km", [])
            self.assertEqual(loop.states, [])

    def test_a_new_question_or_lock_blocks_the_correction(self):
        for loop in (self.loop(owner={"session_id": "s-2"}),
                     self.loop(source_id="s-2"),
                     self.loop(session_id="s-2"),
                     self.loop(stop=True)):
            loop.verifier = FakeVerifier(Verdict("corrected", "About 500 km."))
            loop.verify_answer("s-1", "How far?", "130 km", [])
            self.assertEqual(loop.states, [])

    def test_a_failing_checker_never_publishes_anything(self):
        class Broken:
            def verify(self, question, answer):
                raise TimeoutError()

        loop = self.loop()
        loop.verifier = Broken()
        with patch("builtins.print"):
            loop.verify_answer("s-1", "How far?", "130 km", [])
        self.assertEqual(loop.states, [])

    def test_scheduling_is_skipped_without_a_verifier_or_answer(self):
        loop = self.loop()
        loop.verifier = None
        loop.schedule_verification("s-1", "Q", "A", [])
        loop.verifier = FakeVerifier(Verdict("corrected", "B"))
        loop.schedule_verification("s-1", "Q", "   ", [])
        with patch.object(MODULE.threading, "Thread") as thread:
            loop.schedule_verification("s-1", "Q", "A", [])
        thread.assert_called_once()
        self.assertFalse(thread.call_args.kwargs["daemon"] is False)
