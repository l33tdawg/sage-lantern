"""Actual H42 read-only evidence; delivery does not erase the failed final audit."""
import base64
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType

import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import fast_bundle, fast_bundle_complete_history as history
from lantern_meshticulum.bench import fast_bundle_partial_history as h41, pilot_resume_rpc as rpc
from lantern_meshticulum.bench.history_metadata_fence import HistoricalMetadataFence, FenceError
from lantern_meshticulum.bench.pilot_parent_caps import Cost, ParentPolicy

BASE = Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-history42-inputs-20261013')
OBSERVATION = BASE / 'expired-parent-observation-1791880009759494'
PREPARATION = BASE / 'resume-history-preparation-42'


def pinned(path, digest, maximum=2000000):
    raw = path.read_bytes()
    assert len(raw) <= maximum and hashlib.sha256(raw).hexdigest() == digest
    return json.loads(raw)


@pytest.fixture(params=['unit-a', 'unit-b', 'unit-c'])
def actual(request):
    unit = request.param
    completion = pinned(OBSERVATION / 'COMPLETION.json', history.EXPIRY_COMPLETION_SHA256)
    native = pinned(OBSERVATION / ('NATIVE-' + unit + '.json'), completion['observation_sha256'][unit])
    receipt = pinned(PREPARATION / 'RECEIPT.json', history.NEXT_HISTORY_RECEIPT_SHA256)
    payload = pinned(PREPARATION / (unit + '.json'), receipt['payload_sha256'][unit], 2500000)
    assert len(payload['previous']) == 42
    assert tuple(row['policy']['parent_id'] for row in payload['previous'][:-1]) == history.H41_PREFIX
    row = payload['previous'][-1]
    return unit, native, receipt, row, ParentPolicy.from_dict(row['policy'])


def test_actual_delivery_keeps_original_failure_and_full_reservation(actual, monkeypatch):
    unit, native, receipt, row, policy = actual
    monkeypatch.setattr(fast_bundle, 'require_bindings', lambda: pytest.fail('history used live authority'))
    assert history.require_final_bindings() is True
    assert history.pending_next_roster() == rpc.HISTORY[:42]
    assert len(rpc.HISTORY) == len(rpc.OLD_DIGESTS) == 44
    assert rpc.HISTORY[:42] == history.H41_PREFIX+(history.PARENT,)
    assert rpc.GUARDED_PAID[history.PARENT] == policy.digest
    context = history.original_context(row['owner'], policy, unit=unit)
    assert context.value['whole_bytes'] == 100000 and len(context.value['stages']) == 5
    extra = Cost() if unit == 'unit-b' else Cost(2560, 600000000)
    assert history.declared_partition(policy)[unit]['declared_new_reservation'] == asdict(extra)
    rpc.qualify_gfsk250_mb1_partial_costs(unit, policy, native['observation'], row)
    assert row['actual_native_paid_caps'] == row['actual_controller_paid_caps'] == row['paid_caps'] == asdict(policy.unit(unit).limits)
    assert row['conservative_unused_enrollment_caps'] == asdict(Cost())
    assert row['require_result_complete'] is True and row['failed_started_runs'] == []
    assert native['terminal_outcome_sha256'] == history.NATIVE_OUTCOME_SHA256[unit]
    assert receipt['original_run_complete'] is history.ORIGINAL_RUN_COMPLETE is False
    assert receipt['original_final_audit_timeout_retained'] is True and history.ORIGINAL_FINAL_AUDIT_TIMEOUT == 124
    assert receipt['whole_bundle_complete_from_pinned_local_result'] is True
    assert receipt['native_whole_file_rechecked'] is False
    assert receipt['controller_parts_accepted'] == 5 and receipt['complete_radio_links'] == 10
    assert receipt['whole_file_sha256'] == history.WHOLE_SHA256 and receipt['whole_bytes'] == history.WHOLE_BYTES


@pytest.mark.parametrize('fault', ['credit', 'unused', 'boolean_count', 'wrong_run', 'unstarted',
                                  'endpoint_incomplete', 'database', 'preparation', 'unexpired'])
def test_actual_cost_mutations_refuse(actual, fault):
    unit, native, receipt, row, policy = actual
    observed = native['observation']
    if fault == 'credit': row['actual_native_paid_caps']['radio_attempts'] -= 1
    elif fault == 'unused': row['conservative_unused_enrollment_caps']['radio_attempts'] += 1
    elif fault == 'boolean_count': observed['detail']['start_count'] = bool(observed['detail']['start_count'])
    elif fault == 'wrong_run': row['runs'].append({'run_id': 'f' * 32})
    elif fault == 'unstarted': row['retired_unstarted'].append({})
    elif fault == 'endpoint_incomplete': row['require_result_complete'] = False
    elif fault == 'database': row['database_sha256'] = 'f' * 64
    elif fault == 'preparation': row['preparation_sha256']['f' * 32] = 'f' * 64
    else: observed['policy_expired_at_observation'] = False
    with pytest.raises((RuntimeError, WireError)):
        rpc.qualify_gfsk250_mb1_partial_costs(unit, policy, observed, row)


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
@pytest.mark.parametrize('fault', ['epoch', 'mono', 'stage_pin', 'resource', 'resource_omission', 'coverage'])
def test_actual_stage_deadlines_and_all_five_resources_are_exact(unit, fault):
    completion = pinned(OBSERVATION / 'COMPLETION.json', history.EXPIRY_COMPLETION_SHA256)
    native = pinned(OBSERVATION / ('NATIVE-' + unit + '.json'), completion['observation_sha256'][unit])
    receipt = pinned(PREPARATION / 'RECEIPT.json', history.NEXT_HISTORY_RECEIPT_SHA256)
    row = pinned(PREPARATION / (unit + '.json'), receipt['payload_sha256'][unit], 2500000)['previous'][-1]
    observed = native['observation']; resource = observed['detail']['resources'][-1]
    if fault == 'epoch': resource['application_expires_epoch_us'] += 1
    elif fault == 'mono': resource['fast_stage_deadline_monotonic_us'] -= 1
    elif fault == 'stage_pin': resource['fast_stage_sha256'] = 'f' * 64
    elif fault == 'resource': resource['campaign_id'] = 'f' * 32
    elif fault == 'resource_omission': observed['detail']['resources'].pop()
    else: observed['legacy_relation']['scoped_linkage_complete'] = False
    with pytest.raises(RuntimeError):
        rpc.qualify_gfsk250_mb1_partial_costs(unit, ParentPolicy.from_dict(row['policy']), observed, row)


@pytest.mark.parametrize('fault', ['policy_deadline', 'owner_deadline', 'runtime', 'scope', 'old_parent'])
def test_original_context_cannot_renew_or_retype(actual, fault):
    unit, native, receipt, row, policy = actual
    owner = row['owner']
    if fault == 'policy_deadline': policy = replace(policy, expires_epoch_us=policy.expires_epoch_us + 1)
    elif fault == 'owner_deadline': owner['phase_end_epoch_us'] += 1
    elif fault == 'runtime': owner['runtime_sha256'] = 'f' * 64
    elif fault == 'scope': owner['mb1_bundle_scope_sha256'] = 'f' * 64
    else: policy = replace(policy, parent_id=h41.PARENT)
    with pytest.raises(WireError): history.original_context(owner, policy, unit=unit)


@pytest.mark.parametrize('field', ['EXPIRY_COMPLETION_SHA256', 'NEXT_HISTORY_RECEIPT_SHA256',
    'NATIVE_DATABASE_SHA256', 'NATIVE_OUTCOME_SHA256', 'FAST_STAGE_SHA256', 'FINAL_ACK_SHA256',
    'VERIFIED_PIECE_SHA256', 'STAGE_APPLICATION_ENDS', 'STAGE_NATIVE_DEADLINES'])
def test_missing_actual_evidence_pin_refuses(field, monkeypatch):
    value = getattr(history, field)
    monkeypatch.setattr(history, field, dict.fromkeys(value) if isinstance(value, dict) else None)
    with pytest.raises(WireError): history.require_final_bindings()


@pytest.fixture
def metadata(actual, tmp_path):
    unit, native, receipt, row, policy = actual
    (tmp_path / 'parents' / history.PARENT).mkdir(parents=True)
    for relative, record in native['public_metadata'].items():
        raw = base64.b64decode(record['base64'], validate=True)
        assert hashlib.sha256(raw).hexdigest() == record['sha256']
        target = tmp_path / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
    class Reader:
        def __init__(self): self.opened = []
        def directory(self, path): assert path.is_dir()
        def read(self, path, maximum):
            self.opened.append(path)
            raw = path.read_bytes(); assert len(raw) <= maximum; return raw
        def check(self): return True
    return unit, tmp_path, Reader(), row


def test_actual_public_metadata_and_exact_absence(metadata):
    unit, state, reader, row = metadata
    files = history.metadata_files(reader, state, unit, row)
    assert len(files) == {'unit-a': 15, 'unit-b': 0, 'unit-c': 10}[unit]
    assert all(path.name.startswith('fast-stage-') or path.name in ('fast-final-ack.json', 'verified-piece.json') for path in reader.opened)
    assert set(files.values()) == set(history.FAST_STAGE_SHA256[unit].values()) | set(history.FINAL_ACK_SHA256[unit].values()) | set(history.VERIFIED_PIECE_SHA256[unit].values())


@pytest.mark.parametrize('fault', ['extra_stage', 'ack_scope', 'piece_scope'])
def test_actual_public_metadata_rejects_unobserved_scope(metadata, fault):
    unit, state, reader, row = metadata
    if fault == 'extra_stage': (state / 'parents' / history.PARENT / 'fast-stage-5.json').write_bytes(b'{}')
    else: row['fast_final_ack_sha256' if fault == 'ack_scope' else 'verified_piece_sha256']['f' * 32] = 'f' * 64
    with pytest.raises(WireError): history.metadata_files(reader, state, unit, row)


@pytest.mark.parametrize('kind', ['stage', 'ack', 'piece'])
def test_actual_public_metadata_changed_bytes_refuse(tmp_path, kind):
    unit = 'unit-a'
    completion = pinned(OBSERVATION / 'COMPLETION.json', history.EXPIRY_COMPLETION_SHA256)
    native = pinned(OBSERVATION / ('NATIVE-' + unit + '.json'), completion['observation_sha256'][unit])
    receipt = pinned(PREPARATION / 'RECEIPT.json', history.NEXT_HISTORY_RECEIPT_SHA256)
    row = pinned(PREPARATION / (unit + '.json'), receipt['payload_sha256'][unit], 2500000)['previous'][-1]
    for relative, record in native['public_metadata'].items():
        target = tmp_path / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(record['base64'], validate=True))
    paths = {'stage': tmp_path/'parents'/history.PARENT/'fast-stage-4.json',
             'ack': tmp_path/'runs'/next(iter(history.FINAL_ACK_SHA256[unit]))/'fast-final-ack.json',
             'piece': tmp_path/'runs'/next(iter(history.VERIFIED_PIECE_SHA256[unit]))/'verified-piece.json'}
    paths[kind].write_bytes(b'{}')
    class Reader:
        def directory(self, path): assert path.is_dir()
        def read(self, path, maximum): return path.read_bytes()
        def check(self): return True
    with pytest.raises(WireError): history.metadata_files(Reader(), tmp_path, unit, row)


@pytest.mark.parametrize('record', ['stage', 'ack', 'piece'])
def test_h42_metadata_paths_remain_fenced(tmp_path, record):
    root = tmp_path/'state'; root.mkdir(mode=0o700)
    relative = ('parents/'+history.PARENT+'/fast-stage-4.json' if record == 'stage' else
                'runs/'+next(iter(history.FINAL_ACK_SHA256['unit-a']))+'/'+('fast-final-ack.json' if record == 'ack' else 'verified-piece.json'))
    path = root/relative; path.parent.mkdir(parents=True, mode=0o700)
    path.parent.parent.chmod(0o700); path.write_bytes(b'{}'); path.chmod(0o600)
    parts = relative.split('/')
    dirs = {'': [parts[0]], parts[0]: [parts[1]], '/'.join(parts[:2]): [parts[2]]}
    with HistoricalMetadataFence(root, owner_uid=os.geteuid(), expected_files={relative:hashlib.sha256(b'{}').hexdigest()}, expected_directories=dirs) as fence:
        fence.check(); path.write_bytes(b'{"changed":true}')
        with pytest.raises(FenceError): fence.check()


def test_missing_new_scope_still_refuses(monkeypatch):
    monkeypatch.setattr(fast_bundle, 'PHYSICAL_BINDINGS', MappingProxyType(dict(fast_bundle.PHYSICAL_BINDINGS,
        scope_sha256=None, history_receipt_sha256=None)))
    with pytest.raises(WireError, match='pending_fast_bundle_scope'): fast_bundle.require_bindings()


def test_h41_roster_prefix_is_immutable(monkeypatch):
    from lantern_meshticulum.bench import retained_capture
    roster = list(retained_capture.PAID_HISTORY)
    roster[39], roster[40] = roster[40], roster[39]
    monkeypatch.setattr(retained_capture, 'PAID_HISTORY', tuple(roster))
    with pytest.raises(WireError, match='60d9_H41_prefix_changed'): history.pending_next_roster()
