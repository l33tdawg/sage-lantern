"""Real local process/socket controls, with modeled SSH authentication.

No remote host, native grant or radio is contacted or qualified here.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from lantern_meshticulum.bench import pilot_ssh
from lantern_meshticulum.bench.pilot_connections import Connections
from lantern_meshticulum.bench.pilot_ssh import Job, SSHOperator
from meshticulum.bench.wire import WireError
from tests.test_pilot_ssh import config


LOCAL_MASTER = '''import os,socket,sys,time
os.umask(0o077)
s=socket.socket(socket.AF_UNIX);s.bind(sys.argv[1]);s.listen(1)
time.sleep(30)
'''


def local_factory(records, fail_unit=None):
    def create(argv, **kwargs):
        records.append(argv)
        path = next(v.split('=',1)[1] for v in argv if v.startswith('ControlPath='))
        code = 'raise SystemExit(2)' if Path(path).name == fail_unit else LOCAL_MASTER
        return Job([sys.executable, '-I', '-B', '-c', code, path], **kwargs)
    return create


def connection(records, fail_unit=None):
    return Connections(clock=time.monotonic, deadline=time.monotonic()+60,
                       job_factory=local_factory(records,fail_unit))


def test_live_owned_process_and_private_socket_survive_many_reuses_then_retire():
    records=[];owned=connection(records)
    try:
        owned.open('unit-a',['ssh','-T','-o','ControlMaster=no','-o','ControlPath=none','host-a'])
        path=owned.path('unit-a');inode=path.stat().st_ino
        for _ in range(10):assert owned.path('unit-a').stat().st_ino==inode
        assert len(records)==1 and owned.jobs['unit-a'].process.poll() is None
        assert 'ControlMaster=yes' in records[0] and 'ControlPersist=no' in records[0]
    finally:owned.close()
    assert owned.jobs['unit-a'].process.poll() is not None and not owned.directory.exists()


def test_terminated_master_refuses_reuse_instead_of_starting_a_replacement():
    records=[];owned=connection(records)
    try:
        owned.open('unit-a',['ssh','-T','-o','ControlMaster=no','-o','ControlPath=none','host-a'])
        job=owned.jobs['unit-a'];job.process.terminate();job.process.wait(timeout=3)
        with pytest.raises(WireError,match='ssh_control_process_not_live'):owned.path('unit-a')
        assert len(records)==1
    finally:owned.close()


def test_replaced_socket_is_refused_and_cleanup_preserves_the_foreign_inode():
    records=[];owned=connection(records);replacement=None
    owned.open('unit-a',['ssh','-T','-o','ControlMaster=no','-o','ControlPath=none','host-a'])
    path=owned.path('unit-a');path.unlink()
    replacement=socket.socket(socket.AF_UNIX);replacement.bind(str(path));path.chmod(0o700)
    inode=path.stat().st_ino
    try:
        with pytest.raises(WireError,match='ssh_control_socket_replaced'):owned.path('unit-a')
        with pytest.raises(WireError,match='ssh_control_cleanup_unconfirmed'):owned.close()
        assert path.stat().st_ino==inode and owned.jobs['unit-a'].process.poll() is not None
    finally:
        replacement.close();path.unlink();owned.close()


def test_failed_second_connection_still_reaps_every_owned_master():
    records=[];owned=connection(records,fail_unit='unit-b')
    try:
        owned.open('unit-a',['ssh','-T','-o','ControlMaster=no','-o','ControlPath=none','host-a'])
        with pytest.raises(WireError,match='ssh_control_setup_failed_or_expired'):
            owned.open('unit-b',['ssh','-T','-o','ControlMaster=no','-o','ControlPath=none','host-b'])
    finally:owned.close()
    assert len(records)==2 and all(j.process.poll() is not None for j in owned.jobs.values())


def test_operator_has_explicit_reuse_and_no_network_fallback(monkeypatch):
    records=[];monkeypatch.setattr(pilot_ssh,'Job',local_factory(records))
    cfg,source=config();cfg['handoff_end_epoch_us']=time.time_ns()//1000+60000000
    op=SSHOperator(cfg,provision_source=source)
    assert 'ControlPath=none' in op._ssh('unit-c',pty=False)
    try:
        op.open_connections()
        for unit in cfg['units']:
            argv=op._ssh(unit,pty=False)
            assert 'ControlMaster=no' in argv and 'ProxyCommand=/usr/bin/false' in argv
            assert 'ControlPath='+str(op.connections.path(unit)) in argv
            assert 'ClearAllForwardings=yes' in argv and 'ForwardAgent=no' in argv
        assert len(records)==3
        op.deadline=op.clock()-1
        with pytest.raises(WireError,match='ssh_pilot_port_or_window_exhausted'):
            op.endpoints('unit-c','unit-b')
        assert len(records)==3
    finally:op.close_connections()
    assert op.connections is None and all(event['outside_handoff'] for event in op.events[-1:])


def test_close_refuses_a_live_management_client_before_stopping_its_master(monkeypatch):
    records=[];monkeypatch.setattr(pilot_ssh,'Job',local_factory(records))
    cfg,source=config();cfg['handoff_end_epoch_us']=time.time_ns()//1000+60000000
    op=SSHOperator(cfg,provision_source=source);op.open_connections()
    client=Job([sys.executable,'-c','import time;time.sleep(30)'],deadline=time.monotonic()+60)
    op.jobs[('unit-c','1'*32)]=client
    try:
        with pytest.raises(WireError,match='ssh_control_clients_still_live'):op.close_connections()
        assert all(j.process.poll() is None for j in op.connections.jobs.values())
    finally:
        client.close();op.close_connections()


def test_expired_window_refuses_before_creating_a_socket_or_process(monkeypatch):
    cfg,source=config();op=SSHOperator(cfg,provision_source=source,clock=lambda:10,epoch=lambda:1000000000)
    op.deadline=10
    monkeypatch.setattr(pilot_ssh,'Job',lambda *a,**k:pytest.fail('must not start a process'))
    with pytest.raises(WireError,match='ssh_control_duplicate_or_expired'):op.open_connections()
    assert op.connections is None


def test_actual_openssh_cannot_fall_back_to_a_new_tcp_connection(tmp_path):
    listener=socket.socket()
    try:
        listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.1)
        port=listener.getsockname()[1]
        result=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
            '-o','ControlMaster=no','-o','ControlPath='+str(tmp_path/'missing'),
            '-o','ProxyCommand=/usr/bin/false','-o','ConnectTimeout=1',
            '-p',str(port),'127.0.0.1','true'],capture_output=True,timeout=3)
        assert result.returncode!=0
        with pytest.raises(socket.timeout):listener.accept()
    finally:listener.close()


def test_lost_control_refuses_dispatch_but_explicit_cleanup_can_use_a_new_pinned_channel(monkeypatch):
    records=[];monkeypatch.setattr(pilot_ssh,'Job',local_factory(records))
    cfg,source=config();cfg['handoff_end_epoch_us']=time.time_ns()//1000+60000000
    op=SSHOperator(cfg,provision_source=source);op.open_connections()
    job=op.connections.jobs['unit-c'];job.process.terminate();job.process.wait(timeout=3)
    commands=[]
    def cleanup_job(argv,**kwargs):
        commands.append(argv)
        return Job([sys.executable,'-c','print(\'{"schema":"source-cleanup","retired":true}\')'],**kwargs)
    monkeypatch.setattr(pilot_ssh,'Job',cleanup_job)
    try:
        with pytest.raises(WireError,match='ssh_control_process_not_live'):
            op._command('unit-c',['source-dispatch'],'source-cleanup')
        assert not commands
        value=op._command('unit-c',['source-retire'],'source-cleanup',cleanup=True)
        assert value['retired'] and len(commands)==1
        assert 'ControlPath=none' in commands[0] and 'StrictHostKeyChecking=yes' in commands[0]
        assert any(e.get('schema')=='meshticulum.ssh-cleanup-recovery.v1' for e in op.events)
    finally:op.close_connections()
