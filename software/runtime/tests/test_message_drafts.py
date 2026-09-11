import dataclasses
import unittest
from concurrent.futures import ThreadPoolExecutor

from sage_lantern.message_drafts import DraftError, MessageDraft


class MessageDraftTests(unittest.TestCase):
    def setUp(self):
        self.draft = MessageDraft()

    def add(self, text):
        ticket = self.draft.begin_add(expected_revision=self.draft.view().revision)
        return self.draft.commit_segment(ticket, text, expected_revision=ticket.revision)

    def test_exact_stt_and_explicit_paragraph_boundaries(self):
        self.add("  Original punctuation!  ")
        self.add("يفتح يوم الاثنين")
        review = self.draft.review("recipient-id", "key-v1", expected_revision=4)
        self.assertEqual(review.text, "  Original punctuation!  \n\nيفتح يوم الاثنين")
        self.assertFalse(hasattr(review, "sent"))

    def test_replace_only_selected_and_undo(self):
        self.add("first")
        self.add("second")
        ticket = self.draft.begin_replace(0, expected_revision=4)
        self.assertEqual(self.draft.view().paragraphs, ("first", "second"))
        changed = self.draft.commit_segment(ticket, "replacement", expected_revision=5)
        self.assertEqual(changed.paragraphs, ("replacement", "second"))
        self.assertTrue(changed.can_undo)
        self.assertEqual(self.draft.undo(expected_revision=6).paragraphs, ("first", "second"))
        with self.assertRaisesRegex(DraftError, "nothing_to_undo"):
            self.draft.undo(expected_revision=7)

    def test_failed_transcription_cancel_preserves_original(self):
        self.add("original")
        ticket = self.draft.begin_replace(0, expected_revision=2)
        with self.assertRaises(DraftError):
            self.draft.commit_segment(ticket, "", expected_revision=3)
        self.assertEqual(self.draft.view().revision, 3)
        self.assertEqual(self.draft.cancel_segment(ticket, expected_revision=3).paragraphs, ("original",))
        with self.assertRaises(DraftError):
            self.draft.commit_segment(ticket, "late STT", expected_revision=4)

    def test_forged_and_foreign_tickets_rejected(self):
        ticket = self.draft.begin_add(expected_revision=0)
        for invalid in (None, dataclasses.replace(ticket), MessageDraft().begin_add(expected_revision=0)):
            with self.subTest(invalid=type(invalid)):
                with self.assertRaisesRegex(DraftError, "invalid_segment_ticket"):
                    self.draft.commit_segment(invalid, "text", expected_revision=1)

    def test_revision_and_busy_fences(self):
        ticket = self.draft.begin_add(expected_revision=0)
        for revision in (0, True, 1.0, "1"):
            with self.assertRaisesRegex(DraftError, "revision_conflict"):
                self.draft.commit_segment(ticket, "text", expected_revision=revision)
        with self.assertRaisesRegex(DraftError, "segment_in_progress"):
            self.draft.begin_add(expected_revision=1)
        with self.assertRaisesRegex(DraftError, "segment_in_progress"):
            self.draft.review("id", "v1", expected_revision=1)

    def test_invalid_segments_never_mutate(self):
        ticket = self.draft.begin_add(expected_revision=0)
        for text in (None, 123, "", "   ", "a\nb", "a\tb", "\x00", "\ud800", "a\u202eb", "a\u2066b", "a\u2028b", "x" * 2049, "ع" * 1025):
            with self.subTest(kind=type(text), size=len(text) if isinstance(text, str) else 0):
                with self.assertRaises(DraftError):
                    self.draft.commit_segment(ticket, text, expected_revision=1)
                self.assertEqual(self.draft.view().paragraphs, ())
                self.assertEqual(self.draft.view().revision, 1)

    def test_bounds_include_separator_bytes(self):
        for _ in range(3):
            self.add("x" * 2048)
        ticket = self.draft.begin_add(expected_revision=6)
        with self.assertRaisesRegex(DraftError, "message_too_large"):
            self.draft.commit_segment(ticket, "x" * 2048, expected_revision=7)
        self.draft.commit_segment(ticket, "x" * 2042, expected_revision=7)
        review = self.draft.review("id", "v1", expected_revision=8)
        self.assertEqual(len(review.text.encode()), 8192)

    def test_paragraph_count_bound(self):
        for _ in range(32):
            self.add("a")
        with self.assertRaisesRegex(DraftError, "too_many_paragraphs"):
            self.draft.begin_add(expected_revision=64)

    def test_invalid_indexes(self):
        self.add("original")
        for index in (-1, 1, True, 0.0, "0", None):
            with self.assertRaisesRegex(DraftError, "invalid_paragraph"):
                self.draft.begin_replace(index, expected_revision=2)

    def test_review_requires_content_and_bounded_binding(self):
        with self.assertRaisesRegex(DraftError, "empty_draft"):
            self.draft.review("id", "v1", expected_revision=0)
        self.add("private")
        for binding in ("", "x" * 257, "name with space", "ع", "id\n", None):
            with self.assertRaisesRegex(DraftError, "invalid_recipient_binding"):
                self.draft.review(binding, "v1", expected_revision=2)
            with self.assertRaisesRegex(DraftError, "invalid_recipient_binding"):
                self.draft.review("id", binding, expected_revision=2)

    def test_immutable_review_one_shot_confirmation_not_send(self):
        self.add("private")
        review = self.draft.review("id", "v1", expected_revision=2)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            review.recipient_id = "other"
        with self.assertRaisesRegex(DraftError, "invalid_review"):
            self.draft.confirm_review(dataclasses.replace(review), expected_revision=3)
        self.assertIs(self.draft.confirm_review(review, expected_revision=3), review)
        with self.assertRaisesRegex(DraftError, "invalid_review"):
            self.draft.confirm_review(review, expected_revision=4)
        self.assertEqual(self.draft.view().paragraphs, ("private",))
        self.assertNotIn("private", repr(review))
        self.assertNotIn("private", repr(self.draft.view()))

    def test_edit_and_new_recipient_invalidate_review(self):
        self.add("original")
        old = self.draft.review("first", "v1", expected_revision=2)
        new = self.draft.review("second", "v2", expected_revision=3)
        with self.assertRaisesRegex(DraftError, "invalid_review"):
            self.draft.confirm_review(old, expected_revision=4)
        ticket = self.draft.begin_replace(0, expected_revision=4)
        self.draft.cancel_segment(ticket, expected_revision=5)
        with self.assertRaisesRegex(DraftError, "invalid_review"):
            self.draft.confirm_review(new, expected_revision=6)

    def test_lock_clears_text_and_invalidates_late_work_after_unlock(self):
        self.add("private")
        review = self.draft.review("id", "v1", expected_revision=2)
        ticket = self.draft.begin_replace(0, expected_revision=3)
        locked = self.draft.lock()
        self.assertEqual(locked.paragraphs, ())
        self.assertTrue(locked.locked)
        self.assertFalse(locked.pending_segment or locked.can_undo)
        with self.assertRaisesRegex(DraftError, "draft_locked"):
            self.draft.commit_segment(ticket, "late", expected_revision=locked.revision)
        unlocked = self.draft.unlock(expected_revision=locked.revision)
        with self.assertRaisesRegex(DraftError, "invalid_segment_ticket"):
            self.draft.commit_segment(ticket, "late", expected_revision=unlocked.revision)
        with self.assertRaisesRegex(DraftError, "invalid_review"):
            self.draft.confirm_review(review, expected_revision=unlocked.revision)
        self.assertEqual(unlocked.paragraphs, ())

    def test_lock_invalidates_current_review(self):
        self.add("private")
        review = self.draft.review("id", "v1", expected_revision=2)
        locked = self.draft.lock()
        with self.assertRaisesRegex(DraftError, "draft_locked"):
            self.draft.confirm_review(review, expected_revision=locked.revision)
        with self.assertRaisesRegex(DraftError, "revision_conflict"):
            self.draft.unlock(expected_revision=review.revision)

    def test_simultaneous_begin_only_one_wins(self):
        def begin():
            try:
                self.draft.begin_add(expected_revision=0)
                return True
            except DraftError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(lambda _: begin(), range(2))), 1)


if __name__ == "__main__":
    unittest.main()
