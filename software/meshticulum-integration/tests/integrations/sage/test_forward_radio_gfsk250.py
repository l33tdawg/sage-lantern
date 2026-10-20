# SPDX-License-Identifier: Apache-2.0
"""Private GFSK250 on real PTYs/SQLite/crypto; native facts and RF are models."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
import select
import struct
import threading
import time
import tty

import pytest
from meshticulum.bench.authority import canonical
from meshticulum.bench.gfsk250_profile import CAPABILITY, Gfsk250Profile, estimated_airtime_us
from meshticulum.bench.hat_worker import HATWorker, Gfsk250HATWorker
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, Outcome
from meshticulum.bench.serial_worker import SerialWorker, Gfsk250SerialWorker
from meshticulum.bench.wire import FrameKind
from lantern_meshticulum.bench.profiles import GFSK250_FIRMWARE, profile_for, profile_record
from lantern_meshticulum.sage.forward_radio import (
    ForwardRadioFactory, NativeRadioObserver, ForwardRadioError, plan_stage, _radio_types,
)
from lantern_meshticulum.sage.radio_native_adoption import NativeRadioAdoption, source_pins
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_radio_native_adoption import model, enroll_fixture
from tests.integrations.sage import test_forward_radio_composition as composition
from tests.integrations.sage.test_forward_radio_composition import shared_process_peer_scheduling


def plan250(r):
    p=json.loads((r.directory/'grant.json').read_bytes())
    p['radio_modem']='gfsk250';p['profile']=replace(profile_for('gfsk250'),lease_ms=10000).to_dict()
    return p


@contextmanager
def factory250(tmp_path,monkeypatch,*,mode='gfsk250',damage=None):
    with model(tmp_path) as r:
        started=time.monotonic()
        monkeypatch.setattr(ExclusiveSerial,'monotonic',staticmethod(lambda:r.state.now[1]/1000000+time.monotonic()-started))
        def device_facts(observer):
            observer._bound()
            assert all(os.fstat(fd).st_ino==identity[1]
                       for _,fd,identity in observer.adoption._fd_refs)
            observer._bound();return True
        # Only OS/device facts are modeled; signed scopes, enrollment, grants,
        # prepayment, selected worker capability and framed replies are real.
        monkeypatch.setattr(NativeRadioObserver,'_device_facts',device_facts)
        p=plan250(r);(r.directory/'grant.json').write_bytes(_canonical(p))
        profile=plan_stage(r.owner,p,b's'*32,slot=0,kind=1)
        observer=NativeRadioObserver(r.owner,profile,r.transport)
        stage=ForwardRadioFactory(r.owner,profile,r.directory,r.transport,observer=observer)
        enrollment=NativeRadioAdoption(r.owner,profile.child,r.directory,r.transport)
        enroll_fixture(r,enrollment);enrollment.release()
        boot=bytes(2)+bytes.fromhex('085BA5F6F844')+(1).to_bytes(8,'big')
        def discovered(obs,worker):
            obs.device();assert type(worker) is Gfsk250SerialWorker and worker.identity.boot==boot
            assert worker.identity.version==GFSK250_FIRMWARE
            obs.worker,obs.identity=worker,worker.identity;obs.status(disarmed=True)
        monkeypatch.setattr(NativeRadioObserver,'discovered',discovered)
        observer._methods=tuple((n,getattr(type(observer),n))for n,_ in observer._methods)
        old=replace(profile_for('gfsk125' if mode=='gfsk125' else 'gfsk250'),lease_ms=10000)
        if damage=='counter-over-admitted':old=replace(old,max_boot_attempts=512)
        state=dict(mode=mode,profile=bytes(30) if mode=='lora' else old.encode(board_id=1),
            armed=damage=='prearmed',attempts=257 if damage=='counter-over-admitted' else 2,air=100,
            commands=[],frames=[],errors=[],arm_paid=None,arm_count=0,disarm_count=0)
        tty.setraw(r.transport.fd);stop=threading.Event()
        def firmware():
            try:
                decoder=Decoder()
                while not stop.is_set():
                    if not select.select([r.master],[],[],.01)[0]:continue
                    for frame in decoder.feed(os.read(r.master,4096)):
                        state['commands'].append(frame);reply_boot=boot;request=frame.request
                        if frame.type is Message.HELLO:
                            version='mtb1-9816f92488' if damage=='firmware' else GFSK250_FIRMWARE
                            body=b'\x01\x01\x00\xff'+version.encode()+bytes(16-len(version));kind=Message.HELLO_REPLY
                        elif frame.type is Message.GFSK250_CAPABILITIES:
                            body=CAPABILITY if damage!='capability' else CAPABILITY[:-1]+b'\0';kind=Message.GFSK250_CAPABILITIES_REPLY
                        elif frame.type is Message.DISARM:
                            state['armed']=False;state['disarm_count']+=1;body=b'\0';kind=Message.DISARM_REPLY
                            if damage=='clipped' and state['disarm_count']==1:
                                r.state.now=tuple(v+68000000 for v in r.state.now)
                        elif frame.type is Message.GFSK250_ARM:
                            state['arm_paid']=r.state.rows[r.op.budget.record_id]['payload']['body']['allocations'][:]
                            state['profile']=frame.body;state['mode']='gfsk250';state['armed']=True;state['arm_count']+=1
                            body=bytes(47) if damage=='arm-body' else frame.body;kind=Message.GFSK250_ARM_REPLY
                            if damage=='revoked':r.state.owner_live=False
                        elif frame.type in (Message.STATUS,Message.GFSK_STATUS,Message.GFSK250_STATUS):
                            match={'lora':Message.STATUS,'gfsk125':Message.GFSK_STATUS,'gfsk250':Message.GFSK250_STATUS}[state['mode']]
                            if frame.type is not match:
                                kind=Message.REFUSED;body=b'\x02' if damage=='refused-other' else b'\x01'
                            else:
                                kind={Message.STATUS:Message.STATUS_REPLY,Message.GFSK_STATUS:Message.GFSK_STATUS_REPLY,
                                      Message.GFSK250_STATUS:Message.GFSK250_STATUS_REPLY}[frame.type]
                                actual=state['profile'];armed=state['armed'];attempts=state['attempts']
                                if state['arm_count'] and damage=='post-profile':
                                    actual=replace(Gfsk250Profile.decode(actual,board_id=1),chip_power_dbm=1).encode(board_id=1)
                                if state['arm_count'] and damage=='post-unarmed':armed=False
                                if state['arm_count'] and damage=='counter-regression':attempts=1
                                body=actual+struct.pack('!IQI3B',attempts,state['air'],0,int(armed),
                                    int(damage=='transmitting'),int(damage=='fenced'))
                            if damage=='stale-boot':reply_boot=bytes(16)
                            if damage=='stale-request':request+=1
                        elif frame.type is Message.TRANSMIT:
                            assert state['armed'];state['frames'].append(frame.body)
                            estimate=estimated_airtime_us(Gfsk250Profile.decode(state['profile'],board_id=1),len(frame.body))
                            state['attempts']+=1;state['air']+=estimate
                            for outcome in (Outcome.STARTED,Outcome.UNKNOWN if damage=='unknown-tx' else Outcome.RF_DONE):
                                body=struct.pack('!B32sQQIh',outcome,hashlib.sha256(frame.body).digest(),estimate,
                                    0 if outcome is Outcome.STARTED else estimate,1 if outcome is Outcome.RF_DONE else 0,0)
                                os.write(r.master,Frame(Message.TX_RESULT,boot,request,body).encode())
                            continue
                        else:raise AssertionError('wrong private modem command')
                        os.write(r.master,Frame(kind,reply_boot,request,body).encode())
            except BaseException as error:
                if not stop.is_set():state['errors'].append(error)
        thread=threading.Thread(target=firmware,daemon=True);thread.start()
        r.profile,r.observer,r.stage,r.firmware=profile,observer,stage,state
        try:yield r
        finally:
            if stage.adoption is not None and not stage.adoption._closed:stage.adoption.release()
            stop.set();thread.join(1)
            assert not thread.is_alive() and not state['errors'],state['errors']


def paid(r):
    return r.state.rows[r.op.budget.record_id]['payload']['body']['allocations']


def test_private_typed_plan_binds_modem_profile_and_original_endpoint_peers(tmp_path):
    with model(tmp_path) as r:
        p=plan250(r);profile=plan_stage(r.owner,p,b's'*32,slot=0,kind=1)
        assert type(profile.radio) is Gfsk250Profile
        assert profile.child.cost.radio_attempts==256 and profile.child.cost.estimated_airtime_us==60000000
        assert profile.child.profile_digest==hashlib.sha256(canonical(profile_record(profile.radio))).hexdigest()
        assert profile.child.application_deadline_monotonic_us==r.owner.intention.application_deadline_monotonic_us-1000000
        route={k:p[k] for k in ('initiator','responder','session_id','generation','profile','object','sender_unit','country','service','radio_modem')}
        assert profile.grant.route_binding_digest==hashlib.sha256(canonical(route)).digest()
        route.pop('radio_modem')
        wrong=replace(profile.grant,route_binding_digest=hashlib.sha256(canonical(route)).digest())
        with pytest.raises(Exception):NativeRadioAdoption.validate_plan(r.owner,replace(profile.child,grant_digest=wrong.digest.hex()),p,b's'*32)
        assert _radio_types(profile.radio)==(Gfsk250SerialWorker,Gfsk250HATWorker,GFSK250_FIRMWARE)
        legacy=plan_stage(r.owner,json.loads((r.directory/'grant.json').read_bytes()),b's'*32,slot=0,kind=1)
        assert _radio_types(legacy.radio)[:2]==(SerialWorker,HATWorker)
        pins=source_pins()
        assert {'meshticulum.bench.gfsk250_profile','meshticulum.bench.gfsk250_sx1262',
                'meshticulum.bench.serial_protocol','lantern_meshticulum.bench.profiles'}<=pins.keys()


@pytest.mark.parametrize('change',['absent','none','gfsk125','schema-v1','bitrate','power','antenna','country','extra','relay-peer','renewed'])
def test_private_gfsk250_has_no_implicit_mode_or_peer_or_authority_expansion(tmp_path,change):
    with model(tmp_path) as r:
        p=plan250(r)
        if change=='absent':p.pop('radio_modem')
        elif change in ('none','gfsk125'):p['radio_modem']=None if change=='none' else change
        elif change=='schema-v1':p['profile']['schema']='meshticulum.gfsk-profile.v1'
        elif change=='bitrate':p['profile']['bit_rate_bps']=500000
        elif change=='power':p['profile']['chip_power_dbm']=1
        elif change=='antenna':p['antenna_confirmed']=False
        elif change=='country':p['country']='ZZ'
        elif change=='extra':p['public_fast_bundle_approved']=True
        elif change=='relay-peer':p['responder']['agent_id']='f'*64
        else:p['application_deadline_monotonic_us']=r.owner.intention.application_deadline_monotonic_us+1
        with pytest.raises(ForwardRadioError):plan_stage(r.owner,p,b's'*32,slot=0,kind=1)
        assert paid(r)==[]


@pytest.mark.parametrize('mode',['lora','gfsk125','gfsk250'])
def test_factory_probes_disarmed_mode_then_uses_current_boot250_worker_and_full_prepayment(tmp_path,monkeypatch,mode):
    with factory250(tmp_path,monkeypatch,mode=mode) as r:
        runner=r.stage.prepare(payload=r.packet)
        assert type(runner.transfer.flow.worker) is Gfsk250SerialWorker
        commands=[f.type for f in r.firmware['commands']]
        probes=[Message.STATUS]+([Message.GFSK_STATUS] if mode!='lora' else [])+([Message.GFSK250_STATUS] if mode=='gfsk250' else [])
        assert commands==[Message.HELLO,Message.GFSK250_CAPABILITIES,*probes,Message.DISARM,Message.GFSK250_ARM,Message.GFSK250_STATUS]
        assert r.firmware['arm_paid']==paid(r)==[r.profile.child.to_dict()]
        assert r.observer.last_status==dict(attempts=2,reserved_us=100,disarmed=False)
        # Actual durable cipher and journal; source firmware reports modeled TX.
        payload=r.packet[:208]
        packet=r.stage.flow.cipher.seal(payload,FrameKind.DATA,authorize=r.stage._authorize)
        assert payload not in packet
        result=r.stage.worker.transmit(packet)
        assert result.outcome=='RF_DONE' and r.firmware['frames']==[packet]
        assert r.stage.journal.snapshot()['attempts']==1
        assert r.stage._cleanup(time.monotonic()+3) and r.stage._terminal(r.profile.child,None)
        assert r.observer.physical_disarmed is True and paid(r)==[r.profile.child.to_dict()]
        assert not r.stage.report()['native_qualified']


@pytest.mark.parametrize('damage',['firmware','capability','refused-other','stale-boot','stale-request',
    'transmitting','fenced','counter-over-admitted','arm-body','post-profile','post-unarmed','counter-regression','revoked'])
def test_bad_identity_status_or_arm_never_transmits_or_refunds(tmp_path,monkeypatch,damage):
    with factory250(tmp_path,monkeypatch,damage=damage) as r:
        with pytest.raises(Exception):r.stage.prepare(payload=r.packet)
        assert r.firmware['frames']==[] and paid(r)==[r.profile.child.to_dict()]
        assert r.stage.report()['uncertain'] and not r.stage.report()['native_qualified']
        if damage not in ('arm-body','post-profile','post-unarmed','counter-regression','revoked'):
            assert not any(f.type is Message.GFSK250_ARM for f in r.firmware['commands'])
        if damage in ('refused-other','stale-boot','stale-request'):
            assert [f.type for f in r.firmware['commands']][-1] is Message.STATUS


@pytest.mark.parametrize('mode',['lora','gfsk125','gfsk250'])
def test_preexisting_armed_device_is_never_adopted(tmp_path,monkeypatch,mode):
    with factory250(tmp_path,monkeypatch,mode=mode,damage='prearmed') as r:
        with pytest.raises(Exception):r.stage.prepare(payload=r.packet)
        assert not any(f.type in (Message.DISARM,Message.GFSK250_ARM,Message.TRANSMIT) for f in r.firmware['commands'])
        assert paid(r)==[r.profile.child.to_dict()]


def test_clipped_arm_never_renews_the_already_expired_original_flow_deadline(tmp_path,monkeypatch):
    with factory250(tmp_path,monkeypatch,damage='clipped') as r:
        # A late ARM can fit a shorter MCU lease, but that does not renew the
        # original service/flow deadline fixed before setup consumed its time.
        with pytest.raises(Exception):r.stage.prepare(payload=r.packet)
        worker=r.stage.worker
        assert 1000<=worker.arm_lease_ms<r.profile.radio.lease_ms
        assert worker.arm_lease_end_upper_bound<r.profile.child.application_deadline_monotonic_us/1000000
        assert worker.profile==r.profile.radio
        arm=next(f for f in r.firmware['commands'] if f.type is Message.GFSK250_ARM)
        assert r.observer._armed_record[3]==arm.body
        assert r.firmware['frames']==[] and r.observer.physical_disarmed is True
        assert r.stage.report()['uncertain'] and paid(r)==[r.profile.child.to_dict()]


def test_acknowledged_arm_lease_cannot_change_after_observation(tmp_path,monkeypatch):
    with factory250(tmp_path,monkeypatch) as r:
        r.stage.prepare(payload=r.packet);worker=r.stage.worker
        lease=worker.arm_lease_ms;worker.arm_lease_ms+=1
        with pytest.raises(ForwardRadioError):r.observer.current(worker)
        worker.arm_lease_ms=lease
        assert r.stage._cleanup(time.monotonic()+3) and paid(r)==[r.profile.child.to_dict()]


def test_current_boot_capability_cannot_be_reused_after_identity_replacement(tmp_path,monkeypatch):
    with factory250(tmp_path,monkeypatch) as r:
        r.stage.prepare(payload=r.packet);worker=r.stage.worker;identity=worker.identity
        worker.identity=replace(identity,boot=bytes(16));before=r.stage.journal.snapshot()
        with pytest.raises(Exception):worker.transmit(b'not authorized by earlier boot')
        assert r.stage.journal.snapshot()==before and r.firmware['frames']==[]
        worker.identity=identity
        assert r.stage._cleanup(time.monotonic()+3) and paid(r)==[r.profile.child.to_dict()]


@pytest.mark.parametrize('change',['expiry','revocation'])
def test_original_expiry_or_revocation_refuses_rf_but_containment_keeps_paid_caps(tmp_path,monkeypatch,change):
    with factory250(tmp_path,monkeypatch) as r:
        r.stage.prepare(payload=r.packet)
        if change=='expiry':r.state.now=(r.owner.context.expires_epoch_us,r.profile.child.application_deadline_monotonic_us)
        else:r.state.owner_live=False
        before=r.stage.journal.snapshot()
        with pytest.raises(Exception):r.stage.worker.transmit(b'never transmitted')
        assert r.firmware['frames']==[] and r.stage.journal.snapshot()==before
        assert r.stage._cleanup(time.monotonic()+3) and r.observer.physical_disarmed is True
        assert paid(r)==[r.profile.child.to_dict()]


def test_unknown250_outcome_is_paid_and_fenced_not_reported_terminal(tmp_path,monkeypatch):
    with factory250(tmp_path,monkeypatch,damage='unknown-tx') as r:
        r.stage.prepare(payload=r.packet)
        packet=r.stage.flow.cipher.seal(r.packet[:208],FrameKind.DATA,authorize=r.stage._authorize)
        assert r.stage.worker.transmit(packet).outcome=='UNKNOWN'
        before=r.stage.journal.snapshot();assert before['attempts']==1 and before['fenced']
        assert not r.stage._cleanup(time.monotonic()+3) and r.observer.physical_disarmed is True
        assert r.stage.report()['uncertain'] and not r.stage._terminal(r.profile.child,None)
        assert paid(r)==[r.profile.child.to_dict()]


class Gfsk250WirePair(composition.WirePair):
    """Same real dual-PTY composition, with exact v2 command/status/RX bytes."""
    def _firmware(self,index):
        try:
            decoder=Decoder();boot=self.boots[index];mode='lora'
            while not self.stop.is_set():
                if not select.select([self.masters[index]],[],[],.01)[0]:continue
                for frame in decoder.feed(os.read(self.masters[index],4096)):
                    self.commands[index].append(frame.type)
                    if frame.type is Message.HELLO:
                        body=b'\x01\x01\x00\xff'+GFSK250_FIRMWARE.encode()+bytes(16-len(GFSK250_FIRMWARE));kind=Message.HELLO_REPLY
                    elif frame.type is Message.GFSK250_CAPABILITIES:body=CAPABILITY;kind=Message.GFSK250_CAPABILITIES_REPLY
                    elif frame.type is Message.DISARM:self.armed[index]=False;body=b'\0';kind=Message.DISARM_REPLY
                    elif frame.type is Message.GFSK250_ARM:
                        self.profiles[index]=frame.body;self.armed[index]=True;mode='gfsk250';body=frame.body;kind=Message.GFSK250_ARM_REPLY
                    elif frame.type in (Message.STATUS,Message.GFSK_STATUS,Message.GFSK250_STATUS):
                        if frame.type is (Message.STATUS if mode=='lora' else Message.GFSK250_STATUS):
                            body=self.profiles[index]+struct.pack('!IQI3B',self.spent[index],self.reserved[index],0,int(self.armed[index]),0,0)
                            kind=Message.STATUS_REPLY if mode=='lora' else Message.GFSK250_STATUS_REPLY
                        else:kind=Message.REFUSED;body=b'\x01'
                    elif frame.type is Message.TRANSMIT:
                        assert self.armed[index];packet=frame.body;self.frames[index].append(packet)
                        estimate=estimated_airtime_us(Gfsk250Profile.decode(self.profiles[index],board_id=1),len(packet))
                        self.spent[index]+=1;self.reserved[index]+=estimate
                        for outcome in (Outcome.STARTED,Outcome.RF_DONE):
                            body=struct.pack('!B32sQQIh',outcome,hashlib.sha256(packet).digest(),estimate,
                                0 if outcome is Outcome.STARTED else estimate,0 if outcome is Outcome.STARTED else 1,0)
                            self.send(index,Frame(Message.TX_RESULT,boot,frame.request,body))
                        if self.armed[1-index]:
                            self.send(1-index,Frame(Message.GFSK250_RECEIVE,self.boots[1-index],0,struct.pack('!h',-650)+packet))
                        continue
                    else:raise AssertionError('unexpected GFSK250 firmware command')
                    self.send(index,Frame(kind,boot,frame.request,body))
        except BaseException as error:
            if not self.stop.is_set():self.errors.append(error)


def test_five_gfsk250_private_endpoint_stages_preserve_crypto_and_one_inbox(tmp_path,monkeypatch,shared_process_peer_scheduling):
    old_plan=composition.plan
    def gfsk_plan(owner,slot,packet):
        p=old_plan(owner,slot,packet);p['radio_modem']='gfsk250'
        p['profile']=replace(profile_for('gfsk250'),lease_ms=45000).to_dict();return p
    monkeypatch.setattr(composition,'FIRMWARE',GFSK250_FIRMWARE)
    monkeypatch.setattr(composition,'WirePair',Gfsk250WirePair)
    monkeypatch.setattr(composition,'plan',gfsk_plan)
    composition.test_five_radio_factories_wire_crypto_capture_and_one_private_inbox(
        tmp_path,monkeypatch,'none',shared_process_peer_scheduling)
