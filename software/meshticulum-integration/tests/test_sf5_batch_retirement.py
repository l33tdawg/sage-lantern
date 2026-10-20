"""Actual Controller retirement with explicit local transport fault models."""
import importlib.util
from pathlib import Path
import sys
import pytest
from lantern_meshticulum.bench.pilot_controller import Controller

ROOT=Path(__file__).resolve().parents[3]
folder=ROOT/'firmware/meshticulum-bench';sys.path.insert(0,str(folder))
spec=importlib.util.spec_from_file_location('batch_retirement_runner',folder/'run_public_sf5_batch.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


class LocalOperator:
    def __init__(self,fault=None):self.fault=fault;self.calls=[];self.status_calls=[]
    def revoke(self,unit,run,transport):
        self.calls.append((unit,run))
        if unit=='unit-a' and self.fault=='raise':raise RuntimeError('modeled retirement failure')
        return dict(unit=unit,run_id=run,retired=not(unit=='unit-a' and self.fault=='unconfirmed'))
    def stop(self,unit,run):raise RuntimeError('modeled stop failure')
    def status(self,unit,run):self.status_calls.append((unit,run));return dict(live=False)


def controller(operator):
    value=Controller.__new__(Controller);value.operator=operator;value.cleanup=[];value.links=[]
    for run in ('a'*32,'b'*32):
        value.links.append(dict(run_id=run,transport='lora',attempted=['unit-a','unit-c'],
            prepared=['unit-a','unit-c'],retired=[],phase='complete',
            outcomes={u:dict(complete=True,disarmed_on_exit=True) for u in ('unit-a','unit-c')}))
    return value


def finish(value,result):
    return runner.finish_sample(value.operator,value,result,index=0,original_application={},verified={},metrics={})


def test_sample_advance_requires_all_real_retirement_receipts():
    current=controller(LocalOperator());result=dict(samples=[],links=[])
    current=finish(current,result)
    assert current is None and len(result['samples'])==1 and len(result['links'])==2
    assert len(result['samples'][0]['cleanup'])==4
    assert len(result['sample_cleanup'][0]['cleanup'])==4
    assert all(set(row['retired'])==set(row['prepared'])==set(row['attempted']) for row in result['links'])


@pytest.mark.parametrize('fault',['raise','unconfirmed','stop'])
def test_returned_retirement_failure_keeps_current_and_never_advances_sample(fault):
    op=LocalOperator(fault);current=controller(op);original=current;result=dict(samples=[],links=[])
    if fault=='stop':current._retire(current.links[0],stop=True)
    with pytest.raises(RuntimeError,match='batch_retirement_errors'):
        current=finish(current,result)
    assert current is original and result['samples']==result['links']==[]
    assert result['sample_cleanup'] and not op.status_calls


def test_finally_retry_success_preserves_original_error_and_marks_incomplete():
    op=LocalOperator('raise');current=controller(op);result=dict(complete=True,samples=[],links=[])
    with pytest.raises(RuntimeError):finish(current,result)
    op.fault=None;runner.cleanup_current(current,result)
    assert result['complete'] is False and result['cleanup_error']=='current_controller_cleanup_unproved'
    assert any('retirement_error' in row for row in result['cleanup'])
    assert all(set(row['retired'])=={'unit-a','unit-c'} for row in current.links)


def test_finally_checks_returned_errors_without_raised_exception():
    current=controller(LocalOperator('unconfirmed'));result=dict(complete=True)
    runner.cleanup_current(current,result)
    assert result['complete'] is False and any('retirement_error' in row for row in result['cleanup'])


@pytest.mark.parametrize('fault',['missing_prepared','missing_retired','missing_receipt','empty_error'])
def test_missing_retirement_scope_or_receipt_cannot_be_promoted(fault):
    current=controller(LocalOperator());cleanup=current.retire()
    if fault=='missing_prepared':current.links[0]['prepared'].remove('unit-a')
    elif fault=='missing_retired':current.links[0]['retired'].remove('unit-a')
    elif fault=='missing_receipt':cleanup.pop()
    else:cleanup[0]['retirement_error']=''
    with pytest.raises(RuntimeError):runner.require_retired(current.links,cleanup,completed=True)
