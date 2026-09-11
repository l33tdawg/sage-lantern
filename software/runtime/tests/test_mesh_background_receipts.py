"""Actual root receipt policy and supervisor worker; simulated serial/platform."""

import time

import pytest

from sage_lantern.mesh_community import MeshRequestAccess
from sage_lantern.mesh_envelope import Peer, open_envelope
from sage_lantern.mesh_fragments import FragmentAssembler
from sage_lantern.mesh_inbox import inbox_id
from sage_lantern.mesh_read_root import RootReadReceipts
from sage_lantern.community_service import CommunityService
from sage_lantern.mesh_daemon import MeshDaemon
from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
from test_mesh_background import background, incoming, wait_until
from test_community_mesh_runtime import integrated
from test_mesh_runtime import factory
from test_mesh_daemon import runtime as daemon_runtime, socket_connector


@pytest.fixture
def pending_receipt(background):
    state = background
    state.http_live = True
    state.root_epoch = "a" * 32
    state.root_session = "c" * 64
    access = MeshRequestAccess(state.root_epoch, lambda: state.http_live)
    incoming(state, "Synthetic rendered private message")
    with state.provider._authority.grant(state.runtime.actor, access, ("receive",)) as context:
        while state.pending:
            state.runtime.poll_once(context)
    root = RootReadReceipts(state.provider, state.identity, active=lambda: state.active)
    state.receipt_root = root
    item = state.provider.list_messages("inbox", access_check=access).items[0]
    binding = (state.root_epoch, state.root_session)
    state.background_permission = True
    state.permission_checks = []
    def background_check():
        state.permission_checks.append(state.http_live)
        return (state.background_permission and state.active and state.authorized
                and (state.root_epoch, state.root_session) == binding)
    challenge = root.invoke("render_challenge", dict(id=item.id, rendered=True), access, binding)
    result = root.invoke("rendered", dict(id=item.id, challenge_id=challenge["challenge_id"],
        rendered=True, share_read_receipt=True), access, binding, background_check=background_check)
    state.receipt_id = result["receipt_id"]
    assert result["state"] == "queued" and not root._contexts and not state.transmits
    state.http_live = False
    state.original = item
    yield state
    root.close(timeout=1)


def test_worker_sends_consented_receipt_after_http_guard_closed(pending_receipt):
    state = pending_receipt
    state.stop_after = lambda: state.rows[state.receipt_id]["payload"]["body"]["state"] == "serial_accepted"
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set(), timeout=20)
    assert state.provider.pause_receiving(timeout=5) is True
    body = state.rows[state.receipt_id]["payload"]["body"]
    assert body["state"] == "serial_accepted" and len(state.transmits) == body["next_fragment"] > 1
    assembler = FragmentAssembler()
    for command in state.transmits:
        wire = assembler.accept(state.device._key, command[13:].decode())
    opened = open_envelope(wire.decode(), state.remote._signing_key,
        expected_sender=Peer("local", bytes.fromhex(state.identity.agent_id)),
        expected_recipient=Peer("remote", bytes.fromhex(state.remote.agent_id)), now=int(time.time()))
    assert opened.kind == "read" and opened.original_id == state.original.message_id
    assert False in state.permission_checks and not state.receipt_root._contexts
    assert not state.held and state.service.close(timeout=5) is True


@pytest.mark.parametrize("revocation", ["relock", "new_unlock", "session", "contact", "root_permission", "root_close"])
def test_receipt_requires_current_root_session_and_contact(pending_receipt, revocation):
    state = pending_receipt
    if revocation == "relock":
        state.root_epoch = None
    elif revocation == "new_unlock":
        state.root_epoch = "b" * 32
    elif revocation == "session":
        state.root_session = "d" * 64
    elif revocation == "root_permission":
        state.background_permission = False
    elif revocation == "root_close":
        assert state.receipt_root.close(timeout=1)
    else:
        state.binding = False
    state.stop_after = lambda: state.finished >= 3
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    state.provider.pause_receiving(timeout=5)
    assert not state.transmits and state.rows[state.receipt_id]["payload"]["body"]["state"] == "queued"
    state.binding = True


def test_uncertain_receipt_dispatch_is_not_retried(pending_receipt, monkeypatch):
    state = pending_receipt
    attempted = []
    def unknown(**kwargs):
        attempted.append(kwargs)
        raise TimeoutError("synthetic lost serial acknowledgment")
    monkeypatch.setattr(state.device, "send_text", unknown)
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    state.provider.pause_receiving(timeout=5)
    assert len(attempted) == 1 and state.rows[state.receipt_id]["payload"]["body"]["state"] == "dispatching"
    finished = state.finished
    state.stop_after = lambda: state.finished >= finished + 3
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    state.provider.pause_receiving(timeout=5)
    assert len(attempted) == 1 and state.service.close(timeout=5) is False


def test_root_permission_is_rechecked_between_receipt_fragments(pending_receipt, monkeypatch):
    state = pending_receipt
    dispatch = state.runtime.receipts.dispatch_one
    def revoke_after_fragment(*args, **kwargs):
        progress = dispatch(*args, **kwargs)
        state.background_permission = False
        finished = state.finished
        state.stop_after = lambda: state.finished >= finished + 4
        return progress
    monkeypatch.setattr(state.runtime.receipts, "dispatch_one", revoke_after_fragment)
    state.start()
    wait_until(lambda: state.runtime._receive_stop.is_set())
    assert state.provider.pause_receiving(timeout=5)
    body = state.rows[state.receipt_id]["payload"]["body"]
    assert body["state"] == "queued" and body["next_fragment"] == 1
    assert len(state.transmits) == 1


def test_service_daemon_root_worker_delivers_receipt_without_live_http_guard(daemon_runtime):
    state, supervisor, resource, config, opened = daemon_runtime
    original_id = incoming(state, "Synthetic end-to-end rendered text")
    while state.pending:
        assert state.runtime.poll_once(state.context) == "captured"
    config.update(messaging_enabled=True, dispatch_enabled=True)
    epoch = "e" * 32
    daemon = MeshDaemon(supervisor, resource, epoch_reader=lambda: epoch)
    connector, workers = socket_connector(daemon)
    facade = MeshVoiceFacade(connector=connector, epoch_reader=lambda: epoch)
    service = CommunityService(mesh_provider=facade)
    http_live = True
    def call(method, body):
        return service.dispatch("/community/messages/" + method, body,
                                lambda: http_live, session_id="synthetic-ui-session")
    try:
        items = call("list", dict(box="inbox"))["items"]
        assert len(items) == 1
        identifier = items[0]["id"]
        challenge = call("render-challenge", dict(id=identifier, rendered=True))
        result = call("rendered", dict(id=identifier, challenge_id=challenge["challenge_id"],
                                       rendered=True, share_read_receipt=True))
        assert result["state"] == "queued"
        http_live = False
        for worker in workers:
            worker.join(3)
            assert not worker.is_alive()
        assert not resource._read_receipts._contexts
        identifier = result["receipt_id"]
        wait_until(lambda: state.rows[identifier]["payload"]["body"]["state"] == "serial_accepted", timeout=90)
        resource._coordinator.submit(lambda device: None).result(timeout=5)
        assert resource._provider.pause_receiving(timeout=5)
        assembler = FragmentAssembler()
        wire = None
        for command in state.transmits:
            wire = assembler.accept(state.device._key, command[13:].decode())
        assert wire is not None
        opened_receipt = open_envelope(wire.decode(), state.remote._signing_key,
            expected_sender=Peer("local", bytes.fromhex(state.identity.agent_id)),
            expected_recipient=Peer("remote", bytes.fromhex(state.remote.agent_id)), now=int(time.time()))
        assert opened_receipt.kind == "read"
        assert inbox_id(Peer("remote", bytes.fromhex(state.remote.agent_id)), opened_receipt.original_id) == original_id
        assert len(opened) == 1
        supervisor.phase = "draining"
        assert service.close(timeout=5)
        poller = resource._provider._runtime._poller
        assert poller is None or not poller.is_alive()
    finally:
        supervisor.phase = "draining"
        service.close(timeout=5)
        for worker in workers:
            worker.join(3)
            assert not worker.is_alive()


def test_root_drain_verifies_queued_receipt_and_consent(daemon_runtime):
    state, supervisor, resource, config, opened = daemon_runtime
    incoming(state)
    while state.pending:
        state.runtime.poll_once(state.context)
    allowed = True
    access = lambda: allowed
    item = resource.invoke("list_messages", dict(box="inbox"), access)["items"][0]
    binding = ("e" * 32, "c" * 64)
    challenge = resource.invoke("render_challenge", dict(id=item["id"], rendered=True), access,
                                request_binding=binding)
    result = resource.invoke("rendered", dict(id=item["id"], challenge_id=challenge["challenge_id"],
        rendered=True, share_read_receipt=True), access, request_binding=binding,
        background_check=lambda: supervisor.phase == "active")
    assert result["state"] == "queued" and not state.transmits
    allowed = False
    supervisor.phase = "draining"
    assert resource.close(5)
