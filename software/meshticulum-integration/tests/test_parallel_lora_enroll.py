"""Real local Job subprocesses exercise the bounded enrollment orchestration."""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sys
from threading import Lock, Thread
import time

import pytest

from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps
from lantern_meshticulum.bench.pilot_ssh import Job
from meshticulum.bench.wire import WireError
from tests.test_lora_comparison import value


@pytest.fixture
def runner(monkeypatch):
    bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    monkeypatch.syspath_prepend(str(bench))
    spec=importlib.util.spec_from_file_location('parallel_enroll_runner',bench/'run_public_lora_comparison.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def inputs():
    context,_,now=value();policy=context.policy
    captures={u:dict(parent_id=policy.parent_id,phase_start_epoch_us=policy.issued_epoch_us,
        phase_end_epoch_us=context.phase_end_epoch_us,review_sha256='a'*64) for u in ('unit-a','unit-b','unit-c')}
    owners={u:dict(unit=u,policy_digest=policy.digest) for u in captures}
    priors={u:dict(unit=u,full_caps=asdict(policy.unit(u).prior_debt),refunds=0) for u in captures}
    return policy,owners,priors,captures,now


CHILD='''import json,sys,time
from pathlib import Path
root=Path(sys.argv[1]);unit=sys.argv[2];packet=json.loads((root/(unit+'.json')).read_text())
assert packet['action']=='enroll' and packet['payload']['owner']['unit']==unit
(root/('started-'+unit)).write_text('started')
while not (root/('release-'+unit)).exists():time.sleep(.005)
print(json.dumps(dict(schema='local-orchestration-child.v1',unit=unit)))
raise SystemExit(17 if unit==sys.argv[3] else 0)
'''


def wait_for(predicate,seconds=2):
    end=time.monotonic()+seconds
    while not predicate() and time.monotonic()<end:time.sleep(.005)
    assert predicate()


@pytest.mark.parametrize('failed_unit,error_type',[(None,RuntimeError),('unit-c',RuntimeError),
    ('unit-b',RuntimeError),('unit-a',RuntimeError),('unit-c',KeyboardInterrupt)])
def test_all_enrollments_overlap_close_and_join_before_worker_continuation(
        runner,tmp_path,failed_unit,error_type):
    policy,owners,priors,captures,now=inputs()
    original=deepcopy((policy.to_dict(),owners,priors,captures))
    parent=ParentCaps.enroll(tmp_path/'caps.sqlite',policy,clock=lambda:tuple(now),boot_reader=lambda:policy.controller_boot_id)
    before=parent.snapshot();jobs={};closed=set();lock=Lock();outcome={};workers=[]
    original_deadline=time.monotonic()+5
    def native(unit,action,packet):
        assert action=='enroll' and packet==dict(policy=original[0],owner=owners[unit],prior=priors[unit])
        (tmp_path/(unit+'.json')).write_text(json.dumps(dict(action=action,payload=packet)))
        job=Job([sys.executable,'-B','-c',CHILD,str(tmp_path),unit,failed_unit or '-'],deadline=original_deadline)
        with lock:jobs[unit]=job
        try:
            if job.wait()!=0:raise error_type('enroll_rejected_'+unit)
            return dict(unit=unit,source_only=True)
        finally:
            job.close()
            with lock:closed.add(unit)
    def controller():
        try:
            outcome['receipts']=runner.parallel_enroll(native,policy,owners,priors,captures)
            workers.append('all-enrolled')
        except BaseException as error:outcome['error']=error
    thread=Thread(target=controller)
    try:
        thread.start()
        # All three genuine children must be live at once; a sequential caller
        # cannot reach this point while their release files remain absent.
        wait_for(lambda:all((tmp_path/('started-'+u)).exists() for u in captures))
        assert thread.is_alive() and not workers
        if failed_unit:
            (tmp_path/('release-'+failed_unit)).touch()
            wait_for(lambda:failed_unit in closed)
            assert thread.is_alive() and not workers
        for unit in captures:(tmp_path/('release-'+unit)).touch()
        thread.join(timeout=2)
        assert not thread.is_alive() and closed==set(captures)
        assert all(job.closed and job.process.poll() is not None and job.deadline==original_deadline for job in jobs.values())
        assert parent.snapshot()==before
        assert (policy.to_dict(),owners,priors,captures)==original
        if failed_unit:
            assert type(outcome['error']) is error_type and workers==[]
        else:
            assert workers==['all-enrolled'] and set(outcome['receipts'])==set(captures)
    finally:
        for unit in captures:(tmp_path/('release-'+unit)).touch()
        thread.join(timeout=2)
        for job in jobs.values():job.close()
        parent.close()


@pytest.mark.parametrize('field',['parent_id','phase_start_epoch_us','phase_end_epoch_us','review_sha256'])
def test_original_capture_binding_mismatch_starts_no_enrollment(runner,field):
    policy,owners,priors,captures,_=inputs();old=captures['unit-a'][field]
    captures['unit-a'][field]=old+1 if type(old) is int else 'f'*len(old)
    calls=[]
    with pytest.raises(RuntimeError,match='capture_original_scope_changed'):
        runner.parallel_enroll(lambda *args:calls.append(args),policy,owners,priors,captures)
    assert not calls


def test_original_job_deadline_is_never_renewed_and_all_children_close(runner,tmp_path):
    policy,owners,priors,captures,_=inputs();jobs=[];workers=[]
    original_deadline=time.monotonic()+.35
    def native(unit,action,packet):
        job=Job([sys.executable,'-B','-c','import time; time.sleep(10)'],deadline=original_deadline)
        jobs.append(job)
        try:job.wait()
        finally:job.close()
    with pytest.raises(WireError,match='native_management_deadline'):
        runner.parallel_enroll(native,policy,owners,priors,captures)
        workers.append('not-authorized')
    assert len(jobs)==3 and workers==[]
    assert all(job.closed and job.process.poll() is not None and job.deadline==original_deadline for job in jobs)
