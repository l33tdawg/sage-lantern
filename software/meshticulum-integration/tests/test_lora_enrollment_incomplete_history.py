"""Actual02ec public bytes and conservative incomplete enrollment; no native writes."""
from copy import deepcopy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil

import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import lora_enrollment_incomplete_history as history,pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,canonical


@pytest.fixture(scope='module')
def inputs():
    base=Path(os.environ['MESHTICULUM_H47_FIXTURE'])
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module);return module
    old=load('H47_original_input_reader',base/'original_assemble_history44.py')
    prepared=load('H47_actual_inputs',base/'prepare_history47_inputs.py')
    return base,prepared.inputs(old.read,old.load,old.need,old.sha,old.canonical)


@pytest.fixture(params=('unit-a','unit-b','unit-c'))
def actual(request,inputs):
    base,values=inputs;unit=request.param;row=deepcopy(values['latest'][unit])
    return unit,row,ParentPolicy.from_dict(row['policy']),values


def test_actual_partial_enrollment_keeps_raw_schema_unknowns_and_all_paid_caps(actual):
    unit,row,policy,values=actual;before=canonical(row)
    assert history.qualify_row(unit,policy,row)==row['paid_caps']
    assert rpc.retained_owner_view(row['owner'],policy,unit)is row['owner']
    assert row['owner']['schema']==history.OWNER_SCHEMA and canonical(row)==before
    assert row['actual_native_paid_caps']is row['enrollment_incomplete']['actual_claim_resource_prepared_start_counts']is None
    assert row['actual_controller_paid_caps']==values['histories'][unit]['previous'][-1]['paid_caps']
    extra={}if unit=='unit-b'else dict(radio_attempts=1024,estimated_airtime_us=240000000)
    assert row['paid_caps']=={k:v+extra.get(k,0)for k,v in row['actual_controller_paid_caps'].items()}
    assert row['native_controller_debt_views_may_be_summed']is row['original_cleanup_proved']is False
    assert row['runs']==row['retired_unstarted']==[] and row['preparation_sha256']=={}
    assert row['failed_started_runs']is None and row['enrollment_incomplete']['callback_terminal_proved']is False
    assert tuple(v['policy']['parent_id']for v in values['histories'][unit]['previous'])==history.H46_PREFIX


@pytest.mark.parametrize('damage',('full_caps','native_zero','native_as_controller','summed','refund','terminal','count_zero',
    'schema','scope','owner_flag','policy','prior','run','identity','database','public_file','classification'))
def test_actual_enrollment_unknowns_history_source_and_full_reservation_cannot_be_rewritten(actual,damage):
    unit,row,policy,values=actual
    if damage=='full_caps':row['paid_caps']['radio_attempts']-=1
    elif damage=='native_zero':row['actual_native_paid_caps']={k:0 for k in row['paid_caps']}
    elif damage=='native_as_controller':row['actual_native_paid_caps']=row['actual_controller_paid_caps']
    elif damage=='summed':row['native_controller_debt_views_may_be_summed']=True
    elif damage=='refund':row['enrollment_incomplete']['no_refund']=False
    elif damage=='terminal':row['enrollment_incomplete']['callback_terminal_proved']=True
    elif damage=='count_zero':row['enrollment_incomplete']['actual_claim_resource_prepared_start_counts']={'claim':0}
    elif damage=='schema':row['owner']['schema']='meshticulum.private-resume-owner.v1'
    elif damage=='scope':row['owner']['comparison_scope_sha256']='f'*64
    elif damage=='owner_flag':row['owner']['alpha_qualified']=True
    elif damage=='policy':row['policy']['expires_epoch_us']+=1
    elif damage=='prior':row['prior_metadata']['full_caps']['radio_attempts']-=1
    elif damage=='run':row['runs']=[{'run_id':'f'*32}]
    elif damage=='identity':row['preparation_sha256']={'f'*32:'f'*64}
    elif damage=='database':row['database_sha256']='f'*64
    elif damage=='public_file':row['public_metadata_sha256'].pop(next(iter(row['public_metadata_sha256'])))
    else:row['enrollment_incomplete']['native_state']='enrolled-zero'
    with pytest.raises((WireError,RuntimeError,KeyError)):history.qualify_row(unit,ParentPolicy.from_dict(row['policy']),row)


def test_retained_experiment_does_not_lookup_current_scope_or_supply_live_authority(actual,monkeypatch):
    from lantern_meshticulum.bench import mcu_artifact as mcu
    from types import MappingProxyType
    unit,row,policy,values=actual
    monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(dict(scope_sha256=None,history_receipt_sha256=None,preceding_parent=None)))
    def forbid(*args,**kwargs):raise AssertionError('historical owner consulted current authority')
    monkeypatch.setattr(mcu,'require_lora_selector_scope',forbid)
    monkeypatch.setattr(mcu,'validate_lora_artifact',forbid)
    assert history.owner_view(row['owner'],policy,unit=unit)is row['owner']
    monkeypatch.setattr(history,'NEXT_HISTORY_RECEIPT_SHA256',None)
    with pytest.raises(WireError,match='pending_02ec_H47'):history.require_final_bindings()


@pytest.fixture
def owned(actual,inputs,tmp_path):
    unit,row,policy,values=actual;base=inputs[0];state=tmp_path/'state'
    shutil.copytree(base/'actual-public'/unit,state)
    # Raw intake records this empty directory; copying files alone omits it.
    assert values['observed'][unit]['directories']['prepared-identities']['names']==[]
    (state/'parents'/history.PARENT/'prepared-identities').mkdir(mode=0o700)
    (state/'runs').mkdir(mode=0o700)
    for path in (state,*state.rglob('*')):path.chmod(0o700 if path.is_dir()else 0o600)
    return unit,row,policy,values,state


def verify(owned,*,guard=lambda:None,clock=None,current_boot=None):
    unit,row,policy,values,state=owned
    original=policy.unit(unit).host_boot_id
    phase=row['owner']['native_phase'][unit]
    mono=phase['deadline_monotonic_us']+history.ORIGINAL_ABSOLUTE_END-history.ORIGINAL_PHASE_END+1
    with rpc.Reader()as reader:
        try:return history.metadata_files(reader,state,unit,row,guard=guard,
            clock=clock or(lambda:(history.ORIGINAL_ABSOLUTE_END+1,mono)),
            current_boot=current_boot or(lambda:original),previous_history=values['histories'][unit])
        finally:
            held=list(reader._tokens);reader.close()
            assert reader._closed
            for fd in held:
                with pytest.raises(OSError):os.fstat(fd)


def test_genuine_reader_reobserves_actual_capture_seals_partial_bytes_and_closes(owned):
    unit,row,policy,values,state=owned
    assert verify(owned)==row['public_metadata_sha256']
    parent=state/'parents'/history.PARENT
    assert list((parent/'prepared-identities').iterdir())==[]
    assert (parent/'native.sqlite').exists()is(unit=='unit-c')
    if unit=='unit-c':
        assert (parent/'native.sqlite').stat().st_size==4096
        assert (parent/'native.sqlite.owner').read_bytes()==(parent/'native.sqlite.approval').read_bytes()==b''


@pytest.mark.parametrize('damage',('extra_parent_file','identity','missing_capture','corrupt_capture','symlink','late_epoch','late_monotonic','revoked'))
def test_fresh_namespace_byte_expiry_and_revocation_checks_refuse(owned,damage):
    unit,row,policy,values,state=owned;parent=state/'parents'/history.PARENT
    if damage in ('extra_parent_file','identity'):
        path=parent/('unreviewed.json'if damage=='extra_parent_file'else'prepared-identities/'+('f'*32)+'.json')
        path.write_bytes(b'{}');path.chmod(0o600)
    elif damage=='missing_capture':(parent/'capture.json').unlink()
    elif damage=='corrupt_capture':
        path=parent/'capture.json';raw=path.read_bytes();path.write_bytes(raw[:-1]+bytes([raw[-1]^1]));path.chmod(0o600)
    elif damage=='symlink':
        path=parent/'capture.json';path.rename(parent/'saved-capture');path.symlink_to(parent/'saved-capture')
    def revoked():raise RuntimeError('original_current_guard_revoked')
    phase=row['owner']['native_phase'][unit];end=phase['deadline_monotonic_us']+history.ORIGINAL_ABSOLUTE_END-history.ORIGINAL_PHASE_END
    clock=(lambda:(history.ORIGINAL_ABSOLUTE_END, end+1))if damage=='late_epoch'else(lambda:(history.ORIGINAL_ABSOLUTE_END+1,end))if damage=='late_monotonic'else None
    with pytest.raises((WireError,RuntimeError,OSError)):
        verify(owned,guard=revoked if damage=='revoked'else lambda:None,clock=clock)


def test_real_metadata_fence_keeps_exact_partial_namespace_and_empty_identity_scope(owned):
    from lantern_meshticulum.bench.history_identity_seal import capture,SealedHistory
    unit,row,policy,values,state=owned;files=verify(owned)
    dirs={'':sorted(os.listdir(state))}
    for path in state.rglob('*'):
        if path.is_dir():dirs[path.relative_to(state).as_posix()]=sorted(os.listdir(path))
    raw,pin=capture(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
    with SealedHistory(state,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin)as held:held.check()
    assert held._closed and not held._fence._fds


def test_active_partial_owner_lock_refuses_instead_of_claiming_terminal(inputs,tmp_path):
    unit='unit-c';base,values=inputs;row=deepcopy(values['latest'][unit]);policy=ParentPolicy.from_dict(row['policy'])
    state=tmp_path/'state';shutil.copytree(base/'actual-public'/unit,state);(state/'runs').mkdir(mode=0o700)
    assert values['observed'][unit]['directories']['prepared-identities']['names']==[]
    (state/'parents'/history.PARENT/'prepared-identities').mkdir(mode=0o700)
    for path in (state,*state.rglob('*')):path.chmod(0o700 if path.is_dir()else 0o600)
    with (state/'parents'/history.PARENT/'native.sqlite.owner').open('r+b')as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):verify((unit,row,policy,values,state))


@pytest.fixture(scope='module')
def actual47(inputs):
    import subprocess
    import ast
    base,values=inputs;out=base/'resume-history-preparation-47'
    raw=(out/'RECEIPT.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==history.NEXT_HISTORY_RECEIPT_SHA256=='dcef33079caa071f9ff50dc044f2d42e41513133722ad4d80096de3fa09bdf0c'
    receipt=json.loads(raw);proof_raw=(out/'INPUT_PROOF.json').read_bytes()
    assert hashlib.sha256(proof_raw).hexdigest()==receipt['input_proof_sha256']=='e187752f71e373dfb9d7399e9e7536a4b2eaad1082ed61b810b63d8866d0e172'
    proof=json.loads(proof_raw)
    assert receipt['local_controllers_reobserved']==len(receipt['controller_sha256'])==47
    assert receipt['native_counts']is receipt['native_starts']is receipt['actual_unknown_cost']is None
    assert receipt['original_run_complete']is receipt['original_cleanup_proved']is receipt['physical_quiescence_observed']is False
    assert receipt['native_controller_debt_views_may_be_summed']is receipt['native_permission']is False
    assert receipt['no_refund']is True and receipt['global_debt_complete']is False
    assert receipt['controller_allocations_observed']==0 and receipt['prepared_identity_counts']=={u:0 for u in history.OWNER_SHA256}
    assert receipt['prior_H46_original_run_complete']is receipt['prior_H43_original_run_complete']is True
    assert receipt['prior_H45_original_run_complete']is receipt['prior_H44_original_run_complete']is receipt['prior_H42_original_run_complete']is False
    assert proof['actual_cost']is proof['native_counts']is None and proof['no_refund']is True
    payloads={};controllers={}
    for unit,pin in receipt['payload_sha256'].items():
        raw=(out/(unit+'.json')).read_bytes();assert hashlib.sha256(raw).hexdigest()==pin
        payloads[unit]=json.loads(raw)
        assert canonical(payloads[unit]['previous'][:-1])==canonical(values['histories'][unit]['previous'])
        assert payloads[unit]['legacy_inventory']==values['histories'][unit]['legacy_inventory']
        assert canonical(payloads[unit]['previous'][-1])==canonical(values['latest'][unit])
    for parent,pin in receipt['controller_sha256'].items():
        raw=(out/parent/'controller.json').read_bytes();assert hashlib.sha256(raw).hexdigest()==pin
        controllers[parent]=json.loads(raw)
    script=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench/run_public_sf5_c_bulk_8k.py'
    spec=importlib.util.spec_from_file_location('H47_retained_controller_caps',script)
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    original=subprocess.check_output(['git','show','43612001d65f10f4d06df00ce90a113f5d39db11:firmware/meshticulum-bench/run_public_sf5_c_bulk_8k.py'],cwd=script.parents[2])
    function=next(n for n in ast.parse(original).body if isinstance(n,ast.FunctionDef)and n.name=='retained_controller_caps')
    namespace={k:getattr(runner,k)for k in ('need','canonical','sha','asdict')}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'immutable_H46_controller_caps','exec'),namespace)
    return payloads,controllers,runner.retained_controller_caps,namespace['retained_controller_caps']


@pytest.mark.parametrize('unit',('unit-a','unit-b','unit-c'))
@pytest.mark.parametrize('index',range(47))
def test_all_actual47_controller_rows_preserve_old46_and_new_unknown_native_debt(actual47,unit,index):
    payloads,controllers,current,original=actual47;row=deepcopy(payloads[unit]['previous'][index])
    observed=deepcopy(controllers[row['policy']['parent_id']]);before=canonical((row,observed))
    assert current(unit,row,observed)==row['paid_caps']
    assert canonical((row,observed))==before
    if index<46 or unit=='unit-b':assert original(unit,row,observed)==row['paid_caps']
    else:
        assert row['actual_native_paid_caps']is None
        assert row['paid_caps']!=row['actual_controller_paid_caps']
        with pytest.raises(RuntimeError,match='retained_paid_caps_changed'):original(unit,row,observed)


@pytest.mark.parametrize('damage',('allocated','boolean_count','false_zero','limit','controller_prior','unexpired','pending','summed','native_zero'))
def test_real47_controller_and_unknown_native_views_cannot_be_relabelled(actual47,damage):
    payloads,controllers,current,original=actual47;unit='unit-c'
    row=deepcopy(payloads[unit]['previous'][-1]);observed=deepcopy(controllers[history.PARENT])
    if damage=='allocated':observed['detail']['allocation_count']=1
    elif damage=='boolean_count':observed['detail']['allocation_count']=False
    elif damage=='false_zero':observed['detail']['units'][0]['full_prepaid_caps']['radio_attempts']=False
    elif damage=='limit':observed['detail']['units'][0]['limit']['radio_attempts']-=1
    elif damage=='controller_prior':observed['detail']['units'][0]['prior_plus_prepaid_caps']['radio_attempts']+=256
    elif damage=='unexpired':observed['observation_original_epoch_us']=history.ORIGINAL_ABSOLUTE_END
    elif damage=='pending':observed['pending_unknown']=True
    elif damage=='summed':row['native_controller_debt_views_may_be_summed']=True
    else:row['actual_native_paid_caps']={k:0 for k in row['paid_caps']}
    with pytest.raises((RuntimeError,WireError)):current(unit,row,observed)


def test_actual_H47_roster_append_preserves_every_original46_identifier_and_digest(inputs):
    from lantern_meshticulum.bench.retained_capture import PAID_HISTORY
    base,values=inputs
    assert PAID_HISTORY[:47]==history.H46_PREFIX+(history.PARENT,)==rpc.HISTORY[:47]
    assert len(PAID_HISTORY)==len(rpc.OLD_DIGESTS)==49
    assert tuple(v['policy_digest']for v in values['histories']['unit-a']['previous'])==rpc.OLD_DIGESTS[:46]
    assert rpc.OLD_DIGESTS[46]==history.POLICY_DIGEST
    assert history.require_final_bindings()
