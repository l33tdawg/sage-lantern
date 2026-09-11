"""Voice-to-root IPC contract peer; actual protected preparation and mesh queue.

The in-process contract peer is not a deployed daemon or proof that root's
high-level RPC dispatch has landed. Its queue invokes real RootPublicMesh.
"""

from dataclasses import asdict
import hashlib
import json
import struct
import time
import uuid

import pytest

from sage_lantern.community_public import CommunityPublic, PublicProposal, PublicReceipt
from sage_lantern.community_service import CommunityService, CommunityError
from sage_lantern.mesh_community import MeshActionAuthority, MeshRequestAccess
from sage_lantern.mesh_public_root import RootPublicMesh
from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
from sage_lantern.public_mesh_protocol import proposal_data
from sage_lantern.public_mesh_protocol import open_request
from sage_lantern.public_mesh_provider import PUBLIC_BURST, public_fragments
from sage_lantern.mesh_fragments import FragmentAssembler
from test_public_gateway_bridge import fixture
from test_mesh_runtime import factory
from test_public_mesh_protocol import IDENTITY, GATEWAY, GATEWAY_KEY, NOW
from test_meshcore_transport import SerialDouble, frame


@pytest.fixture
def configured(factory):
    state = factory
    state.adopt()
    state.allowed = True
    state.epoch = "b" * 32
    state.authority = MeshActionAuthority()
    state.runtime._authorize_context = state.authority.authorized
    state.source_id = str(uuid.uuid4())
    state.source = dict(question="Synthetic question", answer="Explicit public derivative", has_photo=True)
    state.calls = []
    state.bad_result = False
    state.public_available = True
    state.sources_reply = None
    settings = dict(author=IDENTITY.agent_id, storage_actor=state.identity.agent_id, storage_chain="local",
        chain_id="commons", domain="public-domain", gateway_key=GATEWAY_KEY, radio_key=state.radio.hex(),
        timing=dict(frame_seconds=0.1, gateway_seconds=30, safety_seconds=10))
    root = RootPublicMesh(state.runtime, state.authority, settings, IDENTITY, state.identity,
        active=lambda: state.active, contact_check=lambda pins: state.binding and pins.radio_key == state.radio)
    from test_mesh_public_root import prime_contact
    prime_contact(root, state.runtime, state.authority)
    state.transmits.clear()
    state.root = root
    def protected(operation, callback):
        access = MeshRequestAccess(state.epoch, lambda: state.allowed)
        with state.authority.grant(state.runtime.actor, access, (operation,)) as context:
            return state.runtime._run(context, operation, lambda device: callback())
    state.protected = protected
    def invoke(method, arguments):
        state.calls.append((method, arguments))
        assert state.allowed
        if method == "ready":
            return True
        if method == "public_ready":
            if not state.public_available:
                raise ValueError("synthetic unsupported public method")
            return dict(available=True, reason="ready")
        if method == "public_prepare":
            item = CommunityPublic(None, None, chain_id="commons", domain="public-domain", author=IDENTITY.agent_id).prepare(**arguments)
            state.runtime._public_mesh.preflight(item)
            return proposal_data(item)
        if method == "public_queue":
            assert arguments["consent"] is True and arguments["source_id"] == state.source_id
            from sage_lantern.community_public import PublicProposal
            item = PublicProposal(**arguments["proposal"])
            fingerprint = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()
            def prepare():
                if not root.journal.reserve(arguments["request_id"], fingerprint):
                    previous = root.journal.get(arguments["request_id"])
                    assert previous["fingerprint"] == fingerprint
                    return previous["result"]
                assert root.journal.prepare_public_source(item, arguments["created_at"], arguments["source_id"]) is True
                payload = root.journal._get("sdk", item.operation_id)["payload"]
                root.journal._verify_source(payload)
                return None
            previous = protected("public_prepare", prepare)
            if previous is not None:
                return previous
            result = root.invoke("public_begin", dict(proposal=arguments["proposal"], request_id=arguments["request_id"], receipt=None),
                                 MeshRequestAccess(state.epoch, lambda: state.allowed))
            assert result["state"] == "queued"
            response = dict(id=item.operation_id, state="pending", created_at=arguments["created_at"])
            assert protected("public_prepare", lambda: root.journal.complete(arguments["request_id"], response)) is True
            return dict(response, state="accepted") if state.bad_result else response
        if method == "public_list":
            return {"items": [], "more": False}
        if method == "public_sources":
            return state.sources_reply
        assert method == "close"
        return state.runtime.close(timeout=5)
    class Connection:
        def __init__(self):
            self.raw = bytearray()
            self.pending = None
        def settimeout(self, timeout):
            assert 0 < timeout <= 60
        def reply(self, value):
            raw = json.dumps(value).encode()
            self.raw.extend(len(raw).to_bytes(4, "big") + raw)
        def sendall(self, raw):
            value = json.loads(raw[4:])
            if value.get("op") == "bind":
                self.reply(dict(broker_id="a" * 32, actor=asdict(state.runtime.actor)))
            elif "guard" in value:
                assert value["allowed"] is True and value["guard"] == "c" * 32
                self.reply(dict(result=invoke(self.pending["method"], self.pending["args"])))
            elif value["method"] == "close":
                self.reply(dict(result=invoke("close", {})))
            else:
                assert value["epoch"] == state.epoch
                self.pending = value
                self.reply(dict(guard="c" * 32))
        def recv(self, count):
            result = bytes(self.raw[:count])
            del self.raw[:count]
            return result
        def close(self):
            pass
    facade = MeshVoiceFacade(connector=Connection, epoch_reader=lambda: state.epoch)
    state.service = CommunityService(mesh_provider=facade,
        source_fetch=lambda source: dict(state.source) if source == state.source_id else None)
    state.call = lambda route, body: state.service.dispatch("/community/" + route, body,
        lambda: state.allowed, session_id="synthetic-session")
    state.preview = lambda: state.call("shares/preview", dict(source_id=state.source_id, **state.source))
    yield state
    state.service.close(timeout=5)


def submit(preview):
    return dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=str(uuid.uuid4()), consent=True)


def test_exact_history_source_reaches_root_signed_preparation_and_typed_queue(configured):
    state = configured
    assert state.call("status", {})["sharing"] == dict(available=True, reason="ready")
    preview = state.preview()
    assert preview["photo_included"] is False
    body = submit(preview)
    response = state.call("shares/submit", body)
    assert response["state"] == "pending"
    data = state.rows[body["request_id"]]["payload"]["body"]["data"]
    assert data["state"] == "queued" and not state.transmits
    request = state.runtime._public_mesh._live[body["request_id"]]
    payload = open_request(request.wire, GATEWAY, author=IDENTITY.agent_id,
        chain_id="commons", domain="public-domain", now=request.issued_at)
    assert state.source_id not in json.dumps(payload) and "source_id" not in payload
    assert state.call("shares/submit", body) == response
    assert sum(method == "public_queue" for method, _ in state.calls) == 2


def test_service_reserved_result_allows_dispatch_and_recorded_receipt_poll(configured, fixture, monkeypatch):
    state = configured
    fixture.now = int(time.time())
    transmitted = []
    stream = state.device._stream
    original_write = stream.write

    def write(data, timeout):
        command = data[3:]
        if command[:1] == b"\x02" and command[13:].startswith(b"LP1:"):
            transmitted.append(command)
            ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + state.device._key).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
            return SerialDouble.write(stream, data, timeout)
        return original_write(data, timeout)

    monkeypatch.setattr(stream, "write", write)
    body = submit(state.preview())
    response = state.call("shares/submit", body)
    proposal = PublicProposal(**next(arguments["proposal"] for method, arguments in state.calls
                                    if method == "public_queue"))
    assert state.protected("public_prepare", lambda: state.root.journal.get(body["request_id"]))["result"] == response
    access = MeshRequestAccess(state.epoch, lambda: state.allowed)

    def exchange(request_id):
        start = len(transmitted)
        initial = state.root.invoke("public_inspect", dict(request_id=request_id), access)
        for _ in range((initial["progress"]["fragment_count"] + PUBLIC_BURST - 1) // PUBLIC_BURST):
            with state.authority.grant(state.runtime.actor, access, ("background_dispatch",)) as context:
                state.runtime._run(context, "background_dispatch", state.runtime._dispatch_pending)
        assembler = FragmentAssembler()
        wire = None
        for command in transmitted[start:]:
            wire = assembler.accept(state.device._key, "SL1:" + command[13:].decode()[4:])
        assert wire is not None
        fixture.now = int(time.time())
        sealed = fixture.bridge().handle(wire, author=IDENTITY.agent_id)
        for fragment in public_fragments(sealed):
            state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + struct.pack("<I", NOW) + fragment.encode())
            with state.authority.grant(state.runtime.actor, access, ("receive",)) as context:
                assert state.runtime.poll_once(context) == "captured"
        result = state.root.invoke("public_inspect", dict(request_id=request_id), access)
        assert result["progress"]["state"] == "consumed"
        assert all(result["result"][key] is False for key in
                   ("acceptance_available", "consensus_verified", "cache_eligible"))
        return result["result"]

    result = exchange(body["request_id"])
    assert result["state"] == "receipt"
    receipt = PublicReceipt(**result["receipt"])

    def archive():
        assert state.root.journal.sdk_journal.reserve(proposal.operation_id, proposal.content_hash)
        assert state.root.journal.record_public(proposal, receipt, response["created_at"])

    state.protected("public_prepare", archive)
    poll_id = str(uuid.uuid4())
    queued = state.root.invoke("public_begin", dict(proposal=asdict(proposal), request_id=poll_id,
                                                    receipt=asdict(receipt)), access)
    assert queued["state"] == "queued"
    observed = exchange(poll_id)
    assert observed["state"] == "observed" and observed["observed_status"] == "committed"
    assert sum(method == "POST" for method, _ in fixture.calls) == 1


@pytest.mark.parametrize("change", ["no_consent", "digest", "deleted", "changed", "locked", "new_session"])
def test_missing_or_stale_approval_never_calls_root_queue(configured, change):
    state = configured
    preview = state.preview()
    body = submit(preview)
    if change == "no_consent":
        body["consent"] = False
    elif change == "digest":
        body["digest"] = "0" * 64
    elif change == "deleted":
        state.source_id = str(uuid.uuid4())
    elif change == "changed":
        state.source["answer"] = "Changed after preview"
    elif change == "locked":
        state.allowed = False
    else:
        state.service.lock()
        state.epoch = "d" * 32
    with pytest.raises(CommunityError):
        state.call("shares/submit", body)
    assert not any(method == "public_queue" for method, _ in state.calls) and not state.transmits


def test_gateway_accepted_label_is_rejected(configured):
    state = configured
    state.bad_result = True
    with pytest.raises(CommunityError):
        state.call("shares/submit", submit(state.preview()))


def test_missing_public_rpc_does_not_disable_private_status(configured):
    state = configured
    state.public_available = False
    result = state.call("status", {})
    assert result["messaging"] == dict(available=True, reason="ready")
    assert result["sharing"] == dict(available=False, reason="not_ready")


def test_oversized_complete_derivative_fails_at_preview_without_reservation(configured):
    state = configured
    state.source["answer"] = "x" * 1100
    before = len(state.rows)
    with pytest.raises(CommunityError):
        state.preview()
    assert len(state.rows) == before and not state.service._previews
    assert not any(method == "public_queue" for method, _ in state.calls)


@pytest.mark.parametrize("items", [[], [{"source_id": "wrong", "state": "not_shared"}]])
def test_source_lookup_cannot_fabricate_missing_items(configured, items):
    state = configured
    state.sources_reply = dict(items=items)
    with pytest.raises(CommunityError):
        state.call("shares/status", dict(source_ids=[state.source_id]))
