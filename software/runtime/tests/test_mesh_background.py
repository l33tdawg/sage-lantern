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
from sage_lantern.mesh_outbox import OutboxProgress
from sage_lantern.mesh_runtime import CAPTURE_RESERVE_SECONDS, DISPATCH_BURST
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
    state.revoked = set()
    state.stop_after = None
    def transport(body):
        if body["op"] == "acquire":
            assert state.lease_live and body["epoch"] == "a" * 32
            token = uuid.uuid4().hex + uuid.uuid4().hex
            state.held.add(token)
            return {"lease": token}
        if body["op"] == "check":
            # revoked models an owner session rotating the storage epoch: the
            # leases admitted under the old one stay in the bookkeeping (so they
            # still close cleanly) but no longer authorize anything.
            return {"allowed": state.lease_live and body["lease"] in state.held
                    and body["lease"] not in state.revoked}
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
    # Multi-fragment (the mesh path caps a message's text at 512 bytes, and the
    # fragment transport at 48 bytes per fragment), so the stop_after below leaves
    # this genuinely mid-flight for the restart to pick up.
    state.body["paragraphs"] = ["é" * 200, "Remaining authorized fragments"]
    assert state.call("messages/send", state.body)["state"] == "queued"
    identifier = state.body["request_id"]
    original = dict(state.rows[identifier]["payload"]["body"]["approved"])
    state.stop_after = lambda: bool(state.transmits)
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.provider.pause_receiving(timeout=5) is True
    # A wake sends a burst of fragments, not one (see DISPATCH_BURST).
    assert state.rows[identifier]["payload"]["body"]["next_fragment"] == DISPATCH_BURST
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


def test_unknown_dispatch_inside_the_window_is_retried_by_the_worker(background, monkeypatch):
    """Owner decision 2026-09-13: a lost acknowledgement is retryable, not a park.

    The fragment reached the wire, so the row is left in 'dispatching' with its
    cursor intact; the unit must not sit at not_ready until a human restarts it.
    Inside the approval window the background worker drives the audited
    retry_decision itself and re-sends the *same* fragment.
    """
    state = background
    state.call("messages/send", state.body)
    identifier = state.body["request_id"]
    attempted = []
    delivered = state.device.send_text
    def lost_once(**kwargs):
        attempted.append(kwargs)
        if len(attempted) == 1:
            raise TimeoutError("synthetic serial acknowledgment lost")
        return delivered(**kwargs)
    monkeypatch.setattr(state.device, "send_text", lost_once)
    monkeypatch.setattr("sage_lantern.mesh_runtime.RETRY_BACKOFF_SECONDS", (0.05, 0.1))
    state.start()
    wait_until(lambda: len(attempted) == 1)
    wait_until(lambda: identifier in state.runtime.outbox.unresolved())
    body = state.rows[identifier]["payload"]["body"]
    assert body["state"] == "dispatching" and body["next_fragment"] == 0
    # inside the window this is retryable, so nothing is latched
    assert state.runtime.outbox.latching_unknown() is False
    assert state.runtime.admission_safe() is True
    # the worker itself requeues the row, re-sends the same first fragment and
    # the message keeps making progress without any restart
    wait_until(lambda: len(attempted) >= 2)
    wait_until(lambda: state.rows[identifier]["payload"]["body"]["next_fragment"] >= 1)
    assert state.runtime.outbox.unresolved() == frozenset()
    assert state.runtime.outbox.latching_unknown() is False
    assert state.provider.pause_receiving(timeout=5) is True


def test_timed_out_dispatch_inside_the_window_recovers_without_restart(background, monkeypatch):
    """Unit A's 2026-09-13 failure: the job budget expired with a frame on the wire.

    The abandoned job is fenced, allowed to unwind, and the worker keeps living:
    the row is requeued through the audited retry decision and the message makes
    progress in the same process, with no owner restart and no not_ready park.
    """
    state = background
    # Long enough that the dispatch reaches the wire write, short enough that the
    # stalled acknowledgement cannot outlast the job budget.
    shorten_job_budget(state, 1.5)
    state.call("messages/send", state.body)
    identifier = state.body["request_id"]
    attempted = []
    entered = threading.Event()
    delivered = state.device.send_text
    def stalled_once(**kwargs):
        attempted.append(kwargs)
        if len(attempted) > 1:
            return delivered(**kwargs)
        entered.set()
        while not state.runtime._receive_stop.wait(0.01):
            pass
        raise TimeoutError("synthetic stalled acknowledgment")
    monkeypatch.setattr(state.device, "send_text", stalled_once)
    monkeypatch.setattr("sage_lantern.mesh_runtime.RETRY_BACKOFF_SECONDS", (0.05, 0.1))
    state.start()
    assert entered.wait(5)
    wait_until(lambda: state.rows[identifier]["payload"]["body"]["state"] == "dispatching")
    assert len(attempted) == 1
    # the worker survived the timed-out job instead of parking the unit
    wait_until(lambda: len(attempted) >= 2)
    wait_until(lambda: state.rows[identifier]["payload"]["body"]["next_fragment"] >= 1)
    assert state.runtime._uncertain is False and state.runtime.admission_safe() is True
    assert state.runtime.outbox.unresolved() == frozenset()
    assert state.provider.pause_receiving(timeout=5) is True


def test_unknown_dispatch_outside_the_window_never_retries_and_stays_closed(background, monkeypatch):
    state = background
    state.call("messages/send", state.body)
    identifier = state.body["request_id"]
    attempted = []
    def lost_ack(**kwargs):
        attempted.append(kwargs)
        raise TimeoutError("synthetic serial acknowledgment lost")
    monkeypatch.setattr(state.device, "send_text", lost_ack)
    state.start()
    wait_until(lambda: len(attempted) == 1)
    wait_until(lambda: identifier in state.runtime.outbox.unresolved())
    # the signed window has run out: escalate to the owner instead of retrying
    state.runtime.outbox.clock = lambda: int(time.time()) + 90000
    assert state.runtime.outbox.latching_unknown() is True
    wait_until(lambda: state.runtime.admission_safe() is False)
    assert len(attempted) == 1
    assert state.rows[identifier]["payload"]["body"]["state"] == "dispatching"
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
        served = threading.Event()
        def served_by_this_worker():
            if local_id in state.rows:
                served.set()
            # Do not stop the worker from here: the point is that the *same*
            # thread resumes. A retired worker now clears its own _poller
            # reference, so a stop signal here would race that cleanup instead
            # of proving the resume.
            return False
        state.stop_after = served_by_this_worker
        state.authorized = True
        wait_until(lambda: served.is_set())
        assert state.runtime._poller is poller and local_id in state.rows
        assert not state.runtime._receive_stop.is_set()
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


def test_idle_dispatch_wake_is_skipped_without_opening_a_job(background, monkeypatch):
    """A wake with nothing to dispatch cannot write a frame; it used to open a
    capture lease and hold the serial device for its whole budget (unit B,
    2026-09-19: 10 s, with the owner's own request refused behind it)."""
    state = background
    dispatched = []
    monkeypatch.setattr(state.runtime, "_dispatch_pending", lambda device: dispatched.append(time.monotonic()))
    state.runtime._queued_rows().clear()
    state.runtime._next_scan_at = time.monotonic() + 3600
    state.start()
    time.sleep(0.6)
    assert dispatched == []
    assert state.runtime._poller.is_alive() and not state.runtime._receive_stop.is_set()
    assert state.provider.pause_receiving(timeout=5) is True


def test_idle_dispatch_wake_still_runs_when_the_scan_is_due(background, monkeypatch):
    state = background
    dispatched = threading.Event()
    def dispatch(device):
        dispatched.set()
        state.runtime._next_scan_at = time.monotonic() + 3600
    monkeypatch.setattr(state.runtime, "_dispatch_pending", dispatch)
    state.runtime._queued_rows().clear()
    state.runtime._next_scan_at = 0.0
    state.start()
    assert dispatched.wait(5)
    assert state.runtime._poller.is_alive() and not state.runtime._receive_stop.is_set()
    assert state.provider.pause_receiving(timeout=5) is True


def test_queued_row_keeps_the_dispatch_wake_running(background, monkeypatch):
    state = background
    dispatched = threading.Event()
    def dispatch(device):
        dispatched.set()
        state.runtime._next_scan_at = time.monotonic() + 3600
    monkeypatch.setattr(state.runtime, "_dispatch_pending", dispatch)
    state.runtime._queued_rows().add("1" * 8 + "-0000-4000-8000-" + "1" * 12)
    state.runtime._next_scan_at = time.monotonic() + 3600
    state.start()
    assert dispatched.wait(5)
    assert state.runtime._poller.is_alive()
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


def shorten_job_budget(state, seconds):
    state.runtime.config = replace(state.runtime.config, operation_timeout=seconds)


def test_background_timeout_before_destructive_step_keeps_messaging_usable(background, monkeypatch):
    """A stalled pre-capture check must not cost a cold start.

    The job never reached the destructive dequeue, so nothing was sent or
    consumed: the worker retires, local messaging still answers, and the next
    request restarts the reviewed worker in the same process.
    """
    state = background
    shorten_job_budget(state, 0.4)
    stalled = threading.Event()
    original = state.device.get_contacts

    def stalled_contacts(*, authorize):
        assert authorize() is True
        stalled.set()
        while not state.runtime._receive_stop.wait(0.01):
            pass
        return ()

    monkeypatch.setattr(state.device, "get_contacts", stalled_contacts)
    written = state.device.wire_frames
    state.start()
    assert stalled.wait(5)
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.device.wire_frames == written and not state.transmits
    assert state.runtime._uncertain is False and state.runtime.admission_safe() is True
    assert state.provider.pause_receiving(timeout=5) is True
    assert state.call("messages/list", {"box": "inbox"})["items"] == []
    monkeypatch.setattr(state.device, "get_contacts", original)
    state.start()
    wait_until(lambda: state.finished >= 1)
    assert state.runtime._uncertain is False and state.provider.pause_receiving(timeout=5) is True


def test_background_timeout_after_destructive_step_stays_fail_closed(background, monkeypatch):
    """Reaching the dequeue keeps the previous behaviour, even without a reply."""
    state = background
    shorten_job_budget(state, 0.6)
    entered = threading.Event()

    def stalled_receive(*, authorize, persist):
        entered.set()
        while not state.runtime._receive_stop.wait(0.01):
            pass
        return None

    monkeypatch.setattr(state.device, "get_contacts", lambda *, authorize: ())
    monkeypatch.setattr(state.device, "receive_next", stalled_receive)
    state.start()
    assert entered.wait(5)
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.runtime._uncertain is True and state.runtime.admission_safe() is False
    assert state.provider.pause_receiving(timeout=5) is False
    with pytest.raises(RuntimeError):
        state.start()


def test_dispatch_path_does_not_mark_the_destructive_step_up_front(background, monkeypatch):
    """The dispatch layers own the destructive-action marker, not the worker.

    mesh_outbox.dispatch_one reads the contact inventory before it makes the row
    'dispatching' and sets its own uncertainty only after a fragment actually
    reached the wire. If the worker pre-set the job marker for this path, a stall
    in that read would be classified post-wire and latch the runtime until a
    process restart - which is exactly what happened on unit A on 2026-09-13.
    """
    state = background
    shorten_job_budget(state, 0.4)
    state.call("messages/send", state.body)
    seen = {}

    def stalled_dispatch(device):
        job = getattr(state.runtime._local, "job", None)
        seen["radio_action"] = None if job is None else job.radio_action.is_set()
        raise TimeoutError("synthetic stall in the read-only inventory step")

    monkeypatch.setattr(state.runtime, "_dispatch_pending", stalled_dispatch)
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert seen.get("radio_action") is False
    assert state.runtime._uncertain is False and state.runtime.admission_safe() is True
    assert state.provider.pause_receiving(timeout=5) is True
    assert state.rows[state.body["request_id"]]["payload"]["body"]["state"] == "queued"


def test_background_dispatch_spends_the_inventory_the_wake_refreshed(background, monkeypatch):
    """One fragment, one contact read on the worker path too.

    The worker refreshes the inventory at the top of every wake; the outbox must
    dispatch against that same inventory instead of reading the contact list a
    second time. This is the path that could not deliver on unit A on
    2026-09-13 - a stall in either read ended the wake before any fragment
    reached the wire.
    """
    state = background
    state.call("messages/send", state.body)
    seen = {}
    def capture(message_id, **kwargs):
        seen.update(kwargs)
        seen["refreshed"] = state.runtime._local.inventory
        # A real dispatch returns progress; the burst loop reads it to decide
        # whether this row has another fragment to spend the same inventory on.
        return OutboxProgress(message_id=message_id, revision=2, state="awaiting_receipt",
                              next_fragment=1, fragment_count=1)
    monkeypatch.setattr(state.runtime.outbox, "dispatch_one", capture)
    state.stop_after = lambda: "refreshed" in seen
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert "read_contacts" not in seen
    assert seen["inventory"] is seen["refreshed"]


def test_background_dispatch_bursts_fragments_in_one_wake(background):
    """One wake sends DISPATCH_BURST fragments, not one.

    Wakes alternate with the receive path and sleep `interval` between them, so
    the old one-fragment-per-wake shape made the pacing - two seconds of it - the
    limit for every message longer than a single frame, whatever the radio could
    carry. The public-mesh path in the same file always sent a burst.
    """
    state = background
    # 484 bytes is under the 512-byte mesh-path limit and still ~17 fragments at
    # 48 bytes each, so one wake cannot possibly finish it.
    state.body["paragraphs"] = ["y" * 240, "z" * 240]
    assert state.call("messages/send", state.body)["state"] == "queued"
    identifier = state.body["request_id"]
    state.stop_after = lambda: bool(state.transmits)
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.provider.pause_receiving(timeout=5) is True
    body = state.rows[identifier]["payload"]["body"]
    assert len(state.transmits) == DISPATCH_BURST
    assert body["next_fragment"] == DISPATCH_BURST
    assert body["state"] == "queued"


def test_capture_reserve_is_wired_from_the_job_budget(background, monkeypatch):
    state = background
    seen = []

    def receive(*, authorize, persist):
        seen.append((state.runtime._local.capture_reserve, state.runtime._local.background_deadline))
        return None

    monkeypatch.setattr(state.device, "get_contacts", lambda *, authorize: ())
    monkeypatch.setattr(state.device, "receive_next", receive)
    state.stop_after = lambda: bool(seen)
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.provider.pause_receiving(timeout=5) is True
    assert seen and seen[0][0] == CAPTURE_RESERVE_SECONDS and seen[0][1] > 0


def test_capture_deadline_only_restricts_background_jobs(background):
    state = background
    runtime = state.runtime
    assert runtime._capture_deadline_open() is True
    runtime._local.capture_reserve = 1.0
    runtime._local.background_deadline = time.monotonic() + 0.05
    assert runtime._capture_deadline_open() is False
    runtime._local.background_deadline = time.monotonic() + 5
    assert runtime._capture_deadline_open() is True


def test_rotated_capture_grant_retires_clean_and_restarts_under_the_new_epoch(background, monkeypatch):
    """An owner session rotation must not strand the worker or the queued row.

    Units A and B, 2026-09-18 18:16-18:39: the storage epoch rotated under a
    live dispatch grant, every store call admitted under it was denied
    (``background_dispatch WorkflowUnavailable access_denied
    authorize_context``), the wake retired the worker, and nothing
    authenticated again inside the process - the row stayed queued and the
    owner's own send was answered 503. Retiring must leave no stale worker or
    pause state behind, and the restart path the daemon supervisor drives must
    re-admit under the new epoch without any request joining the old worker.
    """
    state = background
    assert state.call("messages/send", state.body)["state"] == "queued"
    identifier = state.body["request_id"]
    entered = threading.Event()
    release = threading.Event()
    original = state.runtime._inventory
    def gated(device, authorize, *, fresh=0.0):
        # Hold the dispatch wake - and only that wake - once it is admitted and
        # before it can touch the store, so the rotation below lands on a job
        # that holds the old grant.
        if getattr(state.runtime._local, "operation", None) == "background_dispatch":
            entered.set()
            assert release.wait(5)
        return original(device, authorize, fresh=fresh)
    monkeypatch.setattr(state.runtime, "_inventory", gated)
    state.start()
    assert entered.wait(5)
    # The rotation happens while the wake is admitted and on the radio: the
    # lease it holds is revoked, while the next job may still authenticate.
    assert state.held
    state.revoked.update(state.held)
    release.set()
    retired = state.runtime._poller
    wait_until(lambda: not retired.is_alive())
    assert state.runtime._receive_pausing is False
    assert state.runtime._uncertain is False and state.runtime.admission_safe() is True
    assert state.rows[identifier]["payload"]["body"]["state"] == "queued" and not state.transmits
    # The daemon supervisor's path, off every request: a fresh grant under the
    # current epoch, and the row it was carrying reaches the wire.
    assert state.provider.restart_receiving(consent_reader=lambda: state.consent,
        lease_factory=state.lease_factory, unlocked_check=lambda: state.authorized) is True
    assert state.runtime._poller is not retired and state.runtime._poller.is_alive()
    wait_until(lambda: bool(state.transmits))
    assert state.rows[identifier]["payload"]["body"]["state"] != "queued"
    assert state.provider.pause_receiving(timeout=5) is True


def test_restart_receiving_refuses_a_latched_runtime(background):
    """A latched runtime must never be re-admitted by the supervisor.

    The restart path exists for a rotated capture epoch, not for an unknown
    write: when any target reports a latching unknown, admission is unsafe, a
    retried restart refuses outright, and no replacement worker starts.
    """
    state = background
    state.start()
    wait_until(lambda: state.runtime._poller.is_alive())
    running = state.runtime._poller
    state.runtime.receiver._uncertain = True
    assert state.runtime.admission_safe() is False
    # A worker that is already running is left exactly where it is: its own
    # access checks deny every job from here, and no replacement is started.
    assert state.provider.restart_receiving(consent_reader=lambda: state.consent,
        lease_factory=state.lease_factory, unlocked_check=lambda: state.authorized) is True
    assert state.runtime._poller is running
    # Once it has retired on its own, the same call refuses to re-admit it.
    wait_until(lambda: not running.is_alive())
    assert state.provider.restart_receiving(consent_reader=lambda: state.consent,
        lease_factory=state.lease_factory, unlocked_check=lambda: state.authorized) is False
    assert state.runtime._poller is running and not running.is_alive()
