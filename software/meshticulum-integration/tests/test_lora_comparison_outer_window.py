"""Original outer-clock controls, without live windows, credentials or devices."""
from copy import deepcopy
import os

import pytest

from tests.test_lora_comparison_runner import runner

BOOT='00000000-0000-4000-8000-000000000001'


def operation():
    return dict(absolute_device_window=dict(issued_epoch_us=1000000000,expires_epoch_us=1720000000,count_authorized=1),
        original_outer_monotonic_us=2000000000,original_outer_deadline_monotonic_us=2720000000,
        original_outer_controller_boot=BOOT)


def window(runner):
    op=operation();now=[1010000000,2010000000];boots=[BOOT];calls=[]
    def boot():calls.append('boot');return boots[0]
    return runner.OuterWindow(op,clock=lambda:tuple(now),boot_reader=boot),op,now,boots,calls


@pytest.mark.parametrize('field,value',[('epoch_end',1720000001),('mono_end',2720000001),
    ('epoch_start',True),('mono_start',None),('boot',None),('count',True)])
def test_exact_original_720_seconds_and_typed_fields_required(runner,field,value):
    op=operation()
    if field=='epoch_end':op['absolute_device_window']['expires_epoch_us']=value
    elif field=='mono_end':op['original_outer_deadline_monotonic_us']=value
    elif field=='epoch_start':op['absolute_device_window']['issued_epoch_us']=value
    elif field=='mono_start':op['original_outer_monotonic_us']=value
    elif field=='boot':op['original_outer_controller_boot']=value
    else:op['absolute_device_window']['count_authorized']=value
    with pytest.raises((RuntimeError,ValueError)):
        runner.OuterWindow(op,clock=lambda:(1010000000,2010000000),boot_reader=lambda:BOOT)


def test_same_original_600_phase_fits_both_original_720_ends(runner):
    outer,op,now,boots,calls=window(runner)
    now[:]=[1120000000,2120000000]
    assert outer.phase()==tuple(now)
    assert now[0]+600000000==op['absolute_device_window']['expires_epoch_us']
    assert now[1]+600000000==op['original_outer_deadline_monotonic_us']
    now[:]=[1120000001,2120000001]
    with pytest.raises(RuntimeError,match='cannot_fit_phase'):outer.phase()


def test_epoch_rollback_cannot_map_a_later_600_phase_past_original_monotonic_end(runner):
    outer,op,now,boots,calls=window(runner)
    # The old epoch-only condition accepts this: a rolled-back epoch has600s
    # left, while the original monotonic interval has only590s remaining.
    now[:]=[1100000000,2130000000]
    assert now[0]+600000000<=op['absolute_device_window']['expires_epoch_us']
    with pytest.raises(RuntimeError,match='cannot_fit_phase'):outer.phase()


@pytest.mark.parametrize('index',[0,1])
def test_clock_regression_refuses(runner,index):
    outer,op,now,boots,calls=window(runner);now[index]-=1
    with pytest.raises(RuntimeError,match='expired_or_regressed'):outer.check()


@pytest.mark.parametrize('index',[0,1])
def test_either_original_end_expires_even_when_other_clock_remains_live(runner,index):
    outer,op,now,boots,calls=window(runner)
    now[index]=op['absolute_device_window']['expires_epoch_us'] if index==0 else op['original_outer_deadline_monotonic_us']
    with pytest.raises(RuntimeError,match='expired_or_regressed'):outer.check()


def test_boot_change_at_effect_boundary_and_process_change_refuse(runner,monkeypatch):
    outer,op,now,boots,calls=window(runner)
    boots[0]='00000000-0000-4000-8000-000000000002'
    with pytest.raises(RuntimeError,match='boot_changed'):outer.boundary()
    boots[0]=BOOT;monkeypatch.setattr(runner.os,'getpid',lambda:outer._pid+1)
    with pytest.raises(RuntimeError,match='outer_rebound'):outer.check()


def test_clock_reads_do_not_spawn_duplicate_boot_observers(runner):
    outer,op,now,boots,calls=window(runner);assert len(calls)==1
    for _ in range(50):outer.monotonic();outer.epoch()
    assert len(calls)==1
    outer.phase();assert len(calls)==2


def test_binding_cannot_be_renewed_after_construction(runner):
    outer,op,now,boots,calls=window(runner)
    op['absolute_device_window']['expires_epoch_us']+=1000000
    op['absolute_device_window']['issued_epoch_us']+=1000000
    with pytest.raises(RuntimeError,match='outer_rebound'):outer.check()


def test_prephase_read_crossing_original_end_refuses_and_later_read_is_not_called(runner,monkeypatch):
    outer,op,now,boots,calls=window(runner);reads=[]
    def read(*args):reads.append(args);now[1]=op['original_outer_deadline_monotonic_us'];return b'{}'
    monkeypatch.setattr(runner,'read',read)
    with pytest.raises(RuntimeError,match='expired_or_regressed'):outer.read('modeled-prior.json')
    assert len(reads)==1
    with pytest.raises(RuntimeError,match='expired_or_regressed'):outer.read('later-modeled-prior.json')
    assert len(reads)==1


def test_cleanup_job_wait_is_clamped_to_original_outer_end(monkeypatch):
    from lantern_meshticulum.bench import pilot_ssh as ssh
    from tests.test_pilot_ssh import config
    cfg,source=config();now=[10.0];seen=[]
    class Process:
        def poll(self):return 0
    class Job:
        def __init__(self,argv,*,password,deadline,clock):
            seen.append(deadline);self.output=bytearray(b'{"schema":"modeled-cleanup"}\n');self.process=Process()
        def wait(self):return 0
        def close(self):pass
    monkeypatch.setattr(ssh,'Job',Job)
    operator=ssh.SSHOperator(cfg,provision_source=source,clock=lambda:now[0],epoch=lambda:1000000000,
        outer_deadline_monotonic=12.0)
    assert operator.deadline==12.0
    assert operator._command('unit-a',['/usr/bin/true'],'modeled-cleanup',cleanup=True)['schema']=='modeled-cleanup'
    assert seen==[12.0] # Original5s cleanup would otherwise wait until15.
    now[0]=12.0
    with pytest.raises(Exception):operator._command('unit-a',['/usr/bin/true'],'modeled-cleanup',cleanup=True)
    assert seen==[12.0]


@pytest.mark.parametrize('damage',['deleted','none','later'])
@pytest.mark.parametrize('method',['argv','command'])
def test_comparison_operator_refuses_changed_outer_cap_before_dispatch(runner,monkeypatch,damage,method):
    from lantern_meshticulum.bench import pilot_ssh as ssh
    from tests.test_pilot_ssh import config
    outer,op,now,boots,calls=window(runner);cfg,source=config()
    operator=runner.outer_ssh_operator_type(ssh.SSHOperator,outer)(cfg,provision_source=source,
        clock=outer.monotonic,epoch=outer.epoch,outer_deadline_monotonic=outer.deadline)
    if damage=='deleted':del operator._outer_deadline_monotonic
    elif damage=='none':operator._outer_deadline_monotonic=None
    else:operator._outer_deadline_monotonic=outer.deadline+1
    monkeypatch.setattr(ssh,'Job',lambda *a,**k:pytest.fail('changed cap must not open a job'))
    with pytest.raises(RuntimeError,match='ssh_outer_deadline_changed'):
        if method=='argv':operator._argv('unit-a',['/usr/bin/true'],root=True)
        else:operator._command('unit-a',['/usr/bin/true'],'modeled-cleanup',cleanup=True)


def test_comparison_operator_requires_an_admitted_cap_even_in_unconstructed_model(runner):
    from lantern_meshticulum.bench import pilot_ssh as ssh
    outer,*_=window(runner);kind=runner.outer_ssh_operator_type(ssh.SSHOperator,outer)
    operator=object.__new__(kind)
    with pytest.raises(RuntimeError,match='ssh_outer_deadline_changed'):
        operator._command('unit-a',['/usr/bin/true'],'modeled-cleanup',cleanup=True)


def test_original_default_operator_cleanup_keeps_original_five_seconds(monkeypatch):
    from lantern_meshticulum.bench import pilot_ssh as ssh
    from tests.test_pilot_ssh import config
    cfg,source=config();seen=[]
    class Process:
        def poll(self):return 0
    class Job:
        def __init__(self,argv,*,password,deadline,clock):
            seen.append(deadline);self.output=bytearray(b'{"schema":"modeled-cleanup"}\n');self.process=Process()
        def wait(self):return 0
        def close(self):pass
    monkeypatch.setattr(ssh,'Job',Job)
    operator=ssh.SSHOperator(cfg,provision_source=source,clock=lambda:10.0,epoch=lambda:1000000000)
    assert operator._outer_deadline_monotonic is None
    operator._command('unit-a',['/usr/bin/true'],'modeled-cleanup',cleanup=True)
    assert seen==[15.0]
