"""Actual Core workers/flow on PTY firmware; sysfs/native facts are models, no RF."""
from contextlib import contextmanager
from dataclasses import asdict,replace
import json
import os
import select
import threading
import time
import tty

import pytest
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.serial_protocol import Decoder,Frame,Message
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.flow_runtime import BenchFlow
from lantern_meshticulum.sage.forward_radio import (
    ForwardRadioFactory,NativeRadioObserver,ForwardRadioError,plan_stage,FIRMWARE,
)
from lantern_meshticulum.sage.radio_native_adoption import NativeRadioAdoption
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_radio_native_adoption import model,enroll_fixture


@contextmanager
def factory_model(tmp_path,monkeypatch,*,damage=None,context_box=None):
    with model(tmp_path) as r:
        # Source-only clock and sysfs model; authority/client/root scopes remain actual.
        monkeypatch.setattr(ExclusiveSerial,'monotonic',staticmethod(lambda:r.state.now[1]/1000000))
        def device_facts(observer):
            observer._bound()
            assert all(os.fstat(fd).st_ino==identity[1]
                       for _,fd,identity in observer.adoption._fd_refs)
            observer._bound();return True
        # Model only OS identity facts; keep the production authorization
        # bookends in device/current and the actual signed adoption observer.
        monkeypatch.setattr(NativeRadioObserver,'_device_facts',device_facts)
        p=json.loads((r.directory/'grant.json').read_bytes());p['profile']['lease_ms']=10000
        (r.directory/'grant.json').write_bytes(_canonical(p))
        profile=plan_stage(r.owner,p,b's'*32,slot=0,kind=1)
        observer=NativeRadioObserver(r.owner,profile,r.transport)
        stage=ForwardRadioFactory(r.owner,profile,r.directory,r.transport,observer=observer)
        # The enrollment is prepared independently; factory creates no record.
        enrollment=NativeRadioAdoption(r.owner,profile.child,r.directory,r.transport)
        enroll_fixture(r,enrollment);enrollment.release()
        master=r.master
        tty.setraw(r.transport.fd);stop=threading.Event();commands=[];state={'profile':bytes(30),'armed':False,'attempts':0}
        boot=bytes(2)+bytes.fromhex('F8:5B:1B:A5:F6:08'.replace(':',''))[::-1]+(1).to_bytes(8,'big')
        # Device identity is a declared source model; no original root/config mutation.
        def discovered(obs,worker):
            obs.device();assert worker.identity.boot==boot and worker.identity.version==FIRMWARE
            obs.worker,obs.identity=worker,worker.identity;obs.status(disarmed=True)
        monkeypatch.setattr(NativeRadioObserver,'discovered',discovered)
        observer._methods=tuple((n,getattr(type(observer),n))for n,_ in observer._methods)
        def firmware():
            decoder=Decoder()
            while not stop.is_set():
                if not select.select([master],[],[],.01)[0]:continue
                try:raw=os.read(master,4096)
                except OSError:return
                for frame in decoder.feed(raw):
                    commands.append(frame.type)
                    if frame.type is Message.HELLO:
                        body=bytes((1,1))+b'\x00\xff'+FIRMWARE.encode()+bytes(16-len(FIRMWARE))
                        kind=Message.HELLO_REPLY
                    elif frame.type is Message.DISARM:state['armed']=False;body=b'\0';kind=Message.DISARM_REPLY
                    elif frame.type is Message.ARM:
                        state['profile']=frame.body;state['armed']=True;body=frame.body;kind=Message.ARM_REPLY
                        if damage=='arm':body=bytes(len(frame.body))
                        if damage=='revocation':r.state.owner_live=False
                    elif frame.type is Message.STATUS:
                        body=state['profile']+state['attempts'].to_bytes(4,'big')+bytes(8)+bytes(4)+bytes((int(state['armed']),0,0));kind=Message.STATUS_REPLY
                        if damage=='disarm' and Message.ARM in commands and not state['armed']:body=body[:-3]+b'\x01\0\0'
                    else:raise AssertionError('source control never transmits application RF')
                    os.write(master,Frame(kind,boot,frame.request,body).encode())
        thread=threading.Thread(target=firmware,daemon=True);thread.start()
        r.profile,r.observer,r.stage,r.commands=profile,observer,stage,commands
        if context_box is not None:context_box['r']=r
        try:yield r
        finally:
            if stage.adoption is not None and not stage.adoption._closed:stage.adoption.release()
            stop.set();thread.join(1)


def test_factory_constructs_actual_arm_flow_and_prepaid_runner_before_any_payload(tmp_path,monkeypatch):
    with factory_model(tmp_path,monkeypatch) as r:
        runner=r.stage.prepare(payload=r.packet)
        assert type(runner.transfer) is ServiceTransfer and type(runner.transfer.flow) is BenchFlow
        assert r.commands==[Message.HELLO,Message.STATUS,Message.DISARM,Message.ARM,Message.STATUS]
        row=r.state.rows[r.op.budget.record_id]['payload']['body']
        assert row['allocations']==[r.profile.child.to_dict()] and runner.admission.bound
        assert r.stage.journal.snapshot()['attempts']==0 and r.stage.store._db.execute('SELECT tx FROM state').fetchone()==(0,)
        assert r.stage._cleanup(time.monotonic()+3)
        assert r.stage._terminal(r.profile.child,None)
        assert (r.directory/'active').read_bytes()==b'retired\n'
        assert r.stage.report()['physical_disarmed'] is True and r.stage.report()['native_qualified'] is False
        assert r.stage.adoption._closed and r.hardware._forward_radio is None


@pytest.mark.parametrize('damage',['arm','disarm','revocation'])
def test_actual_protocol_failure_preserves_paid_caps_and_unknown_status(tmp_path,monkeypatch,damage):
    with factory_model(tmp_path,monkeypatch,damage=damage) as r:
        if damage in ('arm','revocation'):
            with pytest.raises(Exception):r.stage.prepare(payload=r.packet)
        else:
            r.stage.prepare(payload=r.packet);assert not r.stage._cleanup(time.monotonic()+3)
            assert not r.stage.adoption._closed # Physical ownership retained without disarm proof.
        body=r.state.rows[r.op.budget.record_id]['payload']['body']
        assert body['allocations'][0]['cost']==asdict(r.profile.child.cost)
        assert r.stage._unknown and not r.stage.report()['native_qualified']


def test_factory_requires_typed_observer_and_refuses_renewed_deadline_before_io(tmp_path):
    with model(tmp_path) as r:
        p=json.loads((r.directory/'grant.json').read_bytes())
        profile=plan_stage(r.owner,p,b's'*32,slot=0,kind=1)
        with pytest.raises(ForwardRadioError):ForwardRadioFactory(r.owner,profile,r.directory,r.transport,observer=lambda:True)
        p['application_deadline_monotonic_us']=r.owner.intention.application_deadline_monotonic_us+1
        with pytest.raises(ForwardRadioError):plan_stage(r.owner,p,b's'*32,slot=0,kind=1)


def test_observer_replacement_refuses_before_payment_or_arm(tmp_path,monkeypatch):
    with factory_model(tmp_path,monkeypatch) as r:
        original=r.observer.bind
        def bind(token):
            original(token);r.state.owner_live=False
        # Alter a retained method after construction; this must refuse before ARM.
        r.observer.bind=bind
        with pytest.raises(Exception):r.stage.prepare(payload=r.packet)
        assert Message.ARM not in r.commands
        assert r.state.rows[r.op.budget.record_id]['payload']['body']['allocations']==[]


def test_authority_fd_reuse_never_closes_foreign_fd_during_containment(tmp_path,monkeypatch):
    with factory_model(tmp_path,monkeypatch) as r:
        r.stage.prepare(payload=r.packet)
        fd=r.stage.authority.fds['grant.json'];foreign=os.open(tmp_path/'foreign',os.O_RDWR|os.O_CREAT,0o600)
        os.dup2(foreign,fd);os.close(foreign)
        try:
            with pytest.raises(Exception):r.stage._live()
            assert not r.stage._cleanup(time.monotonic()+3)
            os.fstat(fd)
        finally:
            # Source cleanup owns its other originals, never the replacement.
            r.stage.authority.fds.pop('grant.json');r.stage.authority.close();os.close(fd)
