import copy
import time

import pytest

from sage_lantern.mesh_daemon import validate_config
from sage_lantern.community_service import CommunityMeshProvider
from sage_lantern.mesh_community import MeshActionAuthority
from test_mesh_daemon import configuration, runtime
from test_mesh_runtime import factory
from test_mesh_background import incoming, valid_sender_radio
from test_mesh_delivery_receipts import receipt_rows


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_delivery_flag_requires_exact_boolean(factory, value):
    config = configuration(factory)
    config.update(messaging_enabled=True, dispatch_enabled=True, delivery_receipts_enabled=value)
    with pytest.raises(Exception):
        validate_config(config)


@pytest.mark.parametrize("messaging,dispatch", [(False, False), (True, False), (False, True)])
def test_delivery_opt_in_requires_both_workers(factory, messaging, dispatch):
    config = configuration(factory)
    config.update(messaging_enabled=messaging, dispatch_enabled=dispatch, delivery_receipts_enabled=True)
    with pytest.raises(Exception):
        validate_config(config)


@pytest.mark.parametrize("flag", [None, False, True])
def test_root_forwarding_and_legacy_config_not_rewritten(runtime, flag):
    state, supervisor, resource, config, opened = runtime
    config.update(messaging_enabled=True, dispatch_enabled=True)
    if flag is not None:
        config["delivery_receipts_enabled"] = flag
    original = copy.deepcopy(config)
    validate_config(config)
    consent = resource.consent()
    assert consent.delivery_receipts_enabled is (flag is True)
    assert config == original and not opened
    previous = consent.revision
    config["delivery_receipts_enabled"] = not (flag is True)
    validate_config(config)
    assert resource.consent().revision != previous


@pytest.mark.parametrize("key", ["delivery_receipt_enabled", "read_receipts_enabled", "delivery_consent"])
def test_no_unknown_or_read_consent_config_escape(factory, key):
    config = configuration(factory)
    config[key] = True
    with pytest.raises(Exception):
        validate_config(config)


@pytest.mark.parametrize("receive_locked,dispatch_locked", [(False, False), (True, False), (False, True), (True, True)])
def test_root_forwarding_does_not_expand_locked_dispatch(runtime, receive_locked, dispatch_locked):
    state, supervisor, resource, config, opened = runtime
    config.update(messaging_enabled=True, dispatch_enabled=True, delivery_receipts_enabled=True,
                  receive_while_locked=receive_locked, dispatch_while_locked=dispatch_locked)
    validate_config(config)
    consent = resource.consent()
    assert MeshActionAuthority.receive_allowed(consent, resource.actor, "receive", lambda: False) is receive_locked
    assert MeshActionAuthority.receive_allowed(consent, resource.actor, "background_dispatch", lambda: False) is dispatch_locked
    assert not MeshActionAuthority.receive_allowed(consent, resource.actor, "read", lambda: True)


@pytest.mark.parametrize("flag", [None, False, True])
def test_actual_root_provider_queues_only_explicit_delivery_consent(runtime, flag, monkeypatch):
    state, supervisor, resource, config, opened = runtime
    start = CommunityMeshProvider.start_receiving
    def faster_poll(provider, **kwargs):
        return start(provider, **kwargs, interval=0.2)
    monkeypatch.setattr(CommunityMeshProvider, "start_receiving", faster_poll)
    config.update(messaging_enabled=True, dispatch_enabled=True)
    if flag is not None:
        config["delivery_receipts_enabled"] = flag
    validate_config(config)
    local_id = incoming(state)
    assert resource.invoke("ready", {}, lambda: True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if local_id in state.rows and state.rows[config["receiver_slot"]]["payload"]["state"] == "idle":
            break
        assert resource._provider._runtime._poller.is_alive()
        time.sleep(0.01)
    else:
        pytest.fail("bounded root receiver did not finish")
    assert resource._provider.pause_receiving(timeout=5)
    rows = receipt_rows(state)
    assert len(rows) == int(flag is True)
    if rows:
        _, approved = resource._provider._runtime.receipts._verified(rows[0])
        assert approved["local_id"] == local_id and approved["kind"] == "delivery"
        assert approved["render_ack_binding"] == ""
        assert resource._read_receipts._pending == {}
        assert resource._read_receipts._challenges == {}
