"""Native publisher verification wait, with synthetic SSH/clock only; no devices."""
from copy import deepcopy
import json
import pytest

from lantern_meshticulum.bench import pilot_ssh as ssh, pilot_bundle as bundle
from lantern_meshticulum.bench.profiles import GFSK250_FAST_BUNDLE_KIND
from meshticulum.bench.wire import WireError
from tests.test_pilot_ssh import config
from tests.test_pilot_bundle import data
from tests.test_fast_bundle import fast_data


@pytest.fixture
def operator(fast_data,monkeypatch):
    _,_,context,p=fast_data;cfg,source=config()
    cfg.update(schema=bundle.COMMITTED_CONTRACTS[GFSK250_FAST_BUNDLE_KIND][5],bundle_parent_id=p.parent_id,
        bundle_context=context,bundle_capture_commitment=bundle.public_context_commitment(context,parent_id=p.parent_id),
        handoff_end_epoch_us=context['expires_epoch_us']+5000000)
    now=[100000000,800.0];jobs=[]
    value=ssh.SSHOperator(cfg,provision_source=source,clock=lambda:now[1],epoch=lambda:now[0])
    monkeypatch.setattr(value,'_argv',lambda unit,command,**kw:list(command))
    class Job:
        def __init__(self,argv,*,password,deadline,clock):
            self.deadline=deadline;self.password=password;self.closed=False
            schemas={'pilot-verify':'meshticulum.pilot-verified-piece.v1',
                'pilot-fast-admit':'meshticulum.native-fast-stage-admitted.v1',
                'pilot-bundle-status':'meshticulum.native-public100k-bundle-status.v1',
                'pilot-bundle-finish':'meshticulum.native-public100k-bundle-finish.v1'}
            self.output=json.dumps(dict(schema=schemas.get(argv[7],'test'),source_control_only=True)).encode()+b'\n'
            jobs.append(self)
        def wait(self):
            now[0]+=6000000;now[1]+=6
            if now[1]>=self.deadline:raise WireError('native_management_deadline')
            return 0
        def close(self):self.closed=True;self.password=None
    monkeypatch.setattr(ssh,'Job',Job)
    return value,now,jobs


def verify(operator,*,expiry=None):
    value,now,jobs=operator
    kwargs={} if expiry is None else dict(application_expires_epoch_us=expiry)
    return value.verify('unit-a','1'*32,'2'*32,'3'*32,**kwargs)


def test_six_second_fast_publisher_accept_is_bounded_by30_and_keeps_original_window(operator):
    value,now,jobs=operator;outer=value.deadline;expiry=now[0]+180000000
    reply=verify(operator,expiry=expiry)
    assert reply['source_control_only'] and jobs[0].deadline==830
    assert jobs[0].closed and jobs[0].password is None and value.deadline==outer
    assert now[0]<expiry


def test_default_management_still_times_out_at5_and_reaps_owned_job(operator):
    with pytest.raises(WireError,match='native_management_deadline'):verify(operator)
    assert operator[2][0].deadline==805 and operator[2][0].closed


@pytest.mark.parametrize('bound',['application','outer'])
def test_longer_wait_never_exceeds_original_application_or_outer_end(operator,bound):
    value,now,jobs=operator;expiry=now[0]+180000000
    if bound=='application':expiry=now[0]+3000000
    else:value.deadline=now[1]+3
    with pytest.raises(WireError,match='native_management_deadline'):verify(operator,expiry=expiry)
    assert jobs[0].deadline==803 and jobs[0].closed


@pytest.mark.parametrize('fault',['expired','extended','boolean','wrong_kind','receiver','action'])
def test_special_wait_cannot_enable_other_action_role_kind_or_window(operator,fault):
    value,now,jobs=operator;expiry=now[0]+180000000
    if fault=='expired':expiry=now[0]
    elif fault=='extended':expiry=value.config['bundle_capture_commitment']['expires_epoch_us']+1
    elif fault=='boolean':expiry=True
    elif fault=='wrong_kind':value.config['bundle_capture_commitment']['qualification_kind']='c-bulk32-v1'
    unit='unit-c' if fault=='receiver' else 'unit-a'
    action='pilot-status' if fault=='action' else 'pilot-verify'
    with pytest.raises(WireError):
        value._action(unit,action,'3'*32,schema='meshticulum.pilot-verified-piece.v1',bundle_management_expires_epoch_us=expiry)
    assert jobs==[]


@pytest.mark.parametrize('action',['admit','status','finish'])
def test_related_fast_proof_calls_get_same_bounded_wait_and_original_clock(operator,action):
    value,now,jobs=operator;outer=value.deadline;parent=value.config['bundle_parent_id']
    if action=='admit':reply=value.fast_admit('unit-c',parent,1,now[0],now[0]+180000000,{'source_control':True})
    elif action=='status':reply=value.bundle_status('unit-a',parent)
    else:reply=value.bundle_finish('unit-a',parent)
    assert reply['source_control_only'] and jobs[0].deadline==830
    assert jobs[0].closed and jobs[0].password is None and value.deadline==outer


def test_fast_admit_wait_uses_existing_part_expiry_and_rejects_other_parent_or_part(operator):
    value,now,jobs=operator;parent=value.config['bundle_parent_id']
    for proposed,index in [('f'*32,1),(parent,5)]:
        with pytest.raises(WireError):value.fast_admit('unit-c',proposed,index,now[0],now[0]+3000000,None)
    assert jobs==[]
    with pytest.raises(WireError,match='native_management_deadline'):
        value.fast_admit('unit-a',parent,1,now[0],now[0]+3000000,None)
    assert jobs[0].deadline==803 and jobs[0].closed


@pytest.mark.parametrize('action',['status','finish'])
def test_fast_whole_actions_clamp_to_original_whole_and_reject_wrong_receiver(operator,action):
    value,now,jobs=operator;parent=value.config['bundle_parent_id']
    call=value.bundle_status if action=='status' else value.bundle_finish
    with pytest.raises(WireError):call('unit-c',parent)
    assert jobs==[]
    now[0]=value.config['bundle_capture_commitment']['expires_epoch_us']-3000000
    with pytest.raises(WireError,match='native_management_deadline'):call('unit-a',parent)
    assert jobs[0].deadline==803 and jobs[0].closed
