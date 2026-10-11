"""Genuine signed context/ParentCaps/Controller; native management is modeled."""
from types import SimpleNamespace
import hashlib
import pytest
from lantern_meshticulum.bench.pilot_bundle import BundleCampaign,BundleContext
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps
from meshticulum.bench.wire import WireError
from tests.test_pilot_bundle import data,PEERS,BOOT
from tests.test_pilot_controller import ModelOperator


@pytest.fixture
def campaign(data,tmp_path,monkeypatch):
    import tests.test_pilot_controller as model
    monkeypatch.setattr(model,'BOOTS',{u:BOOT for u in PEERS})
    _,chunks,value,policy=data
    now=[100000000]
    clock=SimpleNamespace(epoch=lambda:now[0],mono=lambda:(801000000+now[0]-100000000)/1e6,
        sleep=lambda seconds:now.__setitem__(0,now[0]+int(seconds*1e6)))
    root=tmp_path/'controller';root.mkdir(mode=0o700)
    parent=ParentCaps.enroll(root/'caps.sqlite',policy,clock=lambda:(clock.epoch(),int(clock.mono()*1e6)),boot_reader=lambda:BOOT)
    context=BundleContext(value,policy)
    class Operator(ModelOperator):
        def __init__(self):super().__init__(clock);self.verified=[];self.missing_receive=False
        def prepare(self,p):
            result=super().prepare(p);claim=result['parent']
            claim['application_deadline_monotonic_us']=801000000+p['application_expires_epoch_us']-100000000
            return result
        def start(self,unit,pair):
            handle=super().start(unit,pair)
            for peer in (pair['sender'],pair['receiver']):
                if (peer,pair['run_id']) in self.results:
                    record=self.results[(peer,pair['run_id'])]
                    record['original_application_expires_epoch_us']=self.packages[(peer,pair['run_id'])]['application_expires_epoch_us']
                    record['start_monotonic_us']=int(clock.mono()*1e6)
            return handle
        def status(self,unit,run):
            result=super().status(unit,run);result['radio_receive_checked']=not self.missing_receive
            return result
        def bundle_status(self,unit,parent_id):
            return dict(schema='meshticulum.native-public100k-bundle-status.v1',unit=unit,parent_id=parent_id,
                bundle_context_sha256=policy.bundle_context_sha256,manifest_sha256=value['manifest_sha256'],
                whole_file_sha256=value['whole_sha256'],whole_bytes=100000,original_whole_expires_epoch_us=value['expires_epoch_us'],
                verified_parts=list(self.verified),all_parts_present=len(self.verified)==5,whole_complete=False)
        def verify(self,unit,campaign,request,response):
            stage=context.stage(campaign,self.packages[(unit,response)]['application_expires_epoch_us']);index=stage['index']
            entry=context.expected['parts'][index];self.verified.append(index)
            # Signed data is real; this metadata models the independently
            # checked native acceptance already covered by test_pilot_bundle.
            from meshticulum.resources import lamplighter as ll
            ll.chunk(chunks[entry['chunk_sha256']],context.manifest['chunks'][index])
            return dict(schema='meshticulum.pilot-verified-piece.v1',private_admission=False,sage_imported=False,
                unit=unit,campaign_id=campaign,response_run_id=response,manifest_sha256=value['manifest_sha256'],
                chunk_sha256=entry['chunk_sha256'],publisher_verified=True,
                original_application_expires_epoch_us=stage['expires_epoch_us'],
                original_application_deadline_us=801000000+stage['expires_epoch_us']-100000000,
                verified_monotonic_us=int(clock.mono()*1e6)+1,useful_verified_bytes=32740,
                bundle_part=dict(part_index=index,useful_decoded_bytes=entry['raw_bytes'],bundle_context_sha256=policy.bundle_context_sha256))
    op=Operator()
    def make(**options):
        return BundleCampaign(op,parent,value,peers=PEERS,boots={u:BOOT for u in PEERS},
            handoff_deadline_monotonic=policy.deadline_monotonic_us/1e6,clock=clock.mono,epoch=clock.epoch,sleep=clock.sleep,**options)
    def response(index):
        from meshticulum.resources import resource
        request,entry=context.exchange(index);raw=chunks[entry['chunk_sha256']]
        return resource.RESPONSE.pack(resource.MAGIC,hashlib.sha256(request).digest()[:16],0,len(raw),0)+raw
    try:yield make,op,parent,now,context,response
    finally:parent.close()


@pytest.mark.parametrize('opt_in',[False,True])
def test_real_signed_bundle_stage_keeps_mode_and_original_clock_for_each_controller(campaign,opt_in):
    make,op,parent,now,context,response=campaign
    run=make(lora_sender_first=True) if opt_in else make()  # Actual default remains receiver-first.
    original=parent.policy.to_dict()
    for index in (0,1):
        now[0]=context.value['stages'][index]['issued_epoch_us']
        result=run.transfer_part(index,response(index));control=run.controllers[-1]
        assert result['bundle_part']['part_index']==index
        assert control.bundle_context.value==context.value and control.lora_sender_first is opt_in
        assert [link['launch_order'] for link in control.links]==([['unit-a','unit-c'],['unit-c','unit-a']] if opt_in else [['unit-c','unit-a'],['unit-a','unit-c']])
        assert all(link['sender_receive_ready'] is opt_in for link in control.links)
        stage=context.value['stages'][index];anchor=dict(original_epoch_us=parent.policy.issued_epoch_us,
            original_monotonic_us=parent.policy.original_monotonic_us,deadline_monotonic_us=parent.policy.deadline_monotonic_us)
        _,stage_end,handoff=context.monotonic_bounds(stage,anchor)
        assert control.deadline==min(stage_end,handoff-10000000)/1e6
        assert all(package['application_expires_epoch_us']==stage['expires_epoch_us'] for unit,runid in op.packages for package in [op.packages[(unit,runid)]] if package['campaign_id']==stage['campaign_id'])
    assert parent.policy.to_dict()==original and parent.snapshot()['allocations']==4


@pytest.mark.parametrize('mode',[None,0,1,'yes'])
def test_non_boolean_bundle_opt_in_refuses_before_any_claim(campaign,mode):
    make,op,parent,_,_,_=campaign
    with pytest.raises(WireError,match='invalid_bundle_startup_order'):make(lora_sender_first=mode)
    assert parent.snapshot()['allocations']==0 and op.events==[]


def test_mutated_bundle_mode_refuses_before_status_or_prepay(campaign):
    make,op,parent,_,_,response=campaign;run=make(lora_sender_first=True);run.lora_sender_first=False
    with pytest.raises(WireError,match='bundle_startup_order_changed'):run.transfer_part(0,response(0))
    assert parent.snapshot()['allocations']==0 and op.events==[]


def test_missing_native_receive_marker_refuses_peer_launch_and_retains_paid_pair(campaign):
    make,op,parent,_,_,response=campaign;run=make(lora_sender_first=True);op.missing_receive=True
    with pytest.raises(WireError,match='radio_receive_readiness_missing'):run.transfer_part(0,response(0))
    assert [event[1] for event in op.events if event[0]=='start']==['unit-a']
    assert parent.snapshot()['allocations']==1
    assert len([event for event in op.events if event[0]=='revoke'])==2


def test_original_whole_monotonic_deadline_refuses_fresh_stage_without_new_credit(campaign):
    make,op,parent,now,_,response=campaign;run=make(lora_sender_first=True)
    now[0]=400000000
    with pytest.raises(Exception):run.transfer_part(0,response(0))
    assert parent.snapshot()['allocations']==0 and op.events==[]


def test_original_stage_epoch_expiry_refuses_while_whole_parent_remains_live(campaign):
    make,op,parent,now,context,response=campaign;run=make(lora_sender_first=True)
    now[0]=context.value['stages'][0]['expires_epoch_us']
    assert parent.current() is True
    with pytest.raises(WireError,match='bundle_part_stage_not_current'):run.transfer_part(0,response(0))
    assert parent.snapshot()['allocations']==0 and op.events==[]
