"""Checks for the disabled model-router candidate.

These tests exercise *decisions only*. No inference server, no device, no
network. Every lane is described by data, which is the point: the selection
policy is falsifiable offline.

Run from the repository root:

    PYTHONPATH=.:src python3 -m pytest scripts/tests/test_ember_model_router.py -q
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest

# The reserved candidate directory keeps Rook's hyphenated path boundary, so it
# is not a valid Python identifier. Register the whole dotted chain synthetically
# under the real package path so tests read naturally, and load the two modules
# by file path rather than renaming the directory.
#
# This is deliberately self-contained: it registers `software`,
# `software.experiments` and the candidate here, rather than adding
# `__init__.py` files outside the reserved directories. Those markers were
# removed at review request; nothing else in the tree imports this path.
ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE = ROOT / "software" / "experiments" / "model-router-ember"
_PREFIX = "software.experiments.model_router_ember"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if _PREFIX not in sys.modules:
    import importlib.util
    import types

    # (module name, directory it exposes as __path__)
    _SYNTHETIC = (
        ("software", ROOT / "software"),
        ("software.experiments", ROOT / "software" / "experiments"),
    )
    for module_name, directory in _SYNTHETIC:
        if module_name in sys.modules:
            continue
        parent = types.ModuleType(module_name)
        parent.__path__ = [str(directory)]  # type: ignore[attr-defined]
        sys.modules[module_name] = parent

    package = types.ModuleType(_PREFIX)
    package.__path__ = [str(_CANDIDATE)]  # type: ignore[attr-defined]
    sys.modules[_PREFIX] = package
    setattr(sys.modules["software.experiments"], "model_router_ember", package)

    for name in ("policy", "router"):
        spec = importlib.util.spec_from_file_location(
            f"{_PREFIX}.{name}", _CANDIDATE / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PREFIX}.{name}"] = module
        setattr(package, name, module)
        spec.loader.exec_module(module)

from software.experiments.model_router_ember import policy, router as _router  # noqa: E402

from software.experiments.model_router_ember import policy  # noqa: F401  (remapped above)

Capability = _router.Capability
Decision = _router.Decision
HandlerFacts = _router.HandlerFacts
InvalidFacts = _router.InvalidFacts
LaneFacts = _router.LaneFacts
LifecycleFacts = _router.LifecycleFacts
Readiness = _router.Readiness
RefusalCode = _router.RefusalCode
RequestClass = _router.RequestClass
RequestFacts = _router.RequestFacts
RouteRefused = _router.RouteRefused
SessionFacts = _router.SessionFacts
StatusIntent = _router.StatusIntent
classify = _router.classify_prompt
classify_facts = _router.classify
fallback_after_failure = _router.fallback_after_failure
resolve_status_intent = _router.resolve_status_intent
_route_impl = _router.route
_fallback_impl = _router.fallback_after_failure

# --- Fixtures --------------------------------------------------------------

#: Lifecycle facts every routing call must now supply.
#:
#: `policy.REQUIRE_LIFECYCLE_FACTS` is True, so an omitted lifecycle is a
#: `LIFECYCLE_UNVERIFIED` refusal rather than an assumed-clear admission. That
#: is the point of the revision: the router cannot tell "nobody asked about
#: cancellation" apart from "cancellation was checked and clear" unless the
#: caller says so. Tests therefore declare a clear lifecycle explicitly, and
#: the tests that check the refusal pass None on purpose.
CLEAR = LifecycleFacts()


def clear_lifecycle(**overrides) -> LifecycleFacts:
    return LifecycleFacts(**overrides)


# The wrappers below default only the lifecycle, so the pre-existing test bodies
# keep testing what they were written to test (lane selection, gating, budgets)
# in a state where cancellation and sleep are explicitly known clear. Every
# lifecycle-specific behaviour is asserted directly against `_route_impl` and
# `_fallback_impl` in the revision-2 section at the end of this file. A default
# here is a declared fact, not an assumed one: pass `lifecycle=None` to exercise
# the unverified path.
def route(request, lanes, session, *, lifecycle=CLEAR, **kwargs):
    return _route_impl(request, lanes, session, lifecycle=lifecycle, **kwargs)


def fallback_after_failure(request, lanes, session, *, lifecycle=CLEAR, **kwargs):
    return _fallback_impl(request, lanes, session, lifecycle=lifecycle, **kwargs)


def text_lane(**overrides) -> LaneFacts:
    base = dict(
        lane_id="resident-text",
        endpoint=policy.GUARDED_TEXT_ENDPOINT,
        model="lantern-native5-2b",
        capabilities=frozenset({Capability.TEXT}),
        readiness=Readiness.READY,
        qualified=True,
        context_tokens=policy.TEXT_CONTEXT_TOKENS,
        cold_load_seconds=0.0,
    )
    base.update(overrides)
    return LaneFacts(**base)


def vision_lane(**overrides) -> LaneFacts:
    base = dict(
        lane_id="vision",
        endpoint=policy.CONTENDED_VISION_ENDPOINT,
        model="lantern-qwen35-08b-vision",
        capabilities=frozenset({Capability.TEXT, Capability.IMAGE, Capability.LONG_CONTEXT}),
        readiness=Readiness.READY,
        qualified=True,
        context_tokens=policy.VISION_CONTEXT_TOKENS,
        cold_load_seconds=0.0,
        contended=False,
    )
    base.update(overrides)
    return LaneFacts(**base)


def escalation_lane(**overrides) -> LaneFacts:
    base = dict(
        lane_id="escalation",
        endpoint="http://127.0.0.1:8083",
        model="specialist",
        capabilities=frozenset({Capability.TEXT, Capability.ESCALATION}),
        readiness=Readiness.READY,
        qualified=True,
        context_tokens=8192,
        cold_load_seconds=6.0,
    )
    base.update(overrides)
    return LaneFacts(**base)


def make_request(**overrides) -> RequestFacts:
    base = dict(
        prompt="what is the capital of Australia",
        request_class=RequestClass.RESIDENT_SIMPLE,
        owner_id="owner-a",
        session_epoch=7,
    )
    base.update(overrides)
    return RequestFacts(**base)


def session(**overrides) -> SessionFacts:
    base = dict(owner_id="owner-a", epoch=7)
    base.update(overrides)
    return SessionFacts(**base)


def lanes(*items) -> dict:
    return {lane.lane_id: lane for lane in items}


# --- Classification --------------------------------------------------------


@pytest.mark.parametrize("prompt", [
    "battery",
    "battery level",
    "what time is it",
    "signal",
    "storage",
])
def test_deterministic_intents_resolve_to_a_supported_intent(prompt):
    # Classification no longer returns DETERMINISTIC: whether a status question
    # can skip the model depends on a handler existing and being fresh, which is
    # a routing fact rather than a property of the text.
    assert resolve_status_intent(prompt) is not None


@pytest.mark.parametrize("prompt", [
    "why does the tide rise",
    "explain how a radio works",
    "compare LoRa and WiFi",
    "plan a route to the market",
])
def test_reasoning_markers_route_to_reasoning(prompt):
    assert classify(prompt) is RequestClass.RESIDENT_REASONING


def test_plain_question_is_simple():
    assert classify("what is the capital of Australia") is RequestClass.RESIDENT_SIMPLE


def test_image_payload_forces_vision_class():
    # Derived from the payload, not from a caller-supplied flag.
    facts = make_request(prompt="what is this", image_bytes=2048,
                         image_width=320, image_height=240)
    assert classify_facts(facts) is RequestClass.VISION


def test_image_bearing_request_is_vision_even_when_the_caller_says_simple():
    # Review reproduction 2: a mislabelled image request must not select a
    # text-only lane and silently discard the photo.
    facts = make_request(request_class=RequestClass.RESIDENT_SIMPLE, image_bytes=2048,
                         image_width=320, image_height=240)
    assert classify_facts(facts) is RequestClass.VISION


def test_escalation_approval_forces_escalation_class():
    facts = make_request(escalation_approved=True)
    assert classify_facts(facts) is RequestClass.ESCALATION


def test_classification_rejects_non_string():
    with pytest.raises(InvalidFacts):
        classify(None)  # type: ignore[arg-type]


def test_classification_rejects_empty_prompt():
    with pytest.raises(ValueError):
        classify("   ")


def test_deterministic_prefix_must_be_short_and_leading():
    # A long sentence that merely mentions battery is not a status request.
    prompt = "battery technology has improved enormously over the last twenty years"
    assert classify(prompt) is not RequestClass.DETERMINISTIC


# --- Deterministic bypass --------------------------------------------------
#
# The bypass is no longer reachable from a caller-supplied label. A `DETERMINISTIC`
# request_class is now ignored: the router resolves a closed-set intent from the
# prompt and only bypasses when a handler is registered, available and fresh.
# Review reproduction 3 showed the old prefix matcher answering `battery
# chemistry` and `storage of insulin` from device state; these tests keep that
# closed.


def status_handlers(intent=StatusIntent.BATTERY_LEVEL, **overrides) -> dict:
    base = dict(intent=intent, registered=True, available=True, observed_at=0.0)
    base.update(overrides)
    return {intent: HandlerFacts(**base)}


def test_deterministic_decision_bypasses_inference_entirely():
    result = route(
        make_request(prompt="battery level"),
        lanes(text_lane()),
        session(),
        handlers=status_handlers(),
        now=0.0,
    )
    assert result.bypass_inference is True
    assert result.max_tokens == 0
    assert result.endpoint == ""
    assert result.lane_id == "deterministic"


def test_deterministic_bypass_survives_every_lane_being_broken():
    broken = lanes(text_lane(readiness=Readiness.FAILED, qualified=False))
    result = route(
        make_request(prompt="battery level"),
        broken,
        session(),
        handlers=status_handlers(),
        now=0.0,
    )
    assert result.bypass_inference is True


def test_a_caller_label_cannot_forge_a_deterministic_bypass():
    # The old API let a caller assert DETERMINISTIC and skip the model. It no
    # longer can: no handler means no bypass, whatever the label says.
    result = route(
        make_request(prompt="what is the capital of Australia",
                request_class=RequestClass.DETERMINISTIC),
        lanes(text_lane()),
        session(),
    )
    assert result.bypass_inference is False


def test_status_phrasing_without_a_handler_routes_to_the_model():
    # Honest and slow beats fast and fabricated.
    result = route(make_request(prompt="battery level"), lanes(text_lane()), session())
    assert result.bypass_inference is False
    assert result.lane_id == "resident-text"


def test_status_phrasing_with_a_stale_handler_routes_to_the_model():
    stale = status_handlers(observed_at=0.0)
    result = route(
        make_request(prompt="battery level"),
        lanes(text_lane()),
        session(),
        handlers=stale,
        now=policy.STATUS_MAX_AGE_SECONDS + 1.0,
    )
    assert result.bypass_inference is False


def test_status_phrasing_with_an_unavailable_handler_routes_to_the_model():
    result = route(
        make_request(prompt="battery level"),
        lanes(text_lane()),
        session(),
        handlers=status_handlers(available=False),
        now=0.0,
    )
    assert result.bypass_inference is False


def test_a_retry_is_never_answered_from_status_state():
    # A second attempt in a turn is a new question; a cached reading must not
    # short-circuit it.
    result = route(
        make_request(prompt="battery level", is_retry=True),
        lanes(text_lane()),
        session(),
        handlers=status_handlers(),
        now=0.0,
    )
    assert result.bypass_inference is False


def test_an_image_is_never_answered_by_the_deterministic_bypass():
    result = route(
        make_request(prompt="battery level", has_image=True, image_bytes=1024,
                image_width=64, image_height=64),
        lanes(vision_lane()),
        session(),
        handlers=status_handlers(),
        now=0.0,
    )
    assert result.bypass_inference is False
    assert result.request_class is RequestClass.VISION


# --- Narrow intent contract (review reproduction 3) ------------------------


@pytest.mark.parametrize("prompt", [
    # The exact three the review reproduced, plus the general shape.
    "battery chemistry",
    "storage of insulin",
    "signal processing",
    "what is battery chemistry",
    "how does signal processing work",
    "explain storage of insulin",
    "what time zone is Sydney in",
    "storage area networks",
])
def test_general_knowledge_is_never_a_status_intent(prompt):
    assert resolve_status_intent(prompt) is None


@pytest.mark.parametrize("prompt,intent", [
    ("battery level", StatusIntent.BATTERY_LEVEL),
    ("Battery Level", StatusIntent.BATTERY_LEVEL),
    ("  battery   level  ", StatusIntent.BATTERY_LEVEL),
    ("what time is it", StatusIntent.TIME),
    ("signal strength", StatusIntent.SIGNAL_STRENGTH),
    ("how much storage is left", StatusIntent.STORAGE_FREE),
])
def test_supported_status_phrasings_resolve_exactly(prompt, intent):
    assert resolve_status_intent(prompt) is intent


def test_an_unsupported_status_phrasing_falls_through_to_the_model():
    # "how much battery is left on my phone" is a different device; not ours.
    assert resolve_status_intent("how much battery is left on my phone") is None


# --- Unavailable and unqualified lanes -------------------------------------


def test_no_lanes_configured_refuses():
    with pytest.raises(RouteRefused) as caught:
        route(make_request(), {}, session())
    assert caught.value.code is RefusalCode.NO_LANE


def test_unqualified_lane_is_refused_not_used():
    with pytest.raises(RouteRefused) as caught:
        route(make_request(), lanes(text_lane(qualified=False)), session())
    assert caught.value.code is RefusalCode.LANE_UNQUALIFIED


@pytest.mark.parametrize("readiness", [Readiness.STOPPED, Readiness.LOADING, Readiness.FAILED])
def test_non_ready_lane_is_refused(readiness):
    with pytest.raises(RouteRefused) as caught:
        route(make_request(), lanes(text_lane(readiness=readiness)), session())
    assert caught.value.code is RefusalCode.LANE_NOT_READY


def test_unqualified_lane_blocks_even_when_another_is_ready():
    """The unqualified lane must not be silently substituted in."""
    broken = text_lane(lane_id="broken", qualified=False)
    with pytest.raises(RouteRefused) as caught:
        route(make_request(), lanes(broken), session())
    assert caught.value.code is RefusalCode.LANE_UNQUALIFIED


def test_capability_missing_is_distinct_from_no_lane():
    no_text = LaneFacts(
        lane_id="vision-only",
        endpoint=policy.CONTENDED_VISION_ENDPOINT,
        model="vision",
        capabilities=frozenset({Capability.IMAGE}),
        readiness=Readiness.READY,
        qualified=True,
    )
    with pytest.raises(RouteRefused) as caught:
        route(make_request(), lanes(no_text), session())
    assert caught.value.code is RefusalCode.CAPABILITY_MISSING


def test_ready_lane_is_selected_when_everything_is_healthy():
    result = route(make_request(), lanes(text_lane()), session())
    assert result.lane_id == "resident-text"
    assert result.bypass_inference is False
    assert result.max_tokens == policy.TEXT_MAX_TOKENS


# --- Inference-guard binding ----------------------------------------------


def test_guard_binding_forces_the_guarded_endpoint_for_text():
    elsewhere = text_lane(lane_id="elsewhere", endpoint="http://127.0.0.1:9999")
    with pytest.raises(RouteRefused) as caught:
        route(make_request(), lanes(elsewhere), session(inference_guard_active=True))
    assert caught.value.code is RefusalCode.NO_LANE


def test_guard_binding_is_satisfied_by_the_guarded_endpoint():
    result = route(make_request(), lanes(text_lane()), session(inference_guard_active=True))
    assert result.endpoint == policy.GUARDED_TEXT_ENDPOINT
    assert any("guarded endpoint binding honoured" in note for note in result.notes)


def test_guard_is_inactive_by_default_and_permits_other_endpoints():
    elsewhere = text_lane(lane_id="elsewhere", endpoint="http://127.0.0.1:9999")
    result = route(make_request(), lanes(elsewhere), session())
    assert result.endpoint == "http://127.0.0.1:9999"


# --- Images ----------------------------------------------------------------


def image_request(**overrides) -> RequestFacts:
    base = dict(
        prompt="what is this",
        request_class=RequestClass.VISION,
        owner_id="owner-a",
        session_epoch=7,
        has_image=True,
        image_bytes=100_000,
        image_width=640,
        image_height=480,
    )
    base.update(overrides)
    return RequestFacts(**base)


def test_vision_request_routes_to_vision_lane():
    result = route(image_request(), lanes(vision_lane()), session())
    assert result.lane_id == "vision"
    assert result.max_tokens == policy.IMAGE_MAX_TOKENS


def test_vision_request_is_refused_when_only_text_exists():
    """No silent downgrade: a photo cannot become a text question."""
    with pytest.raises(RouteRefused) as caught:
        route(image_request(), lanes(text_lane()), session())
    assert caught.value.code is RefusalCode.CAPABILITY_MISSING


def test_vision_refused_when_vision_lane_unqualified():
    with pytest.raises(RouteRefused) as caught:
        route(image_request(), lanes(vision_lane(qualified=False)), session())
    assert caught.value.code is RefusalCode.LANE_UNQUALIFIED


@pytest.mark.parametrize("field,value,detail", [
    ("image_bytes", 0, "empty"),
    ("image_bytes", policy.IMAGE_MAX_BYTES + 1, "too_large"),
    ("image_width", 0, "no_dimensions"),
    ("image_width", policy.IMAGE_MAX_DIMENSION + 1, "dimension"),
    ("image_height", policy.IMAGE_MAX_DIMENSION + 1, "dimension"),
])
def test_unusable_images_are_refused(field, value, detail):
    with pytest.raises(RouteRefused) as caught:
        route(image_request(**{field: value}), lanes(vision_lane()), session())
    assert caught.value.code is RefusalCode.IMAGE_UNUSABLE
    assert caught.value.detail == detail


def test_pixel_budget_is_enforced_independently_of_dimensions():
    # 640x640 = 409600 > 307200, yet each dimension is within its own limit.
    with pytest.raises(RouteRefused) as caught:
        route(image_request(image_width=640, image_height=640), lanes(vision_lane()), session())
    assert caught.value.detail == "pixels"


def test_image_at_the_exact_pixel_limit_is_accepted():
    result = route(image_request(image_width=640, image_height=480), lanes(vision_lane()), session())
    assert result.lane_id == "vision"


# --- Context overflow ------------------------------------------------------


def test_context_overflow_is_refused_before_issuing_the_request():
    too_big = text_lane(context_tokens=100)
    with pytest.raises(RouteRefused) as caught:
        route(make_request(), lanes(too_big), session())
    assert caught.value.code is RefusalCode.CONTEXT_OVERFLOW


def test_context_overflow_reports_the_arithmetic():
    with pytest.raises(RouteRefused) as caught:
        route(make_request(), lanes(text_lane(context_tokens=100)), session())
    assert ">" in caught.value.detail


def test_large_prompt_tokens_trigger_overflow():
    nearly_full = text_lane(context_tokens=policy.TEXT_CONTEXT_TOKENS)
    with pytest.raises(RouteRefused) as caught:
        route(make_request(prompt_tokens=policy.TEXT_CONTEXT_TOKENS), lanes(nearly_full), session())
    assert caught.value.code is RefusalCode.CONTEXT_OVERFLOW


def test_explicit_priority_decides_between_equally_capable_lanes():
    """Precedence is declared, never inferred from context size or name."""
    secondary = text_lane(lane_id="aaa-secondary", context_tokens=32768, priority=5)
    primary = text_lane(lane_id="zzz-primary", context_tokens=policy.TEXT_CONTEXT_TOKENS, priority=0)
    result = route(make_request(), lanes(secondary, primary), session())
    assert result.lane_id == "zzz-primary"


def test_a_warm_lane_beats_a_cold_higher_priority_lane():
    """Cold load is real latency, so it outranks declared precedence."""
    cold = text_lane(lane_id="cold", cold_load_seconds=8.0, priority=0)
    warm = text_lane(lane_id="warm", cold_load_seconds=0.0, priority=9)
    result = route(make_request(total_budget_seconds=60.0), lanes(cold, warm), session())
    assert result.lane_id == "warm"


# --- Timeouts and cancellation --------------------------------------------


def test_cold_load_overrunning_the_deadline_is_refused():
    with pytest.raises(RouteRefused) as caught:
        route(make_request(total_budget_seconds=5.0),
              lanes(text_lane(cold_load_seconds=6.0)), session())
    assert caught.value.code is RefusalCode.DEADLINE_EXCEEDED


def test_cold_load_is_charged_against_the_deadline_not_ignored():
    result = route(make_request(total_budget_seconds=10.0),
                   lanes(text_lane(cold_load_seconds=4.0)), session())
    assert result.deadline_seconds == pytest.approx(6.0)
    assert any("cold load" in note for note in result.notes)


def test_warm_lane_is_preferred_over_cold_lane():
    cold = text_lane(lane_id="cold", cold_load_seconds=8.0)
    warm = text_lane(lane_id="warm", cold_load_seconds=0.0)
    result = route(make_request(total_budget_seconds=60.0), lanes(cold, warm), session())
    assert result.lane_id == "warm"


def test_zero_deadline_is_refused():
    with pytest.raises(RouteRefused) as caught:
        route(make_request(total_budget_seconds=0.5), lanes(text_lane()), session())
    assert caught.value.code is RefusalCode.DEADLINE_EXCEEDED


def test_serialised_lane_notes_that_the_request_may_queue():
    result = route(make_request(), lanes(text_lane(serialised=True)), session())
    assert any("queue" in note for note in result.notes)


# --- Stale owner and session ----------------------------------------------


def test_stale_owner_is_refused():
    with pytest.raises(RouteRefused) as caught:
        route(make_request(owner_id="owner-b"), lanes(text_lane()), session(owner_id="owner-a"))
    assert caught.value.code is RefusalCode.STALE_SESSION
    assert caught.value.detail == "owner_changed"


def test_stale_epoch_is_refused():
    with pytest.raises(RouteRefused) as caught:
        route(make_request(session_epoch=8), lanes(text_lane()), session(epoch=7))
    assert caught.value.code is RefusalCode.STALE_SESSION
    assert caught.value.detail == "epoch_changed"


def test_owner_check_precedes_epoch_check():
    with pytest.raises(RouteRefused) as caught:
        route(make_request(owner_id="owner-b", session_epoch=99),
              lanes(text_lane()), session(owner_id="owner-a", epoch=7))
    assert caught.value.detail == "owner_changed"


# --- Contended endpoint ----------------------------------------------------


def test_contended_endpoint_is_refused():
    """`:8082` is drafted by two mutually exclusive units today."""
    with pytest.raises(RouteRefused) as caught:
        route(image_request(), lanes(vision_lane(contended=True)), session())
    assert caught.value.code is RefusalCode.PORT_CONTENDED


def test_vision_lane_may_be_directly_configured_uncontended():
    """The arbitration outcome is an input, not something the router invents."""
    result = route(image_request(), lanes(vision_lane(contended=False)), session())
    assert result.lane_id == "vision"


# --- Escalation ------------------------------------------------------------


def test_escalation_without_approval_is_refused():
    # Without the explicit opt-in the request is not an escalation at all, so
    # the escalation lane is excluded and the only lane left is none. The old
    # version accepted the caller's ESCALATION label and reached the lane.
    with pytest.raises(RouteRefused) as caught:
        route(make_request(escalation_approved=False, attempts=5),
              lanes(escalation_lane()), session())
    assert caught.value.code is RefusalCode.NO_LANE
    assert caught.value.detail == "escalation_lane_requires_opt_in"


def test_escalation_requires_a_spent_attempt():
    with pytest.raises(RouteRefused) as caught:
        route(make_request(escalation_approved=True, attempts=0),
              lanes(escalation_lane()), session())
    assert caught.value.detail == "no_failed_attempt"


def test_approved_escalation_routes_to_the_escalation_lane():
    result = route(make_request(escalation_approved=True, attempts=1,
                           total_budget_seconds=120.0),
                   lanes(escalation_lane()), session())
    assert result.lane_id == "escalation"


def test_automatic_escalation_is_disabled_by_policy():
    assert policy.ALLOW_AUTOMATIC_ESCALATION is False


def test_a_resident_question_never_reaches_the_escalation_lane_by_itself():
    """Even with a healthy escalation lane present, a simple question stays resident."""
    simple = text_lane()
    result = route(make_request(), lanes(simple, escalation_lane()), session())
    assert result.lane_id == "resident-text"


# --- Fallback --------------------------------------------------------------


def test_fallback_excludes_the_failed_lane():
    first = text_lane(lane_id="first")
    second = text_lane(lane_id="second", endpoint="http://127.0.0.1:9998")
    result = fallback_after_failure(make_request(), lanes(first, second), session(),
                                    failed_lane_id="first")
    assert result.lane_id == "second"
    assert result.fallback_reason == "first_failed"


def test_fallback_refuses_when_no_lane_remains():
    with pytest.raises(RouteRefused) as caught:
        fallback_after_failure(make_request(), lanes(text_lane()), session(),
                               failed_lane_id="resident-text")
    assert caught.value.code is RefusalCode.NO_LANE


def test_fallback_does_not_escalate_on_its_own():
    """A failed resident lane must not silently promote to the expensive lane."""
    resident = text_lane()
    with pytest.raises(RouteRefused) as caught:
        fallback_after_failure(make_request(), lanes(resident, escalation_lane()), session(),
                               failed_lane_id="resident-text")
    assert caught.value.code is RefusalCode.NO_LANE


def test_fallback_increments_the_attempt_counter():
    first = text_lane(lane_id="first")
    second = text_lane(lane_id="second", endpoint="http://127.0.0.1:9998")
    result = fallback_after_failure(make_request(attempts=0), lanes(first, second), session(),
                                    failed_lane_id="first")
    assert result.fallback_reason == "first_failed"


def test_fallback_still_honours_the_guard_binding():
    resident = text_lane()
    elsewhere = text_lane(lane_id="elsewhere", endpoint="http://127.0.0.1:9999")
    with pytest.raises(RouteRefused):
        fallback_after_failure(make_request(), lanes(resident, elsewhere),
                               session(inference_guard_active=True),
                               failed_lane_id="resident-text")


# --- Purity ----------------------------------------------------------------


def test_route_does_not_mutate_its_inputs():
    lane_map = lanes(text_lane())
    facts = make_request()
    sess = session()
    snapshot = (copy.deepcopy(lane_map), copy.deepcopy(facts), copy.deepcopy(sess))
    route(facts, lane_map, sess)
    assert (lane_map, facts, sess) == snapshot


def test_route_is_deterministic():
    lane_map, facts, sess = lanes(text_lane()), make_request(), session()
    assert route(facts, lane_map, sess) == route(facts, lane_map, sess)


def test_route_rejects_wrong_types():
    # Strict validation replaces duck typing: a wrong type is an `InvalidFacts`
    # caller bug, deliberately a different type from a `RouteRefused` routing
    # outcome so the two cannot be confused.
    with pytest.raises(InvalidFacts):
        route(object(), lanes(text_lane()), session())  # type: ignore[arg-type]
    with pytest.raises(InvalidFacts):
        route(make_request(), lanes(text_lane()), object())  # type: ignore[arg-type]


def test_decision_is_immutable():
    result = route(make_request(), lanes(text_lane()), session())
    assert isinstance(result, Decision)
    with pytest.raises(Exception):
        result.lane_id = "tampered"  # type: ignore[misc]


def test_every_decision_carries_an_honesty_note():
    result = route(make_request(), lanes(text_lane()), session())
    assert any("no inference performed" in note for note in result.notes)


# --- Refusal codes are a closed, stable set --------------------------------


def test_refusal_codes_are_stable_strings():
    for code in RefusalCode:
        assert code.value == code.value.lower()
        assert " " not in code.value


def test_route_refused_is_a_runtime_error():
    refusal = RouteRefused(RefusalCode.NO_LANE, "x")
    assert isinstance(refusal, RuntimeError)
    assert "no_lane" in str(refusal)


# ===========================================================================
# Revision 2 -- regression coverage for the six reproduced review defects.
#
# Each block names the reproduction it closes. These call `_route_impl` and
# `_fallback_impl` directly so the lifecycle parameter is explicit and the
# wrapper defaults above cannot hide a missing fact.
# ===========================================================================


# --- Rep 1: identity and lifecycle before every path -----------------------


@pytest.mark.parametrize("prompt,handlers", [
    ("battery level", None),                      # would be a bypass candidate
    ("what is the capital of Australia", None),    # ordinary question
])
def test_stale_owner_is_refused_before_any_path(prompt, handlers):
    """The old deterministic early return skipped the session check entirely."""
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(prompt=prompt, owner_id="owner-b"),
            lanes(text_lane()),
            session(owner_id="owner-a"),
            lifecycle=CLEAR,
            handlers=handlers,
            now=0.0,
        )
    assert caught.value.code is RefusalCode.STALE_SESSION
    assert caught.value.detail == "owner_changed"


def test_stale_epoch_is_refused_before_the_deterministic_bypass():
    """Rep 1's exact case: a fresh handler must not rescue a stale session."""
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(prompt="battery level", session_epoch=99),
            lanes(text_lane()),
            session(epoch=7),
            lifecycle=CLEAR,
            handlers={StatusIntent.BATTERY_LEVEL: HandlerFacts(
                intent=StatusIntent.BATTERY_LEVEL, registered=True,
                available=True, observed_at=0.0)},
            now=0.0,
        )
    assert caught.value.code is RefusalCode.STALE_SESSION
    assert caught.value.detail == "epoch_changed"


def test_owner_check_precedes_the_epoch_check_on_every_path():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(prompt="battery level", owner_id="owner-b", session_epoch=99),
            lanes(text_lane()),
            session(owner_id="owner-a", epoch=7),
            lifecycle=CLEAR,
            now=0.0,
        )
    assert caught.value.detail == "owner_changed"


# --- Rep 2: capabilities come from the payload, not the label --------------


def test_image_on_a_mislabelled_request_never_selects_a_text_lane():
    """The review's exact case: RESIDENT_SIMPLE label carrying an image."""
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(request_class=RequestClass.RESIDENT_SIMPLE,
                         image_bytes=2048, image_width=320, image_height=240),
            lanes(text_lane()),
            session(),
            lifecycle=CLEAR,
        )
    assert caught.value.code is RefusalCode.CAPABILITY_MISSING
    assert "image" in caught.value.detail


def test_image_on_an_approved_escalation_still_requires_image_capability():
    """Rep 2's second case: approval must not waive the image requirement."""
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(escalation_approved=True, attempts=1,
                         image_bytes=2048, image_width=320, image_height=240),
            lanes(escalation_lane()),
            session(),
            lifecycle=CLEAR,
        )
    # The escalation lane is text+escalation only: an image-bearing escalation
    # is refused rather than having the photo silently dropped.
    assert caught.value.code in (RefusalCode.CAPABILITY_MISSING, RefusalCode.NO_LANE)


def test_image_validation_runs_before_the_deterministic_bypass():
    """Rep 2: an unusable image is refused even on a status phrasing."""
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(prompt="battery level", image_bytes=0, has_image=True),
            lanes(text_lane()),
            session(),
            lifecycle=CLEAR,
            handlers={StatusIntent.BATTERY_LEVEL: HandlerFacts(
                intent=StatusIntent.BATTERY_LEVEL, registered=True,
                available=True, observed_at=0.0)},
            now=0.0,
        )
    # `has_image=True` with no bytes is a coherent declaration of an empty
    # image, so the useful refusal is IMAGE_UNUSABLE:empty, not InvalidFacts.
    assert caught.value.code is RefusalCode.IMAGE_UNUSABLE
    assert caught.value.detail == "empty"


def test_an_oversized_image_is_refused_before_the_bypass():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(prompt="battery level",
                         image_bytes=policy.IMAGE_MAX_BYTES + 1,
                         image_width=320, image_height=240),
            lanes(vision_lane()),
            session(),
            lifecycle=CLEAR,
            handlers={StatusIntent.BATTERY_LEVEL: HandlerFacts(
                intent=StatusIntent.BATTERY_LEVEL, registered=True,
                available=True, observed_at=0.0)},
            now=0.0,
        )
    assert caught.value.code is RefusalCode.IMAGE_UNUSABLE
    assert caught.value.detail == "too_large"


def test_long_context_is_only_served_by_a_long_context_lane():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(long_context_requested=True),
            lanes(text_lane()),
            session(),
            lifecycle=CLEAR,
        )
    assert caught.value.code is RefusalCode.CAPABILITY_MISSING
    assert "long_context" in caught.value.detail


def test_a_long_context_request_is_served_by_a_capable_lane():
    result = _route_impl(
        make_request(long_context_requested=True),
        lanes(vision_lane()),
        session(),
        lifecycle=CLEAR,
    )
    assert result.request_class is RequestClass.RESIDENT_REASONING
    assert result.lane_id == "vision"


# --- Rep 3: closed intent set, never a prefix match ------------------------


def test_a_phrase_containing_a_status_word_is_not_a_status_intent():
    """The three the review reproduced, as whole routing decisions."""
    for prompt in ("battery chemistry", "storage of insulin", "signal processing"):
        with pytest.raises(RouteRefused) as caught:
            # No handler is registered for these, so they must reach the lane
            # gate like any other question. An unqualified lane proves the
            # request was routed rather than bypassed.
            _route_impl(
                make_request(prompt=prompt),
                lanes(text_lane(qualified=False)),
                session(),
                lifecycle=CLEAR,
                handlers={},
                now=0.0,
            )
        assert caught.value.code is RefusalCode.LANE_UNQUALIFIED


# --- Rep 4: strict types and finite bounds ---------------------------------


@pytest.mark.parametrize("overrides", [
    {"qualified": "false"},          # truthy string selected an unqualified lane
    {"qualified": 1},
    {"session_epoch": True},         # bool is an int subclass
    {"session_epoch": 7.0},
    {"session_epoch": -1},
    {"image_bytes": -1},
    {"prompt_tokens": -500},         # negative reduced the context estimate
    {"attempts": -1},
    {"has_image": "yes"},
    {"long_context_requested": 1},
])
def test_invalid_lane_and_request_facts_are_rejected(overrides):
    is_lane = "qualified" in overrides
    with pytest.raises(InvalidFacts):
        if is_lane:
            _route_impl(make_request(), lanes(text_lane(**overrides)), session(),
                        lifecycle=CLEAR)
        else:
            _route_impl(make_request(**overrides), lanes(text_lane()), session(),
                        lifecycle=CLEAR)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0, "60"])
def test_non_finite_or_negative_budget_is_rejected(bad):
    with pytest.raises(InvalidFacts):
        _route_impl(make_request(total_budget_seconds=bad), lanes(text_lane()),
                    session(), lifecycle=CLEAR)


@pytest.mark.parametrize("bad", [True, 1.5, -3])
def test_lane_context_tokens_must_be_a_positive_int(bad):
    with pytest.raises(InvalidFacts):
        _route_impl(make_request(), lanes(text_lane(context_tokens=bad)), session(),
                    lifecycle=CLEAR)


def test_qualification_does_not_rely_on_truthiness():
    """A string is truthy; only a real bool qualifies a lane."""
    with pytest.raises(InvalidFacts):
        _route_impl(make_request(), lanes(text_lane(qualified="false")), session(),
                    lifecycle=CLEAR)


def test_epoch_is_not_satisfied_by_a_bool():
    """`True == 1`, so a bool epoch used to match an epoch of 1."""
    with pytest.raises(InvalidFacts):
        _route_impl(make_request(session_epoch=True), lanes(text_lane()),
                    session(epoch=1), lifecycle=CLEAR)


# --- Rep 5: the output budget actually selected is the one reserved --------


def test_reserved_output_budget_equals_the_selected_budget():
    """The old arithmetic reserved 256 while selecting 512."""
    result = _route_impl(make_request(), lanes(text_lane()), session(), lifecycle=CLEAR)
    assert result.max_tokens == policy.TEXT_MAX_TOKENS
    assert result.max_tokens != policy.ANSWER_TOKEN_RESERVE


def test_the_review_arithmetic_is_now_refused():
    """The review's exact probe: 320 policy + 640 recall + 256 answer admitted
    at 1916 of 2048, although the selected 512-token output needs 2172.

    `prompt_tokens` is now the *complete* input by contract, so the same request
    declares 960 and is admitted; the request that the old code wrongly admitted
    -- 1660 input tokens plus a 512-token answer -- is now refused.
    """
    # 1660 input + 512 output = 2172 > 2048: refused.
    with pytest.raises(RouteRefused) as caught:
        _route_impl(make_request(prompt_tokens=1660), lanes(text_lane()), session(),
                    lifecycle=CLEAR)
    assert caught.value.code is RefusalCode.CONTEXT_OVERFLOW
    assert caught.value.detail == "2172>2048"


def test_an_input_that_exactly_fills_the_lane_is_admitted():
    # 1536 input + 512 output = 2048 == context: fits exactly.
    result = _route_impl(make_request(prompt_tokens=1536), lanes(text_lane()),
                         session(), lifecycle=CLEAR)
    assert result.max_tokens == policy.TEXT_MAX_TOKENS


def test_input_one_token_over_the_lane_is_refused():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(make_request(prompt_tokens=1537), lanes(text_lane()), session(),
                    lifecycle=CLEAR)
    assert caught.value.code is RefusalCode.CONTEXT_OVERFLOW


def test_policy_and_recall_are_not_added_twice():
    """The contract fix: the router adds nothing to `prompt_tokens`."""
    declared = 1000
    result = _route_impl(make_request(prompt_tokens=declared), lanes(text_lane()),
                         session(), lifecycle=CLEAR)
    # 1000 + 512 = 1512 of 2048. If policy/recall were added again the old code
    # would have computed 1000+320+640+512 = 2472 and refused.
    assert result.max_tokens == policy.TEXT_MAX_TOKENS
    assert declared + result.max_tokens <= policy.TEXT_CONTEXT_TOKENS


def test_the_reference_estimator_matches_the_contract():
    assert policy.estimated_input_tokens(1000) == 1000 + policy.POLICY_TOKEN_RESERVE + policy.RECALL_ITEM_TOKEN_RESERVE * policy.RECALL_TOP_K


def test_vision_budget_is_reserved_not_the_text_budget():
    result = _route_impl(
        make_request(prompt="what is this", image_bytes=2048,
                     image_width=320, image_height=240),
        lanes(vision_lane()), session(), lifecycle=CLEAR)
    assert result.max_tokens == policy.IMAGE_MAX_TOKENS


# --- Rep 6: cancellation, sleep fencing and one shared deadline ------------


def test_missing_lifecycle_facts_are_refused_by_default():
    """"We did not check" must not read as "we checked and it is clear"."""
    assert policy.REQUIRE_LIFECYCLE_FACTS is True
    with pytest.raises(RouteRefused) as caught:
        _route_impl(make_request(), lanes(text_lane()), session(), lifecycle=None)
    assert caught.value.code is RefusalCode.LIFECYCLE_UNVERIFIED


def test_a_missing_lifecycle_is_refused_even_for_the_deterministic_bypass():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(prompt="battery level"),
            lanes(text_lane()),
            session(),
            lifecycle=None,
            handlers={StatusIntent.BATTERY_LEVEL: HandlerFacts(
                intent=StatusIntent.BATTERY_LEVEL, registered=True,
                available=True, observed_at=0.0)},
            now=0.0,
        )
    assert caught.value.code is RefusalCode.LIFECYCLE_UNVERIFIED


def test_a_cancelled_request_is_refused_before_selection():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(make_request(), lanes(text_lane()), session(),
                    lifecycle=clear_lifecycle(cancelled=True))
    assert caught.value.code is RefusalCode.CANCELLED


def test_a_cancelled_request_is_refused_on_the_bypass_path_too():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(
            make_request(prompt="battery level"), lanes(text_lane()), session(),
            lifecycle=clear_lifecycle(cancelled=True),
            handlers={StatusIntent.BATTERY_LEVEL: HandlerFacts(
                intent=StatusIntent.BATTERY_LEVEL, registered=True,
                available=True, observed_at=0.0)},
            now=0.0)
    assert caught.value.code is RefusalCode.CANCELLED


def test_a_sleep_fenced_request_is_refused():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(make_request(), lanes(text_lane()), session(),
                    lifecycle=clear_lifecycle(fenced=True))
    assert caught.value.code is RefusalCode.LIFECYCLE_FENCED


@pytest.mark.parametrize("bad", [True, "1.0", -0.5, float("nan")])
def test_invalid_lifecycle_facts_are_rejected(bad):
    with pytest.raises(InvalidFacts):
        _route_impl(make_request(), lanes(text_lane()), session(),
                    lifecycle=LifecycleFacts(elapsed_seconds=bad))


def test_elapsed_time_is_deducted_from_the_shared_budget():
    result = _route_impl(make_request(total_budget_seconds=30.0), lanes(text_lane()),
                         session(), lifecycle=clear_lifecycle(elapsed_seconds=5.0))
    assert result.deadline_seconds == pytest.approx(25.0)
    assert any("already spent" in note for note in result.notes)


def test_fallback_does_not_reset_the_budget():
    """The review gap: fallback handed over the original relative deadline."""
    first, second = text_lane(lane_id="first"), text_lane(lane_id="second")
    result = _fallback_impl(
        make_request(total_budget_seconds=30.0),
        lanes(first, second),
        session(),
        failed_lane_id="first",
        lifecycle=clear_lifecycle(elapsed_seconds=12.0),
    )
    assert result.deadline_seconds == pytest.approx(18.0)
    assert result.deadline_seconds < 30.0
    assert result.fallback_reason == "first_failed"


def test_fallback_refuses_when_the_shared_budget_is_spent():
    first, second = text_lane(lane_id="first"), text_lane(lane_id="second")
    with pytest.raises(RouteRefused) as caught:
        _fallback_impl(
            make_request(total_budget_seconds=10.0),
            lanes(first, second),
            session(),
            failed_lane_id="first",
            lifecycle=clear_lifecycle(elapsed_seconds=9.5),
        )
    assert caught.value.code is RefusalCode.DEADLINE_EXCEEDED


def test_fallback_refuses_on_a_cancelled_lifecycle():
    with pytest.raises(RouteRefused) as caught:
        _fallback_impl(
            make_request(),
            lanes(text_lane(lane_id="first"), text_lane(lane_id="second")),
            session(),
            failed_lane_id="first",
            lifecycle=clear_lifecycle(cancelled=True),
        )
    assert caught.value.code is RefusalCode.CANCELLED


def test_fallback_refuses_without_lifecycle_facts():
    with pytest.raises(RouteRefused) as caught:
        _fallback_impl(
            make_request(),
            lanes(text_lane(lane_id="first"), text_lane(lane_id="second")),
            session(),
            failed_lane_id="first",
            lifecycle=None,
        )
    assert caught.value.code is RefusalCode.LIFECYCLE_UNVERIFIED


def test_fallback_result_is_a_retry_and_cannot_be_answered_from_status():
    first, second = text_lane(lane_id="first"), text_lane(lane_id="second")
    result = _fallback_impl(
        make_request(prompt="battery level", attempts=0, total_budget_seconds=30.0),
        lanes(first, second),
        session(),
        failed_lane_id="first",
        lifecycle=CLEAR,
        handlers={StatusIntent.BATTERY_LEVEL: HandlerFacts(
            intent=StatusIntent.BATTERY_LEVEL, registered=True,
            available=True, observed_at=0.0)},
        now=0.0,
    )
    assert result.bypass_inference is False
    assert result.fallback_reason == "first_failed"


def test_cold_load_is_charged_against_the_shared_budget_not_a_fresh_one():
    cold = text_lane(cold_load_seconds=8.0)
    result = _route_impl(make_request(total_budget_seconds=30.0), lanes(cold),
                         session(), lifecycle=clear_lifecycle(elapsed_seconds=4.0))
    assert result.deadline_seconds == pytest.approx(18.0)


def test_the_deadline_is_never_longer_than_the_total_budget():
    """No path may hand out more time than the request was granted."""
    result = _route_impl(make_request(total_budget_seconds=25.0), lanes(text_lane()),
                         session(), lifecycle=CLEAR)
    assert result.deadline_seconds <= 25.0


# --- Deterministic bypass notes must not overclaim -------------------------


def test_the_bypass_names_the_handler_it_defers_to():
    """No claim of an answer the router did not produce."""
    result = _route_impl(
        make_request(prompt="battery level"), lanes(text_lane()), session(),
        lifecycle=CLEAR,
        handlers={StatusIntent.BATTERY_LEVEL: HandlerFacts(
            intent=StatusIntent.BATTERY_LEVEL, registered=True,
            available=True, observed_at=0.0)},
        now=0.0)
    assert any("battery_level handler" in note for note in result.notes)
    assert any("no model involved" in note for note in result.notes)


def test_lane_notes_do_not_claim_live_contention():
    """A second service definition is a configuration observation."""
    result = _route_impl(
        make_request(prompt="what is this", image_bytes=2048,
                     image_width=320, image_height=240),
        lanes(vision_lane(contended=False)), session(), lifecycle=CLEAR)
    assert not any("contention" in note for note in result.notes)


def test_the_serialisation_note_says_within_the_engine():
    result = _route_impl(make_request(), lanes(text_lane(serialised=True)),
                         session(), lifecycle=CLEAR)
    assert any("engine serialises" in note for note in result.notes)


# ===========================================================================
# Reviewer probe coverage, 2 October 2026.
#
# Rook independently probed the revision and found seven contract mismatches in
# the deterministic bypass. These tests mirror his eleven probe cases so the
# same defects cannot return silently.
# ===========================================================================


def fresh_handler(intent=StatusIntent.TIME, **overrides) -> HandlerFacts:
    base = dict(intent=intent, registered=True, available=True, observed_at=0.0)
    base.update(overrides)
    return HandlerFacts(**base)


def time_handlers(**overrides) -> dict:
    return {StatusIntent.TIME: fresh_handler(StatusIntent.TIME, **overrides)}


def test_probe_valid_deterministic_control():
    result = _route_impl(make_request(prompt="what time is it"), lanes(text_lane()),
                         session(), lifecycle=CLEAR, handlers=time_handlers(), now=0.0)
    assert result.bypass_inference is True
    assert result.lane_id == "deterministic"


def test_probe_stale_owner_control():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(make_request(prompt="what time is it", owner_id="other"),
                    lanes(text_lane()), session(), lifecycle=CLEAR,
                    handlers=time_handlers(), now=0.0)
    assert caught.value.code is RefusalCode.STALE_SESSION


def test_probe_cancelled_control():
    with pytest.raises(RouteRefused) as caught:
        _route_impl(make_request(prompt="what time is it"), lanes(text_lane()),
                    session(), lifecycle=clear_lifecycle(cancelled=True),
                    handlers=time_handlers(), now=0.0)
    assert caught.value.code is RefusalCode.CANCELLED


def test_probe_expired_total_budget_is_refused_not_negative():
    """Probe mismatch 1: the bypass returned a decision with -1.0s remaining."""
    with pytest.raises(RouteRefused) as caught:
        _route_impl(make_request(prompt="what time is it", total_budget_seconds=10.0),
                    lanes(text_lane()), session(),
                    lifecycle=clear_lifecycle(elapsed_seconds=11.0),
                    handlers=time_handlers(), now=0.0)
    assert caught.value.code is RefusalCode.DEADLINE_EXCEEDED


def test_probe_zero_total_budget_is_refused():
    """Probe mismatch 2: the bypass returned a decision with 0.0s remaining."""
    with pytest.raises(RouteRefused) as caught:
        _route_impl(make_request(prompt="what time is it", total_budget_seconds=0.0),
                    lanes(text_lane()), session(), lifecycle=CLEAR,
                    handlers=time_handlers(), now=0.0)
    assert caught.value.code is RefusalCode.DEADLINE_EXCEEDED


def test_probe_bypass_deadline_is_never_negative():
    result = _route_impl(make_request(prompt="what time is it", total_budget_seconds=30.0),
                         lanes(text_lane()), session(),
                         lifecycle=clear_lifecycle(elapsed_seconds=5.0),
                         handlers=time_handlers(), now=0.0)
    assert result.deadline_seconds == pytest.approx(25.0)
    assert result.deadline_seconds > 0


@pytest.mark.parametrize("overrides", [
    {"registered": "false"},   # probe mismatch 3: truthy string enabled the bypass
    {"available": "false"},    # probe mismatch 4: same
    {"observed_at": True},     # probe mismatch 6: bool passed as a timestamp
    {"observed_at": "0"},
    {"registered": 1},
    {"available": 1},
])
def test_probe_handler_facts_are_strictly_typed(overrides):
    with pytest.raises(InvalidFacts):
        _route_impl(make_request(prompt="what time is it"), lanes(text_lane()),
                    session(), lifecycle=CLEAR,
                    handlers=time_handlers(**overrides), now=0.0)


def test_probe_handler_intent_mismatch_is_rejected():
    """Probe mismatch 5: a handler filed under one intent served another."""
    mismatched = {StatusIntent.TIME: fresh_handler(StatusIntent.BATTERY_LEVEL)}
    with pytest.raises(InvalidFacts) as caught:
        _route_impl(make_request(prompt="what time is it"), lanes(text_lane()),
                    session(), lifecycle=CLEAR, handlers=mismatched, now=0.0)
    assert "declares intent" in str(caught.value)


@pytest.mark.parametrize("bad_owner", ["", "   ", "\t"])
def test_probe_empty_owner_identity_is_invalid(bad_owner):
    """Probe mismatch 7: an empty owner matched another empty owner."""
    with pytest.raises(InvalidFacts):
        _route_impl(make_request(owner_id=bad_owner), lanes(text_lane()),
                    session(owner_id=bad_owner), lifecycle=CLEAR)


def test_probe_empty_session_owner_is_invalid():
    with pytest.raises(InvalidFacts):
        _route_impl(make_request(), lanes(text_lane()), session(owner_id=""),
                    lifecycle=CLEAR)


def test_probe_a_disabled_handler_does_not_bypass():
    """`registered=False` is a real False, and must route to the model."""
    result = _route_impl(make_request(prompt="what time is it"), lanes(text_lane()),
                         session(), lifecycle=CLEAR,
                         handlers=time_handlers(registered=False), now=0.0)
    assert result.bypass_inference is False


def test_probe_fallback_never_consults_a_handler():
    """A failed step is a retry, and retries are never answered from status.

    So the fallback path does not read handlers at all -- and therefore does not
    validate them. Rejecting malformed handlers the path would never use would
    refuse a call that is, in fact, correct. The malformed-handler contract
    belongs to the path that reads them, which is `route` on a non-retry.
    """
    result = _fallback_impl(
        make_request(prompt="what time is it"),
        lanes(text_lane(lane_id="first"), text_lane(lane_id="second")),
        session(), failed_lane_id="first", lifecycle=CLEAR,
        handlers=time_handlers(registered="false"), now=0.0)
    assert result.bypass_inference is False
    assert result.fallback_reason == "first_failed"
