"""Real protected files/SQLite/signatures; RF and OS process facts are modeled."""
import base64
from collections import deque
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from contextlib import contextmanager

from meshticulum.bench.authority import RunAuthority, revoke
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.service_runner import ServiceRunner
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.serial_protocol import Outcome, RawReceive
from meshticulum.bench.serial_worker import AttemptResult
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import FrameKind, HEADER, SessionCipher
from meshticulum.bench.gfsk_profile import radio_airtime_us
from meshticulum.resources import knowledge_pack as kp, lamplighter as ll
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from lantern_meshticulum.bench import fast_bundle as fast, pilot_bundle as bundle, pilot_job
from lantern_meshticulum.bench import pilot_resource_node as resource_node
from lantern_meshticulum.bench.profiles import GFSK250_NAME, admission_for
from lantern_meshticulum.bench.pilot_native_caps import _open_existing
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps, Cost, canonical
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_provision import provision
from lantern_meshticulum.ip.node import _write
from tests import test_native_mb1_bundle100k as legacy
from tests.test_pilot_bundle import data, postterminal_fixture
from tests.test_public32_pilot import tmp_path, protected_source, clock, facts, PEERS, BOOT


@pytest.fixture
def fast_data(data):
    payload,chunks,old,p=data
    value=bundle.context_value(base64.b64decode(old['manifest_base64']),base64.b64decode(old['signature_base64']),
        old['trust'],old['manifest_sha256'],chunks=chunks,
        policy_clock=(p.issued_epoch_us,p.original_monotonic_us,p.issued_epoch_us+1350000000,p.original_monotonic_us+1350000000),
        stages=[dict(campaign_id=row['campaign_id']) for row in old['stages']],qualification_kind=fast.KIND)
    p=replace(p,qualification_kind=fast.KIND,expires_epoch_us=value['expires_epoch_us'],
        deadline_monotonic_us=value['deadline_monotonic_us'],bundle_context_sha256=kp.sha256_hex(canonical(value)))
    return payload,chunks,value,p


@pytest.fixture
def native(fast_data,tmp_path,protected_source,monkeypatch):
    from lantern_meshticulum.ip import node
    payload,chunks,value,p=fast_data
    monkeypatch.setattr(legacy,'GFSK_MB1_BUNDLE_KIND',fast.KIND)
    monkeypatch.setattr(legacy,'GFSK_NAME',GFSK250_NAME);monkeypatch.setattr(legacy,'mode',fast)
    base=tmp_path/'native';base.mkdir(mode=0o700)
    roots,p=legacy.protected_units(base,p,value,monkeypatch)
    now=[clock()];local_clock=lambda:now[0]
    monkeypatch.setattr(node,'_facts',facts);monkeypatch.setattr(resource_node,'_facts',facts)
    monkeypatch.setattr(resource_node,'clock',local_clock);monkeypatch.setattr(resource_node,'STATE',roots['unit-a'])
    monkeypatch.setattr(pilot_job,'inspect',lambda directory,**kw:dict(live=False,job_record_found=True,pid=17,start_ticks=13))
    c=bundle.BundleContext(value,p)
    for unit in ('unit-a','unit-c'):
        public=roots[unit]/'public'/value['manifest_sha256'];public.mkdir(parents=True,mode=0o700)
        public.chmod(0o700);public.parent.chmod(0o700)
        directory=public/('received' if unit=='unit-a' else 'pack');directory.mkdir(mode=0o700)
        _write(public/'trust.json',canonical(value['trust']));_write(directory/'manifest.json',c.manifest_raw)
        _write(directory/'manifest.sig.json',c.signature_raw)
        if unit=='unit-c':
            (directory/'chunks').mkdir(mode=0o700)
            for name,raw in chunks.items():_write(directory/'chunks'/(name+'.json'),raw)
    cache=ll.VerifiedCache(roots['unit-c']/'public'/value['manifest_sha256']/'pack',value['trust'],value['manifest_sha256'])
    control=tmp_path/'controller';control.mkdir(mode=0o700)
    parent=ParentCaps.enroll(control/'caps.sqlite',p,clock=local_clock,boot_reader=lambda:BOOT)
    yield dict(payload=payload,context=c,roots=roots,policy=p,now=now,clock=local_clock,parent=parent,cache=cache)
    assert parent.close()


def admit(n,index,proof=None,expiry=None):
    issued=n['now'][0][0];expires=expiry or issued+180000000;result={}
    for unit in ('unit-c','unit-a'):
        caps=_open_existing(n['roots'][unit],n['policy'].parent_id,facts(unit),n['clock'])
        try:result[unit]=fast.admit_stage(caps,index,issued,expires,proof,clock=n['clock'])
        finally:assert caps.close()
    return expires,result


def pair(n,index,expiry,response=None,source=None):
    c=n['context'];request,_=c.exchange(index);body=request if response is None else response
    return pair_packages(campaign_id=c.value['stages'][index]['campaign_id'],resource_sha256=c.value['manifest_sha256'],
        application_expires_epoch_us=expiry,sender='unit-a' if response is None else 'unit-c',
        receiver='unit-c' if response is None else 'unit-a',transport='lora',format='MR1.request' if response is None else 'MR1.response',
        descriptor=dict(bytes=len(body),sha256=kp.sha256_hex(body)),source=None if response is None else dict(kind='served_public_chunk',request_run_id=source),
        request=request if response is None else None,peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=n['now'][0][0],
        radio_permission=dict(country='MY',antenna_confirmed=True),public32=True,profile=GFSK250_NAME,
        mb1_descriptor_sha256=c.value['transport_descriptor_sha256'])


def wrap(n,current):
    parent=n['parent'];permit=parent.prepay_pair(tuple(current['packages'].values()));children=parent.consume(permit)
    for unit,packet in current['packages'].items():
        packet.update(schema='meshticulum.public-pilot-package.v2',parent=dict(schema='meshticulum.public-parent-allocation.v1',
            parent_id=parent.policy.parent_id,policy_digest=parent.policy.digest,allocation_id=permit.key,
            child=next(child for child in children if child.unit==unit).to_dict()))


def modeled_result(n,current,body):
    wrap(n,current)
    for unit in ('unit-a','unit-c'):
        value=postterminal_fixture(n['roots'][unit],n['policy'],current,unit,body,n['clock'])
        target=n['roots'][unit]/'runs'/current['run_id']
        plan=json.loads((target/'grant.json').read_bytes())
        value.update(object=plan['object'],grant_context_sha256=value['parent']['grant_context_sha256'],
            attempt_journal=dict(fenced=False),flow=dict(complete=True),native_job=dict(pid=17,start_ticks=13),
            end_monotonic_us=n['now'][0][1],stop_requested=False)
        (target/'result.json').write_bytes(canonical(value))


def one_part(n,index,previous=None):
    expiry,_=admit(n,index,previous)
    request,_=n['context'].exchange(index);response=n['context'].transport.response(index,n['cache'])
    request_pair=pair(n,index,expiry);response_pair=pair(n,index,expiry,response,request_pair['run_id'])
    modeled_result(n,request_pair,request);modeled_result(n,response_pair,response)
    verified=resource_node.verify('unit-a',n['context'].value['stages'][index]['campaign_id'],request_pair['run_id'],response_pair['run_id'])
    for current in (request_pair,response_pair):
        for unit in ('unit-a','unit-c'):revoke(n['roots'][unit]/'runs'/current['run_id'])
    return verified,request_pair,response_pair


def test_all_five_progress_admissions_verify_same100k_without_fixed_wait_or_refund(native):
    n=native;previous=None;expires=[]
    original=n['context'].raw
    for index in range(5):
        n['now'][0]=tuple(v+1000000 for v in n['now'][0])
        verified,_,_=one_part(n,index,previous);previous=verified['fast_progress_proof']
        expires.append(verified['original_application_expires_epoch_us'])
    caps=_open_existing(n['roots']['unit-a'],n['policy'].parent_id,facts('unit-a'),n['clock'])
    try:done=bundle.finish_native_bundle(caps,state=n['roots']['unit-a'],clock=n['clock'])
    finally:assert caps.close()
    assert done['whole_complete'] and done['useful_decoded_bytes']==100000
    assert done['whole_file_sha256']==kp.sha256_hex(n['payload']) and n['context'].raw==original
    assert expires==sorted(expires) and expires[-1]-expires[0]==4000000
    for unit in ('unit-a','unit-c'):
        caps=_open_existing(n['roots'][unit],n['policy'].parent_id,facts(unit),n['clock'])
        try:snapshot=caps.snapshot()
        finally:assert caps.close()
        assert snapshot['claims']==snapshot['prepared']==snapshot['starts']==10
        assert n['policy'].unit(unit).limits==n['policy'].unit(unit).prior_debt.plus(Cost(2560,600000000))
    assert n['policy'].unit('unit-b').limits==n['policy'].unit('unit-b').prior_debt


@pytest.mark.parametrize('fault',['skip','duplicate','extended','expired','wrong_kind','stage_mutation'])
def test_stage_refusals_preserve_original_clock_and_never_create_new_radio_grant(native,fault):
    n=native;p=n['policy'];root=n['roots']['unit-a']
    if fault in ('duplicate','stage_mutation'):
        expiry,_=admit(n,0)
        path=root/'parents'/p.parent_id/'fast-stage-0.json';before=path.read_bytes()
    caps=_open_existing(root,p.parent_id,facts('unit-a'),n['clock'])
    try:
        if fault=='stage_mutation':
            value=json.loads(before);value['expires_epoch_us']+=1;path.write_bytes(canonical(value))
            assert caps.current() is False
        else:
            if fault=='expired':n['now'][0]=(p.expires_epoch_us,p.deadline_monotonic_us)
            if fault=='wrong_kind':caps.policy=replace(p,qualification_kind='c-gfsk250-mb1-bundle100k-v1')
            with pytest.raises(Exception):fast.admit_stage(caps,1 if fault=='skip' else 0,n['now'][0][0],
                n['now'][0][0]+(180000001 if fault=='extended' else 180000000),None,clock=n['clock'])
            if fault=='duplicate':assert path.read_bytes()==before
        assert not (root/'runs'/'unused').exists()
    finally:caps.close()


@pytest.mark.parametrize('fault',['missing_mac','wrong_mac','wrong_object','wrong_campaign','not_retired','not_complete','not_verified'])
def test_next_stage_requires_exact_verified_previous_part_and_both_retired_links(native,fault):
    n=native;verified,request,response=one_part(n,0);proof=deepcopy(verified['fast_progress_proof'])
    target=n['roots']['unit-a']/'runs'/response['run_id']
    if fault=='missing_mac':proof.pop('mac')
    if fault=='wrong_mac':proof['mac']='0'*64
    if fault=='wrong_object':proof['body']['object']['sha256']='0'*64
    if fault=='wrong_campaign':proof['body']['campaign_id']='f'*32
    if fault=='not_retired':(target/'active').write_bytes(b'active\n')
    if fault=='not_complete':
        value=json.loads((target/'result.json').read_bytes());value['complete']=False;(target/'result.json').write_bytes(canonical(value))
    if fault=='not_verified':
        value=json.loads((target/'verified-piece.json').read_bytes());value['publisher_verified']=False;(target/'verified-piece.json').write_bytes(canonical(value))
    caps=_open_existing(n['roots']['unit-a'],n['policy'].parent_id,facts('unit-a'),n['clock'])
    try:
        with pytest.raises(Exception):fast.admit_stage(caps,1,n['now'][0][0],n['now'][0][0]+180000000,proof,clock=n['clock'])
        assert len(caps._validate()[0])==2
        assert not (caps.path.parent/'fast-stage-1.json').exists()
    finally:assert caps.close()


def test_unbound_fast_native_source_refuses(monkeypatch):
    from types import MappingProxyType
    monkeypatch.setattr(fast,'PHYSICAL_BINDINGS',MappingProxyType(dict(fast.PHYSICAL_BINDINGS,
        scope_sha256=None,history_receipt_sha256=None,preceding_parent=None)))
    with pytest.raises(Exception,match='pending_fast_bundle_scope'):fast.require_bindings()


def test_real_campaign_constructor_and_first_admissions_use_ssh_encoder(native,monkeypatch):
    from lantern_meshticulum.bench.pilot_controller import Controller
    from lantern_meshticulum.bench.pilot_ssh import SSHOperator, INVOKE
    from tests.test_pilot_ssh import config
    n=native;p=n['policy'];context=n['context'];cfg,source=config()
    commitment=bundle.public_context_commitment(context.value,parent_id=p.parent_id)
    cfg.update(schema=bundle.COMMITTED_CONTRACTS[fast.KIND][5],bundle_parent_id=p.parent_id,
        bundle_context=context.value,bundle_capture_commitment=commitment,
        handoff_end_epoch_us=commitment['phase_end_epoch_us'])
    for row in cfg['units'].values():row['host_boot_id']=BOOT
    mono=lambda:n['now'][0][1]/1000000;epoch=lambda:n['now'][0][0]
    operator=SSHOperator(cfg,provision_source=source,clock=mono,epoch=epoch)
    calls=[]
    def command(unit,argv,schema,**kwargs):
        assert argv[4]==INVOKE and argv[6]==cfg['manifest_sha256']
        calls.append((unit,argv[7]))
        if argv[7]=='pilot-bundle-status':
            return dict(schema=schema,unit=unit,parent_id=p.parent_id,
                bundle_context_sha256=p.bundle_context_sha256,manifest_sha256=context.value['manifest_sha256'],
                whole_file_sha256=context.value['whole_sha256'],whole_bytes=100000,
                original_whole_expires_epoch_us=context.value['expires_epoch_us'],
                all_parts_present=False,whole_complete=False,verified_parts=[])
        assert argv[7]=='pilot-fast-admit'
        assert json.loads(base64.b64decode(argv[argv.index('--proof-base64')+1],validate=True)) is None
        return dict(schema=schema,unit=unit,parent_id=p.parent_id,radio_opened=False,arm_commands=0,tx_commands=0,
            admission=dict(context.value['stages'][0],schema=fast.STAGE,index=0,
                bundle_context_sha256=p.bundle_context_sha256,issued_epoch_us=epoch(),expires_epoch_us=epoch()+180000000))
    monkeypatch.setattr(operator,'_command',command)
    guard_path=Path(__file__).parents[3]/'firmware/meshticulum-bench/pair_launch_guard_sender_first.py'
    spec=importlib.util.spec_from_file_location('constructor_launch_guard',guard_path)
    guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    guard.install(operator,lambda unit:pytest.fail('no endpoint starts in this control'),lora_sender_first=True)
    class BeforeRadio(RuntimeError): pass
    def no_radio(self,*args,**kwargs):raise BeforeRadio()
    monkeypatch.setattr(Controller,'direct_piece',no_radio)
    original=(p.expires_epoch_us,p.deadline_monotonic_us,context.raw)
    campaign=fast.FastBundleCampaign(operator,n['parent'],context.value,peers=PEERS,
        boots={u:BOOT for u in PEERS},handoff_deadline_monotonic=p.deadline_monotonic_us/1000000,
        clock=mono,epoch=epoch,sleep=lambda seconds:pytest.fail('unexpected wait'),lora_sender_first=True)
    with pytest.raises(BeforeRadio):campaign.transfer_part(0,context.transport.response(0,n['cache']))
    assert calls==[('unit-a','pilot-bundle-status'),('unit-c','pilot-fast-admit'),('unit-a','pilot-fast-admit')]
    assert len(campaign.controllers)==1 and campaign.links==campaign.parts==[]
    assert n['parent'].snapshot()['allocations']==0 and operator.jobs=={} and operator.connections is None
    assert (p.expires_epoch_us,p.deadline_monotonic_us,context.raw)==original


def test_fast_runner_250_inspection_accepts_actual_firmware_and_refuses125_or_unproven_capability(monkeypatch):
    bench=Path(__file__).parents[3]/'firmware/meshticulum-bench';monkeypatch.syspath_prepend(str(bench))
    path=bench/'run_public_gfsk250_mb1_fast_bundle100k.py';spec=importlib.util.spec_from_file_location('fast_runner',path)
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    radio=dict(firmware='mtb1-def16514fc',mcu_boot='000008f6a51b5bf80000000000000031',
        usb_serial='F8:5B:1B:A5:F6:08',disarmed=True,radio_modem='gfsk250',boot_attempts=0,boot_reserved_estimated_airtime_us=0)
    cap=dict(verified=True,radio_modem='gfsk250',record_version=2,mcu_boot=radio['mcu_boot'],firmware=radio['firmware'])
    actual=dict(radio=radio,radio_capability=cap,arm_commands=0,tx_commands=0)
    assert runner.fresh250_status(actual,radio['firmware'],radio)
    cold=deepcopy(actual);cold['radio']['radio_modem']='lora'
    assert runner.fresh250_status(cold,radio['firmware'],radio)
    cold['radio'].pop('radio_modem')
    assert runner.fresh250_status(cold,radio['firmware'],radio)
    wrong=deepcopy(actual);wrong['radio']['radio_modem']='gfsk125'
    with pytest.raises(Exception):runner.fresh250_status(wrong,radio['firmware'],radio)
    wrong=deepcopy(actual);wrong['radio_capability']['verified']=False
    with pytest.raises(Exception):runner.fresh250_status(wrong,radio['firmware'],radio)
    with pytest.raises(Exception):runner.fresh250_status(actual,'mtb1-b433927ba6',radio)
    assert all(set(stage)=={'campaign_id'} for stage in runner.planned_stages(123))


class ModelRadio:
    """Only RF is modeled; AEAD, flow, authority and attempt journals are real."""
    def __init__(self,journal,clock,profile):
        self.journal,self.clock,self.profile=journal,clock,profile
        self.incoming=deque();self.sent=[];self.peer=None;self.owned=True;self.fenced=False
    def transmit(self,packet):
        cost=radio_airtime_us(self.profile,len(packet));attempt=self.journal.reserve(packet,cost)
        self.journal.settle(attempt,packet,'RF_DONE','source_RF_model')
        self.sent.append(packet);self.peer.incoming.append(packet)
        return AttemptResult(attempt,'RF_DONE',cost,None)
    def receive(self,timeout=0):
        if self.incoming:return self.clock()[1]/1000000,RawReceive(-70.,3.,self.incoming.popleft())


@contextmanager
def live_request(n,*,remaining_us=180000000):
    expiry,_=admit(n,0,expiry=n['now'][0][0]+remaining_us)
    current=pair(n,0,expiry);request,_=n['context'].exchange(0)
    # Reproduce the real33.332s response-style lease ceiling. This is an
    # explicitly different NEW model grant, not alteration of a live grant.
    if remaining_us==33332000:
        for packet in current['packages'].values():packet['grant']['profile']['lease_ms']=33332
    wrap(n,current);endpoints=[]
    with ExitStack() as stack:
        for unit in ('unit-a','unit-c'):
            packet=current['packages'][unit];provision(packet,state=n['roots'][unit],facts=facts(unit),clock=n['clock'])
            directory=n['roots'][unit]/'runs'/current['run_id'];plan=json.loads((directory/'grant.json').read_bytes())
            caps=_open_existing(n['roots'][unit],n['policy'].parent_id,facts(unit),n['clock']);stack.callback(caps.close)
            caps.consume_start(current['run_id'],plan,base64.b64decode(packet['secret_base64']))
            from lantern_meshticulum.bench.profiles import profile_from_plan
            profile=profile_from_plan(plan)
            authority=RunAuthority(directory,expected_unit=unit,host_boot_id=BOOT,clock=n['clock'],
                owner_probe=lambda c=caps:c.current(),radio_admission=admission_for(profile));stack.callback(authority.close)
            journal=AttemptJournal(directory/'model-attempts.sqlite',binding=authority.grant.digest,max_attempts=256,
                max_airtime_us=60000000,create=True);stack.callback(journal.close)
            store=SessionStore(directory/'model-wire.sqlite',authority.grant,authority.local_peer,boot_id=BOOT,
                clock=n['clock'],create=True);stack.callback(store.close)
            worker=ModelRadio(journal,n['clock'],profile)
            ledger=Ledger(BudgetPolicy(240000000,60000000,120000000,1000000,quantization_us=1),authority.ledger_epoch,n['now'][0][1])
            flow=BenchFlow(authority=authority,worker=worker,cipher=SessionCipher(authority.grant,authority.local_peer,store),
                journal=journal,flow_key=authority.flow_key,contract=authority.contract,profile=authority.carrier_profile,
                ledger=ledger,scheduler_epoch=authority.scheduler_epoch,broker_epoch=authority.broker_epoch,
                sends=authority.sends,clock=n['clock']);stack.callback(flow.close)
            runner=ServiceRunner(ServiceTransfer(flow),payload=request if authority.sends else None);runner.start()
            endpoints.append(dict(directory=directory,authority=authority,caps=caps,flow=flow,worker=worker,runner=runner,journal=journal))
        endpoints[0]['worker'].peer=endpoints[1]['worker'];endpoints[1]['worker'].peer=endpoints[0]['worker']
        yield endpoints


def step(n,endpoints):
    for endpoint in endpoints:endpoint['runner'].step()
    n['now'][0]=tuple(v+50000 for v in n['now'][0])


def test_lost_final_ACK_cannot_release_receiver_until_authenticated_sender_recovery(native):
    n=native
    with live_request(n) as endpoints:
        sender,receiver=endpoints;lost=False
        for _ in range(80):
            step(n,endpoints)
            if receiver['runner'].wire_complete and not lost:
                # RF ACK was durably paid, then lost on the modeled link.
                sender['worker'].incoming.clear();lost=True
                break
        assert lost and receiver['runner'].wire_complete and not sender['runner'].wire_complete
        assert fast.receiver_completed(receiver['authority'],receiver['caps'],receiver['directory']) is False
        with pytest.raises(Exception,match='final_ack_not_confirmed'):
            fast.final_ack_proof(sender['authority'],sender['caps'],sender['runner'])
        paid=receiver['journal'].snapshot()['attempts']
        for _ in range(200):
            step(n,endpoints)
            if sender['runner'].wire_complete:break
        assert sender['runner'].wire_complete and receiver['journal'].snapshot()['attempts']>paid
        proof=fast.final_ack_proof(sender['authority'],sender['caps'],sender['runner'])
        value=fast.deliver_final_ack('unit-c',sender['authority'].grant.session.session_id.hex(),proof,
            state=n['roots']['unit-c'],clock=n['clock'],facts=facts('unit-c'))
        assert value['delivered'] and not value['complete'] and not value['radio_opened']
        assert fast.receiver_completed(receiver['authority'],receiver['caps'],receiver['directory']) is True


@pytest.mark.parametrize('fault',['mac','session','object','revoked','expired'])
def test_forged_or_late_completion_cannot_disarm_or_complete_receiver(native,fault):
    n=native
    with live_request(n) as endpoints:
        sender,receiver=endpoints
        for _ in range(80):
            step(n,endpoints)
            if sender['runner'].wire_complete:break
        proof=fast.final_ack_proof(sender['authority'],sender['caps'],sender['runner'])
        if fault=='mac':proof['mac']='0'*64
        if fault=='session':proof['body']['session_id']='f'*32
        if fault=='object':proof['body']['object']['sha256']='f'*64
        if fault=='revoked':revoke(receiver['directory'])
        if fault=='expired':n['now'][0]=(receiver['authority'].application_expires_epoch_us,receiver['authority'].application_deadline_us)
        with pytest.raises(Exception):
            fast.deliver_final_ack('unit-c',sender['authority'].grant.session.session_id.hex(),proof,
                state=n['roots']['unit-c'],clock=n['clock'],facts=facts('unit-c'))
        assert not (receiver['directory']/'fast-final-ack.json').exists()
        assert receiver['worker'].owned is True


def test_actual250_short_remaining_window_finishes_with_proof_without20s_grace_or_extension(native):
    n=native
    with live_request(n,remaining_us=33332000) as endpoints:
        sender,receiver=endpoints;original=(receiver['authority'].application_deadline_us,receiver['authority'].deadline_us)
        start=n['now'][0]
        for _ in range(80):
            step(n,endpoints)
            if sender['runner'].wire_complete:break
        # Match actual native timing: receivecomplete20.607250s, sender22.564232s.
        n['now'][0]=tuple(v+22564232 for v in start)
        proof=fast.final_ack_proof(sender['authority'],sender['caps'],sender['runner'])
        fast.deliver_final_ack('unit-c',sender['authority'].grant.session.session_id.hex(),proof,
            state=n['roots']['unit-c'],clock=n['clock'],facts=facts('unit-c'))
        assert fast.receiver_completed(receiver['authority'],receiver['caps'],receiver['directory']) is True
        assert n['now'][0][1]+20000000>receiver['authority'].deadlines.application_deadline_monotonic*1000000
        assert original==(receiver['authority'].application_deadline_us,receiver['authority'].deadline_us)


def test_completion_after_effective_lease_expiry_refuses_while_original_application_is_still_live(native):
    n=native
    with live_request(n,remaining_us=33332000) as endpoints:
        sender,receiver=endpoints
        for _ in range(80):
            step(n,endpoints)
            if sender['runner'].wire_complete:break
        proof=fast.final_ack_proof(sender['authority'],sender['caps'],sender['runner'])
        fast.deliver_final_ack('unit-c',sender['authority'].grant.session.session_id.hex(),proof,
            state=n['roots']['unit-c'],clock=n['clock'],facts=facts('unit-c'))
        effective=int(receiver['authority'].deadlines.application_deadline_monotonic*1000000)
        delta=effective+1-n['now'][0][1];n['now'][0]=tuple(v+delta for v in n['now'][0])
        assert receiver['authority'].live() is True
        assert n['now'][0][1]<receiver['authority'].application_deadline_us
        with pytest.raises(Exception,match='effective_deadline_expired'):
            fast.receiver_completed(receiver['authority'],receiver['caps'],receiver['directory'])


@pytest.mark.parametrize('fault',[None,'sender_failed','sender_incomplete','delivery_failed'])
def test_controller_releases_only_proved_sender_completion_and_preserves_cleanup(native,monkeypatch,fault):
    from tests import test_pilot_controller as model
    from lantern_meshticulum.bench.pilot_controller import Controller
    n=native;expiry,_=admit(n,0);context=n['context'];p=n['policy']
    monkeypatch.setattr(model,'BOOTS',{u:BOOT for u in PEERS})
    class ModelClock:
        def mono(self):return n['now'][0][1]/1000000
        def epoch(self):return n['now'][0][0]
        def sleep(self,seconds):n['now'][0]=tuple(v+int(seconds*1000000) for v in n['now'][0])
    timer=ModelClock()
    class BarrierModel(model.ModelOperator):
        """Modeled processes, actual controller prepayment and completion checks."""
        def prepare(self,package):
            result=super().prepare(package);claim=result['parent']
            claim['application_deadline_monotonic_us']=n['now'][0][1]+expiry-timer.epoch()
            claim['grant_context_sha256']=package['parent']['child']['metadata']['grant_digest']
            return result
        def start(self,unit,pair):
            result=super().start(unit,pair);run=pair['run_id']
            if all((u,run) in self.handles for u in (pair['sender'],pair['receiver'])):
                for u in (pair['sender'],pair['receiver']):
                    value=self.results[(u,run)]
                    value.update(original_application_expires_epoch_us=expiry,
                        grant_context_sha256=self.claims[(u,run)]['grant_context_sha256'],flow=dict(complete=True))
                sender=self.results[(pair['sender'],run)]
                sender['fast_final_ack_proof']=fast._proof(dict(schema=fast.FINAL_ACK,session_id=run),b'x'*32)
                self.handles[(pair['receiver'],run)].code=None
                if fault=='sender_failed':self.handles[(pair['sender'],run)].code=1
                if fault=='sender_incomplete':sender['complete']=False
            return result
        def status(self,unit,run):
            value=super().status(unit,run);value['radio_receive_checked']=True
            value['grant_context_sha256']=self.claims[(unit,run)]['grant_context_sha256'];return value
        def fast_complete(self,unit,run,proof):
            self.events.append(('barrier',unit,run))
            if fault=='delivery_failed':raise RuntimeError('modeled delivery failure')
            fast._verify(proof,dict(schema=fast.FINAL_ACK,session_id=run),b'x'*32)
            self.handles[(unit,run)].code=0
            return dict(schema='meshticulum.native-fast-completion-delivered.v1',unit=unit,run_id=run,
                proof_sha256=kp.sha256_hex(canonical(proof)),delivered=True,complete=False,radio_opened=False,
                arm_commands=0,tx_commands=0)
    op=BarrierModel(timer)
    controller=Controller(op,campaign_id=context.value['stages'][0]['campaign_id'],resource_sha256=context.value['manifest_sha256'],
        expires_epoch_us=expiry,handoff_deadline_monotonic=timer.mono()+190,peers=PEERS,boots={u:BOOT for u in PEERS},
        parent=n['parent'],radio_permission=dict(country='MY',antenna_confirmed=True),clock=timer.mono,epoch=timer.epoch,
        sleep=timer.sleep,public32=True,lora_sender_first=True,bundle_context=context.value,radio_profile=GFSK250_NAME)
    request,_=context.exchange(0)
    if fault is None:
        run=controller.link('unit-a','unit-c','lora','MR1.request',request)
        assert controller.links[0]['phase']=='complete' and controller.links[0]['completion_barrier']['run_id']==run
        assert len([e for e in op.events if e[0]=='barrier'])==1
        assert controller.retire()
    else:
        with pytest.raises(Exception):controller.link('unit-a','unit-c','lora','MR1.request',request)
        assert controller.links[0]['phase']=='failed'
        assert set(controller.links[0]['retired'])=={'unit-a','unit-c'}
    assert n['parent'].snapshot()['allocations']==1
