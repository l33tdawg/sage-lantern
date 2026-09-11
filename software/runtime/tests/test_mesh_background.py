"""Synthetic supervisor transport and serial; real lease client, SDK and crypto."""

from dataclasses import replace
import copy
import hashlib
import json
import struct
import threading
import time
import uuid

import pytest
import httpx
from nacl.signing import SigningKey, VerifyKey
from sage_sdk import SageClient

from sage_lantern.community_service import CommunityError, CommunityMeshProvider, CommunityService
from sage_lantern.mesh_community import MeshReceiveConsent
from sage_lantern.mesh_envelope import Peer, seal_message
from sage_lantern.mesh_fragments import split_envelope
from sage_lantern.mesh_inbox import inbox_id
from sage_lantern.mesh_inbox import MeshInbox
from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.mesh_receipt_sender import MeshReceiptSender, ReceiptPermit
from sage_lantern.meshcore_transport import MeshCoreClient, decode_incoming
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.workflow_store import WorkflowStore
from sage_lantern.mesh_serial_owner import SerialScheduler
from sage_lantern.storage_supervisor_socket import CaptureLease
from test_community_mesh_runtime import integrated, factory
from test_meshcore_transport import SerialDouble, handshake_bytes, frame
from test_meshcore_contacts import start, contact, end
import test_mesh_runtime as runtime_fixture


KEY = bytes(SigningKey(b"s" * 32).verify_key)


@pytest.fixture(autouse=True)
def valid_sender_radio(monkeypatch):
    def radio_client(incoming, chunk=1):
        stream = SerialDouble(incoming, chunk)
        return MeshCoreClient(stream, expected_public_key=KEY), stream
    monkeypatch.setattr(runtime_fixture, "KEY", KEY)
    monkeypatch.setattr(runtime_fixture, "radio_client", radio_client)
    monkeypatch.setattr(runtime_fixture, "handshake_bytes", lambda: handshake_bytes(key=KEY))


def wait_until(check, *, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("synthetic worker did not reach expected state")


@pytest.fixture
def background(integrated):
    state = integrated
    state.consent = MeshReceiveConsent(state.runtime.actor, "b" * 64, True, True, True, True)
    state.held = set()
    state.finished = 0
    state.lease_live = True
    state.stop_after = None
    def transport(body):
        if body["op"] == "acquire":
            assert state.lease_live and body["epoch"] == "a" * 32
            token = uuid.uuid4().hex + uuid.uuid4().hex
            state.held.add(token)
            return {"lease": token}
        if body["op"] == "check":
            return {"allowed": state.lease_live and body["lease"] in state.held}
        assert body["op"] == "finish"
        state.held.remove(body["lease"])
        state.finished += 1
        if state.stop_after is not None and state.stop_after():
            state.runtime._receive_stop.set()
        return {"finished": True}
    def lease(consent):
        assert consent == state.consent
        return CaptureLease("a" * 32, transport=transport)
    state.lease_factory = lease
    state.start = lambda: state.provider.start_receiving(consent_reader=lambda: state.consent,
        lease_factory=lease, unlocked_check=lambda: state.authorized, interval=0.2)
    yield state
    state.provider.pause_receiving(timeout=5)


def incoming(state, text="Synthetic private inbox"):
    now = int(time.time())
    identifier = str(uuid.uuid4())
    sender = Peer("remote", bytes.fromhex(state.remote.agent_id))
    envelope = seal_message(state.remote._signing_key, sender=sender,
        recipient=Peer("local", bytes.fromhex(state.identity.agent_id)), message_id=identifier,
        content=text, issued_at=now, expires_at=now + 900)
    for fragment in split_envelope(envelope.wire.encode()):
        state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + struct.pack("<I", now) + fragment.encode())
    return inbox_id(sender, identifier)


@pytest.fixture
def recipient(background):
    state = background
    rows = {}
    def handler(request):
        canonical = request.method.encode() + b" " + request.url.raw_path + b"\n" + request.content
        signed = hashlib.sha256(canonical).digest() + struct.pack(">q", int(request.headers["x-timestamp"])) + bytes.fromhex(request.headers["x-nonce"])
        VerifyKey(bytes.fromhex(state.remote.agent_id)).verify(signed, bytes.fromhex(request.headers["x-signature"]))
        assert request.headers["x-agent-id"] == state.remote.agent_id
        if request.url.path == "/v1/messages/storage":
            return httpx.Response(200, json=dict(schema="sage.message-storage.v1", agent_id=state.remote.agent_id,
                instance_id="e" * 64, vault_generation="1", vault_active=state.lease_live, encryption_expected=True,
                stable=True, canonical_send_idempotency=True, encrypted_send_admission=True))
        if request.url.path == "/v1/workflows":
            assert request.method == "GET"
            limit = int(request.url.params.get("limit", "20"))
            after = request.url.params.get("after", "")
            selected = [rows[identifier] for identifier in sorted(rows) if identifier > after]
            more = len(selected) > limit
            selected = selected[:limit]
            return httpx.Response(200, json=dict(schema="sage.workflow-journal.v1", items=selected,
                has_more=more, next_after=selected[-1]["record_id"] if more else None))
        identifier = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(200 if identifier in rows else 404, json=rows.get(identifier, {}))
        body = json.loads(request.content)
        revision = rows.get(identifier, {}).get("revision", 0)
        if body["expected_revision"] != revision:
            return httpx.Response(409, json={})
        rows[identifier] = dict(schema="sage.workflow-journal.v1", agent_id=state.remote.agent_id,
            record_id=identifier, revision=revision + 1, kind=body["kind"], payload=copy.deepcopy(body["payload"]),
            trust="untrusted_auxiliary")
        return httpx.Response(200, json=rows[identifier])
    endpoint = "http://127.0.0.1:19101"
    client = SageClient(endpoint, state.remote, timeout=5, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, transport=httpx.MockTransport(handler), trust_env=False, timeout=5)
    peer = Peer("remote", bytes.fromhex(state.remote.agent_id))
    sender = Peer("local", bytes.fromhex(state.identity.agent_id))
    store = WorkflowStore(client, storage=MessageStorageProbe(client, actor=RecipientID("remote", state.remote.agent_id), endpoint=endpoint))
    inbox = MeshInbox(store, recipient=peer, recipient_signing_key=state.remote._signing_key,
        resolve_peer=lambda message: sender if message.sender_prefix == KEY[:6] else None,
        access_check=lambda: state.lease_live, storage_check=lambda: state.lease_live)
    yield rows, store, inbox, peer, sender
    client._client.close()


def test_locked_receive_persists_without_disclosure_or_receipt(background):
    state = background
    local_id = incoming(state)
    state.authorized = False
    state.service.lock()
    state.stop_after = lambda: local_id in state.rows
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.provider.pause_receiving(timeout=5) is True
    assert local_id in state.rows and not state.transmits and not state.held
    with pytest.raises(CommunityError):
        state.call("messages/list", {"box": "inbox"})
    state.authorized = True
    assert state.call("messages/list", {"box": "inbox"})["items"][0]["text"] == "Synthetic private inbox"
    assert state.service.close(timeout=5) is True


def test_simulated_background_roundtrip_reconstructs_bytes_and_receipt_after_restart(background, recipient):
    state = background
    state.body["paragraphs"] = ["é" * 200, "Remaining authorized fragments"]
    assert state.call("messages/send", state.body)["state"] == "queued"
    identifier = state.body["request_id"]
    original = dict(state.rows[identifier]["payload"]["body"]["approved"])
    state.stop_after = lambda: len(state.transmits) == 1
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.provider.pause_receiving(timeout=5) is True
    assert state.rows[identifier]["payload"]["body"]["next_fragment"] == 1
    assert state.service.close(timeout=5) is True
    state.scheduler = SerialScheduler(state.device, release=lambda: setattr(state, "released", True))
    state.provider = CommunityMeshProvider.from_config(state.config, coordinator=state.scheduler,
        activation_check=state.arguments["activation_check"], verify_binding=state.arguments["verify_binding"],
        storage_lifecycle_check=state.arguments["storage_lifecycle_check"])
    state.runtime = state.provider._runtime
    state.service = CommunityService(mesh_provider=state.provider)
    state.stop_after = lambda: state.rows[identifier]["payload"]["body"]["state"] == "awaiting_receipt"
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set(), timeout=len(split_envelope(original["wire"].encode())) * 2)
    assert state.provider.pause_receiving(timeout=5) is True
    final = state.rows[identifier]["payload"]["body"]
    assert final["approved"] == original and final["message_id"] == identifier
    assert final["next_fragment"] == len(state.transmits) > 1
    assert not state.held and state.call("messages/list", {"box": "outbox"})["items"][0]["state"] == "sent"
    rows, store, inbox, peer, sender = recipient
    for command in state.transmits:
        assert command[:3] == b"\x02\x00\x00" and command[7:13] == state.radio[:6]
        message = decode_incoming(b"\x07" + KEY[:6] + bytes(2) + command[3:7] + command[13:])
        admitted = inbox.ingest(str(uuid.uuid4()), message)
    assert admitted.status == "admitted"
    original_message = inbox.historical_read(admitted.record_id)
    expected_bytes = "\n\n".join(state.body["paragraphs"]).encode("utf-8")
    assert original_message.content.encode("utf-8") == expected_bytes
    assert original_message.message_id == identifier and original_message.digest == original["content_digest"]
    assert rows[admitted.record_id]["payload"]["wire"] == original["wire"]

    now = int(time.time())
    approved_card = verify_contact_card(sign_contact_card(state.identity, chain_id="local", radio_public_key=KEY.hex(),
        display_name="Synthetic sender", revision=1, issued_at=now - 1, expires_at=now + 900, now=now,
        findable=True, messaging_allowed=True), now=now)
    def policy(action):
        assert state.lease_live and action.kind == "delivery" and action.local_id == admitted.record_id
        assert action.original_sender == sender and action.original_id == identifier
        assert action.original_digest == original_message.digest and action.radio_public_key == KEY
        return ReceiptPermit(action, "synthetic-approved-durable-delivery")
    receipts = MeshReceiptSender(store, state.remote, inbox=inbox, sender=peer,
        guard=lambda action: state.lease_live, policy=policy, verify_contact=lambda action: approved_card)
    progress = receipts.queue(local_id=admitted.record_id, kind="delivery", radio_public_key=KEY,
        issued_at=now, expires_at=now + 900, access_check=lambda: state.lease_live)
    stream = SerialDouble(handshake_bytes(key=state.radio), chunk=10000)
    device = MeshCoreClient(stream, expected_public_key=state.radio)
    device.handshake()
    original_write = stream.write
    returned = []
    def write(data, timeout):
        command = data[3:]
        if command == b"\x04":
            stream.incoming.extend(frame(start(1)) + frame(contact(KEY)) + frame(end(123)))
        elif command[:1] == b"\x02":
            assert rows[progress.receipt_id]["payload"]["body"]["state"] == "dispatching"
            returned.append(command)
            ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + state.radio).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
        return original_write(data, timeout)
    stream.write = write
    for _ in range(progress.fragment_count):
        progress = receipts.dispatch_one(progress.receipt_id, device=device, radio_timestamp=now,
                                         access_check=lambda: state.lease_live)
    assert progress.state == "serial_accepted" and receipts.close(timeout=1) is True
    for command in returned:
        assert command[7:13] == KEY[:6]
        state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + command[3:7] + command[13:])
    state.stop_after = lambda: state.rows[identifier]["payload"]["body"]["state"] == "delivered"
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set(), timeout=len(returned) * 2)
    assert state.provider.pause_receiving(timeout=5) is True
    assert state.call("messages/list", {"box": "outbox"})["items"][0]["state"] == "delivered"
    assert state.rows[identifier]["payload"]["body"]["approved"] == original
    assert state.service.close(timeout=5) is True


def test_uncertain_dispatch_is_never_automatically_retried(background, monkeypatch):
    state = background
    state.call("messages/send", state.body)
    attempted = []
    def lost_ack(**kwargs):
        attempted.append(kwargs)
        raise TimeoutError("synthetic serial acknowledgment lost")
    monkeypatch.setattr(state.device, "send_text", lost_ack)
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.provider.pause_receiving(timeout=5) is False
    assert len(attempted) == 1
    assert state.rows[state.body["request_id"]]["payload"]["body"]["state"] == "dispatching"
    state.stop_after = lambda: state.finished >= 4
    with pytest.raises(RuntimeError):
        state.start()
    assert len(attempted) == 1
    assert state.service.close(timeout=5) is False


@pytest.mark.parametrize("change", ["disabled", "malformed", "locked", "revision"])
def test_policy_failure_never_dequeues(background, change):
    state = background
    incoming(state)
    state.authorized = False
    if change == "disabled":
        state.consent = replace(state.consent, messaging_enabled=False)
    elif change == "malformed":
        state.consent = replace(state.consent, messaging_enabled=1)
    elif change == "locked":
        state.consent = replace(state.consent, receive_while_locked=False, dispatch_while_locked=False)
    else:
        state.consent = replace(state.consent, revision="bad")
    count = len(state.pending)
    with pytest.raises(ValueError):
        state.start()
    assert len(state.pending) == count and not state.held


def test_capture_retains_lease_through_pause_and_policy_change(background, monkeypatch):
    state = background
    incoming(state)
    entered, release = threading.Event(), threading.Event()
    original = state.device.receive_next
    def receive(*, authorize, persist):
        def capture(message):
            entered.set()
            assert release.wait(5)
            return persist(message)
        return original(authorize=authorize, persist=capture)
    monkeypatch.setattr(state.device, "receive_next", receive)
    state.start()
    assert entered.wait(5)
    state.consent = replace(state.consent, revision="c" * 64, messaging_enabled=False)
    assert state.provider.pause_receiving(timeout=0.02) is False
    assert state.held
    release.set()
    assert state.provider.pause_receiving(timeout=5) is True
    assert not state.held and state.rows[state.config.receiver_slot]["payload"]["state"] == "captured"
    with pytest.raises(ValueError):
        state.start()
    assert state.service.close(timeout=5) is True


def test_no_boolean_lease_substitution(background):
    state = background
    incoming(state)
    count = len(state.pending)
    state.provider.start_receiving(consent_reader=lambda: state.consent,
        lease_factory=lambda consent: True, unlocked_check=lambda: state.authorized, interval=0.2)
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.provider.pause_receiving(timeout=5) is True
    assert len(state.pending) == count and not state.transmits


@pytest.mark.parametrize("resume", [True, False])
def test_unlocked_only_worker_waits_on_lock_and_joins_or_resumes(background, monkeypatch, resume):
    state = background
    state.consent = replace(state.consent, receive_while_locked=False, dispatch_enabled=False,
                            dispatch_while_locked=False)
    waiting = threading.Event()
    original = state.runtime.start_receiving
    def start_receiving(grant_factory, operations, **kwargs):
        def observed():
            choices = operations()
            if not choices:
                waiting.set()
            return choices
        return original(grant_factory, observed, **kwargs)
    monkeypatch.setattr(state.runtime, "start_receiving", start_receiving)
    def lock_after_job():
        state.authorized = False
        return False
    state.stop_after = lock_after_job
    state.start()
    assert waiting.wait(5)
    poller = state.runtime._poller
    completed = state.finished
    local_id = incoming(state)
    count = len(state.pending)
    waiting.clear()
    assert waiting.wait(2)
    assert poller.is_alive() and not state.runtime._receive_stop.is_set()
    assert state.finished == completed and not state.held
    assert len(state.pending) == count and local_id not in state.rows and not state.transmits
    if resume:
        state.stop_after = lambda: local_id in state.rows
        state.authorized = True
        wait_until(lambda: state.runtime._receive_stop.is_set())
        assert state.runtime._poller is poller and local_id in state.rows
        assert state.finished > completed and not state.held and not state.transmits
    assert state.provider.pause_receiving(timeout=5) is True
    assert not poller.is_alive() and state.service.close(timeout=5) is True


def test_lock_while_serial_job_queued_does_not_acquire_lease(background, monkeypatch):
    state = background
    state.consent = replace(state.consent, receive_while_locked=False, dispatch_enabled=False,
                            dispatch_while_locked=False)
    submitted = threading.Event()
    original = state.runtime._submit
    def submit(callback):
        state.authorized = False
        submitted.set()
        return original(callback)
    monkeypatch.setattr(state.runtime, "_submit", submit)
    state.start()
    assert submitted.wait(2)
    state.scheduler.submit(lambda device: None).result(5)
    assert state.runtime._poller.is_alive() and not state.runtime._receive_stop.is_set()
    assert not state.held and state.finished == 0 and not state.transmits
    assert state.provider.pause_receiving(timeout=5) is True


@pytest.mark.parametrize("choices", [None, [], ("unknown",), ("receive", "receive")])
def test_malformed_background_operations_still_stop(background, choices):
    state = background
    state.runtime.start_receiving(lambda operation: pytest.fail("must not acquire"), lambda: choices, interval=0.2)
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.finished == 0 and not state.held and not state.transmits
    assert state.provider.pause_receiving(timeout=5) is True


def test_mailbox_fence_wait_does_not_kill_worker(background, monkeypatch):
    state = background
    attempted = threading.Event()
    original = state.runtime._submit
    def submit(operation):
        attempted.set()
        return original(operation)
    monkeypatch.setattr(state.runtime, "_submit", submit)
    with state.runtime._fence() as lease:
        state.start()
        assert attempted.wait(2)
        assert lease() is True and state.runtime._poller.is_alive()
        assert not state.held
    wait_until(lambda: state.finished >= 1)
    assert not state.runtime._receive_stop.is_set()
    assert state.provider.pause_receiving(timeout=5) is True


@pytest.mark.parametrize("operation", ["acquire", "finish"])
def test_unknown_supervisor_lease_outcome_blocks_clean_close(background, operation):
    state = background
    def lease(consent):
        if operation == "acquire":
            raise TimeoutError("synthetic acquire acknowledgment lost")
        result = state.lease_factory(consent)
        def close():
            raise TimeoutError("synthetic finish acknowledgment lost")
        result.close = close
        return result
    state.provider.start_receiving(consent_reader=lambda: state.consent, lease_factory=lease,
        unlocked_check=lambda: state.authorized, interval=0.2)
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.provider.pause_receiving(timeout=5) is False
    assert state.service.close(timeout=5) is False
