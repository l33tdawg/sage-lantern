import copy
import hashlib
import json
import time

import pytest

from sage_lantern import mesh_daemon as module
from sage_lantern.mesh_contacts import sign_contact_card
from test_mesh_daemon import runtime
from test_mesh_runtime import factory


def request_for(resource, card):
    return dict(op="import-contact", broker_id=resource.supervisor.broker_id, epoch="e" * 32,
                config_sha256=hashlib.sha256(json.dumps(resource.config, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                card_sha256=hashlib.sha256(card).hexdigest(), card=card.decode())


def invoke(resource, request, epoch="e" * 32):
    return resource.import_contact(request, broker_id=resource.supervisor.broker_id, epoch_reader=lambda: epoch)


def test_empty_real_registry_import_then_list_without_transmit(runtime):
    state, supervisor, resource, config, opened = runtime
    state.rows.clear()
    assert resource.invoke("ready", {}, lambda: True)
    assert resource.invoke("contacts", {}, lambda: True) == []
    request = request_for(resource, state.card)
    assert invoke(resource, request) == dict(card_sha256=request["card_sha256"], revision=1,
                                            findable=True, messaging_allowed=True)
    assert len(resource.invoke("contacts", {}, lambda: True)) == 1
    assert not state.transmits and len(opened) == 1
    assert config["messaging_enabled"] is False and config["dispatch_enabled"] is False


@pytest.mark.parametrize("change", ["broker", "epoch", "config", "digest", "signature", "peer", "extra", "inactive", "uninitialized", "expired", "oversized", "changed_file"])
def test_import_denied_without_writes_or_new_radio_open(runtime, change):
    state, supervisor, resource, config, opened = runtime
    if change != "uninitialized":
        assert resource.invoke("ready", {}, lambda: True)
    request = request_for(resource, state.card)
    epoch = "e" * 32
    if change == "broker":
        request["broker_id"] = "c" * 32
    elif change == "epoch":
        epoch = None
    elif change == "config":
        request["config_sha256"] = "0" * 64
    elif change == "digest":
        request["card_sha256"] = "0" * 64
    elif change == "signature":
        request["card"] = request["card"].replace("Remote", "Forged")
        request["card_sha256"] = hashlib.sha256(request["card"].encode()).hexdigest()
    elif change == "peer":
        config["peers"] = []
        request = request_for(resource, state.card)
    elif change == "extra":
        request["approve"] = True
    elif change == "inactive":
        supervisor.phase = "draining"
    elif change == "expired":
        now = int(time.time()) - 2000
        card = sign_contact_card(state.remote, chain_id="remote", radio_public_key=state.radio.hex(),
            display_name="Remote", revision=2, issued_at=now, expires_at=now+1000, now=now,
            findable=True, messaging_allowed=True)
        request = request_for(resource, card)
    elif change == "oversized":
        request = request_for(resource, b" " * 4097)
    elif change == "changed_file":
        resource._config_reader = lambda: {**config, "dispatch_enabled": True}
    before = copy.deepcopy(state.rows)
    opens = len(opened)
    with pytest.raises(Exception):
        invoke(resource, request, epoch)
    assert state.rows == before and not state.transmits and len(opened) == opens


def test_signed_revocation_replaces_consent_not_history_and_rejects_old_card(runtime):
    state, supervisor, resource, config, opened = runtime
    assert resource.invoke("ready", {}, lambda: True)
    before = set(state.rows)
    now = int(time.time())
    card = sign_contact_card(state.remote, chain_id="remote", radio_public_key=state.radio.hex(),
        display_name="Remote", revision=2, issued_at=now, expires_at=now+1000, now=now,
        findable=False, messaging_allowed=False)
    result = invoke(resource, request_for(resource, card))
    assert result["revision"] == 2 and result["messaging_allowed"] is False
    assert resource.invoke("contacts", {}, lambda: True) == []
    assert set(state.rows) == before
    with pytest.raises(Exception):
        invoke(resource, request_for(resource, state.card))
    assert not state.transmits


def test_epoch_revocation_on_worker_prevents_registry_write(runtime, monkeypatch):
    state, supervisor, resource, config, opened = runtime
    assert resource.invoke("ready", {}, lambda: True)
    request = request_for(resource, state.card)
    current = ["e" * 32]
    original = resource._provider._runtime.put_contact
    def revoked(*args, **kwargs):
        current[0] = None
        return original(*args, **kwargs)
    monkeypatch.setattr(resource._provider._runtime, "put_contact", revoked)
    before = copy.deepcopy(state.rows)
    with pytest.raises(Exception):
        resource.import_contact(request, broker_id=supervisor.broker_id, epoch_reader=lambda: current[0])
    assert state.rows == before and not state.transmits


@pytest.mark.parametrize("uid", [0, 1001])
def test_daemon_route_requires_kernel_root_uid(runtime, monkeypatch, uid):
    state, supervisor, resource, config, opened = runtime
    assert resource.invoke("ready", {}, lambda: True)
    request = request_for(resource, state.card)
    sent = []
    monkeypatch.setattr(module, "receive", lambda connection: request)
    monkeypatch.setattr(module, "send", lambda connection, value: sent.append(value))
    daemon = module.MeshDaemon(supervisor, resource, epoch_reader=lambda: "e" * 32)
    if uid == 0:
        daemon.handle(object(), pid=901, uid=uid)
        assert sent[0]["result"]["card_sha256"] == request["card_sha256"]
    else:
        with pytest.raises(module.MeshIPCError):
            daemon.handle(object(), pid=901, uid=uid)
        assert sent == []
