"""Explicit telemetry launch selection, with inert jobs and no native actions."""
import ast
import importlib.util
from pathlib import Path
import shlex

import pytest

from lantern_meshticulum.bench import pilot_ssh
from meshticulum.bench.wire import WireError
from tests.test_pilot_ssh import config

BENCH=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'


@pytest.fixture
def starts(monkeypatch):
    seen=[]
    def job(argv,**kwargs):
        seen.append((shlex.split(argv[-1]),kwargs));return object()
    monkeypatch.setattr(pilot_ssh,'Job',job)
    def make(**kwargs):
        cfg,source=config()
        return pilot_ssh.SSHOperator(cfg,provision_source=source,clock=lambda:10,epoch=lambda:1000000000,**kwargs)
    return make,seen


@pytest.mark.parametrize('value',[None,0,1,'true',{},[]])
def test_nonbool_selection_refuses_before_any_job(starts,value):
    make,seen=starts
    with pytest.raises(WireError,match='invalid_native_phase_timing_mode'):make(native_phase_timing=value)
    assert not seen


@pytest.mark.parametrize('transport',['lora','tcp','websocket'])
def test_default_start_keeps_existing_action_and_no_profiler_argument(starts,transport):
    make,seen=starts;op=make()
    op.start('unit-a',dict(run_id='1'*32,transport=transport,sender='unit-a',receiver='unit-c'))
    args,options=seen[0]
    assert '--native-phase-timing' not in args
    assert ('service-run' if transport=='lora' else 'ip-service-run') in args
    assert options['deadline']==op.deadline-10 and op.native_phase_timing is False


def test_explicit_true_is_forwarded_only_to_radio_service_and_keeps_receive_guard(starts):
    make,seen=starts;op=make(native_phase_timing=True)
    op.start('unit-a',dict(run_id='1'*32,transport='lora',sender='unit-a',receiver='unit-c',sender_receive_ready=True))
    args,options=seen[0]
    assert args.count('--native-phase-timing')==1 and 'service-run' in args
    assert '--sender-receive-ready' in args and options['deadline']==op.deadline-10


@pytest.mark.parametrize('transport',['tcp','websocket','ip','unknown'])
def test_enabled_IP_or_foreign_transport_refuses_before_job(starts,transport):
    make,seen=starts;op=make(native_phase_timing=True)
    with pytest.raises(WireError,match='native_phase_timing_requires_radio_service'):
        op.start('unit-a',dict(run_id='1'*32,transport=transport,sender='unit-a',receiver='unit-c'))
    assert not seen and op.jobs=={}


def test_expired_start_still_refuses_before_any_profiler_launch(starts):
    make,seen=starts;op=make(native_phase_timing=True);op.deadline=10
    with pytest.raises(WireError,match='pilot_job_duplicate_or_expired'):
        op.start('unit-a',dict(run_id='1'*32,transport='lora',sender='unit-a',receiver='unit-c'))
    assert not seen


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(BENCH))
    spec=importlib.util.spec_from_file_location('profile_selection_controls',BENCH/'run_public_gfsk125_bundle100k.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_operation_opt_in_is_explicit_and_default_off(runner):
    assert runner.phase_timing_mode({}) is False
    assert runner.phase_timing_mode({'native_phase_timing':False}) is False
    assert runner.phase_timing_mode({'native_phase_timing':True}) is True


@pytest.mark.parametrize('value',[None,0,1,'true',{},[]])
def test_operation_nonbool_refuses_without_runner_execution(runner,value):
    with pytest.raises(RuntimeError,match='invalid_native_phase_timing_mode'):
        runner.phase_timing_mode({'native_phase_timing':value})


@pytest.mark.parametrize('value',[False,True])
def test_operation_value_reaches_actual_constructor_expression_and_metadata(runner,value):
    tree=ast.parse((BENCH/'run_public_gfsk125_bundle100k.py').read_bytes())
    init=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='SSHOperator')
    observed=[]
    env=dict(SSHOperator=lambda *a,**k:observed.append(k),cfg={},provision=b'',bootstrap=b'',password=None,
        op={'native_phase_timing':value},phase_timing_mode=runner.phase_timing_mode)
    eval(compile(ast.Expression(init),'[inert-launch-constructor]','eval'),env)
    assert len(observed)==1 and observed[0]['native_phase_timing'] is value
    assert observed[0]['parallel_preparation'] is True
    values=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='dict'
            and any(k.arg=='schema' and isinstance(k.value,ast.Constant) and k.value.value=='meshticulum.public-gfsk125-bundle100k-result.v1' for k in n.keywords)]
    assert len(values)==1 and any(k.arg=='native_phase_timing_enabled' for k in values[0].keywords)
    from lantern_meshticulum.bench import node
    source=Path(node.__file__).read_text()
    assert "'native_phase_timing_requested': native_phase_timing" in source
    assert "'native_phase_timing_enabled': False" in source
    assert "result['native_phase_timing_enabled']=True" in source
