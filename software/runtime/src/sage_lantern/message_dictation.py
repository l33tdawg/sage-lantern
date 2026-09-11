"""STT-only draft bridge. Caller owns capture, audio cleanup and owner auth.

Run transcribe on the controller's worker, then display its preview and call
accept only on explicit review. No thread, recording, file or model is created
here. Existing WhisperCppSTT handles recognition/no-speech detection; a defensive
marker-only guard covers injected delegates too (no shared validator is exposed).
Audio must be an immutable caller-owned regular file in a trusted directory for
the entire call. Metadata checks detect changes, not hostile same-UID writers.
Cancellation fences completion; it cannot terminate the injected synchronous STT
call. Its timeout/process termination remains the delegate/controller's job.
"""

from __future__ import annotations

import io
import os
import re
import stat
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

from .adapters import NoSpeechDetected, SpeechToText
from .message_drafts import MAX_PARAGRAPH_BYTES, DraftError, DraftView, MessageDraft, SegmentTicket


MAX_AUDIO_SECONDS = 30
MAX_WAV_BYTES = 16000 * 4 * MAX_AUDIO_SECONDS + 65536


class DictationError(ValueError):
    """Non-sensitive controller error code."""


@dataclass(frozen=True, repr=False)
class DictationPreview:
    ticket: SegmentTicket
    text: str


def _fingerprint(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def _validate_wav(path: Path):
    descriptor = None
    try:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 44 <= metadata.st_size <= MAX_WAV_BYTES:
            raise ValueError
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_WAV_BYTES + 1)
        if len(raw) != metadata.st_size:
            raise ValueError
        with wave.open(io.BytesIO(raw), "rb") as recording:
            frames = recording.getnframes()
            if (recording.getnchannels() != 2 or recording.getsampwidth() != 2
                    or recording.getframerate() != 16000 or recording.getcomptype() != "NONE"
                    or not 1 <= frames <= 16000 * MAX_AUDIO_SECONDS
                    or len(recording.readframes(frames)) != frames * 4):
                raise ValueError
        if _fingerprint(path.stat(follow_symlinks=False)) != _fingerprint(metadata):
            raise ValueError
        return _fingerprint(metadata)
    except (OSError, ValueError, EOFError, wave.Error):
        raise DictationError("invalid_audio") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


class MessageDictation:
    def __init__(self, draft: MessageDraft, stt: SpeechToText) -> None:
        self.draft = draft
        self.stt = stt
        self._mutex = threading.Lock()
        self._ticket: SegmentTicket | None = None
        self._preview: DictationPreview | None = None
        self._running = False

    def begin_add(self, *, expected_revision: int) -> SegmentTicket:
        with self._mutex:
            ticket = self.draft.begin_add(expected_revision=expected_revision)
            self._ticket, self._preview = ticket, None
            return ticket

    def begin_replace(self, paragraph_index: int, *, expected_revision: int) -> SegmentTicket:
        with self._mutex:
            ticket = self.draft.begin_replace(paragraph_index, expected_revision=expected_revision)
            self._ticket, self._preview = ticket, None
            return ticket

    def _current(self, ticket: SegmentTicket, expected_revision: int) -> None:
        view = self.draft.view()
        if (ticket is None or ticket is not self._ticket or view.locked
                or type(expected_revision) is not int or expected_revision != view.revision
                or ticket.revision != view.revision or not view.pending_segment):
            raise DictationError("stale_dictation")

    def transcribe(self, ticket: SegmentTicket, audio_path: Path, *, expected_revision: int) -> DictationPreview:
        with self._mutex:
            self._current(ticket, expected_revision)
            if self._running:
                raise DictationError("dictation_busy")
            self._running = True
            self._preview = None
        try:
            fingerprint = _validate_wav(audio_path)
            with self._mutex:
                self._current(ticket, expected_revision)
            try:
                text = self.stt.transcribe(audio_path)
            except NoSpeechDetected:
                raise DictationError("no_speech") from None
            except Exception:
                raise DictationError("transcription_failed") from None
            try:
                if _fingerprint(audio_path.stat(follow_symlinks=False)) != fingerprint:
                    raise DictationError("audio_changed")
            except OSError:
                raise DictationError("audio_changed") from None
            with self._mutex:
                self._current(ticket, expected_revision)
                if type(text) is not str or len(text) > MAX_PARAGRAPH_BYTES:
                    raise DictationError("invalid_transcript")
                annotation = r"(?:music|background music|noise|background noise|silence|blank_audio|inaudible|applause|laughter)"
                spoken = re.sub(r"\[\s*" + annotation + r"\s*\]|\(\s*" + annotation + r"\s*\)", "", text, flags=re.IGNORECASE)
                if not spoken.strip(" \t\r\n♪♫.,!…"):
                    raise DictationError("no_speech")
                try:
                    MessageDraft._text(text)
                except DraftError:
                    raise DictationError("invalid_transcript") from None
                self._preview = DictationPreview(ticket, text)
                return self._preview
        finally:
            with self._mutex:
                self._running = False

    def accept(self, preview: DictationPreview, *, expected_revision: int) -> DraftView:
        with self._mutex:
            if preview is None or preview is not self._preview:
                raise DictationError("invalid_preview")
            self._current(preview.ticket, expected_revision)
            result = self.draft.commit_segment(preview.ticket, preview.text, expected_revision=expected_revision)
            self._ticket, self._preview = None, None
            return result

    def cancel(self, ticket: SegmentTicket, *, expected_revision: int) -> DraftView:
        with self._mutex:
            self._current(ticket, expected_revision)
            result = self.draft.cancel_segment(ticket, expected_revision=expected_revision)
            self._ticket, self._preview = None, None
            return result

    def lock(self) -> DraftView:
        with self._mutex:
            result = self.draft.lock()
            self._ticket, self._preview = None, None
            return result
