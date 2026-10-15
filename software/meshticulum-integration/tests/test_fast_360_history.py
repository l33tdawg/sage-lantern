"""Actual H46 completed3605 retention inputs; no live scope, consumption claim or refund."""
import base64
import hashlib
from copy import deepcopy
from dataclasses import asdict, replace
import importlib.util
import json
import lzma
import os
from pathlib import Path

import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import fast_bundle, fast_bundle_360_history as history
from lantern_meshticulum.bench import fast_bundle_complete_history as h42
from lantern_meshticulum.bench import pilot_resume_rpc as rpc, retained_capture
from lantern_meshticulum.bench.pilot_parent_caps import Cost, ParentPolicy

RECEIPT_SHA256 = 'cf01a817a18458f0ca2b91dca012afcc29044a9c65bb426ee9316e003cb32826'


@pytest.fixture(scope='module')
def pinned_inputs():
    fixture=os.environ.get('MESHTICULUM_H46_FIXTURE')
    if not fixture:pytest.skip('MESHTICULUM_H46_FIXTURE must name the actual completed3605 preparation lane')
    base=Path(fixture)
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
    old=load('h46_actual_reader',base/'original_assemble_history44.py')
    prep=load('h46_actual_inputs',base/'prepare_history46_inputs.py')
    values=prep.inputs(old.read,old.load,old.need,old.sha,old.canonical)
    receipt=old.read(base/'resume-history-preparation-46/RECEIPT.json',RECEIPT_SHA256)
    assert receipt['local_controllers_reobserved']==46 and receipt['original_history_receipt_sha256']==prep.OLD_SHA
    assert receipt['stage_supplement_sha256']==history.EXPIRY_COMPLETION_SHA256
    assert receipt['original_run_complete'] is True and receipt['original_cleanup_proved'] is False
    assert receipt['prior_H45_original_run_complete'] is receipt['prior_H44_original_run_complete'] is False
    assert receipt['prior_H43_original_run_complete'] is True and receipt['prior_H42_original_run_complete'] is False
    assert receipt['prior_H42_final_audit_timeout_retained'] is True
    assert receipt['actual_unknown_cost'] is None and receipt['global_debt_complete'] is False and receipt['no_refund'] is True
    assert receipt['native_prepared_counts']==receipt['native_start_counts']=={'unit-a':10,'unit-b':0,'unit-c':10}
    assert receipt['native_starts']==20 and receipt['controller_allocations_observed']==10
    assert receipt['complete_radio_links']==10 and receipt['retired_endpoints']==20
    for unit,pin in receipt['payload_sha256'].items():
        payload=old.read(base/'resume-history-preparation-46'/(unit+'.json'),pin,2500000)
        assert old.canonical(payload['previous'][:-1])==old.canonical(values['histories'][unit]['previous'])
        assert payload['legacy_inventory']==values['histories'][unit]['legacy_inventory']
        assert payload['previous'][-1]==values['latest'][unit]
    return receipt,values['metadata_wrapper'],values['histories'],values['operation'],values['metadata'],values['latest']


@pytest.fixture(params=['unit-a', 'unit-b', 'unit-c'])
def actual(request, pinned_inputs):
    receipt, completion, previous, operation, native, latest = deepcopy(pinned_inputs)
    unit = request.param
    row = latest[unit]
    return unit, native[unit], row, ParentPolicy.from_dict(row['policy']), previous[unit], receipt


def test_real_complete_inputs_preserve_prior_caps_and_h42_failure(actual, monkeypatch):
    unit, native, row, policy, previous, receipt = actual
    monkeypatch.setattr(fast_bundle, 'require_bindings', lambda: pytest.fail('history used live authority'))
    context = history.original_context(row['owner'], policy, unit=unit)
    assert context.value['whole_bytes'] == 100000 and len(context.value['stages']) == 5
    extra = Cost() if unit == 'unit-b' else Cost(2560, 600000000)
    partition = history.declared_partition(policy)[unit]
    assert partition['declared_new_reservation'] == asdict(extra)
    assert partition['actual_consumption'] is None and partition['refunds'] == 0
    assert policy.unit(unit).prior_debt == Cost(**previous['previous'][-1]['paid_caps'])
    assert row['prior_metadata']['full_caps'] == previous['previous'][-1]['paid_caps']
    assert row['actual_native_paid_caps'] == row['actual_controller_paid_caps'] == row['paid_caps'] == asdict(policy.unit(unit).limits)
    assert row['conservative_unused_enrollment_caps'] == asdict(Cost())
    count = 0 if unit == 'unit-b' else 10
    assert len(row['runs']) == len(row['controller_endpoint_refs']) == count
    assert len(history.FAST_STAGE_SHA256[unit]) == len(history.FINAL_ACK_SHA256[unit]) == count//2
    assert len(history.VERIFIED_PIECE_SHA256[unit]) == (5 if unit == 'unit-a' else 0)
    assert native['terminal_outcome_sha256'] == history.NATIVE_OUTCOME_SHA256[unit]
    assert native['actual_cost'] is None and native['refunds'] == 0
    assert native['global_debt_complete'] is False and row['require_result_complete'] is True
    assert receipt['original_run_complete'] is True and h42.ORIGINAL_RUN_COMPLETE is False
    assert receipt['prior_H42_final_audit_timeout_retained'] is True and h42.ORIGINAL_FINAL_AUDIT_TIMEOUT == 124
    assert history.ORIGINAL_RUN_COMPLETE is True and history.ORIGINAL_FINAL_AUDIT_TIMEOUT is None
    assert history.require_final_bindings() is True
    rpc.qualify_gfsk250_mb1_partial_costs(unit, policy, native['observation'], row)


def test_real_expired_context_decodes_without_live_authority(actual):
    unit, native, row, policy, previous, receipt = actual
    context = history.retained_context(row['owner'], policy, unit=unit, observation=native['observation'])
    assert len(context.value['stages']) == 5


@pytest.mark.parametrize('fault', ['policy_deadline', 'owner_deadline', 'runtime', 'scope', 'parent'])
def test_original_signed_context_mutations_refuse(actual, fault):
    unit, native, row, policy, previous, receipt = actual
    owner = row['owner']['original_owner']
    if fault == 'policy_deadline': policy = replace(policy, expires_epoch_us=policy.expires_epoch_us+1)
    elif fault == 'owner_deadline': owner['phase_end_epoch_us'] += 1
    elif fault == 'runtime': owner['runtime_sha256'] = 'f'*64
    elif fault == 'scope': owner['mb1_bundle_scope_sha256'] = 'f'*64
    else: policy = replace(policy, parent_id=h42.PARENT)
    with pytest.raises(WireError): history.original_context(owner, policy, unit=unit)


@pytest.mark.parametrize('clock', ['epoch', 'monotonic'])
def test_both_original_expiry_clocks_are_required(actual, clock):
    unit, native, row, policy, previous, receipt = actual
    observed = native['observation']
    if clock == 'epoch': observed['observation_original_epoch_us'] = history.ORIGINAL_ABSOLUTE_END-1
    else:
        observed['observation_original_monotonic_us'] = (row['owner']['original_owner']['native_phase'][unit]['deadline_monotonic_us']
            +history.ORIGINAL_ABSOLUTE_END-history.ORIGINAL_PHASE_END-1)
    with pytest.raises(WireError, match='3605_original_clocks_live'):
        history.retained_context(row['owner'], policy, unit=unit, observation=observed)


def test_missing_real_h43_receipt_prevents_cost_admission(actual, monkeypatch):
    unit, native, row, policy, previous, receipt = actual
    assert history.NEXT_HISTORY_RECEIPT_SHA256 == RECEIPT_SHA256
    monkeypatch.setattr(history, 'NEXT_HISTORY_RECEIPT_SHA256', None)
    with pytest.raises(WireError, match='pending_3605_final_result_expiry_and_history'):
        rpc.qualify_gfsk250_mb1_partial_costs(unit, policy, native['observation'], row)


def test_active46_keeps_exact45_prefix_and_refuses_old45_scope(monkeypatch):
    from types import MappingProxyType
    from lantern_meshticulum.bench import capture_8120,mcu_artifact
    assert rpc.HISTORY==retained_capture.PAID_HISTORY and len(rpc.HISTORY)==len(rpc.OLD_DIGESTS)==46
    assert rpc.HISTORY[:45]==history.H45_PREFIX and rpc.HISTORY[-1]==history.PARENT
    assert rpc.GUARDED_PAID[history.PARENT]==rpc.OLD_DIGESTS[45]==history.POLICY_DIGEST
    assert rpc.PARTIAL250[history.PARENT] is history and history in rpc.COMPLETE_FAST_HISTORY
    assert history.pending_next_roster()==rpc.HISTORY
    assert capture_8120.PARENT not in rpc.HISTORY and capture_8120.H42_RECEIPT_SHA256==h42.NEXT_HISTORY_RECEIPT_SHA256
    monkeypatch.setattr(mcu_artifact,'PENDING_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256=history.ORIGINAL_SCOPE_SHA256,history_receipt_sha256=history.ORIGINAL_HISTORY_RECEIPT_SHA256,
        preceding_parent='e2449e5bcd4af15d45f7a756dff7d997')))
    with pytest.raises(mcu_artifact.McuArtifactError,match='pending_mcu_artifact_native_scope_and_history'):
        mcu_artifact.require_selector_scope(mcu_artifact.KIND)


@pytest.mark.parametrize('fault',['schema','artifact','flattened'])
def test_raw_selected_historical_owner_refuses_substitution(actual,fault):
    unit,native,row,policy,previous,receipt=actual
    raw=row['owner']
    if fault=='schema':raw['schema']='meshticulum.finite-public-resume-owner.v1'
    elif fault=='artifact':raw['mcu_artifact']['image_sha256']='f'*64
    else:raw=raw['original_owner']
    with pytest.raises(WireError):history.owner_view(raw,policy,unit=unit)


@pytest.mark.parametrize('fault', ['credit', 'unused', 'boolean_count', 'wrong_run', 'unstarted',
                                  'endpoint_incomplete', 'database', 'preparation', 'unexpired'])
def test_actual_full_reservation_and_endpoint_mutations_refuse(actual, fault):
    unit, native, row, policy, previous, receipt = actual
    observed = native['observation']
    if fault == 'credit': row['actual_native_paid_caps']['radio_attempts'] -= 1
    elif fault == 'unused': row['conservative_unused_enrollment_caps']['radio_attempts'] += 1
    elif fault == 'boolean_count': observed['detail']['start_count'] = bool(observed['detail']['start_count'])
    elif fault == 'wrong_run': row['runs'].append({'run_id': 'f'*32})
    elif fault == 'unstarted': row['retired_unstarted'].append({})
    elif fault == 'endpoint_incomplete': row['require_result_complete'] = False
    elif fault == 'database': row['database_sha256'] = 'f'*64
    elif fault == 'preparation': row['preparation_sha256']['f'*32] = 'f'*64
    else: observed['policy_expired_at_observation'] = False
    with pytest.raises((RuntimeError, WireError)):
        rpc.qualify_gfsk250_mb1_partial_costs(unit, policy, observed, row)


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
@pytest.mark.parametrize('fault', ['epoch', 'mono', 'stage_pin', 'resource', 'resource_omission', 'unknown', 'unretired'])
def test_five_original_stages_and_retired_journals_are_exact(unit, fault, pinned_inputs):
    receipt, completion, previous, operation, native, latest = deepcopy(pinned_inputs)
    observed = native[unit]['observation']; row = latest[unit]
    resource = observed['detail']['resources'][-1]
    if fault == 'epoch': resource['application_expires_epoch_us'] += 1
    elif fault == 'mono': resource['fast_stage_deadline_monotonic_us'] -= 1
    elif fault == 'stage_pin': resource['fast_stage_sha256'] = 'f'*64
    elif fault == 'resource': resource['campaign_id'] = 'f'*32
    elif fault == 'resource_omission': observed['detail']['resources'].pop()
    elif fault == 'unknown': observed['legacy_runs'][0]['journal']['has_unknown_or_pending'] = True
    else: observed['legacy_runs'][0]['wire']['retired'] = 0
    with pytest.raises(RuntimeError):
        rpc.qualify_gfsk250_mb1_partial_costs(unit, ParentPolicy.from_dict(row['policy']), observed, row)


@pytest.mark.parametrize('field', ['EXPIRY_COMPLETION_SHA256', 'NEXT_HISTORY_RECEIPT_SHA256',
    'NATIVE_DATABASE_SHA256', 'NATIVE_OUTCOME_SHA256', 'FAST_STAGE_SHA256', 'FINAL_ACK_SHA256',
    'VERIFIED_PIECE_SHA256', 'STAGE_APPLICATION_ENDS', 'STAGE_NATIVE_DEADLINES'])
def test_missing_actual_evidence_pin_refuses(field, monkeypatch):
    value = getattr(history, field)
    monkeypatch.setattr(history, field, dict.fromkeys(value) if isinstance(value, dict) else None)
    with pytest.raises(WireError): history.require_final_bindings()


@pytest.fixture
def metadata(actual, tmp_path):
    unit, native, row, policy, previous, receipt = actual
    (tmp_path/'parents'/history.PARENT).mkdir(parents=True)
    for relative, record in native['public_metadata'].items():
        target = tmp_path/relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(lzma.decompress(base64.b64decode(record['base64'], validate=True)))
    class Reader:
        def directory(self, path): assert path.is_dir()
        def read(self, path, maximum):
            raw = path.read_bytes(); assert len(raw) <= maximum; return raw
        def check(self): return True
    return unit, tmp_path, Reader(), row


def test_actual_stage_ack_piece_metadata_is_exact(metadata):
    unit, state, reader, row = metadata
    files = history.metadata_files(reader, state, unit, row)
    assert len(files) == {'unit-a':15, 'unit-b':0, 'unit-c':10}[unit]
    assert set(files.values()) == (set(history.FAST_STAGE_SHA256[unit].values())
        |set(history.FINAL_ACK_SHA256[unit].values())|set(history.VERIFIED_PIECE_SHA256[unit].values()))


@pytest.mark.parametrize('fault', ['extra_stage', 'ack_scope', 'piece_scope'])
def test_public_metadata_scope_drift_refuses(metadata, fault):
    unit, state, reader, row = metadata
    if fault == 'extra_stage': (state/'parents'/history.PARENT/'fast-stage-5.json').write_bytes(b'{}')
    else: row['fast_final_ack_sha256' if fault == 'ack_scope' else 'verified_piece_sha256']['f'*32] = 'f'*64
    with pytest.raises(WireError): history.metadata_files(reader, state, unit, row)
