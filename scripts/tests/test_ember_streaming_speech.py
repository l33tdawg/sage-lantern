"""Checks for the disabled streaming-speech candidate.

Synthetic deltas, an injected mock playback sink, no audio device, no runtime
import, no network. Everything here is deterministic: there is no thread, no
sleep and no wall-clock assertion anywhere.

Run from the repository root:

    PYTHONPATH=.:software/runtime/src python3 -m pytest scripts/tests/test_ember_streaming_speech.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import pytest

# The reserved candidate directory keeps its hyphenated path boundary, so it is
# not a valid Python identifier. Register the package chain synthetically, as
# the model-router suite does, rather than renaming the directory or adding
# __init__.py markers outside the reserved paths.
ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE = ROOT / "software" / "experiments" / "streaming-speech-ember"
_PREFIX = "software.experiments.streaming_speech_ember"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if _PREFIX not in sys.modules:
    for module_name, directory in (
        ("software", ROOT / "software"),
        ("software.experiments", ROOT / "software" / "experiments"),
    ):
        if module_name in sys.modules:
            continue
        parent = types.ModuleType(module_name)
        parent.__path__ = [str(directory)]  # type: ignore[attr-defined]
        sys.modules[module_name] = parent

    package = types.ModuleType(_PREFIX)
    package.__path__ = [str(_CANDIDATE)]  # type: ignore[attr-defined]
    sys.modules[_PREFIX] = package
    setattr(sys.modules["software.experiments"], "streaming_speech_ember", package)

from software.experiments.streaming_speech_ember import controller as _controller  # noqa: E402
from software.experiments.streaming_speech_ember import policy as _policy  # noqa: E402
from software.experiments.streaming_speech_ember import segmenter as _segmenter  # noqa: E402

FenceReason = _controller.FenceReason
InvalidFacts = _controller.InvalidFacts
LifecycleFacts = _controller.LifecycleFacts
PlaybackState = _controller.PlaybackState
Segmenter = _segmenter.Segmenter
SessionFacts = _controller.SessionFacts
SpeechPolicy = _policy.SpeechPolicy
SpeechRefused = _controller.SpeechRefused
StreamingSpeechController = _controller.StreamingSpeechController


SESSION = SessionFacts("session-1", "owner-a", "0" * 32)
CLEAR = LifecycleFacts()


class MockSink:
    """Records what the controller asked of it. Playback is what this records."""

    def __init__(self, *, fail_on=None, stop_raises=False):
        self.spoken = []
        #: Every id the sink was actually asked to speak, including one whose
        #: `speak` raised. `spoken` records playback; this records the attempt,
        #: which is what a fence must stop from advancing.
        self.attempted = []
        self.stop_attempts = 0
        self.stopped_while_active = 0
        self.active = None
        self.stop_raises = stop_raises
        self.fail_on = fail_on

    def speak(self, item_id, text):
        self.attempted.append(item_id)
        if self.fail_on is not None and self.fail_on(item_id, text):
            raise RuntimeError("mock sink failure")
        self.spoken.append((item_id, text))
        self.active = item_id

    def stop(self):
        self.stop_attempts += 1
        if self.stop_raises:
            raise RuntimeError("mock sink cannot stop")
        if self.active is not None:
            self.stopped_while_active += 1
        self.active = None

    @property
    def texts(self):
        return [text for _, text in self.spoken]


def build(sink=None, *, verify=False, **policy_kwargs):
    sink = MockSink() if sink is None else sink
    policy = SpeechPolicy(verify=verify, **policy_kwargs)
    return StreamingSpeechController(sink, session=SESSION, policy=policy), sink


def drain(controller, *, session=SESSION, lifecycle=CLEAR, limit=200):
    """Complete every item the sink has been handed, in order."""
    steps = 0
    while controller.inflight is not None and steps < limit:
        controller.completed(controller.inflight.item_id, session=session,
                             lifecycle=lifecycle)
        steps += 1
    return steps


def stream(controller, text, *, mode="delta", session=SESSION, lifecycle=CLEAR):
    """Feed text and drain after each delta, so a queue bound cannot mask order."""
    return controller.feed(text, mode=mode, session=session, lifecycle=lifecycle)


# --------------------------------------------------------------------------
# Segmentation: what is not a sentence boundary
# --------------------------------------------------------------------------

def test_segmenter_splits_plain_sentences():
    segmenter = Segmenter(max_bytes=256)
    assert segmenter.push("One. Two! Three? ") == ["One. ", "Two! ", "Three? "]
    assert segmenter.pending == ""


def test_segmenter_holds_a_decimal_point():
    segmenter = Segmenter(max_bytes=256)
    released = segmenter.push("The value is 3.14 metres. Then more. ")
    assert released == ["The value is 3.14 metres. ", "Then more. "]
    assert not any(seg.rstrip().endswith("3.") for seg in released)


def test_segmenter_holds_abbreviations_and_initials():
    segmenter = Segmenter(max_bytes=256)
    released = segmenter.push("Dr. Smith met Mrs. Jones at 5 p.m. yesterday. ")
    assert released == ["Dr. Smith met Mrs. Jones at 5 p.m. yesterday. "]
    segmenter = Segmenter(max_bytes=256)
    assert segmenter.push("J. R. Tolkien wrote it. ") == ["J. R. Tolkien wrote it. "]


def test_segmenter_treats_an_ellipsis_as_one_terminator():
    segmenter = Segmenter(max_bytes=256)
    assert segmenter.push("Wait... what happened. ") == ["Wait... ", "what happened. "]


def test_segmenter_does_not_split_inside_an_unclosed_tag():
    segmenter = Segmenter(max_bytes=256)
    assert segmenter.push("Press <b") == []
    assert segmenter.push(". ") == []  # the terminator is inside the open tag
    assert segmenter.push(">bold</b>. ") == ["Press <b. >bold</b>. "]


def test_segmenter_keeps_an_entity_whole_across_pushes():
    segmenter = Segmenter(max_bytes=256)
    released = segmenter.push("AT&amp")
    released += segmenter.push(";T is a company. ")
    assert released == ["AT&amp;T is a company. "]


def test_segmenter_handles_cjk_without_spaces():
    segmenter = Segmenter(max_bytes=256)
    released = segmenter.push("\u4f60\u597d\u3002\u4e16\u754c\uff01\u518d\u89c1\u3002")
    # The final terminator is held back: more text could still follow it.
    assert released == ["\u4f60\u597d\u3002", "\u4e16\u754c\uff01"]
    assert segmenter.flush() == ["\u518d\u89c1\u3002"]


def test_segmenter_reconstructs_the_input_exactly():
    text = ("Cost is 3.14 euro. Dr. Who arrived... \u4f60\u597d\u3002"
            "AT&amp;T \U0001f469\u200d\U0001f4bb stays. e\u0301lan vital. ")
    for step in (1, 2, 3, 7):
        segmenter = Segmenter(max_bytes=32)
        released = []
        for index in range(0, len(text), step):
            released.extend(segmenter.push(text[index:index + step]))
        released.extend(segmenter.flush())
        assert "".join(released) == text, f"step={step}"


def test_segmenter_forces_a_cut_over_the_byte_budget():
    segmenter = Segmenter(max_bytes=32)
    released = segmenter.push("x" * 200)
    assert released, "a long unterminated run must not grow without limit"
    assert all(len(seg.encode("utf-8")) <= 40 for seg in released)


def test_segmenter_does_not_orphan_a_combining_mark():
    segmenter = Segmenter(max_bytes=32)
    released = segmenter.push("e\u0301" * 60)
    for segment in released[:-1]:
        assert segment, "no empty segment"
        assert not _segmenter._joinable(segment[0]), (
            "a segment must not start with a combining mark")


# --------------------------------------------------------------------------
# Streaming release and ordering
# --------------------------------------------------------------------------

def test_streaming_releases_a_sentence_before_the_answer_ends():
    controller, sink = build()
    outcome = stream(controller, "One. Two. Three.")
    assert outcome.status == "accepted"
    assert outcome.released == ("One. ", "Two. ")
    assert sink.texts == ["One. "]          # playback is serialised
    assert controller.queue_depth == 1
    assert controller.pending_text == "Three."


def test_playback_is_ordered_and_serialised():
    controller, sink = build()
    stream(controller, "One. Two. Three. Four. ")
    assert sink.texts == ["One. "]
    drain(controller)
    assert sink.texts == ["One. ", "Two. ", "Three. ", "Four. "]
    assert [item_id for item_id, _ in sink.spoken] == [1, 2, 3, 4]
    assert controller.state is PlaybackState.DRAINED


def test_a_duplicate_cumulative_snapshot_is_suppressed():
    controller, sink = build()
    stream(controller, "One. Two. ")
    before = list(sink.texts)
    outcome = stream(controller, "One. Two. ", mode="snapshot")
    assert outcome.status == "duplicate"
    assert outcome.released == ()
    assert controller.generated == "One. Two. "
    assert sink.texts == before


def test_a_cumulative_snapshot_releases_only_the_new_tail():
    controller, sink = build()
    stream(controller, "One. ", mode="snapshot")
    outcome = stream(controller, "One. Two. ", mode="snapshot")
    assert outcome.status == "accepted"
    assert outcome.released == ("Two. ",)
    assert controller.generated == "One. Two. "


def test_repeating_raw_delta_text_is_not_treated_as_a_retry():
    # Interface decision, 5 Oct. Two identical raw deltas are two emissions: the
    # text cannot prove delivery, and treating an equal string as a retry
    # silently drops a legitimate repeated token ("Echo. Echo. " became one).
    # Suppression belongs to an interface that can state identity, not to a
    # comparison of content.
    controller, sink = build()
    first = controller.feed("One. ", session=SESSION, lifecycle=CLEAR)
    second = controller.feed("One. ", session=SESSION, lifecycle=CLEAR)
    drain(controller)
    assert first.status == second.status == "accepted"
    assert controller.generated == "One. One. "
    assert "".join(sink.texts) == "One. One. "


def test_a_repeated_emission_identity_is_suppressed_as_a_retry():
    # The supported way to suppress a retry: the caller says which emission this
    # is. The same identity twice is one emission re-delivered; two identities
    # are two emissions however identical their text.
    controller, sink = build()
    first = controller.feed("Echo. ", session=SESSION, lifecycle=CLEAR, emission_id="e-1")
    repeat = controller.feed("Echo. ", session=SESSION, lifecycle=CLEAR, emission_id="e-1")
    second = controller.feed("Echo. ", session=SESSION, lifecycle=CLEAR, emission_id="e-2")
    drain(controller)
    assert first.status == "accepted" and first.explicit_identity is True
    assert repeat.status == "duplicate" and repeat.explicit_identity is True
    assert second.status == "accepted"
    assert "".join(sink.texts) == "Echo. Echo. "


def test_a_stale_snapshot_is_refused_not_replayed():
    controller, _ = build()
    stream(controller, "One. Two. ", mode="snapshot")
    with pytest.raises(SpeechRefused) as error:
        controller.feed("One. ", mode="snapshot", session=SESSION, lifecycle=CLEAR)
    assert str(error.value) == "stale_snapshot"
    assert controller.generated == "One. Two. "


def test_a_divergent_snapshot_is_refused():
    controller, _ = build()
    stream(controller, "One. ", mode="snapshot")
    with pytest.raises(SpeechRefused) as error:
        controller.feed("Something else. ", mode="snapshot", session=SESSION,
                        lifecycle=CLEAR)
    assert str(error.value) == "divergent_snapshot"


# --------------------------------------------------------------------------
# Cancellation: stopping the sink, not just the queue
# --------------------------------------------------------------------------

def test_mid_sentence_cancel_speaks_no_fragment():
    controller, sink = build()
    outcome = stream(controller, "The quick brown fox")
    assert outcome.released == ()
    controller.cancel()
    assert sink.texts == []
    assert controller.state is PlaybackState.STOPPED
    assert controller.discarded_bytes == len("The quick brown fox")


def test_cancel_stops_the_active_sink_not_only_the_queue():
    controller, sink = build()
    stream(controller, "One. Two. Three. ")
    assert sink.active == 1, "an item must be playing, or this proves nothing"
    assert controller.queue_depth == 2
    controller.cancel()
    assert sink.stop_attempts == 1
    assert sink.stopped_while_active == 1, (
        "cancellation must stop playback, not only empty the queue")
    assert controller.stop_calls == 1
    assert controller.queue_depth == 0
    assert controller.held_items == 0
    assert controller.inflight is None
    assert controller.drained_on_fence == 2


def test_late_deltas_after_cancel_are_fenced():
    controller, sink = build()
    stream(controller, "One. ")
    spoken_before = list(sink.texts)
    controller.cancel()
    outcome = controller.feed("Two. ", session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "fenced"
    assert outcome.reason == FenceReason.CANCELLED.value
    assert controller.generated == "One. "
    assert sink.texts == spoken_before, "a fenced session must speak nothing further"


def test_a_late_playback_completion_after_cancel_is_ignored():
    controller, sink = build()
    stream(controller, "One. Two. ")
    spoken_before = list(sink.texts)
    controller.cancel()
    outcome = controller.completed(1, session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "fenced"
    assert sink.texts == spoken_before
    assert controller.inflight is None


def test_cancel_is_idempotent():
    controller, sink = build()
    stream(controller, "One. ")
    controller.cancel()
    controller.cancel()
    assert sink.stop_attempts == 1
    assert controller.stop_calls == 1


# --------------------------------------------------------------------------
# Fencing: owner, lock, sleep, deadline
# --------------------------------------------------------------------------

def test_owner_change_fences_and_drains():
    controller, sink = build()
    stream(controller, "One. Two. Three. ")
    stranger = SessionFacts("session-1", "owner-b", "0" * 32)
    outcome = controller.feed("Four. ", session=stranger, lifecycle=CLEAR)
    assert outcome.status == "fenced"
    assert outcome.reason == FenceReason.OWNER_CHANGED.value
    assert sink.stop_attempts == 1
    assert controller.queue_depth == 0
    assert controller.fence_reason is FenceReason.OWNER_CHANGED


def test_epoch_change_fences():
    controller, sink = build()
    stream(controller, "One. ")
    rotated = SessionFacts("session-1", "owner-a", "1" * 32)
    outcome = controller.feed("Two. ", session=rotated, lifecycle=CLEAR)
    assert outcome.status == "fenced"
    assert outcome.reason == FenceReason.OWNER_CHANGED.value


def test_lock_fences_before_any_release():
    controller, sink = build()
    outcome = controller.feed("One. Two. ", session=SESSION,
                              lifecycle=LifecycleFacts(locked=True))
    assert outcome.status == "fenced"
    assert outcome.reason == FenceReason.LOCKED.value
    assert sink.texts == []
    assert controller.generated == ""


def test_sleep_drains_pending_output_and_stops_playback():
    controller, sink = build()
    stream(controller, "One. Two. Three. Four. ")
    assert sink.active is not None
    outcome = controller.feed("Five. ", session=SESSION,
                              lifecycle=LifecycleFacts(sleeping=True))
    assert outcome.status == "fenced"
    assert outcome.reason == FenceReason.SLEEPING.value
    assert sink.stop_attempts == 1
    assert sink.stopped_while_active == 1
    assert controller.drained_on_fence > 0, "pending output must be drained"
    assert controller.queue_depth == 0 and controller.held_items == 0
    assert controller.pending_text == ""


def test_the_deadline_fences_late_work():
    controller, sink = build(deadline_seconds=5.0)
    stream(controller, "One. ")
    outcome = controller.feed("Two. ", session=SESSION,
                              lifecycle=LifecycleFacts(elapsed_seconds=5.5))
    assert outcome.status == "fenced"
    assert outcome.reason == FenceReason.DEADLINE_EXCEEDED.value
    assert sink.stop_attempts == 1


def test_a_fence_is_sticky():
    controller, _ = build()
    controller.cancel()
    outcome = controller.feed("One. ", session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "fenced"
    assert controller.generated == ""


# --------------------------------------------------------------------------
# Verification: nothing is spoken that a verdict could replace
# --------------------------------------------------------------------------

def test_verification_blocks_every_release_until_the_verdict():
    controller, sink = build(verify=True)
    outcome = stream(controller, "The distance is 130 km. It takes two hours. ")
    assert outcome.status == "accepted"
    assert outcome.released == ()
    assert sink.texts == [], "nothing may be spoken before the verdict"
    assert controller.state is PlaybackState.BUFFERING
    assert controller.generated == "The distance is 130 km. It takes two hours. "


def test_an_ok_verdict_releases_the_draft():
    controller, sink = build(verify=True)
    stream(controller, "One. Two. ")
    outcome = controller.verdict("ok", "", session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "accepted"
    assert outcome.reason == "verdict_ok"
    assert controller.verdict_status == "ok"
    drain(controller)
    assert "".join(sink.texts) == "One. Two. "


def test_a_corrected_verdict_speaks_only_the_corrected_text():
    controller, sink = build(verify=True)
    stream(controller, "The distance is 130 km. ")
    outcome = controller.verdict("corrected", "The distance is 515 km. ",
                                 session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "accepted"
    drain(controller)
    assert not any("130" in text for text in sink.texts), (
        "a sentence a later verdict replaced must never reach the sink")
    assert "".join(sink.texts) == "The distance is 515 km. "
    assert controller.discarded_bytes == len("The distance is 130 km. ".encode())


@pytest.mark.parametrize("status", ["unsure", "unavailable"])
def test_an_inconclusive_verdict_speaks_nothing(status):
    # Interface decision, 5 Oct, replacing the earlier pin on the runtime's
    # fallback. The runtime rule that only "corrected" replaces the text answers
    # "which text is current"; it does not answer "was this approved". Reading a
    # surviving draft as approved turns "we could not check this" into "this
    # passed", which is exactly the inference verification-before-speech exists
    # to prevent. The draft still survives as the current text - it is simply
    # never spoken, and the bytes are accounted rather than dropped in silence.
    controller, sink = build(verify=True)
    stream(controller, "One. Two. ")
    outcome = controller.verdict(status, "", session=SESSION, lifecycle=CLEAR)
    drain(controller)
    assert sink.texts == [], "an inconclusive check approved no draft to speak"
    assert outcome.status == "unapproved"
    assert outcome.reason == "verdict_" + status
    assert controller.unapproved_bytes == len("One. Two. ".encode("utf-8"))
    assert controller.generated == "One. Two. ", "the draft is still the current text"
    assert controller.discarded_bytes == 0, "unapproved is not the same as superseded"


@pytest.mark.parametrize("status", ["unsure", "unavailable"])
def test_an_inconclusive_verdict_still_freezes_the_answer(status):
    # The draft was not released, and it must not be able to escape afterwards
    # either: an inconclusive verdict ends the answer just as a decisive one does.
    controller, sink = build(verify=True)
    stream(controller, "One. Two. ")
    controller.verdict(status, "", session=SESSION, lifecycle=CLEAR)
    with pytest.raises(SpeechRefused) as refused:
        controller.feed("Three. ", session=SESSION, lifecycle=CLEAR)
    assert refused.value.args[0] == "answer_frozen"
    drain(controller)
    assert sink.texts == []
    assert controller.generated == "One. Two. ", "the late delta was refused, not buffered"


def test_deltas_after_a_verdict_are_refused():
    controller, _ = build(verify=True)
    stream(controller, "One. ")
    controller.verdict("ok", "", session=SESSION, lifecycle=CLEAR)
    with pytest.raises(SpeechRefused) as error:
        controller.feed("Two. ", session=SESSION, lifecycle=CLEAR)
    assert str(error.value) == "answer_frozen"


def test_a_verdict_without_verification_enabled_is_refused():
    controller, _ = build(verify=False)
    with pytest.raises(SpeechRefused) as error:
        controller.verdict("ok", "", session=SESSION, lifecycle=CLEAR)
    assert str(error.value) == "verification_disabled"


def test_a_verdict_after_cancel_is_fenced():
    controller, sink = build(verify=True)
    stream(controller, "One. ")
    controller.cancel()
    outcome = controller.verdict("ok", "", session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "fenced"
    assert sink.texts == []


def test_the_draft_budget_is_enforced_while_verification_is_pending():
    controller, sink = build(verify=True, draft_max_bytes=64)
    first = controller.feed("The distance is 515 kilometres. ", session=SESSION,
                            lifecycle=CLEAR)
    assert first.status == "accepted"
    second = controller.feed("It takes about two hours by train. ", session=SESSION,
                             lifecycle=CLEAR)
    assert second.status == "backpressure"
    assert second.reason == "draft_budget_exceeded"
    assert controller.generated == "The distance is 515 kilometres. ", (
        "a refused delta must not be consumed")
    assert sink.texts == []


# --------------------------------------------------------------------------
# Queue bounds, backpressure and loss
# --------------------------------------------------------------------------

def test_queue_saturation_applies_backpressure_without_losing_text():
    controller, sink = build(queue_max_items=1, queue_max_bytes=64,
                             held_max_bytes=48, segment_max_bytes=32,
                             draft_max_bytes=256)
    pieces = ["Alpha one. ", "Bravo two. ", "Charlie three. ", "Delta four. ",
              "Echo five. ", "Foxtrot six. "]
    accepted = []
    blocked = []
    for piece in pieces:
        outcome = controller.feed(piece, session=SESSION, lifecycle=CLEAR)
        if outcome.status == "backpressure":
            blocked.append(piece)
        else:
            assert outcome.status == "accepted"
            accepted.append(piece)
    assert blocked, "these bounds must produce backpressure, or nothing is tested"
    assert blocked == pieces[len(accepted):], "backpressure must not reorder"

    remaining = list(blocked)
    steps = 0
    while remaining and steps < 200:
        steps += 1
        outcome = controller.feed(remaining[0], session=SESSION, lifecycle=CLEAR)
        if outcome.status == "backpressure":
            drain(controller)
            continue
        accepted.append(remaining.pop(0))
    assert not remaining, "retrying after a drain must eventually be accepted"
    drain(controller)
    assert controller.pending_text == ""
    assert "".join(sink.texts) == "".join(accepted), (
        "no accepted text may be lost or reordered by backpressure")


def test_backpressure_does_not_consume_the_refused_delta():
    controller, _ = build(held_max_bytes=16, segment_max_bytes=16,
                          queue_max_items=1, queue_max_bytes=32)
    piece = "Alpha one. Bravo two. "  # 21 bytes, more than held_max_bytes
    outcome = controller.feed(piece, session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "backpressure"
    assert outcome.reason == "queue_and_held_full"
    assert controller.generated == ""


# --------------------------------------------------------------------------
# Sink failure and stop failure
# --------------------------------------------------------------------------

def test_a_sink_failure_fences_and_preserves_the_unspoken_text():
    sink = MockSink(fail_on=lambda item_id, _text: item_id == 2)
    controller, sink = build(sink)
    stream(controller, "One. Two. Three. ")
    drain(controller)
    assert controller.fence_reason is FenceReason.SINK_FAILED
    assert controller.state is PlaybackState.FENCED
    assert controller.sink_error == "RuntimeError"
    assert sink.stop_attempts == 1
    assert "Two. " in controller.unspeakable_text
    assert "Three. " in controller.unspeakable_text, (
        "text the failure prevented must be preserved, not silently dropped")


def test_a_stop_failure_is_recorded_not_swallowed():
    sink = MockSink(stop_raises=True)
    controller, sink = build(sink)
    stream(controller, "One. ")
    controller.cancel()
    assert sink.stop_attempts == 1
    assert controller.stop_calls == 0
    assert controller.stop_failures == 1
    # A refused stop may leave audio playing, so the state must not claim the
    # drain completed. `stopped` means the audio positively ended; this is a
    # different fact and it is reported as one.
    assert controller.state is PlaybackState.STOP_UNCERTAIN
    assert controller.state is not PlaybackState.STOPPED
    assert controller.stop_unresolved is not None, "the obligation is retained"
    assert controller.stop_unresolved.item_id == 1


def test_a_successful_stop_clears_the_obligation():
    sink = MockSink()
    controller, sink = build(sink)
    stream(controller, "One. ")
    controller.cancel()
    assert controller.stop_calls == 1
    assert controller.stop_failures == 0
    assert controller.stop_unresolved is None, "a positive stop leaves nothing owed"
    assert controller.state is PlaybackState.STOPPED


def test_a_final_sentence_without_trailing_whitespace_is_released_by_finish():
    # The streamed boundary is a terminator plus the evidence that the sentence
    # ended. A last sentence stopped by end-of-input has no such evidence, so it
    # waits - and a `finish` event is the explicit, bounded way to say the answer
    # is over. Without it the text stays pending forever, which is the defect.
    controller, sink = build()
    controller.feed("Final sentence.", session=SESSION, lifecycle=CLEAR)
    drain(controller)
    assert sink.texts == [], "nothing is guessed from the absence of more input"
    assert controller.pending_text == "Final sentence."

    outcome = controller.finish(session=SESSION, lifecycle=CLEAR)
    drain(controller)
    assert outcome.status == "accepted"
    assert outcome.reason == "finished"
    assert "".join(sink.texts) == "Final sentence.", "the final answer is spoken intact"
    assert controller.pending_text == ""


def test_finish_is_refused_when_verification_owns_the_end_of_the_answer():
    # With verification enabled the verdict is the end of the answer, so a
    # caller-driven finish would be a second, weaker path around the gate.
    controller, sink = build(verify=True)
    stream(controller, "One. Two. ")
    with pytest.raises(SpeechRefused) as refused:
        controller.finish(session=SESSION, lifecycle=CLEAR)
    assert refused.value.args[0] == "verification_pending"
    assert sink.texts == []


def test_a_corrected_answer_larger_than_the_draft_budget_is_refused_before_mutation():
    # A correction is new input the draft budget was never applied to. It is
    # checked before the draft is replaced or anything starts playing.
    controller, sink = build(verify=True, draft_max_bytes=64, held_max_bytes=64)
    stream(controller, "Short. ")
    oversized = "word " * 200
    outcome = controller.verdict("corrected", oversized, session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "backpressure"
    assert outcome.reason == "corrected_budget_exceeded"
    assert controller.generated == "Short. ", "the draft was not replaced"
    assert sink.texts == [], "and nothing was played"
    assert controller.discarded_bytes == 0


def test_an_approved_answer_too_large_for_its_bounds_is_not_played():
    # The verdict-to-playback transition is bounded like every other release. An
    # approved answer that cannot fit is refused honestly rather than enqueued
    # past the held buffer.
    controller, sink = build(verify=True, held_max_bytes=64, queue_max_bytes=32,
                             queue_max_items=1, segment_max_bytes=32)
    stream(controller, "word " * 240)
    outcome = controller.verdict("ok", "", session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "backpressure"
    assert outcome.reason == "release_exceeds_bounds"
    assert sink.texts == []
    assert controller.last_released, "the unplaceable segments are retained, not lost"
    assert controller.approved_unplaced_bytes > 0, (
        "approved-but-unplaced bytes are reported, not lost")
    assert controller.unapproved_bytes == 0, (
        "the verifier approved this text, so none of it is unapproved")


def test_a_natural_sentence_obeys_the_segment_ceiling():
    # A boundary moves the cut; it does not license the size. This is the
    # 202-byte-segment-under-a-32-byte-ceiling case: the sentence is emitted in
    # as many ceiling-sized pieces as it needs, and it still reconstructs.
    controller, sink = build(segment_max_bytes=32, held_max_bytes=4096,
                             queue_max_bytes=4096, queue_max_items=64)
    text = ("a" * 200) + ". "
    for index in range(0, len(text), 40):
        controller.feed(text[index:index + 40], session=SESSION, lifecycle=CLEAR)
        drain(controller)
    controller.finish(session=SESSION, lifecycle=CLEAR)
    drain(controller)
    spoken = "".join(sink.texts)
    assert spoken == text, "the sentence still reconstructs exactly"
    assert max(len(item.encode("utf-8")) for item in sink.texts) <= 32, (
        "no segment may exceed the ceiling, boundary or not")


# --------------------------------------------------------------------------
# Facts validation: a caller bug is not a speech outcome
# --------------------------------------------------------------------------

def test_invalid_facts_are_not_speech_outcomes():
    assert not issubclass(SpeechRefused, InvalidFacts)
    assert not issubclass(InvalidFacts, SpeechRefused)


def test_identity_and_lifecycle_are_validated_before_anything_is_consumed():
    controller, sink = build()
    with pytest.raises(InvalidFacts):
        controller.feed("One. ", session=SessionFacts("", "owner-a", "0" * 32),
                        lifecycle=CLEAR)
    with pytest.raises(InvalidFacts):
        controller.feed("One. ", session=SESSION,
                        lifecycle=LifecycleFacts(elapsed_seconds=-1.0))
    with pytest.raises(InvalidFacts):
        controller.feed(123, session=SESSION, lifecycle=CLEAR)
    with pytest.raises(InvalidFacts):
        controller.feed("One. ", session=SESSION, lifecycle=CLEAR, mode="guess")
    assert controller.generated == ""
    assert sink.texts == []


def test_bad_construction_is_refused():
    with pytest.raises(InvalidFacts):
        StreamingSpeechController(object(), session=SESSION)
    with pytest.raises(InvalidFacts):
        StreamingSpeechController(MockSink(), session=("session-1", "owner-a", 1))
    with pytest.raises(ValueError):
        SpeechPolicy(queue_max_items=0)
    with pytest.raises(ValueError):
        SpeechPolicy(deadline_seconds=-1.0)
    with pytest.raises(ValueError):
        Segmenter(max_bytes=4)


# --------------------------------------------------------------------------
# SIMULATION - deterministic synthetic timeline, not a measurement
# --------------------------------------------------------------------------

def _first_item_time(*, verify, deltas, verdict_at=None):
    """SIMULATION. Injected elapsed clock; no real audio, no real time."""
    controller, sink = build(verify=verify)
    first = None
    for seconds, text in deltas:
        controller.feed(text, session=SESSION,
                        lifecycle=LifecycleFacts(elapsed_seconds=seconds))
        if first is None and sink.spoken:
            first = seconds
    if verify:
        controller.verdict("ok", "", session=SESSION,
                           lifecycle=LifecycleFacts(elapsed_seconds=verdict_at))
        if first is None and sink.spoken:
            first = verdict_at
    return first


def test_simulation_streaming_speaks_sooner_than_full_answer_buffering():
    """SIMULATION, labelled: synthetic deltas and an injected elapsed clock.

    Not a latency measurement. It shows the structural trade-off only: with
    verification off the first sentence is speakable as soon as it completes,
    and with verification on nothing may be spoken before the verdict, so the
    first approved item cannot precede it.
    """
    deltas = [(0.0, "The distance is 515 kilometres. "),
              (0.4, "It takes about two hours by train. ")]
    verdict_at = 1.2
    streaming = _first_item_time(verify=False, deltas=deltas)
    buffered = _first_item_time(verify=True, deltas=deltas, verdict_at=verdict_at)
    assert streaming == 0.0
    assert buffered == verdict_at
    assert streaming < buffered


# --------------------------------------------------------------------------
# Bounded admission, release and settlement (independent r1 findings)
# --------------------------------------------------------------------------


def _drain_fully(controller, sink, *, limit=2000):
    """Complete every item the sink is handed, including items placed late."""
    steps = 0
    while controller.inflight is not None and steps < limit:
        controller.completed(controller.inflight.item_id, session=SESSION, lifecycle=CLEAR)
        steps += 1
    return steps


def test_a_refused_emission_identity_stays_retryable():
    # The identity of a delta refused for backpressure must not be committed:
    # the retry carries the identity the refusal was reported under.
    controller, sink = build(queue_max_items=1, queue_max_bytes=32,
                             held_max_bytes=16, segment_max_bytes=16)
    for text in ("Alpha. ", "Bravo. ", "Charlie. "):
        assert controller.feed(text, session=SESSION, lifecycle=CLEAR).status == "accepted"
    generated_before = controller.generated
    blocked = controller.feed("Deltaa. ", session=SESSION, lifecycle=CLEAR,
                              emission_id="refused-emission")
    assert blocked.status == "backpressure"
    assert blocked.reason == "queue_and_held_full"
    assert controller.generated == generated_before, "a refusal consumes no text"

    _drain_fully(controller, sink)
    retry = controller.feed("Deltaa. ", session=SESSION, lifecycle=CLEAR,
                            emission_id="refused-emission")
    assert retry.status == "accepted", "the refused identity is still retryable"
    _drain_fully(controller, sink)
    assert "".join(sink.texts) == "Alpha. Bravo. Charlie. Deltaa. "


def test_an_accepted_identity_still_suppresses_its_own_retry():
    controller, sink = build()
    first = controller.feed("Echo. ", session=SESSION, lifecycle=CLEAR, emission_id="id-1")
    assert first.status == "accepted"
    second = controller.feed("Echo. ", session=SESSION, lifecycle=CLEAR, emission_id="id-1")
    assert second.status == "duplicate"
    distinct = controller.feed("Echo. ", session=SESSION, lifecycle=CLEAR, emission_id="id-2")
    assert distinct.status == "accepted", "equal text under a new id is a new emission"
    _drain_fully(controller, sink)
    assert "".join(sink.texts) == "Echo. Echo. "


def test_an_approved_answer_progresses_through_public_events():
    # An approved answer wider than its bounds is refused as a unit, stays
    # retained, and then progresses as playback frees room. The verdict is not
    # re-evaluated: this is release progress, not a second approval.
    controller, sink = build(verify=True, draft_max_bytes=2048, held_max_bytes=64,
                             queue_max_items=1, queue_max_bytes=32, segment_max_bytes=32)
    text = "Word. " * 200
    assert controller.feed(text, session=SESSION, lifecycle=CLEAR).status == "accepted"
    first = controller.verdict("ok", "", session=SESSION, lifecycle=CLEAR)
    assert first.status == "backpressure"
    assert first.reason == "release_exceeds_bounds"
    assert sink.texts == [], "an answer that does not fit is not partly played"
    assert controller._queue_bytes <= 32 and controller._held_bytes <= 64
    assert controller.last_released, "the unplaceable segments are retained"

    _drain_fully(controller, sink)
    retry = controller.verdict("ok", "", session=SESSION, lifecycle=CLEAR)
    _drain_fully(controller, sink)
    assert retry.status in ("accepted", "backpressure")
    assert "".join(sink.texts) == text, "the whole approved answer is spoken"
    assert controller.approved_unplaced_bytes == 0, (
        "bytes that reached playback are no longer counted as unplaced")
    assert controller.unapproved_bytes == 0, (
        "an approved answer is never reported as unapproved")


def test_a_retained_stop_obligation_is_settled_by_its_own_completion():
    sink = MockSink(stop_raises=True)
    controller, sink = build(sink)
    stream(controller, "Active. Pending. ")
    retained_id = sink.active
    controller.cancel()
    assert controller.state is PlaybackState.STOP_UNCERTAIN
    assert controller.stop_unresolved is not None

    # The sink reports that exact item complete. That is the positive evidence
    # which settles the obligation; no second stop is needed to believe it.
    outcome = controller.completed(retained_id, session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "accepted"
    assert controller.stop_unresolved is None, "the retained obligation is resolved"
    assert controller.inflight is None
    assert controller.fence_reason is FenceReason.CANCELLED, "the fence still stands"
    assert controller.feed("Forbidden. ", session=SESSION, lifecycle=CLEAR).status == "fenced"


def test_a_retained_stop_obligation_is_settled_by_a_successful_stop_retry():
    sink = MockSink(stop_raises=True)
    controller, sink = build(sink)
    stream(controller, "Active. ")
    controller.cancel()
    assert controller.stop_unresolved is not None
    sink.stop_raises = False
    controller.cancel()
    assert controller.stop_unresolved is None
    assert controller.state is PlaybackState.STOPPED
    assert sink.stop_attempts == 2, "the retry really asked the sink again"
    assert controller.feed("Forbidden. ", session=SESSION, lifecycle=CLEAR).status == "fenced"


def test_a_foreign_completion_does_not_settle_a_retained_obligation():
    sink = MockSink(stop_raises=True)
    controller, sink = build(sink)
    stream(controller, "Active. ")
    retained_id = sink.active
    controller.cancel()
    foreign = SessionFacts(sink and SESSION.session_id, "owner-b", SESSION.epoch)
    outcome = controller.completed(retained_id, session=foreign, lifecycle=CLEAR)
    assert outcome.status == "fenced"
    assert controller.stop_unresolved is not None, "a foreign callback settles nothing"


def test_every_valid_queue_byte_limit_binds_while_the_sink_is_active():
    for limit in (16, 64):
        controller, sink = build(queue_max_items=2, queue_max_bytes=limit,
                                 held_max_bytes=128, segment_max_bytes=64)
        controller.feed("Seed. ", session=SESSION, lifecycle=CLEAR)
        text = "x" * 62 + ". "
        controller.feed(text, session=SESSION, lifecycle=CLEAR)
        assert controller._queue_bytes <= limit, (
            f"the queue byte ceiling of {limit} binds on the first waiting item")
        _drain_fully(controller, sink)
        assert "".join(sink.texts) == "Seed. " + text


def test_unicode_segments_obey_the_declared_byte_ceiling():
    texts = {"combining": "a" * 15 + "e\u0301" + " tail. ",
             "joiner": "a" * 13 + "x\u200dy" + " tail. ",
             "ascii": "a" * 40 + ". ",
             "cjk": "\u6c49" * 20}
    for kind, text in texts.items():
        segmenter = Segmenter(max_bytes=16)
        pieces = segmenter.push(text) + segmenter.flush()
        sizes = [len(piece.encode("utf-8")) for piece in pieces]
        assert "".join(pieces) == text, f"{kind} still reconstructs"
        assert max(sizes) <= 16, f"{kind} obeyed the ceiling, got {sizes}"
        for piece in pieces[:-1]:
            assert piece and not _segmenter._joinable(piece[0]), (
                "no segment starts with an attachment")


def test_the_exposed_playback_record_is_immutable():
    controller, sink = build()
    stream(controller, "Active. ")
    record = controller.inflight
    original = (record.item_id, record.text)
    for field, value in (("item_id", 999), ("text", "Altered externally.")):
        with pytest.raises((AttributeError, TypeError)):
            setattr(record, field, value)
    assert (record.item_id, record.text) == original
    assert str(record) == original[1], "the record is still its own text"
    _drain_fully(controller, sink)
    assert controller.inflight is None, "the real completion still matches"


# -- r3: the four adjacent findings ---------------------------------------------


def test_a_failed_direct_handover_does_not_start_the_next_segment():
    """R2-F01: the r2 direct handover played the next segment into a fence."""
    sink = MockSink(fail_on=lambda item_id, text: item_id == 1)
    controller, sink = build(sink, verify=True, queue_max_items=4, queue_max_bytes=16,
                             held_max_bytes=256, segment_max_bytes=64, draft_max_bytes=512)
    text = "One oversized segment. Another oversized segment. "
    stream(controller, text)
    outcome = controller.verdict("ok", text, session=SESSION, lifecycle=CLEAR)

    assert sink.attempted == [1], (
        "the failing segment is the only one the sink may be asked to speak")
    assert sink.texts == [], "and nothing after it reached playback"
    assert controller.fence_reason is FenceReason.SINK_FAILED
    assert controller.state is not PlaybackState.SPEAKING, (
        "a fenced controller must not still report itself speaking")
    outcome = controller.feed("Must remain fenced. ", session=SESSION, lifecycle=CLEAR)
    assert outcome.status == "fenced"


def test_a_failed_speak_stops_the_walk_before_the_next_segment():
    """R2-F01: the walk continued past a failure that had already closed admission."""
    sink = MockSink(fail_on=lambda item_id, text: item_id == 1)
    controller, sink = build(sink, queue_max_items=4, queue_max_bytes=16,
                             held_max_bytes=256, segment_max_bytes=64)
    stream(controller, "One oversized segment. Another oversized segment. ")
    assert sink.attempted == [1], "only the segment whose speak raised was attempted"
    assert controller.fence_reason is FenceReason.SINK_FAILED


def test_a_queued_segment_cannot_overtake_an_older_held_one():
    """R2-F02: a newer queued segment overtook an older held one."""
    controller, sink = build(queue_max_items=1, queue_max_bytes=32,
                             held_max_bytes=256, segment_max_bytes=64)
    texts = ["First. ", "Older. ", "Later held. ", "Newest. "]
    for text in texts[:3]:
        assert stream(controller, text).status == "accepted"
    # First. left through a direct handover, so Older. is queued while Later
    # held. waits in the held buffer. The next arrival must not jump it.
    controller.completed(controller.inflight.item_id, session=SESSION, lifecycle=CLEAR)
    assert stream(controller, texts[3]).status == "accepted"
    _drain_fully(controller, sink)
    assert sink.texts == texts, "accepted segments play in emission order"


def test_a_single_attachment_run_obeys_the_declared_ceiling():
    """R2-F03: one base plus ten marks is 21 bytes under a 16-byte ceiling."""
    text = "a" + "\u0301" * 10
    segmenter = Segmenter(max_bytes=16)
    pieces = segmenter.push(text) + segmenter.flush()
    sizes = [len(piece.encode("utf-8")) for piece in pieces]
    assert "".join(pieces) == text, "the run still reconstructs exactly"
    assert max(sizes) <= 16, f"no segment may exceed the ceiling, got {sizes}"


def test_a_repeated_verdict_resumes_a_refused_release_progressively():
    """R2-I01: the retained answer stays bounded and resumable, never truncated."""
    controller, sink = build(verify=True, draft_max_bytes=2048, held_max_bytes=64,
                             queue_max_items=1, queue_max_bytes=32, segment_max_bytes=32)
    text = "Word. " * 200
    assert stream(controller, text).status == "accepted"
    first = controller.verdict("ok", "", session=SESSION, lifecycle=CLEAR)
    assert first.status == "backpressure" and first.reason == "release_exceeds_bounds"
    assert sink.texts == [], "the first verdict is all-or-nothing: nothing plays"
    assert controller.last_released, "the unplaceable segments are retained"
    assert controller.approved_unplaced_bytes > 0

    before = controller.approved_unplaced_bytes
    second = controller.verdict("ok", "", session=SESSION, lifecycle=CLEAR)
    assert second.status in ("accepted", "backpressure")
    assert controller._queue_bytes <= 32 and controller._held_bytes <= 64, (
        "resuming never places past either bound")
    _drain_fully(controller, sink)
    assert "".join(sink.texts) == text, "the whole approved answer is spoken"
    assert controller._release_cursor == len(controller._approved_segments)
    assert controller.approved_unplaced_bytes < before, (
        "bytes that reached playback stop being reported as unplaced")
