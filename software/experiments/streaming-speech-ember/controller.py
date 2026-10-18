"""Disabled streaming-speech controller: generated text, approved text, playback.

**Not wired into the runtime. No audio device is opened. No runtime module is
imported.** The playback sink is injected by the caller and is expected to be a
mock in this phase.

The whole point of the separation is one rule: with verification enabled, a
sentence may not be spoken while a later verdict could still replace it. Because
the runtime's verifier only accepts a *complete* answer
(``AnswerVerifier.verify(question, answer)``), that rule makes streaming and
enabled verification mutually exclusive at this layer. The controller enforces
it rather than pretending otherwise: while ``policy.verify`` is true nothing is
released, the draft is buffered under a byte bound, and the verdict decides what
may be spoken. ``policy.VERDICT_RELEASE`` records the mapping, which mirrors the
runtime's existing rule that only ``corrected`` replaces the draft.

Three states are kept apart on purpose:

* **generated** - ``generated``, whatever the model has produced so far.
* **approved** - what verification has cleared. Same as generated when
  verification is off; frozen at the verdict when it is on.
* **playback** - ``state``, ``spoken_items`` and the sink's own record. A release
  is not playback; only the sink starting an item is.

Cancellation stops the active sink *and* drains what was pending. Emptying a
queue while the sink keeps talking is not cancellation, so ``stop()`` is always
called on the sink when anything is in flight.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import Enum

from .policy import DEFAULT_POLICY, VERDICT_RELEASE, VERDICTS, SpeechPolicy
from .segmenter import Segmenter


class SpeechRefused(RuntimeError):
    """An expected refusal: the caller asked for something the session forbids."""


class InvalidFacts(ValueError):
    """Malformed caller facts. Deliberately not a SpeechRefused.

    A caller bug must never be readable as a speech outcome, so the two are
    different types.
    """


class PlaybackState(str, Enum):
    IDLE = "idle"
    BUFFERING = "buffering"
    SPEAKING = "speaking"
    DRAINED = "drained"
    STOPPED = "stopped"
    FENCED = "fenced"
    #: A stop was attempted and the sink refused it. Playback may still be
    #: running, so neither STOPPED nor DRAINED may be reported: those say the
    #: audio positively ended, and this says only that we asked it to.
    STOP_UNCERTAIN = "stop_uncertain"


class FenceReason(str, Enum):
    CANCELLED = "cancelled"
    OWNER_CHANGED = "owner_changed"
    LOCKED = "locked"
    SLEEPING = "sleeping"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    SINK_FAILED = "sink_failed"
    CALLER_FENCED = "caller_fenced"


@dataclass(frozen=True)
class SessionFacts:
    """The authoritative session, named after the ones the runtime already has.

    session_id : the question flow's ``current_session`` (orchestrator._run),
                 the id its state transitions and its output file are keyed on.
    epoch      : ``SessionContext.epoch`` (session_context.py), the 32-hex value
                 the auth broker issues and rotates. A rotation means the device
                 session this work belonged to is gone.
    owner      : the device-lock owner identity. An owner change is a fence, not
                 a credential: this carries no authority of its own.

    These are facts the caller supplies. Nothing here issues, fetches, validates
    or stores a session against the runtime.
    """

    session_id: str
    owner: str
    epoch: str


@dataclass(frozen=True)
class LifecycleFacts:
    cancelled: bool = False
    locked: bool = False
    sleeping: bool = False
    elapsed_seconds: float = 0.0


class SpeechItem(str):
    """One queued emission. A ``str`` so a retained item stays serialisable.

    ``item_id`` and ``text`` are the interface. The ``str`` value is the item's
    text, which means a controller that must retain an unresolved obligation can
    expose it as `inflight` without handing a reporter an object it cannot
    render or a reviewer a state that cannot be written down.

    The fields are set once and then frozen. `item_id` names one playback
    obligation and `completed(item_id)` matches on it, so a record whose id can
    be reassigned after a completion was issued makes the real callback
    unrecognisable: the obligation would stay open forever while the caller read
    a record naming a different item. `text` is frozen for the same reason - the
    sink was handed exactly these characters, and a report rendering different
    ones describes playback that never happened.

    Immutability is enforced here rather than by dropping the ``str`` base. A
    reporter that cannot render this object is a serialisation problem, and the
    fix for it belongs in the reporter or in an explicit value adapter, not in
    the controller's playback identity.
    """

    __slots__ = ("item_id", "text", "slot")

    def __new__(cls, item_id, text, slot=0):
        self = super().__new__(cls, text)
        # object.__setattr__ is used because this class refuses `setattr` at
        # every other point in its life; the slots are filled exactly once here.
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "text", text)
        # The admission ordinal. The queue and the held buffer are two
        # destinations for one ordered stream, so playback order cannot be
        # recovered from which container an item landed in - a newer segment can
        # be queued while an older one is still held, and draining the queue
        # first would play it out of order. The ordinal is what carries the
        # caller's emission order across both.
        object.__setattr__(self, "slot", slot)
        return self

    def __setattr__(self, name, value):
        raise AttributeError(
            f"SpeechItem is immutable; {name} cannot be reassigned")

    def __delattr__(self, name):
        raise AttributeError(
            f"SpeechItem is immutable; {name} cannot be deleted")

    def __repr__(self):
        return f"SpeechItem(item_id={self.item_id}, text={self.text!r})"


@dataclass(frozen=True)
class SpeechOutcome:
    status: str  # accepted | duplicate | backpressure | unapproved | fenced | ignored
    reason: str
    released: tuple = ()
    queue_depth: int = 0
    held_items: int = 0
    state: str = PlaybackState.IDLE.value
    #: True when this call carried an explicit upstream emission identity, so a
    #: retry was recognised from that identity rather than from token text.
    explicit_identity: bool = False


def _require_str(name, value):
    if type(value) is not str or not value:
        raise InvalidFacts(f"{name} must be a non-empty str")
    return value


def validate_session(session):
    if type(session) is not SessionFacts:
        raise InvalidFacts("session must be SessionFacts")
    _require_str("session.session_id", session.session_id)
    _require_str("session.owner", session.owner)
    _require_str("session.epoch", session.epoch)
    return session


def validate_lifecycle(lifecycle):
    if type(lifecycle) is not LifecycleFacts:
        raise InvalidFacts("lifecycle must be LifecycleFacts")
    for name in ("cancelled", "locked", "sleeping"):
        if type(getattr(lifecycle, name)) is not bool:
            raise InvalidFacts(f"lifecycle.{name} must be a bool")
    elapsed = lifecycle.elapsed_seconds
    if type(elapsed) is not float or elapsed != elapsed or elapsed in (
            float("inf"), float("-inf")) or elapsed < 0.0:
        raise InvalidFacts("lifecycle.elapsed_seconds must be a finite float >= 0")
    return lifecycle


class StreamingSpeechController:
    def __init__(self, sink, *, session, policy=DEFAULT_POLICY):
        validate_session(session)
        if type(policy) is not SpeechPolicy:
            raise InvalidFacts("policy must be a SpeechPolicy")
        for name in ("speak", "stop"):
            if not callable(getattr(sink, name, None)):
                raise InvalidFacts(f"sink must provide a callable {name}()")
        self._sink = sink
        self._session = session
        self._policy = policy
        self._segmenter = Segmenter(max_bytes=policy.segment_max_bytes)
        self._queue = deque()
        self._queue_bytes = 0
        self._held = deque()
        self._held_bytes = 0
        self._inflight = None
        self._next_item = 1
        #: The admission ordinal, independent of the item id: it is what orders
        #: playback across the queue and the held buffer.
        self._next_slot = 1
        self._generated = ""
        self._last_delta = None
        #: The upstream identity of the last accepted delta, when it supplied one.
        self._last_emission_id = None
        #: Segments a refused release could not place, retained rather than lost.
        self._last_released = []
        #: Every segment of an approved answer, and how many have been placed.
        #: Kept so a bounded release can resume instead of restarting or dying.
        self._approved_segments = None
        self._release_cursor = 0
        self._spoken_items = []
        self._state = PlaybackState.IDLE
        self._fence = None
        self._frozen = False
        self._verdict_status = None
        self._stop_calls = 0
        self._stop_failures = 0
        # The item still owed a stop: set only when the sink refused to stop, so
        # an unresolved obligation is distinguishable from a completed drain.
        self._stop_unresolved = None
        self._discarded_bytes = 0
        # Text that was complete and current but never approved to speak. Kept
        # separate from `_discarded_bytes`, which counts superseded text: these
        # bytes were never rejected as *text*, only as speech. An approved answer
        # is deliberately not counted here - see `_approved_unplaced_bytes`.
        self._unapproved_bytes = 0
        # Approved text the bounds have not let play yet. It is reported on its
        # own because the two are different facts: this text was cleared to
        # speak, and calling it "unapproved" told an operator an answer had been
        # rejected when the verifier had in fact accepted it.
        self._approved_unplaced_bytes = 0
        self._unspeakable_text = ""
        self._sink_error = ""
        self._drained_on_fence = 0

    # --- read-only evidence -------------------------------------------------

    @property
    def state(self):
        return self._state

    @property
    def generated(self):
        return self._generated

    @property
    def spoken_items(self):
        return tuple(self._spoken_items)

    @property
    def queue_depth(self):
        return len(self._queue)

    @property
    def held_items(self):
        return len(self._held)

    @property
    def inflight(self):
        return self._inflight

    @property
    def fence_reason(self):
        return self._fence

    @property
    def verdict_status(self):
        return self._verdict_status

    @property
    def stop_calls(self):
        return self._stop_calls

    @property
    def stop_failures(self):
        return self._stop_failures

    @property
    def stop_unresolved(self):
        """The item still owed a stop, or None once a stop positively completed."""
        return self._stop_unresolved

    @property
    def discarded_bytes(self):
        return self._discarded_bytes

    @property
    def unapproved_bytes(self):
        """Text the verifier never cleared to speak. Not an approved answer's
        unplaced remainder: that is `approved_unplaced_bytes`."""
        return self._unapproved_bytes

    @property
    def approved_unplaced_bytes(self):
        """Approved text the bounds have not let play yet, reported separately
        from `unapproved_bytes` so an accepted answer is never described as a
        rejected one."""
        return self._approved_unplaced_bytes

    @property
    def last_released(self):
        """Segments a refused release could not place, so nothing looks dropped."""
        return tuple(self._last_released)

    @property
    def drained_on_fence(self):
        return self._drained_on_fence

    @property
    def unspeakable_text(self):
        """Text a sink failure prevented from being spoken, kept for the caller."""
        return self._unspeakable_text

    @property
    def sink_error(self):
        return self._sink_error

    @property
    def pending_text(self):
        return self._segmenter.pending

    # --- events -------------------------------------------------------------

    def feed(self, text, *, session, lifecycle, mode="delta", emission_id=None):
        """Accept generated text. Returns an outcome or raises SpeechRefused.

        ``mode='snapshot'`` treats ``text`` as the whole answer so far, so a
        repeated identical snapshot is a duplicate, a shorter one is a stale
        rewind, and an unrelated one is divergent. ``mode='delta'`` appends.

        ``emission_id`` is the upstream's own stable identifier for this
        emission, and it is the only sound basis for retry suppression. Without
        it a delta that merely repeats the previous text is treated as a repeat
        of that text, which cannot distinguish a retry from a legitimately
        repeated token: upstream sending "Echo. " twice on purpose loses the
        second one. Supplying an identity makes the decision exactly as good as
        the caller's own bookkeeping - the same id twice is a retry, two ids are
        two emissions however identical their text - and the controller stops
        inferring delivery from content.
        """
        if type(text) is not str:
            raise InvalidFacts("text must be a str")
        if mode not in ("delta", "snapshot"):
            raise InvalidFacts("mode must be 'delta' or 'snapshot'")
        if emission_id is not None and (type(emission_id) is not str or not emission_id):
            raise InvalidFacts("emission_id must be a non-empty str")
        guarded = self._guard(session, lifecycle)
        if guarded is not None:
            return guarded
        if self._frozen:
            raise SpeechRefused("answer_frozen")
        identity = emission_id is not None

        def outcome(status, reason, released=()):
            record = self._outcome(status, reason, released)
            return replace(record, explicit_identity=True) if identity else record

        delta = self._reconcile(text, mode, emission_id)
        if not delta:
            return outcome("duplicate", "duplicate_" + mode)

        if self._policy.verify:
            # Nothing may be spoken before the verdict, so this is a draft buffer
            # and it is bounded like one.
            if len(self._generated.encode("utf-8")) + len(delta.encode("utf-8")) > \
                    self._policy.draft_max_bytes:
                return outcome("backpressure", "draft_budget_exceeded")
            self._generated += delta
            self._last_delta = delta
            self._commit_emission(emission_id)
            self._state = PlaybackState.BUFFERING
            return outcome("accepted", "")

        # Without verification a delta may be released, but only if everything
        # already buffered plus this delta is guaranteed to fit in held, so no
        # segmentation of it can overflow the bound. The delta is not consumed
        # when it would not fit: the caller retries the same text later.
        if not self._has_room_for(delta):
            return outcome("backpressure", "queue_and_held_full")
        self._generated += delta
        self._last_delta = delta
        released = self._segmenter.push(delta)
        if not self._release(released):
            self._last_released = list(released)
            return outcome("backpressure", "release_exceeds_bounds")
        self._commit_emission(emission_id)
        return outcome("accepted", "", released)

    def finish(self, *, session, lifecycle):
        """End of answer: flush the final segment through the same gate.

        The streaming path releases a sentence when its terminator arrives, so a
        complete final sentence with no trailing whitespace has nothing to
        trigger it and waits forever. This is that missing event. It is guarded
        like every other entry point, it refuses once the answer is frozen, and
        it flushes through the ordinary admission and bounding path rather than
        bypassing it - so a finish can be refused for room exactly as a delta
        can.
        """
        guarded = self._guard(session, lifecycle)
        if guarded is not None:
            return guarded
        if self._frozen:
            raise SpeechRefused("answer_frozen")
        if self._policy.verify:
            # With verification enabled a verdict is the end of the answer. A
            # caller-driven finish here would be a second, weaker release path
            # around the gate this mode exists to enforce.
            raise SpeechRefused("verification_pending")
        remainder = self._segmenter.pending
        released = self._segmenter.flush()
        if not self._release(released):
            self._last_released = list(released)
            return self._outcome("backpressure", "release_exceeds_bounds")
        if not released and not remainder:
            return self._outcome("ignored", "nothing_pending")
        return self._outcome("accepted", "finished", released)

    def verdict(self, status, text, *, session, lifecycle):
        """Apply the verifier's verdict and release what it clears."""
        guarded = self._guard(session, lifecycle)
        if guarded is not None:
            return guarded
        if not self._policy.verify:
            raise SpeechRefused("verification_disabled")
        retry = self._approved_segments is not None and self._release_cursor < len(
            self._approved_segments)
        if self._frozen and not retry:
            raise SpeechRefused("answer_frozen")
        if retry:
            # The verdict was already accepted and only part of the approved
            # answer fitted its bounds. A retry re-offers the remainder; it does
            # not re-evaluate approval, because the verifier's decision is not
            # something a bound can revise.
            return self._retry_approved_release()
        if status not in VERDICTS:
            raise InvalidFacts("unknown verdict status")
        if type(text) is not str:
            raise InvalidFacts("verdict text must be a str")
        self._verdict_status = status
        release = VERDICT_RELEASE[status]
        if release is None:
            # The verifier reached no verdict, so nothing was approved and the
            # draft must not be spoken. This is not the same as "the draft
            # survives the verdict" - surviving is a statement about which text
            # is current, and speaking it would read "could not check" as
            # "passed". The bytes are accounted rather than discarded silently,
            # and the session still freezes so no later delta can slip out.
            self._unapproved_bytes += len(self._generated.encode("utf-8"))
            self._frozen = True
            return self._outcome("unapproved", "verdict_" + status, ())
        if release == "corrected":
            # The replacement's size is checked *before* anything mutates. The
            # caller injecting this text is not the party enforcing draft bounds,
            # and a correction is new input that the draft budget was never
            # applied to: without this check a 6144-byte correction is accepted
            # under a 64-byte draft budget and replaces the draft wholesale.
            corrected_bytes = len(text.encode("utf-8"))
            if corrected_bytes > self._policy.draft_max_bytes:
                return self._outcome("backpressure", "corrected_budget_exceeded", ())
            # The draft is discarded, not spoken. Nothing was released while the
            # verdict was outstanding, so this is a structural guarantee here.
            self._discarded_bytes = len(self._generated.encode("utf-8"))
            self._generated = text
        approved = self._generated
        self._frozen = True
        # The verdict arrives after the answer is complete, so this is also the
        # end-of-answer signal that flushes the final sentence.
        segments = self._segmenter.push(approved) + self._segmenter.flush()
        self._approved_segments = list(segments)
        self._release_cursor = 0
        # The verifier cleared this text, so none of it is unapproved. Any count
        # left by an earlier unavailable verdict described different text, and an
        # approved answer must never inherit it.
        self._unapproved_bytes = 0
        # Nothing plays unless the whole approved answer fits. Partial playback of
        # an answer the bounds cannot hold would be a silent truncation dressed as
        # a release; the retained list plus the retry contract is the honest form
        # of "approved, not yet speakable". The cursor stays at zero on a refusal,
        # so a retry places the whole answer from the beginning once the sink has
        # drained enough to hold it. The shared helper keeps this verdict and its
        # retry on one contract.
        outcome = self._release_approved_answer(self._approved_segments)
        if outcome.status != "accepted":
            return outcome
        return self._outcome("accepted", "verdict_" + status, segments)

    def _fits_entirely(self, segments):
        """Whether every segment can be placed right now, using the placement rules.

        An approved answer is released as a unit, so its feasibility is decided
        before the first segment moves. This mirrors `_place` exactly - same
        queue ceilings, same direct handover for a segment wider than the queue,
        same held bound - so the pre-check cannot disagree with the placement it
        authorises.
        """
        queue_items = 0
        queue_bytes = 0
        held_bytes = 0
        inflight = self._inflight is not None
        for text in segments:
            if not text:
                continue
            size = len(text.encode("utf-8"))
            if (queue_items < self._policy.queue_max_items
                    and queue_bytes + size <= self._policy.queue_max_bytes):
                queue_items += 1
                queue_bytes += size
                continue
            if not inflight and not queue_items and not held_bytes:
                if size > self._policy.queue_max_bytes:
                    inflight = True
                    continue
            if held_bytes + size <= self._policy.held_max_bytes:
                held_bytes += size
                continue
            return False
        return True

    def _release_progress(self):
        """Place as much of the approved answer as the bounds allow, and say so.

        Placed segments leave the retained list as they are accepted, so the
        cursor is both the progress record and the resume point. A segment that
        does not fit stops the walk without being consumed, which is what makes
        the next call continue rather than restart.
        """
        segments = self._approved_segments
        if segments is None:
            return True
        while self._release_cursor < len(segments):
            text = segments[self._release_cursor]
            if text:
                # `_place` may start playback directly, so a refusal has to roll
                # the pending state back itself rather than leaving a segment
                # that reached the sink counted as still pending.
                completed_before = len(self._spoken_items)
                queue_before = deque(self._queue)
                queue_bytes_before = self._queue_bytes
                held_before = deque(self._held)
                held_bytes_before = self._held_bytes
                if not self._place(text):
                    if self._fence is None and len(self._spoken_items) != completed_before:
                        self._inflight = None
                        self._state = PlaybackState.IDLE
                        del self._spoken_items[completed_before:]
                    elif self._fence is None:
                        self._queue = queue_before
                        self._queue_bytes = queue_bytes_before
                        self._held = held_before
                        self._held_bytes = held_bytes_before
                    # When the sink failed on this segment it raised the fence,
                    # and `_close` has already cleared both buffers, left the
                    # obligation inflight and set the fenced state. Restoring any
                    # of that would put back the speech the fence exists to stop.
                    break
            self._release_cursor += 1
        self._pump()
        self._sync_approved_accounting()
        return self._release_cursor >= len(segments)

    def _approved_backpressure(self, approved):
        """Report an approved answer whose bounds are not yet satisfied.

        This is not a terminal refusal: the text stays retained and named, so a
        retry can place more of it once the sink has drained. The bytes are
        counted as *approved but unplaced* - the verifier approved them, the
        bounds have not yet let them play - and the count is corrected as the
        remainder is placed, so it never claims bytes that are already speaking.
        They are deliberately not counted as unapproved: an accepted answer
        described as rejected is the misreport this separation exists to stop.
        """
        remaining = self._approved_segments[self._release_cursor:]
        self._unspeakable_text = approved
        self._last_released = list(remaining)
        self._sync_approved_accounting()
        return self._outcome("backpressure", "release_exceeds_bounds", ())

    def _sync_approved_accounting(self):
        """Keep the approved-unplaced count equal to what is genuinely unplaced.

        The verifier approved this answer; the bounds have not let all of it play
        yet. Counting the whole answer once and never correcting it would report
        bytes as unplaced after they had played, so the count follows the release
        cursor and nothing else. It writes `_approved_unplaced_bytes`, never
        `_unapproved_bytes`: this text was approved, and the two counters answer
        different questions.
        """
        if self._approved_segments is None:
            return
        self._approved_unplaced_bytes = sum(
            len(text.encode("utf-8"))
            for text in self._approved_segments[self._release_cursor:])

    def _retry_approved_release(self):
        """Resume a release whose first release was refused as a unit.

        The all-or-nothing rule is what a *first* verdict owes: nothing plays
        unless the whole answer fits at that moment. Once that verdict has been
        given, the answer is approved and retained, and the documented recovery is
        that the release cursor advances - `completed` continues it as playback
        frees room, and a repeated `verdict` resumes it. A retry therefore places
        the prefix the bounds now allow and retains the rest, which is why an
        answer wider than the whole queue-and-held bound can still be spoken
        instead of being permanently unreachable.

        It is still bounded: `_release_progress` places a prefix, rolls a
        placement back if it does not fit, and never exceeds either container.
        `approved_unplaced_bytes` follows the cursor, so bytes that reached
        playback stop being reported as unplaced.

        A backpressure from *here* can therefore follow partial progress: the
        prefix already placed stays placed rather than being rolled back, because
        a retry that resumed part of the answer has done real work. Only the first
        settlement places nothing on refusal.
        """
        if self._release_progress():
            return self._outcome("accepted", "verdict_release_complete",
                                 tuple(self._approved_segments))
        return self._approved_backpressure(self._unspeakable_text)

    def _release_approved_answer(self, segments):
        """Release a *first* approved answer as a unit, or report backpressure.

        This is the first-verdict settlement only. A retry does not arrive here:
        it returns earlier through `_retry_approved_release`, which resumes the
        retained prefix progressively. The two are deliberately different
        contracts, and an earlier revision of this docstring said they shared
        this helper, which was false and made the source look as though a retry
        re-applied the all-or-nothing rule it had already satisfied.

        What they do *not* share is the whole-remainder rule, which is this
        helper's alone: a first settlement plays the entire approved answer or
        nothing, because an answer that has not been approved must never be
        spoken in part. A retry is approved already, so it places the prefix the
        bounds now allow and retains the rest, and an answer wider than the
        queue-and-held bound stays speakable across completions instead of being
        permanently unreachable.

        They also do not share what a refusal has already *placed*, and the
        difference is the whole contract:

        * a **first-settlement capacity backpressure** places nothing at all -
          the entire answer must fit at that moment, so a refusal means no part
          of it played;
        * a **recovery backpressure** can follow partial progress - the prefix
          the bounds allowed is placed and stays placed, and the refusal covers
          only what is left;
        * in both cases the **verifier has already approved the answer** before
          any capacity settlement happens. Capacity decides how much is spoken
          now; it never decides whether the text was acceptable.

        What both do share is that a refusal leaves the text retained and named
        for a later attempt.
        """
        if not self._fits_entirely(segments):
            return self._approved_backpressure("".join(segments))
        if not self._release_progress():
            return self._approved_backpressure("".join(segments))
        return self._outcome("accepted", "verdict_release_complete",
                             tuple(self._approved_segments))

    def completed(self, item_id, *, session, lifecycle):
        """Playback-completion callback. Late or foreign ones are ignored.

        This is also the public event that can settle a retained stop obligation,
        and the one that carries an approved answer past a bound it did not fit
        the first time. Both are narrow: a completion for a *different* item
        settles nothing, and only the exact retained item is eligible.
        """
        if type(item_id) is not int:
            raise InvalidFacts("item_id must be an int")
        # A failed stop leaves the item named as an obligation, and `_inflight`
        # still names it too, so obligation resolution is decided before the
        # ordinary completion path and before the fence: this exact item,
        # presented by the session that owns it, is the positive evidence that
        # resolves the obligation, and no second stop is needed to believe it.
        # Admission stays closed either way - an obligation resolves playback,
        # not the fence.
        if (self._stop_unresolved is not None
                and self._stop_unresolved.item_id == item_id):
            # Facts first, so a foreign or malformed completion cannot settle an
            # obligation. Only a *fence* is deliberately not disqualifying: the
            # obligation exists because the session was fenced mid-stop, and
            # requiring an unfenced session to settle it would make it
            # unreachable from every public path.
            validate_session(session)
            validate_lifecycle(lifecycle)
            if session != self._session:
                self._close(FenceReason.OWNER_CHANGED)
                return self._outcome("fenced", FenceReason.OWNER_CHANGED.value)
            self._retire_stop_obligation()
            return self._outcome("accepted", "stop_obligation_settled")
        guarded = self._guard(session, lifecycle)
        if guarded is not None:
            return guarded
        if self._inflight is None or self._inflight.item_id != item_id:
            return self._outcome("ignored", "stale_completion")
        self._inflight = None
        self._pump()
        # An approved answer that did not fit its bounds still has unplayed
        # segments retained. Playback has just freed room, so the release
        # continues here: this completion is the public event that progresses it,
        # and without this the remainder would wait for a caller that has no
        # reason to ask again.
        if self._approved_segments is not None and \
                self._release_cursor < len(self._approved_segments):
            self._release_progress()
        # A pump can fence the session (sink failure). Drained must never
        # overwrite a fence: the fence is the stronger fact.
        if (self._fence is None and self._inflight is None and not self._queue
                and not self._held and not self._segmenter.pending
                and self._release_cursor >= len(self._approved_segments or ())):
            self._state = PlaybackState.DRAINED
        return self._outcome("accepted", "")

    def cancel(self, reason=FenceReason.CANCELLED):
        """Stop the sink and drain pending output. Takes no session facts.

        Teardown must not be conditional on facts the teardown itself may be
        racing, so this is unconditional and idempotent. It is also the retry for
        a stop that failed: the fence is already set, so `_close` has nothing left
        to do, and the retained obligation would otherwise be unreachable from
        every public path.
        """
        if self._stop_unresolved is not None:
            self._settle_stop_obligation()
        self._close(reason)
        return self._outcome("fenced", reason.value)

    def fence(self, reason=FenceReason.CALLER_FENCED):
        return self.cancel(reason)

    # --- internals ----------------------------------------------------------

    def _guard(self, session, lifecycle):
        """Validate identity and lifecycle first, then decide admission.

        Identity is checked before any path, including the release path: this is
        the ordering defect that had to be fixed in the router, and it is not
        repeated here.
        """
        validate_session(session)
        validate_lifecycle(lifecycle)
        if self._fence is not None:
            return self._outcome("fenced", self._fence.value)
        if session != self._session:
            self._close(FenceReason.OWNER_CHANGED)
            return self._outcome("fenced", FenceReason.OWNER_CHANGED.value)
        for flag, reason in ((lifecycle.cancelled, FenceReason.CANCELLED),
                             (lifecycle.locked, FenceReason.LOCKED),
                             (lifecycle.sleeping, FenceReason.SLEEPING)):
            if flag:
                self._close(reason)
                return self._outcome("fenced", reason.value)
        if lifecycle.elapsed_seconds > self._policy.deadline_seconds:
            self._close(FenceReason.DEADLINE_EXCEEDED)
            return self._outcome("fenced", FenceReason.DEADLINE_EXCEEDED.value)
        return None

    def _close(self, reason):
        if self._fence is not None:
            return
        self._fence = reason
        if reason is FenceReason.SINK_FAILED:
            parts = [self._inflight.text] if self._inflight else []
            parts.extend(item.text for item in self._queue)
            parts.extend(item.text for item in self._held)
            parts.append(self._segmenter.pending)
            self._unspeakable_text = "".join(parts)
        else:
            self._discarded_bytes += (
                sum(len(item.text.encode("utf-8")) for item in self._queue)
                + sum(len(item.text.encode("utf-8")) for item in self._held)
                + self._segmenter.pending_bytes)
        self._drained_on_fence = len(self._queue) + len(self._held)
        stopped = self._stop_sink()
        self._queue.clear()
        self._queue_bytes = 0
        self._held.clear()
        self._held_bytes = 0
        if stopped:
            self._inflight = None
            self._state = (PlaybackState.STOPPED if reason is FenceReason.CANCELLED
                           else PlaybackState.FENCED)
        else:
            # The sink refused to stop, so playback may still be active. Keeping
            # the inflight obligation is the honest state: clearing it would let
            # `stopped` and `inflight is None` together assert a drain that never
            # happened. Nothing new can start, because the session is fenced.
            self._stop_unresolved = self._inflight
            self._state = PlaybackState.STOP_UNCERTAIN
        self._segmenter = Segmenter(max_bytes=self._policy.segment_max_bytes)

    def _stop_sink(self):
        try:
            self._sink.stop()
        except Exception:  # a sink that cannot stop must not mask the fence
            self._stop_failures += 1
            return False
        self._stop_calls += 1
        return True

    def _settle_stop_obligation(self):
        """Resolve a retained stop obligation against the sink, or refuse to claim it.

        The obligation is cleared only when the sink positively stops. A failing
        sink leaves it exactly where it was, so "we could not stop it" can never
        be reported as stopped. Settlement also never pumps queued speech: the
        session stays fenced and admission stays closed, because resolving an
        obligation resolves playback, not the fence.
        """
        if self._stop_unresolved is None:
            return True
        if not self._stop_sink():
            return False
        self._retire_stop_obligation()
        return True

    def _retire_stop_obligation(self):
        """Clear a resolved obligation and leave the fence and state honest."""
        self._stop_unresolved = None
        self._inflight = None
        if self._fence is not None:
            self._state = (PlaybackState.STOPPED if self._fence is FenceReason.CANCELLED
                           else PlaybackState.FENCED)

    def _reconcile(self, text, mode, emission_id=None):
        if mode == "snapshot":
            if text == self._generated:
                return ""
            if text.startswith(self._generated):
                return text[len(self._generated):]
            if self._generated.startswith(text):
                raise SpeechRefused("stale_snapshot")
            raise SpeechRefused("divergent_snapshot")
        if emission_id is not None:
            # Exactly as good as the caller's bookkeeping: a repeated identity is
            # the same emission re-delivered, a new identity is a new emission
            # even when its text is character-for-character identical.
            #
            # This only *reads* the identity. Committing it here would record a
            # delivery that has not been admitted yet, so a delta refused for
            # backpressure would come back as a duplicate on retry and its text
            # would be lost - the retry is the caller doing exactly what the
            # backpressure outcome asks for.
            if emission_id == self._last_emission_id:
                return ""
            return text
        # No identity, so there is nothing here that can distinguish a retry from
        # a deliberate repeat - the text is the same either way. Reporting the
        # second one as a duplicate would silently drop a legitimate token, so
        # the text is accepted. Suppression is not inferred from content; a
        # caller that needs retry suppression supplies `emission_id`, and a
        # caller that can only send whole answers uses `mode='snapshot'`, where
        # an unchanged snapshot really is the same statement re-sent.
        return text

    def _commit_emission(self, emission_id):
        """Remember an emission only once its text has been admitted.

        A refused delta is not a delivered one, so it stays retryable under the
        identity it was refused with. Nothing is remembered for a delta that
        carried no identity, which is what keeps a deliberate repeat from being
        mistaken for a retry.
        """
        if emission_id is not None:
            self._last_emission_id = emission_id

    def _has_room_for(self, delta):
        used = (self._held_bytes + self._segmenter.pending_bytes
                + len(delta.encode("utf-8")))
        return used <= self._policy.held_max_bytes

    def _queue_has_room(self, item):
        """Whether this item fits the queue's item *and* byte ceilings.

        The byte ceiling binds on the first waiting item too. Exempting an empty
        queue let a 64-byte segment sit in a queue declared to hold 16 bytes,
        which is the bound the caller asked for reporting as obeyed while it was
        not. A segment can legitimately be larger than `queue_max_bytes` - the
        two bounds are set independently - so the queue is not the only way to
        reach the sink: `_place` hands such a segment straight over when nothing
        is playing and nothing is waiting, and holds it otherwise.
        """
        size = len(item.text.encode("utf-8"))
        return (len(self._queue) < self._policy.queue_max_items
                and self._queue_bytes + size <= self._policy.queue_max_bytes)

    def _release(self, segments):
        """Place segments in the queue or the held buffer; never beyond both.

        The held buffer is a bound, not an overflow bin. A release that cannot
        fit is refused as a whole and rolled back, so nothing is partially
        enqueued and nothing is dropped: the text stays exactly where the caller
        put it and the caller retries. Returning False is how the verdict path
        learns that a complete approved answer did not actually become speakable.
        """
        queue_before = deque(self._queue)
        queue_bytes_before = self._queue_bytes
        held_before = deque(self._held)
        held_bytes_before = self._held_bytes
        for text in segments:
            if not text:
                continue
            if not self._place(text):
                if self._fence is not None:
                    # A sink failure closed admission and cleared the buffers in
                    # `_close`; restoring the pre-release snapshot would put back
                    # speech the fence exists to stop.
                    return False
                self._queue = queue_before
                self._queue_bytes = queue_bytes_before
                self._held = held_before
                self._held_bytes = held_bytes_before
                return False
        self._pump()
        return True

    def _place(self, text):
        """Make one segment reachable by the sink, or report that it is not.

        Three destinations, in order: the queue, a direct handover when the sink
        is idle and nothing is waiting, and the held buffer. A direct handover is
        what keeps a segment larger than `queue_max_bytes` from being unusable -
        it would not fit the queue's byte ceiling and would otherwise wait in
        held for a queue it can never enter. Nothing is stored twice, no bound is
        exceeded, and the item still starts through `_start` so the sink and the
        state machine see one code path.
        """
        if self._fence is not None:
            # A fence closes admission, and the sink failure that raised it may
            # have happened on this very segment. Placing anything now would hand
            # a fenced session's speech to the sink - the walk must stop here and
            # report that this segment reached nowhere.
            return False
        size = len(text.encode("utf-8"))
        item = SpeechItem(self._next_item, text, self._next_slot)
        if self._queue_has_room(item):
            self._next_item += 1
            self._next_slot += 1
            self._queue.append(item)
            self._queue_bytes += size
            return True
        if self._direct_handover(item, size):
            # A direct handover was attempted, so this segment's destination is
            # decided: it either reaches the sink now or it reaches nowhere.
            # Falling through to the held buffer here would re-admit an item whose
            # start has already failed - and start it again later, against a sink
            # that just refused it, after the failure had already fenced the
            # session and cleared every waiting buffer.
            if not self._start(item):
                return False
            self._next_item += 1
            self._next_slot += 1
            return True
        if self._held_bytes + size <= self._policy.held_max_bytes:
            self._next_item += 1
            self._next_slot += 1
            self._held.append(item)
            self._held_bytes += size
            return True
        return False

    def _direct_handover(self, item, size):
        """Whether this segment must go straight to the sink to remain playable.

        Only for a segment the queue can never hold because it is larger than the
        queue's whole byte ceiling, and only while the sink is idle with nothing
        waiting. A segment that fits the queue waits its turn as before; this is
        the escape from a bound that would otherwise deadlock the answer, not a
        second scheduling policy.
        """
        return (size > self._policy.queue_max_bytes
                and not self._queue and not self._held and self._inflight is None)

    def _pump(self):
        if self._fence is not None:
            # A fence means nothing new may start. This is not redundant with the
            # failed `_start` below: a sink that stops successfully leaves no
            # obligation inflight, so the loop would otherwise take the next item
            # and speak it into a session that was fenced precisely because
            # playback broke.
            return
        while self._inflight is None:
            item = self._take_oldest()
            if item is None:
                return
            if not self._start(item):
                # The sink failed on this item, which has fenced the session and
                # closed admission. Starting the next one would play speech into
                # a fence that exists precisely because playback broke.
                return

    def _take_oldest(self):
        """Take the earliest-admitted waiting item from either destination.

        The queue and the held buffer hold one ordered stream, so the next item
        to play is the one the caller emitted first - never simply the one in a
        particular container. Draining the queue first let a segment queued after
        an older held segment overtake it, which is a reordering of the caller's
        own emission order and not a scheduling decision this controller is
        entitled to make.
        """
        if not self._queue:
            return self._pop_held() if self._held else None
        if not self._held:
            return self._pop_queued()
        if self._held[0].slot < self._queue[0].slot:
            return self._pop_held()
        return self._pop_queued()

    def _pop_queued(self):
        item = self._queue.popleft()
        self._queue_bytes -= len(item.text.encode("utf-8"))
        return item

    def _pop_held(self):
        item = self._held.popleft()
        self._held_bytes -= len(item.text.encode("utf-8"))
        return item

    def _start(self, item):
        """Hand one item to the sink as the single active playback obligation.

        Returns whether the sink took it. A raising sink has still been invoked,
        so the item stays the inflight obligation and the session is fenced; the
        caller is told False so it stops the walk rather than reaching for the
        next segment.
        """
        self._inflight = item
        self._state = PlaybackState.SPEAKING
        try:
            self._sink.speak(item.item_id, item.text)
        except Exception as error:
            self._sink_error = type(error).__name__
            self._inflight = item
            self._close(FenceReason.SINK_FAILED)
            return False
        self._spoken_items.append(item)
        return True

    def _outcome(self, status, reason, released=()):
        return SpeechOutcome(status, reason, tuple(released), len(self._queue),
                             len(self._held), self._state.value)
