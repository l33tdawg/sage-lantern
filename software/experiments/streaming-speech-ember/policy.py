"""Tunable bounds and verdict policy for the disabled streaming-speech candidate.

Pure data and validation. No audio, no runtime import, no I/O. The numbers live
here rather than inside the controller so they can be argued about and changed
without touching the state machine.
"""
from __future__ import annotations

from dataclasses import dataclass

# How a completed verification verdict maps onto what may be spoken.
#
# The runtime's existing rule (orchestrator.py:136-152 and
# answer_verification.parse_verdict) is that only "corrected" replaces the draft
# text and an inconclusive check leaves the draft in place. That is a rule about
# which *text* survives, and it says nothing about whether the surviving text was
# approved. Verification-before-speech needs the second question answered, so the
# two are recorded separately here.
#
# An approval to speak exists only where the verifier actually returned one:
#   "ok"        -> the draft is approved as written
#   "corrected" -> the replacement is approved
#   "unsure"    -> no verdict was reached; nothing is approved
#   "unavailable" -> the same, and no more trustworthy for being an outage
#
# Mapping an inconclusive verdict onto the draft would read "we could not check
# this" as "this passed", which is the one inference verification-before-speech
# exists to prevent. "unavailable" is deliberately not treated as a softer
# failure than "unsure": being unable to ask is not an answer.
VERDICT_RELEASE = {
    "ok": "draft",
    "unsure": None,
    "unavailable": None,
    "corrected": "corrected",
}
VERDICTS = tuple(VERDICT_RELEASE)


def _require_int(name, value, *, minimum):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an int >= {minimum}")
    return value


def _require_finite(name, value, *, minimum):
    if type(value) is not float or value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be a finite float")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class SpeechPolicy:
    """Bounds for one controller. Every bound is enforced, none is advisory.

    queue_*  : items ready for the sink. The sink queue is the only place a
               release-ready segment waits, and it is bounded in items and bytes.
    held_max_bytes : overflow for segments that could not enter the queue. It is
               bounded in bytes rather than items because a delta can segment
               into an arbitrary number of sentences; bounding the bytes is what
               makes "nothing is dropped" provable. When it is full the
               controller applies backpressure instead of accepting more text.
    draft_max_bytes : the whole-answer buffer used while verification is enabled.
               A draft past this bound is refused rather than grown without limit.
    deadline_seconds : wall time for one speech session, measured on the injected
               clock. Past it the session is fenced.
    verify   : whether a verdict must arrive before anything may be spoken.
    """

    queue_max_items: int = 4
    queue_max_bytes: int = 1024
    held_max_bytes: int = 4096
    segment_max_bytes: int = 512
    draft_max_bytes: int = 16384
    deadline_seconds: float = 30.0
    verify: bool = False

    def __post_init__(self):
        _require_int("queue_max_items", self.queue_max_items, minimum=1)
        _require_int("queue_max_bytes", self.queue_max_bytes, minimum=16)
        _require_int("held_max_bytes", self.held_max_bytes, minimum=16)
        _require_int("segment_max_bytes", self.segment_max_bytes, minimum=16)
        _require_int("draft_max_bytes", self.draft_max_bytes, minimum=64)
        _require_finite("deadline_seconds", self.deadline_seconds, minimum=0.001)
        if type(self.verify) is not bool:
            raise ValueError("verify must be a bool")


DEFAULT_POLICY = SpeechPolicy()
