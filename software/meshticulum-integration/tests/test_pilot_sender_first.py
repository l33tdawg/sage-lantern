"""Finite orchestration/owned readiness source controls, no physical claim."""
from types import SimpleNamespace
import json
import pytest
from lantern_meshticulum.bench.pilot_controller import Controller
from lantern_meshticulum.bench import node, pilot_job
from meshticulum.bench.wire import WireError
from tests.test_pilot_controller import Clock, ModelOperator, model_parent, controller as legacy_controller, PEERS, BOOTS, PERMISSION, MANIFEST, CHUNK, REQUEST, RESPONSE
from meshticulum.resources import resource
REQUEST32=resource.encode_request('/packs/'+MANIFEST+'/chunks/'+CHUNK+'.json',0,32740,public32=True)


def make(fault=None):
    clock=Clock()
    class Op(ModelOperator):
        def status(self,unit,run):
            value=super().status(unit,run)
            value['radio_receive_checked']=fault!='missing_receive'
            if fault=='wrong_identity':value['unit']='unit-b'
            if fault=='late':clock.now=201
            if fault=='exited_after_ready':self.handles[(unit,run)].code=1
            return value
    op=Op(clock,fault)
    controller=Controller(op,campaign_id='d'*32,resource_sha256=MANIFEST,expires_epoch_us=1160000000,
        handoff_deadline_monotonic=200,peers=PEERS,boots=BOOTS,parent=model_parent(clock,qualification_kind='c-bulk32-v1'),radio_permission=PERMISSION,
        clock=clock.mono,epoch=clock.epoch,sleep=clock.sleep,lora_sender_first=True,public32=True)
    return controller,op,clock


def test_opt_in_lora_sender_is_ready_before_receiver_starts_same_pair_and_full_paid_caps():
    pilot,op,clock=make();pilot.link('unit-a','unit-c','lora','MR1.request',REQUEST32)
    assert [e[1] for e in op.events if e[0]=='start']==['unit-a','unit-c']
    assert pilot.links[0]['launch_order']==['unit-a','unit-c']
    assert len([e for e in op.events if e[0]=='prepare'])==2
    assert len(pilot.links[0]['prepared_parent_claims'])==2
    assert pilot.links[0]['phase']=='complete' and len(pilot.links[0]['outcomes'])==2


def test_opt_in_keeps_tcp_receiver_first():
    pilot,op,clock=legacy_controller();pilot.link('unit-c','unit-a','tcp','MR1.request',REQUEST)
    assert [e[1] for e in op.events if e[0]=='start']==['unit-a','unit-c']


@pytest.mark.parametrize('fault',['missing_receive','wrong_identity','not_ready','late','exited_after_ready'])
def test_missing_actual_receive_identity_or_original_deadline_never_starts_receiver(fault):
    pilot,op,clock=make(fault)
    with pytest.raises((WireError,RuntimeError)):pilot.link('unit-a','unit-c','lora','MR1.request',REQUEST32)
    assert [e[1] for e in op.events if e[0]=='start']==['unit-a']
    assert len([e for e in op.events if e[0]=='revoke'])==2
    assert len([e for e in op.events if e[0]=='stop'])==2


def test_mode_mutation_refuses_before_preparation():
    pilot,op,clock=make();pilot.lora_sender_first=False
    with pytest.raises(WireError,match='startup_order_changed'):pilot.link('unit-a','unit-c','lora','MR1.request',REQUEST32)
    assert op.events==[]


def test_receive_marker_is_bound_to_actual_job_identity_not_live_boolean(tmp_path,monkeypatch):
    path=tmp_path/'run';path.mkdir(mode=0o700)
    monkeypatch.setattr(pilot_job,'kernel',lambda _:dict(start_ticks=17,state='R',command_sha256='a'*64))
    value=pilot_job.record(path,unit='unit-c',boot=BOOTS['unit-c'],run_id='d'*32,grant_digest='b'*64,transport='lora')
    pilot_job.ready(path,value,radio_receive_checked=True)
    assert pilot_job._ready(path,value,detail=True)==(True,True)
    record=json.loads((path/'radio-ready.json').read_bytes());record['run_id']='e'*32
    (path/'radio-ready.json').write_text(json.dumps(record))
    with pytest.raises(WireError):pilot_job._ready(path,value,detail=True)


def test_legacy_ready_has_no_receive_checked_marker(tmp_path,monkeypatch):
    path=tmp_path/'run';path.mkdir(mode=0o700)
    monkeypatch.setattr(pilot_job,'kernel',lambda _:dict(start_ticks=17,state='R',command_sha256='a'*64))
    value=pilot_job.record(path,unit='unit-c',boot=BOOTS['unit-c'],run_id='d'*32,grant_digest='b'*64,transport='lora')
    pilot_job.ready(path,value)
    assert pilot_job._ready(path,value,detail=True)==(True,False)

from meshticulum.bench.service_runner import ServiceRunner
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.wire import FrameKind
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.test_meshticulum_bench_runtime import Clock as FlowClock
from tests.service_helpers import scope


def test_real_flow_aead_sender_waits_for_matching_receiver_credit_before_any_data(tmp_path):
    clock=FlowClock()
    with carrier_pair(tmp_path/'pair',REQUEST,clock,'unit-a','unit-b',95,service=scope('MR1.request',clock)) as pair:
        sender=ServiceRunner(ServiceTransfer(pair.sender),payload=REQUEST)
        receiver=ServiceRunner(ServiceTransfer(pair.receiver));sender.start()
        clock.advance(4100000)  # Delayed setup consumes original life; no receiver startup is spent.
        assert node._sender_receive_ready(sender,pair.sender,pair.workers[0],pair.sender.authority,lambda:True)
        assert pair.sender.flow._peer_credit==0 and not pair.workers[0].sent
        receiver.start()
        for _ in range(200):
            receiver.step();sender.step();clock.advance(5000)
            if sender.wire_complete and receiver.wire_complete:break
        assert sender.wire_complete and receiver.wire_complete and receiver.public_payload==REQUEST
        assert pair.sender.flow.acked_frames()==1 and not pair.sender.flow.retransmits
        assert pair.receiver.flow._startup_announce_left==7
        assert pair.sender.journal.snapshot()['attempts']==1
        assert pair.sender.authority.profile.max_boot_attempts==256
        assert pair.sender.authority.profile.lease_ms==90000


def test_real_late_eighth_startup_ack_reproduces_cutoff_before_first_data(tmp_path):
    clock=FlowClock()
    with carrier_pair(tmp_path/'pair',REQUEST,clock,'unit-a','unit-b',96,service=scope('MR1.request',clock)) as pair:
        sender=ServiceRunner(ServiceTransfer(pair.sender),payload=REQUEST)
        receiver=ServiceRunner(ServiceTransfer(pair.receiver));sender.start();receiver.start()
        for index in range(8):
            receiver.step()
            if index<7:pair.workers[0].incoming.clear()  # Sender's radio isn't listening yet.
            clock.advance(500000)
        assert len(pair.workers[1].sent)==8 and len(pair.workers[0].incoming)==1
        with pytest.raises(WireError,match='startup_grant_unanswered'):receiver.step()
        assert pair.receiver.flow.failure=='startup_grant_unanswered' and not pair.receiver.received
        sender.step()
        assert len(pair.workers[0].sent)==1 and not pair.sender.flow.acked_frames()


@pytest.mark.parametrize('fault',['owner','authority','receive','callback_owner','callback_expiry'])
def test_receive_pass_failure_or_owner_change_refuses_readiness_without_data(tmp_path,fault):
    clock=FlowClock()
    with carrier_pair(tmp_path/'pair',REQUEST,clock,'unit-a','unit-b',97,service=scope('MR1.request',clock)) as pair:
        sender=ServiceRunner(ServiceTransfer(pair.sender),payload=REQUEST);sender.start()
        current=[True]
        original=pair.workers[0].receive
        def receive(*args,**kwargs):
            value=original(*args,**kwargs)
            if fault=='receive':raise RuntimeError('model owned receive refused')
            if fault=='callback_owner':pair.sender.worker=SimpleNamespace(fenced=False)
            if fault=='callback_expiry':current[0]=False
            return value
        pair.workers[0].receive=receive
        if fault=='owner':pair.sender.worker=object()
        if fault=='authority':current[0]=False
        with pytest.raises((WireError,RuntimeError)):
            node._sender_receive_ready(sender,pair.sender,pair.workers[0],pair.sender.authority,lambda:current[0])
        assert not pair.workers[0].sent and not pair.sender.journal.snapshot()['attempts']


def test_old_unconditional_ready_pass_reproduces_data_then_refusal_with_genuine_queued_credit(tmp_path):
    clock=FlowClock()
    with carrier_pair(tmp_path/'pair',REQUEST,clock,'unit-a','unit-b',98,service=scope('MR1.request',clock)) as pair:
        sender=ServiceRunner(ServiceTransfer(pair.sender),payload=REQUEST)
        receiver=ServiceRunner(ServiceTransfer(pair.receiver));sender.start();receiver.start();receiver.step()
        assert pair.workers[0].incoming and pair.sender.flow._peer_credit==0
        with pytest.raises(WireError,match='dispatched_before_receive_readiness'):
            node._sender_receive_ready(sender,pair.sender,pair.workers[0],pair.sender.authority,lambda:True)
        assert len(pair.workers[0].sent)==pair.sender.journal.snapshot()['attempts']==1


def test_default_ready_path_leaves_valid_credit_queued_and_normal_loop_completes(tmp_path):
    clock=FlowClock()
    with carrier_pair(tmp_path/'pair',REQUEST,clock,'unit-a','unit-b',99,service=scope('MR1.request',clock)) as pair:
        sender=ServiceRunner(ServiceTransfer(pair.sender),payload=REQUEST)
        receiver=ServiceRunner(ServiceTransfer(pair.receiver));sender.start();receiver.start();receiver.step()
        queued=list(pair.workers[0].incoming)
        assert node._sender_readiness_pass(False,sender,pair.sender,pair.workers[0],pair.sender.authority,lambda:True) is False
        assert list(pair.workers[0].incoming)==queued and not pair.workers[0].sent
        assert pair.sender.journal.snapshot()['attempts']==0
        clock.advance(5000)
        for _ in range(100):
            sender.step();receiver.step();clock.advance(5000)
            if sender.wire_complete and receiver.wire_complete:break
        assert sender.wire_complete and receiver.wire_complete and receiver.public_payload==REQUEST
        assert pair.sender.journal.snapshot()['attempts']==1


def test_explicit_native_public32_ready_path_performs_owned_zero_credit_receive(tmp_path):
    clock=FlowClock();service=dict(scope('MR1.request',clock),public_response_max_bytes=32768)
    with carrier_pair(tmp_path/'pair',REQUEST,clock,'unit-a','unit-b',100,service=service) as pair:
        sender=ServiceRunner(ServiceTransfer(pair.sender),payload=REQUEST);sender.start()
        assert pair.sender.authority.public32
        assert node._sender_readiness_pass(True,sender,pair.sender,pair.workers[0],pair.sender.authority,lambda:True)
        assert not pair.workers[0].sent and pair.sender.journal.snapshot()['attempts']==0


def test_native_opt_in_refuses_ordinary_service_before_extra_receive_step(tmp_path):
    clock=FlowClock()
    with carrier_pair(tmp_path/'pair',REQUEST,clock,'unit-a','unit-b',101,service=scope('MR1.request',clock)) as pair:
        sender=ServiceRunner(ServiceTransfer(pair.sender),payload=REQUEST);sender.start()
        with pytest.raises(WireError,match='requires_public32_sender'):
            node._sender_readiness_pass(True,sender,pair.sender,pair.workers[0],pair.sender.authority,lambda:True)
        assert not pair.workers[0].sent


@pytest.mark.parametrize('transport,mode,unit,expected',[('lora',True,'unit-c',True),
    ('lora',True,'unit-a',False),('lora',False,'unit-c',False),('tcp',False,'unit-c',False)])
def test_actual_ssh_start_forwards_native_opt_in_only_to_explicit_lora_sender(monkeypatch,transport,mode,unit,expected):
    from lantern_meshticulum.bench import pilot_ssh
    from tests.test_pilot_ssh import operator
    captured=[]
    monkeypatch.setattr(pilot_ssh,'Job',lambda argv,**kwargs:captured.append(argv) or object())
    op=operator();p=dict(run_id='e'*32,sender='unit-c',receiver='unit-a',transport=transport,sender_receive_ready=mode)
    op.start(unit,p)
    assert ('--sender-receive-ready' in captured[0][-1]) is expected


def test_controller_paired_mode_is_explicit_and_transport_bound():
    pilot,op,clock=make();pilot.link('unit-a','unit-c','lora','MR1.request',REQUEST32)
    assert pilot.links[0]['sender_receive_ready'] is True
    pilot,op,clock=legacy_controller();pilot.link('unit-c','unit-a','tcp','MR1.request',REQUEST)
    assert pilot.links[0]['sender_receive_ready'] is False
