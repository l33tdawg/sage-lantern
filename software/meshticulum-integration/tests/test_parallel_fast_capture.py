# SPDX-License-Identifier: Apache-2.0
"""Concurrent controller calls with bounded modeled native callbacks; no RF."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest


@pytest.fixture
def runner(monkeypatch):
    bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    monkeypatch.syspath_prepend(str(bench))
    spec=importlib.util.spec_from_file_location('parallel_capture_runner',
        bench/'run_public_gfsk250_mb1_fast_bundle100k.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def payloads():
    return {u:dict(parent_id='a'*32,phase_start_epoch_us=1000000,
        phase_end_epoch_us=1356000000,review_sha256='b'*64,
        history=dict(unit=u),facts=dict(unit=u)) for u in ('unit-a','unit-b','unit-c')}


def test_captures_overlap_with_distinct_unit_inputs_and_original_scope(runner):
    packets=payloads();before=deepcopy(packets);entered=Barrier(3,timeout=2)
    closed=set();calls=[];lock=Lock()
    def native(unit,action,packet):
        assert action=='capture' and packet is packets[unit]
        assert packet['history']['unit']==packet['facts']['unit']==unit
        with lock:calls.append(unit)
        try:
            # A sequential runner cannot pass this barrier.
            entered.wait()
            return dict(unit=unit,actual_history_verified=True,refunds=0)
        finally:
            with lock:closed.add(unit)
    observed=runner.parallel_captures(native,packets)
    assert set(observed)==closed==set(packets)
    assert sorted(calls)==sorted(packets) and packets==before
    assert all(observed[u]['unit']==u for u in packets)


@pytest.mark.parametrize('failed_unit',['unit-a','unit-b','unit-c'])
def test_failed_capture_joins_every_started_callback_before_enrollment(runner,failed_unit):
    packets=payloads();entered=Barrier(3,timeout=2);release=Event()
    closed=set();calls=[];lock=Lock();enrolled=False
    def native(unit,action,packet):
        with lock:calls.append(unit)
        try:
            entered.wait()
            if unit==failed_unit:
                release.set()
                raise RuntimeError('capture_rejected_'+unit)
            assert release.wait(2)
            return dict(unit=unit)
        finally:
            with lock:closed.add(unit)
    with pytest.raises(RuntimeError,match='capture_rejected_'+failed_unit):
        runner.parallel_captures(native,packets)
        enrolled=True
    assert closed==set(packets) and sorted(calls)==sorted(packets)
    assert enrolled is False


@pytest.mark.parametrize('field',['parent_id','phase_start_epoch_us','phase_end_epoch_us','review_sha256'])
def test_mismatched_original_scope_dispatches_nothing(runner,field):
    packets=payloads();value=packets['unit-a'][field]
    packets['unit-a'][field]=value+1 if type(value) is int else 'c'*len(value)
    calls=[]
    with pytest.raises(RuntimeError,match='capture_original_scope_changed'):
        runner.parallel_captures(lambda *args:calls.append(args),packets)
    assert calls==[]


def test_missing_unit_dispatches_nothing(runner):
    packets=payloads();del packets['unit-b'];calls=[]
    with pytest.raises(RuntimeError,match='capture_units_changed'):
        runner.parallel_captures(lambda *args:calls.append(args),packets)
    assert calls==[]
