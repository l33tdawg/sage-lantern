"""Disabled decision-only model router for the SAGE Lantern.

This module decides *where a request should go*. It does not go there. It opens
no sockets, performs no inference, imports no part of `sage_lantern`, spawns no
process and writes nothing. Given the same facts it returns the same `Decision`,
which is what makes the policy testable without a device.

Design position, stated once and defended in the proposal:

  Capability is a refusal, never a downgrade.

If a photo request has no qualified vision lane, the correct outcome is a refusal
the user can understand, not a text-only answer that pretends the photo was
seen. The shipped orchestrator already enforces this ("Language model does not
support photos; text fallback is forbidden"); this router keeps that property
rather than working around it.

Revision 2 addresses the six defects found in Rook's first review
(`work/ember-router-review-20261002/REVIEW.md`). The load-bearing change is
ordering: **identity and lifecycle are validated before every path**, including
the deterministic bypass, so no route can be taken on facts that have already
expired. The second is that **required capabilities are derived from the payload,
not from the caller's label**, so an attached photo can never be silently
dropped by a caller that mislabelled its own request.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Mapping

from . import policy


class RefusalCode(str, Enum):
    """Why a request could not be routed. Stable strings; they reach the UI."""

    NO_LANE = "no_lane"
    LANE_NOT_READY = "lane_not_ready"
    LANE_UNQUALIFIED = "lane_unqualified"
    CAPABILITY_MISSING = "capability_missing"
    CONTEXT_OVERFLOW = "context_overflow"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    STALE_SESSION = "stale_session"
    PORT_CONTENDED = "port_contended"
    ESCALATION_NOT_APPROVED = "escalation_not_approved"
    IMAGE_UNUSABLE = "image_unusable"
    #: The request was cancelled before it was admitted.
    CANCELLED = "cancelled"
    #: The device is sleeping or fencing; nothing may be admitted.
    LIFECYCLE_FENCED = "lifecycle_fenced"
    #: No lifecycle facts were supplied and policy refuses to assume them.
    LIFECYCLE_UNVERIFIED = "lifecycle_unverified"
    #: A fact was missing, mistyped or out of bounds. A programming error or a
    #: corrupted caller, never a user-facing condition.
    INVALID_FACTS = "invalid_facts"


class RouteRefused(RuntimeError):
    """Raised instead of returning a decision that would mislead the user."""

    def __init__(self, code: RefusalCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}:{detail}" if detail else code.value)


class InvalidFacts(ValueError):
    """A supplied fact failed strict validation.

    Deliberately a *different* type from `RouteRefused`. A refusal is a routing
    outcome a caller must handle and may surface to a user. An invalid fact is a
    caller bug and must not be confusable with "the device declined".
    """


class RequestClass(str, Enum):
    """How much thinking a request actually needs.

    Classification is intentionally rule-based and inspectable. A model-based
    classifier would add an inference call to decide whether to make an
    inference call, which is the wrong trade on a one-model device.
    """

    DETERMINISTIC = "deterministic"
    RESIDENT_SIMPLE = "resident_simple"
    RESIDENT_REASONING = "resident_reasoning"
    VISION = "vision"
    ESCALATION = "escalation"


class Capability(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    LONG_CONTEXT = "long_context"
    ESCALATION = "escalation"


class Readiness(str, Enum):
    """Lane lifecycle. Only READY may be selected."""

    STOPPED = "stopped"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


class StatusIntent(str, Enum):
    """The exact device questions a bypass handler may answer.

    This is a closed set on purpose. An open prefix match ("battery…",
    "storage…") silently captured `battery chemistry` and `storage of insulin`
    in review, answering general knowledge questions from device state. A closed
    enum means an unsupported phrasing routes to the model, which is merely
    slower, rather than being answered from the wrong source, which is wrong.
    """

    TIME = "time"
    BATTERY_LEVEL = "battery_level"
    SIGNAL_STRENGTH = "signal_strength"
    STORAGE_FREE = "storage_free"


#: Exact normalised phrasings per intent. Matched on the whole string, never a
#: prefix, so a longer general question cannot be captured by a shorter status
#: phrasing.
_INTENT_PHRASES: Mapping[StatusIntent, frozenset[str]] = {
    StatusIntent.TIME: frozenset({
        "what time is it",
        "what is the time",
        "what's the time",
        "whats the time",
        "tell me the time",
        "current time",
    }),
    StatusIntent.BATTERY_LEVEL: frozenset({
        "battery",
        "battery level",
        "battery percentage",
        "how much battery",
        "how much battery is left",
        "how much charge is left",
        "what is the battery level",
        "what's the battery level",
    }),
    StatusIntent.SIGNAL_STRENGTH: frozenset({
        "signal",
        "signal strength",
        "how strong is the signal",
        "what is the signal strength",
        "what's the signal strength",
        "how many bars",
    }),
    StatusIntent.STORAGE_FREE: frozenset({
        "storage",
        "storage free",
        "free storage",
        "how much storage is left",
        "how much space is left",
        "how much storage do i have",
    }),
}


@dataclass(frozen=True)
class HandlerFacts:
    """Whether a bypass handler can actually answer an intent, right now.

    Supplied by the caller. The router cannot read device state and does not
    pretend to: without a registered, available handler with a fresh
    observation, a status question is routed to the model like any other
    question. That is slower and honest, which beats fast and fabricated.
    """

    intent: StatusIntent
    registered: bool = False
    available: bool = False
    #: Monotonic seconds at which the value was observed.
    observed_at: float = 0.0

    def is_fresh(self, now: float, max_age: float) -> bool:
        if not (self.registered and self.available):
            return False
        age = now - self.observed_at
        # A negative age means the clock went backwards (suspend/resume, epoch
        # reset). Treat that as stale rather than fresh: it cannot be trusted.
        return 0.0 <= age <= max_age


@dataclass(frozen=True)
class LaneFacts:
    """Everything the router is allowed to know about a lane.

    Supplied by the caller. The router never probes a lane itself, so a decision
    can be replayed offline and a test never needs a server.
    """

    lane_id: str
    endpoint: str
    model: str
    capabilities: frozenset[Capability]
    readiness: Readiness = Readiness.STOPPED

    #: Provenance/qualification. The training roadmap states plainly that no
    #: current adapter is eligible for deployment, so this defaults to False and
    #: a lane must be *positively* qualified to be selectable.
    qualified: bool = False

    context_tokens: int = policy.TEXT_CONTEXT_TOKENS

    #: Cold-load cost. Zero for an already-resident lane.
    cold_load_seconds: float = 0.0

    #: True when this endpoint is claimed by more than one mutually exclusive
    #: service definition. This is a *configuration* observation, not proof of
    #: live contention: per-engine locks do not establish exclusion between
    #: independently running engines, and only post-gate measurement can.
    contended: bool = False

    #: True when the lane holds its own engine lock, so a request queues behind
    #: whatever that engine is already running. This proves serialisation
    #: *within* one engine, not mutual exclusion across engines.
    serialised: bool = True

    #: Declared precedence among equally-capable lanes. Lower wins. Explicit
    #: rather than inferred from context size, endpoint or name: a lane is not
    #: better because its context window is larger, and sorting by name would
    #: make routing depend on spelling.
    priority: int = 0

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class RequestFacts:
    """Everything the router is allowed to know about the request."""

    prompt: str
    owner_id: str
    session_epoch: int

    #: The caller's *label*. Advisory only. Required capabilities are derived
    #: from the payload below, never from this field, because a caller that
    #: mislabels an image-bearing request must not be able to have the image
    #: silently discarded.
    request_class: RequestClass | None = None

    #: The payload actually attached. `image_bytes > 0` is what makes a request
    #: an image request, regardless of `request_class`.
    has_image: bool = False
    image_bytes: int = 0
    image_width: int = 0
    image_height: int = 0

    #: Tokens the caller has committed to the prompt text.
    prompt_tokens: int = 0

    #: Set only by an explicit, user-visible choice.
    escalation_approved: bool = False

    #: Distinct failed attempts already spent on this request.
    attempts: int = 0

    #: Set when expanded context is deliberately requested. A caller may not
    #: receive a long-context lane without asking for one.
    long_context_requested: bool = False

    #: Set on requests that follow an earlier failed step in the same turn. Such
    #: steps are never answered from status state: a retry is a new question and
    #: must not be short-circuited by a cached fact.
    is_retry: bool = False

    #: Total wall-clock budget for the whole user-visible answer, shared across
    #: every lane attempt. `fallback_after_failure` deducts elapsed time from it
    #: rather than restarting it.
    total_budget_seconds: float = policy.RESIDENT_DEADLINE_SECONDS

    def total_required_capabilities(self) -> frozenset[Capability]:
        """Capabilities demanded by the payload and the explicit request.

        Derived from facts, not from `request_class`. This is the fix for a
        mislabelled image request reaching a text-only lane.
        """
        required = {Capability.TEXT}
        if self.attaches_image:
            required.add(Capability.IMAGE)
        if self.long_context_requested:
            required.add(Capability.LONG_CONTEXT)
        if self.escalation_approved:
            required.add(Capability.ESCALATION)
        return frozenset(required)

    @property
    def attaches_image(self) -> bool:
        """True when an image is actually present in the payload.

        `image_bytes` is authoritative and `has_image` is a redundant assertion.
        A request cannot claim to carry an image it does not carry: see
        `validate_request`.
        """
        return bool(self.has_image) or self.image_bytes > 0


@dataclass(frozen=True)
class SessionFacts:
    """Session identity the lane state was observed under."""

    owner_id: str
    epoch: int
    inference_guard_active: bool = False


@dataclass(frozen=True)
class LifecycleFacts:
    """Cancellation and power state at admission time.

    The first version of this candidate had no cancellation or sleep admission
    at all, and deducted elapsed work from nothing. Fallback then handed the
    next lane the original relative deadline, so a request could consume its
    whole budget, fail, and be granted the same budget again.

    Policy is explicit about the absence case: `REQUIRE_LIFECYCLE_FACTS` decides
    whether a missing `LifecycleFacts` is admitted or refused. The default is to
    refuse, because "we did not check" must not read the same as "we checked and
    it was fine".
    """

    cancelled: bool = False
    #: True while the device is entering or inside sleep, or a session/owner
    #: fence has been raised. Nothing may be admitted.
    fenced: bool = False
    #: Monotonic seconds already consumed by earlier work on this request.
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class Decision:
    """The routing outcome. Everything the caller needs and nothing more."""

    lane_id: str
    endpoint: str
    model: str
    request_class: RequestClass
    max_tokens: int
    #: Budget remaining for *this* lane attempt, after elapsed work and cold
    #: load are deducted from the shared total.
    deadline_seconds: float
    bypass_inference: bool = False

    #: Populated when the router fell back rather than selecting first choice.
    fallback_reason: str | None = None

    #: Honest per-decision notes surfaced to logs and the UI. Never claims
    #: success and never claims a lane was healthy when it was only assumed.
    notes: tuple[str, ...] = field(default_factory=tuple)


# --- Strict validation -----------------------------------------------------
#
# Review reproduction 4: `qualified="false"`, `session_epoch=True`, a NaN
# deadline and a negative prompt-token count were all accepted. Python's
# `bool` is a subclass of `int`, so `True` silently passed as an epoch, and
# `"false"` is truthy, so a string qualification selected an unqualified lane.
# Every fact below is checked exactly.


def _require_bool(name: str, value: object) -> bool:
    if type(value) is not bool:
        raise InvalidFacts(f"{name} must be bool, got {type(value).__name__}")
    return value


def _require_int(name: str, value: object, *, minimum: int | None = None,
                 maximum: int | None = None) -> int:
    # `type(...) is not int` also rejects bool, which is what we want.
    if type(value) is not int:
        raise InvalidFacts(f"{name} must be int, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise InvalidFacts(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise InvalidFacts(f"{name} must be <= {maximum}, got {value}")
    return value


def _require_finite_float(name: str, value: object, *, minimum: float = 0.0) -> float:
    if type(value) not in (int, float) or type(value) is bool:
        raise InvalidFacts(f"{name} must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidFacts(f"{name} must be finite, got {value!r}")
    if number < minimum:
        raise InvalidFacts(f"{name} must be >= {minimum}, got {number}")
    return number


def _require_str(name: str, value: object) -> str:
    if type(value) is not str:
        raise InvalidFacts(f"{name} must be str, got {type(value).__name__}")
    return value


def _require_identity(name: str, value: object) -> str:
    """An identity must be a non-empty string.

    An empty owner matched another empty owner and read as the same session, so
    a missing identity silently authenticated. Absence is not an identity.
    """
    text = _require_str(name, value)
    if not text.strip():
        raise InvalidFacts(f"{name} must not be empty")
    return text


def validate_handler(key: object, handler: object) -> None:
    """Strict validation of a status handler.

    Added after review probing showed `registered="false"` and
    `available="false"` selecting a deterministic bypass: both strings are
    truthy, so an explicitly *disabled* handler was treated as usable. The
    handler's `intent` must also agree with the key it is filed under, or a
    caller can register one intent and have another served.
    """
    if type(key) is not StatusIntent:
        raise InvalidFacts(f"handler key must be a StatusIntent, got {type(key).__name__}")
    if type(handler) is not HandlerFacts:
        raise InvalidFacts(f"handler must be HandlerFacts, got {type(handler).__name__}")
    if type(handler.intent) is not StatusIntent:
        raise InvalidFacts("handler.intent must be a StatusIntent")
    if handler.intent is not key:
        raise InvalidFacts(
            f"handler filed under {key.value} declares intent {handler.intent.value}"
        )
    _require_bool("handler.registered", handler.registered)
    _require_bool("handler.available", handler.available)
    _require_finite_float("handler.observed_at", handler.observed_at)


def validate_lane(lane: LaneFacts) -> None:
    if type(lane) is not LaneFacts:
        raise InvalidFacts(f"lane must be LaneFacts, got {type(lane).__name__}")
    _require_identity("lane_id", lane.lane_id)
    _require_str("endpoint", lane.endpoint)
    _require_str("model", lane.model)
    # Qualification must be a real boolean: `qualified="false"` is truthy.
    _require_bool("qualified", lane.qualified)
    _require_bool("contended", lane.contended)
    _require_bool("serialised", lane.serialised)
    if type(lane.readiness) is not Readiness:
        raise InvalidFacts("readiness must be a Readiness")
    if not isinstance(lane.capabilities, frozenset):
        raise InvalidFacts("capabilities must be a frozenset")
    for capability in lane.capabilities:
        if type(capability) is not Capability:
            raise InvalidFacts(f"unknown capability {capability!r}")
    _require_int("context_tokens", lane.context_tokens, minimum=1)
    _require_finite_float("cold_load_seconds", lane.cold_load_seconds)
    _require_int("priority", lane.priority)


def validate_session(session: SessionFacts) -> None:
    if type(session) is not SessionFacts:
        raise InvalidFacts(f"session must be SessionFacts, got {type(session).__name__}")
    _require_identity("session.owner_id", session.owner_id)
    # Not `bool`: `session_epoch=True` passed as the integer 1 before.
    _require_int("session.epoch", session.epoch, minimum=0)
    _require_bool("session.inference_guard_active", session.inference_guard_active)


def validate_request(request: RequestFacts) -> None:
    if type(request) is not RequestFacts:
        raise InvalidFacts(f"request must be RequestFacts, got {type(request).__name__}")
    _require_str("prompt", request.prompt)
    _require_identity("owner_id", request.owner_id)
    _require_int("session_epoch", request.session_epoch, minimum=0)
    if request.request_class is not None and type(request.request_class) is not RequestClass:
        raise InvalidFacts("request_class must be a RequestClass or None")
    _require_bool("has_image", request.has_image)
    _require_int("image_bytes", request.image_bytes, minimum=0)
    _require_int("image_width", request.image_width, minimum=0)
    _require_int("image_height", request.image_height, minimum=0)
    # Negative token counts were accepted and reduced the context estimate.
    _require_int("prompt_tokens", request.prompt_tokens, minimum=0)
    _require_bool("escalation_approved", request.escalation_approved)
    _require_int("attempts", request.attempts, minimum=0)
    _require_bool("long_context_requested", request.long_context_requested)
    _require_bool("is_retry", request.is_retry)
    # NaN passed because every comparison against NaN is False.
    _require_finite_float("total_budget_seconds", request.total_budget_seconds)
    # Note: `has_image=True` with zero bytes is deliberately NOT an
    # `InvalidFacts` here. It is a coherent (if useless) declaration, and the
    # useful refusal is `IMAGE_UNUSABLE:empty` from `_check_image`, which tells
    # the caller the image arrived empty. Only the type of each field is a
    # caller bug.


def validate_lifecycle(lifecycle: LifecycleFacts) -> None:
    if type(lifecycle) is not LifecycleFacts:
        raise InvalidFacts(f"lifecycle must be LifecycleFacts, got {type(lifecycle).__name__}")
    _require_bool("lifecycle.cancelled", lifecycle.cancelled)
    _require_bool("lifecycle.fenced", lifecycle.fenced)
    _require_finite_float("lifecycle.elapsed_seconds", lifecycle.elapsed_seconds)


# --- Classification --------------------------------------------------------

_REASONING_MARKERS = (
    "why",
    "explain",
    "compare",
    "step by step",
    "prove",
    "derive",
    "plan",
    "trade-off",
    "tradeoff",
    "analyse",
    "analyze",
)


def resolve_status_intent(prompt: str) -> StatusIntent | None:
    """Map an exact status phrasing to a supported intent, or None.

    Whole-string equality against a closed phrase list. A prefix match would
    re-capture `battery chemistry`, so there is no prefix matching here at all.
    """
    if type(prompt) is not str:
        raise InvalidFacts("prompt must be a string")
    normalised = normalise(prompt)
    for intent, phrases in _INTENT_PHRASES.items():
        if normalised in phrases:
            return intent
    return None


def normalise(prompt: str) -> str:
    return " ".join(prompt.strip().lower().split())


def classify(request: RequestFacts) -> RequestClass:
    """Classify a request's *model* needs. Not a routing decision.

    The deterministic intent is resolved separately by `resolve_status_intent`
    and only becomes a bypass when a handler is registered, available and
    fresh. A status phrasing with no handler falls through to the model, so the
    model-facing class for such a phrasing is an ordinary question class.
    """
    validate_request(request)
    if request.escalation_approved:
        return RequestClass.ESCALATION
    if request.attaches_image:
        return RequestClass.VISION
    return classify_prompt(request.prompt, long_context=request.long_context_requested)


def classify_prompt(prompt: str, *, long_context: bool = False) -> RequestClass:
    """Classify a bare prompt. Exposed because classification is testable alone.

    Deliberately does NOT return DETERMINISTIC. Whether a status question can be
    answered without a model depends on whether a handler exists and is fresh,
    which is a routing fact rather than a property of the text, so it is decided
    in `route` where those facts are available.
    """
    if type(prompt) is not str:
        raise InvalidFacts("prompt must be a string")
    if long_context:
        return RequestClass.RESIDENT_REASONING
    normalised = normalise(prompt)
    if not normalised:
        raise InvalidFacts("prompt must not be empty")
    if any(marker in normalised for marker in _REASONING_MARKERS):
        return RequestClass.RESIDENT_REASONING
    return RequestClass.RESIDENT_SIMPLE


# --- Gating ----------------------------------------------------------------

#: A stand-in lane for the deterministic bypass, which has no cold load.
#: Exists so the bypass runs the same budget check as every other path rather
#: than computing its own deadline.
_NO_COLD_LOAD = LaneFacts(
    lane_id="deterministic",
    endpoint="",
    model="",
    capabilities=frozenset(),
    cold_load_seconds=0.0,
)


def _budget_tokens(request_class: RequestClass) -> int:
    if request_class is RequestClass.VISION:
        return policy.IMAGE_MAX_TOKENS
    if request_class is RequestClass.ESCALATION:
        return policy.ESCALATION_MAX_TOKENS
    return policy.TEXT_MAX_TOKENS


def _check_session(request: RequestFacts, session: SessionFacts) -> None:
    """Identity and lifecycle check. Runs before *every* path.

    Review reproduction 1: this used to run only inside the lane gate, so a
    deterministic request with a stale owner returned a Decision without ever
    being checked. It is now the first thing `route` does.
    """
    if session.owner_id != request.owner_id:
        raise RouteRefused(RefusalCode.STALE_SESSION, "owner_changed")
    if session.epoch != request.session_epoch:
        raise RouteRefused(RefusalCode.STALE_SESSION, "epoch_changed")


def _check_lifecycle(lifecycle: LifecycleFacts | None) -> float:
    """Refuse cancelled or fenced work. Returns elapsed seconds."""
    if lifecycle is None:
        if policy.REQUIRE_LIFECYCLE_FACTS:
            raise RouteRefused(RefusalCode.LIFECYCLE_UNVERIFIED, "no_lifecycle_facts")
        return 0.0
    validate_lifecycle(lifecycle)
    if lifecycle.cancelled:
        raise RouteRefused(RefusalCode.CANCELLED, "cancelled_before_admission")
    if lifecycle.fenced:
        raise RouteRefused(RefusalCode.LIFECYCLE_FENCED, "sleep_or_session_fence")
    return lifecycle.elapsed_seconds


def _check_image(request: RequestFacts) -> None:
    """Validate an attached image. Applied before deterministic and escalation."""
    if not request.attaches_image:
        return
    if request.image_bytes <= 0:
        raise RouteRefused(RefusalCode.IMAGE_UNUSABLE, "empty")
    if request.image_bytes > policy.IMAGE_MAX_BYTES:
        raise RouteRefused(RefusalCode.IMAGE_UNUSABLE, "too_large")
    if request.image_width <= 0 or request.image_height <= 0:
        raise RouteRefused(RefusalCode.IMAGE_UNUSABLE, "no_dimensions")
    if (request.image_width > policy.IMAGE_MAX_DIMENSION
            or request.image_height > policy.IMAGE_MAX_DIMENSION):
        raise RouteRefused(RefusalCode.IMAGE_UNUSABLE, "dimension")
    if request.image_width * request.image_height > policy.IMAGE_MAX_PIXELS:
        raise RouteRefused(RefusalCode.IMAGE_UNUSABLE, "pixels")


def _check_context(lane: LaneFacts, request: RequestFacts, output_budget: int) -> None:
    """Refuse before issuing a request that cannot fit.

    Review reproduction 5: the first version reserved `ANSWER_TOKEN_RESERVE`
    (256) while *selecting* a 512-token output budget, so a request could be
    admitted at 1916 of 2048 and still ask for 512 tokens — 2172 in a
    2048-token window. The reservation now equals the budget actually chosen,
    and the input count is defined once, by contract:

      `request.prompt_tokens` is the **complete** input: prompt text plus policy
      plus every recalled item. It does not include the output budget.

    That contract removes the double counting: the old arithmetic added policy
    and recall reserves on top of a caller count that already contained them.
    """
    committed = request.prompt_tokens + output_budget
    if committed > lane.context_tokens:
        raise RouteRefused(
            RefusalCode.CONTEXT_OVERFLOW,
            f"{committed}>{lane.context_tokens}",
        )


def _check_deadline(elapsed: float, request: RequestFacts, lane: LaneFacts,
                    *, minimum: float = 1.0) -> float:
    """Remaining budget for one attempt, from the single shared total.

    Cold load is charged against the same total, because it is time the user
    waits for.
    """
    remaining = request.total_budget_seconds - elapsed - max(0.0, lane.cold_load_seconds)
    if remaining < minimum:
        raise RouteRefused(RefusalCode.DEADLINE_EXCEEDED, f"{remaining:.3f}s")
    return remaining


def _gate(lane: LaneFacts, request: RequestFacts, session: SessionFacts,
          required: frozenset[Capability], output_budget: int,
          elapsed: float) -> float:
    """Apply every lane precondition. Returns the effective deadline."""
    validate_lane(lane)
    if not lane.qualified:
        raise RouteRefused(RefusalCode.LANE_UNQUALIFIED, lane.lane_id)
    missing = required - lane.capabilities
    if missing:
        # The payload's needs, not the caller's label. An image-bearing request
        # reaching here against a text-only lane is refused, not downgraded.
        raise RouteRefused(
            RefusalCode.CAPABILITY_MISSING,
            ",".join(sorted(capability.value for capability in missing)),
        )
    if lane.readiness is not Readiness.READY:
        raise RouteRefused(RefusalCode.LANE_NOT_READY, lane.readiness.value)
    if lane.contended:
        raise RouteRefused(RefusalCode.PORT_CONTENDED, lane.lane_id)
    _check_context(lane, request, output_budget)
    return _check_deadline(elapsed, request, lane)


# --- Selection -------------------------------------------------------------


def _select_lane(
    request_class: RequestClass,
    lanes: Mapping[str, LaneFacts],
    request: RequestFacts,
    session: SessionFacts,
    required: frozenset[Capability],
    output_budget: int,
    elapsed: float,
    *,
    guarded: bool,
) -> LaneFacts:
    candidates = list(lanes.values())
    if not candidates:
        raise RouteRefused(RefusalCode.NO_LANE, request_class.value)
    for lane in candidates:
        validate_lane(lane)

    # The inference guard is a hard binding, not a preference. If it is active,
    # a text request physically cannot be served by any endpoint other than the
    # guarded one, because the adapter refuses to admit it.
    if guarded and request_class in (RequestClass.DETERMINISTIC,
                                     RequestClass.RESIDENT_SIMPLE,
                                     RequestClass.RESIDENT_REASONING):
        eligible = [lane for lane in candidates if lane.endpoint == policy.GUARDED_TEXT_ENDPOINT]
        if not eligible:
            raise RouteRefused(RefusalCode.NO_LANE, "guard_binding")
        candidates = eligible

    # Escalation lanes are opt-in, never a silent promotion target. A resident
    # request that fails must not quietly become an expensive one; that is
    # precisely the runaway the policy forbids.
    if Capability.ESCALATION not in required:
        candidates = [lane for lane in candidates if not lane.has(Capability.ESCALATION)]
        if not candidates:
            raise RouteRefused(RefusalCode.NO_LANE, "escalation_lane_requires_opt_in")

    capable = [lane for lane in candidates if required <= lane.capabilities]
    if not capable:
        raise RouteRefused(
            RefusalCode.CAPABILITY_MISSING,
            ",".join(sorted(capability.value for capability in required)),
        )

    # Prefer an explicitly higher-priority lane, then a warm one. A cold load is
    # charged against the same budget, so a warm lower-priority lane still
    # beats a cold higher-priority one.
    capable.sort(key=lambda lane: (lane.cold_load_seconds, lane.priority, lane.lane_id))

    last: RouteRefused | None = None
    for lane in capable:
        try:
            _gate(lane, request, session, required, output_budget, elapsed)
            return lane
        except RouteRefused as refusal:
            last = refusal
    assert last is not None
    raise last


def _decision_notes(lane: LaneFacts, request: RequestFacts, guarded: bool,
                    elapsed: float) -> tuple[str, ...]:
    notes: list[str] = []
    if elapsed > 0:
        notes.append(f"{elapsed:.1f}s of the shared budget already spent")
    if lane.cold_load_seconds > 0:
        notes.append(f"cold load {lane.cold_load_seconds:.1f}s charged to the shared budget")
    if lane.serialised:
        notes.append("engine serialises; this request may queue behind current work")
    if guarded and lane.endpoint == policy.GUARDED_TEXT_ENDPOINT:
        notes.append("guarded endpoint binding honoured")
    if lane.contended:
        notes.append("endpoint has more than one service definition")
    notes.append("candidate decision only; no inference performed")
    return tuple(notes)


def route(
    request: RequestFacts,
    lanes: Mapping[str, LaneFacts],
    session: SessionFacts,
    *,
    lifecycle: LifecycleFacts | None = None,
    handlers: Mapping[StatusIntent, HandlerFacts] | None = None,
    now: float = 0.0,
) -> Decision:
    """Decide where `request` should go, or refuse with a reason.

    Pure function. Same inputs, same output, no side effects, no clock reads —
    `now` is supplied by the caller so a decision is reproducible.
    """
    validate_request(request)
    validate_session(session)
    if not isinstance(lanes, Mapping):
        raise InvalidFacts("lanes must be a mapping")

    # 1. Identity and lifecycle first, before *any* path can be taken.
    #    Review reproduction 1 lived here: the deterministic bypass returned
    #    before this check, so a stale owner was accepted.
    _check_session(request, session)
    elapsed = _check_lifecycle(lifecycle)

    # 2. Validate the payload's image before anything can bypass it.
    _check_image(request)

    # 3. Required capabilities come from the payload, not the caller's label.
    required = request.total_required_capabilities()

    # 4. Resolve the caller's label into a class, agreeing with it only where
    #    the payload does not contradict it.
    request_class = classify(request)
    if request.request_class is RequestClass.ESCALATION and not request.escalation_approved:
        raise InvalidFacts("request_class ESCALATION requires escalation_approved")
    if request_class is RequestClass.ESCALATION:
        if not request.escalation_approved:
            raise RouteRefused(RefusalCode.ESCALATION_NOT_APPROVED, "no_opt_in")
        if request.attempts < policy.ESCALATION_MIN_ATTEMPTS:
            raise RouteRefused(RefusalCode.ESCALATION_NOT_APPROVED, "no_failed_attempt")

    # 5. Deterministic bypass. Only for a closed-set intent with a registered,
    #    available, fresh handler, and never on a retry step.
    #
    #    The bypass still spends the request's budget: a bypass with no time
    #    left is refused like any other path. Reviewer probing showed this
    #    returning a decision with a negative deadline.
    if not request.attaches_image and not request.escalation_approved and not request.is_retry:
        intent = resolve_status_intent(request.prompt)
        if intent is not None:
            handler = (handlers or {}).get(intent)
            if handler is not None:
                validate_handler(intent, handler)
            if handler is not None and handler.is_fresh(now, policy.STATUS_MAX_AGE_SECONDS):
                remaining = _check_deadline(
                    elapsed, request, _NO_COLD_LOAD,
                    minimum=policy.MIN_DEADLINE_SECONDS,
                )
                return Decision(
                    lane_id="deterministic",
                    endpoint="",
                    model="",
                    request_class=RequestClass.DETERMINISTIC,
                    max_tokens=0,
                    deadline_seconds=remaining,
                    bypass_inference=True,
                    notes=(
                        f"bypass to the registered {intent.value} handler; "
                        "no model involved",
                        "answer must come from that handler's current value",
                    ),
                )
            # No usable handler: this is an ordinary question, not a status query
            # we can answer. Fall through to the model rather than fabricating a
            # device-state answer.
            request_class = classify_prompt(
                request.prompt, long_context=request.long_context_requested
            )

    output_budget = _budget_tokens(request_class)
    guarded = session.inference_guard_active

    lane = _select_lane(request_class, lanes, request, session, required,
                        output_budget, elapsed, guarded=guarded)
    deadline = _check_deadline(elapsed, request, lane)

    return Decision(
        lane_id=lane.lane_id,
        endpoint=lane.endpoint,
        model=lane.model,
        request_class=request_class,
        max_tokens=output_budget,
        deadline_seconds=deadline,
        bypass_inference=False,
        notes=_decision_notes(lane, request, guarded, elapsed),
    )


def fallback_after_failure(
    request: RequestFacts,
    lanes: Mapping[str, LaneFacts],
    session: SessionFacts,
    *,
    failed_lane_id: str,
    lifecycle: LifecycleFacts | None = None,
    handlers: Mapping[StatusIntent, HandlerFacts] | None = None,
    now: float = 0.0,
) -> Decision:
    """Re-route after a lane failed, excluding the lane that just failed.

    Refuses rather than escalating unless escalation was explicitly approved and
    enabled by policy. There is no silent quality-triggered escalation.

    Review gap: the first version handed the replacement lane the original
    relative deadline, so a request could spend its whole budget, fail, and be
    granted the same budget again. The caller now passes `lifecycle` with the
    time actually spent, and that is deducted from the one shared total.
    """
    validate_request(request)
    validate_session(session)
    _check_session(request, session)
    elapsed = _check_lifecycle(lifecycle)

    remaining = {lane_id: lane for lane_id, lane in lanes.items() if lane_id != failed_lane_id}
    if not remaining:
        raise RouteRefused(RefusalCode.NO_LANE, "no_remaining_lane")

    # Escalation stays refused unless explicitly approved by the caller and
    # enabled in policy; the attempt is marked as a retry so a failed step
    # cannot be answered from cached status state.
    retry = replace(
        request,
        escalation_approved=request.escalation_approved and policy.ALLOW_AUTOMATIC_ESCALATION,
        attempts=request.attempts + 1,
        is_retry=True,
    )
    if request.escalation_approved and not policy.ALLOW_AUTOMATIC_ESCALATION:
        raise RouteRefused(RefusalCode.ESCALATION_NOT_APPROVED, "automatic_escalation_disabled")

    decision = route(
        retry,
        remaining,
        session,
        lifecycle=LifecycleFacts(
            cancelled=lifecycle.cancelled if lifecycle else False,
            fenced=lifecycle.fenced if lifecycle else False,
            elapsed_seconds=elapsed,
        ),
        handlers=handlers,
        now=now,
    )
    return replace(decision, fallback_reason=f"{failed_lane_id}_failed")
