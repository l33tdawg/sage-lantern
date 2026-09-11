import copy
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import time
from types import SimpleNamespace

import httpx
import pytest
from sage_sdk import AgentIdentity

from sage_lantern import mesh_daemon, mesh_gateway_root
from sage_lantern.public_gateway_bridge import LocalPublicREST
from sage_lantern.public_mesh_protocol import canonical
from test_mesh_daemon import runtime, factory
from test_public_mesh_protocol import GATEWAY, GATEWAY_KEY, IDENTITY
from test_public_gateway_runtime import gateway
from test_public_gateway_bridge import fixture


def settings(state, config):
    role = dict(schema="sage.lantern.public-gateway-role.v1", role="public_gateway", enabled=True,
        endpoint="http://127.0.0.1:19101", chain_id="commons", domain="public-domain", gateway_key=GATEWAY_KEY,
        storage_actor=config["agent_id"], storage_chain=config["chain_id"],
        pins=[dict(radio_key=state.radio.hex(), author=IDENTITY.agent_id)],
        timing=dict(frame_seconds=0.1, gateway_seconds=30, safety_seconds=10),
        operation_seconds=50, lease_seconds=55, poll_interval=0.01)
    return dict(identity_file="/var/lib/sage-lantern/identity/gateway.key", role=role,
        backend_pin=dict(pid=123, start_ticks="456", boot_id="00000000-0000-4000-8000-000000000001", listener_inode="789"),
        calibration=dict(schema="sage.lantern.gateway-calibration.v1", radio_key=config["radio"]["public_key"],
            role_digest=hashlib.sha256(canonical(role)).hexdigest(), issued_at=int(time.time())-1, expires_at=int(time.time())+600))


@pytest.fixture
def configured(runtime, monkeypatch):
    state, supervisor, resource, config, opened = runtime
    config.update(messaging_enabled=True, dispatch_enabled=True)
    config["public_gateway"] = settings(state, config)
    state.backend_current = state.stop_budget = True
    monkeypatch.setattr(mesh_gateway_root, "backend_current", lambda value: state.backend_current)
    monkeypatch.setattr(mesh_gateway_root, "shutdown_supported", lambda: state.stop_budget)
    def identity(value):
        assert not opened and "public_gateway" not in value
        assert value["agent_id"] == GATEWAY_KEY and value["identity_file"].endswith("/gateway.key")
        return AgentIdentity.from_seed(bytes(GATEWAY)), os.open(os.devnull, os.O_RDONLY)
    monkeypatch.setattr(mesh_daemon, "load_identity", identity)
    original = mesh_gateway_root.GatewayRoot
    def root(resource, identity):
        return original(resource, identity, backend_check=lambda value: state.backend_current,
            rest_factory=lambda endpoint: LocalPublicREST(endpoint,
                transport=httpx.MockTransport(lambda request: pytest.fail("startup must not submit REST"))))
    monkeypatch.setattr(mesh_gateway_root, "GatewayRoot", root)
    return state, supervisor, resource, config, opened


def test_actual_daemon_starts_explicit_gateway_and_drains(configured):
    state, supervisor, resource, config, opened = configured
    assert resource.invoke("ready", {}, lambda: True) is True
    assert len(opened) == 1 and resource._gateway.role._started
    assert resource._provider._runtime._public_gateway is resource._gateway.role
    gateway = resource._gateway
    assert resource.invoke("ready", {}, lambda: True) is True and resource._gateway is gateway
    supervisor.phase = "draining"
    assert resource.close(5) is True and state.released
    assert gateway.rest._client.is_closed


@pytest.mark.parametrize("failure", ["backend", "stop_budget", "expired"])
def test_missing_evidence_denies_before_usb(configured, failure):
    state, supervisor, resource, config, opened = configured
    if failure == "backend":
        state.backend_current = False
    elif failure == "stop_budget":
        state.stop_budget = False
    else:
        config["public_gateway"]["calibration"].update(issued_at=1, expires_at=2)
    with pytest.raises(Exception):
        resource.invoke("ready", {}, lambda: True)
    assert not opened and resource._uncertain


@pytest.mark.parametrize("field", ["identity_file", "endpoint", "gateway_key", "storage_actor", "lease_seconds", "calibration", "both"])
def test_invalid_configuration_rejected(configured, field):
    state, supervisor, resource, original, opened = configured
    config = copy.deepcopy(original)
    value = config["public_gateway"]
    if field == "identity_file":
        value[field] = config[field]
    elif field == "endpoint":
        value["role"][field] = config[field]
    elif field == "gateway_key":
        value["role"][field] = config["agent_id"]
    elif field == "storage_actor":
        value["role"][field] = IDENTITY.agent_id
    elif field == "lease_seconds":
        value["role"][field] = 56
    elif field == "calibration":
        value[field]["role_digest"] = "0" * 64
    else:
        config["public_mesh"] = {}
    with pytest.raises(Exception):
        mesh_daemon.validate_config(config)


def test_extended_resource_lease_stays_bound_to_broker_and_vault(configured, monkeypatch):
    state, supervisor, resource, config, opened = configured
    assert resource.invoke("ready", {}, lambda: True)
    assert resource._provider.pause_receiving(timeout=5)
    clock = [time.monotonic()]
    monkeypatch.setattr(mesh_daemon, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    lease = resource.gateway_lease(resource._gateway.role._consent(), 55)
    clock[0] += 40
    supervisor.phase = "draining"
    assert lease.check() is True
    old = supervisor.broker_id
    supervisor.broker_id = "c" * 32
    assert lease.check() is False
    supervisor.broker_id = old
    snapshot = resource.probe.snapshot()
    monkeypatch.setattr(resource.probe, "snapshot", lambda: replace(snapshot, vault_generation=snapshot.vault_generation+1))
    assert lease.check() is False
    monkeypatch.setattr(resource.probe, "snapshot", lambda: snapshot)
    clock[0] += 16
    assert lease.check() is False
    lease.close()
    assert lease.check() is False


def test_backend_process_and_listener_pin(tmp_path, monkeypatch):
    proc = tmp_path
    process = proc / "123"
    (process / "fd").mkdir(parents=True)
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "net").mkdir()
    (process / "stat").write_text("123 (public node) " + " ".join(["0"]*19 + ["456"]))
    (proc / "sys/kernel/random/boot_id").write_text("00000000-0000-4000-8000-000000000001")
    (process / "fd/4").symlink_to("socket:[789]")
    (proc / "net/tcp").write_text("header\n0: 0100007F:4A9D 00000000:0000 0A 0 0 0 0 0 789\n")
    value = dict(role=dict(endpoint="http://127.0.0.1:19101"), backend_pin=dict(pid=123,
        start_ticks="456", boot_id="00000000-0000-4000-8000-000000000001", listener_inode="789"))
    assert mesh_gateway_root.backend_process_current(value, proc=proc)
    value["backend_pin"]["start_ticks"] = "457"
    assert mesh_gateway_root.backend_process_current(value, proc=proc) is False
    value["backend_pin"]["start_ticks"] = "456"
    original = os.readlink
    def closed(path):
        result = original(path)
        (proc / "net/tcp").write_text("header\n")
        return result
    monkeypatch.setattr(mesh_gateway_root.os, "readlink", closed)
    assert mesh_gateway_root.backend_process_current(value, proc=proc) is False


@pytest.mark.parametrize("microseconds,expected", [(30_000_000, False), (65_000_000, True), (75_000_000, True), (True, False), (2**64-1, False)])
def test_effective_stop_budget_not_configuration_text(monkeypatch, microseconds, expected):
    import json
    from sage_lantern import suspend_system
    monkeypatch.setattr(suspend_system, "_run", lambda argv, timeout: SimpleNamespace(returncode=0,
        stdout=json.dumps(dict(type="t", data=[microseconds])).encode()))
    assert mesh_gateway_root.shutdown_supported() is expected


@pytest.mark.parametrize("damage", [None, "dispatching", "signature", "binding", "revision"])
def test_preopen_verifies_real_gateway_progress_without_radio(gateway, damage, monkeypatch):
    from sage_lantern.public_gateway_runtime import SIGN_DOMAIN
    from sage_lantern.public_mesh_provider import public_fragments
    from test_public_mesh_protocol import request
    state = gateway
    transport = state.bridge._workflow.storage.client._client._transport
    original = transport.handle_request
    def handler(incoming):
        if incoming.url.path == "/v1/workflows":
            after = incoming.url.params.get("after", "")
            limit = int(incoming.url.params.get("limit", "20"))
            rows = [row for key, row in sorted(state.records.items()) if key > after]
            more = len(rows) > limit
            rows = rows[:limit]
            return httpx.Response(200, json=dict(schema="sage.workflow-journal.v1", items=rows,
                has_more=more, next_after=rows[-1]["record_id"] if more else None))
        return original(incoming)
    monkeypatch.setattr(transport, "handle_request", handler)
    for text in public_fragments(request(now=state.now).wire):
        progress = state.ingest(text)
    role = dict(gateway_key=GATEWAY_KEY, chain_id=state.bridge._chain, domain=state.bridge._domain,
        endpoint=state.bridge._endpoint, storage_actor=state.bridge._workflow.storage.actor.agent_id,
        storage_chain=state.bridge._workflow.storage.actor.chain_id,
        pins=[dict(radio_key=state.pin.radio_key.hex(), author=state.pin.author)])
    identity = AgentIdentity.from_seed(bytes(GATEWAY))
    assert mesh_gateway_root.verify_before_open(state.bridge._workflow, identity, dict(role=role), access_check=state.access)
    if damage is None:
        return
    row = state.records[progress.slot_id]
    body = row["payload"]["body"]
    if damage == "dispatching":
        body["data"]["state"] = "dispatching"
    elif damage == "binding":
        body["binding"]["bridge"]["chain_id"] = "foreign"
    elif damage == "revision":
        body["revision"] += 1
    row["payload"]["signature"] = GATEWAY.sign(SIGN_DOMAIN + canonical(body)).signature.hex()
    if damage == "signature":
        row["payload"]["signature"] = "0" * 128
    sent = len(state.transmits)
    with pytest.raises(Exception):
        mesh_gateway_root.verify_before_open(state.bridge._workflow, identity, dict(role=role), access_check=state.access)
    assert len(state.transmits) == sent


def test_foreign_or_corrupt_gateway_journal_blocks_usb(configured):
    state, supervisor, resource, config, opened = configured
    identifier = "10000000-0000-4000-8000-000000000001"
    state.rows[identifier] = dict(schema="sage.workflow-journal.v1", agent_id=state.identity.agent_id,
        record_id=identifier, revision=1, kind="public_proposal", trust="untrusted_auxiliary",
        payload=dict(body=dict(schema="sage.lantern.public-gateway-runtime.v1"), signature="0"*128))
    with pytest.raises(Exception):
        resource.invoke("ready", {}, lambda: True)
    assert not opened and resource._uncertain and resource.close(1) is False


def test_gateway_grant_rejects_missed_lock_unlock_but_capture_lease_survives(configured):
    state, supervisor, resource, config, opened = configured
    state.access_epoch = "a" * 32
    resource._epoch = lambda: state.access_epoch
    assert resource.invoke("ready", {}, lambda: True)
    assert resource._provider.pause_receiving(timeout=5)
    gateway = resource._gateway
    lease = gateway.acquire(gateway.role._consent())
    assert gateway.unlocked() and lease.check()
    state.access_epoch = "b" * 32
    assert gateway.unlocked() is False
    assert gateway.role.gateway.bridge._access() is False
    assert lease.check() is True
    lease.close()
    assert lease.check() is False
    assert gateway.unlocked() is True
