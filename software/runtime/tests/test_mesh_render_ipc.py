"""Real root IPC, protected workflows and domain receipts; no native IO."""

import copy
import json
import struct
import threading
import time
import uuid

import httpx
import pytest

from sage_lantern.mesh_daemon import MeshDaemon
from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
from sage_lantern.mesh_envelope import Peer, seal_message
from sage_lantern.mesh_fragments import split_envelope
from sage_lantern.mesh_read_receipts import SCHEMA
from test_mesh_daemon import runtime, factory, socket_connector


@pytest.fixture
def rendered(runtime):
    state, supervisor, resource, config, opened = runtime
    now = int(time.time())
    sealed = seal_message(state.remote._signing_key,
        sender=Peer("remote", bytes.fromhex(state.remote.agent_id)), recipient=Peer("local", bytes.fromhex(state.identity.agent_id)),
        message_id=str(uuid.uuid4()), content="Synthetic explicitly rendered text", issued_at=now, expires_at=now + 900)
    for text in split_envelope(sealed.wire.encode()):
        state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + struct.pack("<I", now) + text.encode())
        assert state.runtime.poll_once(state.context) == "captured"
    state.epoch = "e" * 32
    epoch = lambda: state.epoch
    resource._epoch = epoch
    connector, workers = socket_connector(MeshDaemon(supervisor, resource, epoch_reader=epoch))
    facade = MeshVoiceFacade(connector=connector, epoch_reader=epoch)
    item = facade.list_messages("inbox", access_check=lambda: True).items[0]
    yield state, supervisor, resource, facade, item
    for worker in workers:
        worker.join(timeout=3)
        assert not worker.is_alive()


def begin(state):
    return state[3].render_challenge(state[4].id, rendered=True, access_check=lambda: True)


def acknowledge(state, challenge, **changes):
    arguments = dict(challenge_id=challenge["challenge_id"], rendered=True, share_read_receipt=True,
                     access_check=lambda: True, on_submit=lambda: None)
    arguments.update(changes)
    return state[3].rendered(state[4].id, **arguments)


def test_challenge_is_read_only_ack_queues_and_drain_verifies(rendered):
    state, supervisor, resource, facade, item = rendered
    before = copy.deepcopy(state.rows)
    assert facade.open_message(item.id, access_check=lambda: True).id == item.id
    challenge = begin(rendered)
    assert challenge["id"] == item.id and state.rows == before and not state.transmits
    result = acknowledge(rendered, challenge)
    assert set(result) == {"id", "receipt_id", "state"} and result["id"] == item.id and result["state"] == "queued"
    consent = next(row for row in state.rows.values() if row["payload"].get("body", {}).get("schema") == SCHEMA)
    assert consent["payload"]["body"]["session"]["epoch"] == state.epoch
    assert "Synthetic explicitly" not in json.dumps(consent)
    assert not state.transmits
    supervisor.phase = "draining"
    assert resource.close(5) is True and state.released


@pytest.mark.parametrize("change", ["epoch", "message", "challenge", "consent", "rendered", "expired"])
def test_wrong_message_epoch_challenge_or_consent_denied(rendered, change):
    state, supervisor, resource, facade, item = rendered
    challenge = begin(rendered)
    before = copy.deepcopy(state.rows)
    options = {}
    if change == "epoch":
        state.epoch = "f" * 32
    elif change == "message":
        item_id = item.id[:-1] + ("0" if item.id[-1] != "0" else "1")
        with pytest.raises(RuntimeError):
            facade.rendered(item_id, challenge_id=challenge["challenge_id"], rendered=True, share_read_receipt=True,
                            access_check=lambda: True, on_submit=lambda: None)
        assert state.rows == before
        return
    elif change == "challenge":
        options["challenge_id"] = str(uuid.uuid4())
    elif change == "consent":
        options["share_read_receipt"] = 1
    elif change == "rendered":
        options["rendered"] = False
    else:
        resource._read_receipts.helper._clock = lambda: challenge["expires_at"]
    with pytest.raises((RuntimeError, ValueError)):
        acknowledge(rendered, challenge, **options)
    assert state.rows == before and not state.transmits


def test_duplicate_ack_cannot_replace_approval(rendered):
    challenge = begin(rendered)
    acknowledge(rendered, challenge)
    before = copy.deepcopy(rendered[0].rows)
    with pytest.raises(RuntimeError):
        acknowledge(rendered, challenge)
    assert rendered[0].rows == before


def test_lost_consent_write_ack_retains_owner_drain_failure(rendered):
    state, supervisor, resource, facade, item = rendered
    challenge = begin(rendered)
    client = resource._provider._runtime.store.client._client
    original = client._transport
    def lost(request):
        result = original.handle_request(request)
        if request.method == "PUT" and json.loads(request.content).get("payload", {}).get("body", {}).get("schema") == SCHEMA:
            raise httpx.ReadTimeout("synthetic persisted consent, reply lost")
        return result
    client._transport = httpx.MockTransport(lost)
    with pytest.raises(RuntimeError):
        acknowledge(rendered, challenge)
    assert any(row["payload"].get("body", {}).get("schema") == SCHEMA for row in state.rows.values())
    supervisor.phase = "draining"
    assert resource.close(0.2) is False and not state.released and not client.is_closed


def test_tampered_consent_blocks_drain(rendered):
    challenge = begin(rendered)
    acknowledge(rendered, challenge)
    state, supervisor, resource, facade, item = rendered
    consent = next(row for row in state.rows.values() if row["payload"].get("body", {}).get("schema") == SCHEMA)
    consent["payload"]["body"]["local_id"] = str(uuid.uuid4())
    supervisor.phase = "draining"
    assert resource.close(0.5) is False and not state.released


def test_close_cannot_release_while_consent_write_active(rendered):
    state, supervisor, resource, facade, item = rendered
    challenge = begin(rendered)
    client = resource._provider._runtime.store.client._client
    original = client._transport
    entered, release = threading.Event(), threading.Event()
    results = []
    def blocked(request):
        result = original.handle_request(request)
        if request.method == "PUT" and json.loads(request.content).get("payload", {}).get("body", {}).get("schema") == SCHEMA:
            entered.set()
            assert release.wait(timeout=3)
        return result
    client._transport = httpx.MockTransport(blocked)
    worker = threading.Thread(target=lambda: results.append(acknowledge(rendered, challenge)))
    worker.start()
    try:
        assert entered.wait(timeout=2)
        assert resource.close(0.02) is False and not state.released and not client.is_closed
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive() and results[0]["state"] == "queued"
    supervisor.phase = "draining"
    assert resource.close(5) is True


def test_lock_after_persist_denies_response_and_clean_drain(rendered):
    state, supervisor, resource, facade, item = rendered
    challenge = begin(rendered)
    client = resource._provider._runtime.store.client._client
    original = client._transport
    def lock(request):
        result = original.handle_request(request)
        if request.method == "PUT" and json.loads(request.content).get("payload", {}).get("body", {}).get("schema") == SCHEMA:
            state.epoch = None
        return result
    client._transport = httpx.MockTransport(lock)
    with pytest.raises(RuntimeError):
        acknowledge(rendered, challenge)
    assert any(row["payload"].get("body", {}).get("schema") == SCHEMA for row in state.rows.values())
    supervisor.phase = "draining"
    assert resource.close(0.1) is False and not client.is_closed and not state.transmits


def test_real_receipt_worker_dispatches_with_root_epoch_not_closed_http_guard(rendered):
    state, supervisor, resource, facade, item = rendered
    result = acknowledge(rendered, begin(rendered))
    resource.config.update(messaging_enabled=True, dispatch_enabled=True)
    resource.resume_background(lambda: True)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        row = state.rows.get(result["receipt_id"])
        if row is not None and row["payload"]["body"]["state"] == "serial_accepted":
            break
        if resource._provider._runtime._poller is not None and not resource._provider._runtime._poller.is_alive():
            pytest.fail("receipt worker stopped before serial acceptance")
        time.sleep(0.1)
    else:
        pytest.fail("bounded real receipt scheduler did not finish")
    assert state.transmits
    assert resource._provider.pause_receiving(timeout=5) is True
    runtime = resource._provider._runtime
    assert runtime.receipts._uncertain is False
    assert runtime.outbox._uncertain is False
    assert runtime.receiver._known in ("idle", "captured")
    supervisor.phase = "draining"
    assert resource.close(5) is True


@pytest.mark.parametrize("revoke", [False, True])
def test_pause_joins_progress_verification_without_bypassing_lock(rendered, monkeypatch, revoke):
    state, supervisor, resource, facade, item = rendered
    result = acknowledge(rendered, begin(rendered))
    runtime = resource._provider._runtime
    entered, release = threading.Event(), threading.Event()
    original = runtime.store.put
    def put(identifier, kind, revision, payload, *, access_check):
        row = original(identifier, kind, revision, payload, access_check=access_check)
        body = payload.get("body", {})
        if identifier == result["receipt_id"] and body.get("state") == "queued":
            entered.set()
            assert release.wait(5)
            if access_check() is not True:
                raise RuntimeError("synthetic revoked response verification")
        return row
    monkeypatch.setattr(runtime.store, "put", put)
    resource.config.update(messaging_enabled=True, dispatch_enabled=True)
    resource.resume_background(lambda: True)
    assert entered.wait(5)
    assert resource._provider.pause_receiving(timeout=0.02) is False
    assert not state.released and runtime._poller.is_alive()
    if revoke:
        state.epoch = None
    release.set()
    assert resource._provider.pause_receiving(timeout=5) is (not revoke)
    assert runtime.receipts._uncertain is revoke
    if revoke:
        state.epoch = "e" * 32
        with pytest.raises(RuntimeError):
            resource.invoke("ready", {}, lambda: True)
    supervisor.phase = "draining"
    assert resource.close(5) is (not revoke)
    assert state.released is (not revoke)


def test_locked_epoch_never_dispatches_queued_read_even_with_generic_locked_policy(rendered):
    state, supervisor, resource, facade, item = rendered
    acknowledge(rendered, begin(rendered))
    state.epoch = None
    resource.config.update(messaging_enabled=True, dispatch_enabled=True, receive_while_locked=True, dispatch_while_locked=True)
    resource.resume_background(lambda: True)
    time.sleep(2.5)
    assert not state.transmits
    assert resource._provider.pause_receiving(timeout=5) is True
