# SPDX-License-Identifier: Apache-2.0
"""A-owned real PTY/worker/grant/crypto composition; native facts and RF modeled."""
import base64
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace
import uuid

import pytest
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.serial_protocol import Frame, Message, Outcome
from meshticulum.bench.serial_worker import Gfsk250SerialWorker, journal_binding
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher, HEADER, FrameKind
from meshticulum.contracts.identity import PeerID
from meshticulum.privacy.forward_relay import RelayStage
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from lantern_meshticulum.bench.profiles import GFSK250_FIRMWARE, admission_for, profile_for
from lantern_meshticulum.sage.forward_native_relay import RELAY_SCOPES
from lantern_meshticulum.sage.forward_native_relay_radio import (
    RelayRadioAdoption, RelayRadioObserver, RelayRadioError, NativeRelayRadioFactory,
    plan_radio_hop, SCHEMA, DOMAIN, SCOPE)
from lantern_meshticulum.sage.mesh_inbox import _canonical
from lantern_meshticulum.sage.radio_native_adoption import NativeRadioAdoption
from tests.integrations.sage.test_forward_native_relay import relay_rig, ModeledFactory
from tests.integrations.sage.test_forward_radio_gfsk250 import Gfsk250WirePair
from tests.integrations.sage.test_forward_radio_composition import shared_process_peer_scheduling


def native_plan(r, packet, *, kind=1, sends=False):
    peers = tuple(PeerID(x.chain_id,x.public_key.hex()) for x in (r.context.initiator,r.context.responder))
    first,last = peers if kind % 2 else peers[::-1]
    first,last = (r.local,last) if sends else (first,r.local)
    slot = (kind-1)*2+int(sends)
    return dict(schema='meshticulum.service-run.v1',unit='unit-a',host_boot_id=r.intention.host_boot_id,
        local_peer=asdict(r.local),initiator=asdict(first),responder=asdict(last),
        session_id=uuid.uuid4().hex,generation=uuid.uuid4().hex,issued_epoch_us=r.context.issued_epoch_us,
        expires_epoch_us=r.context.expires_epoch_us,radio_modem='gfsk250',
        profile=replace(profile_for('gfsk250'),lease_ms=10000).to_dict(),sender_unit='unit-a' if sends else 'unit-c',
        country='MY',antenna_confirmed=True,max_frames=512,
        application_deadline_monotonic_us=r.intention.application_deadline_monotonic_us-1000000,
        object=dict(bytes=len(packet),sha256=hashlib.sha256(packet).hexdigest(),chunk_index=slot,object_id=r.context.digest.hex()),
        service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=r.context.expires_epoch_us))


def files(path, plan):
    path.mkdir(mode=0o700)
    for name,raw in (('grant.json',_canonical(plan)),('secret.bin',b'k'*32),('active',b'active\n')):
        fd = os.open(path/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(raw)


class RadioWire(Gfsk250WirePair):
    def __init__(self,path,damage=None):
        self.damage=damage; self.dropped=False; self.paid_at_arm=None
        super().__init__(path)
        if damage=='prearmed': self.armed[0]=True

    def send(self,index,frame):
        if index==0:
            if frame.type is Message.GFSK250_ARM_REPLY and self.paid_at_arm:
                self.paid_at_arm()
            if self.damage=='capability' and frame.type is Message.GFSK250_CAPABILITIES_REPLY:
                frame=replace(frame,body=frame.body[:-1]+bytes([frame.body[-1]^1]))
            if self.damage=='boot' and frame.type in (Message.STATUS_REPLY,Message.GFSK250_STATUS_REPLY):
                frame=replace(frame,boot=bytes(16))
            if self.damage=='profile' and frame.type is Message.GFSK250_STATUS_REPLY and self.armed[0]:
                frame=replace(frame,body=bytes([frame.body[0]^1])+frame.body[1:])
            if self.damage=='unknown' and frame.type is Message.TX_RESULT and frame.body[0]==Outcome.RF_DONE:
                row=list(struct.unpack('!B32sQQIh',frame.body));row[0]=Outcome.UNKNOWN;row[4]=0
                frame=replace(frame,body=struct.pack('!B32sQQIh',*row))
        if self.damage=='loss' and not self.dropped and frame.type is Message.GFSK250_RECEIVE:
            if HEADER.unpack_from(frame.body[2:])[2]==FrameKind.DATA:
                self.dropped=True;return
        return super().send(index,frame)


@contextmanager
def prepared_rig(tmp_path,monkeypatch,*,sends=False,damage=None,scopes=None,enroll=True,lease_ms=10000):
    def setup(r):
        r.hardware._forward_radio=r.hardware._radio_observer=None;r.hardware._initializing=False
    with relay_rig(tmp_path,scopes=RELAY_SCOPES+(SCOPE,) if scopes is None else scopes,before_create=setup) as r:
        wire=RadioWire(r.path/'wire',damage)
        started=time.monotonic(); clock_call=type(r.clock).__call__
        def coherent_clock(clock):
            epoch,mono=clock_call(clock)
            elapsed=int((time.monotonic()-started)*1000000) if clock is r.clock else 0
            return epoch+elapsed,mono+elapsed
        monkeypatch.setattr(type(r.clock),'__call__',coherent_clock)
        monkeypatch.setattr(ExclusiveSerial,'monotonic',staticmethod(lambda:r.clock()[1]/1000000))
        read_text=Path.read_text
        def locks(path,*args,**kwargs):
            if str(path)!='/proc/locks':return read_text(path,*args,**kwargs)
            rows=[]
            for transport in wire.transports:
                for fd in (transport.fd,transport.lock_fd):
                    if fd is None:continue
                    info=os.fstat(fd)
                    rows.append(f'1: FLOCK ADVISORY WRITE {os.getpid()} {os.major(info.st_dev):x}:{os.minor(info.st_dev):x}:{info.st_ino} 0 EOF')
            return '\n'.join(rows)
        monkeypatch.setattr(Path,'read_text',locks)
        # Device/sysfs/serial location facts only are source models; the real
        # common status parser, current-boot capability and ARM worker run.
        def device(observer):
            observer._bound();assert observer.adoption.current();return True
        def discovered(observer,worker):
            observer.device();assert type(worker) is Gfsk250SerialWorker
            assert worker.identity.boot==wire.boots[0] and worker.identity.board_id==1
            assert worker.identity.version==GFSK250_FIRMWARE
            observer.worker,observer.identity=worker,worker.identity;observer.status(disarmed=True)
        monkeypatch.setattr(RelayRadioObserver,'device',device)
        monkeypatch.setattr(RelayRadioObserver,'discovered',discovered)
        packet=r.sessions[0].start();plan=native_plan(r,packet,sends=sends)
        plan['profile']['lease_ms']=lease_ms
        profile=plan_radio_hop(r.owner,plan,b'k'*32,slot=int(sends),kind=1,sends=sends)
        directory=r.path/'native';files(directory,plan)
        observer=RelayRadioObserver(r.owner,profile,wire.transports[0])
        factory=NativeRelayRadioFactory(r.owner,profile,directory,wire.transports[0],observer=observer)
        r.packet,r.native_plan,r.profile,r.directory,r.wire,r.observer,r.factory=packet,plan,profile,directory,wire,observer,factory
        r.remote=None
        try:
            if enroll:
                adoption=RelayRadioAdoption(r.owner,profile,directory,wire.transports[0])
                try:
                    body=dict(record_id=adoption.record_id,revision=1,binding=json.loads(adoption._binding),state='active')
                    signature=r.hardware.identity._signing_key.sign(DOMAIN+_canonical(body)).signature
                    value=dict(schema=SCHEMA,body=body,signature=base64.b64encode(signature).decode())
                    adoption.store.put(adoption.record_id,'mesh_outbound',0,value,access_check=r.owner.access_check)
                finally:adoption.release()
            yield r
        finally:
            r.owner.fence()
            r.hardware._forward_children.close(6)
            if r.remote is not None:
                q=r.remote
                q.flow.close();q.worker.disarm();q.store.close();q.journal.close();q.authority.close()
            # The explicit source PTY owner performs test teardown even when
            # the modeled native condition intentionally remains uncertain.
            if factory.adoption is not None:factory.adoption.release()
            for handle in (factory.flow,factory.store,factory.journal,factory.authority):
                if handle is not None:
                    try:handle.close()
                    except Exception:pass
            wire.close()


def paid(r):
    return r.state.rows[r.owner._budget.record_id]['payload']['body']['allocations']


def begin(r):
    other=ModeledFactory(r,r.packet,1,not r.profile.child.sends)
    incoming,outgoing=(other,r.factory) if r.profile.child.sends else (r.factory,other)
    plan=lambda factory:factory.profile.child if factory is r.factory else factory.plan
    hop=lambda factory:factory.profile.hop if factory is r.factory else factory.hop
    stage=RelayStage(1,hop(incoming),hop(outgoing),len(r.packet),hashlib.sha256(r.packet).digest())
    def arm_paid():assert paid(r)==[plan(incoming).to_dict(),plan(outgoing).to_dict()]
    r.wire.paid_at_arm=arm_paid
    r.children=r.owner.prepare(stage,plan(incoming),plan(outgoing),incoming_factory=incoming,outgoing_factory=outgoing)
    r.other=other
    return next(child for child in r.children if child.factory is r.factory)


def remote(r):
    p=json.loads(_canonical(r.native_plan));p['unit']='unit-b' if r.profile.child.sends else 'unit-c'
    p['local_peer']=p['responder'] if r.profile.child.sends else p['initiator']
    directory=r.path/'remote';files(directory,p)
    a=RunAuthority(directory,expected_unit=p['unit'],host_boot_id=r.intention.host_boot_id,
        clock=r.clock,owner_probe=lambda:True,radio_admission=admission_for(r.profile.radio))
    w=Gfsk250SerialWorker(r.wire.transports[1]);w.discover(expected_board_id=1,expected_version=GFSK250_FIRMWARE)
    j=AttemptJournal(directory/'attempts.sqlite',binding=journal_binding(a.grant.digest,w.identity,a.profile),
        max_attempts=256,max_airtime_us=60000000,create=True)
    w.arm(journal=j,profile=a.profile,context_digest=a.grant.digest,authorize=a.live,
        authority_deadline=a.application_deadline_us/1000000)
    s=SessionStore(directory/'wire.sqlite',a.grant,a.local_peer,boot_id=r.intention.host_boot_id,clock=r.clock,create=True)
    flow=BenchFlow(authority=a,worker=w,cipher=SessionCipher(a.grant,a.local_peer,s),journal=j,
        flow_key=a.flow_key,contract=a.contract,profile=a.carrier_profile,
        ledger=Ledger(BudgetPolicy(240000000,60000000,120000000,1000000,quantization_us=1),a.ledger_epoch,r.clock()[1]),
        scheduler_epoch=a.scheduler_epoch,broker_epoch=a.broker_epoch,sends=not r.profile.child.sends,clock=r.clock)
    r.remote=SimpleNamespace(authority=a,worker=w,journal=j,store=s,flow=flow,transfer=ServiceTransfer(flow))
    return r.remote


@pytest.mark.parametrize('kind',range(1,6))
@pytest.mark.parametrize('sends',[False,True])
def test_all_ten_plans_bind_a_and_exact_adjacent_direction(tmp_path,kind,sends):
    with relay_rig(tmp_path) as r:
        packet=r.sessions[0].start();p=native_plan(r,packet,kind=kind,sends=sends)
        profile=plan_radio_hop(r.owner,p,b'k'*32,slot=(kind-1)*2+int(sends),kind=kind,sends=sends)
        assert profile.hop.session.initiator==r.local if sends else profile.hop.session.responder==r.local
        assert profile.child.slot==(kind-1)*2+int(sends) and profile.child.cost.radio_attempts==256
        with pytest.raises(Exception):NativeRadioAdoption.validate_plan(r.owner,profile.child,p,b'k'*32)
        assert not hasattr(r.owner,'local_initiator') and not hasattr(r.owner,'_operation')


@pytest.mark.parametrize('damage',['endpoint-peer','local-peer','renewed','modem','profile','object','role'])
def test_plan_refuses_endpoint_substitution_or_changed_policy(tmp_path,damage):
    with relay_rig(tmp_path) as r:
        p=native_plan(r,r.sessions[0].start())
        if damage=='endpoint-peer':p['responder']=asdict(PeerID(r.context.responder.chain_id,r.context.responder.public_key.hex()))
        elif damage=='local-peer':p['local_peer']=p['initiator']
        elif damage=='renewed':p['application_deadline_monotonic_us']+=2000000
        elif damage=='modem':p['radio_modem']='gfsk125'
        elif damage=='profile':p['profile']['chip_power_dbm']=1
        elif damage=='object':p['object']['object_id']='0'*64
        else:p['sender_unit']=p['unit']
        with pytest.raises(RelayRadioError):plan_radio_hop(r.owner,p,b'k'*32,slot=0,kind=1,sends=False)


def test_actual_factory_uses_relay_scope_and_full_pair_prepayment(tmp_path,monkeypatch):
    with prepared_rig(tmp_path,monkeypatch) as r:
        child=begin(r)
        assert type(r.factory.worker) is Gfsk250SerialWorker and r.hardware._forward_radio is r.factory.adoption
        assert r.factory.current(r.owner,child).hop is r.profile.hop
        assert len(paid(r))==2 and r.wire.frames==[[],[]]
        assert r.observer._armed_record[1] is r.factory.worker.profile
        assert r.observer.last_status['disarmed'] is False
        assert r.hardware._forward_children.close(6)
        assert len(paid(r))==2 and r.observer.physical_disarmed is True
        assert r.factory.terminal(r.owner,child).pending_unknown is False
        assert not r.owner.snapshot()['native_qualified']


def test_worker_has_no_unheld_native_dispatch_permission(tmp_path,monkeypatch):
    with prepared_rig(tmp_path,monkeypatch,sends=True) as r:
        begin(r);before=r.factory.journal.snapshot()
        with pytest.raises(Exception):r.factory.worker.transmit(b'outside original held relay lease')
        assert r.factory.journal.snapshot()==before and r.wire.frames[0]==[]
        assert r.hardware._forward_children.close(6)


@pytest.mark.parametrize('occupied',['_provider','_coordinator','_initializing','_radio_observer','_forward_radio'])
def test_runtime_seam_refuses_every_existing_producer(tmp_path,monkeypatch,occupied):
    with prepared_rig(tmp_path,monkeypatch) as r:
        a=RelayRadioAdoption(r.owner,r.profile,r.directory,r.wire.transports[0])
        try:
            setattr(r.hardware,occupied,True if occupied=='_initializing' else object())
            with pytest.raises(Exception):r.hardware.adopt_forward_relay_radio(a)
            assert not a._adopted and r.wire.commands[0]==[]
        finally:
            setattr(r.hardware,occupied,False if occupied=='_initializing' else None);a.release()


def test_actual_foreign_registry_drain_closes_owned_radio_without_new_dispatch(tmp_path,monkeypatch):
    with prepared_rig(tmp_path,monkeypatch) as r:
        begin(r);before=paid(r)[:];results=[]
        thread=threading.Thread(target=lambda:results.append(r.hardware._forward_children.close(6)))
        thread.start();thread.join(8)
        assert not thread.is_alive() and results==[True]
        assert r.observer.physical_disarmed is True and not r.wire.frames[0]
        assert r.factory.adoption._closed and r.factory.journal.lock_fd is None
        assert paid(r)==before and r.factory.transport.fd is r.factory.transport.lock_fd is None


@pytest.mark.parametrize('missing',['scope','record','endpoint-runtime-entry'])
def test_no_implicit_or_endpoint_adoption_permission(tmp_path,monkeypatch,missing):
    if missing=='scope':
        with prepared_rig(tmp_path,monkeypatch,scopes=RELAY_SCOPES,enroll=False) as r:
            with pytest.raises(Exception):begin(r)
            assert not r.wire.commands[0]
        return
    with prepared_rig(tmp_path,monkeypatch,enroll=missing!='record') as r:
        if missing=='record':
            with pytest.raises(Exception):begin(r)
        else:
            adoption=RelayRadioAdoption(r.owner,r.profile,r.directory,r.wire.transports[0])
            try:
                with pytest.raises(Exception):r.hardware.adopt_forward_radio(adoption)
                with pytest.raises(Exception):r.hardware.adopt_forward_relay_radio(adoption)
            finally:adoption.release()
        assert not r.wire.commands[0]


@pytest.mark.parametrize('damage',['capability','boot','profile','prearmed'])
def test_bad_capability_boot_arm_profile_or_existing_producer_never_transmits(tmp_path,monkeypatch,damage):
    with prepared_rig(tmp_path,monkeypatch,damage=damage) as r:
        with pytest.raises(Exception):begin(r)
        assert r.wire.frames[0]==[] and len(paid(r))==2
        if damage in ('capability','boot','prearmed'):assert Message.GFSK250_ARM not in r.wire.commands[0]
        if damage=='prearmed':assert Message.DISARM not in r.wire.commands[0]


@pytest.mark.parametrize('damage',['cancel','expiry','revoked','unknown'])
def test_cancel_expiry_revocation_and_unknown_never_refund_pair(tmp_path,monkeypatch,damage):
    with prepared_rig(tmp_path,monkeypatch,sends=True,damage=damage) as r:
        child=begin(r);before=r.factory.journal.snapshot()
        if damage=='cancel':r.owner.fence()
        elif damage=='expiry':r.clock.epoch=r.context.expires_epoch_us
        elif damage=='revoked':r.state.owner_live=False
        if damage=='unknown':
            def transmit():
                with r.owner.owner_lease():
                    packet=r.factory.flow.cipher.seal(r.packet,FrameKind.DATA,authorize=r.factory._authorize)
                    return r.factory.worker.transmit(packet)
            assert r.owner._run(transmit).outcome=='UNKNOWN'
            after=r.factory.journal.snapshot();assert after['attempts']==before['attempts']+1 and after['fenced']
            assert not r.hardware._forward_children.close(6)
            assert r.observer.physical_disarmed is True
        else:
            with pytest.raises(Exception):r.factory.worker.transmit(b'never sent')
            assert r.factory.journal.snapshot()==before and not r.wire.frames[0]
            r.hardware._forward_children.close(6)
        assert len(paid(r))==2


@pytest.mark.parametrize('sends',[False,True])
@pytest.mark.parametrize('damage',[None,'loss'])
def test_adjacent_radio_and_opaque_relay_complete_real_crypto_flow_under_loss(
        tmp_path,monkeypatch,sends,damage,shared_process_peer_scheduling):
    with prepared_rig(tmp_path,monkeypatch,sends=sends,damage=damage,lease_ms=45000) as r:
        child=begin(r);peer=remote(r)
        source=r.other.remote if sends else peer
        target=peer if sends else r.other.remote
        source.transfer.send(r.packet)
        for _ in range(800):
            source.flow.step();done=r.owner.step();target.flow.step()
            if done and source.flow.flow.complete and target.flow.flow.complete:break
            r.clock.advance(25000);time.sleep(.001)
        else:pytest.fail('adjacent source flow did not complete within original finite window')
        assert target.transfer.received()==r.packet
        assert r.sessions[1].respond(target.transfer.received()).startswith(b'MPF2')
        assert r.factory.flow.flow.complete and r.wire.frames[0]
        if damage=='loss':assert r.wire.dropped
        # Registry containment does no extra ACK/data work and retains all debt.
        paid_before=paid(r)[:]
        assert r.hardware._forward_children.close(6)
        final=r.factory.terminal(r.owner,child)
        assert final.resources_closed and final.radio_disarmed and not final.pending_unknown
        assert sum(final.journal_snapshot['outcomes'].values())==final.journal_snapshot['attempts']>0
        assert paid(r)==paid_before and not r.owner.snapshot()['native_qualified']


def test_normal_receiver_retirement_holds_original_ack_grace_then_disarms(
        tmp_path,monkeypatch,shared_process_peer_scheduling):
    # The original, independently declared 45-second MCU lease covers the
    # existing 20-second ACK grace. No clock or lease is renewed during retire.
    with prepared_rig(tmp_path,monkeypatch,lease_ms=45000) as r:
        child=begin(r);peer=remote(r);peer.transfer.send(r.packet)
        for _ in range(800):
            peer.flow.step();done=r.owner.step();r.other.remote.flow.step()
            if done and peer.flow.flow.complete and r.other.remote.flow.flow.complete:break
            r.clock.advance(25000);time.sleep(.001)
        else:pytest.fail('source transfer did not complete')
        deadline=r.factory.worker.deadline;lease=r.factory.worker.arm_lease_ms
        before=r.clock()[1];receipt=r.owner.finish_stage()
        assert r.clock()[1]-before>=20000000 and receipt['native_qualified'] is False
        assert r.factory.worker.deadline==deadline and r.factory.worker.arm_lease_ms==lease==45000
        assert child.retired and r.observer.physical_disarmed is True and len(paid(r))==2
