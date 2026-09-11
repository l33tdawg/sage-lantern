import dataclasses
import tempfile
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sage_lantern.adapters import NoSpeechDetected
from sage_lantern.message_dictation import DictationError, MessageDictation, MAX_WAV_BYTES
from sage_lantern.message_drafts import MessageDraft


class STTStub:
    def __init__(self, result="Exact dictated text."):
        self.result = result
        self.calls = []

    def transcribe(self, path):
        self.calls.append(path)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def respond(self, *args, **kwargs):
        raise AssertionError("LLM forbidden")

    def recall(self, *args, **kwargs):
        raise AssertionError("recall forbidden")


class DictationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.audio = Path(self.temporary.name) / "fixture.wav"
        self.wav()
        self.draft = MessageDraft()
        self.stt = STTStub()
        self.bridge = MessageDictation(self.draft, self.stt)

    def wav(self, channels=2, rate=16000, width=2, frames=160):
        with wave.open(str(self.audio), "wb") as recording:
            recording.setnchannels(channels)
            recording.setsampwidth(width)
            recording.setframerate(rate)
            recording.writeframes(b"\0" * frames * channels * width)

    def original(self):
        ticket = self.draft.begin_add(expected_revision=0)
        self.draft.commit_segment(ticket, "original", expected_revision=1)

    def test_only_stt_called_then_explicit_accept(self):
        original_audio = self.audio.read_bytes()
        ticket = self.bridge.begin_add(expected_revision=0)
        preview = self.bridge.transcribe(ticket, self.audio, expected_revision=1)
        self.assertEqual(self.stt.calls, [self.audio])
        self.assertEqual(self.draft.view().paragraphs, ())
        self.assertEqual(self.bridge.accept(preview, expected_revision=1).paragraphs, (self.stt.result,))
        self.assertEqual(self.audio.read_bytes(), original_audio)
        self.assertEqual(list(self.audio.parent.iterdir()), [self.audio])
        with self.assertRaises(DictationError):
            self.bridge.accept(preview, expected_revision=2)

    def test_replace_cancel_and_undo(self):
        self.original()
        ticket = self.bridge.begin_replace(0, expected_revision=2)
        preview = self.bridge.transcribe(ticket, self.audio, expected_revision=3)
        self.bridge.cancel(ticket, expected_revision=3)
        self.assertEqual(self.draft.view().paragraphs, ("original",))
        with self.assertRaises(DictationError):
            self.bridge.accept(preview, expected_revision=4)
        ticket = self.bridge.begin_replace(0, expected_revision=4)
        preview = self.bridge.transcribe(ticket, self.audio, expected_revision=5)
        self.bridge.accept(preview, expected_revision=5)
        self.assertEqual(self.draft.undo(expected_revision=6).paragraphs, ("original",))

    def test_errors_and_markers_preserve_original(self):
        self.original()
        ticket = self.bridge.begin_replace(0, expected_revision=2)
        for result in (NoSpeechDetected("secret"), RuntimeError("secret"), "", "[MUSIC]", "(background noise) ♪", "[BLANK_AUDIO]", None, "x" * 2049, "bad\ntext"):
            self.stt.result = result
            with self.subTest(result_type=type(result)):
                with self.assertRaises(DictationError) as error:
                    self.bridge.transcribe(ticket, self.audio, expected_revision=3)
                self.assertNotIn("secret", str(error.exception))
                self.assertEqual(self.draft.view().paragraphs, ("original",))
                self.assertEqual(self.draft.view().revision, 3)

    def test_arabic_not_rewritten(self):
        self.stt.result = "  يفتح يوم الاثنين.  "
        ticket = self.bridge.begin_add(expected_revision=0)
        preview = self.bridge.transcribe(ticket, self.audio, expected_revision=1)
        self.assertEqual(preview.text, self.stt.result)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            preview.text = "changed"
        with self.assertRaises(DictationError):
            self.bridge.accept(dataclasses.replace(preview), expected_revision=1)

    def test_invalid_audio_never_calls_stt(self):
        ticket = self.bridge.begin_add(expected_revision=0)
        for kwargs in ({"channels": 1}, {"rate": 8000}, {"width": 1}, {"frames": 0}, {"frames": 480001}):
            self.wav(**kwargs)
            with self.assertRaisesRegex(DictationError, "invalid_audio"):
                self.bridge.transcribe(ticket, self.audio, expected_revision=1)
        for raw in (b"not WAV", b"x" * (MAX_WAV_BYTES + 1)):
            self.audio.write_bytes(raw)
            with self.assertRaises(DictationError):
                self.bridge.transcribe(ticket, self.audio, expected_revision=1)
        self.wav()
        self.audio.write_bytes(self.audio.read_bytes()[:-4])
        with self.assertRaises(DictationError):
            self.bridge.transcribe(ticket, self.audio, expected_revision=1)
        self.assertEqual(self.stt.calls, [])

    def test_symlink_and_relative_path_rejected(self):
        linked = self.audio.parent / "link.wav"
        linked.symlink_to(self.audio)
        ticket = self.bridge.begin_add(expected_revision=0)
        for path in (linked, Path("fixture.wav"), self.audio.parent):
            with self.assertRaises(DictationError):
                self.bridge.transcribe(ticket, path, expected_revision=1)
        self.assertEqual(self.stt.calls, [])

    def test_async_cancel_and_lock_fence_completion(self):
        for lock in (False, True):
            with self.subTest(lock=lock):
                draft = MessageDraft()
                entered, release = threading.Event(), threading.Event()

                class BlockingSTT:
                    def transcribe(self, path):
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError("test timeout")
                        return "late completion"

                bridge = MessageDictation(draft, BlockingSTT())
                ticket = bridge.begin_add(expected_revision=0)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(bridge.transcribe, ticket, self.audio, expected_revision=1)
                    try:
                        self.assertTrue(entered.wait(5))
                        with self.assertRaisesRegex(DictationError, "dictation_busy"):
                            bridge.transcribe(ticket, self.audio, expected_revision=1)
                        if lock:
                            locked = bridge.lock()
                            draft.unlock(expected_revision=locked.revision)
                        else:
                            bridge.cancel(ticket, expected_revision=1)
                        bridge.begin_add(expected_revision=draft.view().revision)
                    finally:
                        release.set()
                    with self.assertRaisesRegex(DictationError, "stale_dictation"):
                        future.result(timeout=5)
                self.assertEqual(draft.view().paragraphs, ())

    def test_direct_draft_lock_invalidates_preview(self):
        ticket = self.bridge.begin_add(expected_revision=0)
        preview = self.bridge.transcribe(ticket, self.audio, expected_revision=1)
        self.draft.lock()
        with self.assertRaisesRegex(DictationError, "stale_dictation"):
            self.bridge.accept(preview, expected_revision=2)

    def test_changed_audio_fails_closed(self):
        class MutatingSTT:
            def transcribe(self, path):
                path.write_bytes(b"changed")
                return "text"
        bridge = MessageDictation(self.draft, MutatingSTT())
        ticket = bridge.begin_add(expected_revision=0)
        with self.assertRaisesRegex(DictationError, "audio_changed"):
            bridge.transcribe(ticket, self.audio, expected_revision=1)
        self.assertEqual(self.draft.view().paragraphs, ())


if __name__ == "__main__":
    unittest.main()
