# SPDX-License-Identifier: Apache-2.0
"""Actual native observer classes with modeled resource/owner/boot/key inputs."""
from contextlib import contextmanager
import copy,hashlib,os,sys,threading,uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
runtime=os.environ.get('LANTERN_NATIVE_RUNTIME_SOURCE')
if runtime:sys.path.insert(0,str(Path(runtime).resolve()))
pytest.importorskip('sage_lantern.mesh_daemon',reason='optional native Lantern runtime required')
from sage_lantern.mesh_daemon import HardwareRuntime
from sage_lantern.mesh_community import MeshActionAuthority,MeshRequestAccess
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.message_contacts import RecipientID
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from meshticulum.privacy.forward_session import ForwardContext,public
from meshticulum.privacy.mesh_binary_envelope import to_binary
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner,ForwardNativeOwnerError,SCOPES,approval_binding
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_forward_operation import backend
from tests.integrations.sage.test_mesh_inbox import SENDER,RECIPIENT,SENDER_KEY,KEY,sealed
from tests.test_meshticulum_bench_runtime import BOOT

@contextmanager
def setup(*,initiator=True,scopes=SCOPES,approval_override=None,original=None,peer_observer=None):
    state,client,endpoint=backend(SENDER_KEY if initiator else KEY)
    state.owner_live=True;state.epoch='e'*32;state.boot=BOOT;state.now=(100000000,50000000)
    noise=(X25519PrivateKey.generate(),X25519PrivateKey.generate())
    ctx=ForwardContext(SENDER,RECIPIENT,uuid.uuid4().bytes,b'a'*32,100000000,180000000,64,
                       public(noise[0]),public(noise[1]))
    packet=to_binary(sealed(issued_at=100,expires_at=180).decode())
    intention=ReceiveIntention(str(uuid.uuid4()),hashlib.sha256(packet).hexdigest(),len(packet),
        ctx.binding_digest.hex(),BOOT,ctx.expires_epoch_us,130000000)
    if original is not None:ctx,intention,noise,packet=original
    local,remote=(SENDER,RECIPIENT) if initiator else (RECIPIENT,SENDER)
    h=HardwareRuntime.__new__(HardwareRuntime) # No daemon thread/radio initialization.
    h.identity=client._identity;h.actor=RecipientID(local.chain_id,local.public_key.hex())
    h.probe=MessageStorageProbe(client,actor=h.actor,endpoint=endpoint)
    h._mutex=threading.Lock();h._closed=h._closing=h._uncertain=False;h._provider=None
    h._epoch=lambda:state.epoch;h._lock_marker=lambda:True
    h.config=dict(agent_id=local.public_key.hex(),chain_id=local.chain_id,messaging_enabled=True,
        receive_while_locked=False,dispatch_enabled=True,dispatch_while_locked=False,
        peers=[dict(chain_id=remote.chain_id,agent_id=remote.public_key.hex(),radio_public_key='a'*64)])
    h._config_reader=lambda:copy.deepcopy(h.config)
    supervisor=SimpleNamespace(phase='active',broker_id='b'*32,_resource=h,
        resource_lifecycle_check=lambda:state.live,verified_epoch=lambda:state.instance_id+':1')
    h.supervisor=supervisor
    revision=hashlib.sha256(_canonical(h.config)).hexdigest()
    approved=approval_binding(ctx,intention,initiator,config_revision=revision,broker_id=supervisor.broker_id,
        storage_epoch=h.probe.snapshot().epoch)
    authority=MeshActionAuthority()
    with authority.grant(h.actor,MeshRequestAccess(state.epoch,lambda:state.owner_live),scopes,
                         approval_binding=approval_override or approved) as native_context:
        create=lambda:ForwardNativeOwner(h,authority,native_context,context=ctx,intention=intention,
            local_initiator=initiator,key_check=lambda p,d:p==public(noise[0 if initiator else 1]) and d==ctx.digest,
            peer_key_check=peer_observer or (lambda peer,p,d:peer==remote and p==public(noise[1 if initiator else 0]) and d==ctx.digest),
            boot_reader=lambda:state.boot,clock_reader=lambda:state.now)
        try:yield SimpleNamespace(state=state,client=client,hardware=h,supervisor=supervisor,authority=authority,
            native_context=native_context,context=ctx,intention=intention,noise=noise,packet=packet,create=create)
        finally:client._client.close()

@pytest.mark.parametrize('initiator',[True,False])
def test_borrowed_native_composition_uses_exact_durable_counter_store_and_role(initiator):
    with setup(initiator=initiator) as r:
        owner=r.create();h=r.hardware
        h.provider=lambda *a:pytest.fail('must not open MeshCore provider')
        op=owner.operation(r.noise[0 if initiator else 1],limits=Cost(2000,300000000),prior_debt=Cost(),
            sealed_record=r.packet if initiator else None)
        assert op.session.counters is owner._composition.journal
        assert op.session.counters.store is op.budget.store and op.session.counters._lease is op.budget._lease
        assert (op.receiver is None)==initiator and not h._mutex.locked()
        assert len(r.state.rows)==3 # Prepared parent + signed idle quarantine + crypto claim.
        assert r.client._client.is_closed is False
        assert owner.close() is True and r.client._client.is_closed is False # Actual signed quarantine was observed.
        assert h._provider is None

@pytest.mark.parametrize('missing',SCOPES)
def test_each_independent_forward_scope_is_required_even_for_receiver_ack(missing):
    with setup(initiator=False,scopes=tuple(x for x in SCOPES if x!=missing)) as r:
        with pytest.raises(ForwardNativeOwnerError):r.create()
        assert r.state.rows=={} and not r.hardware._mutex.locked()

def test_generic_old_approval_cannot_be_used_as_original_forward_permission():
    with setup(approval_override='f'*64) as r:
        with pytest.raises(ForwardNativeOwnerError):r.create()
        assert not r.state.rows

@pytest.mark.parametrize('field',['_key_check','_peer_key_check','_boot','_clock','owner_lease'])
def test_observer_or_lease_replacement_cannot_bypass_original_native_binding(field):
    with setup() as r:
        owner=r.create()
        original=getattr(owner,field)
        replacement={'_key_check':lambda *args:True,'_peer_key_check':lambda *args:True,
                     '_boot':lambda:r.state.boot,'_clock':lambda:r.state.now,
                     'owner_lease':lambda:None}[field]
        setattr(owner,field,replacement)
        with pytest.raises(ForwardNativeOwnerError):owner.access_check()
        assert owner._uncertain is True and not r.state.rows and not r.hardware._mutex.locked()
        setattr(owner,field,original)
        with pytest.raises(ForwardNativeOwnerError):owner.access_check()

@pytest.mark.parametrize('field',['_key_check','_peer_key_check','_boot','_clock','owner_lease'])
def test_observer_replacement_inside_callback_fences_before_returning_success(field):
    with setup() as r:
        owner=r.create();original=r.hardware.configured
        def configured():
            result=original()
            setattr(owner,field,lambda *args:True)
            return result
        r.hardware.configured=configured
        with pytest.raises(ForwardNativeOwnerError):owner.access_check()
        assert owner._uncertain is True and not r.state.rows
        with pytest.raises(ForwardNativeOwnerError):owner.access_check()

@pytest.mark.parametrize('mutation',['closing','closed','uncertain','broker','resource','supervisor','probe',
    'identity','authority','native-context','config','owner','access-epoch','storage-epoch','boot','expired','deadline','peer-purpose','local-purpose'])
def test_current_native_observation_change_refuses_before_crypto_claim_or_wire(mutation):
    with setup() as r:
        owner=r.create();h=r.hardware
        if mutation=='closing':h._closing=True
        elif mutation=='closed':h._closed=True
        elif mutation=='uncertain':h._uncertain=True
        elif mutation=='broker':r.supervisor.broker_id='c'*32
        elif mutation=='resource':r.supervisor._resource=object()
        elif mutation=='supervisor':h.supervisor=object()
        elif mutation=='probe':h.probe=object()
        elif mutation=='identity':h.identity=object()
        elif mutation=='authority':owner.authority=SimpleNamespace(authorized=lambda *a:True)
        elif mutation=='native-context':owner.native_context=copy.copy(owner.native_context)
        elif mutation=='config':h.config['dispatch_enabled']=False
        elif mutation=='owner':r.state.owner_live=False
        elif mutation=='access-epoch':r.state.epoch='c'*32
        elif mutation=='storage-epoch':r.state.instance_id='c'*64
        elif mutation=='boot':r.state.boot=str(uuid.uuid4())
        elif mutation=='expired':r.state.now=(180000000,50000000)
        elif mutation=='deadline':r.state.now=(100000000,130000000)
        elif mutation=='local-purpose':owner._key_check=lambda *a:False
        else:owner._peer_key_check=lambda *a:False
        with pytest.raises(Exception):owner.access_check()
        assert not r.state.rows and not owner._refs[-1].locked()

def test_existing_drain_lease_is_not_fresh_permission_and_close_still_releases_mutex():
    with setup() as r:
        owner=r.create();lease=[];original=r.hardware.capture_lease
        def capture(consent):
            value=original(consent);lease.append(value);return value
        r.hardware.capture_lease=capture
        with pytest.raises(ForwardNativeOwnerError):
            with owner.owner_lease():
                assert lease[0].check() is True
                r.state.owner_live=False
                assert lease[0].check() is True # Existing drain lease remains admitted.
        assert lease[0].check() is False and not r.hardware._mutex.locked()

def test_after_lease_exit_permission_change_refuses_publication():
    with setup() as r:
        owner=r.create();original=r.hardware.capture_lease
        def capture(consent):
            value=original(consent);close=value.close
            def exit():close();r.state.owner_live=False
            value.close=exit;return value
        r.hardware.capture_lease=capture
        with pytest.raises(ForwardNativeOwnerError):
            with owner.owner_lease():pass
        assert not r.hardware._mutex.locked()

def test_scope_expiry_after_actual_authority_context_exit_refuses_reads():
    with setup() as r:owner=r.create()
    with pytest.raises(ForwardNativeOwnerError):owner.access_check()


def test_timing_and_storage_observations_do_not_reenter_noise_enrollment_or_grant_dispatch():
    calls=[];purpose_live=[True]
    def peer(*arguments):
        calls.append(arguments)
        return purpose_live[0]
    with setup(peer_observer=peer) as r:
        owner=r.create();calls.clear()
        assert owner.time()==r.state.now
        assert owner.storage_check() is True
        assert calls==[]
        purpose_live[0]=False
        # The observation itself grants no cryptographic or traffic action.
        # An action still freshly observes, and refuses, the revoked purpose.
        with pytest.raises(ForwardNativeOwnerError):
            owner.dispatch_check(r.context,r.intention)
        assert calls and not r.state.rows


@pytest.mark.parametrize('method', ['time', 'storage_check'])
@pytest.mark.parametrize('drift', ['authority', 'storage', 'expiry', 'boot'])
def test_observation_only_checks_still_refuse_native_permission_or_epoch_drift(method,drift):
    with setup() as r:
        owner=r.create()
        if drift=='authority':r.state.owner_live=False
        elif drift=='storage':r.state.instance_id='f'*64
        elif drift=='expiry':r.state.now=(r.intention.application_expires_epoch_us,r.state.now[1])
        else:r.state.boot=str(uuid.uuid4())
        with pytest.raises(ForwardNativeOwnerError):getattr(owner,method)()
        assert not r.state.rows

def test_native_callback_caught_reentry_fences_owner_before_publication():
    holder={}
    def purpose(*args):
        if 'owner' in holder:
            with pytest.raises(ForwardNativeOwnerError):holder['owner'].access_check()
        return True
    with setup(peer_observer=purpose) as r:
        owner=r.create();holder['owner']=owner
        with pytest.raises(ForwardNativeOwnerError):owner.access_check()
        assert owner._uncertain and not r.state.rows


def test_constructing_owner_close_cannot_certify_parent_cas_join():
    with setup() as r:
        owner=r.create();observed=[];original=owner.receiver
        def receiver():
            result=original();snapshot=result.probe.snapshot
            def during_storage():
                if owner._constructing and not owner._busy and not owner._held and not observed:
                    observed.append(owner.close())
                return snapshot()
            result.probe.snapshot=during_storage
            return result
        owner.receiver=receiver
        with pytest.raises(Exception):owner.operation(r.noise[0],limits=Cost(2000,300000000),
            prior_debt=Cost(),sealed_record=r.packet)
        assert observed==[False] and owner._budget is not None and not r.hardware._mutex.locked()
        assert r.client._client.is_closed is False


def test_final_native_callback_counter_substitution_refuses_factory_publication():
    holder={};observed=[]
    def purpose(*args):
        owner=holder.get('owner')
        if owner is not None and owner._operation is not None:
            observed.append(True);owner._composition.session.counters=object()
        return True
    with setup(peer_observer=purpose) as r:
        owner=r.create();holder['owner']=owner
        with pytest.raises(ForwardNativeOwnerError):owner.operation(r.noise[0],limits=Cost(2000,300000000),
            prior_debt=Cost(),sealed_record=r.packet)
        assert observed and owner._uncertain and len(r.state.rows)==3
        assert not r.hardware._mutex.locked()


def test_all_five_stages_use_borrowed_native_observers_and_canonical_both_role_journals(tmp_path):
    from tests.integrations.sage.test_forward_children import tracked_stage_pair
    with setup() as left:
        original=(left.context,left.intention,left.noise,left.packet)
        with setup(initiator=False,original=original) as right:
            class SharedClock:
                def __call__(self):return left.state.now
                def advance(self,us):
                    left.state.now=tuple(v+us for v in left.state.now);right.state.now=left.state.now
            clock=SharedClock();owners=[left.create(),right.create()]
            ops=[o.operation(left.noise[i],limits=Cost(2000,300000000),prior_debt=Cost(),
                sealed_record=left.packet if i==0 else None) for i,o in enumerate(owners)]
            p=SimpleNamespace(operations=ops,clock=clock,context=left.context)
            for index in range(5):tracked_stage_pair(tmp_path,p,owners,index)
            assert ops[1].settle().status=='admitted'
            for o,r in zip(owners,(left,right)):
                b=r.state.rows[o._composition.journal.record_id]['payload']['body']
                assert b['confirmation_received'] is True
                assert (b['capsule'] is None)==o.local_initiator
                assert not r.hardware._mutex.locked() and r.hardware._provider is None
            assert ops[0].session.counters is owners[0]._composition.journal
            assert ops[1].session.counters is owners[1]._composition.journal
