"""Factory integration with real SDK, coordinator, crypto and synthetic serial."""

from collections import deque
import copy
import hashlib
import json
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace
import uuid

import httpx
import pytest
from nacl.signing import VerifyKey, SigningKey
from sage_sdk import AgentIdentity, SageClient

import sage_lantern.mesh_runtime as module
from sage_lantern.mesh_contacts import sign_contact_card
from sage_lantern.mesh_envelope import Peer, seal_message
from sage_lantern.mesh_fragments import split_envelope
from sage_lantern.mesh_inbox import inbox_id
from sage_lantern.mesh_receipt_sender import ReceiptPermit
from sage_lantern.mesh_runtime import MeshRuntime, MeshRuntimeConfig, MeshUserContext, MeshRuntimeError
from sage_lantern.mesh_serial_owner import SerialScheduler
from sage_lantern.message_contacts import RecipientID
from test_meshcore_transport import client as radio_client, handshake_bytes, frame, KEY
from test_meshcore_contacts import start, contact, end


@pytest.fixture
def factory(monkeypatch):
    identity=AgentIdentity.from_seed(b"a"*32)
    remote=AgentIdentity.from_seed(b"b"*32)
    radio=bytes(SigningKey(b"r"*32).verify_key)
    state=SimpleNamespace(active=True,authorized=True,binding=True,storage=True,rows={},requests=[],transmits=[],pending=deque(),released=False)
    device,stream=radio_client(handshake_bytes(),chunk=10000)
    device.handshake()
    original_write=stream.write
    def write(data,timeout):
        command=data[3:]
        if command==b"\x04":
            stream.incoming.extend(frame(start(1))+frame(contact(radio))+frame(end(123)))
        elif command==b"\x0a":
            stream.incoming.extend(frame(state.pending.popleft() if state.pending else b"\x0a"))
        elif command[:1]==b"\x02":
            assert any(row["payload"].get("body",{}).get("state")=="dispatching" for row in state.rows.values())
            state.transmits.append(command)
            ack=hashlib.sha256(command[3:7]+b"\0"+command[13:]+KEY).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00"+ack+struct.pack("<I",100)))
        return original_write(data,timeout)
    stream.write=write
    scheduler=SerialScheduler(device,release=lambda:setattr(state,"released",True))
    def handler(request):
        canonical=request.method.encode()+b" "+request.url.raw_path+b"\n"+request.content
        signed=hashlib.sha256(canonical).digest()+struct.pack(">q",int(request.headers["x-timestamp"]))+bytes.fromhex(request.headers["x-nonce"])
        VerifyKey(bytes.fromhex(identity.agent_id)).verify(signed,bytes.fromhex(request.headers["x-signature"]))
        state.requests.append((request.method,request.url.path))
        if request.url.path=="/v1/messages/storage":
            return httpx.Response(200,json=dict(schema="sage.message-storage.v1",agent_id=identity.agent_id,instance_id="d"*64,
                vault_generation="1",vault_active=state.storage,encryption_expected=True,stable=True,canonical_send_idempotency=True,encrypted_send_admission=True))
        if request.url.path=="/v1/workflows":
            after=request.url.params.get("after","")
            limit=int(request.url.params.get("limit",20))
            rows=[row for key,row in sorted(state.rows.items()) if key>after]
            more=len(rows)>limit
            rows=rows[:limit]
            return httpx.Response(200,json=dict(schema="sage.workflow-journal.v1",items=rows,has_more=more,next_after=rows[-1]["record_id"] if more else None))
        identifier=request.url.path.rsplit("/",1)[-1]
        if request.method=="GET":
            return httpx.Response(200 if identifier in state.rows else 404,json=state.rows.get(identifier,{}))
        body=json.loads(request.content)
        revision=state.rows.get(identifier,{}).get("revision",0)
        if revision!=body["expected_revision"]:
            return httpx.Response(409,json={})
        row=dict(schema="sage.workflow-journal.v1",agent_id=identity.agent_id,record_id=identifier,revision=revision+1,
                 kind=body["kind"],payload=body["payload"],trust="untrusted_auxiliary")
        state.rows[identifier]=copy.deepcopy(row)
        return httpx.Response(200,json=row)
    clients=[]
    def sdk(*args,**kwargs):
        client=SageClient(*args,**kwargs)
        client._client.close()
        client._client=httpx.Client(base_url=client._base_url,transport=httpx.MockTransport(handler),trust_env=False,timeout=5)
        clients.append(client)
        return client
    monkeypatch.setattr(module,"SageClient",sdk)
    monkeypatch.setattr(AgentIdentity,"from_file",lambda path:identity)
    config=MeshRuntimeConfig("http://127.0.0.1:19100","local",Path("/synthetic/identity"),str(uuid.uuid4()))
    actor=RecipientID("local",identity.agent_id)
    context=MeshUserContext(actor,"a"*32,"explicit-approval","render-ack")
    def policy(context,action):
        assert context.actor==actor and state.authorized
        return ReceiptPermit(action,context.approval_binding,context.render_ack_binding if action.kind=="read" else "")
    arguments=dict(coordinator=scheduler,activation_check=lambda:state.active,
                   authorize_context=lambda context,operation,action:state.authorized and context.epoch=="a"*32,
                   verify_binding=lambda owner,card,key:state.binding and owner==actor and card.recipient==RecipientID("remote",remote.agent_id) and key==radio,
                   receipt_policy=policy,storage_lifecycle_check=lambda:state.storage)
    state.config,state.context,state.arguments=config,context,arguments
    state.scheduler,state.device,state.identity,state.remote,state.radio=scheduler,device,identity,remote,radio
    state.make=lambda **changes:MeshRuntime.from_config(config,**{**arguments,**changes})
    state.runtime=state.make()
    now=int(time.time())
    state.card=sign_contact_card(remote,chain_id="remote",radio_public_key=radio.hex(),display_name="Remote",
        revision=1,issued_at=now-1,expires_at=now+1000,now=now,findable=True,messaging_allowed=True)
    state.adopt=lambda:state.runtime.put_contact(context,state.card,known_actor=RecipientID("remote",remote.agent_id))
    yield state
    state.runtime.close(timeout=1)
    scheduler.close(timeout=1)
    for client in clients: client.__exit__(None,None,None)


def test_real_factory_queue_dispatch_mailbox_and_close(factory):
    state=factory
    state.adopt()
    now=int(time.time())
    identifier=str(uuid.uuid4())
    result=state.runtime.queue_message(state.context,message_id=identifier,recipient=Peer("remote",bytes.fromhex(state.remote.agent_id)),
        radio_public_key=state.radio,content="Synthetic private message",consent_binding="contact-policy",issued_at=now,expires_at=now+900)
    assert result.state=="queued" and not state.transmits
    result=state.runtime.dispatch_one(state.context,identifier,radio_timestamp=now)
    assert result.next_fragment==1 and len(state.transmits)==1
    page=state.runtime.list_messages(state.context,"outbox")
    assert len(page.items)==1 and page.items[0].text=="Synthetic private message"
    assert state.runtime.open_message(state.context,page.items[0].id).message_id==identifier
    assert state.runtime.close(timeout=5) is True and state.released
    assert state.runtime.client._client.is_closed


def test_one_an_outbox_read_seeds_the_dispatch_set(factory):
    """An outbox read marks its queued rows for the wake, without a journal scan.

    Unit A, 2026-09-21: the dispatch wake finds rows from an in-memory set, and
    a row queued by an earlier process was otherwise only discoverable by a
    journal scan that costs more than the whole 10 s wake - so the message never
    left the queue. The owner looking at the outbox is the read that fixes it.
    """
    state=factory
    state.adopt()
    now=int(time.time())
    identifier=str(uuid.uuid4())
    state.runtime.queue_message(state.context,message_id=identifier,recipient=Peer("remote",bytes.fromhex(state.remote.agent_id)),
        radio_public_key=state.radio,content="Synthetic private message",consent_binding="contact-policy",issued_at=now,expires_at=now+900)
    state.runtime._queued_rows().discard(identifier)
    page=state.runtime.list_messages(state.context,"outbox")
    assert [item.message_id for item in page.items]==[identifier]
    assert identifier in state.runtime._queued_rows()
    assert state.runtime.close(timeout=5) is True


def test_one_fragment_costs_one_contact_read(factory, monkeypatch):
    """The fenced dispatch hands the row the inventory this operation refreshed.

    Every operation refreshes the contact list once; the outbox used to read it
    again for its own prefix lookup, so a single fragment paid two reads. At unit
    A's measured stall rate (2026-09-13 16:19-16:22, about one read in three)
    that is what made a multi-fragment message unreachable.
    """
    state=factory
    state.adopt()
    now=int(time.time())
    identifier=str(uuid.uuid4())
    state.runtime.queue_message(state.context,message_id=identifier,recipient=Peer("remote",bytes.fromhex(state.remote.agent_id)),
        radio_public_key=state.radio,content="Synthetic private message",consent_binding="contact-policy",issued_at=now,expires_at=now+900)
    reads=[]
    original=state.device.get_contacts
    def counted(*,authorize):
        reads.append(authorize)
        return original(authorize=authorize)
    monkeypatch.setattr(state.device,"get_contacts",counted)
    result=state.runtime.dispatch_one(state.context,identifier,radio_timestamp=now)
    assert result.next_fragment==1 and len(state.transmits)==1
    assert len(reads)==1


def test_real_receive_and_explicit_read_receipt(factory):
    state=factory
    state.adopt()
    now=int(time.time())
    identifier=str(uuid.uuid4())
    sender=Peer("remote",bytes.fromhex(state.remote.agent_id))
    peer=Peer("local",bytes.fromhex(state.identity.agent_id))
    envelope=seal_message(state.remote._signing_key,sender=sender,recipient=peer,message_id=identifier,
                          content="Synthetic incoming",issued_at=now,expires_at=now+900)
    for text in split_envelope(envelope.wire.encode()):
        state.pending.append(b"\x07"+state.radio[:6]+bytes(2)+struct.pack("<I",now)+text.encode())
        assert state.runtime.poll_once(state.context)=="captured"
    page=state.runtime.list_messages(state.context,"inbox")
    assert len(page.items)==1 and not state.transmits
    receipt=state.runtime.queue_receipt(state.context,local_id=inbox_id(sender,identifier),kind="read",radio_public_key=state.radio,
                                         issued_at=now,expires_at=now+900)
    assert not state.transmits
    progress=state.runtime.dispatch_one(state.context,receipt.receipt_id,receipt=True,radio_timestamp=now)
    assert progress.next_fragment==1 and len(state.transmits)==1


@pytest.mark.parametrize("revocation", ["lock", "stop"])
def test_dequeued_capture_survives_revocation(factory, monkeypatch, revocation):
    state = factory
    state.adopt()
    now = int(time.time())
    envelope = seal_message(state.remote._signing_key,
        sender=Peer("remote", bytes.fromhex(state.remote.agent_id)),
        recipient=Peer("local", bytes.fromhex(state.identity.agent_id)),
        message_id=str(uuid.uuid4()), content="Synthetic capture",
        issued_at=now, expires_at=now + 900)
    fragment = split_envelope(envelope.wire.encode())[0]
    state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + struct.pack("<I", now) + fragment.encode())
    original = state.device.receive_next

    def receive(*, authorize, persist):
        def capture(message):
            if revocation == "lock":
                state.authorized = False
            else:
                state.runtime._stop.set()
            return persist(message)
        return original(authorize=authorize, persist=capture)

    monkeypatch.setattr(state.device, "receive_next", receive)
    with pytest.raises(MeshRuntimeError):
        state.runtime.poll_once(state.context)
    assert state.rows[state.config.receiver_slot]["payload"]["state"] == "captured"
    assert not state.transmits
    assert state.runtime.close(timeout=1) is False


def test_drain_deadline_rechecked_after_storage_callback(factory, monkeypatch):
    runtime = factory.runtime
    runtime._local.mode = "drain"
    runtime._local.lease = lambda: True
    runtime._local.deadline = 20
    times = iter([19, 21])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    try:
        assert runtime._access() is False
    finally:
        runtime._local.__dict__.clear()
        monkeypatch.undo()


def test_queued_initialization_timeout_cancels_without_writes(factory):
    entered, release = threading.Event(), threading.Event()
    future = factory.scheduler.submit(lambda device: (entered.set(), release.wait(timeout=3)))
    assert entered.wait(timeout=1)
    config = module.MeshRuntimeConfig(factory.config.endpoint, factory.config.chain_id,
        factory.config.identity_path, str(uuid.uuid4()), operation_timeout=0.02)
    before = copy.deepcopy(factory.rows)
    try:
        with pytest.raises(MeshRuntimeError):
            MeshRuntime.from_config(config, **factory.arguments)
    finally:
        release.set()
        future.result(timeout=2)
    factory.scheduler.submit(lambda device: None).result(timeout=2)
    assert factory.rows == before


def test_started_initialization_timeout_denies_late_write(factory, monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    config = module.MeshRuntimeConfig(factory.config.endpoint, factory.config.chain_id,
        factory.config.identity_path, str(uuid.uuid4()), operation_timeout=0.05)
    original = module.WorkflowStore.get

    def get(store, identifier, **kwargs):
        if identifier != config.receiver_slot:
            return original(store, identifier, **kwargs)
        entered.set()
        try:
            assert release.wait(timeout=3)
            return original(store, identifier, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(module.WorkflowStore, "get", get)
    before = copy.deepcopy(factory.rows)
    try:
        with pytest.raises(MeshRuntimeError):
            MeshRuntime.from_config(config, **factory.arguments)
        assert entered.is_set() and not finished.is_set()
    finally:
        release.set()
    factory.scheduler.submit(lambda device: None).result(timeout=2)
    assert finished.is_set() and factory.rows == before


@pytest.mark.parametrize("missing",["coordinator","activation_check","authorize_context","verify_binding","receipt_policy","storage_lifecycle_check"])
def test_missing_dependency_rejected(factory,missing):
    before=len(factory.requests)
    with pytest.raises(MeshRuntimeError): factory.make(**{missing:None})
    assert len(factory.requests)==before


def test_root_activation_off_no_new_io(factory):
    factory.active=False
    before=len(factory.requests)
    with pytest.raises(MeshRuntimeError): factory.make()
    assert len(factory.requests)==before
    with pytest.raises(MeshRuntimeError): factory.runtime.poll_once(factory.context)
    assert not factory.transmits


def test_current_auth_and_independent_binding_denial(factory):
    factory.adopt()
    factory.binding=False
    now=int(time.time())
    with pytest.raises(MeshRuntimeError):
        factory.runtime.queue_message(factory.context,message_id=str(uuid.uuid4()),recipient=Peer("remote",bytes.fromhex(factory.remote.agent_id)),
            radio_public_key=factory.radio,content="Synthetic",consent_binding="consent",issued_at=now,expires_at=now+900)
    assert not factory.transmits
    factory.authorized=False
    with pytest.raises(MeshRuntimeError): factory.runtime.list_messages(factory.context,"inbox")
    assert factory.runtime.close(timeout=5) is True


def test_polling_is_explicit_and_stops_on_revocation(factory):
    assert not factory.transmits and factory.runtime._poller is None
    factory.runtime.start_polling(factory.context,interval=0.2)
    factory.authorized=False
    factory.runtime._poller.join(timeout=2)
    assert not factory.runtime._poller.is_alive()
    assert not factory.transmits


def test_unknown_persisted_dispatch_blocks_release(factory):
    factory.adopt()
    now=int(time.time())
    identifier=str(uuid.uuid4())
    factory.runtime.queue_message(factory.context,message_id=identifier,recipient=Peer("remote",bytes.fromhex(factory.remote.agent_id)),
        radio_public_key=factory.radio,content="Synthetic",consent_binding="consent",issued_at=now,expires_at=now+900)
    factory.rows[identifier]["payload"]["body"]["state"]="dispatching"
    assert factory.runtime.close(timeout=2) is False
    assert not factory.released and not factory.runtime.client._client.is_closed


def test_exact_outbox_action_approval_is_not_just_session_auth(factory):
    factory.adopt()
    seen=[]
    def authorize(context,operation,action):
        if action is not None:
            seen.append(action)
            return False
        return True
    factory.runtime._authorize_context=authorize
    now=int(time.time())
    with pytest.raises(MeshRuntimeError):
        factory.runtime.queue_message(factory.context,message_id=str(uuid.uuid4()),recipient=Peer("remote",bytes.fromhex(factory.remote.agent_id)),
            radio_public_key=factory.radio,content="Unapproved content",consent_binding="consent",issued_at=now,expires_at=now+900)
    assert seen and seen[0].content_digest and not factory.transmits
    assert not any(row["kind"]=="mesh_outbound" for row in factory.rows.values())


def test_close_waits_for_coordinator_job_and_retains_owner(factory):
    entered,release=threading.Event(),threading.Event()
    future=factory.scheduler.submit(lambda device:(entered.set(),release.wait(timeout=3)))
    assert entered.wait(timeout=1)
    try:
        assert factory.runtime.close(timeout=0.05) is False
        assert not factory.released and not factory.runtime.client._client.is_closed
    finally:
        release.set()
        future.result(timeout=2)
    assert not factory.released
    assert factory.runtime.close(timeout=2) is True


def test_storage_loss_does_not_release_serial_or_claim_drain(factory):
    factory.storage=False
    assert factory.runtime.close(timeout=1) is False
    assert not factory.released and not factory.runtime.client._client.is_closed
