import copy
import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('guarded_report', Path(__file__).resolve().parents[1] /
    'lantern/benchmarks/guarded_report.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sample(index=1):
    return dict(schema='sage.lantern.guarded-benchmark.v1', run_id=f'run-{index}', configuration='baseline',
        cache_class='resident_cache_miss', guarded_admission_verified=True, outcome='complete',
        synthetic_correct=True, stop_reason='stop',
        timings_ms={name: index * 100 if name == 'total' else None for name in MODULE.TIMINGS},
        tokens={name: None for name in MODULE.COUNTS}, resources={name: None for name in MODULE.RESOURCES},
        **{name: 'a' * 64 for name in MODULE.PINS})


def test_individual_runs_median_and_missing_metrics_preserved():
    rows = [sample(index) for index in (1, 2, 3)]
    result = MODULE.summarize(rows)
    group = result['groups'][0]
    assert group['runs'] == rows and group['three_warm_repeats_verified'] is True
    assert group['timings_ms']['total'] == dict(samples=3, median=200, minimum=100, maximum=300)
    assert group['timings_ms']['stt']['median'] is None
    assert result['speedup_claimed'] is False and result['p95_estimated'] is False


@pytest.mark.parametrize('field', ['cache_class', 'rendered_prompt_sha256', 'audio_sha256', 'source_fixture_sha256'])
def test_incomparable_work_not_pooled(field):
    rows = [sample(1), sample(2)]
    rows[1][field] = 'first_after_process_start' if field == 'cache_class' else 'b' * 64
    assert len(MODULE.summarize(rows)['groups']) == 2


@pytest.mark.parametrize('fault', ['unguarded', 'secret', 'nan', 'boolean_time', 'negative', 'fractional_tokens', 'duplicate'])
def test_invalid_or_private_evidence_rejected(fault):
    row = sample()
    if fault == 'unguarded':
        row['guarded_admission_verified'] = False
    elif fault == 'secret':
        row['token'] = 'must-not-be-accepted'
    elif fault in ('nan', 'boolean_time', 'negative'):
        row['timings_ms']['total'] = {'nan': float('nan'), 'boolean_time': True, 'negative': -1}[fault]
    elif fault == 'fractional_tokens':
        row['tokens']['output'] = 1.5
    with pytest.raises(ValueError):
        MODULE.summarize([row, copy.deepcopy(row)] if fault == 'duplicate' else [row])


def test_failures_never_dropped_or_counted_as_warm_success():
    rows = [sample(index) for index in (1, 2, 3)]
    rows[-1]['outcome'] = 'unknown'
    rows[-1]['synthetic_correct'] = None
    group = MODULE.summarize(rows)['groups'][0]
    assert len(group['runs']) == 3 and group['failed_or_incomplete_count'] == 1
    assert group['three_warm_repeats_verified'] is False
    assert group['timings_ms']['total']['median'] == 150
