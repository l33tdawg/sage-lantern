"""Real inbox/envelope/SDK/radio codecs with synthetic transports only."""

import copy
import hashlib
import json
import struct
import threading
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from sage_sdk import AgentIdentity
from nacl.signing import SigningKey

from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.mesh_envelope import open_envelope, correlate_receipt, seal_delivery_receipt
from sage_lantern.mesh_fragments import split_envelope
from sage_lantern.mesh_inbox import inbox_id
from sage_lantern.mesh_receipt_sender import MeshReceiptSender, ReceiptAction, ReceiptPermit, ReceiptSenderError
from test_mesh_inbox import rig, feed, sealed, SENDER, SENDER_KEY, RECIPIENT, KEY, REMOTE, OPERATION
from test_meshcore_transport import client as radio_client, frame, handshake_bytes, KEY as RADIO_SELF
from test_meshcore_contacts import contact, start, end


RADIO = bytes(SigningKey(b"r"*32).verify_key)


@pytest.fixture
def sender(rig):
    feed(rig.inbox,sealed())
    state=SimpleNamespace(rig=rig,local_id=inbox_id(SENDER,REMOTE),allowed=True,read_approved=True,
                          fault=None,radio_allowed=True,policy_boolean=False,barrier=None,lock=threading.Lock())
    transport=rig.store.client._client._transport
    def handler(request):
        if request.method != "PUT" or "approval" not in json.loads(request.content).get("payload",{}).get("body",{}):
            return transport.handle_request(request)
        body=json.loads(request.content)
        identifier=request.url.path.rsplit("/",1)[-1]
        assert request.headers["X-Agent-ID"]==RECIPIENT.public_key.hex()
        if state.barrier:
            state.barrier.wait(timeout=3)
        with state.lock:
            revision=rig.rows.get(identifier,{}).get("revision",0)
            if revision != body["expected_revision"]:
                return httpx.Response(409,json={})
            if state.fault=="before":
                raise httpx.ReadTimeout("private-diagnostic")
            row=dict(schema="sage.workflow-journal.v1",agent_id=RECIPIENT.public_key.hex(),record_id=identifier,
                     revision=revision+1,kind="mesh_outbound",payload=copy.deepcopy(body["payload"]),trust="untrusted_auxiliary")
            rig.rows[identifier]=row
            if state.fault=="after":
                raise httpx.ReadTimeout("private-diagnostic")
            return httpx.Response(200,json=row)
    rig.store.client._client._transport=httpx.MockTransport(handler)
    def policy(action):
        assert type(action) is ReceiptAction
        if state.policy_boolean:
            return True
        if action.kind=="read" and not state.read_approved:
            return None
        return ReceiptPermit(action,"private-opt-in","render-ack-original" if action.kind=="read" else "")
    def verify(action):
        card=sign_contact_card(AgentIdentity(SENDER_KEY),chain_id=SENDER.chain_id,radio_public_key=RADIO.hex(),
            display_name="Synthetic sender",revision=1,issued_at=100,expires_at=1000,now=rig.now,
            findable=True,messaging_allowed=state.radio_allowed)
        return verify_contact_card(card,now=rig.now)
    state.new=lambda:MeshReceiptSender(rig.store,AgentIdentity(KEY),inbox=rig.new(),sender=RECIPIENT,
        guard=lambda action:state.allowed,policy=policy,verify_contact=verify,clock=lambda:rig.now)
    state.sender=state.new()
    state.access=lambda:state.allowed
    state.queue=lambda kind="delivery", owner=None:(owner or state.sender).queue(local_id=state.local_id,kind=kind,
        radio_public_key=RADIO,issued_at=150,expires_at=900,access_check=state.access)
    state.approved=lambda identifier:rig.rows[identifier]["payload"]["body"]["approval"]["body"]
    yield state


def device_for(state,identifier,*,reply=True,timestamp=500):
    body=state.rig.rows[identifier]["payload"]["body"]
    fragment=split_envelope(state.approved(identifier)["wire"].encode())[body["next_fragment"]]
    stamp=body["timestamp"] or timestamp
    ack=hashlib.sha256(struct.pack("<I",stamp)+b"\0"+fragment.encode()+RADIO_SELF).digest()[:4]
    data=handshake_bytes()+frame(start(1))+frame(contact(RADIO))+frame(end(123))
    if reply:
        data+=frame(b"\x06\x00"+ack+struct.pack("<I",900))
    device,stream=radio_client(data,chunk=179)
    device.handshake()
    original_write=stream.write
    def write(data,timeout):
        if len(data)>3 and data[3]==2:
            assert state.rig.rows[identifier]["payload"]["body"]["state"]=="dispatching"
        return original_write(data,timeout)
    stream.write=write
    return device


@pytest.mark.parametrize("kind",["delivery","read"])
def test_real_receipt_correlates_and_dispatches_after_commit(sender,kind):
    progress=sender.queue(kind)
    approved=sender.approved(progress.receipt_id)
    opened=open_envelope(approved["wire"],SENDER_KEY,expected_sender=RECIPIENT,expected_recipient=SENDER,now=150)
    original=sender.rig.inbox.historical_read(sender.local_id)
    correlate_receipt(opened,original_sender=SENDER,original_recipient=RECIPIENT,original_id=REMOTE,
                      original_digest=original.digest,original_issued_at=100)
    assert opened.kind==kind and opened.message_id==progress.receipt_id
    assert "content" not in approved and "private synthetic" not in str(approved)
    while progress.state=="queued":
        progress=sender.sender.dispatch_one(progress.receipt_id,device=device_for(sender,progress.receipt_id),
                                            radio_timestamp=500,access_check=sender.access)
    assert progress.state=="serial_accepted"
    assert sender.sender.close() is True


def test_two_slots_reopen_preserves_wire_and_rejects_retarget(sender):
    first=sender.queue()
    second=sender.queue("read")
    wire=sender.approved(first.receipt_id)["wire"]
    for _ in range(4):
        assert sender.queue(owner=sender.new()).receipt_id==first.receipt_id
    assert sender.approved(first.receipt_id)["wire"]==wire
    assert len([row for row in sender.rig.rows.values() if row["kind"]=="mesh_outbound"])==2
    assert first.receipt_id!=second.receipt_id
    with pytest.raises(ReceiptSenderError):
        sender.sender.queue(local_id=sender.local_id,kind="delivery",radio_public_key=RADIO,issued_at=151,expires_at=900,access_check=sender.access)


def test_policy_boolean_and_missing_read_authority_never_seals(sender):
    sender.policy_boolean=True
    with pytest.raises(ReceiptSenderError): sender.queue()
    sender.policy_boolean=False
    sender.read_approved=False
    with pytest.raises(ReceiptSenderError): sender.queue("read")
    assert not any(row["kind"]=="mesh_outbound" for row in sender.rig.rows.values())


@pytest.mark.parametrize("fault",["before","after"])
def test_queue_uncertainty_no_rf_and_same_slot_reconciliation(sender,fault):
    sender.fault=fault
    with pytest.raises(ReceiptSenderError): sender.queue()
    assert sender.sender.close() is False
    if fault=="after":
        identifier=next(key for key,row in sender.rig.rows.items() if row["kind"]=="mesh_outbound")
        wire=sender.approved(identifier)["wire"]
        sender.fault=None
        assert sender.queue(owner=sender.new()).receipt_id==identifier
        assert sender.approved(identifier)["wire"]==wire


def test_dispatch_unknown_requires_explicit_retry_preserving_wire_index_timestamp(sender):
    progress=sender.queue()
    wire=sender.approved(progress.receipt_id)["wire"]
    with pytest.raises(ReceiptSenderError):
        sender.sender.dispatch_one(progress.receipt_id,device=device_for(sender,progress.receipt_id,reply=False),radio_timestamp=500,access_check=sender.access)
    owner=sender.new()
    unknown=owner.inspect(progress.receipt_id,access_check=sender.access)
    assert unknown.state=="dispatching" and unknown.next_fragment==0
    with pytest.raises(ReceiptSenderError):
        owner.dispatch_one(progress.receipt_id,device=device_for(sender,progress.receipt_id),radio_timestamp=999,access_check=sender.access)
    owner.retry_decision(progress.receipt_id,expected_revision=unknown.revision,decision_binding="old-worker-fenced",access_check=sender.access)
    assert sender.rig.rows[progress.receipt_id]["payload"]["body"]["timestamp"]==500
    assert sender.approved(progress.receipt_id)["wire"]==wire
    done=owner.dispatch_one(progress.receipt_id,device=device_for(sender,progress.receipt_id),radio_timestamp=999,access_check=sender.access)
    assert done.next_fragment==1
    assert sender.sender.close() is False


@pytest.mark.parametrize("change",["lock","contact","read"])
def test_current_authority_rechecked_before_rf(sender,change):
    progress=sender.queue("read")
    if change=="lock": sender.allowed=False
    elif change=="contact": sender.radio_allowed=False
    else: sender.read_approved=False
    with pytest.raises(ReceiptSenderError):
        sender.sender.dispatch_one(progress.receipt_id,device=device_for(sender,progress.receipt_id),radio_timestamp=500,access_check=sender.access)
    assert sender.rig.rows[progress.receipt_id]["payload"]["body"]["state"]=="queued"


@pytest.mark.parametrize("phase", ["dispatching", "queued"])
@pytest.mark.parametrize("change", ["lock", "contact", "read", "expiry"])
def test_authority_change_during_receipt_cas_is_observed(sender, monkeypatch, phase, change):
    progress = sender.queue("read")
    device = device_for(sender, progress.receipt_id)
    original = sender.sender.store.put
    def put(*args, **kwargs):
        result = original(*args, **kwargs)
        if result["payload"]["body"]["state"] == phase:
            if change == "lock": sender.allowed = False
            elif change == "contact": sender.radio_allowed = False
            elif change == "read": sender.read_approved = False
            else: sender.rig.now = 900
        return result
    monkeypatch.setattr(sender.sender.store, "put", put)
    with pytest.raises(ReceiptSenderError):
        sender.sender.dispatch_one(progress.receipt_id, device=device,
                                   radio_timestamp=500, access_check=sender.access)
    body = sender.rig.rows[progress.receipt_id]["payload"]["body"]
    assert body["next_fragment"] == (0 if phase == "dispatching" else 1)
    assert sender.sender.close() is False


def test_receipt_journal_guards_do_not_expand_contact_reads(sender, monkeypatch):
    progress = sender.queue()
    original = sender.sender.verify_contact
    contacts = []
    def verify(action):
        contacts.append(action)
        return original(action)
    monkeypatch.setattr(sender.sender, "verify_contact", verify)
    sender.sender.dispatch_one(progress.receipt_id, device=device_for(sender, progress.receipt_id),
                               radio_timestamp=500, access_check=sender.access)
    # Five dispatch boundaries plus the radio inventory's four guards.
    assert len(contacts) == 9


def test_missing_or_tampered_admission_no_receipt(sender):
    sender.rig.rows[sender.local_id]["payload"]["attestation"]["body"]["envelope_digest"]="f"*64
    with pytest.raises(ReceiptSenderError): sender.queue()
    assert not any(row["kind"]=="mesh_outbound" for row in sender.rig.rows.values())


def test_no_receipt_for_receipt(sender):
    wire=seal_delivery_receipt(SENDER_KEY,sender=SENDER,recipient=RECIPIENT,message_id=OPERATION,
        original_id=REMOTE,original_digest="d"*64,issued_at=100,expires_at=900).wire.encode()
    feed(sender.rig.inbox,wire)
    sender.local_id=inbox_id(SENDER,OPERATION)
    with pytest.raises(ReceiptSenderError): sender.queue()


@pytest.mark.parametrize("target",["wire","state","approval"])
def test_signed_state_tampering_rejected(sender,target):
    progress=sender.queue()
    body=sender.rig.rows[progress.receipt_id]["payload"]["body"]
    if target=="state": body["state"]="serial_accepted"
    elif target=="wire": body["approval"]["body"]["wire"]+="x"
    else: body["approval"]["body"]["privacy_binding"]="forged"
    with pytest.raises(ReceiptSenderError): sender.new().inspect(progress.receipt_id,access_check=sender.access)


def test_concurrent_queue_same_slot_only_one_winner(sender):
    sender.barrier=threading.Barrier(2)
    outcomes=[]
    def queue():
        try: outcomes.append(sender.queue(owner=sender.new()))
        except ReceiptSenderError: outcomes.append(None)
    workers=[threading.Thread(target=queue) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert sum(item is not None for item in outcomes)==1
    assert len([row for row in sender.rig.rows.values() if row["kind"]=="mesh_outbound"])==1


def test_progress_commit_failure_preserves_unknown_index(sender):
    progress=sender.queue()
    device=device_for(sender,progress.receipt_id)
    original=device.send_text
    def send(**kwargs):
        result=original(**kwargs)
        sender.fault="before"
        return result
    device.send_text=send
    with pytest.raises(ReceiptSenderError):
        sender.sender.dispatch_one(progress.receipt_id,device=device,radio_timestamp=500,access_check=sender.access)
    sender.fault=None
    owner=sender.new()
    state=owner.inspect(progress.receipt_id,access_check=sender.access)
    assert state.state=="dispatching" and state.next_fragment==0
    assert owner.close() is False


def test_full_radio_prefix_collision_never_dispatches(sender):
    progress=sender.queue()
    collision=RADIO[:6]+bytes(26)
    device,stream=radio_client(handshake_bytes()+frame(start(2))+frame(contact(RADIO))+frame(contact(collision))+frame(end(123)),chunk=179)
    device.handshake()
    with pytest.raises(ReceiptSenderError):
        sender.sender.dispatch_one(progress.receipt_id,device=device,radio_timestamp=500,access_check=sender.access)
    assert sender.rig.rows[progress.receipt_id]["payload"]["body"]["state"]=="queued"


def test_permit_cannot_be_reused_for_another_action(sender):
    saved=[]
    old_policy=sender.sender.policy
    def policy(action):
        if not saved:
            saved.append(old_policy(action))
        return saved[0]
    sender.sender.policy=policy
    progress=sender.queue()
    with pytest.raises(ReceiptSenderError):
        sender.sender.dispatch_one(progress.receipt_id,device=device_for(sender,progress.receipt_id),radio_timestamp=500,access_check=sender.access)
    assert sender.rig.rows[progress.receipt_id]["payload"]["body"]["state"]=="queued"


def test_close_fences_inflight_policy_without_new_row(sender):
    entered,release=threading.Event(),threading.Event()
    old_policy=sender.sender.policy
    def policy(action):
        entered.set()
        assert release.wait(timeout=3)
        return old_policy(action)
    sender.sender.policy=policy
    errors=[]
    def queue():
        try: sender.queue()
        except ReceiptSenderError: errors.append(True)
    worker=threading.Thread(target=queue)
    worker.start()
    assert entered.wait(timeout=3)
    assert sender.sender.close(timeout=0) is False
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive() and errors==[True]
    assert sender.sender.close() is True
    assert not any(row["kind"]=="mesh_outbound" for row in sender.rig.rows.values())
