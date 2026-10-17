"""Actual H50 provenance, whole reservations and unproved final audit."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest
from lantern_meshticulum.bench import lora_reporter_incomplete_history as history, pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy, Cost
from meshticulum.bench.wire import WireError


@pytest.fixture(scope='module')
def actual_inputs():
    base = Path(os.environ['MESHTICULUM_H50_FIXTURE'])
    freeze = json.loads((base / 'final-history-binding/H50_FIXTURE_FROZEN.json').read_bytes())
    sys.path.insert(0, str(base))
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
    old = load('H50_exact_input_reader', base / 'original_assemble_history44.py')
    prepared = load('H50_exact_actual_inputs', base / 'h50_inputs.py')
    return prepared.inputs(old.read, old.load, old.need, old.sha, old.canonical, freeze['actual_input_binding_sha256'])


@pytest.fixture(params=('unit-a', 'unit-b', 'unit-c'))
def actual(request, actual_inputs):
    unit = request.param; row = deepcopy(actual_inputs['latest'][unit])
    return unit, row, ParentPolicy.from_dict(row['policy']), actual_inputs


def test_actual_two_completed_links_retain_the_full_50_parent_history_and_missing_audit(actual):
    unit, row, policy, values = actual
    assert rpc.HISTORY == history.H49_PREFIX + (history.PARENT,) and len(rpc.HISTORY) == len(rpc.OLD_DIGESTS) == 50
    assert tuple(v['policy']['parent_id'] for v in values['histories'][unit]['previous']) == history.H49_PREFIX
    assert rpc.retained_owner_view(row['owner'], policy, unit) is row['owner']
    assert history.qualify_row(unit, policy, row) == row['paid_caps']
    count = 0 if unit == 'unit-b' else 2
    assert history.NATIVE_COUNTS[unit] == dict(claim_count=count, resource_count=(0 if unit == 'unit-b' else 1), prepared_count=count, start_count=count)
    assert len(history.endpoint_rows(unit)) == count
    assert Cost(**row['paid_caps']) == policy.unit(unit).limits
    assert Cost(**row['actual_native_paid_caps']) == Cost(**row['actual_controller_paid_caps'])
    assert Cost(**row['actual_native_paid_caps']).plus(Cost(**row['conservative_unused_enrollment_caps'])) == policy.unit(unit).limits
    assert row['original_cleanup_proved'] is row['native_controller_debt_views_may_be_summed'] is False
    assert row['reporter_incomplete']['original_native_final_audit_missing'] is True
    assert row['reporter_incomplete']['physical_quiescence_observed'] is row['reporter_incomplete']['original_safe_handoff_proven'] is False
    assert history.verify_public_bytes(unit, policy, row, values['selected_public'][unit], values['histories'][unit], values['observed'][unit]['native_snapshot'])
    if unit == 'unit-a':
        receipt = 'runs/891a03b0fd31296af6956eee7d09dc2f/verified-piece.json'
        raw = values['selected_public'][unit][receipt]
        assert hashlib.sha256(raw).hexdigest() == history.PUBLIC_FILES[unit][receipt]['sha256']
        assert json.loads(raw)['verified_epoch_us'] > 0


@pytest.mark.parametrize('damage', ('paid_limit', 'claim', 'final_audit', 'public_byte'))
def test_actual_H50_evidence_rejects_a_smaller_limit_false_completion_or_changed_bytes(actual, damage):
    unit, row, policy, values = actual
    raw = dict(values['selected_public'][unit]); native = deepcopy(values['observed'][unit]['native_snapshot'])
    if damage == 'paid_limit': row['paid_caps']['radio_attempts'] -= 1
    elif damage == 'claim': native['detail']['start_count'] += 1
    elif damage == 'final_audit': row['reporter_incomplete']['original_native_final_audit_missing'] = False
    else:
        key = next(iter(raw)); raw[key] += b'changed'
    with pytest.raises((WireError, RuntimeError, KeyError)):
        history.verify_public_bytes(unit, policy, row, raw, values['histories'][unit], native)
