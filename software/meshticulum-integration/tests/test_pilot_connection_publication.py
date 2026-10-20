"""Real local socket publication controls; no remote connections or devices."""
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

from lantern_meshticulum.bench import pilot_connections,pilot_ssh
from lantern_meshticulum.bench.pilot_connections import Connections
from meshticulum.bench.wire import WireError
from tests.test_pilot_ssh import config

ARGV=['ssh','-T','-o','ControlMaster=no','-o','ControlPath=none','inert-host']


class LocalPublisher:
    """Modeled master lifecycle, real socket and OpenSSH link/unlink sequence."""
    def __init__(self,argv,*,clock,deadline,late=False):
        self.path=Path(next(v.split('=',1)[1] for v in argv if v.startswith('ControlPath=')))
        self.alias=self.path.with_name(self.path.name+'.'+'a'*16)
        self.clock,self.deadline,self.late=clock,deadline,late
        self.closed=False;self.socket=None;self.process=SimpleNamespace(poll=lambda:0 if self.closed else None)
        if not late:self.publish()
    def publish(self):
        self.socket=socket.socket(socket.AF_UNIX);self.socket.bind(str(self.alias));self.alias.chmod(0o600);self.socket.listen(1)
        os.link(self.alias,self.path)
    def poll(self):
        if self.late:raise WireError('original_poll_refusal')
        return self.process.poll()
    def pump(self):pass
    def close(self):
        if self.closed:return
        if self.late:self.publish()
        self.socket.close();self.closed=True


@pytest.fixture
def rig(monkeypatch):
    now=[100.0];jobs=[]
    def factory(argv,**kwargs):
        job=LocalPublisher(argv,**kwargs);jobs.append(job);return job
    owned=Connections(clock=lambda:now[0],deadline=160,job_factory=factory)
    def sleep(seconds):now[0]+=seconds
    monkeypatch.setattr(pilot_connections.time,'sleep',sleep)
    try:yield owned,now,jobs
    finally:
        if not owned.closed:owned.close()


def test_two_link_publication_waits_for_one_link_before_ready(rig,monkeypatch):
    owned,now,jobs=rig;observations=[];observe=owned._observe_socket
    def observed(unit):
        info=observe(unit);observations.append(info.st_nlink)
        if len(observations)==1:
            assert unit not in owned.sockets
            jobs[0].alias.unlink()
        return info
    monkeypatch.setattr(owned,'_observe_socket',observed)
    owned.open('unit-a',ARGV)
    assert observations[:2]==[2,1] and now[0]==pytest.approx(100.01)
    assert owned.path('unit-a').stat().st_nlink==1


def test_ready_socket_still_refuses_a_later_hardlink(rig,monkeypatch):
    owned,_,jobs=rig;observe=owned._observe_socket
    def observed(unit):
        info=observe(unit)
        if info.st_nlink==2:jobs[0].alias.unlink()
        return info
    monkeypatch.setattr(owned,'_observe_socket',observed)
    owned.open('unit-a',ARGV);os.link(jobs[0].path,jobs[0].alias)
    with pytest.raises(WireError,match='ssh_control_socket_replaced'):owned.path('unit-a')
    jobs[0].alias.unlink()


@pytest.mark.parametrize('original_remaining',[60,0.025])
def test_publication_timeout_keeps_original_setup_limit_and_cleans_pinned_aliases(rig,original_remaining):
    owned,now,jobs=rig;start=now[0];owned.deadline=start+original_remaining
    with pytest.raises(WireError,match='ssh_control_setup_failed_or_expired'):owned.open('unit-a',ARGV)
    assert now[0]-start<=min(5,original_remaining)+0.011
    assert 'unit-a' not in owned.sockets and 'unit-a' in owned._observed_sockets
    owned.close()
    assert jobs[0].closed and owned.closed and not owned.directory.exists()


def test_socket_published_during_failed_job_reap_is_owned_and_removed(monkeypatch):
    jobs=[]
    def factory(argv,**kwargs):
        job=LocalPublisher(argv,late=True,**kwargs);jobs.append(job);return job
    owned=Connections(clock=lambda:100,deadline=160,job_factory=factory)
    try:
        with pytest.raises(WireError,match='original_poll_refusal'):owned.open('unit-a',ARGV)
        assert not owned._observed_sockets
    finally:owned.close()
    assert jobs[0].closed and owned.closed and not owned.directory.exists()


def test_replaced_pending_inode_is_preserved_and_cleanup_can_be_explicitly_retried(rig,monkeypatch):
    owned,_,jobs=rig;observe=owned._observe_socket;foreign=[]
    def observed(unit):
        info=observe(unit)
        if not foreign:
            jobs[0].path.unlink();other=socket.socket(socket.AF_UNIX);other.bind(str(jobs[0].path));jobs[0].path.chmod(0o600);foreign.append(other)
        return info
    monkeypatch.setattr(owned,'_observe_socket',observed)
    try:
        with pytest.raises(WireError,match='ssh_control_socket_replaced'):owned.open('unit-a',ARGV)
        inode=jobs[0].path.stat().st_ino
        with pytest.raises(WireError,match='ssh_control_cleanup_unconfirmed'):owned.close()
        assert jobs[0].path.stat().st_ino==inode and not owned.closed
        assert os.fstat(owned.fd).st_ino==owned.identity[1]
    finally:
        foreign[0].close();jobs[0].path.unlink()
        monkeypatch.setattr(owned,'_observe_socket',observe)
        owned.close()


def test_setup_error_remains_primary_when_cleanup_needs_another_attempt(monkeypatch):
    made=[]
    class Unconfirmed:
        def __init__(self,**kwargs):self.closed=False;made.append(self)
        def open(self,*args):raise WireError('original_setup_refusal')
        def close(self):raise WireError('cleanup_requires_explicit_retry')
    monkeypatch.setattr(pilot_connections,'Connections',Unconfirmed)
    cfg,source=config();operator=pilot_ssh.SSHOperator(cfg,provision_source=source,clock=lambda:10,epoch=lambda:1000000000)
    with pytest.raises(WireError,match='original_setup_refusal') as error:operator.open_connections()
    assert str(error.value.__cause__)=='cleanup_requires_explicit_retry'
    assert operator.connections is made[0] and operator.connections.closed is False
