from dataclasses import asdict
import copy
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
import sage_lantern.mesh_daemon as module

from sage_lantern.mesh_daemon import HardwareRuntime, MeshDaemon, SCHEMA, device_policy, validate_config
from sage_lantern.mesh_usb_opener import USBOpenError
from sage_lantern.mesh_voice_ipc import MeshIPCError, MeshVoiceFacade, receive, send
from sage_lantern.mesh_community import MeshRequestAccess
from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.mesh_envelope import Peer
from sage_lantern.mesh_outbox import MeshOutbox
from sage_lantern.meshcore_transport import MeshCoreError
from test_mesh_runtime import factory


def configuration(state):
    return dict(schema=SCHEMA, unit_id="unit-a", voice_uid=1001, endpoint=state.config.endpoint,
        chain_id="local", agent_id=state.identity.agent_id, identity_file="/var/lib/sage-lantern/identity/lantern-agent.key", receiver_slot=state.config.receiver_slot,
        radio=dict(stable_path="/dev/serial/by-id/SyntheticRadio", serial="synthetic", vendor_id="1234", product_id="abcd", public_key=state.radio.hex()),
        peers=[dict(chain_id="remote", agent_id=state.remote.agent_id, radio_public_key=state.radio.hex())],
        approve_initial_open=True, acknowledge_reset=True, messaging_enabled=False, receive_while_locked=False,
        dispatch_enabled=False, dispatch_while_locked=False)


class Supervisor:
    phase = "active"
    broker_id = "b" * 32
    # The synthetic node answers one instance id and one vault generation
    # (test_mesh_runtime.factory), so this is the epoch the real broker would
    # have verified inside its window.
    epoch = "d" * 64 + ":1"
    def __init__(self):
        self.alive = True
        self.resource = None
    def voice_peer(self, *, pid, uid, broker_id=None, draining=False):
        return self.alive and (pid, uid) == (901, 1001) and broker_id in (None, self.broker_id) and (self.phase == "active" or draining)
    def resource_lifecycle_check(self):
        return self.alive and self.phase in ("active", "draining")
    def admission_epoch(self):
        if not (self.alive and self.phase in ("active", "draining", "stopping")):
            raise MeshIPCError()
        return self.epoch
    def resource_drain(self, timeout):
        return self.phase == "draining" and self.resource.close(timeout)


@pytest.fixture
def runtime(factory, monkeypatch):
    import sage_lantern.mesh_radio_commission as commission
    monkeypatch.setattr(commission, 'no_unknown', lambda: True)
    state = factory
    state.adopt()
    supervisor = Supervisor()
    config = configuration(state)
    opened = []
    class Opener:
        def __init__(self, *, policy, startup_fence, journals_reconciled):
            self.fence, self.reconciled = startup_fence, journals_reconciled
            assert policy.allow_initial_open is True and policy.acknowledge_possible_reset is True
        def open(self, **kwargs):
            with self.fence() as check:
                assert check() is True and self.reconciled() is True
                opened.append(kwargs)
                return state.scheduler
    resource = HardwareRuntime(supervisor, config, state.identity, state.runtime.store.storage,
        identity_path=Path("/proc/self/fd/12345"),
        config_reader=lambda: copy.deepcopy(config), opener_factory=Opener, epoch_reader=lambda: "e" * 32,
        lock_marker_check=lambda: True)
    supervisor.resource = resource
    yield state, supervisor, resource, config, opened
    supervisor.phase = "draining"
    resource.close(2)


def test_failed_health_connection_is_released_only_without_messaging(runtime):
    state, supervisor, resource, config, opened = runtime
    closes = []
    scheduler = SimpleNamespace(_client=SimpleNamespace(_poisoned=True),
        close=lambda **kwargs: closes.append(kwargs) or True)
    resource._coordinator = scheduler
    resource.release_failed_health_radio(lambda: True)
    assert resource._coordinator is None and closes == [{'timeout': 3}]
    resource._coordinator = scheduler
    resource._provider = SimpleNamespace(_runtime=SimpleNamespace(admission_safe=lambda: True))
    resource.release_failed_health_radio(lambda: True)
    assert resource._coordinator is scheduler and len(closes) == 1
    resource._provider = None


def beacon_resource(coordinator=None, epoch=lambda: None):
    """Bare resource for the locked-beacon policy; never opened or started."""
    resource = object.__new__(HardwareRuntime)
    resource._closed = resource._uncertain = False
    resource._provider = None
    resource._coordinator = coordinator
    resource._mutex = threading.Lock()
    resource._epoch = epoch
    resource.config = {"radio": {"public_key": "ab" * 32}}
    resource.configured = lambda: True
    resource.supervisor = SimpleNamespace(phase="active", resource_lifecycle_check=lambda: True)
    return resource


def test_locked_beacon_never_opens_a_closed_radio():
    resource = beacon_resource()
    assert resource.beacon(lambda: True) is False
    with pytest.raises(MeshIPCError):
        resource.beacon(lambda: False)
    resource._mutex.acquire()
    try:
        assert resource.beacon(lambda: True) is False
    finally:
        resource._mutex.release()


def test_locked_beacon_advertises_once_through_the_open_radio():
    seen = []
    class Radio:
        def health(self, **arguments):
            seen.append(arguments)
            return {"packets_received": 7, "uptime_seconds": 5}
    class Future:
        def result(self, timeout):
            assert 0 < timeout <= 5
            return {"packets_received": 7}
    class Coordinator:
        _client = SimpleNamespace(_poisoned=False, _identity=SimpleNamespace(firmware="v1.17.1-d929643"),
                                  _key=bytes.fromhex("ab" * 32))
        def submit(self, operation):
            operation(Radio())
            return Future()
    resource = beacon_resource(Coordinator())
    assert resource.beacon(lambda: True) is True
    assert [set(call) for call in seen] == [{"authorize", "announce"}] and seen[0]["announce"] is True
    assert seen[0]["authorize"]() is True


def test_locked_beacon_refuses_unverified_poisoned_or_other_identity_radio():
    seen = []
    class Coordinator:
        def __init__(self, client):
            self._client = client
        def submit(self, operation):
            seen.append(operation)
            return SimpleNamespace(result=lambda timeout: True)
    firmware = SimpleNamespace(firmware="v1.17.1-d929643")
    clients = (
        SimpleNamespace(_poisoned=True, _identity=firmware, _key=bytes.fromhex("ab" * 32)),
        SimpleNamespace(_poisoned=False, _identity=None, _key=bytes.fromhex("ab" * 32)),
        SimpleNamespace(_poisoned=False, _identity=firmware, _key=bytes.fromhex("cd" * 32)),
    )
    for client in clients:
        assert beacon_resource(Coordinator(client)).beacon(lambda: True) is False
    assert seen == []


def test_interrupted_receiver_does_not_poison_directory_or_radio_owner(runtime):
    state, supervisor, resource, config, opened = runtime
    identifier = config['receiver_slot']
    previous = state.rows[identifier]
    payload = dict(schema='sage.lantern.mesh-receiver.v1', state='interrupted',
        operation_id=str(uuid.uuid4()), raw_frame=None)
    state.runtime.store.put(identifier, 'mesh_inbound', previous['revision'], payload,
        access_check=lambda: True)
    before = copy.deepcopy(state.rows)
    contacts = resource.invoke('contacts', {}, lambda: True)
    assert len(contacts) == 1 and resource._provider is None
    assert resource.active() and not resource._attempted
    with pytest.raises(MeshIPCError):
        resource.invoke('ready', {}, lambda: True)
    assert resource.active() and not resource._attempted
    assert resource.invoke('contacts', {}, lambda: True) == contacts
    assert state.rows == before and not state.transmits


def test_real_sdk_factory_provider_queue_and_read(runtime):
    state, supervisor, resource, config, opened = runtime
    assert resource.invoke("ready", {}, lambda: True) is True
    assert len(opened) == 1
    contacts = resource.invoke("contacts", {}, lambda: True)
    assert len(contacts) == 1
    identifier = str(uuid.uuid4())
    sent = resource.invoke("queue", dict(card=contacts[0], text="Protected synthetic message", request_id=identifier,
                                       approval_binding="a" * 64), lambda: True)
    assert sent["state"] == "queued" and sent["message_id"] == identifier and not state.transmits
    page = resource.invoke("list_messages", {"box": "outbox"}, lambda: True)
    assert page["items"][0]["text"] == "Protected synthetic message"
    supervisor.phase = "draining"
    assert resource.close(3) is True
    assert state.released is True


def test_a_cancelled_row_lets_the_runtime_start_and_a_lapsed_one_does_not(runtime):
    """Reconcile's two halves, on the rows that actually held unit A down.

    A row left `dispatching` whose approval window has lapsed still refuses
    construction - that fail-closed behaviour is what unit A ran into, and it
    is not being relaxed. What changes is that the owner can end that row: a
    cancelled row is a decision, not an unresolved outcome, so the next
    runtime reconciles it and comes up.

    The rows here are written through MeshOutbox itself under a synthetic clock,
    so every proof is genuine and no signature is fabricated for the test.
    """
    state, supervisor, resource, config, opened = runtime
    store = state.runtime.store
    identifier = str(uuid.uuid4())
    peer = Peer('remote', bytes.fromhex(state.remote.agent_id))
    card = sign_contact_card(state.remote, chain_id='remote', radio_public_key=state.radio.hex(),
                             display_name='Synthetic peer', revision=1, issued_at=100, expires_at=1000,
                             now=150, findable=True, messaging_allowed=True)
    outbox = MeshOutbox(store, state.identity, sender=Peer('local', bytes.fromhex(state.identity.agent_id)),
                        guard=lambda action: True,
                        verify_contact=lambda action: verify_contact_card(card, now=150), clock=lambda: 150)
    outbox.queue(message_id=identifier, recipient=peer, radio_public_key=state.radio,
                 content='Synthetic held message', approval_binding='a' * 64, consent_binding='b' * 64,
                 issued_at=100, expires_at=200, access_check=lambda: True)
    body = outbox._verified(store.get(identifier, kind='mesh_outbound', access_check=lambda: True))
    dispatching = outbox._put({**body, 'state': 'dispatching', 'dispatch_timestamp': 150, 'attempts': 1},
                              body['revision'], lambda: True)

    def build():
        class Opener:
            def __init__(self, *, policy, startup_fence, journals_reconciled):
                self.fence, self.reconciled = startup_fence, journals_reconciled
            def open(self, **kwargs):
                with self.fence() as check:
                    assert check() is True and self.reconciled() is True
                    opened.append(kwargs)
                    return state.scheduler
        return HardwareRuntime(supervisor, config, state.identity, state.runtime.store.storage,
            identity_path=Path('/proc/self/fd/12345'), config_reader=lambda: copy.deepcopy(config),
            opener_factory=Opener, epoch_reader=lambda: 'e' * 32, lock_marker_check=lambda: True)

    # The lapsed window still refuses, and it still latches the unit closed.
    refusing = build()
    with pytest.raises(Exception):
        refusing.invoke('ready', {}, lambda: True)
    assert refusing._uncertain is True and refusing.active() is False and opened == []

    # The owner ends the row; the next runtime reconciles it and serves.
    cancelled = outbox.cancel(identifier, expected_revision=dispatching['revision'],
                              decision_binding='owner-cancel-20260921', access_check=lambda: True)
    assert cancelled.state == 'cancelled'
    accepting = build()
    assert accepting.invoke('ready', {}, lambda: True) is True
    assert len(opened) == 1


def test_radio_unknown_journal_blocks_new_runtime(runtime, monkeypatch):
    import sage_lantern.mesh_radio_commission as commission
    state, supervisor, previous, config, opened = runtime
    monkeypatch.setattr(commission, 'no_unknown', lambda: False)
    resource = HardwareRuntime(supervisor, config, state.identity, state.runtime.store.storage,
        identity_path=Path('/proc/self/fd/12345'), config_reader=lambda: config,
        lock_marker_check=lambda: True)
    assert resource._uncertain and not resource.active()
    with pytest.raises(Exception):
        resource.invoke('ready', {}, lambda: True)
    assert not opened


@pytest.mark.parametrize("change", ["extra", "path", "boolean", "endpoint", "duplicate_peer", "unknown_radio", "no_open", "no_reset"])
def test_manifest_is_exact_and_operator_bounded(factory, change):
    value = configuration(factory)
    if change == "extra":
        value["command"] = "anything"
    elif change == "path":
        value["identity_file"] = "/etc/shadow"
    elif change == "boolean":
        value["dispatch_enabled"] = 1
    elif change == "endpoint":
        value["endpoint"] = "http://example.com:8080"
    elif change == "duplicate_peer":
        value["peers"] *= 2
    elif change == "unknown_radio":
        value["radio"]["port"] = "/dev/ttyUSB9"
    elif change == "no_open":
        value["approve_initial_open"] = False
    else:
        value["acknowledge_reset"] = False
    with pytest.raises((MeshIPCError, ValueError)):
        validate_config(value)


def test_exact_device_policy_no_broad_serial_group(factory):
    policy = device_policy(configuration(factory))
    assert policy == "[Service]\nDevicePolicy=closed\nDeviceAllow=\nDeviceAllow=/dev/serial/by-id/SyntheticRadio rw\n"
    assert "*" not in policy and "char-" not in policy


def test_custody_config_is_optional_strict_and_requires_a_depositor(factory):
    value = configuration(factory)
    validate_config(value)  # no custody key: unchanged
    value["messaging_enabled"] = True
    value["custody"] = {"enabled": True, "depositors": ["peer-chain:" + "ab" * 32]}
    validate_config(value)
    for broken in ({"enabled": True, "depositors": []},
                   {"enabled": True, "depositors": ["not-a-source"]},
                   {"enabled": "yes", "depositors": ["peer-chain:" + "ab" * 32]},
                   {"enabled": False, "depositors": [], "extra": 1}):
        with pytest.raises((MeshIPCError, ValueError)):
            validate_config({**value, "custody": broken})


def test_unresolved_receive_blocks_first_open_and_retry(runtime):
    state, supervisor, resource, config, opened = runtime
    row = state.rows[config["receiver_slot"]]
    row["payload"].update(state="receiving", operation_id=str(uuid.uuid4()), raw_frame=None)
    for index in range(2):
        with pytest.raises(Exception):
            resource.invoke("ready", {}, lambda: True)
    assert opened == [] and resource._uncertain
    assert resource.close(1) is False


def flaky_resource(factory, monkeypatch, outcomes, *, mutate=None):
    """Build a HardwareRuntime whose opener replays `outcomes` in order.

    Each entry is an exception to raise, or None to succeed. `opened` records
    one entry per physical open attempt, so a test can prove how many times the
    radio was actually touched. `mutate(index, state, supervisor)` runs before a
    planned failure is raised.
    """
    import sage_lantern.mesh_radio_commission as commission
    monkeypatch.setattr(commission, 'no_unknown', lambda: True)
    state = factory
    state.adopt()
    supervisor = Supervisor()
    config = configuration(state)
    opened = []
    plan = list(outcomes)
    class Opener:
        def __init__(self, *, policy, startup_fence, journals_reconciled):
            self.fence, self.reconciled = startup_fence, journals_reconciled
            assert policy.allow_initial_open is True and policy.acknowledge_possible_reset is True
        def open(self, **kwargs):
            with self.fence() as check:
                assert check() is True and self.reconciled() is True
                opened.append(kwargs)
                failure = plan.pop(0) if plan else None
                if failure is not None:
                    if mutate is not None:
                        mutate(len(opened), state, supervisor)
                    raise failure
                return state.scheduler
    resource = HardwareRuntime(supervisor, config, state.identity, state.runtime.store.storage,
        identity_path=Path("/proc/self/fd/12345"),
        config_reader=lambda: copy.deepcopy(config), opener_factory=Opener, epoch_reader=lambda: "e" * 32,
        lock_marker_check=lambda: True)
    supervisor.resource = resource
    return state, supervisor, resource, opened


@pytest.mark.parametrize("error,retryable", [
    (MeshCoreError("serial_unavailable"), True),
    (MeshCoreError("send_unconfirmed", outcome_unknown=True), False),
    (OSError("no such device"), True),
    (MeshIPCError(), False),
    (USBOpenError("initial_open_not_approved"), False),
])
def test_only_a_pre_wire_open_failure_is_retryable(error, retryable):
    assert module.retryable_radio_open(error) is retryable


def test_one_flaky_pre_wire_open_is_retried_in_the_same_construction(factory, monkeypatch):
    state, supervisor, resource, opened = flaky_resource(factory, monkeypatch,
        [MeshCoreError("serial_unavailable"), None])
    assert resource.invoke("ready", {}, lambda: True) is True
    assert len(opened) == 2 and resource._provider is not None
    assert resource.active() and not resource._uncertain and resource._attempted
    assert resource.invoke("contacts", {}, lambda: True) is not None
    assert len(opened) == 2


def test_an_exhausted_open_retry_stays_fail_closed_and_single_attempted(factory, monkeypatch):
    state, supervisor, resource, opened = flaky_resource(factory, monkeypatch,
        [MeshCoreError("serial_unavailable") for _ in range(module.RADIO_OPEN_ATTEMPTS)])
    with pytest.raises(MeshCoreError):
        resource.invoke("ready", {}, lambda: True)
    assert len(opened) == module.RADIO_OPEN_ATTEMPTS
    assert resource._uncertain and not resource.active() and state.transmits == []
    with pytest.raises(MeshIPCError):
        resource.invoke("ready", {}, lambda: True)
    assert len(opened) == module.RADIO_OPEN_ATTEMPTS


def test_a_post_wire_open_failure_is_never_retried(factory, monkeypatch):
    state, supervisor, resource, opened = flaky_resource(factory, monkeypatch,
        [MeshCoreError("send_unconfirmed", outcome_unknown=True), None])
    with pytest.raises(MeshCoreError):
        resource.invoke("ready", {}, lambda: True)
    assert len(opened) == 1 and resource._uncertain and not resource.active()


def test_open_retry_stops_when_the_lifecycle_leaves_active(factory, monkeypatch):
    def leave_active(index, state, supervisor):
        supervisor.phase = "draining"
    state, supervisor, resource, opened = flaky_resource(factory, monkeypatch,
        [MeshCoreError("serial_unavailable"), None], mutate=leave_active)
    with pytest.raises(MeshCoreError):
        resource.invoke("ready", {}, lambda: True)
    assert len(opened) == 1 and resource._uncertain and state.transmits == []


def test_admitted_root_lease_survives_user_lock_but_not_owner_loss(runtime):
    state, supervisor, resource, config, opened = runtime
    lease = resource.capture_lease(resource.consent())
    assert lease.check()
    supervisor.phase = "draining"
    assert lease.check()
    with pytest.raises(Exception):
        resource.capture_lease(resource.consent())
    supervisor.alive = False
    assert lease.check() is False
    lease.close()
    assert lease.check() is False


def test_capture_lease_check_issues_no_signed_snapshot_per_check(runtime):
    """The per-admission epoch comparison rides the broker's bounded window.

    Each check used to issue its own signed /v1/messages/storage request; a
    dispatch wake makes ~165 of them inside a 10 s radio budget, and the unit's
    SAGE node fails its whole signed surface closed once 10,000 signatures land
    inside five minutes (unit A 2026-09-19 17:02: 54 `503 Replay window
    saturated` answers, A->B dispatch dead).
    """
    state, supervisor, resource, config, opened = runtime
    lease = resource.capture_lease(resource.consent())
    probes = [path for _, path in state.requests].count("/v1/messages/storage")
    for _ in range(20):
        assert lease.check() is True
    assert [path for _, path in state.requests].count("/v1/messages/storage") == probes
    lease.close()


def test_bad_method_or_arguments_never_opens_usb(runtime):
    state, supervisor, resource, config, opened = runtime
    for method, args in (("exec", {}), ("ready", {"path": "/tmp"}), ("queue", {}), ("list_messages", {"box": "all"})):
        with pytest.raises(Exception):
            resource.invoke(method, args, lambda: True)
    assert opened == []


class Buffer:
    def __init__(self, raw=b""):
        self.raw = bytearray(raw)
        self.output = bytearray()
    def recv(self, size):
        result = bytes(self.raw[:size])
        del self.raw[:size]
        return result
    def sendall(self, value):
        self.output.extend(value)


@pytest.mark.parametrize("raw", [b"\0\0\x80\x01", b"\0\0\0\0", b"\0\0\0\x02{", b"\0\0\0\x11{\"op\":1,\"op\":2}", b"\0\0\0\x02[]"])
def test_frame_bounds_duplicates_and_truncation(raw):
    with pytest.raises(Exception):
        receive(Buffer(raw))


def test_root_rejects_wrong_process_before_disclosing_binding(runtime):
    state, supervisor, resource, config, opened = runtime
    encoded = Buffer()
    send(encoded, {"op": "bind"})
    for pid, uid in ((902, 1001), (901, 1002), (901, 0)):
        connection = Buffer(encoded.output)
        with pytest.raises(Exception):
            MeshDaemon(supervisor, resource).handle(connection, pid=pid, uid=uid)
        assert connection.output == b""
    assert opened == []


def socket_connector(daemon, *, pid=901, uid=1001):
    workers = []
    def connector():
        client, server = socket.socketpair()
        client.settimeout(2)
        server.settimeout(2)
        def run():
            try:
                daemon.handle(server, pid=pid, uid=uid)
            except Exception:
                try:
                    send(server, {"error": "mesh_ipc_unavailable"})
                except Exception:
                    pass
            finally:
                server.close()
        worker = threading.Thread(target=run)
        workers.append(worker)
        worker.start()
        return client
    return connector, workers


def test_real_socket_facade_to_real_sdk_and_synthetic_radio(runtime):
    state, supervisor, resource, config, opened = runtime
    daemon = MeshDaemon(supervisor, resource, epoch_reader=lambda: "e" * 32)
    connector, workers = socket_connector(daemon)
    facade = MeshVoiceFacade(connector=connector, epoch_reader=lambda: "e" * 32)
    access = MeshRequestAccess("f" * 32, lambda: True)
    try:
        assert facade.ready(access_check=access) is True
        contact = facade.contacts(access_check=access)[0]
        submitted = []
        message = facade.queue(contact, text="IPC synthetic", request_id=str(uuid.uuid4()), approval_binding="a" * 64,
                               access_check=access, on_submit=lambda: submitted.append(True))
        assert submitted == [True] and message.state == "queued"
        assert facade.open_message(message.id, access_check=access).text == "IPC synthetic"
        assert facade.list_messages("outbox", access_check=access).items[0] == message
        assert not state.transmits
        supervisor.phase = "draining"
        assert facade.close(3) is True
    finally:
        for worker in workers:
            worker.join(3)
            assert not worker.is_alive()


@pytest.mark.parametrize("revoke", [False, True])
def test_ipc_waits_for_queued_work_and_rechecks_authority(runtime, monkeypatch, revoke):
    state, supervisor, resource, config, opened = runtime
    daemon = MeshDaemon(supervisor, resource, epoch_reader=lambda: "e" * 32)
    connector, workers = socket_connector(daemon)
    facade = MeshVoiceFacade(connector=connector, epoch_reader=lambda: "e" * 32)
    allowed = [True]
    original = resource.invoke
    def queued(*args, **kwargs):
        # A whole background job can legitimately hold the serial queue longer
        # than the old five-second socket read timeout, without any new action.
        time.sleep(5.2)
        if revoke:
            allowed[0] = False
        return original(*args, **kwargs)
    monkeypatch.setattr(resource, "invoke", queued)
    try:
        if revoke:
            with pytest.raises(MeshIPCError):
                facade.ready(access_check=lambda: allowed[0])
            assert not opened
        else:
            assert facade.ready(access_check=lambda: allowed[0]) is True
        assert not state.transmits
    finally:
        for worker in workers:
            worker.join(3)
            assert not worker.is_alive()


@pytest.mark.parametrize("failure", ["lock", "broker", "guard"])
def test_ipc_revocation_never_discloses_or_submits(runtime, failure):
    state, supervisor, resource, config, opened = runtime
    epoch = ["e" * 32]
    daemon = MeshDaemon(supervisor, resource, epoch_reader=lambda: epoch[0])
    connector, workers = socket_connector(daemon)
    facade = MeshVoiceFacade(connector=connector, epoch_reader=lambda: "e" * 32)
    if failure == "broker":
        facade._broker = "a" * 32
    calls = []
    def guard():
        calls.append(True)
        if failure == "lock" and len(calls) > 1:
            epoch[0] = "c" * 32
        return not (failure == "guard" and len(calls) > 1)
    try:
        with pytest.raises(MeshIPCError):
            facade.ready(access_check=guard)
        assert opened == []
    finally:
        for worker in workers:
            worker.join(3)
            assert not worker.is_alive()


def test_unit_confinement_and_no_voice_elevation():
    root = Path(__file__).resolve().parents[3]
    service = (root / "software/systemd/sage-lantern-mesh-runtime.service").read_text()
    voice = (root / "software/systemd/sage-lantern-voice-loop.service.d/40-mesh-broker.conf").read_text()
    for field in ("DevicePolicy=closed", "Restart=no", "NoNewPrivileges=yes", "ProtectSystem=strict",
                  "IPAddressDeny=any", "IPAddressAllow=127.0.0.1/32", "CapabilityBoundingSet=CAP_DAC_READ_SEARCH"):
        assert field in service
    assert "DeviceAllow=" not in service
    assert "User=root" not in voice and "Restart=no" in voice
    assert "voice-start-authorized.json" in voice


def test_mesh_cold_boot_does_not_require_voice_runtime_directory():
    root = Path(__file__).resolve().parents[3]
    service = (root / "software/systemd/sage-lantern-mesh-runtime.service").read_text()
    paths = [path for line in service.splitlines() if line.startswith("ReadOnlyPaths=")
             for path in line.split("=", 1)[1].split()]
    assert "-/run/sage-lantern-voice" in paths
    assert "/run/sage-lantern-voice" not in paths
    assert "ProtectSystem=strict" in service
    dependencies = [line for line in service.splitlines()
                    if line.startswith(("After=", "Requires=", "Wants="))]
    assert all("sage-lantern-voice-loop.service" not in line for line in dependencies)


def test_facade_is_explicit_community_provider_but_arbitrary_duck_type_rejected():
    from sage_lantern.community_service import CommunityService
    facade = MeshVoiceFacade()
    assert CommunityService(mesh_provider=facade)._mesh is facade
    with pytest.raises(ValueError):
        CommunityService(mesh_provider=object())


def test_close_wait_for_other_request_is_bounded():
    facade = MeshVoiceFacade(connector=lambda: (_ for _ in ()).throw(AssertionError("unexpected connection")))
    facade._mutex.acquire()
    start = time.monotonic()
    try:
        assert facade.close(0.02) is False
    finally:
        facade._mutex.release()
    assert time.monotonic() - start < 0.5


def test_missing_private_marker_denies_new_radio_use(runtime):
    state, supervisor, resource, config, opened = runtime
    resource._lock_marker = lambda: False
    with pytest.raises(Exception):
        resource.invoke("ready", {}, lambda: True)
    assert opened == []


def test_background_capture_uses_owned_root_lease_and_drains(runtime):
    state, supervisor, resource, config, opened = runtime
    config["messaging_enabled"] = True
    assert resource.invoke("ready", {}, lambda: True) is True
    deadline = time.monotonic() + 2
    # Seeing the initial row is not proof that capture admission completed.
    # Drain after a completed empty receive, whose durable state is idle.
    while time.monotonic() < deadline:
        row = state.rows.get(state.runtime.config.receiver_slot, {})
        if row.get("revision", 0) >= 3 and row["payload"]["state"] == "idle":
            break
        time.sleep(0.01)
    assert row.get("revision", 0) >= 3 and row["payload"]["state"] == "idle"
    state.scheduler.submit(lambda device: None).result(timeout=2)
    assert resource._provider._runtime._poller is not None
    worker = resource._provider._runtime._poller
    supervisor.phase = "draining"
    assert resource.close(3) is True
    assert not worker.is_alive()
    assert resource._provider._runtime._poller is None


def test_authenticated_ready_leaves_rearm_to_supervisor_and_preserves_unknown(controlled_background):
    values, provider, original, calls = controlled_background
    state, supervisor, resource, config, opened = values
    caller = threading.get_ident()
    assert resource.invoke("ready", {}, lambda: True) is True
    assert provider._runtime._poller is None and calls == []
    worker = _supervisor_tick(resource, original)
    assert worker != caller and calls == [worker]
    assert provider._runtime._poller.is_alive()
    assert provider.pause_receiving(timeout=2) is True
    provider._runtime._uncertain = True
    with pytest.raises(Exception):
        resource.invoke("ready", {}, lambda: True)
    count = len(calls)
    _supervisor_tick(resource, original)
    assert provider._runtime._poller is None and len(calls) == count


def test_supervisor_rearms_cleanly_paused_background_not_unknown(runtime):
    state, supervisor, resource, config, opened = runtime
    config["messaging_enabled"] = True
    assert resource.invoke("ready", {}, lambda: True) is True
    provider = resource._provider
    assert provider.pause_receiving(timeout=2) is True
    assert resource.invoke("ready", {}, lambda: True) is True
    # Re-admission belongs to the supervisor; an authenticated request no
    # longer restarts the worker synchronously on its own critical path.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        worker = provider._runtime._poller
        if worker is not None and worker.is_alive():
            break
        time.sleep(0.01)
    assert provider._runtime._poller.is_alive()
    assert provider.pause_receiving(timeout=2) is True
    provider._runtime._uncertain = True
    with pytest.raises(Exception):
        resource.invoke("ready", {}, lambda: True)
    assert provider._runtime._poller is None


@pytest.mark.parametrize("damage", [None, "symlink", "hardlink", "wrong_actor", "mode"])
def test_key_is_bounded_nofollow_actor_pinned_before_memory_copy(factory, tmp_path, monkeypatch, damage):
    config = configuration(factory)
    config["voice_uid"] = os.geteuid()
    seed = bytes(factory.identity._signing_key)
    key = tmp_path / "key"
    key.write_bytes(seed)
    key.chmod(0o600)
    if damage == "symlink":
        target = tmp_path / "target"
        key.rename(target)
        key.symlink_to(target)
    elif damage == "hardlink":
        os.link(key, tmp_path / "linked")
    elif damage == "wrong_actor":
        config["agent_id"] = factory.remote.agent_id
    elif damage == "mode":
        key.chmod(0o644)
    original_open = os.open
    copies, seals = [], []
    def opened(path, flags, *args, dir_fd=None, **kwargs):
        if path == "/":
            return original_open(tmp_path, flags)
        if flags & os.O_DIRECTORY:
            assert flags & os.O_NOFOLLOW
            return os.dup(dir_fd)
        assert path == "lantern-agent.key" and flags & os.O_NOFOLLOW
        return original_open(key, flags)
    def memory(name, flags):
        copies.append(name)
        return original_open(tmp_path / "synthetic-memory", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    monkeypatch.setattr(module.os, "open", opened)
    monkeypatch.setattr(module.os, "memfd_create", memory, raising=False)
    monkeypatch.setattr(module.os, "MFD_CLOEXEC", 1, raising=False)
    monkeypatch.setattr(module.os, "MFD_ALLOW_SEALING", 2, raising=False)
    for name, value in (("F_ADD_SEALS", 100), ("F_SEAL_SEAL", 1), ("F_SEAL_WRITE", 2), ("F_SEAL_GROW", 4), ("F_SEAL_SHRINK", 8)):
        monkeypatch.setattr(module.fcntl, name, value, raising=False)
    monkeypatch.setattr(module.fcntl, "fcntl", lambda *args: seals.append(args))
    if damage:
        with pytest.raises(MeshIPCError):
            module.load_identity(config)
        assert copies == []
    else:
        identity, descriptor = module.load_identity(config)
        try:
            assert identity.agent_id == config["agent_id"]
            assert os.pread(descriptor, 32, 0) == seed
            assert seals == [(descriptor, 100, 15)]
        finally:
            os.close(descriptor)


@pytest.mark.parametrize("failure", [None, "probe", "listener"])
def test_actual_main_composes_lifetime_and_closes_key_on_startup_failure(factory, monkeypatch, failure):
    import sys
    import sage_lantern.storage_supervisor_socket as socket_module
    events = []
    config = configuration(factory)
    monkeypatch.setattr(sys, "argv", ["mesh-daemon", "--serve"])
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.socket, "SO_PEERCRED", 999, raising=False)
    monkeypatch.setattr(module.os.path, "lexists", lambda path: False)
    monkeypatch.setattr(module, "_marker_state", lambda path: True)
    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "load_identity", lambda value: (factory.identity, 123456))
    monkeypatch.setattr(module.os, "close", lambda descriptor: events.append(("key_closed", descriptor)))
    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs == {"timeout": 5, "trust_env": False}
            events.append("client")
        def __exit__(self, *args):
            events.append("client_closed")
    def probe(*args, **kwargs):
        if failure == "probe":
            raise ValueError("synthetic")
        return SimpleNamespace(snapshot=lambda: events.append("storage_observed"))
    class Owner:
        def __init__(self, *args, **kwargs):
            events.append("owner")
        def start(self):
            events.append("voice_started")
        def own_resource(self, resource):
            events.append("resource_owned")
        def close(self):
            events.append("owner_closed_fence_retained")
        def begin_shutdown(self, timeout):
            events.append("admission_fenced")
            return True
        def finish_shutdown(self, timeout):
            events.append("resource_joined")
            return True
    class Event:
        def set(self):
            return None
        def is_set(self):
            return False
        def wait(self, timeout=None):
            return True
    class Thread:
        def __init__(self, *, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    class Server:
        def __init__(self, owner, *, path):
            assert path == module.SOCKET
            if failure == "listener":
                raise ValueError("synthetic")
            events.append("listener")
        def serve(self):
            events.append("serve")
        def close(self, timeout):
            events.append("listener_closed")
            return True
    def resource(*args, **kwargs):
        assert kwargs["identity_path"] == Path("/proc/self/fd/123456")
        events.append("resource")
        return object()
    monkeypatch.setattr(module, "SageClient", Client)
    monkeypatch.setattr(module, "MessageStorageProbe", probe)
    monkeypatch.setattr(module, "RootStorageSupervisor", Owner)
    monkeypatch.setattr(module, "MeshVoiceBackend", lambda **kwargs: object())
    monkeypatch.setattr(module, "HardwareRuntime", resource)
    monkeypatch.setattr(module.threading, "Event", Event)
    monkeypatch.setattr(module.threading, "Thread", Thread)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(socket_module, "StorageBrokerServer", Server)
    if failure:
        with pytest.raises(ValueError):
            module.main()
    else:
        module.main()
        assert events.index("storage_observed") < events.index("voice_started") < events.index("resource_owned") < events.index("serve")
    assert events[-2:] == ["client_closed", ("key_closed", 123456)]
    if failure == "listener":
        assert "owner_closed_fence_retained" in events
    if failure == "probe":
        assert "voice_started" not in events


def test_owned_background_runtime_joins_during_stop_before_probe_close(runtime, tmp_path):
    from sage_lantern.storage_supervisor import RootStorageSupervisor
    from test_storage_supervisor import Backend
    state, old_supervisor, resource, config, opened = runtime
    directory = tmp_path.resolve() / "owner"
    directory.mkdir(mode=0o700)
    owner = RootStorageSupervisor(directory, backend=Backend(), snapshot=resource.probe.snapshot,
        access_epoch=lambda: "e" * 32, owner_uid=os.geteuid())
    try:
        owner.start()
        resource.supervisor = owner
        owner.own_resource(resource)
        config["messaging_enabled"] = True
        assert resource.invoke("ready", {}, lambda: True) is True
        lease = resource.capture_lease(resource.consent())
        assert owner.begin_shutdown(10) is True
        assert lease.check() is True
        assert resource.active() is False
        worker = resource._provider._runtime._poller
        assert owner.finish_shutdown(5) is True
        assert worker.is_alive() is False
        assert resource._provider._runtime._poller is None
        assert state.released is True
        assert resource.probe.client._client.is_closed is False
        assert lease.check() is False
        assert owner._fd is not None
        lease.close()
    finally:
        owner.close()


class _OneSupervisorTick:
    def __init__(self):self.waits=0;self.stopped=False
    def wait(self, seconds):
        self.waits+=1
        return self.stopped or self.waits>1
    def set(self):self.stopped=True


@pytest.fixture
def controlled_background(request, monkeypatch):
    original=HardwareRuntime._supervise_background
    monkeypatch.setattr(HardwareRuntime,'_supervise_background',lambda self:None)
    values=request.getfixturevalue('runtime')
    state,supervisor,resource,config,opened=values
    config['messaging_enabled']=True
    assert resource.invoke('ready',{},lambda:True) is True
    provider=resource._provider
    assert provider.pause_receiving(timeout=2) is True
    calls=[];restart=provider.restart_receiving
    def record(**kwargs):
        calls.append(threading.get_ident());return restart(**kwargs)
    monkeypatch.setattr(provider,'restart_receiving',record)
    return values,provider,original,calls


def _supervisor_tick(resource, original):
    stop=_OneSupervisorTick();saved=resource._background_stop;resource._background_stop=stop
    errors=[]
    def invoke():
        try:original(resource)
        except BaseException as error:errors.append(type(error).__name__)
    thread=threading.Thread(target=invoke,name='controlled-mesh-supervisor')
    try:
        thread.start();thread.join(5)
        assert not thread.is_alive() and not errors and stop.waits==2
        return thread.ident
    finally:
        stop.set();thread.join(5);resource._background_stop=saved


def test_supervisor_rearm_refuses_lost_owner_and_never_waits_for_request_mutex(controlled_background):
    values,provider,original,calls=controlled_background
    state,supervisor,resource,config,opened=values
    assert resource._mutex.acquire(blocking=False)
    try:_supervisor_tick(resource,original)
    finally:resource._mutex.release()
    assert not calls and provider._runtime._poller is None
    supervisor.alive=False
    _supervisor_tick(resource,original)
    assert not calls and provider._runtime._poller is None and not state.transmits


@pytest.mark.parametrize('boundary',['entry','acquire'])
def test_known_capture_lease_refusal_before_token_return_is_not_unknown(runtime,monkeypatch,boundary):
    from sage_lantern.mesh_community import MeshActionAuthority, MeshLeaseRefused
    state,supervisor,resource,config,opened=runtime
    config['messaging_enabled']=True
    if boundary=='entry':supervisor.phase='draining'
    else:
        snapshot=resource.probe.snapshot
        def revoke_before_acquire():
            value=snapshot();supervisor.phase='draining';return value
        monkeypatch.setattr(resource.probe,'snapshot',revoke_before_acquire)
    with pytest.raises(MeshLeaseRefused):
        with MeshActionAuthority().receive_grant(resource.actor,'receive',resource.consent,
                resource.capture_lease,lambda:True):
            pytest.fail('refused lease must never yield an admitted context')
    assert not resource._uncertain and not state.transmits and opened==[]


def test_lost_acquisition_return_after_token_issue_remains_unknown(runtime):
    from sage_lantern.mesh_community import MeshActionAuthority
    from sage_lantern.mesh_runtime import MeshLeaseUncertain
    state,supervisor,resource,config,opened=runtime;config['messaging_enabled']=True;issued=[]
    def lost(consent):
        issued.append(resource.capture_lease(consent))
        raise RuntimeError('fixture lost acquired lease return')
    try:
        with pytest.raises(MeshLeaseUncertain):
            with MeshActionAuthority().receive_grant(resource.actor,'receive',resource.consent,lost,lambda:True):pass
        assert len(issued)==1 and issued[0].check() is True and not state.transmits
    finally:
        for lease in issued:lease.close()

# --- provider refusals: audible, and not a permanent latch -------------------
#
# Unit A answered 503 `mesh_ipc_unavailable` on every community route for a day
# after one provider start failed, and the journal could not say why: active()
# folds four conditions into one bool and resource_lifecycle_check() swallows
# its own exceptions. The next three tests pin the two properties that fix it -
# a refusal carries its conjuncts into the journal, and a refusal raised before
# the radio loop wrote nothing does not latch the unit.

def refusal_log(monkeypatch):
    written = []

    def syslog(_priority, message):
        written.append(json.loads(message))

    monkeypatch.setitem(sys.modules, 'syslog', SimpleNamespace(
        LOG_ERR=3, LOG_WARNING=4, LOG_INFO=6, syslog=syslog))
    return written


def test_a_pre_wire_refusal_names_its_conjuncts_and_does_not_latch(runtime, monkeypatch):
    state, supervisor, resource, config, opened = runtime
    written = refusal_log(monkeypatch)
    def guard():
        # True at the provider entry and through the receiver read, false the
        # moment the provider marks itself initializing: the shape of a session
        # or epoch moving between two checks in the same request. (A call
        # counter cannot express this - the store calls the guard itself.)
        return not resource._initializing

    with pytest.raises(MeshIPCError):
        resource.provider(guard)
    refusals = [row for row in written if row['event'] == 'mesh_provider_refused']
    assert refusals and refusals[-1]['stage'] == 'reconcile-entry'
    assert refusals[-1]['guard'] is False
    assert refusals[-1]['attempted'] is True and refusals[-1]['wire_started'] is False
    # Nothing reached the radio, so the attempt is cleared and the next request
    # may try again instead of every route answering 503 until a restart.
    assert resource._attempted is False and resource._initializing is False
    provider = resource.provider(lambda: True)
    assert provider is not None and resource._provider is provider
    assert opened, 'the retry never opened the radio'


def test_a_refusal_reports_the_supervisor_facts(runtime, monkeypatch):
    state, supervisor, resource, config, opened = runtime
    written = refusal_log(monkeypatch)
    supervisor.alive = False
    with pytest.raises(MeshIPCError):
        resource.provider(lambda: True)
    row = [entry for entry in written if entry['event'] == 'mesh_provider_refused'][-1]
    assert row['stage'] == 'provider-entry'
    assert row['active'] is False and row['resource_lifecycle'] is False
    assert row['supervisor_phase'] == 'active' and row['uncertain'] is False
    assert resource._attempted is False


def test_an_unknown_capture_state_stays_closed_and_is_never_cleared(runtime, monkeypatch):
    state, supervisor, resource, config, opened = runtime
    written = refusal_log(monkeypatch)
    resource._uncertain = True
    for _ in range(2):
        with pytest.raises(MeshIPCError):
            resource.provider(lambda: True)
    assert resource._uncertain is True
    assert resource.active() is False
    assert resource._provider is None
    row = [entry for entry in written if entry['event'] == 'mesh_provider_refused'][-1]
    assert row['uncertain'] is True and row['active'] is False
