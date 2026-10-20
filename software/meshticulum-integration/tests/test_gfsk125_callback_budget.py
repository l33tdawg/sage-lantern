"""Launcher-only bounded setup and inherited failure cleanup controls; no devices."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

BENCH=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'

@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(BENCH))
    spec=importlib.util.spec_from_file_location('gfsk_budget_controls',BENCH/'run_public_gfsk125_batch.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_capture_budget_is_separate_from_unchanged_control_calls(runner):
    assert runner.native_callback_seconds('capture',200000000,0)==120
    for action in ('observe-cache','enroll','guard','audit'):
        assert runner.native_callback_seconds(action,200000000,0)==45


@pytest.mark.parametrize('remaining,expected',[(120,112),(60,52),(9,1),(200,120)])
def test_capture_never_uses_the_reserved_original_cleanup_time(runner,remaining,expected):
    assert runner.native_callback_seconds('capture',remaining*1000000,0)==expected


@pytest.mark.parametrize('remaining',[8,7,0,-1])
def test_expired_or_insufficient_original_phase_refuses_before_dispatch(runner,remaining):
    with pytest.raises(RuntimeError,match='batch_original_remote_window_expired'):
        runner.native_callback_seconds('capture',remaining*1000000,0)


def test_repeated_budget_computation_cannot_extend_original_phase(runner):
    end=200000000
    assert runner.native_callback_seconds('capture',end,0)==120
    assert runner.native_callback_seconds('capture',end,150000000)==42
    assert end==200000000


def test_returned_cleanup_error_still_makes_result_incomplete(runner):
    current=SimpleNamespace(links=[],retire=lambda:[dict(unit='unit-c',stop_error='modeled timeout')])
    result={'complete':True};runner.cleanup_current(current,result)
    assert result['complete'] is False and result['cleanup_error']=='current_controller_cleanup_unproved'


def test_missing_endpoint_retirement_is_never_cleared_after_a_setup_error(runner):
    link=dict(run_id='a'*32,attempted=['unit-c'],prepared=['unit-c'],retired=[])
    with pytest.raises(RuntimeError,match='batch_endpoint_retirement_unproved'):
        runner.require_retired([link],[])
