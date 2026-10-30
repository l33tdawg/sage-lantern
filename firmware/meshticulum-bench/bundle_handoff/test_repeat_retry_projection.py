"""Bounded v7 failure metadata; historical missing values stay absent."""
from copy import deepcopy
import json

import pytest

from test_sdk_batch_entry import sdk, failure_report, request


class IntSubclass(int):
    pass


@pytest.mark.parametrize('value', [0, 7, 2**63 - 1])
@pytest.mark.parametrize('field', ['flow_snapshot', 'flow_snapshot_before_close'])
def test_repeat_epoch_survives_both_failure_snapshot_projections(value, field):
    report = failure_report()
    metrics = report['last_part_metrics']
    metrics[field] = deepcopy(metrics['flow_snapshot'])
    metrics[field]['repeat_retry_epochs'] = value
    metrics[field]['repeat_retry_epoch_payload'] = 'private-body'
    actual = sdk.diagnostic_report(report, request(), 'A', EOFError())
    projected = actual['last_part_metrics'][field]
    assert projected['repeat_retry_epochs'] == value
    assert projected['attempt_journal']['attempts'] == 34
    assert projected['fast_retry_offers'] == 2 and projected['fast_retransmits'] == 1
    assert 'private' not in json.dumps(actual)


@pytest.mark.parametrize('value', [None, True, False, -1, 2**63, 2**1000,
    1.0, float('inf'), float('nan'), 'private-key', {}, [], IntSubclass(1)])
def test_invalid_epoch_is_dropped_without_coercion_or_other_metadata_loss(value):
    report = failure_report()
    report['last_part_metrics']['flow_snapshot']['repeat_retry_epochs'] = value
    result = sdk.diagnostic_report(report, request(), 'A', EOFError())
    actual = result['last_part_metrics']['flow_snapshot']
    assert 'repeat_retry_epochs' not in actual
    assert actual['retransmits'] == 5 and actual['attempt_journal']['attempts'] == 34
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('version', range(1, 7))
def test_legacy_policy_absence_is_not_replaced_by_a_zero_epoch_count(version):
    report = failure_report()
    flow = report['last_part_metrics']['flow_snapshot']
    flow['timing_policy'] = {'schema': f'meshticulum.data-burst-policy.v{version}'}
    actual = sdk.diagnostic_report(report, request(), 'A', EOFError())
    assert 'repeat_retry_epochs' not in actual['last_part_metrics']['flow_snapshot']
    assert 'repeat_retry_epochs' not in sdk._flow_report(None)


def test_durable_failure_record_contains_only_the_bounded_scalar(tmp_path):
    report = failure_report()
    metrics = report['last_part_metrics']
    metrics['flow_snapshot']['repeat_retry_epochs'] = 4
    metrics['flow_snapshot_before_close'] = dict(metrics['flow_snapshot'], repeat_retry_epochs=3)
    metrics['flow_snapshot']['repeat_retry_evidence'] = ['private-sequence-payload'] * 1000
    root = sdk.DiagnosticRoot(tmp_path)
    try:
        assert sdk.retain_failure_diagnostics(root, report, request(), 'A', EOFError('private-error'))
    finally:
        root.close()
    raw = (tmp_path / sdk.DIAGNOSTICS_NAME).read_bytes()
    assert len(raw) < sdk.MAX_DIAGNOSTICS_BYTES and b'private' not in raw
    actual = json.loads(raw)['last_part_metrics']
    assert actual['flow_snapshot']['repeat_retry_epochs'] == 4
    assert actual['flow_snapshot_before_close']['repeat_retry_epochs'] == 3
    assert 'repeat_retry_evidence' not in actual['flow_snapshot']
