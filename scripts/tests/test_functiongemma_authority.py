"""Lifecycle source regressions; scopes/consent/session issuance are synthetic.

No actual SAGE authentication, human consent, device lock or sleep is exercised.
"""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lantern/benchmarks/tool_routing"))
from action_authority import PRIVATE_ACTIONS, ReviewedActionAuthority
from action_session import ActionRefused, AuthenticatedScope, SubmittedInstruction


SAVE = "Save a private note titled 'Keys' with exact text 'Use shelf 4.'"
SEARCH = "Find my saved note about the keys."


@pytest.fixture
def authority():
    a = ReviewedActionAuthority(enabled=True)
    scope = AuthenticatedScope("synthetic-owner", "synthetic-session", "synthetic-epoch", "owner-test",
                               "a" * 64, "synthetic-chain", "synthetic-profile", "synthetic-device",
                               "explicit-private-actions-test-only", time.monotonic() + 30)
    a.activate(scope, allowed_actions=PRIVATE_ACTIONS)
    return a, scope


def submitted(a, text=SEARCH, **kwargs):
    review = a.prepare_review(text, source="typed")
    return a.submit_review(review, reviewed_text=text, **kwargs)


def test_authority_defaults_disabled_and_cannot_self_authenticate(authority):
    _, scope = authority
    with pytest.raises(ActionRefused):
        ReviewedActionAuthority().activate(scope, allowed_actions=PRIVATE_ACTIONS)
    with pytest.raises(ActionRefused):
        ReviewedActionAuthority(enabled=True).prepare_review(SEARCH, source="typed")


@pytest.mark.parametrize("source", ["interim_speech", "recalled", "model", "history", True, []])
def test_noncurrent_sources_cannot_issue_review(authority, source):
    a, _ = authority
    with pytest.raises(ActionRefused):
        a.prepare_review(SEARCH, source=source)


def test_review_never_authorizes_before_exact_submission(authority):
    a, scope = authority
    review = a.prepare_review(SEARCH, source="final_speech")
    assert not a.check(scope, SubmittedInstruction(review, SEARCH))
    instruction = a.submit_review(review, reviewed_text=SEARCH)
    assert a.resolve(instruction) == scope
    assert a.check(scope, instruction)
    assert not a.check(scope, replace(instruction, text=SAVE))
    with pytest.raises(ActionRefused):
        a.submit_review(review, reviewed_text=SEARCH)


def test_changed_review_is_consumed_and_needs_fresh_review(authority):
    a, _ = authority
    review = a.prepare_review(SEARCH, source="typed")
    for text in (SAVE, SEARCH):
        with pytest.raises(ActionRefused):
            a.submit_review(review, reviewed_text=text)


@pytest.mark.parametrize("approval", [False, "true", 1, None])
def test_private_write_needs_strict_per_review_human_approval(authority, approval):
    a, _ = authority
    review = a.prepare_review(SAVE, source="typed")
    with pytest.raises(ActionRefused):
        a.submit_review(review, reviewed_text=SAVE, approve_private_write=approval)


def test_private_write_needs_session_consent_as_well_as_review(authority):
    a, scope = authority
    a.revoke()
    a.activate(replace(scope, epoch="new-epoch", consent_revision="read-only-test"),
               allowed_actions=frozenset({"search_memory"}))
    with pytest.raises(ActionRefused):
        a.prepare_review(SAVE, source="typed")
    with pytest.raises(ActionRefused):
        a.prepare_review("Check this Lantern's battery level.", source="typed")
    assert a.resolve(submitted(a)).consent_revision == "read-only-test"


def test_guard_single_use_and_steps_require_current_worker(authority):
    a, scope = authority
    instruction = submitted(a)
    with pytest.raises(ActionRefused):
        a.dispatch_step(scope, instruction, lambda: None)
    assert a.run_guarded(scope, instruction,
                         lambda: a.dispatch_step(scope, instruction, lambda: "local-step")) == "local-step"
    for action in (lambda: a.resolve(instruction),
                   lambda: a.run_guarded(scope, instruction, lambda: None)):
        with pytest.raises(ActionRefused):
            action()


@pytest.mark.parametrize("field", ["owner", "session", "epoch", "private_domain", "sage_identity",
                                   "sage_chain", "sage_endpoint_profile", "device", "consent_revision"])
def test_each_scope_field_is_bound_to_review(authority, field):
    a, scope = authority
    assert not a.check(replace(scope, **{field: "other"}), submitted(a))


def test_cancel_fences_dispatch_and_publication(authority):
    a, scope = authority
    instruction = submitted(a)
    effects = []

    def work():
        a.cancel(instruction.request_id)
        a.publish(scope, instruction, lambda: effects.append("published"))

    with pytest.raises(ActionRefused):
        a.run_guarded(scope, instruction, work)
    assert effects == []


def test_revoke_wins_before_send_and_does_not_wait_for_network(authority):
    a, scope = authority
    instruction = submitted(a)
    arrived, release = threading.Event(), threading.Event()
    effects, failures = [], []

    def work():
        arrived.set()
        assert release.wait(2)
        return a.dispatch_step(scope, instruction, lambda: effects.append("send"))

    def worker():
        try:
            a.run_guarded(scope, instruction, work)
        except ActionRefused as e:
            failures.append(str(e))

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert arrived.wait(2)
        a.revoke()
        # If revoke held the gate over work, this assertion could never run.
        assert not a.check(scope, instruction)
        with pytest.raises(ActionRefused):
            a.activate(replace(scope, epoch="replacement"), allowed_actions=PRIVATE_ACTIONS)
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert failures and effects == []
    assert a.drain(0.1)
    a.activate(replace(scope, epoch="replacement"), allowed_actions=PRIVATE_ACTIONS)
    assert not a.check(scope, instruction)


def test_drain_timeout_is_permanently_tainted_even_after_worker_returns(authority):
    a, scope = authority
    instruction = submitted(a)
    arrived, release = threading.Event(), threading.Event()
    failures = []

    def work():
        arrived.set()
        assert release.wait(2)

    def worker():
        try:
            a.run_guarded(scope, instruction, work)
        except ActionRefused:
            failures.append(True)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert arrived.wait(2)
        a.revoke()
        assert a.drain(0) is False
    finally:
        release.set()
        thread.join(2)
    assert failures and not thread.is_alive()
    assert a.drain(0) is False
    with pytest.raises(ActionRefused):
        a.activate(replace(scope, epoch="fresh"), allowed_actions=PRIVATE_ACTIONS)


def test_old_epoch_cannot_be_reactivated_and_close_is_terminal(authority):
    a, scope = authority
    a.revoke()
    with pytest.raises(ActionRefused):
        a.activate(scope, allowed_actions=PRIVATE_ACTIONS)
    a.close()
    with pytest.raises(ActionRefused):
        a.activate(replace(scope, epoch="fresh"), allowed_actions=PRIVATE_ACTIONS)


def test_review_deadline_and_capacity_do_not_refresh(authority):
    _, scope = authority
    now = [1.0]
    a = ReviewedActionAuthority(enabled=True, clock=lambda: now[0], capacity=1)
    a.activate(replace(scope, expires_at=100), allowed_actions=PRIVATE_ACTIONS)
    review = a.prepare_review(SEARCH, source="typed")
    now[0] = 31.0
    with pytest.raises(ActionRefused):
        a.submit_review(review, reviewed_text=SEARCH)
    with pytest.raises(ActionRefused):
        a.prepare_review(SEARCH, source="typed")


@pytest.mark.parametrize("timeout", [True, -1, 6, float("inf"), float("nan")])
def test_drain_requires_revoke_and_bounded_deadline(authority, timeout):
    a, _ = authority
    with pytest.raises(ActionRefused):
        a.drain(timeout)
    with pytest.raises(ActionRefused):
        a.drain(0)
