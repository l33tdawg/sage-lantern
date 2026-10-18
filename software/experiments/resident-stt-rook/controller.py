"""Disabled resident-ASR lifecycle contract, with an injected backend only.

No audio capture, sockets, model loading library, service wiring or partial
transcript publication. Tests use synthetic PCM and fake backends. A real
Whisper binding and waveform/accuracy qualification are still required.
"""
from dataclasses import dataclass
import math
import re
import threading
import time


class Refused(RuntimeError):
    pass


class Busy(Refused):
    pass


class Cancelled(Refused):
    pass


class NoSpeech(Refused):
    pass


def require(value):
    if value is not True:
        raise Refused('resident_stt_refused')


@dataclass(frozen=True)
class Profile:
    model_sha256: str
    backend_sha256: str
    baseline_parameters_sha256: str
    threads: int = 6
    beam_size: int = 1
    best_of: int = 2

    def validate(self):
        for digest in (self.model_sha256, self.backend_sha256, self.baseline_parameters_sha256):
            require(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest) is not None)
        require(type(self.threads) is int and 1 <= self.threads <= 8)
        require(type(self.beam_size) is int and self.beam_size == 1)
        require(type(self.best_of) is int and self.best_of == 2)
        # The hash binds ALL effective baseline parameters, including implicit
        # backend defaults. These three familiar flags are not a parity proof.


@dataclass(frozen=True)
class Lease:
    owner: str
    session: str
    epoch: int
    request: str
    deadline: float
    sequence: int = 0

    def validate(self, now):
        for value in (self.owner, self.session, self.request):
            require(type(value) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}', value) is not None)
        require(type(self.epoch) is int and self.epoch >= 0)
        require(type(self.sequence) is int and self.sequence >= 0)
        require(type(self.deadline) in (int, float) and math.isfinite(self.deadline))
        require(now < self.deadline <= now + 120)


@dataclass(frozen=True)
class FinalTranscript:
    lease: Lease
    text: str


def transcript(raw):
    require(type(raw) is str and len(raw.encode('utf-8')) <= 4096)
    require(not any(ord(c) < 32 and c not in '\n\t' for c in raw))
    raw = raw.strip()
    annotation = r'(?:music|background music|noise|background noise|silence|blank_audio|inaudible|applause|laughter)'
    spoken = re.sub(r'\[\s*' + annotation + r'\s*\]|\(\s*' + annotation + r'\s*\)', '', raw, flags=re.IGNORECASE)
    if not spoken.strip(' \t\r\n♪♫.,!…'):
        raise NoSpeech('no_spoken_question')
    return raw


class ResidentCandidate:
    """One model context, one active request, no background thread/queue.

    Backend methods: load(profile, abort), transcribe(pcm, profile, abort),
    reset_utterance(), close(). reset must discard per-request decoder/audio
    state while retaining immutable weights. abort polls owner/session/deadline.
    A returned transcript stays bound to its lease; consumers must revalidate
    that lease atomically before using it. Hashes identify a profile but do not
    themselves attest a backend; the trusted composition root owns attestation.
    """
    def __init__(self, backend, profile, authorize, *, enabled=False, clock=time.monotonic):
        require(type(enabled) is bool)
        require(type(profile) is Profile)
        profile.validate()
        require(callable(authorize) and callable(clock))
        self.backend, self.profile, self.authorize, self.clock = backend, profile, authorize, clock
        self.enabled = enabled
        self._mutex = threading.Lock()
        self._active = None
        self._loaded = False
        self._closed = False
        self._closing = False
        self._binding = None
        self._last_sequence = -1
        self._drained = threading.Event()
        self._drained.set()

    @property
    def drained(self):
        return self._drained.is_set()

    def transcribe(self, lease, pcm):
        require(self.enabled is True and type(lease) is Lease)
        lease.validate(self.clock())
        # Already-prepared 16kHz signed-16 stereo PCM from ONE capture owner.
        # Maximum 120s. This module never converts or opens an audio device.
        require(type(pcm) is bytes and 4 <= len(pcm) <= 16000*4*120 and len(pcm)%4 == 0)
        require(self.authorize(lease) is True)
        with self._mutex:
            require(not self._closed)
            if self._active is not None:
                raise Busy('resident_stt_busy')
            binding = (lease.owner, lease.session, lease.epoch)
            require(binding != self._binding or lease.sequence > self._last_sequence)
            self._binding, self._last_sequence = binding, lease.sequence
            cancel = threading.Event()
            self._active = cancel
            self._drained.clear()
        def abort():
            try:
                return (self._closed or cancel.is_set() or self.clock() >= lease.deadline
                        or self.authorize(lease) is not True)
            except Exception:
                return True
        success = False
        cleanup_needed = self._loaded
        try:
            if abort():
                raise Cancelled('resident_stt_cancelled')
            # Exact digital silence contains no represented speech. This is
            # deliberately not an amplitude/noise threshold: every nonzero
            # sample, however quiet, remains eligible for normal decoding.
            if pcm.count(0) == len(pcm):
                raise NoSpeech('silent_pcm')
            if not self._loaded:
                cleanup_needed = True
                self.backend.load(self.profile, abort)
                self._loaded = True
            if abort():
                raise Cancelled('resident_stt_cancelled')
            raw = self.backend.transcribe(pcm, self.profile, abort)
            if abort():
                raise Cancelled('resident_stt_cancelled')
            final = FinalTranscript(lease, transcript(raw))
            if abort():
                raise Cancelled('resident_stt_cancelled')
            success = True
            return final
        except (Cancelled, NoSpeech):
            if cleanup_needed and not self._loaded:
                # An interrupted load may hold partial native resources. It
                # cannot be reused as though a complete model were loaded.
                with self._mutex:
                    self._closed = True
            raise
        except Exception:
            # Unknown backend failures poison the context. Do not retry, reload
            # silently or label a failed model ready for another request.
            with self._mutex:
                self._closed = True
            raise
        finally:
            try:
                if cleanup_needed:
                    self.backend.reset_utterance()
                with self._mutex:
                    closed = self._closed
                    if not closed:
                        # Release atomically with the close check: an idle
                        # close now owns unload; an active close is handled
                        # below. Neither can strand a closed, loaded context.
                        self._active = None
                        self._drained.set()
                if closed:
                    if cleanup_needed:
                        self.backend.close()
                    self._loaded = False
                    with self._mutex:
                        self._active = None
                        self._drained.set()
            except Exception:
                with self._mutex:
                    self._closed = True
                # Reset/unload failure cannot certify drain or return a result.
                raise Refused('resident_stt_reset_failed') from None
            else:
                if success and abort():
                    raise Cancelled('resident_stt_cancelled')

    def cancel(self):
        with self._mutex:
            if self._active is not None:
                self._active.set()
        # A cancellation request is not a drain acknowledgement. Backend work
        # must actually return and reset before drained becomes true.
        return self.drained

    def close(self):
        with self._mutex:
            self._closed = True
            if self._closing:
                return False
            if self._active is not None:
                self._active.set()
                return False
            self._closing = True
            self._drained.clear()
        try:
            if self._loaded:
                self.backend.close()
                self._loaded = False
        except Exception:
            # Unknown unload state stays fenced and cannot acknowledge drain.
            raise Refused('resident_stt_close_failed') from None
        else:
            with self._mutex:
                self._closing = False
                self._drained.set()
        return True
