"""Memory-only STT draft editing; no recognition, persistence, encryption or send.

The caller supplies STT text unchanged and provides touchscreen Record/Stop;
physical PTT may invoke the same operations. Recipient verification, owner
authorization and recording/time limits belong to the caller. Review is not
directory verification. confirm_review returns only a snapshot, never a receipt.
Lock discards local text, not guaranteed Python memory zeroization. A caller
holding an earlier snapshot must clear its display and fence its own transport
on lock: this component cannot revoke copies already handed to another layer.
"""

from __future__ import annotations

import secrets
import threading
import unicodedata
from dataclasses import dataclass


MAX_PARAGRAPHS = 32
MAX_PARAGRAPH_BYTES = 2048
MAX_MESSAGE_BYTES = 8192


class DraftError(ValueError):
    """Stable error code containing no draft or recipient text."""


@dataclass(frozen=True, repr=False)
class DraftView:
    revision: int
    locked: bool
    paragraphs: tuple[str, ...]
    pending_segment: bool
    can_undo: bool


@dataclass(frozen=True, repr=False)
class SegmentTicket:
    token: str
    revision: int
    paragraph_index: int | None


@dataclass(frozen=True, repr=False)
class ReviewedMessage:
    review_id: str
    revision: int
    recipient_id: str
    key_version: str
    paragraphs: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


class MessageDraft:
    """One draft with CAS revisions and opaque, instance-bound segment tickets.

    begin_add/begin_replace return a ticket with the new revision. Commit or
    cancel using that revision. A failed STT job should cancel; invalid text
    leaves its ticket and original paragraph intact for retry or cancel.
    review requires an explicit caller-selected stable recipient ID/key version;
    confirm_review is a separate owner confirmation of that exact review object.
    Neither method verifies recipient identity or queues a message.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._revision = 0
        self._locked = False
        self._paragraphs: tuple[str, ...] = ()
        self._pending: SegmentTicket | None = None
        self._undo: tuple[str, ...] | None = None
        self._review: ReviewedMessage | None = None

    def _check(self, expected_revision: int) -> None:
        if self._locked:
            raise DraftError("draft_locked")
        if type(expected_revision) is not int or expected_revision != self._revision:
            raise DraftError("revision_conflict")

    def _idle(self) -> None:
        if self._pending is not None:
            raise DraftError("segment_in_progress")

    def _view(self) -> DraftView:
        return DraftView(self._revision, self._locked, self._paragraphs,
                         self._pending is not None, self._undo is not None)

    def view(self) -> DraftView:
        with self._mutex:
            return self._view()

    @staticmethod
    def _text(text: str) -> None:
        if type(text) is not str or not text.strip() or len(text) > MAX_PARAGRAPH_BYTES:
            raise DraftError("invalid_segment")
        if any(unicodedata.category(character) in {"Cc", "Cs", "Zl", "Zp"}
               or unicodedata.bidirectional(character) in
               {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
               for character in text):
            raise DraftError("invalid_segment")
        if len(text.encode("utf-8")) > MAX_PARAGRAPH_BYTES:
            raise DraftError("segment_too_large")

    @staticmethod
    def _identity(value: str) -> None:
        if (type(value) is not str or not 1 <= len(value) <= 256
                or not value.isascii() or any(not 33 <= ord(character) <= 126 for character in value)):
            raise DraftError("invalid_recipient_binding")

    def _begin(self, index: int | None) -> SegmentTicket:
        self._revision += 1
        self._review = None
        self._pending = SegmentTicket(secrets.token_hex(16), self._revision, index)
        return self._pending

    def begin_add(self, *, expected_revision: int) -> SegmentTicket:
        with self._mutex:
            self._check(expected_revision)
            self._idle()
            if len(self._paragraphs) >= MAX_PARAGRAPHS:
                raise DraftError("too_many_paragraphs")
            return self._begin(None)

    def begin_replace(self, paragraph_index: int, *, expected_revision: int) -> SegmentTicket:
        with self._mutex:
            self._check(expected_revision)
            self._idle()
            if type(paragraph_index) is not int or not 0 <= paragraph_index < len(self._paragraphs):
                raise DraftError("invalid_paragraph")
            return self._begin(paragraph_index)

    def _ticket(self, ticket: SegmentTicket) -> None:
        if ticket is not self._pending or ticket is None:
            raise DraftError("invalid_segment_ticket")

    def commit_segment(self, ticket: SegmentTicket, text: str, *, expected_revision: int) -> DraftView:
        """Apply explicitly reviewed STT text; never auto-call on recognition finish."""
        with self._mutex:
            self._check(expected_revision)
            self._ticket(ticket)
            self._text(text)
            paragraphs = list(self._paragraphs)
            if ticket.paragraph_index is None:
                paragraphs.append(text)
            else:
                paragraphs[ticket.paragraph_index] = text
            if len("\n\n".join(paragraphs).encode("utf-8")) > MAX_MESSAGE_BYTES:
                raise DraftError("message_too_large")
            self._undo = self._paragraphs if ticket.paragraph_index is not None else None
            self._paragraphs = tuple(paragraphs)
            self._pending = None
            self._revision += 1
            return self._view()

    def cancel_segment(self, ticket: SegmentTicket, *, expected_revision: int) -> DraftView:
        with self._mutex:
            self._check(expected_revision)
            self._ticket(ticket)
            self._pending = None
            self._revision += 1
            return self._view()

    def undo(self, *, expected_revision: int) -> DraftView:
        with self._mutex:
            self._check(expected_revision)
            self._idle()
            if self._undo is None:
                raise DraftError("nothing_to_undo")
            self._paragraphs, self._undo = self._undo, None
            self._review = None
            self._revision += 1
            return self._view()

    def review(self, recipient_id: str, key_version: str, *, expected_revision: int) -> ReviewedMessage:
        with self._mutex:
            self._check(expected_revision)
            self._idle()
            self._identity(recipient_id)
            self._identity(key_version)
            if not self._paragraphs:
                raise DraftError("empty_draft")
            self._revision += 1
            self._review = ReviewedMessage(secrets.token_hex(16), self._revision,
                                           recipient_id, key_version, self._paragraphs)
            return self._review

    def confirm_review(self, review: ReviewedMessage, *, expected_revision: int) -> ReviewedMessage:
        with self._mutex:
            self._check(expected_revision)
            self._idle()
            if review is not self._review or review is None:
                raise DraftError("invalid_review")
            self._review = None
            self._undo = None
            self._revision += 1
            return review

    def lock(self) -> DraftView:
        """Unconditionally invalidate work; safety lock never needs a fresh revision."""
        with self._mutex:
            self._locked = True
            self._paragraphs = ()
            self._pending = None
            self._undo = None
            self._review = None
            self._revision += 1
            return self._view()

    def unlock(self, *, expected_revision: int) -> DraftView:
        """Caller must authenticate owner first; starts empty after lock."""
        with self._mutex:
            if type(expected_revision) is not int or expected_revision != self._revision:
                raise DraftError("revision_conflict")
            if not self._locked:
                raise DraftError("draft_not_locked")
            self._locked = False
            self._revision += 1
            return self._view()
