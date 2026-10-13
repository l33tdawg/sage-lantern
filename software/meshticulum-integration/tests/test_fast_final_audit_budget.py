"""Fast final-accounting callback bounds; no device or clocked scope issued."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

BENCH=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(BENCH))
    spec=importlib.util.spec_from_file_location('fast_final_audit_budget',BENCH/'run_public_gfsk250_mb1_fast_bundle100k.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def test_full_history_audit_gets120_but_existing_controls_keep45(runner):
    assert runner.native_callback_seconds('audit',200000000,0)==120
    assert runner.native_callback_seconds('capture',200000000,0)==120
    for action in ('guard','enroll','observe-cache'):
        assert runner.native_callback_seconds(action,200000000,0)==45


@pytest.mark.parametrize('remaining',[9,30,75,125,200])
def test_audit_fits_original_whole_and_leaves_original_phase_cleanup_reserve(runner,remaining):
    phase=remaining*1000000;whole=phase-5000000
    seconds=runner.native_callback_seconds('audit',phase,0)
    assert seconds==min(120,remaining-8) and seconds*1000000<=whole-3000000
    assert phase==remaining*1000000 and whole==phase-5000000


@pytest.mark.parametrize('remaining',[8,7,0,-1])
def test_exhausted_original_audit_window_refuses_before_staging(runner,remaining):
    with pytest.raises(RuntimeError,match='batch_original_remote_window_expired'):
        runner.native_callback_seconds('audit',remaining*1000000,0)


def test_audit_budget_reaches_real_file_command_and_staging_cannot_refresh_it(runner,monkeypatch):
    import management_file_rpc as file_rpc
    from lantern_meshticulum.bench.management_input import SCHEMA
    now=[1791878000000000,10.0];calls=[]
    monkeypatch.setattr(runner.time,'time_ns',lambda:now[0]*1000)
    monkeypatch.setattr(runner.secrets,'token_hex',lambda n:'c'*32)
    class Operator:
        deadline=145.0
        def clock(self):return now[1]
        def stage_management_input(self,unit,action,payload,**kwargs):
            calls.append(dict(unit=unit,action=action,payload=payload,**kwargs))
            now[0]+=3000000;now[1]+=3
            return dict(schema=SCHEMA,unit=unit,action=action,parent_id=kwargs['parent_id'],
                operation_sha256=kwargs['operation_sha256'],nonce=kwargs['nonce'],
                original_end_epoch_us=kwargs['original_end_epoch_us'],owner_uid=1000,
                file_identity=[1]*9,directory_identity=[1]*5,path='/SOURCE_ONLY/private/input.xz',
                compressed_sha256='d'*64,raw_sha256='e'*64)
    op=Operator();start=tuple(now);phase=start[0]+135000000;cfg=dict(handoff_end_epoch_us=phase,
        bundle_parent_id='a'*32,units={'unit-c':dict(python='/usr/bin/python3')})
    packet=dict(runtime_sha256='f'*64,manifest_sha256='f'*64,native_adapter_sha256='b'*64)
    command=runner.rpc_command(op,cfg,packet,'unit-c','audit',dict(source_only_model=True),
        runner.native_callback_seconds('audit',phase,now[0]),operation_sha256='b'*64)
    assert command[2]=='117s' and command.original_end_epoch_us==start[0]+120000000
    assert command.local_deadline==130.0 and op.deadline==145.0 and cfg['handoff_end_epoch_us']==phase
    assert calls[0]['original_end_epoch_us']==command.original_end_epoch_us
    now[0]+=60000000;now[1]+=60
    assert now[1]<command.local_deadline and now[0]<command.original_end_epoch_us
    # A separately evaluated callback never moves the original phase end.
    assert runner.native_callback_seconds('audit',phase,now[0])==64
    assert cfg['handoff_end_epoch_us']==phase


def test_failed_final_or_cleanup_evidence_remains_incomplete(runner):
    result={'complete':False,'native_final':{}}
    current=SimpleNamespace(links=[],retire=lambda:[dict(retirement_error='model')])
    runner.cleanup_current(current,result)
    assert result['complete'] is False and result['native_final']=={}
