"""Five real factory/worker/flow/crypto stages on framed PTYs; native/RF modeled.

Frames are encrypted/decrypted only through the actual factory ServiceTransfer
and ForwardStageRunner on each endpoint. Firmware models report source TX_DONE,
not measured RF. Device/sysfs/kernel-lock facts and SDK storage are explicit
models. No production activation, physical qualification or key-erasure claim
beyond dropping the actual session's Python references is made.
"""
from contextlib import contextmanager
from dataclasses import asdict,replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import select
import struct
import threading
import time
import tty
import traceback
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.serial_protocol import Decoder,Frame,Message,Outcome
from meshticulum.bench.serial_worker import estimated_airtime_us
from meshticulum.privacy.forward_session import ForwardContext,public
from meshticulum.privacy.mesh_binary_envelope import to_binary
from lantern_meshticulum.bench.profiles import profile_for
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner,SCOPES
from lantern_meshticulum.sage.forward_radio import ForwardRadioFactory,NativeRadioObserver,plan_stage,FIRMWARE
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.mesh_inbox import _canonical,INBOX_SCHEMA
from lantern_meshticulum.sage.radio_native_adoption import NativeRadioAdoption,SCOPE,SCHEMA,DOMAIN
from tests.integrations.sage.test_forward_native_owner import setup
from tests.integrations.sage.test_mesh_inbox import SENDER,RECIPIENT,sealed
from tests.test_meshticulum_bench_runtime import BOOT


def clock():return time.time_ns()//1000,time.monotonic_ns()//1000


def original():
    epoch,mono=clock();noise=X25519PrivateKey.generate(),X25519PrivateKey.generate()
    context=ForwardContext(SENDER,RECIPIENT,uuid.uuid4().bytes,b'a'*32,epoch-1000000,epoch+180000000,
                           64,public(noise[0]),public(noise[1]))
    packet=to_binary(sealed(issued_at=epoch//1000000-1,expires_at=(epoch+180000000)//1000000).decode())
    intention=ReceiveIntention(str(uuid.uuid4()),hashlib.sha256(packet).hexdigest(),len(packet),
        context.binding_digest.hex(),BOOT,context.expires_epoch_us,mono+180000000)
    return context,intention,noise,packet


class WirePair:
    """Two genuine PTY endpoints; models MCU framing, costs and encrypted RF."""
    def __init__(self,directory,fault='none',counters=None):
        directory.mkdir(mode=0o700)
        self.fault=fault;self.changed=False;self.stop=threading.Event();self.guard=threading.Lock()
        self.commands=[[],[]];self.spent,self.reserved=counters or ([0,0],[0,0]);self.frames=[[],[]];self.errors=[]
        self.armed=[False,False];self.profiles=[bytes(30),bytes(30)];self.masters=[];self.transports=[];self._write=[threading.Lock(),threading.Lock()]
        self.boots=[bytes(2)+bytes.fromhex(s.replace(':',''))[::-1]+(1).to_bytes(8,'big')
                    for s in ('F8:5B:1B:A5:F6:08','44:1B:F6:62:56:54')]
        for index in (0,1):
            master,slave=os.openpty();tty.setraw(slave);lock=os.open(directory/f'lock-{index}',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
            fcntl.flock(slave,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            transport=ExclusiveSerial.__new__(ExclusiveSerial);transport.fd,transport.lock_fd=slave,lock
            self.masters.append(master);self.transports.append(transport)
        self.threads=[threading.Thread(target=self._firmware,args=(i,),daemon=True)for i in (0,1)]
        for thread in self.threads:thread.start()

    def send(self,index,frame):
        with self._write[index]:os.write(self.masters[index],frame.encode())

    def _firmware(self,index):
        try:
            decoder=Decoder();boot=self.boots[index]
            while not self.stop.is_set():
                if not select.select([self.masters[index]],[],[],.01)[0]:continue
                raw=os.read(self.masters[index],4096)
                for frame in decoder.feed(raw):
                    self.commands[index].append(frame.type)
                    if frame.type is Message.HELLO:
                        body=b'\x01\x01\x00\xff'+FIRMWARE.encode()+bytes(16-len(FIRMWARE));kind=Message.HELLO_REPLY
                    elif frame.type is Message.DISARM:self.armed[index]=False;body=b'\0';kind=Message.DISARM_REPLY
                    elif frame.type is Message.ARM:self.profiles[index]=frame.body;self.armed[index]=True;body=frame.body;kind=Message.ARM_REPLY
                    elif frame.type is Message.STATUS:
                        body=self.profiles[index]+self.spent[index].to_bytes(4,'big')+self.reserved[index].to_bytes(8,'big')+bytes(4)+bytes((int(self.armed[index]),0,0));kind=Message.STATUS_REPLY
                    elif frame.type is Message.TRANSMIT:
                        assert self.armed[index];packet=frame.body;self.frames[index].append(packet)
                        # Exact profile and estimate come from the actually admitted ARM.
                        profile=profile_for('sf7-500');profile=replace(profile,lease_ms=45000)
                        estimate=estimated_airtime_us(profile,len(packet));self.spent[index]+=1;self.reserved[index]+=estimate
                        evidence=lambda outcome,elapsed,irq:struct.pack('!B32sQQIh',outcome,hashlib.sha256(packet).digest(),estimate,elapsed,irq,0)
                        self.send(index,Frame(Message.TX_RESULT,boot,frame.request,evidence(Outcome.STARTED,0,0)))
                        self.send(index,Frame(Message.TX_RESULT,boot,frame.request,evidence(Outcome.RF_DONE,estimate,1)))
                        delivered=packet
                        with self.guard:
                            damage=self.fault!='none' and not self.changed and index==0
                            if damage:self.changed=True
                        if damage and self.fault=='loss':continue
                        if damage and self.fault=='mutate':delivered=packet[:-1]+bytes([packet[-1]^1])
                        if self.armed[1-index]:
                            received=Frame(Message.RECEIVE,self.boots[1-index],0,struct.pack('!hh',-650,100)+delivered)
                            self.send(1-index,received)
                            if damage and self.fault=='replay':self.send(1-index,received)
                        continue
                    else:raise AssertionError('unexpected modeled firmware command')
                    self.send(index,Frame(kind,boot,frame.request,body))
        except BaseException as error:
            if not self.stop.is_set():self.errors.append(error)

    def close(self):
        self.stop.set()
        for thread in self.threads:thread.join(1)
        for transport in self.transports:transport.close()
        for fd in self.masters:os.close(fd)
        assert not self.errors,self.errors


def plan(owner,slot,packet):
    kind=slot+1;first=owner.context.initiator if kind%2 else owner.context.responder
    second=owner.context.responder if kind%2 else owner.context.initiator
    peer=lambda p:dict(chain_id=p.chain_id,agent_id=p.public_key.hex())
    local_unit='unit-a' if owner.local_initiator else 'unit-b';sender='unit-a' if kind%2 else 'unit-b'
    radio=replace(profile_for('sf7-500'),lease_ms=45000)
    return dict(schema='meshticulum.service-run.v1',unit=local_unit,host_boot_id=BOOT,local_peer=peer(owner.local),
        initiator=peer(first),responder=peer(second),session_id=bytes([slot+11]).hex()*16,generation=bytes([slot+21]).hex()*16,
        issued_epoch_us=owner.context.issued_epoch_us,expires_epoch_us=owner.context.expires_epoch_us,profile=asdict(radio),
        object=dict(bytes=len(packet),sha256=hashlib.sha256(packet).hexdigest(),chunk_index=slot,object_id=owner.context.digest.hex()),
        sender_unit=sender,country='MY',antenna_confirmed=True,max_frames=64,
        service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=owner.context.expires_epoch_us),
        application_deadline_monotonic_us=owner.intention.application_deadline_monotonic_us-2000000)


def provision(directory,profile,owner,transport):
    directory.mkdir(mode=0o700)
    for name,raw in (('grant.json',profile.plan_bytes),('secret.bin',profile.grant.secret),('active',b'active\n')):
        fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb')as out:out.write(raw)
    # Explicit source fixture enrollment; factory creates neither this record nor a grant.
    a=NativeRadioAdoption(owner,profile.child,directory,transport)
    body=dict(record_id=a.record_id,revision=1,binding=json.loads(a._binding),state='active')
    import base64
    sig=owner.resource.identity._signing_key.sign(DOMAIN+_canonical(body)).signature
    a.store.put(a.record_id,'mesh_outbound',0,dict(schema=SCHEMA,body=body,signature=base64.b64encode(sig).decode()),access_check=owner.access_check)
    a.release()


@pytest.mark.parametrize('fault',['none','loss'])
def test_five_radio_factories_wire_crypto_capture_and_one_private_inbox(tmp_path,monkeypatch,fault):
    values=original();commands=[queue.Queue(),queue.Queue()];replies=queue.Queue();reports=[[],[]];pairs=[];active=[];counters=([0,0],[0,0])
    def device(observer):
        observer._bound();assert observer.adoption.current();observer._bound();return True
    def discovered(observer,worker):
        observer.device();assert worker.identity.board_id==1 and worker.identity.version==FIRMWARE
        assert worker.identity.boot in [boot for pair in pairs for boot in pair.boots]
        observer.worker,observer.identity=worker,worker.identity;observer.status(disarmed=True)
    monkeypatch.setattr(NativeRadioObserver,'device',device);monkeypatch.setattr(NativeRadioObserver,'discovered',discovered)
    original_read=Path.read_text
    def kernel(path,*args,**kwargs):
        if str(path)!='/proc/locks':return original_read(path,*args,**kwargs)
        rows=[]
        for pair in pairs:
            for transport in pair.transports:
                for fd in (transport.fd,transport.lock_fd):
                    if fd is None:continue
                    try:info=os.fstat(fd)
                    except OSError:continue # Other endpoint may release between enumeration and fstat.
                    rows.append(f'1: FLOCK ADVISORY WRITE {os.getpid()} {os.major(info.st_dev):x}:{os.minor(info.st_dev):x}:{info.st_ino} 0 EOF')
        return '\n'.join(rows)
    monkeypatch.setattr(Path,'read_text',kernel)
    def actor(index):
        try:
            with setup(initiator=index==0,scopes=SCOPES+(SCOPE,),original=values) as r:
                h=r.hardware;h._forward_radio=h._coordinator=h._radio_observer=None;h._initializing=False
                owner=ForwardNativeOwner(h,r.authority,r.native_context,context=r.context,intention=r.intention,local_initiator=index==0,
                    key_check=lambda key,digest:key==public(r.noise[index]) and digest==r.context.digest,
                    peer_key_check=lambda peer,key,digest:peer==(r.context.responder if index==0 else r.context.initiator)
                        and key==public(r.noise[1-index]) and digest==r.context.digest,boot_reader=lambda:r.state.boot,clock_reader=clock)
                op=owner.operation(r.noise[index],limits=Cost(2000,300000000),prior_debt=Cost(),sealed_record=r.packet if index==0 else None)
                active.append(op);replies.put(('created',index))
                while True:
                    command=commands[index].get(timeout=150)
                    if command[0]=='packet':replies.put(('packet',index,op.packet()));continue
                    if command[0]=='close':
                        result=owner.close();assert op.session.state=='closed' and op.session._tx is op.session._rx is op.session._s is None
                        replies.put(('closed',index,result));break
                    if command[0]=='settle':
                        result=op.settle();rows=[v for v in r.state.rows.values()if v['payload']['schema']==INBOX_SCHEMA]
                        assert len(rows)==1 and rows[0]['revision']==1
                        before=json.dumps(rows);again=op.settle()
                        after=[v for v in r.state.rows.values()if v['payload']['schema']==INBOX_SCHEMA]
                        assert len(after)==1 and after[0]['revision']==1 and json.dumps(after)==before
                        replies.put(('settled',index,result.status,again.status));continue
                    _,slot,packet,pair=command
                    profile=plan_stage(owner,plan(owner,slot,packet),hashlib.sha256(bytes([slot])+r.context.digest).digest(),slot=slot,kind=slot+1)
                    directory=tmp_path/f'{slot}-{index}';provision(directory,profile,owner,pair.transports[index])
                    observer=NativeRadioObserver(owner,profile,pair.transports[index]);factory=ForwardRadioFactory(owner,profile,directory,pair.transports[index],observer=observer)
                    runner=factory.prepare(payload=packet if profile.child.sends else None);runner.start()
                    deadline=time.monotonic()+35
                    while not runner.step():
                        assert time.monotonic()<deadline;time.sleep(.002)
                    assert runner.wire_complete and owner.retire_stage(runner)
                    report=factory.report();reports[index].append(report)
                    assert report['retired'] and not report['uncertain'] and report['native_qualified'] is False
                    assert report['physical_disarmed'] is True and (directory/'active').read_bytes()==b'retired\n'
                    assert factory.store._db is not None and factory._wire_retired
                    assert op.position==slot+1 and profile.child.application_expires_epoch_us==values[0].expires_epoch_us
                    assert profile.child.application_deadline_monotonic_us==values[1].application_deadline_monotonic_us-2000000
                    row=r.state.rows[op.budget.record_id]['payload']['body']
                    assert len(row['allocations'])==slot+1 and row['allocations'][-1]==profile.child.to_dict()
                    replies.put(('stage',index,slot))
        except BaseException as error:replies.put(('error',index,repr(error),traceback.format_exc()))
    threads=[threading.Thread(target=actor,args=(index,),daemon=True)for index in (0,1)]
    for thread in threads:thread.start()
    def receive(expected,count=1):
        values=[replies.get(timeout=45)for _ in range(count)];assert all(v[0]==expected for v in values),values;return values
    try:
        receive('created',2)
        for slot in range(5):
            sender=slot%2;commands[sender].put(('packet',));packet=receive('packet')[0][2]
            pair=WirePair(tmp_path/f'wire-{slot}',fault=fault if slot==0 else 'none',counters=counters);pairs.append(pair)
            for index in (0,1):commands[index].put(('stage',slot,packet,pair))
            receive('stage',2);pair.close()
        commands[1].put(('settle',));assert receive('settled')[0][2]=='admitted'
        for index in (0,1):commands[index].put(('close',))
        assert all(v[2] is True for v in receive('closed',2))
    finally:
        for index in (0,1):commands[index].put(('close',))
        for thread in threads:thread.join(3)
        for pair in pairs:
            if not pair.stop.is_set():pair.close()
    assert all(not thread.is_alive()for thread in threads) and all(len(v)==5 for v in reports)
    assert all(len(op.budget._permits)==5 and op.position==5 for op in active)
    assert all(sum(v['child']['cost']['radio_attempts']for v in side)==1280
               and sum(v['child']['cost']['estimated_airtime_us']for v in side)==300000000 for side in reports)
    assert counters[0]==[sum(v['attempt_journal']['attempts']for v in side)for side in reports]
    assert not list(tmp_path.glob('*/sender.bin'))
    if fault=='loss':assert pairs[0].changed and sum(r['attempt_journal']['attempts']for side in reports for r in side)>10
