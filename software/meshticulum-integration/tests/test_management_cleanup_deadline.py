"""Bounded housekeeping transport and retained failure proof; no devices."""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from lantern_meshticulum.bench import pilot_ssh as ssh
from meshticulum.bench.wire import WireError
from tests.test_pilot_ssh import config

SCHEMA='meshticulum.management-input-cleaned.v1'
COMMAND=['/usr/bin/timeout','--kill-after=2s','5s','/usr/bin/python3','-I','-B','-c','pinned-entry','root','manifest','adapter','cleanup-management-input']

@pytest.fixture
def modeled(monkeypatch):
    cfg,source=config();now=[10.0];created=[]
    op=ssh.SSHOperator(cfg,provision_source=source,clock=lambda:now[0],epoch=lambda:1000000000)
    def argv(*a,**k):now[0]+=2;return ['inert-ssh']
    monkeypatch.setattr(op,'_argv',argv)
    class Job:
        def __init__(self,argv,**kwargs):
            self.deadline=kwargs['deadline'];self.process=SimpleNamespace(returncode=None,poll=lambda:self.process.returncode)
            self.output=bytearray();self.password='source-dummy';self.closed=False;created.append(self)
        def wait(self):
            now[0]+=6
            if now[0]>=self.deadline:raise WireError('native_management_deadline')
            self.process.returncode=0;self.output.extend(json.dumps(dict(schema=SCHEMA,cleaned=True)).encode());return 0
        def close(self):
            if self.process.returncode is None:
                self.process.returncode=-15
                self.output.extend(json.dumps(dict(schema=SCHEMA,cleaned=True,late=True)).encode())
            self.password=None;self.closed=True
    monkeypatch.setattr(ssh,'Job',Job)
    return op,now,created

def test_five_remote_seconds_keep_separate_transport_and_kill_grace(modeled):
    op,now,created=modeled;original=op.deadline
    value=op._command('unit-b',COMMAND,SCHEMA,root=True,cleanup=True,cleanup_transport_headroom=2)
    assert value['cleaned'] is True and op.deadline==original
    assert created[0].deadline==12+5+2+2
    audit=op.management_cleanup_audits[0]
    assert audit['remote_housekeeping_bound_s']==5 and audit['transport_headroom_s']==2
    assert audit['local_terminal'] and audit['closed'] and audit['credentials_cleared'] and audit['success']
    assert audit['returncode']==0 and audit['output'] and audit['output_sha256']
    assert op.management_cleanup_jobs==created

def test_legacy_command_deadline_is_unchanged_and_no_cleanup_headroom_added(modeled):
    op,now,created=modeled
    with pytest.raises(WireError,match='native_management_deadline'):op._command('unit-b',COMMAND,SCHEMA,root=True,cleanup=True)
    assert created[0].deadline==15 and not op.management_cleanup_audits

@pytest.mark.parametrize('fault',['dispatch','schema','remote_limit','cleanup_false','root_false'])
def test_transport_headroom_cannot_authorize_dispatch_or_other_management(modeled,fault):
    op,now,created=modeled;cmd=list(COMMAND);schema=SCHEMA;kwargs=dict(root=True,cleanup=True,cleanup_transport_headroom=2)
    if fault=='dispatch':cmd[11]='guard'
    elif fault=='schema':schema='different'
    elif fault=='remote_limit':cmd[2]='6s'
    elif fault=='cleanup_false':kwargs['cleanup']=False
    else:kwargs['root']=False
    with pytest.raises(WireError):op._command('unit-b',cmd,schema,**kwargs)
    assert not created

def test_lost_outcome_is_never_reclassified_after_close_even_with_late_receipt(modeled,monkeypatch):
    op,now,created=modeled
    class LostJob:
        def __init__(self,argv,**kwargs):
            self.process=SimpleNamespace(returncode=None,poll=lambda:self.process.returncode)
            self.output=bytearray();self.password='source-dummy';self.closed=False;created.append(self)
        def wait(self):raise WireError('native_management_deadline')
        def close(self):
            self.process.returncode=-15;self.closed=True;self.password=None
            self.output.extend(json.dumps(dict(schema=SCHEMA,cleaned=True,late=True)).encode())
    monkeypatch.setattr(ssh,'Job',LostJob)
    with pytest.raises(WireError,match='native_management_deadline'):
        op._command('unit-b',COMMAND,SCHEMA,root=True,cleanup=True,cleanup_transport_headroom=2)
    assert len(created)==1
    audit=op.management_cleanup_audits[0]
    assert audit['success'] is False and audit['error_type']=='WireError'
    assert audit['reason']=='native_management_deadline' and 'late' in audit['output']
    assert audit['closed'] and audit['local_terminal'] and audit['credentials_cleared']

def test_cleanup_entry_does_not_import_or_dispatch_heavy_native_module():
    path=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench/management_file_rpc.py'
    spec=importlib.util.spec_from_file_location('cleanup_entry_controls',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    tree=ast.parse(module.RPC_FILE_ENTRY);body=tree.body[0].body
    branch=next(n for n in body if isinstance(n,ast.If) and ast.unparse(n.test)=="action == 'cleanup-management-input'")
    before=body[:body.index(branch)]
    assert not any(isinstance(n,ast.Constant) and n.value=='lantern_meshticulum.bench.pilot_resume_rpc' for stmt in before for n in ast.walk(stmt))
    assert any(isinstance(n,ast.Constant) and n.value=='lantern_meshticulum.bench.pilot_resume_rpc' for stmt in branch.orelse for n in ast.walk(stmt))
    calls=[]
    inputs=SimpleNamespace(cleanup=lambda *a,**k:calls.append(k) or True)
    env=dict(action='cleanup-management-input',inputs=inputs,path='private-file',nonce='x',csha='sha',uid='1000',
        identities=dict(file=[1]*9,directory=[1]*5),unit='unit-b')
    exec(compile(ast.Module(body=[branch],type_ignores=[]),'actual-cleanup-branch','exec'),env)
    assert calls and env['result']['native_dispatch_called'] is False
    assert 'module' not in env

def test_close_failure_is_not_success_even_after_valid_receipt(modeled,monkeypatch):
    op,now,created=modeled;original=ssh.Job.close
    def close(self):
        original(self)
        raise WireError('local_cleanup_failed')
    monkeypatch.setattr(ssh.Job,'close',close)
    with pytest.raises(WireError,match='local_cleanup_failed'):
        op._command('unit-b',COMMAND,SCHEMA,root=True,cleanup=True,cleanup_transport_headroom=2)
    audit=op.management_cleanup_audits[0]
    assert audit['remote_receipt_valid'] is True and audit['returncode']==0
    assert audit['success'] is False and audit['reason']=='local_cleanup_failed'
