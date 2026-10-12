"""Pure checkpoint transitions + genuine signed context/typed controller.

These are source controls; STATUS, peer terminal rows and reset operations below
are explicitly modeled. No device, source installation or RF is exercised.
"""
from copy import deepcopy
from types import SimpleNamespace
import os
import threading
import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import bundle_radio_checkpoint as module
from lantern_meshticulum.bench.pilot_bundle import BundleContext
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps
from lantern_meshticulum.bench import pilot_parent_snapshot as snapshot
from tests.test_pilot_bundle import data,PEERS,BOOT,packet
from tests.test_pilot_parent_snapshot import root_observation


def status(attempts=30,airtime=10000000,generation=36):
    return dict(usb_serial=module.USB,firmware=module.FIRMWARE,mcu_boot=module.PREFIX+format(generation,'016x'),
        disarmed=True,boot_attempts=attempts,boot_reserved_estimated_airtime_us=airtime)


def test_sufficient_fresh_capacity_never_calls_pulse_or_reopen():
    value=status(56,20000000);events=[]
    after,pulsed=module.conditional_status(value,probe=lambda:events.append('probe') or dict(value),
        live=lambda:events.append('current'),pulse=lambda:pytest.fail('unexpected pulse'),reopen=lambda:pytest.fail('unexpected reopen'))
    assert after==value and pulsed is False and events==['current','probe','current']


@pytest.mark.parametrize('before',[status(57),status(0,20000001)])
def test_insufficient_actual_headroom_allows_exactly_one_pulse_and_fresh_zero_boot(before):
    events=[]
    after,pulsed=module.conditional_status(before,probe=lambda:events.append('probe') or status(0,0,37),
        live=lambda:events.append('current'),pulse=lambda:events.append('pulse'),reopen=lambda:events.append('reopen'))
    assert pulsed and after==status(0,0,37) and events.count('pulse')==events.count('reopen')==1


@pytest.mark.parametrize('change',[{'mcu_boot':module.PREFIX+format(36,'016x')},{'mcu_boot':'0'*32},
    {'boot_attempts':1},{'boot_reserved_estimated_airtime_us':1},{'disarmed':False},
    {'boot_attempts':True},{'firmware':'other'},{'usb_serial':'other'}])
def test_pulse_without_genuine_greater_zero_disarmed_STATUS_refuses_and_never_retries(change):
    fresh=status(0,0,37);fresh.update(change);events=[]
    with pytest.raises(WireError):module.conditional_status(status(100),probe=lambda:fresh,live=lambda:None,
        pulse=lambda:events.append('pulse'),reopen=lambda:events.append('reopen'))
    assert events==['pulse','reopen']


def test_revocation_before_pulse_refuses_without_hardware_effects():
    with pytest.raises(WireError):module.conditional_status(status(100),probe=lambda:pytest.fail('probe'),
        live=lambda:(_ for _ in ()).throw(WireError('revoked')),pulse=lambda:pytest.fail('pulse'),reopen=lambda:pytest.fail('reopen'))


def test_status_changed_without_pulse_refuses():
    with pytest.raises(WireError,match='without_pulse'):module.conditional_status(status(),probe=lambda:status(31),
        live=lambda:None,pulse=lambda:pytest.fail('pulse'),reopen=lambda:pytest.fail('reopen'))


@pytest.mark.parametrize('now',[(150000000,850000000),(149000000,851000000),(98000000,800000000),
    (149000000,799000000),(148000000,849000000)])
def test_original_handoff_epoch_mono_and_regression_cannot_be_extended(now):
    anchor=dict(original_epoch_us=99000000,original_monotonic_us=800000000,deadline_monotonic_us=1101000000)
    with pytest.raises(WireError):module.clock_bounds(now,{'handoff_end_epoch_us':150000000},
        {'expires_epoch_us':400000000},anchor,last=(149000000,850000000))


def test_handoff_uses_native_anchor_and_tighter_original_whole_deadline():
    anchor=dict(original_epoch_us=99000000,original_monotonic_us=800000000,deadline_monotonic_us=845000000)
    assert module.clock_bounds((140000000,841000000),{'handoff_end_epoch_us':150000000},
        {'expires_epoch_us':149000000},anchor)==(149000000,845000000)


def test_unverified_primitives_refuse_before_callbacks_or_closing_foreign_owner():
    owner=SimpleNamespace(close=lambda:pytest.fail('foreign close'))
    with pytest.raises(WireError,match='unverified_checkpoint_primitives'):
        module.checkpoint(None,serial_owner=owner,primitives=SimpleNamespace(),next_index=1,handoff_sha256='a'*64)


@pytest.fixture
def root_record(data,tmp_path,monkeypatch,root_observation):
    _,chunks,value,policy=data;context=BundleContext(value,policy);now=lambda:(100000000,801000000)
    directory=tmp_path/'controller';directory.mkdir(mode=0o700)
    parent=ParentCaps.enroll(directory/'caps.sqlite',policy,clock=now,boot_reader=lambda:BOOT)
    try:
        pair=packet(value,policy,0,100000000)
        from lantern_meshticulum.bench.pilot_controller import pair_packages
        from meshticulum.resources import resource
        request,entry=context.exchange(0);part=chunks[entry['chunk_sha256']]
        response=resource.RESPONSE.pack(resource.MAGIC,module.hashlib.sha256(request).digest()[:16],0,len(part),0)+part
        reply=pair_packages(campaign_id=value['stages'][0]['campaign_id'],resource_sha256=value['manifest_sha256'],
            application_expires_epoch_us=value['stages'][0]['expires_epoch_us'],sender='unit-c',receiver='unit-a',transport='lora',format='MR1.response',
            descriptor=dict(bytes=len(response),sha256=module.sha(response)),source=dict(kind='served_public_chunk',request_run_id=pair['run_id']),
            request=None,peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=100000000,
            radio_permission={'country':'MY','antenna_confirmed':True},public32=True)
        for pair in (pair,reply):
            permit=parent.prepay_pair(tuple(pair['packages'].values()));parent.consume(permit)
    finally:assert parent.close()
    raw=(directory/'caps.sqlite').read_bytes()
    observation=snapshot.collect(directory,snapshot.Scope('meshticulum.public-parent-snapshot-scope.v1','controller',os.geteuid(),
        policy.parent_id,policy.digest,module.sha(raw),None,BOOT,130000000),clock=now,boot_reader=lambda:BOOT).to_dict()
    assert observation['status']=='scoped_rows_observed' and observation['detail']['allocation_count']==2
    # Use the actual H19 source whitelist. Peer STATUS/projections are modeled.
    history=tuple(module.HISTORY);assert len(history)==28
    natives={};terminal=[]
    for unit in ('unit-a','unit-c'):
        rows=[];claims=[]
        for allocation in observation['detail']['allocations']:
            child=next(c for c in allocation['children'] if c['unit']==unit);run=child['run_id']
            claims.append(dict(controller_allocation_id=allocation['allocation_id'],child=child,prepared=True,started=True))
            rows.append(dict(status='scoped_rows_observed',run_id=run,wire={'retired':1},journal={'has_unknown_or_pending':False}))
            terminal.append(dict(unit=unit,run_id=run,job_record_found=True,live=False,disarmed_on_exit=True,retired=True))
        natives[unit]=dict(parent_id=policy.parent_id,policy_digest=policy.digest,status='scoped_rows_observed',scoped_rows_complete=True,
            pending_unknown=False,detail=dict(claim_count=2,prepared_count=2,start_count=2,claims=claims),legacy_scoped_rows_complete=True,
            legacy_relation=dict(scoped_linkage_complete=True,native_claims_without_scoped_actuals=[]),legacy_runs=rows)
    authorization=dict(schema='meshticulum.root-public100k-checkpoint-owner.v1',producer_sha256='e'*64,
        primitives_sha256=module.PRIMITIVES_SHA,retained_history_count=28,maximum_checkpoints=4)
    proof=dict(schema=module.SCHEMA,producer_sha256='e'*64,parent_id=policy.parent_id,policy_digest=policy.digest,
        bundle_context_sha256=policy.bundle_context_sha256,checkpoint_index=1,next_campaign_id=value['stages'][1]['campaign_id'],
        retained_parent_ids=list(history),original_whole={k:value[k] for k in ('original_epoch_us','original_monotonic_us','expires_epoch_us','deadline_monotonic_us')},
        original_handoff_end_epoch_us=value['stages'][0]['handoff_end_epoch_us'],all_owned_clients_terminal=True,
        all_remote_callbacks_exit_zero=True,refunds=0,native_transport_permission=False,controller=observation,native=natives,terminal_children=terminal)
    return context,policy,authorization,proof


def test_genuine_typed_controller_pins_and_signed_context_fit_pending_root_contract(root_record):
    context,policy,authorization,proof=root_record
    assert module.validate_root_handoff(proof,context=context,policy=policy,next_index=1,authorization=authorization) is proof


@pytest.mark.parametrize('fault',['absent_owner','absent_producer','wrong_context','live_child','missing_child','unretired',
    'unknown','missing_controller_child','native_ref','future_claim','extended_whole','wrong_history','old_count19','refund'])
def test_unsafe_unowned_or_unlinked_handoff_refuses_without_any_action(root_record,fault):
    context,policy,authorization,proof=root_record;proof=deepcopy(proof);authorization=deepcopy(authorization)
    if fault=='absent_owner':authorization=None
    elif fault=='absent_producer':authorization['producer_sha256']=None
    elif fault=='wrong_context':proof['bundle_context_sha256']='0'*64
    elif fault=='live_child':proof['terminal_children'][0]['live']=True
    elif fault=='missing_child':proof['terminal_children'].pop()
    elif fault=='unretired':proof['native']['unit-c']['legacy_runs'][0]['wire']['retired']=0
    elif fault=='unknown':proof['native']['unit-a']['pending_unknown']=True
    elif fault=='missing_controller_child':proof['controller']['detail']['allocations'][0]['children'].pop()
    elif fault=='native_ref':proof['native']['unit-c']['detail']['claims'][0]['controller_allocation_id']='0'*64
    elif fault=='future_claim':proof['native']['unit-a']['detail']['claims'][0]['child']['campaign_id']=context.value['stages'][1]['campaign_id']
    elif fault=='extended_whole':proof['original_whole']['expires_epoch_us']+=1
    elif fault=='wrong_history':proof['retained_parent_ids'].pop()
    elif fault=='old_count19':authorization['retained_history_count']=20
    else:proof['refunds']=1
    with pytest.raises(WireError):module.validate_root_handoff(proof,context=context,policy=policy,next_index=1,authorization=authorization)


@pytest.mark.parametrize('fault',['PID','thread','class','close','pulse'])
def test_registered_primitives_owner_and_code_rebinding_refuse(monkeypatch,fault):
    import types
    class Owner:
        def check_device(self):pass
        def close(self):pass
        def reopen(self):pass
    prim=types.ModuleType('MODEL verified source only')
    prim.RetainedSerialOwner=Owner;prim.one_pulse=lambda *args:None;prim.protected_read=lambda *args:b''
    refs=(Owner,prim.one_pulse,prim.protected_read,Owner.check_device,Owner.close,Owner.reopen)
    monkeypatch.setitem(module._PRIMITIVES,prim,((os.getpid(),threading.get_native_id()),refs))
    assert module.primitives_current(prim)==refs
    if fault=='PID':monkeypatch.setattr(module.os,'getpid',lambda:module._PRIMITIVES[prim][0][0]+1)
    elif fault=='thread':monkeypatch.setattr(module.threading,'get_native_id',lambda:module._PRIMITIVES[prim][0][1]+1)
    elif fault=='class':prim.RetainedSerialOwner=type('Foreign',(),{})
    elif fault=='close':Owner.close=lambda *args:pytest.fail('foreign close')
    else:prim.one_pulse=lambda *args:pytest.fail('foreign pulse')
    with pytest.raises(WireError):module.primitives_current(prim)


def test_one_use_record_is_written_before_probe_and_capture_cleanup_is_unconditional():
    import ast
    path=module.Path(module.__file__);tree=ast.parse(path.read_bytes())
    fn=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='checkpoint')
    call=lambda node,name:isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id==name
    use=next(node for node in ast.walk(fn) if call(node,'write_new'))
    first_probe=next(node for node in ast.walk(fn) if isinstance(node,ast.Assign) and ast.unparse(node.targets[0])=='before')
    assert use.lineno<first_probe.lineno
    guarded=next(node for node in fn.body if isinstance(node,ast.Try))
    assert ast.unparse(guarded.finalbody[0])=='close()'
    assert 'boot_reader=boot' in path.read_text()
