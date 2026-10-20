"""Real callback control flow with modeled jobs; no native scope or device use."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

BENCH=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
ERROR_SCHEMA='meshticulum.management-file-RPC-error.v1'


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(BENCH))
    spec=importlib.util.spec_from_file_location('fast_enrollment_callback',
        BENCH/'run_public_gfsk250_mb1_fast_bundle100k.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


@pytest.mark.parametrize('remaining',[9,30,75,125,200])
def test_enrollment_leaves_original_phase_and_whole_cleanup_reserves(runner,remaining):
    phase=remaining*1000000;whole=phase-5000000
    assert runner.native_callback_seconds('enroll',phase,0)==min(120,remaining-8)
    assert runner.native_callback_seconds('enroll',phase,0)*1000000<=whole-3000000


def test_sanitized_error_record_retains_hash_and_type_without_metadata(runner):
    record=dict(schema=ERROR_SCHEMA,error_type='FenceError',reason='management_file_RPC_refused')
    output=b'SOURCE_ONLY_SECRET_METADATA\n'+json.dumps(record).encode()+b'\n'
    value=runner.rpc_failure_diagnostic(output,1)
    assert value==dict(output_sha256=runner.sha(output),failure_kind='remote_callback_failed',
        remote_error_type='FenceError',remote_reason='management_file_RPC_refused')
    assert 'SOURCE_ONLY_SECRET_METADATA' not in json.dumps(value)


@pytest.mark.parametrize('fault',['duplicate','message','wrong_reason','invalid_type','truncated','non_utf8'])
def test_malformed_or_ambiguous_output_is_not_echoed_or_promoted(runner,fault):
    record=dict(schema=ERROR_SCHEMA,error_type='WireError',reason='management_file_RPC_refused')
    if fault=='message':record['message']='SOURCE_ONLY_SECRET_METADATA'
    elif fault=='wrong_reason':record['reason']='SOURCE_ONLY_SECRET_METADATA'
    elif fault=='invalid_type':record['error_type']='SOURCE_ONLY_SECRET_METADATA/private/path'
    output=json.dumps(record).encode()
    if fault=='duplicate':output+=b'\n'+output
    elif fault=='truncated':output=output[:-1]
    elif fault=='non_utf8':output=b'\xff'+output
    value=runner.rpc_failure_diagnostic(output,124)
    assert value['failure_kind']=='remote_callback_timeout'
    assert value['remote_error_type'] is value['remote_reason'] is None
    assert value['output_sha256']==runner.sha(output)
    assert 'SOURCE_ONLY_SECRET_METADATA' not in json.dumps(value)


@pytest.mark.parametrize('failure',['none','timeout','remote_error','local_deadline','scope','cleanup','timeout_cleanup'])
def test_exact_enrollment_callback_keeps_original_bounds_and_cleanup(runner,monkeypatch,failure):
    from tests.test_mb1_bundle_operator import file_operator
    payload=dict(source_only_metadata=True);operator,state,stages=file_operator(monkeypatch,runner,payload)
    monkeypatch.setattr(runner.time,'monotonic_ns',lambda:int(state['mono']*1000000000))
    packet=dict(runtime_sha256='a'*64,manifest_sha256='b'*64,native_adapter_sha256='c'*64)
    cfg=dict(units={'unit-c':dict(python='/usr/bin/python3')},bundle_parent_id='d'*32,
        handoff_end_epoch_us=1200000000)
    operator.passwords={};operator._argv=lambda *args,**kwargs:['inert-SSH']
    lifecycle=[];jobs=[];owned=[];result=dict(rpc_clients=[])
    record=dict(schema='meshticulum.private-resume-native-rpc.v1',unit='unit-c',action='enroll',
        radio_opened=False,private_production=False)
    if failure=='scope':record['unit']='unit-a'
    success=json.dumps(record).encode()
    error=json.dumps(dict(schema=ERROR_SCHEMA,error_type='WireError',reason='management_file_RPC_refused')).encode()
    class Job:
        def __init__(self,argv,**kwargs):
            self.deadline=kwargs['deadline'];assert self.deadline==620.0
            self.output=success;self.process=SimpleNamespace(returncode=None,poll=lambda:self.process.returncode)
            jobs.append(self);lifecycle.append('start')
        def wait(self):
            state['epoch']+=90000000;state['mono']+=90
            assert state['mono']<self.deadline
            if failure=='local_deadline':raise RuntimeError('native_management_deadline')
            self.process.returncode=124 if failure in ('timeout','timeout_cleanup') else 1 if failure=='remote_error' else 0
            if self.process.returncode:self.output=error
            return self.process.returncode
        def close(self):
            lifecycle.append('close')
            if self.process.returncode is None:
                self.process.returncode=-15;self.output=success # Late output cannot prove success.
    def cleanup(unit,command,schema,**kwargs):
        lifecycle.append('cleanup')
        assert kwargs==dict(root=True,cleanup=True,cleanup_transport_headroom=2)
        if failure in ('cleanup','timeout_cleanup'):raise RuntimeError('cleanup_failed')
        return dict(unit=unit,nonce=command[15],cleaned=True,native_dispatch_called=False,already_absent=True)
    operator._command=cleanup
    native=next(n for n in ast.walk(ast.parse(Path(runner.__file__).read_bytes()))
        if isinstance(n,ast.FunctionDef) and n.name=='native')
    env=dict(vars(runner),operator=operator,cfg=cfg,op=packet,args=SimpleNamespace(operation_sha256='e'*64),
        end=1200000000,Job=Job,one_record=lambda raw,schema:json.loads(raw),owned=owned,result=result,sys=sys)
    exec(compile(ast.Module(body=[native],type_ignores=[]),'exact-enrollment-callback','exec'),env)
    if failure=='none':assert env['native']('unit-c','enroll',payload)==record
    else:
        expected='batch_native_RPC_failed' if failure in ('timeout','remote_error','timeout_cleanup') else \
            'native_management_deadline' if failure=='local_deadline' else \
            'batch_native_scope_changed' if failure=='scope' else 'cleanup_failed'
        with pytest.raises(RuntimeError,match=expected):env['native']('unit-c','enroll',payload)
    assert lifecycle==['start','close','cleanup'] and owned==jobs and len(stages)==1
    audit=result['rpc_clients'][0]
    assert audit['callback_budget_seconds']==120 and audit['original_callback_end_epoch_us']==1120000000
    assert audit['original_local_callback_deadline_monotonic_us']==620000000 and audit['callback_elapsed_us']==93000000
    assert cfg['handoff_end_epoch_us']==1200000000 and operator.deadline==700.0
    assert audit['local_terminal'] is True
    assert audit['remote_terminal'] is (failure in ('none','cleanup'))
    assert audit['management_input_cleanup_confirmed'] is (failure not in ('cleanup','timeout_cleanup'))
    if failure in ('timeout','timeout_cleanup'):
        assert audit['failure']['failure_kind']=='remote_callback_timeout' and audit['returncode']==124
    elif failure in ('local_deadline','scope'):
        assert audit['failure']['failure_kind']=='callback_outcome_unproved'
    assert 'source_only_metadata' not in json.dumps(audit)
