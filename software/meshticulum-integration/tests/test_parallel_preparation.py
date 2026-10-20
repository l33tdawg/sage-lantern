# SPDX-License-Identifier: Apache-2.0
"""Actual controller ledger and concurrent callbacks; modeled native receipts."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Event,Lock
from types import SimpleNamespace
import time
import pytest
from lantern_meshticulum.bench.pilot_ssh import SSHOperator
from meshticulum.bench.wire import WireError
from tests.test_pilot_controller import controller,REQUEST


def paired(op,pilot,prepare):
    proxy=SimpleNamespace(parallel_preparation=True,_preparation_lock=Lock(),config={'units':dict.fromkeys(('unit-a','unit-b','unit-c'))},
        clock=time.monotonic,deadline=time.monotonic()+5,prepare=prepare)
    op.parallel_preparation=True
    op.prepare_pair=lambda packages:SSHOperator.prepare_pair(proxy,packages)
    return proxy


def test_distinct_preparations_overlap_and_both_validate_before_launch_without_double_payment():
    pilot,op,_=controller();original=op.prepare;both=Barrier(2);completed=[]
    def prepare(package):
        both.wait(timeout=2);result=original(package);completed.append(package['unit']);return result
    paired(op,pilot,prepare);start=op.start
    def checked_start(unit,pair):
        assert set(completed)=={'unit-a','unit-c'}
        return start(unit,pair)
    op.start=checked_start
    pilot.link('unit-c','unit-a','lora','MR1.request',REQUEST)
    assert pilot.links[0]['attempted']==['unit-a','unit-c']
    assert pilot.links[0]['prepared']==['unit-a','unit-c']
    assert pilot.parent.snapshot()['allocations']==1
    assert len([e for e in op.events if e[0]=='prepare'])==2


def test_one_refusal_waits_for_other_cleanup_before_any_dispatch_and_keeps_paid_pair():
    pilot,op,_=controller();original=op.prepare;other=Event();released=Event();done=[]
    def prepare(package):
        try:
            if package['unit']=='unit-c':
                other.set();assert released.wait(timeout=2);return original(package)
            assert other.wait(timeout=2);raise RuntimeError('actual preparation refused')
        finally:done.append(package['unit'])
    paired(op,pilot,prepare)
    with ThreadPoolExecutor(max_workers=1) as release:
        release.submit(lambda:(other.wait(timeout=2),time.sleep(.02),released.set()))
        with pytest.raises(RuntimeError):pilot.link('unit-c','unit-a','lora','MR1.request',REQUEST)
    assert set(done)=={'unit-a','unit-c'}
    assert not any(e[0]=='start' for e in op.events)
    assert pilot.links[0]['attempted']==['unit-a','unit-c'] and pilot.parent.snapshot()['allocations']==1


@pytest.mark.parametrize('attack',['mutation','identity','missing_receipt','deadline'])
def test_parallel_completion_cannot_bypass_main_thread_checks(attack):
    pilot,op,clock=controller();original=op.prepare
    def prepare(package):
        receipt=original(package)
        if package['unit']=='unit-c':
            if attack=='mutation':package['grant']['object']['bytes']+=1
            elif attack=='identity':receipt['parent']['unit']='unit-b'
            elif attack=='deadline':clock.now=201
        return receipt
    paired(op,pilot,prepare)
    if attack=='missing_receipt':
        paired_call=op.prepare_pair
        def missing(packages):
            receipts=paired_call(packages);receipts.pop('unit-c');return receipts
        op.prepare_pair=missing
    with pytest.raises((WireError,RuntimeError)):
        pilot.link('unit-c','unit-a','lora','MR1.request',REQUEST)
    assert not any(e[0]=='start' for e in op.events)
    assert pilot.parent.snapshot()['allocations']==1


def test_enabled_mode_has_no_silent_sequential_fallback():
    pilot,op,_=controller();op.parallel_preparation=True
    with pytest.raises(WireError,match='parallel_preparation_api_missing'):
        pilot.link('unit-c','unit-a','lora','MR1.request',REQUEST)
    assert not any(e[0] in ('prepare','start') for e in op.events)


def test_duplicate_endpoint_pair_refuses_before_callback():
    proxy=SimpleNamespace(parallel_preparation=True,config={'units':{'unit-a':{}}},prepare=lambda p:pytest.fail('unexpected prepare'))
    with pytest.raises(WireError):SSHOperator.prepare_pair(proxy,{'unit-a':{'unit':'unit-a'}})
