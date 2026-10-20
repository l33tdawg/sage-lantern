# SPDX-License-Identifier: Apache-2.0
"""Five admitted stages on actual local flow/crypto; native/RF/backend modeled."""
from contextlib import contextmanager
import hashlib,json,uuid,copy
from types import SimpleNamespace
import httpx,pytest
pytest.importorskip('sage_sdk',reason='compatible optional SDK required')
from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from meshticulum.privacy.forward_session import ForwardContext,public
from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.wire import FrameKind
from lantern_meshticulum.sage.forward_receiver import ForwardReceiver
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.forward_budget import ForwardBudget,Cost
from lantern_meshticulum.sage.forward_operation import ForwardOperation,ForwardStageRunner,describe_child
from tests.integrations.sage.test_mesh_inbox import SENDER,RECIPIENT,SENDER_KEY,KEY,sealed
from tests.test_meshticulum_bench_runtime import Clock,BOOT
from tests.test_meshticulum_private_carrier import carrier_pair


def backend(key):
    state=SimpleNamespace(rows={},live=True,instance_id='b'*64)
    identity=AgentIdentity(key)
    def handler(req):
        assert req.headers['X-Agent-ID']==identity.agent_id and req.headers['X-Signature']
        if req.url.path=='/v1/messages/storage':
            return httpx.Response(200,json=dict(schema='sage.message-storage.v1',instance_id=state.instance_id,
                agent_id=identity.agent_id,encryption_expected=True,vault_active=True,vault_generation='1',
                stable=True,canonical_send_idempotency=True,encrypted_send_admission=True))
        if req.url.path=='/v1/workflows':return httpx.Response(200,json=dict(schema='sage.workflow-journal.v1',items=[],next_after=None,has_more=False))
        identifier=req.url.path.rsplit('/',1)[1]
        if req.method=='GET':return httpx.Response(200 if identifier in state.rows else 404,json=state.rows.get(identifier,{}))
        body=json.loads(req.content);revision=state.rows.get(identifier,{}).get('revision',0)
        if revision!=body['expected_revision']:return httpx.Response(409,json={})
        row=dict(schema='sage.workflow-journal.v1',agent_id=identity.agent_id,record_id=identifier,
            revision=revision+1,kind=body['kind'],payload=body['payload'],trust='untrusted_auxiliary')
        state.rows[identifier]=row;return httpx.Response(200,json=row)
    endpoint='http://127.0.0.1:19100';client=SageClient(endpoint,identity,timeout=2,trust_env=False)
    client._client.close();client._client=httpx.Client(base_url=endpoint,timeout=2,trust_env=False,
        follow_redirects=False,transport=httpx.MockTransport(handler))
    return state,client,endpoint

@pytest.fixture
def operation_pair(request):
    if getattr(request,'param',None)=='real':
        import time
        class RealClock:
            def __init__(self):
                self.epoch=time.time_ns()//1000;self.mono=time.monotonic_ns()//1000;self.offset=0
            def __call__(self):
                now=time.monotonic_ns()//1000+self.offset
                return self.epoch+now-self.mono,now
            def advance(self,us):self.offset+=us
        clock=RealClock()
    else:clock=Clock()
    issued=clock()[0]//1000000
    packet=to_binary(sealed(issued_at=issued,expires_at=issued+100).decode())
    noise=(X25519PrivateKey.generate(),X25519PrivateKey.generate())
    epoch,mono=clock()
    ctx=ForwardContext(SENDER,RECIPIENT,uuid.uuid4().bytes,b'a'*32,epoch,epoch+100000000,64,
        public(noise[0]),public(noise[1]))
    deadline=mono+ctx.expires_epoch_us-epoch
    intention=ReceiveIntention(str(uuid.uuid4()),hashlib.sha256(packet).hexdigest(),len(packet),
        (b'a'*32).hex(),BOOT,ctx.expires_epoch_us,deadline)
    states=[];clients=[];owners=[];budgets=[];operations=[]
    for index,key in enumerate((SENDER_KEY,KEY)):
        state,client,endpoint=backend(key);state.held=False
        @contextmanager
        def lease(state=state):
            assert not state.held
            state.held=True
            try:yield
            finally:state.held=False
        remote=RECIPIENT if index==0 else SENDER
        owner=ForwardReceiver(client,context=ctx,local_initiator=index==0,intention=intention,boot_id=BOOT,
            endpoint=endpoint,signing_key=key,owner_lease=lease,access_check=lambda s=state:s.live,
            storage_check=lambda s=state:s.live,peer_check=lambda p,remote=remote:p==remote,
            dispatch_check=lambda ctx,intent,s=state:s.live,read_check=lambda ctx,intent,s=state:s.live,
            key_check=lambda p,d,s=state:s.live,clock=clock)
        slots=tuple((kind,(at%2==0)==(index==0)) for at,kind in enumerate((1,2,3,4,5)))
        budget=ForwardBudget(owner.store,context=ctx,intention=intention,signing_key=key,boot_id=BOOT,
            limits=Cost(2000,300000000,12000,10000000,10000000),prior_debt=Cost(),slots=slots,owner_lease=lease,
            access_check=lambda s=state:s.live,storage_check=lambda s=state:s.live,
            native_check=lambda c,i,p,s=state:s.live,clock=clock)
        budget.prepare() # Parent binding before crypto claim/H1.
        session=owner.create_session(noise[index])
        op=ForwardOperation(session,budget,sealed_record=packet if index==0 else None,
            receiver=None if index==0 else owner,native_check=lambda c,k,sends,s=state:s.live)
        states.append(state);clients.append(client);owners.append(owner);budgets.append(budget);operations.append(op)
    yield SimpleNamespace(clock=clock,context=ctx,packet=packet,intention=intention,states=states,
        clients=clients,owners=owners,budgets=budgets,operations=operations)
    for op in operations:op.close()
    for owner in owners:owner.close()
    for client in clients:client._client.close()


def stage_pair(tmp_path,p,index,fault='none'):
    source=0 if index%2==0 else 1;dest=1-source
    packet=p.operations[source].packet()
    service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=p.context.expires_epoch_us)
    with carrier_pair(tmp_path/str(index),packet,p.clock,'unit-a' if source==0 else'unit-b',
                      'unit-b' if source==0 else'unit-a',index+1,service=service,
                      original_expires_epoch_us=p.context.expires_epoch_us) as pair:
        flows=[pair.sender,pair.receiver];ops=[p.operations[source],p.operations[dest]];runners=[]
        if fault=='loss':pair.workers[0].drop_kind=FrameKind.DATA
        elif fault=='corrupt':pair.workers[0].corrupt_once=True
        elif fault=='replay':pair.workers[0].replay_once=True
        for n,(flow,op) in enumerate(zip(flows,ops)):
            t=ServiceTransfer(flow);plan=describe_child(t,slot=index,kind=index+1)
            permit=op.budget.pre_admit(plan)
            def retire(transfer,clock=p.clock):
                # Model bounded radio re-ACK window; worker/disarm remains modeled.
                for _ in range(5):transfer.flow.step();clock.advance(50000)
                return True
            r=ForwardStageRunner(t,op,permit,native_check=lambda c,plan,t:True,
                retire_child=retire,payload=packet if n==0 else None)
            r.start();runners.append(r)
        for _ in range(2000):
            runners[0].step();runners[1].step();p.clock.advance(50000)
            if all(r.wire_complete for r in runners):break
        else:raise AssertionError('stage model did not complete')
        snaps=[flow.journal.snapshot() for flow in flows]
        assert all(s['attempts']>0 and s['reserved_estimated_airtime_us']>0 for s in snaps)
        assert all(r.operation.position==index for r in runners)
        for r in runners:r.retire()
        assert all(r.operation.position==index+1 for r in runners)
        return snaps

@pytest.mark.parametrize('fault',['none','loss','corrupt','replay'])
def test_all_five_explicit_stages_prepaid_private_flow_and_one_inbox_admission(tmp_path,operation_pair,fault):
    p=operation_pair;observations=[]
    for index in range(5):observations.extend(stage_pair(tmp_path,p,index,fault))
    assert all(o.position==5 for o in p.operations)
    result=p.operations[1].settle();assert result.status=='admitted'
    for index,budget in enumerate(p.budgets):
        body=p.states[index].rows[budget.record_id]['payload']['body']
        assert len(body['allocations'])==5
        assert sum(r['cost']['radio_attempts'] for r in body['allocations'])==1280
        assert sum(r['cost']['estimated_airtime_us'] for r in body['allocations'])==300000000
    assert len(observations)==10 # Direct hop, both owned endpoint journals per stage.
    assert all('private synthetic' not in json.dumps(s.rows) for s in p.states)


@pytest.mark.parametrize('mutation',['worker','journal','profile','local-peer','boot','deadline','cipher-grant'])
def test_binding_change_from_native_callback_refuses_before_any_frame(tmp_path,operation_pair,mutation):
    from lantern_meshticulum.sage.forward_operation import ForwardOperationError
    p=operation_pair;packet=p.operations[0].packet();op=p.operations[0]
    service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=p.context.expires_epoch_us)
    with carrier_pair(tmp_path/'changed',packet,p.clock,'unit-a','unit-b',31,service=service,
                      original_expires_epoch_us=p.context.expires_epoch_us) as pair:
        t=ServiceTransfer(pair.sender);plan=describe_child(t,slot=0,kind=1);admission=op.budget.pre_admit(plan)
        original=(t.flow.worker,t.flow.journal,t.authority.profile,t.authority.local_peer,t.authority.application_deadline_us)
        def policy(ctx,plan,transfer):
            if mutation=='worker':t.flow.worker=object()
            elif mutation=='journal':t.flow.journal=object()
            elif mutation=='profile':t.authority.profile=object()
            elif mutation=='local-peer':t.authority.local_peer=object()
            elif mutation=='cipher-grant':
                from dataclasses import replace
                t.flow.cipher.grant=replace(t.authority.grant,session=replace(t.authority.grant.session,session_id=b'z'*16))
            elif mutation=='boot':t.authority.plan['host_boot_id']='31f9c178-6090-40d9-9359-ecf1c7b950b1'
            else:t.authority.application_deadline_us+=1
            return True
        runner=ForwardStageRunner(t,op,admission,native_check=policy,retire_child=lambda t:True,payload=packet)
        before=pair.sender.journal.snapshot()
        try:
            with pytest.raises(Exception):runner.start()
            assert not any(w.sent for w in pair.workers)
            assert original[1].snapshot()==before
            assert len(p.states[0].rows[op.budget.record_id]['payload']['body']['allocations'])==1
        finally:
            t.flow.worker,t.flow.journal,t.authority.profile,t.authority.local_peer,t.authority.application_deadline_us=original


def test_receiver_credit_and_final_ack_cannot_dispatch_without_parent_allocation(tmp_path,operation_pair):
    from lantern_meshticulum.sage.forward_budget import Admission
    p=operation_pair;packet=p.operations[0].packet();op=p.operations[1]
    service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=p.context.expires_epoch_us)
    with carrier_pair(tmp_path/'unallocated',packet,p.clock,'unit-a','unit-b',32,service=service,
                      original_expires_epoch_us=p.context.expires_epoch_us) as pair:
        t=ServiceTransfer(pair.receiver);plan=describe_child(t,slot=0,kind=1)
        fake=Admission(op.budget,plan,1);before=pair.receiver.journal.snapshot()
        with pytest.raises(Exception):ForwardStageRunner(t,op,fake,native_check=lambda c,p,t:True,retire_child=lambda t:True)
        assert pair.receiver.journal.snapshot()==before and not any(w.sent for w in pair.workers)


def test_failed_parent_cas_never_invokes_native_open_factory(operation_pair):
    from lantern_meshticulum.sage.forward_operation import prepare_stage
    from lantern_meshticulum.sage.forward_budget import ChildPlan
    p=operation_pair;op=p.operations[0];packet=op.packet();called=[]
    plan=ChildPlan(0,1,True,'a1'*32,hashlib.sha256(packet).hexdigest(),len(packet),'lora','b1'*32,
        BOOT,p.context.expires_epoch_us,p.intention.application_deadline_monotonic_us,Cost(2500,1),512)
    with pytest.raises(Exception):prepare_stage(op,plan,open_child=lambda p:called.append(True),
        native_check=lambda c,p,t:True,retire_child=lambda t:True,payload=packet)
    assert called==[]


def test_parent_allocation_precedes_native_open_and_capsule_hash_is_frozen(operation_pair):
    from lantern_meshticulum.sage.forward_operation import prepare_stage
    from lantern_meshticulum.sage.forward_budget import ChildPlan
    p=operation_pair;op=p.operations[0];packet=op.packet();seen=[]
    plan=ChildPlan(0,1,True,'a1'*32,hashlib.sha256(packet).hexdigest(),len(packet),'lora','b1'*32,
        BOOT,p.context.expires_epoch_us,p.intention.application_deadline_monotonic_us,Cost(1,1),512)
    def open_child(candidate):
        seen.append(p.states[0].rows[op.budget.record_id]['payload']['body']['allocations'][0])
        raise RuntimeError('fixture native open refused')
    with pytest.raises(RuntimeError):prepare_stage(op,plan,open_child=open_child,
        native_check=lambda c,p,t:True,retire_child=lambda t:True,payload=packet)
    assert seen==[plan.to_dict()] and op._closed


def test_caught_stage_reentry_fences_original_runner_before_dispatch(tmp_path,operation_pair):
    p=operation_pair;op=p.operations[0];packet=op.packet();box={}
    service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=p.context.expires_epoch_us)
    with carrier_pair(tmp_path/'nested',packet,p.clock,'unit-a','unit-b',33,service=service,
                      original_expires_epoch_us=p.context.expires_epoch_us) as pair:
        t=ServiceTransfer(pair.sender);plan=describe_child(t,slot=0,kind=1);a=op.budget.pre_admit(plan)
        def policy(ctx,plan,t):
            if 'runner' in box:
                with pytest.raises(Exception):box['runner'].step()
            return True
        runner=ForwardStageRunner(t,op,a,native_check=policy,retire_child=lambda t:True,payload=packet);box['runner']=runner
        with pytest.raises(Exception):runner.start()
        assert not any(w.sent for w in pair.workers) and op._closed


def test_allocation_callback_close_prevents_native_factory_even_after_debt_commit(operation_pair):
    from lantern_meshticulum.sage.forward_operation import prepare_stage
    from lantern_meshticulum.sage.forward_budget import ChildPlan
    p=operation_pair;op=p.operations[0];packet=op.packet();called=[]
    plan=ChildPlan(0,1,True,'a1'*32,hashlib.sha256(packet).hexdigest(),len(packet),'lora','b1'*32,
        BOOT,p.context.expires_epoch_us,p.intention.application_deadline_monotonic_us,Cost(1,1),512)
    def native(ctx,intention,plan):op.close();return True
    op.budget._native=native
    with pytest.raises(Exception):prepare_stage(op,plan,open_child=lambda p:called.append(True),
        native_check=lambda c,p,t:True,retire_child=lambda t:True,payload=packet)
    assert called==[] and len(p.states[0].rows[op.budget.record_id]['payload']['body']['allocations'])==1


def test_direct_accept_without_complete_transfer_cannot_advance_crypto(tmp_path,operation_pair):
    p=operation_pair;op=p.operations[1];packet=p.operations[0].packet()
    service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=p.context.expires_epoch_us)
    with carrier_pair(tmp_path/'early',packet,p.clock,'unit-a','unit-b',34,service=service,
                      original_expires_epoch_us=p.context.expires_epoch_us) as pair:
        t=ServiceTransfer(pair.receiver);plan=describe_child(t,slot=0,kind=1);a=op.budget.pre_admit(plan)
        r=ForwardStageRunner(t,op,a,native_check=lambda c,p,t:True,retire_child=lambda t:True)
        before=copy.deepcopy(p.states[1].rows)
        with pytest.raises(Exception):op.accept(packet,r)
        assert p.states[1].rows==before and op.session.state=='closed'
        assert not any(w.sent for w in pair.workers)


@pytest.mark.parametrize('inside_call',[2,3])
def test_one_shot_budget_close_at_inside_lease_pre_dispatch_gate_cannot_send(tmp_path,operation_pair,inside_call):
    p=operation_pair;op=p.operations[0];packet=op.packet();calls=[]
    service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=p.context.expires_epoch_us)
    with carrier_pair(tmp_path/'oneshot',packet,p.clock,'unit-a','unit-b',35,service=service,
                      original_expires_epoch_us=p.context.expires_epoch_us) as pair:
        t=ServiceTransfer(pair.sender);plan=describe_child(t,slot=0,kind=1);a=op.budget.pre_admit(plan)
        def native(ctx,plan,t):
            calls.append(True)
            if len(calls)==inside_call:op.budget.close()
            return True
        r=ForwardStageRunner(t,op,a,native_check=native,retire_child=lambda t:True,payload=packet)
        with pytest.raises(Exception):r.start()
        assert not any(w.sent for w in pair.workers) and op._closed


def test_closed_operation_cannot_settle_while_fresh_receiver_remains_explicit_recovery(operation_pair):
    p=operation_pair;op=p.operations[1];before=copy.deepcopy(p.states[1].rows)
    op.position=5;op.close()
    with pytest.raises(Exception):op.settle()
    assert p.states[1].rows==before


@pytest.mark.parametrize('operation_pair',['real'],indirect=True)
def test_five_stages_alternate_modeled_radio_and_actual_loopback_tcp_with_both_byte_budgets(tmp_path,operation_pair):
    from tests.test_ip_transport import pair as tcp_pair
    import time
    p=operation_pair;ip_records=[]
    for index in range(5):
        if index%2==0:
            stage_pair(tmp_path,p,index)
            continue
        source=0 if index%2==0 else 1;dest=1-source;packet=p.operations[source].packet()
        with tcp_pair(tmp_path/str(index),packet,'MPF2.record',session_id=bytes([index+1])*16,
                      generation=bytes([index+11])*16,expires=p.context.expires_epoch_us,
                      deadline_us=p.intention.application_deadline_monotonic_us) as (flows,workers,stores,clock):
            runners=[]
            for n,op in enumerate((p.operations[source],p.operations[dest])):
                t=ServiceTransfer(flows[n]);plan=describe_child(t,slot=index,kind=index+1)
                admission=op.budget.pre_admit(plan)
                r=ForwardStageRunner(t,op,admission,native_check=lambda c,p,t:True,
                    retire_child=lambda t:True,payload=packet if n==0 else None)
                r.start();runners.append(r)
            for _ in range(3000):
                runners[0].step();runners[1].step()
                if all(r.wire_complete for r in runners):break
                time.sleep(.001)
            else:raise AssertionError('bounded actual TCP stage did not finish')
            for r in runners:r.retire()
            for w in workers:
                s=w.journal.snapshot();assert s['attempts']>0 and s['received_stream_bytes']>0
                assert s['lanes']['ACK']>0 or s['lanes']['DATA']>0
                ip_records.append(s)
    assert len(ip_records)==4 and p.operations[1].settle().status=='admitted'
    for index,b in enumerate(p.budgets):
        rows=p.states[index].rows[b.record_id]['payload']['body']['allocations']
        assert len(rows)==5 and sum(r['cost']['estimated_airtime_us'] for r in rows)==180000000
        assert sum(r['cost']['tx_stream_bytes'] for r in rows)==2097152
        assert sum(r['cost']['rx_stream_bytes'] for r in rows)==2097152


def test_post_prepay_storage_epoch_change_refuses_native_open_but_retains_debt(operation_pair):
    from lantern_meshticulum.sage.forward_operation import prepare_stage
    from lantern_meshticulum.sage.forward_budget import ChildPlan
    p=operation_pair;op=p.operations[0];packet=op.packet();called=[]
    plan=ChildPlan(0,1,True,'a1'*32,hashlib.sha256(packet).hexdigest(),len(packet),'lora','b1'*32,
        BOOT,p.context.expires_epoch_us,p.intention.application_deadline_monotonic_us,Cost(1,1),512)
    def native(ctx,kind,sends):
        rows=p.states[0].rows[op.budget.record_id]['payload']['body']['allocations']
        if rows:p.states[0].instance_id='c'*64
        return True
    op._native=native
    with pytest.raises(Exception):prepare_stage(op,plan,open_child=lambda p:called.append(True),
        native_check=lambda c,p,t:True,retire_child=lambda t:True,payload=packet)
    assert called==[] and len(p.states[0].rows[op.budget.record_id]['payload']['body']['allocations'])==1
