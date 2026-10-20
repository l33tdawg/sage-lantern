# SPDX-License-Identifier: Apache-2.0
"""Real MPF2 + signed SDK CAS adapter; backend/native permission are models."""
from contextlib import contextmanager
from dataclasses import replace
import base64
import copy
import hashlib
import json
import uuid

import pytest
pytest.importorskip('sage_sdk',reason='compatible optional Lantern SDK required')
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from meshticulum.privacy.forward_session import ForwardContext, ForwardSession, ForwardSessionError, public
from meshticulum.privacy.mesh_binary_envelope import to_binary
from lantern_meshticulum.sage.forward_receive_journal import (
    ForwardReceiveJournal, ForwardJournalError, ReceiveIntention, SCHEMA, SIGNATURE_DOMAIN)
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_mesh_inbox import rig, sealed, SENDER, RECIPIENT, SENDER_KEY, KEY, OPERATION

BOOT = '21f9c178-6090-40d9-9359-ecf1c7b950b1'


class SenderCounters:
    """Non-durable sender model; this suite qualifies the protected receiver."""
    def claim(self,binding): self.tx=0;return True
    def reserve_transmit(self):self.tx+=1;return self.tx
    def record_receive(self,n):return n==1


@pytest.fixture
def journal_rig(rig):
    rig.monotonic = 100000000
    rig.permission = True
    rig.lease_active = False
    noise = tuple(X25519PrivateKey.from_private_bytes(hashlib.sha256(label).digest())
        for label in (b'fixture journal initiator Noise',b'fixture journal responder Noise'))
    ctx = ForwardContext(SENDER,RECIPIENT,uuid.uuid4().bytes,b'a'*32,100000000,180000000,64,
                         public(noise[0]),public(noise[1]))
    packet = to_binary(sealed().decode())
    intention = ReceiveIntention(OPERATION,hashlib.sha256(packet).hexdigest(),len(packet),
                                (b'a'*32).hex(),BOOT,180000000,130000000)
    @contextmanager
    def lease():
        assert not rig.lease_active
        rig.lease_active = True
        try:yield
        finally:rig.lease_active = False
    def new(*, context=ctx, intended=intention, boot=BOOT):
        return ForwardReceiveJournal(rig.store,context=context,local_initiator=False,
            local_signing_key=KEY,intention=intended,boot_id=boot,owner_lease=lease,
            access_check=lambda:rig.live,storage_check=lambda:rig.storage,
            peer_check=lambda peer:peer==rig.peer,
            permission_check=lambda wanted:rig.permission and wanted==intended,
            clock=lambda:(int(rig.now*1000000),rig.monotonic))
    rig.new_journal=new
    rig.journal=new()
    rig.context,rig.packet,rig.intention = ctx,packet,intention
    rig.clock=lambda:(int(rig.now*1000000),rig.monotonic)
    rig.sender = lambda: ForwardSession(context=ctx,initiator=True,signing_key=SENDER_KEY,
        noise_static_key=noise[0],counters=SenderCounters(),authorize=lambda:rig.live,
        clock=rig.clock,application_deadline_monotonic_us=130000000)
    rig.receiver = lambda: ForwardSession(context=ctx,initiator=False,signing_key=KEY,
        noise_static_key=noise[1],counters=rig.journal,authorize=lambda:rig.live,
        clock=rig.clock,application_deadline_monotonic_us=130000000,receive_mode='commit')
    return rig


def ready(rig):
    i,r=rig.sender(),rig.receiver()
    i.finish(r.respond(i.start()))
    i.verify_confirmation(r.accept_confirmation(i.confirm()))
    assert i.state==r.state=='ready'
    return i,r


def body(rig):return rig.rows[rig.journal.record_id]['payload']['body']


def accepted(rig):
    i,r=ready(rig);outer=i.seal(rig.packet)
    assert r.receive_into(outer) is None
    return i,r,outer


def test_actual_session_commits_single_signed_replay_capsule_and_recovers_without_keys(journal_rig):
    rig=journal_rig;i,r,outer=accepted(rig)
    b=body(rig)
    assert b['confirmation_received'] and b['capsule']['counter']==2
    assert b['capsule']['outer_sha256']==hashlib.sha256(outer).hexdigest()
    assert b['intention']==rig.intention.to_dict() and b['context']['session_id']==rig.context.session_id.hex()
    assert len(rig.rows)==1 and 'private synthetic' not in json.dumps(rig.rows)
    i.close();r.close();rig.journal.close()
    recovered=rig.new_journal().recover()
    assert recovered.sealed_record==rig.packet and recovered.intention==rig.intention
    assert recovered.handshake_hash==r.handshake_hash.hex()
    assert 'sealed_record' not in repr(recovered)


@pytest.mark.parametrize('phase',['claim','transmit','confirmation','data'])
@pytest.mark.parametrize('failure',['before','after','conflict'])
def test_unknown_boundary_fences_original_owner_and_preserves_joint_state(journal_rig,phase,failure):
    rig=journal_rig
    if phase=='claim':action=lambda:rig.journal.claim(rig.journal.binding)
    else:
        i,r=ready(rig)
        if phase=='transmit':action=rig.journal.reserve_transmit
        elif phase=='confirmation':
            # A duplicate is independently refused; use a fresh claim without a
            # complete exchange to test confirmation callback boundary directly.
            rig.journal.close();rig.rows.clear();rig.journal=rig.new_journal()
            assert rig.journal.claim(rig.journal.binding)
            action=lambda:rig.journal.record_receive(1)
        else:
            outer=i.seal(rig.packet);action=lambda:r.receive_into(outer)
    rig.failure=(SCHEMA,failure)
    with pytest.raises((ForwardJournalError,ForwardSessionError)):action()
    assert rig.journal._uncertain
    assert not rig.lease_active
    row=rig.rows.get(rig.journal.record_id)
    if phase=='claim':assert (row is not None)==(failure=='after')
    if phase=='data':
        assert (body(rig)['capsule'] is not None)==(failure=='after')
        if failure=='after':assert to_binary(body(rig)['capsule']['wire'])==rig.packet
    rig.failure=None
    with pytest.raises(ForwardJournalError):rig.journal.recover()
    if phase=='data':
        restored=rig.new_journal().recover()
        assert (restored is not None)==(failure=='after')


@pytest.mark.parametrize('change',['same','binding','operation','context','role-key-purpose'])
def test_existing_claim_cannot_be_reused_or_evaded_by_original_descriptor_changes(journal_rig,change):
    rig=journal_rig;accepted(rig);before=copy.deepcopy(rig.rows)
    ctx=rig.context;intent=rig.intention
    if change=='binding':
        ctx=replace(ctx,binding_digest=b'b'*32);intent=replace(intent,authority_digest=(b'b'*32).hex())
    elif change=='operation':intent=replace(intent,operation_id=str(uuid.uuid4()))
    elif change=='context':ctx=replace(ctx,max_records=32)
    elif change=='role-key-purpose':
        ctx=replace(ctx,responder_noise_public_key=public(X25519PrivateKey.generate()))
    restored=rig.new_journal(context=ctx,intended=intent)
    assert restored.record_id==rig.journal.record_id
    with pytest.raises(ForwardJournalError):restored.claim(restored.binding)
    assert rig.rows==before


def test_data_replay_and_direct_record_receive_cannot_replace_capsule(journal_rig):
    rig=journal_rig;i,r,outer=accepted(rig);before=copy.deepcopy(rig.rows)
    with pytest.raises(ForwardSessionError):r.receive_into(outer)
    assert rig.rows==before
    restored=rig.new_journal()
    with pytest.raises(ForwardJournalError):restored.record_receive(2)
    assert rig.rows==before


@pytest.mark.parametrize('field,value',[
    ('counter',3),('counter',True),('counter_binding',b'x'*32),
    ('application_deadline_monotonic_us',130000001),('observed_epoch_us',149999999),
    ('observed_monotonic_us',99999999),('sealed_record',b'wrong'),
    ('local_initiator',True),('handshake_hash',b'x')])
def test_constructed_candidate_does_not_override_original_intention(journal_rig,field,value):
    rig=journal_rig;i,r=ready(rig);outer=i.seal(rig.packet);captured=[]
    original=rig.journal.commit_receive
    rig.journal.commit_receive=lambda c:captured.append(c) or True
    r.receive_into(outer)
    rig.journal.commit_receive=original
    before=copy.deepcopy(rig.rows)
    with pytest.raises(ForwardJournalError):rig.journal.commit_receive(replace(captured[0],**{field:value}))
    assert rig.rows==before and body(rig)['capsule'] is None


@pytest.mark.parametrize('mode',['epoch','mono','permission','boot','storage','peer'])
def test_original_native_liveness_refusal_creates_no_data_capsule(journal_rig,mode):
    rig=journal_rig;i,r=ready(rig);outer=i.seal(rig.packet);before=copy.deepcopy(rig.rows)
    if mode=='epoch':rig.now=180
    elif mode=='mono':rig.monotonic=130000000
    elif mode=='permission':rig.permission=False
    elif mode=='boot':rig.journal.boot_id=str(uuid.uuid4())
    elif mode=='storage':rig.storage=False
    else:rig.peer=RECIPIENT
    with pytest.raises(ForwardSessionError):r.receive_into(outer)
    assert rig.rows==before and body(rig)['capsule'] is None


def test_recovery_after_expiry_boot_change_is_read_only_and_never_renews_claim(journal_rig):
    rig=journal_rig;accepted(rig);before=copy.deepcopy(rig.rows);rig.now=200;rig.monotonic=1
    rig.permission=False
    restored=rig.new_journal(boot=str(uuid.uuid4()))
    result=restored.recover()
    assert result.sealed_record==rig.packet and result.intention==rig.intention
    assert rig.rows==before
    with pytest.raises(ForwardJournalError):restored.claim(restored.binding)
    assert rig.rows==before


@pytest.mark.parametrize('callback',['access','storage','peer','permission','clock','lease'])
def test_callback_close_cannot_return_success_or_dispatch_data(journal_rig,callback):
    rig=journal_rig;i,r=ready(rig);outer=i.seal(rig.packet);before=copy.deepcopy(rig.rows)
    def closing(*args):
        rig.journal.close()
        return rig.clock() if callback=='clock' else True
    if callback=='lease':
        @contextmanager
        def lease():
            yield
            rig.journal.close()
        rig.journal._lease=lease
    else:setattr(rig.journal,{'access':'_access','storage':'_storage','peer':'_peer',
                             'permission':'_permission','clock':'_clock'}[callback],closing)
    with pytest.raises(ForwardSessionError):r.receive_into(outer)
    if callback=='lease':
        assert body(rig)['capsule'] is not None
        assert rig.new_journal().recover().sealed_record==rig.packet
    else:assert rig.rows==before


def test_caught_nested_operation_fences_outer_before_write(journal_rig):
    rig=journal_rig;i,r=ready(rig);outer=i.seal(rig.packet);before=copy.deepcopy(rig.rows)
    called=[]
    def permission(intent):
        called.append(True)
        with pytest.raises(ForwardJournalError):rig.journal.recover()
        return True
    rig.journal._permission=permission
    with pytest.raises(ForwardSessionError):r.receive_into(outer)
    assert called and rig.rows==before and rig.journal._uncertain


def test_storage_epoch_change_after_commit_keeps_capsule_and_fences_producer(journal_rig):
    rig=journal_rig;i,r=ready(rig);outer=i.seal(rig.packet)
    rig.after_put=lambda:setattr(rig,'instance','c'*64)
    with pytest.raises(ForwardSessionError):r.receive_into(outer)
    assert body(rig)['capsule'] is not None and rig.journal._uncertain
    rig.after_put=None
    assert rig.new_journal().recover().sealed_record==rig.packet


@pytest.mark.parametrize('bad',['unsigned','missing-capsule-bytes','boolean-intention','different-original'])
def test_recovery_refuses_tampered_or_incomplete_signed_record(journal_rig,bad):
    rig=journal_rig;accepted(rig)
    row=rig.rows[rig.journal.record_id];b=row['payload']['body']
    if bad=='unsigned':b['capsule']['outer_sha256']='d'*64
    else:
        if bad=='missing-capsule-bytes':b['capsule'].pop('wire')
        elif bad=='boolean-intention':b['intention']['packet_bytes']=True
        else:b['intention']['application_deadline_monotonic_us']+=1
        row['payload']['signature']=base64.b64encode(KEY.sign(SIGNATURE_DOMAIN+_canonical(b)).signature).decode()
    with pytest.raises(ForwardJournalError):rig.new_journal().recover()


def test_data_requires_confirmation_and_never_uses_separate_data_receive_write(journal_rig):
    rig=journal_rig;i,r=ready(rig)
    rig.journal.record_receive=lambda n:pytest.fail('DATA must not use record_receive')
    assert r.receive_into(i.seal(rig.packet)) is None
    assert body(rig)['capsule'] is not None


@pytest.mark.parametrize('operation',['claim','data','recover'])
@pytest.mark.parametrize('change',['access','storage','peer','epoch','deadline','permission'])
def test_post_lease_revocation_or_change_cannot_publish_stale_success(journal_rig,operation,change):
    rig=journal_rig
    if operation=='claim':action=lambda:rig.journal.claim(rig.journal.binding)
    elif operation=='data':
        i,r=ready(rig);outer=i.seal(rig.packet);action=lambda:r.receive_into(outer)
    else:
        accepted(rig);rig.journal=rig.new_journal();action=rig.journal.recover
    @contextmanager
    def lease():
        yield
        if change=='access':rig.live=False
        elif change=='storage':rig.storage=False
        elif change=='peer':rig.peer=RECIPIENT
        elif change=='epoch':rig.instance='c'*64
        elif change=='deadline':rig.now=180
        else:rig.permission=False
    rig.journal._lease=lease
    # Historical quarantine reads use current read guards, but are not fresh
    # application admission and may occur after expiry/dispatch revocation.
    if operation=='recover' and change in ('deadline','permission'):
        assert action().sealed_record==rig.packet
    else:
        with pytest.raises((ForwardJournalError,ForwardSessionError)):action()
        assert rig.journal._uncertain
    if operation=='claim':assert body(rig)['capsule'] is None
    else:assert to_binary(body(rig)['capsule']['wire'])==rig.packet


def test_actual_competing_cas_wins_joint_commit_and_losing_owner_is_fenced(journal_rig):
    rig=journal_rig;i,r=ready(rig);outer=i.seal(rig.packet)
    client=rig.store.client;original=client.workflow_put;won=[]
    def race(*args,**kwargs):
        payload=kwargs['payload']
        if payload['schema']==SCHEMA and payload['body']['capsule'] is not None and not won:
            # Competing transaction wins this exact protected CAS. The second
            # real SDK request sees the authoritative model's revision conflict.
            won.append(original(*args,**kwargs))
        return original(*args,**kwargs)
    client.workflow_put=race
    with pytest.raises(ForwardSessionError):r.receive_into(outer)
    client.workflow_put=original
    assert won and rig.journal._uncertain
    assert rig.new_journal().recover().sealed_record==rig.packet
    assert body(rig)['capsule']['outer_sha256']==hashlib.sha256(outer).hexdigest()


def test_recovery_clock_rollback_refuses_without_mutating_joint_record(journal_rig):
    rig=journal_rig;accepted(rig);before=copy.deepcopy(rig.rows);rig.now=149
    with pytest.raises(ForwardJournalError):rig.new_journal().recover()
    assert rig.rows==before


def test_live_provider_refuses_old_valid_signed_revision_after_data_commit(journal_rig):
    rig=journal_rig;i,r=ready(rig);old=copy.deepcopy(rig.rows[rig.journal.record_id])
    outer=i.seal(rig.packet);r.receive_into(outer)
    assert rig.journal._highest_revision>old['revision']
    rig.rows[rig.journal.record_id]=old
    before=copy.deepcopy(rig.rows)
    with pytest.raises(ForwardSessionError):r.receive_into(outer)
    assert rig.rows==before and rig.journal._uncertain


def test_live_recovery_refuses_same_revision_signed_equivocation(journal_rig):
    rig=journal_rig;accepted(rig);rig.journal.recover()
    row=rig.rows[rig.journal.record_id];b=row['payload']['body']
    b['capsule']['outer_sha256']='d'*64
    row['payload']['signature']=base64.b64encode(KEY.sign(SIGNATURE_DOMAIN+_canonical(b)).signature).decode()
    with pytest.raises(ForwardJournalError):rig.journal.recover()


def test_signed_capsule_without_local_confirmation_transmit_is_integrity_failure(journal_rig):
    rig=journal_rig;accepted(rig);row=rig.rows[rig.journal.record_id];b=row['payload']['body'];b['tx']=0
    row['payload']['signature']=base64.b64encode(KEY.sign(SIGNATURE_DOMAIN+_canonical(b)).signature).decode()
    with pytest.raises(ForwardJournalError):rig.new_journal().recover()


@pytest.mark.parametrize('operation',['claim','data','recover'])
@pytest.mark.parametrize('callback',['snapshot','clock'])
def test_post_exit_observation_revocation_is_rechecked_before_publication(journal_rig,operation,callback):
    rig=journal_rig
    if operation=='claim':action=lambda:rig.journal.claim(rig.journal.binding)
    elif operation=='data':
        i,r=ready(rig);outer=i.seal(rig.packet);action=lambda:r.receive_into(outer)
    else:
        accepted(rig);rig.journal=rig.new_journal();action=rig.journal.recover
    if callback=='snapshot':
        original=rig.store.storage.snapshot
        def observation():
            result=original()
            if not rig.lease_active:rig.live=False
            return result
        rig.store.storage.snapshot=observation
    else:
        def observation():
            result=rig.clock()
            if not rig.lease_active:rig.live=False
            return result
        rig.journal._clock=observation
    with pytest.raises((ForwardJournalError,ForwardSessionError)):action()
    assert rig.journal._uncertain
    if operation=='claim':assert body(rig)['capsule'] is None
    else:assert to_binary(body(rig)['capsule']['wire'])==rig.packet


def test_recovery_preserves_original_preparation_observation_and_deadlines(journal_rig):
    rig=journal_rig;i,r=ready(rig);initial=copy.deepcopy(body(rig))
    rig.now=151;rig.monotonic=101000000;r.receive_into(i.seal(rig.packet))
    rig.now=200;rig.monotonic=102000000
    recovered=rig.new_journal().recover()
    assert recovered.prepared_epoch_us==initial['prepared_epoch_us']
    assert recovered.prepared_monotonic_us==initial['prepared_monotonic_us']
    assert recovered.observed_epoch_us==151000000 and recovered.observed_monotonic_us==101000000
    assert recovered.intention==rig.intention
