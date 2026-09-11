"""Synthetic serial and real signed SDK MockTransport; no device/network I/O."""

import base64
from dataclasses import replace
import json
import threading
from types import SimpleNamespace

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient

from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.meshcore_transport import decode_incoming
from sage_lantern.mesh_receiver import MeshReceiver, ReceiverError
from sage_lantern.workflow_store import WorkflowStore


SLOT = "00000000-0000-4000-8000-000000000001"
FRAME = bytes((27, 0, 0, 0, 1, 0, 2, 0, 3)) + b"raw"


def test_interrupted_capture_is_preserved_and_not_replayed(rig):
    from sage_lantern.mesh_receiver import _payload, _validate
    payload = _payload("interrupted", SLOT)
    _validate(payload)
    rig.worker._write(0, payload)
    assert rig.worker.state() == "interrupted"
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()
    assert rig.calls == 0
    assert rig.row["payload"] == payload


@pytest.mark.parametrize("failure", ["before", "after"])
def test_capture_unknown_latches(rig, failure):
    rig.put_failure = ("captured", failure)
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()
    assert rig.worker._uncertain
    assert not rig.worker.close(1)


@pytest.fixture
def rig():
    identity = AgentIdentity(SigningKey(bytes(range(32))))
    fixture = SimpleNamespace(row=None, live=True, storage=True, calls=0, put_failure=None,
                              before_capture=None, driver_failure=False, empty=False, message=None)

    def handler(request):
        assert request.headers["X-Agent-ID"] == identity.agent_id
        assert request.headers["X-Signature"]
        if request.url.path == "/v1/messages/storage":
            return httpx.Response(200, json=dict(schema="sage.message-storage.v1", instance_id="b" * 64,
                agent_id=identity.agent_id, encryption_expected=True, vault_active=True,
                vault_generation="1", stable=True, canonical_send_idempotency=True,
                encrypted_send_admission=True))
        if request.url.path == "/v1/workflows":
            return httpx.Response(200, json=dict(schema="sage.workflow-journal.v1", items=[],
                                                 next_after=None, has_more=False))
        assert request.url.path == "/v1/workflows/" + SLOT
        if request.method == "GET":
            return httpx.Response(200 if fixture.row else 404, json=fixture.row or {})
        body = json.loads(request.content)
        current = fixture.row["revision"] if fixture.row else 0
        state = body["payload"]["state"]
        failure = fixture.put_failure
        if body["expected_revision"] != current or failure == (state, "conflict"):
            return httpx.Response(409, json={"error": "conflict"})
        if failure == (state, "before"):
            raise httpx.ReadTimeout("synthetic")
        fixture.row = dict(schema="sage.workflow-journal.v1", agent_id=identity.agent_id,
            record_id=SLOT, revision=current + 1, kind=body["kind"], payload=body["payload"],
            trust="untrusted_auxiliary")
        if failure == (state, "after"):
            raise httpx.ReadTimeout("synthetic")
        return httpx.Response(200, json=fixture.row)

    endpoint = "http://127.0.0.1:19100"
    client = SageClient(endpoint, identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, timeout=2, trust_env=False,
        follow_redirects=False, transport=httpx.MockTransport(handler))
    probe = MessageStorageProbe(client, actor=RecipientID("home", identity.agent_id), endpoint=endpoint)
    store = WorkflowStore(client, storage=probe)

    class Driver:
        def receive_next(self, *, authorize, persist):
            assert fixture.row["payload"]["state"] == "receiving"
            if not authorize():
                raise RuntimeError()
            fixture.calls += 1
            if fixture.driver_failure:
                raise TimeoutError()
            if fixture.before_capture:
                fixture.before_capture()
            message = None if fixture.empty else fixture.message or decode_incoming(FRAME)
            if message is not None:
                assert persist(message) is True
            if not authorize():
                raise RuntimeError()
            return message

    fixture.new = lambda: MeshReceiver(store, Driver(), slot_id=SLOT,
        access_check=lambda: fixture.live, storage_check=lambda: fixture.storage)
    fixture.worker = fixture.new()
    yield fixture
    client._client.close()


def test_capture_then_durable_consume(rig):
    assert rig.worker.receive_next() == "captured"
    assert base64.b64decode(rig.row["payload"]["raw_frame"]) == FRAME
    operation = rig.row["payload"]["operation_id"]
    seen = []
    assert rig.worker.process_pending(lambda identifier, message: seen.append((identifier, message)) or True)
    assert seen == [(operation, decode_incoming(FRAME))]
    assert rig.worker.state() == "idle"
    assert rig.worker.close(0)


def test_empty_and_noop(rig):
    rig.empty = True
    assert rig.worker.receive_next() == "idle"
    assert rig.worker.process_pending(lambda *args: pytest.fail()) is False


@pytest.mark.parametrize("state,mode", [("idle", "before"), ("receiving", "before"),
    ("receiving", "after"), ("receiving", "conflict")])
def test_predequeue_uncertainty_never_calls_radio(rig, state, mode):
    rig.put_failure = (state, mode)
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()
    assert rig.calls == 0
    assert not rig.worker.close(0)


@pytest.mark.parametrize("mode", ["before", "after", "conflict"])
def test_capture_uncertainty_recovery(rig, mode):
    rig.put_failure = ("captured", mode)
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()
    assert not rig.worker.close(0)
    rig.put_failure = None
    restarted = rig.new()
    assert restarted.state() == ("captured" if mode == "after" else "receiving")
    with pytest.raises(ReceiverError):
        restarted.receive_next()
    assert rig.calls == 1
    assert restarted.close(0) is (mode == "after")


def test_timeout_or_client_death_blocks_new_dequeue(rig):
    rig.driver_failure = True
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()
    restarted = rig.new()
    with pytest.raises(ReceiverError):
        restarted.receive_next()
    with pytest.raises(ReceiverError):
        restarted.process_pending(lambda *args: True)
    assert rig.calls == 1
    assert not restarted.close(0)


def test_lock_after_dequeue_still_protects_capture(rig):
    rig.before_capture = lambda: setattr(rig, "live", False)
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()
    assert rig.row["payload"]["state"] == "captured"
    with pytest.raises(ReceiverError):
        rig.worker.process_pending(lambda *args: pytest.fail("disclosed"))
    assert rig.worker.close(0)


def test_invalid_lifecycle_does_not_claim_persistence(rig):
    rig.before_capture = lambda: setattr(rig, "storage", False)
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()
    assert rig.row["payload"]["state"] == "receiving"
    assert not rig.worker.close(0)


@pytest.mark.parametrize("ack", [False, None, 1, "true"])
def test_handler_requires_literal_durable_true(rig, ack):
    rig.worker.receive_next()
    assert rig.worker.process_pending(lambda *args: ack) is False
    assert rig.row["payload"]["state"] == "captured"


def test_uncertain_handler_replays_same_id(rig):
    rig.worker.receive_next()
    committed = set()
    def consume(identifier, message):
        if identifier not in committed:
            committed.add(identifier)
            raise TimeoutError()
        return True
    with pytest.raises(ReceiverError):
        rig.worker.process_pending(consume)
    assert rig.new().process_pending(consume)
    assert len(committed) == 1 and rig.calls == 1


def test_clear_cas_unknown_does_not_retry_handler_automatically(rig):
    rig.worker.receive_next()
    rig.put_failure = ("idle", "after")
    with pytest.raises(ReceiverError):
        rig.worker.process_pending(lambda *args: True)
    assert not rig.worker.close(0)
    rig.put_failure = None
    assert rig.new().state() == "idle"


@pytest.mark.parametrize("change", [dict(schema="bad"), dict(state="unknown"),
    dict(raw_frame="!!!"), dict(raw_frame=base64.b64encode(b"\x0a").decode()),
    dict(operation_id="bad"), dict(extra=True)])
def test_corrupt_row_fails_closed(rig, change):
    rig.worker.receive_next()
    rig.row["payload"].update(change)
    restarted = rig.new()
    with pytest.raises(ReceiverError):
        restarted.state()
    assert not restarted.close(0)


def test_forged_decoded_metadata_rejected(rig):
    rig.message = replace(decode_incoming(FRAME), read=True)
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()
    assert rig.row["payload"]["state"] == "receiving"


def test_close_fences_admission_joins_capture_without_disclosing(rig):
    entered, release = threading.Event(), threading.Event()
    def pause():
        entered.set()
        assert release.wait(2)
    rig.before_capture = pause
    errors = []
    def run():
        try:
            rig.worker.receive_next()
        except ReceiverError:
            errors.append(True)
    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(2)
    try:
        with pytest.raises(ReceiverError):
            rig.worker.state()
        assert not rig.worker.close(0.01)
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and errors == [True]
    assert rig.worker.close(0)
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()


def test_fresh_close_cannot_certify_unread_slot(rig):
    assert not rig.worker.close(0)


def test_locked_admission_does_not_initialize_or_dequeue(rig):
    rig.live = False
    with pytest.raises(ReceiverError):
        rig.worker.receive_next()
    assert rig.row is None and rig.calls == 0


def test_captured_restart_does_not_dequeue(rig):
    rig.worker.receive_next()
    restarted = rig.new()
    with pytest.raises(ReceiverError):
        restarted.receive_next()
    assert restarted.process_pending(lambda *args: True)
    assert rig.calls == 1


def test_close_from_handler_never_deadlocks(rig):
    rig.worker.receive_next()
    def consume(*args):
        assert not rig.worker.close(0)
        return True
    assert rig.worker.process_pending(consume)
    assert rig.worker.close(0)


def test_close_not_blocked_by_live_guard(rig):
    entered, release = threading.Event(), threading.Event()
    def guard():
        entered.set()
        assert release.wait(2)
        return True
    rig.worker._access = guard
    def run():
        with pytest.raises(ReceiverError):
            rig.worker.receive_next()
    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(2)
    try:
        assert not rig.worker.close(0.01)
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and rig.calls == 0


@pytest.mark.parametrize("timeout", [-1, 61, True, float("nan")])
def test_close_bounds(rig, timeout):
    with pytest.raises(ReceiverError):
        rig.worker.close(timeout)
