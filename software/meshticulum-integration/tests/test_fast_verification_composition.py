"""Protected publisher acceptance through the controller/SSH API; RF is modeled."""
import json

import pytest

from lantern_meshticulum.bench import pilot_ssh as ssh, pilot_bundle as bundle
from lantern_meshticulum.bench.pilot_controller import Controller
from lantern_meshticulum.bench.profiles import GFSK250_NAME
from tests.test_pilot_ssh import config
from tests.test_pilot_bundle import data
from tests.test_fast_bundle import fast_data, native, admit, pair, modeled_result, resource_node, PEERS, BOOT
from tests.test_public32_pilot import tmp_path, protected_source


@pytest.mark.parametrize('expire_native',[False,True])
def test_delayed_completed_response_publisher_accept_keeps_native_original_deadline(native,monkeypatch,expire_native):
    n=native;expiry,_=admit(n,0);context=n['context'];p=n['policy']
    request,_=context.exchange(0);response=context.transport.response(0,n['cache'])
    request_pair=pair(n,0,expiry);response_pair=pair(n,0,expiry,response,request_pair['run_id'])
    modeled_result(n,request_pair,request);modeled_result(n,response_pair,response)
    cfg,source=config();commitment=bundle.public_context_commitment(context.value,parent_id=p.parent_id)
    cfg.update(schema=bundle.COMMITTED_CONTRACTS[p.qualification_kind][5],bundle_parent_id=p.parent_id,
        bundle_context=context.value,bundle_capture_commitment=commitment,handoff_end_epoch_us=commitment['phase_end_epoch_us'])
    for row in cfg['units'].values():row['host_boot_id']=BOOT
    mono=lambda:n['now'][0][1]/1000000;epoch=lambda:n['now'][0][0]
    op=ssh.SSHOperator(cfg,provision_source=source,clock=mono,epoch=epoch)
    monkeypatch.setattr(op,'_argv',lambda unit,command,**kwargs:list(command))
    jobs=[]
    class LocalVerificationJob:
        def __init__(self,argv,*,deadline,clock,password):
            self.deadline=deadline;self.password=password;self.closed=False;self.output=b'';jobs.append(self)
        def wait(self):
            n['now'][0]=tuple(v+6000000 for v in n['now'][0])
            if expire_native:
                request_dir=n['roots']['unit-a']/'runs'/request_pair['run_id']
                value=json.loads((request_dir/'result.json').read_bytes())
                value['original_application_deadline_us']=n['now'][0][1]
                (request_dir/'result.json').write_bytes(bundle.canonical(value))
            verified=resource_node.verify('unit-a',context.value['stages'][0]['campaign_id'],
                request_pair['run_id'],response_pair['run_id'])
            self.output=json.dumps(verified).encode()+b'\n'
            return 0
        def close(self):self.closed=True;self.password=None
    monkeypatch.setattr(ssh,'Job',LocalVerificationJob)
    pilot=Controller(op,campaign_id=context.value['stages'][0]['campaign_id'],resource_sha256=context.value['manifest_sha256'],
        expires_epoch_us=expiry,handoff_deadline_monotonic=mono()+190,peers=PEERS,boots={u:BOOT for u in PEERS},
        parent=n['parent'],radio_permission=dict(country='MY',antenna_confirmed=True),clock=mono,epoch=epoch,
        public32=True,lora_sender_first=True,bundle_context=context.value,radio_profile=GFSK250_NAME)
    def completed_link(sender,receiver,transport,format,body,source=None,**kwargs):
        current=request_pair if format=='MR1.request' else response_pair
        outcome=json.loads((n['roots']['unit-a']/'runs'/current['run_id']/'result.json').read_bytes())
        outcome['start_monotonic_us']=n['now'][0][1]
        pilot.links.append(dict(outcomes={'unit-a':outcome},phase='complete'))
        return current['run_id']
    monkeypatch.setattr(pilot,'link',completed_link)
    retired=[];monkeypatch.setattr(pilot,'retire',lambda:retired.append(True))
    original=(p.expires_epoch_us,p.deadline_monotonic_us,p.unit('unit-a').limits,pilot.deadline,expiry)
    if expire_native:
        with pytest.raises(Exception,match='native_request_original_deadline_expired_or_extended'):
            pilot.direct_piece(request,response,'lora',publisher='unit-c',requester='unit-a')
        assert retired and not pilot.verified
    else:
        value=pilot.direct_piece(request,response,'lora',publisher='unit-c',requester='unit-a')
        assert value['publisher_verified'] and value['bundle_part']['part_index']==0 and value['fast_progress_proof']
        assert value['original_application_expires_epoch_us']==expiry and value['request_to_verification_us']>=6000000
    assert jobs[0].closed and jobs[0].password is None
    assert (p.expires_epoch_us,p.deadline_monotonic_us,p.unit('unit-a').limits,pilot.deadline,expiry)==original
