"""Actual failed d1fd historical inputs; source evidence, never current RF authority."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile

import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import fast_bundle_d1fd_history as history, pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy, canonical

METADATA_SHA = 'f55738b239f92f3fde7c8ca984d8edd228603f5a7601a741bac953d52f7f6292'


@pytest.fixture(scope='module')
def actual_inputs():
    path = os.environ.get('MESHTICULUM_H44_FIXTURE')
    if not path:
        pytest.skip('MESHTICULUM_H44_FIXTURE must name retained actual d1fd source inputs')
    base = Path(path)
    spec = importlib.util.spec_from_file_location('actual_d1fd_inputs',base/'assemble_history44.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    result = module.inputs(base.parent/'meshticulum-19ad-native-radio-execution-20261015/D1FD_PUBLIC_METADATA_V3.json',METADATA_SHA)
    receipt = module.read(base/'resume-history-preparation-44/RECEIPT.json',history.NEXT_HISTORY_RECEIPT_SHA256)
    assert receipt['local_controllers_reobserved'] == 44 and receipt['controller_allocations_observed'] == 1
    assert receipt['original_run_complete'] is receipt['original_cleanup_proved'] is receipt['physical_quiescence_observed'] is receipt['global_debt_complete'] is False
    assert receipt['actual_unknown_cost'] is None and receipt['no_refund'] is True
    for unit,pin in receipt['payload_sha256'].items():
        payload = module.read(base/'resume-history-preparation-44'/(unit+'.json'),pin,2500000)
        assert payload['previous'][-1] == result[-2][unit]
        assert module.canonical(payload['previous'][:-1]) == module.canonical(result[1][unit]['previous'])
        assert payload['legacy_inventory'] == result[1][unit]['legacy_inventory']
    return result


@pytest.fixture(params=('unit-a','unit-b','unit-c'))
def actual(request, actual_inputs):
    receipt, previous, operation, controller, native, latest, selected = deepcopy(actual_inputs)
    unit = request.param; row = latest[unit]
    return unit, row, ParentPolicy.from_dict(row['policy']), native[unit], previous[unit], controller


def test_raw_v2_owner_and_signed_context_preserve_full_caps_and_prior_failure(actual):
    unit,row,policy,native,previous,controller = actual
    raw = canonical(row['owner']); owner = rpc.retained_owner_view(row['owner'],policy,unit)
    assert owner == row['owner']['original_owner'] and canonical(row['owner']) == raw
    assert hashlib.sha256(raw).hexdigest() == history.RAW_OWNER_SHA256[unit]
    context = history.original_context(row['owner'],policy,unit=unit)
    assert context.value['whole_bytes'] == 100000 and len(context.value['stages']) == 5
    partition = history.declared_partition(policy)[unit]
    assert partition['declared_new_reservation']['radio_attempts'] == (0 if unit=='unit-b' else 2560)
    assert partition['actual_consumption'] is None and partition['refunds'] == 0
    assert row['paid_caps'] == native['accounting']['whole_parent_paid_caps']
    assert row['runs'] == row['retired_unstarted'] == row['controller_endpoint_refs'] == []
    assert len(row['controller_observed_endpoint_refs']) == (0 if unit=='unit-b' else 1)
    assert row['actual_native_paid_caps'] == row['prior_metadata']['full_caps'] == previous['previous'][-1]['paid_caps']
    assert row['actual_controller_paid_caps']['radio_attempts'] == row['actual_native_paid_caps']['radio_attempts']+(0 if unit=='unit-b' else 256)
    assert row['original_cleanup_proved'] is row['require_result_complete'] is row['native_controller_debt_views_may_be_summed'] is False
    rpc.qualify_history_claims(policy,unit,native['observation'],[],[])
    history.qualify_costs(unit,policy,native['observation'],row)


@pytest.mark.parametrize('fault',('flattened','artifact','schema','scope','runtime','policy','unit'))
def test_raw_or_context_substitution_refuses_before_historical_interpretation(actual,fault):
    unit,row,policy,native,previous,controller = actual; record = row['owner']
    if fault=='flattened': record=record['original_owner']
    elif fault=='artifact': record['mcu_artifact']['image_sha256']='f'*64
    elif fault=='schema': record['schema']='unknown.owner.v2'
    elif fault=='scope': record['original_owner']['mb1_bundle_scope_sha256']='f'*64
    elif fault=='runtime': record['original_owner']['runtime_sha256']='f'*64
    elif fault=='policy': policy=replace(policy,expires_epoch_us=policy.expires_epoch_us+1)
    else: unit=next(u for u in ('unit-a','unit-b','unit-c') if u!=unit)
    with pytest.raises((RuntimeError,WireError,KeyError)):
        rpc.retained_owner_view(record,policy,unit)


@pytest.mark.parametrize('axis',('epoch','monotonic'))
def test_expired_original_dual_clocks_cannot_be_reset(actual,axis):
    unit,row,policy,native,previous,controller=actual; observed=native['observation']
    history.retained_context(row['owner'],policy,unit=unit,observation=observed)
    if axis=='epoch': observed['observation_original_epoch_us']=history.ORIGINAL_ABSOLUTE_END-1
    else:
        owner=row['owner']['original_owner'];phase=owner['native_phase'][unit]
        observed['observation_original_monotonic_us']=phase['deadline_monotonic_us']+history.ORIGINAL_ABSOLUTE_END-history.ORIGINAL_PHASE_END-1
    with pytest.raises(WireError,match='d1fd_original_clocks_live'):
        history.retained_context(row['owner'],policy,unit=unit,observation=observed)


def test_legacy_h43_view_keeps_original_object_and_v2_wrong_parent_refuses(actual):
    unit,row,policy,native,previous,controller=actual
    old=previous['previous'][-1];old_policy=ParentPolicy.from_dict(old['policy'])
    assert rpc.retained_owner_view(old['owner'],old_policy,unit) is old['owner']
    with pytest.raises(RuntimeError,match='unreviewed_retained_mcu_owner'):
        rpc.retained_owner_view(row['owner'],old_policy,unit)


def test_missing_new_receipt_is_an_explicit_cost_refusal(actual,monkeypatch):
    unit,row,policy,native,previous,controller=actual
    monkeypatch.setattr(history,'NEXT_HISTORY_RECEIPT_SHA256',None)
    with pytest.raises(WireError,match='pending_d1fd_public_metadata_and_history'):
        history.qualify_costs(unit,policy,native['observation'],row)


@pytest.mark.parametrize('count',(False,True,1))
def test_claim_count_cannot_convert_empty_native_rows_to_started_or_boolean(actual,count):
    unit,row,policy,native,previous,controller=actual
    native['observation']['detail']['claim_count']=count
    with pytest.raises(RuntimeError):rpc.qualify_history_claims(policy,unit,native['observation'],[],[])


@pytest.mark.parametrize('fault',('native_debt','controller_debt','full_cap','unused','sum_permission','controller_ref','cleanup','boolean_count'))
def test_actual_zero_child_reservation_does_not_accept_debt_or_scope_drift(actual,fault):
    unit,row,policy,native,previous,controller=actual
    if fault=='native_debt':row['actual_native_paid_caps']['radio_attempts']-=1
    elif fault=='controller_debt':row['actual_controller_paid_caps']['radio_attempts']-=1
    elif fault=='full_cap':row['paid_caps']['radio_attempts']-=1
    elif fault=='unused':row['conservative_unused_enrollment_caps']['radio_attempts']+=1
    elif fault=='sum_permission':row['native_controller_debt_views_may_be_summed']=True
    elif fault=='controller_ref':row['controller_observed_endpoint_refs'].append({})
    elif fault=='cleanup':row['original_cleanup_proved']=True
    else:native['observation']['detail']['claim_count']=False
    with pytest.raises(WireError):history.qualify_costs(unit,policy,native['observation'],row)


def test_current_roster_is_append_only_and_old_native_scope_refuses_without_rebinding(monkeypatch):
    from types import MappingProxyType
    from lantern_meshticulum.bench import retained_capture,mcu_artifact
    monkeypatch.setattr(mcu_artifact,'PENDING_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256='16f73f63ce91875dfcb907733ebae16b97a78450c7c769a3c9066b9752eac78e',
        history_receipt_sha256='4d27db070dc0106d067bc49372919b32431ff04a31894bff3d14746d4bca4594',
        preceding_parent='bea52c6aa6a5a95501997a46247b514f')))
    bindings=mcu_artifact.PENDING_NATIVE_BINDINGS
    assert len(rpc.HISTORY)==len(rpc.OLD_DIGESTS)==49
    assert rpc.HISTORY[:43]==history.H43_PREFIX and rpc.HISTORY[43]==history.PARENT
    assert rpc.OLD_DIGESTS[43]==rpc.GUARDED_PAID[history.PARENT]==history.POLICY_DIGEST
    assert history.pending_next_roster()==rpc.HISTORY[:44]==retained_capture.PAID_HISTORY[:44]
    assert bindings['preceding_parent']==history.H43_PREFIX[-1]
    with pytest.raises(mcu_artifact.McuArtifactError,match='pending_mcu_artifact_native_scope_and_history'):
        mcu_artifact.require_selector_scope(mcu_artifact.KIND)
    assert mcu_artifact.PENDING_NATIVE_BINDINGS is bindings


@pytest.fixture(params=('unit-a','unit-b','unit-c'))
def owned_fence(request,actual_inputs):
    # Capture-named leaves use actual row bytes as opaque filename fixtures;
    # they do not claim to be the persisted captures. No native DB is fabricated.
    from lantern_meshticulum.bench.history_metadata_fence import HistoricalMetadataFence
    unit=request.param;row=actual_inputs[-2][unit];selected=actual_inputs[-1][unit]
    relative='parents/'+history.PARENT;identity=relative+'/prepared-identities'
    with tempfile.TemporaryDirectory(prefix='.d1fd-fence-',dir=Path(__file__).parent) as temporary:
        root=Path(temporary)/'state';root.mkdir(mode=0o700)
        (root/identity).mkdir(parents=True,mode=0o700);(root/'runs').mkdir(mode=0o700)
        contents={relative+'/'+n:canonical(v) for n,v in (('policy.json',row['policy']),('owner.json',row['owner']),('prior.json',row['prior_metadata']))}
        contents.update(selected)
        for name in ('capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json'):
            contents[relative+'/'+name]=canonical(row)
        for name,raw in contents.items():
            path=root/name;path.write_bytes(raw);path.chmod(0o600)
        for path in root.rglob('*'):
            if path.is_dir():path.chmod(0o700)
        def expected():
            directories={'':sorted(p.name for p in root.iterdir())}
            for path in root.rglob('*'):
                if path.is_dir():directories[str(path.relative_to(root))]=sorted(p.name for p in path.iterdir())
            return {n:hashlib.sha256(v).hexdigest() for n,v in contents.items()},directories
        yield root,contents,expected,relative,identity,unit


def test_exact_empty_d1fd_namespace_real_fence_and_seal_close_owned_descriptors(owned_fence):
    from lantern_meshticulum.bench.history_metadata_fence import HistoricalMetadataFence
    from lantern_meshticulum.bench.history_identity_seal import capture,SealedHistory
    root,contents,expected,relative,identity,unit=owned_fence;files,directories=expected()
    assert directories[identity]==[]
    with HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories) as held:
        assert held.check() is None
    assert held._closed and not held._fds
    raw,pin=capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)
    with SealedHistory(root,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin) as seal:
        seal.check()
    assert seal._closed and not seal._fence._fds and not seal._chain.fds


@pytest.mark.parametrize('fault',('extra_stage','foreign_stage','identity','secret'))
def test_exact_d1fd_fence_addition_does_not_allow_foreign_or_prepared_metadata(owned_fence,fault):
    from lantern_meshticulum.bench.history_metadata_fence import FenceError
    from lantern_meshticulum.bench.history_identity_seal import capture
    root,contents,expected,relative,identity,unit=owned_fence
    name={'extra_stage':relative+'/fast-stage-1.json','foreign_stage':'parents/'+('f'*32)+'/fast-stage-0.json',
          'identity':identity+'/'+('f'*32)+'.json','secret':relative+'/secret.bin'}[fault]
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(b'{}');path.chmod(0o600)
    contents[name]=b'{}';files,directories=expected()
    with pytest.raises(FenceError):capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)


def test_retained_d1fd_raw_owner_drift_fences_once_then_stays_failed(owned_fence):
    from lantern_meshticulum.bench.history_metadata_fence import FenceError
    from lantern_meshticulum.bench.history_identity_seal import capture,SealedHistory
    root,contents,expected,relative,identity,unit=owned_fence;files,directories=expected()
    raw,pin=capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)
    with SealedHistory(root,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin) as seal:
        seal.check();(root/relative/'owner.json').write_bytes(b'changed')
        with pytest.raises(FenceError):seal.check()
        with pytest.raises(FenceError):seal.check()
