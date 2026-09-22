import json
import os
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage_lantern.message_storage import MessageStorageSnapshot
from sage_lantern.storage_supervisor import (
    DrainProof, Producer, RootStorageSupervisor, RootVoiceBackend, StorageSupervisorError,
)
from sage_lantern.storage_supervisor_socket import CaptureLease, StorageBrokerServer, drain_check
import sage_lantern.storage_supervisor as module
import sage_lantern.storage_supervisor_socket as wire


class Backend:
    def __init__(self):
        self.events = []
        self.running = False
        self.vault = (800, "a" * 32)
        self.instance = Producer(901, 1001, {"instance_id": str(uuid.uuid4())})
        self.on_stop = lambda: None
        self.stop_result = True
        self.gates = True

    def vault_identity(self):
        return self.vault

    def start_voice(self):
        self.events.append("start_voice")
        assert not self.running
        self.running = True
        return self.instance

    def producer(self, *, stopping=False):
        if not self.running:
            raise StorageSupervisorError()
        return self.instance

    def drain_prerequisites(self, transition):
        self.events.append("auth_and_fence")
        return self.gates

    def stop_voice(self, producer, transition, *, timeout):
        self.events.append("stop_voice")
        assert producer == self.instance
        self.on_stop()
        self.running = False
        return self.stop_result

    def voice_inactive(self):
        self.events.append("voice_absent")
        return not self.running

    def stop_vault(self, expected):
        self.events.append("stop_vault")
        assert expected == self.vault and not self.running
        return True

    def resume_prerequisites(self):
        return self.gates and not self.running


@pytest.fixture
def setup(tmp_path):
    directory = tmp_path.resolve() / "state"
    directory.mkdir(mode=0o700)
    backend = Backend()
    state = dict(epoch="b" * 32, snapshot=MessageStorageSnapshot("c" * 64, "1"), now=10.0)
    supervisor = RootStorageSupervisor(directory, backend=backend, snapshot=lambda: state["snapshot"],
                                       access_epoch=lambda: state["epoch"], clock=lambda: state["now"], owner_uid=os.geteuid())
    yield supervisor, backend, state
    supervisor.close()


def capture(supervisor, state):
    return supervisor.acquire(pid=901, uid=1001, epoch=state["epoch"])


@pytest.mark.parametrize("method", ["voice", "resource", "lease"])
@pytest.mark.parametrize("change_phase", [False, True])
def test_diagnostic_io_releases_state_lock_and_revalidates(setup, method, change_phase):
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    token = capture(supervisor, state)
    observed = []
    def snapshot():
        def check_lock():
            acquired = supervisor._mutex.acquire(blocking=False)
            observed.append(acquired)
            if acquired:
                try:
                    if change_phase:
                        supervisor.phase = "recovery_required"
                finally:
                    supervisor._mutex.release()
        thread = threading.Thread(target=check_lock)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
        return state["snapshot"]
    supervisor._snapshot = snapshot
    result = (supervisor.voice_peer(pid=901, uid=1001) if method == "voice"
              else supervisor.check(token, pid=901, uid=1001) if method == "lease"
              else supervisor.resource_lifecycle_check())
    assert observed == [True]
    assert result is (not change_phase)


def test_admission_checks_reuse_one_verification_per_window(setup):
    """A dispatch wake makes ~165 admission checks; each one used to pay its own
    signed snapshot plus two systemctl reads, which saturated the unit's SAGE
    replay window (unit A 2026-09-19 17:02: 503 Replay window saturated)."""
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    token = capture(supervisor, state)
    probes, vaults, producers = [], [], []
    supervisor._snapshot = lambda: probes.append(state["now"]) or state["snapshot"]
    original_vault, original_producer = backend.vault_identity, backend.producer
    backend.vault_identity = lambda: vaults.append(state["now"]) or original_vault()
    backend.producer = lambda *, stopping=False: producers.append(stopping) or original_producer(stopping=stopping)
    for _ in range(40):
        assert supervisor.voice_peer(pid=901, uid=1001) is True
        assert supervisor.check(token, pid=901, uid=1001) is True
        assert supervisor.verified_epoch() == state["snapshot"].epoch
        state["now"] += module.LIVE_CHECK_SECONDS / 200
    assert (len(probes), len(vaults), producers) == (1, 1, [False])
    state["now"] += module.LIVE_CHECK_SECONDS + 0.001
    assert supervisor.voice_peer(pid=901, uid=1001) is True
    assert (len(probes), len(vaults)) == (2, 2)


def test_admission_checks_observe_a_new_vault_within_one_window(setup):
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    token = capture(supervisor, state)
    assert supervisor.voice_peer(pid=901, uid=1001) is True
    state["snapshot"] = MessageStorageSnapshot("e" * 64, "2")
    assert supervisor.voice_peer(pid=901, uid=1001) is True
    assert supervisor.check(token, pid=901, uid=1001) is True
    state["now"] += module.LIVE_CHECK_SECONDS + 0.001
    assert supervisor.voice_peer(pid=901, uid=1001) is False
    assert supervisor.check(token, pid=901, uid=1001) is False
    with pytest.raises(StorageSupervisorError):
        supervisor.verified_epoch()


def test_admission_checks_observe_a_new_producer_within_one_window(setup):
    supervisor, backend, state = setup
    supervisor.start()
    assert supervisor.voice_peer(pid=901, uid=1001) is True
    backend.instance = Producer(902, 1001, {"instance_id": str(uuid.uuid4())})
    assert supervisor.voice_peer(pid=901, uid=1001) is True
    state["now"] += module.LIVE_CHECK_SECONDS + 0.001
    assert supervisor.voice_peer(pid=901, uid=1001) is False


def test_reuse_never_outlives_an_in_process_change(setup):
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    token = capture(supervisor, state)
    assert supervisor.voice_peer(pid=901, uid=1001) is True
    assert supervisor.check(token, pid=901, uid=1001) is True
    supervisor.phase = "recovery_required"
    assert supervisor.voice_peer(pid=901, uid=1001) is False
    assert supervisor.check(token, pid=901, uid=1001) is False
    with pytest.raises(StorageSupervisorError):
        supervisor.verified_epoch()


def test_drain_never_reads_the_reused_live_producer(setup):
    supervisor, backend, state = setup
    supervisor.start()
    stops = []
    original = backend.producer
    backend.producer = lambda *, stopping=False: stops.append(stopping) or original(stopping=stopping)
    assert supervisor.voice_peer(pid=901, uid=1001) is True
    assert supervisor.voice_peer(pid=901, uid=1001) is True
    assert stops == [False]
    backend.running = False
    assert supervisor.voice_peer(pid=901, uid=1001, draining=True) is False
    assert stops[-1] is True


def test_real_lifetime_order_and_explicit_vault_gate(setup):
    supervisor, backend, state = setup
    supervisor.start()
    token = capture(supervisor, state)
    observed = []
    def during_stop():
        state["epoch"] = None
        observed.append(supervisor.check(token, pid=901, uid=1001))
        observed.append(supervisor.drain_check(pid=901, uid=1001))
        with pytest.raises(StorageSupervisorError):
            capture(supervisor, state)
    backend.on_stop = during_stop
    proof = supervisor.drain(str(uuid.uuid4()))
    assert observed == [True, True]
    assert not supervisor.check(token, pid=901, uid=1001)
    assert "stop_vault" not in backend.events
    supervisor.close_vault(proof)
    assert supervisor.phase == "vault_closed"
    assert backend.events.index("auth_and_fence") < backend.events.index("stop_voice") < backend.events.index("stop_vault")


@pytest.mark.parametrize("bad", [False, None, 1, {"confirmed": True}])
def test_unknown_drain_never_stops_vault_or_reopens(setup, bad):
    supervisor, backend, state = setup
    supervisor.start()
    token = capture(supervisor, state)
    backend.stop_result = bad
    with pytest.raises(StorageSupervisorError):
        supervisor.drain(str(uuid.uuid4()))
    assert supervisor.phase == "recovery_required"
    assert json.loads((supervisor.directory / "fence.json").read_text())["phase"] == "recovery_required"
    assert not supervisor.check(token, pid=901, uid=1001)
    with pytest.raises(StorageSupervisorError):
        supervisor.start()
    assert "stop_vault" not in backend.events


def test_timeout_is_not_join_or_recovery_authority(setup):
    supervisor, backend, state = setup
    supervisor.start()
    backend.on_stop = lambda: state.update(now=50)
    with pytest.raises(StorageSupervisorError):
        supervisor.drain(str(uuid.uuid4()), timeout=5)
    assert supervisor.phase == "recovery_required"
    assert "stop_vault" not in backend.events


def test_checks_continue_on_other_thread_while_stop_waits(setup):
    supervisor, backend, state = setup
    supervisor.start()
    token = capture(supervisor, state)
    entered, finished = threading.Event(), threading.Event()
    backend.on_stop = lambda: (entered.set(), finished.wait(2))
    results = []
    worker = threading.Thread(target=lambda: results.append(supervisor.drain(str(uuid.uuid4()))))
    worker.start()
    assert entered.wait(1)
    try:
        assert supervisor.check(token, pid=901, uid=1001) is True
        assert supervisor.drain_check(pid=901, uid=1001) is True
        with pytest.raises(StorageSupervisorError):
            supervisor.drain(str(uuid.uuid4()))
    finally:
        finished.set()
        worker.join(2)
    assert len(results) == 1


@pytest.mark.parametrize("change", ["vault", "epoch", "process", "expiry", "owner", "fence"])
def test_lease_binding_and_expiry_fail_closed(setup, change):
    supervisor, backend, state = setup
    supervisor.start()
    token = capture(supervisor, state)
    if change == "vault":
        backend.vault = (800, "d" * 32)
    elif change == "epoch":
        state["snapshot"] = MessageStorageSnapshot("c" * 64, "2")
    elif change == "process":
        backend.instance = replace(backend.instance, identity={"instance_id": str(uuid.uuid4())})
    elif change == "expiry":
        state["now"] += 30
    elif change == "owner":
        (supervisor.directory / "owner.lock").unlink()
        (supervisor.directory / "owner.lock").touch(mode=0o600)
    else:
        (supervisor.directory / "fence.json").unlink()
    assert supervisor.check(token, pid=901, uid=1001) is False
    assert "stop_vault" not in backend.events


def test_rearm_adopts_a_rotated_epoch_with_nothing_in_flight(setup):
    """An owner unlock rotates the epoch under a running broker (2026-09-22).

    Every guarded call compares the live pair against the process-start
    baseline, so without a re-arm `resource_lifecycle_check` - and therefore
    `active()`, provider construction and every community route - stays False
    until the daemon restarts. The re-arm is only allowed while nothing can be
    in flight, and a live lease still pins the pair it was admitted under.
    """
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    token = capture(supervisor, state)
    state["snapshot"] = MessageStorageSnapshot("c" * 64, "2")
    assert supervisor.check(token, pid=901, uid=1001) is False
    assert supervisor.resource_lifecycle_check() is False
    with pytest.raises(StorageSupervisorError):
        supervisor.rearm()
    supervisor.finish_capture(token, pid=901, uid=1001)
    assert supervisor.rearm() is True
    assert supervisor.resource_lifecycle_check() is True
    assert supervisor.verified_epoch() == state["snapshot"].epoch
    rearmed = capture(supervisor, state)
    assert supervisor.check(rearmed, pid=901, uid=1001)
    supervisor.finish_capture(rearmed, pid=901, uid=1001)
    assert supervisor.rearm() is False


def test_rearm_refuses_anything_it_cannot_prove_quiescent(setup):
    supervisor, backend, state = setup
    with pytest.raises(StorageSupervisorError):
        supervisor.rearm()
    supervisor.start()
    with pytest.raises(StorageSupervisorError):
        supervisor.rearm()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    token = capture(supervisor, state)
    state["snapshot"] = MessageStorageSnapshot("c" * 64, "2")
    with pytest.raises(StorageSupervisorError):
        supervisor.rearm()
    supervisor.finish_capture(token, pid=901, uid=1001)
    supervisor.phase = "recovery_required"
    with pytest.raises(StorageSupervisorError):
        supervisor.rearm()


def test_rearm_refuses_a_pair_that_moves_while_it_derives(setup):
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    baseline = supervisor._baseline
    reads = iter([MessageStorageSnapshot("c" * 64, "2"), MessageStorageSnapshot("c" * 64, "3")])
    supervisor._snapshot = lambda: next(reads)
    with pytest.raises(StorageSupervisorError):
        supervisor.rearm()
    assert supervisor._baseline == baseline


def test_lock_survival_is_not_new_capture_authority(setup):
    supervisor, backend, state = setup
    supervisor.start()
    token = capture(supervisor, state)
    state["epoch"] = None
    assert supervisor.check(token, pid=901, uid=1001)
    assert not supervisor.check(token, pid=902, uid=1001)
    assert not supervisor.check(token, pid=901, uid=0)
    assert not supervisor.drain_check(pid=901, uid=1001)
    with pytest.raises(StorageSupervisorError):
        capture(supervisor, state)
    supervisor.finish_capture(token, pid=902, uid=1001)
    assert supervisor.check(token, pid=901, uid=1001)
    supervisor.finish_capture(token, pid=901, uid=1001)
    assert not supervisor.check(token, pid=901, uid=1001)


def test_capture_limit_no_time_based_removal(setup):
    supervisor, backend, state = setup
    supervisor.start()
    for index in range(32):
        capture(supervisor, state)
    state["now"] += 100
    with pytest.raises(StorageSupervisorError):
        capture(supervisor, state)


def test_restart_refuses_old_journal_and_second_owner(setup):
    supervisor, backend, state = setup
    args = dict(backend=backend, snapshot=lambda: state["snapshot"], access_epoch=lambda: state["epoch"], owner_uid=os.geteuid())
    with pytest.raises(StorageSupervisorError):
        RootStorageSupervisor(supervisor.directory, **args)
    supervisor.start()
    supervisor.close()
    with pytest.raises(StorageSupervisorError):
        RootStorageSupervisor(supervisor.directory, **args)
    assert backend.events == ["start_voice"]


@pytest.mark.parametrize("bad", [False, 1, None])
def test_missing_auth_or_root_fence_never_stops_voice(setup, bad):
    supervisor, backend, state = setup
    supervisor.start()
    backend.gates = bad
    with pytest.raises(StorageSupervisorError):
        supervisor.drain(str(uuid.uuid4()))
    assert "stop_voice" not in backend.events


def test_copied_or_old_proof_cannot_close_vault(setup):
    supervisor, backend, state = setup
    supervisor.start()
    proof = supervisor.drain(str(uuid.uuid4()))
    with pytest.raises(StorageSupervisorError):
        supervisor.close_vault(replace(proof))
    assert "stop_vault" not in backend.events


def test_rpc_exact_peer_and_root_only_controls(setup):
    supervisor, backend, state = setup
    server = object.__new__(StorageBrokerServer)
    server.supervisor, server._proof = supervisor, None
    def transport(value):
        return server.dispatch(value, pid=901, uid=1001)
    for operation in ({"op": "start"}, {"op": "drain", "transition_id": str(uuid.uuid4())},
                      {"op": "close-vault", "transition_id": str(uuid.uuid4())}):
        with pytest.raises(StorageSupervisorError):
            transport(operation)
    assert server.dispatch({"op": "start"}, pid=1, uid=0)["started"] is True
    lease = CaptureLease(state["epoch"], transport=transport)
    assert lease.check()
    assert not drain_check(transport=transport)
    lease.close()
    assert not lease.check()
    with pytest.raises(StorageSupervisorError):
        transport({"op": "acquire", "epoch": state["epoch"], "pid": 901})


@pytest.mark.parametrize("value", [{"allowed": 1}, {"allowed": True, "extra": 1}, None, True])
def test_client_rejects_boolean_shaped_ack(value):
    lease = CaptureLease("a" * 32, transport=lambda request: {"lease": "b" * 64})
    lease._transport = lambda request: value
    assert lease.check() is False
    assert drain_check(transport=lease._transport) is False


def test_root_backend_fixed_vault_stop_and_process_tree_check(monkeypatch):
    events = []
    class System:
        timeout = 30
        clock = staticmethod(lambda: 1)
        def _show(self, unit, deadline, fields):
            events.append(("show", unit))
            if "InvocationID" in fields:
                return dict(ActiveState="active", Job="", MainPID="800", InvocationID="a" * 32)
            stopped = any(member[0] == "command" for member in events)
            return dict(ActiveState="inactive" if stopped else "active", Job="", MainPID="0" if stopped else "800",
                        ControlGroup="/system.slice/sage-lantern-personal-sage.service", KillMode="control-group")
        def _command(self, args, deadline):
            events.append(("command", args))
        def _cgroup(self, value):
            return Path("/sys/fs/cgroup") / value.lstrip("/")
        def _empty_tree(self, group, deadline):
            events.append(("empty_tree", str(group)))
        _no_job = staticmethod(lambda value: value == "")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    backend = RootVoiceBackend(voice_uid=1001, system=System())
    backend.voice_inactive = lambda: True
    assert backend.stop_vault(("800", "a" * 32)) is True
    assert ("command", ["stop", "sage-lantern-personal-sage.service"]) in events
    assert sum(member[0] == "empty_tree" for member in events) == 2


@pytest.mark.parametrize("failure", [None, "wrong_transition", "wrong_process", "integer_check", "missing_check", "unknown_stop"])
def test_root_voice_request_receipt_and_cgroup_order(monkeypatch, failure):
    events = []
    transition = str(uuid.uuid4())
    identity = dict(instance_id=str(uuid.uuid4()), pid=901, start_ticks="123", boot_id=str(uuid.uuid4()), invocation_id="d" * 32)
    producer = Producer(901, 1001, identity)
    receipt = dict(schema="sage.lantern.voice-drain.v1", **identity, transition_id=transition,
                   drained=True, reconcile_required=False, checks=dict.fromkeys(module.CHECKS, True))
    if failure == "wrong_transition":
        receipt["transition_id"] = str(uuid.uuid4())
    elif failure == "wrong_process":
        receipt["pid"] += 1
    elif failure == "integer_check":
        receipt["checks"]["community_drained"] = 1
    elif failure == "missing_check":
        del receipt["checks"]["community_drained"]
    class System:
        timeout = 30
        def stop_verified(self, service):
            events.append("stop_and_cgroup")
            return SimpleNamespace(confirmed=None if failure == "unknown_stop" else True)
        def inactive_verified(self, service):
            events.append("inactive_again")
            return SimpleNamespace(confirmed=True)
    def publish(path, value, **kwargs):
        assert path == Path("/run/sage-lantern-voice-drain-request.json")
        assert value == {**identity, "schema": "sage.lantern.voice-drain-request.v1", "transition_id": transition}
        assert kwargs == {"mode": 0o644}
        events.append("request")
    def read(path, **kwargs):
        assert kwargs == {"owner_uid": 1001}
        events.append("receipt")
        return receipt
    monkeypatch.setattr(module, "publish", publish)
    monkeypatch.setattr(module, "read_json", read)
    backend = RootVoiceBackend(voice_uid=1001, system=System())
    backend.producer = lambda: producer
    assert backend.stop_voice(producer, transition, timeout=10) is (failure is None)
    assert events[:2] == ["request", "stop_and_cgroup"]
    if failure == "unknown_stop":
        assert "receipt" not in events
    if failure is None:
        assert events == ["request", "stop_and_cgroup", "receipt", "inactive_again"]
    assert backend.system.timeout == 30


@pytest.mark.parametrize("bad", ["empty", "long", "duplicate", "trailing", "list", "nan"])
def test_bounded_wire_rejects_malformed_input(bad):
    bodies = {"empty": b"", "long": b" " * 1025, "duplicate": b'{"op":"start","op":"drain"}\n',
              "trailing": b'{"op":"start"}\n{}\n', "list": b"[]\n", "nan": b'{"op":NaN}\n'}
    class Connection:
        def __init__(self):
            self.body = bodies[bad]
        def recv(self, length):
            result, self.body = self.body[:length], self.body[length:]
            return result
    with pytest.raises((StorageSupervisorError, ValueError)):
        wire._receive(Connection())


def test_failed_vault_stop_retains_fence_and_no_second_attempt(setup):
    supervisor, backend, state = setup
    supervisor.start()
    proof = supervisor.drain(str(uuid.uuid4()))
    called = []
    def uncertain(expected):
        called.append(expected)
        raise TimeoutError()
    backend.stop_vault = uncertain
    for index in range(2):
        with pytest.raises(StorageSupervisorError):
            supervisor.close_vault(proof)
    assert len(called) == 1
    assert supervisor.phase == "recovery_required"


def test_lock_between_acquire_checks_discards_lease(setup):
    supervisor, backend, state = setup
    supervisor.start()
    reads = iter([state["epoch"], "e" * 32])
    supervisor._access = lambda: next(reads)
    with pytest.raises(StorageSupervisorError):
        capture(supervisor, state)
    assert supervisor._leases == {}


@pytest.mark.parametrize("damage", ["mode", "symlink", "hardlink"])
def test_private_owner_rejects_unsafe_files(tmp_path, damage):
    directory = tmp_path.resolve() / "state"
    directory.mkdir(mode=0o700)
    lock = directory / "owner.lock"
    if damage == "mode":
        lock.touch(mode=0o644)
    elif damage == "symlink":
        lock.symlink_to(directory / "missing")
    else:
        other = directory / "other"
        other.touch(mode=0o600)
        os.link(other, lock)
    with pytest.raises(StorageSupervisorError):
        RootStorageSupervisor(directory, backend=Backend(), snapshot=lambda: None,
                              access_epoch=lambda: None, owner_uid=os.geteuid())


def test_absent_or_nonlinux_broker_never_grants(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1001)
    with pytest.raises(StorageSupervisorError):
        StorageBrokerServer(None)
    def absent(request):
        raise ConnectionRefusedError()
    assert drain_check(transport=absent) is False
    with pytest.raises(ConnectionRefusedError):
        CaptureLease("a" * 32, transport=absent)


def test_root_owned_resource_must_join_before_vault_stop(setup):
    supervisor, backend, state = setup
    supervisor.start()
    calls = []
    class Resource:
        def close(self, timeout):
            calls.append(supervisor.resource_lifecycle_check())
            return True
    supervisor.own_resource(Resource())
    proof = supervisor.drain(str(uuid.uuid4()))
    assert calls == [True]
    assert supervisor.resource_lifecycle_check() is False
    supervisor.close_vault(proof)


@pytest.mark.parametrize("result", [False, None, 1])
def test_resource_uncertainty_forbids_vault_close(setup, result):
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: result))
    with pytest.raises(StorageSupervisorError):
        supervisor.drain(str(uuid.uuid4()))
    assert supervisor.phase == "recovery_required" and "stop_vault" not in backend.events


def test_background_lifecycle_check_verifies_the_identity_once_per_window(setup):
    """A dispatch wake makes dozens of guarded calls per fragment (unit B,
    2026-09-19: ~165 checks, ~5 s of a 10 s radio budget). The epoch/vault pair
    each of them re-derived is verified once per LIVE_CHECK_SECONDS instead of
    once per call."""
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    epochs, vaults = [], []
    original, original_vault = supervisor._snapshot, backend.vault_identity
    def snapshot():
        epochs.append(state["now"])
        return original()
    def vault():
        vaults.append(state["now"])
        return original_vault()
    supervisor._snapshot, backend.vault_identity = snapshot, vault
    for _ in range(5):
        assert supervisor.resource_lifecycle_check() is True
    assert len(epochs) == 1 and len(vaults) == 1
    state["now"] += module.LIVE_CHECK_SECONDS + 0.001
    assert supervisor.resource_lifecycle_check() is True
    assert len(epochs) == 2 and len(vaults) == 2


def test_background_lifecycle_check_observes_a_new_vault_within_one_window(setup):
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    assert supervisor.resource_lifecycle_check() is True
    backend.vault = (801, "d" * 32)
    state["now"] += module.LIVE_CHECK_SECONDS + 0.001
    assert supervisor.resource_lifecycle_check() is False


def test_background_lifecycle_check_cache_does_not_outlive_a_phase_change(setup):
    supervisor, backend, state = setup
    supervisor.start()
    resource = SimpleNamespace(close=lambda timeout: True)
    supervisor.own_resource(resource)
    assert supervisor.resource_lifecycle_check() is True
    supervisor._resource_joined = True
    assert supervisor.resource_lifecycle_check() is False
    supervisor._resource_joined = False
    assert supervisor.resource_lifecycle_check() is True


def test_clean_resume_starts_fresh_voice_and_rejects_old_broker(setup):
    supervisor, backend, state = setup
    supervisor.start()
    previous = supervisor.broker_id
    proof = supervisor.drain(str(uuid.uuid4()))
    supervisor.resume(proof)
    assert supervisor.phase == "active" and supervisor.broker_id != previous
    assert supervisor.voice_peer(pid=901, uid=1001, broker_id=previous) is False
    assert supervisor.voice_peer(pid=901, uid=1001, broker_id=supervisor.broker_id) is True
    assert backend.events.count("start_voice") == 2
    with pytest.raises(StorageSupervisorError):
        supervisor.resume(proof)


@pytest.mark.parametrize("failure", [None, "handler", "capture"])
def test_server_shutdown_joins_before_sdk_and_owner_release(setup, failure):
    supervisor, backend, state = setup
    supervisor.start()
    events = []
    class Resource:
        def close(self, timeout):
            assert supervisor.resource_lifecycle_check() is True
            events.append("resource_join")
            return failure != "capture"
    supervisor.own_resource(Resource())
    class Thread:
        def __init__(self, name):
            self.name = name
        def join(self, timeout):
            events.append(self.name)
        def is_alive(self):
            return failure == "handler" and self.name == "handler_join"
    server = object.__new__(StorageBrokerServer)
    server._closed = False
    server._stop = threading.Event()
    server._socket = SimpleNamespace(close=lambda: events.append("listener_stop"))
    server._serving_thread = Thread("accept_join")
    server._mutex = threading.Lock()
    server._workers = {Thread("handler_join")}
    server.supervisor = supervisor
    server._inode = 123
    server.path = SimpleNamespace(lstat=lambda: SimpleNamespace(st_ino=123), unlink=lambda: events.append("socket_removed"))
    client = SimpleNamespace(__exit__=lambda *args: events.append("sdk_closed"))
    original_close = supervisor.close
    def closed():
        events.append("owner_released")
        original_close()
    supervisor.close = closed
    retained = len(wire._RETAINED_SHUTDOWN)
    try:
        clean = wire.shutdown_process(supervisor, server, client, timeout=1)
        assert clean is (failure is None)
        if failure is None:
            assert events.index("handler_join") < events.index("resource_join") < events.index("owner_released") < events.index("sdk_closed")
            assert supervisor.phase == "stopped_verified"
            assert len(wire._RETAINED_SHUTDOWN) == retained
        else:
            assert "sdk_closed" not in events and "owner_released" not in events
            assert supervisor._fd is not None
            os.fstat(supervisor._fd)
            assert supervisor.phase == "stopping"
            assert len(wire._RETAINED_SHUTDOWN) == retained + 1
            assert wire._RETAINED_SHUTDOWN[-1][0] is supervisor
    finally:
        del wire._RETAINED_SHUTDOWN[retained:]
