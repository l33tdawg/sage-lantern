"""Future operator selection reaches the compact codec; no native jobs or RF."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

from lantern_meshticulum.bench.capture_storage import COMPACT, PLAIN, canonical, decode, encode, selection


@pytest.fixture
def runner(monkeypatch):
    bench = Path(__file__).resolve().parents[3] / 'firmware/meshticulum-bench'
    monkeypatch.syspath_prepend(str(bench))
    path = bench / 'run_public_gfsk250_mb1_fast_bundle100k.py'
    spec = importlib.util.spec_from_file_location('compact_capture_operator', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize('mode', [PLAIN, COMPACT])
def test_each_future_native_capture_receives_the_explicit_storage_selection(runner, mode):
    units = ('unit-c', 'unit-b', 'unit-a')
    histories = {unit: {'history': unit} for unit in units}
    facts = {unit: {'unit': unit} for unit in units}
    context, commitment = {'bundle': 'original'}, {'commitment': 'original'}
    payloads = runner.capture_payloads_for({'capture_storage': mode}, parent_id='a' * 32,
        histories=histories, facts=facts, epoch=10, end=20, review_sha256='b' * 64,
        bundle_context=context, commitment=commitment)
    assert tuple(payloads) == units
    for unit, payload in payloads.items():
        assert selection(payload) == mode
        assert payload['history'] is histories[unit] and payload['facts'] is facts[unit]
        assert payload['bundle_context'] is context and payload['bundle_capture_commitment'] is commitment
        assert (payload['phase_start_epoch_us'], payload['phase_end_epoch_us']) == (10, 20)
        raw = canonical(dict(schema='source-capture-control', unit=unit, facts=payload['facts']))
        stored = encode(raw, selection(payload))
        assert decode(stored, expected_sha256=hashlib.sha256(raw).hexdigest()) == raw


def test_absent_selection_preserves_plain_storage(runner):
    assert runner.capture_storage_mode({}) == PLAIN


@pytest.mark.parametrize('mode', [None, True, 1, {}, 'automatic', 'zlib-json-v2'])
def test_unknown_mode_is_refused_before_capture_payloads_are_constructed(runner, mode):
    with pytest.raises(RuntimeError, match='invalid_capture_storage_mode'):
        runner.capture_payloads_for({'capture_storage': mode}, parent_id='a' * 32,
            histories={}, facts={}, epoch=10, end=20, review_sha256='b' * 64,
            bundle_context={}, commitment={})
