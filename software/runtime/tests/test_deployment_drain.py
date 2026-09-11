import os
import uuid
from types import SimpleNamespace

import pytest

from sage_lantern.deployment_drain import DeploymentDrain, DeploymentDrainError
from sage_lantern import deployment_drain
from sage_lantern.suspend_journal import SuspendJournal
from sage_lantern.suspend_system import SystemEvidence
from sage_lantern.suspend_io import publish

CANDIDATE = "a" * 64
ROLLBACK = "b" * 64
BOOT = str(uuid.uuid4())


@pytest.mark.parametrize('failure', ['manifest', 'existing_marker', 'idle_stop'])
def test_acquisition_rejects_before_journal_or_appliance(tmp_path, monkeypatch, failure):
    marker = tmp_path / 'deployment.pending'
    monkeypatch.setattr(deployment_drain, 'DEPLOYMENT_FENCE', str(marker))
    monkeypatch.setattr(deployment_drain.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(deployment_drain, 'managed', lambda: True)
    monkeypatch.setattr(deployment_drain, '_boot', lambda: BOOT)
    def forbidden(*args, **kwargs):
        pytest.fail('failed acquisition reached journal or appliance')
    monkeypatch.setattr(deployment_drain, 'SuspendJournal', forbidden)
    stopped = []
    def stop(service, **kwargs):
        assert marker.exists()
        stopped.append(service)
        return SystemEvidence(False, 'synthetic stop failure')
    runner = SimpleNamespace(SuspendSystem=lambda: SimpleNamespace(deployment_stop_verified=stop),
        service_identity=lambda service: (123, 'a' * 32), Appliance=forbidden)
    if failure == 'existing_marker':
        marker.write_bytes(b'existing transaction must survive')
    with pytest.raises((DeploymentDrainError, FileExistsError)):
        DeploymentDrain.acquire(runner, candidate_sha256=CANDIDATE, rollback_sha256=ROLLBACK,
            verify_binding=lambda candidate, rollback: failure != 'manifest',
            verify_broker=forbidden)
    if failure == 'manifest':
        assert not marker.exists() and not stopped
    elif failure == 'existing_marker':
        assert marker.read_bytes() == b'existing transaction must survive' and not stopped
    else:
        assert marker.exists() and stopped == [deployment_drain.IDLE_SUSPEND]


@pytest.fixture
def drained(tmp_path):
    journal = SuspendJournal(directory=tmp_path.resolve() / "journal", owner_uid=os.geteuid())
    calls = []
    state = SimpleNamespace(fence=False, stopped=set(), auth_stopped=False, gates=True, guards=True, sleep_idle=True,
        binding_ok=True, broker_ok=True, model_stopped=False, now=0, boot=BOOT,
        transition_id=str(uuid.uuid4()), calls=calls)
    state.fenced = lambda: state.fence
    def retain():
        calls.append("retain")
        state.fence = True
        return True
    def stop():
        assert state.fence
        calls.append("stop_auth")
        state.auth_stopped = True
        return True
    def quiesce():
        assert state.fence and state.auth_stopped and not state.model_stopped
        calls.append("aggregate")
        return state.gates
    state.retain_fence, state.stop_auth, state.quiesce = retain, stop, quiesce
    state.auth_inactive = lambda: state.auth_stopped
    state.auth_drained = state.voice_drained = lambda: state.gates
    state.kiosk = SimpleNamespace(quiesce=lambda: state.gates, close=lambda: None)
    def model_stop(service, **identity):
        calls.append("stop_model")
        state.model_stopped = True
        return SystemEvidence(True, "synthetic")
    state.system = SimpleNamespace(verify_stop_guards=lambda: SystemEvidence(state.guards, "synthetic"),
        verify_sleep_idle=lambda: SystemEvidence(state.sleep_idle, "synthetic"),
        deployment_stop_verified=model_stop,
        deployment_inactive_verified=lambda service: SystemEvidence(state.gates, "synthetic"))
    marker = tmp_path.resolve() / "deployment.pending"
    binding = dict(schema="sage.lantern.deployment-drain.v1", transition_id=state.transition_id,
        candidate_sha256=CANDIDATE, rollback_sha256=ROLLBACK, boot_id=BOOT, pid=os.getpid())
    publish(marker, binding, owner_uid=os.geteuid())
    def control(operation, transition):
        assert operation == "drain" and state.auth_stopped and state.fence
        calls.append("broker_drain")
        return dict(transition_id=transition, broker_id="c" * 32, vault_epoch="d" * 64 + ":1")
    authority = DeploymentDrain(journal, state, state.transition_id, owner_uid=os.geteuid(),
        candidate_sha256=CANDIDATE, rollback_sha256=ROLLBACK,
        verify_binding=lambda candidate, rollback: state.binding_ok,
        verify_broker=lambda proof: state.broker_ok, marker=marker, binding=binding,
        model_identity=(123, "e" * 32), boot=lambda: state.boot, clock=lambda: state.now, control=control)
    yield authority, journal, state
    journal.close()


def test_aggregate_authority_holds_fence_and_owner_across_mutation(drained):
    authority, journal, state = drained
    evidence = authority.prepare()
    assert evidence.candidate_sha256 == CANDIDATE and evidence.rollback_sha256 == ROLLBACK
    assert state.calls[:5] == ["retain", "stop_auth", "broker_drain", "aggregate", "stop_model"]
    def apply(lease):
        assert lease.assert_current(candidate_sha256=CANDIDATE, rollback_sha256=ROLLBACK) is True
        assert state.fence and journal.load().active
        return "staged"
    assert authority.run(apply) == "staged"
    assert state.fence and journal.load().needs_recovery
    assert journal.load().snapshot.completed is False


@pytest.mark.parametrize("gate", ["gates", "guards", "sleep_idle"])
def test_revoked_gate_never_authorizes_mutation(drained, gate):
    authority, journal, state = drained
    authority.prepare()
    setattr(state, gate, False)
    with pytest.raises(DeploymentDrainError):
        authority.run(lambda check: pytest.fail("mutation must not run"))
    assert state.fence and journal.load().needs_recovery
    setattr(state, gate, True)
    assert authority.check() is False


def test_unknown_aggregate_retains_fence_before_any_swap(drained):
    authority, journal, state = drained
    state.quiesce = lambda: 1
    with pytest.raises(DeploymentDrainError):
        authority.prepare()
    assert state.fence and journal.load().needs_recovery
    assert authority.check() is False


def test_auth_receipt_failure_never_requests_broker_drain(drained):
    authority, journal, state = drained
    state.auth_drained = lambda: False
    with pytest.raises(DeploymentDrainError):
        authority.prepare()
    assert 'broker_drain' not in state.calls
    assert 'stop_model' not in state.calls
    assert state.fence and journal.load().needs_recovery


def test_aggregate_failure_never_stops_model(drained):
    authority, journal, state = drained
    state.quiesce = lambda: False
    with pytest.raises(DeploymentDrainError):
        authority.prepare()
    assert 'broker_drain' in state.calls
    assert 'stop_model' not in state.calls
    assert state.fence and journal.load().needs_recovery


def test_model_stop_uncertainty_never_prepares_authority(drained):
    authority, journal, state = drained
    state.system.deployment_stop_verified = lambda *args, **kwargs: SystemEvidence(False, 'unknown')
    with pytest.raises(DeploymentDrainError):
        authority.prepare()
    assert authority.check() is False
    assert state.fence and journal.load().needs_recovery
    assert journal.load().snapshot.prepared is False


def test_mutation_failure_never_releases_or_restarts(drained):
    authority, journal, state = drained
    authority.prepare()
    def fail(check):
        raise TimeoutError("synthetic uncertain update")
    with pytest.raises(TimeoutError):
        authority.run(fail)
    assert state.fence and journal.load().needs_recovery and not authority.check()


def test_closed_owner_cannot_authorize_a_second_process(drained):
    authority, journal, state = drained
    authority.prepare()
    journal.close()
    assert authority.check() is False
    assert state.fence


def test_mesh_only_boolean_is_not_an_aggregate_platform(drained):
    authority, journal, state = drained
    state.system.verify_stop_guards = lambda: True
    with pytest.raises(DeploymentDrainError):
        authority.prepare()
    assert not authority.check()


def test_success_is_single_use_and_does_not_release_fences(drained):
    authority, journal, state = drained
    authority.prepare()
    authority.run(lambda lease: "done")
    with pytest.raises(DeploymentDrainError):
        authority.run(lambda lease: pytest.fail("second submission"))
    assert state.fence and journal.load().needs_recovery
    assert authority.check() is False


@pytest.mark.parametrize("change", ["candidate", "rollback", "boot", "timeout", "binding", "broker", "marker"])
def test_live_authority_revocation_is_sticky(drained, change):
    authority, journal, state = drained
    authority.prepare()
    candidate, rollback = CANDIDATE, ROLLBACK
    if change == "candidate":
        candidate = "f" * 64
    elif change == "rollback":
        rollback = "f" * 64
    elif change == "boot":
        state.boot = str(uuid.uuid4())
    elif change == "timeout":
        state.now = 600
    elif change == "binding":
        state.binding_ok = False
    elif change == "broker":
        state.broker_ok = False
    else:
        authority._marker.unlink()
    with pytest.raises((DeploymentDrainError, FileNotFoundError)):
        authority.assert_current(candidate_sha256=candidate, rollback_sha256=rollback)
    assert not authority.check()
    assert state.fence and journal.load().active


def test_manifest_changed_inside_mutation_denies_final_check(drained):
    authority, journal, state = drained
    authority.prepare()
    def mutate(lease):
        state.binding_ok = False
    with pytest.raises(DeploymentDrainError):
        authority.run(mutate)
    assert journal.load().needs_recovery


def test_cross_thread_cannot_use_live_authority(drained):
    import threading
    authority, journal, state = drained
    authority.prepare()
    results = []
    thread = threading.Thread(target=lambda: results.append(authority.check()))
    thread.start()
    thread.join(timeout=1)
    assert results == [False] and not authority.check()


def test_closed_lease_retains_journal_fence(drained):
    authority, journal, state = drained
    authority.prepare()
    authority.close()
    assert state.fence and not authority.check()


@pytest.mark.parametrize("value", [True, {}, {"broker_id": "a" * 32, "transition_id": "wrong", "vault_epoch": "b" * 64 + ":1"}])
def test_uncertain_broker_response_never_stops_model(drained, value):
    authority, journal, state = drained
    authority._control = lambda *args: value
    with pytest.raises(DeploymentDrainError):
        authority.prepare()
    assert not state.model_stopped and state.fence and journal.load().active
