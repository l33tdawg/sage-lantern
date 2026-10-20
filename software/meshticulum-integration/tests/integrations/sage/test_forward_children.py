# SPDX-License-Identifier: Apache-2.0
"""Native shutdown tracking with modeled factory/containment; no RF claim."""
import copy,hashlib,threading
from types import SimpleNamespace
import pytest
from lantern_meshticulum.sage.forward_budget import ChildPlan,Cost
from lantern_meshticulum.sage.forward_children import ForwardChildrenError
from tests.integrations.sage.test_forward_native_owner import setup
from tests.test_meshticulum_bench_runtime import BOOT

def hardware_close_ready(h):
    h._radio_events=SimpleNamespace(clear=lambda:None)
    h._background_stop=threading.Event()
    h._read_receipts=h._coordinator=h._gateway=None

def operation(r):
    owner=r.create();op=owner.operation(r.noise[0],limits=Cost(2000,300000000),prior_debt=Cost(),sealed_record=r.packet)
    packet=op.packet()
    p=ChildPlan(0,1,True,'a1'*32,hashlib.sha256(packet).hexdigest(),len(packet),'lora','b1'*32,
        BOOT,r.context.expires_epoch_us,r.intention.application_deadline_monotonic_us,Cost(1,1),512)
    return owner,op,packet,p


def test_factory_is_tracked_after_prepayment_and_shutdown_refuses_opening_before_containment():
    with setup() as r:
        hardware_close_ready(r.hardware);owner,op,packet,p=operation(r);observed=[];contained=[]
        def factory(plan):
            registry=r.hardware._forward_children
            assert registry is owner._registry and len(registry._children)==1
            assert registry._children[0].opening and registry._children[0].plan==p
            assert r.state.rows[op.budget.record_id]['payload']['body']['allocations']==[p.to_dict()]
            observed.append(r.hardware.close(.1))
            assert contained==[] # Never contain a same-thread opening factory.
            raise RuntimeError('modeled partial native factory failure')
        with pytest.raises(Exception):owner.prepare_stage(p,open_child=factory,native_check=lambda *a:True,
            retire_child=lambda t:True,contain_child=lambda *a:contained.append(True) or True,
            terminal_check=lambda *a:True,payload=packet)
        child=owner._registry._children[0]
        assert observed==[False] and not child.opening and child.unknown and not r.hardware._closed
        assert owner._registry.close(.1) is False # Unknown logical debt stays latched.
        assert contained==[True] and child.retired
        assert r.state.rows[op.budget.record_id]['payload']['body']['allocations']==[p.to_dict()]

@pytest.mark.parametrize('failure',['contain','terminal'])
def test_failed_terminal_observation_never_clears_partial_construction_or_prepaid_debt(failure):
    with setup() as r:
        hardware_close_ready(r.hardware);owner,op,packet,p=operation(r);checks=[]
        def contain(*args):checks.append('contain');return failure!='contain'
        def terminal(*args):checks.append('terminal');return failure!='terminal'
        with pytest.raises(Exception):owner.prepare_stage(p,open_child=lambda p:(_ for _ in ()).throw(RuntimeError()),
            native_check=lambda *a:True,retire_child=lambda t:True,contain_child=contain,terminal_check=terminal,payload=packet)
        before=copy.deepcopy(r.state.rows)
        assert r.hardware.close(.1) is False and not r.hardware._closed
        assert checks==(['contain'] if failure=='contain' else ['contain','terminal'])
        assert r.state.rows==before and owner._registry._children[0].unknown
        assert not r.hardware._mutex.locked()


def test_hardware_containment_runs_outside_shared_mutex_and_keeps_unknown_after_success():
    with setup() as r:
        hardware_close_ready(r.hardware);owner,op,packet,p=operation(r);observed=[]
        def contain(plan,runner,deadline):
            acquired=r.hardware._mutex.acquire(blocking=False);observed.append(acquired)
            if acquired:r.hardware._mutex.release()
            assert r.hardware._closing and op.session.state=='closed'
            return True
        with pytest.raises(Exception):owner.prepare_stage(p,open_child=lambda p:(_ for _ in ()).throw(RuntimeError()),
            native_check=lambda *a:True,retire_child=lambda t:True,contain_child=contain,
            terminal_check=lambda *a:True,payload=packet)
        assert r.hardware.close(.1) is False and observed==[True]
        assert not r.hardware._closed


def test_registry_removal_after_admission_cannot_restore_legacy_clean_shutdown():
    with setup() as r:
        hardware_close_ready(r.hardware);owner=r.create();r.hardware._forward_children=None
        with pytest.raises(Exception):owner.access_check()
        assert r.hardware.close(.1) is False and not r.hardware._closed


def test_factory_is_never_entered_when_parent_caps_refuse_and_no_cleanup_ticket_is_invented():
    with setup() as r:
        owner,op,packet,p=operation(r);called=[]
        from dataclasses import replace
        p=replace(p,cost=Cost(2500,1))
        with pytest.raises(Exception):owner.prepare_stage(p,open_child=lambda p:called.append(True),
            native_check=lambda *a:True,retire_child=lambda t:True,contain_child=lambda *a:True,
            terminal_check=lambda *a:True,payload=packet)
        assert called==[] and owner._registry._children==[]
        assert r.state.rows[op.budget.record_id]['payload']['body']['allocations']==[]


def test_empty_known_owner_has_bounded_clean_shutdown_without_legacy_radio_factory():
    with setup() as r:
        hardware_close_ready(r.hardware);owner=r.create()
        assert r.hardware.close(.1) is True and r.hardware._closed
        assert r.hardware._provider is None and r.client._client.is_closed is False
        with pytest.raises(Exception):owner.access_check()


def tracked_stage_pair(tmp_path,p,owners,index,terminal=None):
    from tests.test_meshticulum_private_carrier import carrier_pair
    from meshticulum.bench.services import ServiceTransfer
    from lantern_meshticulum.sage.forward_operation import describe_child
    source=0 if index%2==0 else 1;dest=1-source;packet=p.operations[source].packet()
    service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=p.context.expires_epoch_us)
    with carrier_pair(tmp_path/str(index),packet,p.clock,'unit-a' if source==0 else'unit-b',
                      'unit-b' if source==0 else'unit-a',index+1,service=service,
                      original_expires_epoch_us=p.context.expires_epoch_us) as pair:
        runners=[]
        for n,(flow,owner) in enumerate(zip((pair.sender,pair.receiver),(owners[source],owners[dest]))):
            transfer=ServiceTransfer(flow);plan=describe_child(transfer,slot=index,kind=index+1)
            def retire(t):
                for _ in range(5):t.flow.step();p.clock.advance(50000)
                return True # Modeled late-ACK/terminal cleanup only.
            runner=owner.prepare_stage(plan,open_child=lambda p,t=transfer:t,native_check=lambda *a:True,
                retire_child=retire,contain_child=lambda *a:True,terminal_check=terminal or (lambda *a:True),
                payload=packet if n==0 else None)
            runner.start();runners.append(runner)
        for _ in range(2000):
            runners[0].step();runners[1].step();p.clock.advance(50000)
            if all(r.wire_complete for r in runners):break
        else:raise AssertionError('tracked stage did not finish')
        for owner,runner in zip((owners[source],owners[dest]),runners):assert owner.retire_stage(runner)
        assert all(c.retired for o in owners for c in o._registry._children)


def test_native_operation_cannot_dispatch_a_runner_bypassing_preopen_registry(tmp_path):
    from tests.test_meshticulum_private_carrier import carrier_pair
    from meshticulum.bench.services import ServiceTransfer
    from lantern_meshticulum.sage.forward_operation import ForwardStageRunner,describe_child
    with setup() as r:
        owner,op,packet,p=operation(r)
        class Clock:
            def __call__(self):return r.state.now
            def advance(self,us):r.state.now=tuple(v+us for v in r.state.now)
        service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=r.context.expires_epoch_us)
        with carrier_pair(tmp_path/'untracked',packet,Clock(),'unit-a','unit-b',31,service=service,
                          original_expires_epoch_us=r.context.expires_epoch_us) as pair:
            transfer=ServiceTransfer(pair.sender);plan=describe_child(transfer,slot=0,kind=1)
            admission=op.budget.pre_admit(plan)
            runner=ForwardStageRunner(transfer,op,admission,native_check=lambda *a:True,
                retire_child=lambda t:True,payload=packet)
            with pytest.raises(Exception):runner.start()
            assert not any(w.sent for w in pair.workers) and owner._registry._children==[]


def test_different_request_thread_owner_is_closed_only_by_quiescent_registry_delegation():
    ready=threading.Event();release=threading.Event();holder={}
    def producer():
        with setup() as r:
            hardware_close_ready(r.hardware);holder['r']=r;holder['owner']=r.create()
            ready.set();assert release.wait(2)
    thread=threading.Thread(target=producer);thread.start();assert ready.wait(2)
    try:
        r=holder['r'];owner=holder['owner']
        assert owner.close() is False # Public wrong-thread contract stays intact.
        assert r.hardware.close(.2) is True and r.hardware._closed
        with pytest.raises(Exception):owner._registry_close(owner._registry) # Permit is spent.
    finally:release.set();thread.join(2)
    assert not thread.is_alive()


def test_partial_factory_unknown_fences_new_owners_on_shared_native_hardware():
    with setup() as r:
        owner,op,packet,p=operation(r)
        with pytest.raises(Exception):owner.prepare_stage(p,open_child=lambda p:(_ for _ in ()).throw(RuntimeError()),
            native_check=lambda *a:True,retire_child=lambda t:True,contain_child=lambda *a:True,
            terminal_check=lambda *a:True,payload=packet)
        before=copy.deepcopy(r.state.rows)
        with pytest.raises(Exception):r.create()
        assert r.state.rows==before and owner._registry._uncertain


def test_shutdown_waits_for_open_factory_and_never_contains_before_return():
    entered=threading.Event();release=threading.Event();finished=threading.Event();holder={};contained=[]
    def producer():
        with setup() as r:
            hardware_close_ready(r.hardware);owner,op,packet,p=operation(r);holder.update(r=r,owner=owner)
            def factory(p):entered.set();assert release.wait(2);raise RuntimeError('partial open')
            try:owner.prepare_stage(p,open_child=factory,native_check=lambda *a:True,retire_child=lambda t:True,
                contain_child=lambda *a:contained.append(True) or True,terminal_check=lambda *a:True,payload=packet)
            except Exception:pass
            finished.set()
    thread=threading.Thread(target=producer);thread.start();assert entered.wait(2)
    try:
        assert holder['r'].hardware.close(.03) is False and contained==[]
        release.set();assert finished.wait(2);thread.join(2)
        assert holder['r'].hardware.close(.1) is False and contained==[True]
    finally:release.set();thread.join(2)
    assert not thread.is_alive()


def test_terminal_callback_closure_cannot_publish_success_or_reenter_containment(tmp_path):
    with setup() as left:
        original=(left.context,left.intention,left.noise,left.packet)
        with setup(initiator=False,original=original) as right:
            class Clock:
                def __call__(self):return left.state.now
                def advance(self,us):
                    left.state.now=tuple(v+us for v in left.state.now);right.state.now=left.state.now
            owners=[left.create(),right.create()]
            ops=[o.operation(left.noise[i],limits=Cost(2000,300000000),prior_debt=Cost(),
                sealed_record=left.packet if i==0 else None) for i,o in enumerate(owners)]
            hardware_close_ready(left.hardware);observed=[]
            def terminal(plan,runner):
                observed.append(left.hardware.close(.1));return True
            p=SimpleNamespace(operations=ops,clock=Clock(),context=left.context)
            with pytest.raises(Exception):tracked_stage_pair(tmp_path,p,owners,0,terminal=terminal)
            assert observed==[False] and owners[0]._registry._uncertain
            assert not owners[0]._registry._children[0].observing and not left.hardware._closed


def test_real_purpose_handle_composes_native_owner_without_recursive_setup_lease():
    from contextlib import ExitStack,contextmanager
    from dataclasses import replace
    from lantern_meshticulum.sage.noise_purpose import NoisePurpose
    from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner,approval_binding,SCOPES
    from lantern_meshticulum.sage.message_storage import MessageStorageProbe
    from lantern_meshticulum.sage.message_contacts import RecipientID
    from lantern_meshticulum.sage.workflow_store import WorkflowStore
    from lantern_meshticulum.sage.mesh_inbox import _canonical
    from sage_lantern.mesh_community import MeshRequestAccess
    from meshticulum.privacy.forward_session import public
    with setup() as r,ExitStack() as grants:
        store=WorkflowStore(r.client,storage=MessageStorageProbe(r.client,
            actor=RecipientID(r.context.initiator.chain_id,r.client._identity.agent_id),endpoint=r.hardware.probe.endpoint))
        current=[];setup_held=[False];factory_owner=[]
        @contextmanager
        def setup_lease():
            assert not setup_held[0];setup_held[0]=True
            try:yield
            finally:setup_held[0]=False
        purpose=NoisePurpose(store,local=r.context.initiator,remote=r.context.responder,local_initiator=True,
            intention=r.intention,signing_key=r.client._identity._signing_key,issued_epoch_us=r.context.issued_epoch_us,
            approval_digest='c'*64,owner_lease=setup_lease,access_check=lambda:r.state.owner_live,
            storage_check=lambda:r.state.live,native_check=lambda binding:binding.intention==r.intention,
            enrollment_check=lambda info,digest:digest==current[0].digest,
            remote_pin_check=lambda peer,key,digest:peer==r.context.responder and key==public(r.noise[1])
                and digest==current[0].digest,boot_reader=lambda:r.state.boot,clock=lambda:r.state.now)
        info=purpose.generate();ctx=replace(r.context,initiator_noise_public_key=info.public_key);current.append(ctx)
        handle=purpose.bind(ctx)
        approved=approval_binding(ctx,r.intention,True,config_revision=hashlib.sha256(_canonical(r.hardware.config)).hexdigest(),
            broker_id=r.supervisor.broker_id,storage_epoch=r.hardware.probe.snapshot().epoch)
        native=grants.enter_context(r.authority.grant(r.hardware.actor,MeshRequestAccess(r.state.epoch,lambda:r.state.owner_live),
            SCOPES,approval_binding=approved))
        def factory(key):
            assert not setup_held[0]
            owner=ForwardNativeOwner(r.hardware,r.authority,native,context=ctx,intention=r.intention,local_initiator=True,
                key_check=purpose.key_check,peer_key_check=purpose.peer_key_check,boot_reader=lambda:r.state.boot,
                clock_reader=lambda:r.state.now)
            factory_owner.append(owner)
            return owner.operation(key,limits=Cost(2000,300000000),prior_debt=Cost(),sealed_record=r.packet)
        def abort():return all(owner.close() for owner in factory_owner)
        op=handle.start(factory,abort=abort)
        assert op.session.counters is factory_owner[0]._composition.journal
        assert purpose.retire() is True and op.session._closed and purpose._key is None
        with pytest.raises(Exception):op.packet()
        assert r.state.rows[purpose.record_id]['payload']['body']['state']=='retired'
        assert not r.hardware._mutex.locked() and not setup_held[0]


def test_uncertainty_between_preflight_and_append_refuses_factory_ticket():
    with setup() as r:
        owner,op,packet,p=operation(r);op.budget.pre_admit(p)
        registry=owner._registry;available=registry.available
        def interleaving(current):
            result=available(current)
            registry._uncertain=True # Another failed child between the two lock acquisitions.
            return result
        registry.available=interleaving
        with pytest.raises(Exception):op._run(lambda:registry.begin(owner,p,contain=lambda *a:True,terminal=lambda *a:True))
        assert registry._children==[]
        assert r.state.rows[op.budget.record_id]['payload']['body']['allocations']==[p.to_dict()]
