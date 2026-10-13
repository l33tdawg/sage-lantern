# SPDX-License-Identifier: Apache-2.0
"""One explicitly enrolled MPF2 radio stage over a borrowed Core transport.

No device opener, client, key, grant, enrollment writer or activation default.
The original owner prepays/registers containment before adoption, discovery or
ARM. Native observer uses actual USB/sysfs/firmware replies or fixed HAT pins;
source models are not native qualification. Physical retirement is separate
from application acceptance and never refunds paid maxima.
"""
from dataclasses import dataclass, replace
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import struct
import threading
import time
from types import MethodType

from meshticulum.bench.authority import RunAuthority, canonical
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.hat_worker import HATWorker, Gfsk250HATWorker, journal_binding as hat_binding
from meshticulum.bench.gfsk250_profile import Gfsk250Profile
from meshticulum.bench.gfsk_profile import radio_is_fast
from meshticulum.bench.linux_hat import LinuxHAT
from meshticulum.bench.serial_port import ExclusiveSerial, usb_identity
from meshticulum.bench.serial_protocol import Frame, Message, FirmwareIdentity, RadioProfile, GfskStatus, Gfsk250Status
from meshticulum.bench.serial_worker import SerialWorker, Gfsk250SerialWorker, journal_binding as serial_binding
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import BenchGrant, SessionCipher
from meshticulum.contracts.identity import PeerID, SessionKey
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from .forward_budget import ChildPlan, Cost
from .forward_native_owner import ForwardNativeOwner
from .forward_operation import describe_child
from .mesh_inbox import _canonical
from .radio_native_adoption import NativeRadioAdoption, _identity, private_radio_profile
from ..bench.profiles import GFSK250_FIRMWARE, profile_record, radio_route, admission_for

KINDS = (1,2,3,4,5)
FIRMWARE = 'mtb1-9816f92488'
ACK_GRACE_US = 20000000


class ForwardRadioError(RuntimeError):
    def __init__(self):super().__init__('forward_radio_unavailable')


def require(value):
    if not value:raise ForwardRadioError()


def _radio_types(profile):
    if type(profile) is Gfsk250Profile:return Gfsk250SerialWorker,Gfsk250HATWorker,GFSK250_FIRMWARE
    require(type(profile) is RadioProfile)
    return SerialWorker,HATWorker,FIRMWARE


@dataclass(frozen=True, slots=True, repr=False)
class ForwardRadioProfile:
    context_digest: bytes
    intention_digest: bytes
    approval: str
    plan_bytes: bytes
    grant: BenchGrant
    radio: RadioProfile | Gfsk250Profile
    child: ChildPlan


def plan_stage(owner, native_plan, secret, *, slot, kind):
    """Pure description of independently provisioned native bytes and maxima."""
    require(type(owner) is ForwardNativeOwner and type(native_plan) is dict
            and type(slot) is int and 0<=slot<5 and kind==KINDS[slot])
    try:
        raw=_canonical(native_plan);p=json.loads(raw);radio=private_radio_profile(p)
        route=radio_route(p)
        grant=BenchGrant(SessionKey(PeerID(**p['initiator']),PeerID(**p['responder']),bytes.fromhex(p['session_id']),bytes.fromhex(p['generation'])),
            'bench_transfer',hashlib.sha256(canonical(route)).digest(),p['issued_epoch_us'],p['expires_epoch_us'],208,p['max_frames'],secret)
        sends=(kind%2==1)==owner.local_initiator
        child=ChildPlan(slot,kind,sends,grant.digest.hex(),p['object']['sha256'],p['object']['bytes'],'lora',
            hashlib.sha256(canonical(profile_record(radio))).hexdigest(),owner.intention.host_boot_id,
            owner.context.expires_epoch_us,p['application_deadline_monotonic_us'],
            Cost(radio.max_boot_attempts,radio.max_boot_estimated_airtime_us),grant.max_frames)
        # Reuse the actual adoption's pure grant binding validator; no adoption,
        # file, permission observer or resource is constructed by planning.
        NativeRadioAdoption.validate_plan(owner,child,p,secret)
        require(p['object']['chunk_index']==slot and p['object']['object_id']==owner.context.digest.hex()
                and child.application_deadline_monotonic_us<=owner.intention.application_deadline_monotonic_us-1000000
                and grant.max_frames<=512 and grant.expires_epoch_us-grant.issued_epoch_us<=240000000)
        return ForwardRadioProfile(owner.context.digest,hashlib.sha256(_canonical(owner.intention.to_dict())).digest(),
                                   owner._approval,raw,grant,radio,child)
    except Exception:raise ForwardRadioError() from None


class NativeRadioObserver:
    """Typed native observation, with no caller boolean or permissive callback."""
    def __init__(self,owner,profile,transport):
        require(type(owner) is ForwardNativeOwner and type(profile) is ForwardRadioProfile
                and type(transport) in (ExclusiveSerial,LinuxHAT))
        self.owner,self.profile,self.transport=owner,profile,transport
        self._refs=(owner,profile,transport);self.adoption=None;self.worker=None;self.identity=None
        self.last_status=None;self.physical_disarmed=None;self._used=False
        self._armed_record=self._armed_ref=None
        self._methods=tuple((name,getattr(type(self),name)) for name in
            ('device','_device_facts','_worker_current','discovered','armed','status','_status250','owned','current','disarmed','bind'))

    def _bound(self):
        require(all(a is b for a,b in zip(self._refs,(self.owner,self.profile,self.transport))))
        require(self._armed_record is self._armed_ref)
        if self._armed_record is not None:
            worker,profile,lease,_=self._armed_record
            require(self.worker is worker and worker.profile is profile and profile==self.profile.radio
                    and worker.arm_lease_ms==lease)
        require(all(name not in self.__dict__ and getattr(type(self),name) is method for name,method in self._methods))
        if self.adoption is not None:
            require(self.adoption.owner is self.owner and self.adoption.child is self.profile.child
                    and self.adoption.transport is self.transport)

    def bind(self,adoption):
        self._bound();require(not self._used and type(adoption) is NativeRadioAdoption)
        self._used=True;self.adoption=adoption;self._bound();self.device()

    def device(self):
        self._bound();require(self.adoption is not None and self.adoption.current())
        require(self._device_facts() is True)
        self._bound();require(self.adoption.current());return True

    def _device_facts(self):
        """Fresh native facts only; callers separately bracket live authority."""
        self._bound()
        if type(self.transport) is ExclusiveSerial:
            radio=self.owner.resource.config['radio'];path=Path(radio['stable_path']).resolve(strict=True)
            named=path.stat();held=os.fstat(self.transport.fd)
            require(path.parent==Path('/dev') and path.name.startswith('ttyACM')
                    and (named.st_dev,named.st_ino,named.st_rdev)==(held.st_dev,held.st_ino,held.st_rdev)
                    and usb_identity(path.name)==(radio['vendor_id'],radio['product_id'],radio['serial']))
        else:
            require(b'rockchip,rk3588-orangepi-5-plus\0' in Path('/proc/device-tree/compatible').read_bytes())
            node=Path('/sys/bus/spi/devices/spi0.0/of_node').resolve(strict=True)
            require(str(node).endswith('/spi@feb00000/spidev@0'))
            gpio=Path('/proc/device-tree/pinctrl/gpio@fec40000/phandle').read_bytes()
            require((node.parent/'cs-gpios').read_bytes()==gpio+struct.pack('>II',3,1)
                    and (node/'spi-max-frequency').read_bytes()==struct.pack('>I',1000000))
            named=Path('/dev/spidev0.0').stat();held=os.fstat(self.transport.spi_fd)
            require((named.st_dev,named.st_ino,named.st_rdev)==(held.st_dev,held.st_ino,held.st_rdev))
        self._bound();return True

    def discovered(self,worker):
        serial_type,_,firmware=_radio_types(self.profile.radio)
        self.device();require(type(worker) is serial_type and worker.channel is self.transport
                and type(worker.identity) is FirmwareIdentity and worker.identity.version==firmware
                and worker.identity.board_id==1)
        serial=self.owner.resource.config['radio']['serial']
        require(worker.identity.boot[:8]==bytes(2)+bytes.fromhex(serial.replace(':',''))[::-1])
        self.worker=worker;self.identity=worker.identity;self.status(disarmed=True)

    def armed(self,worker):
        """Pin the acknowledged, possibly narrowed lease before status/flow use."""
        self.device();require(type(self.profile.radio) is Gfsk250Profile
            and type(worker) is Gfsk250SerialWorker and worker is self.worker
            and worker.channel is self.transport and worker.identity is self.identity
            and worker.profile==self.profile.radio and not worker.fenced
            and self._armed_record is None and type(worker.arm_lease_ms) is int
            and 1000<=worker.arm_lease_ms<=self.profile.radio.lease_ms)
        profile=replace(self.profile.radio,lease_ms=worker.arm_lease_ms)
        self._armed_record=(worker,worker.profile,worker.arm_lease_ms,profile.encode(board_id=self.identity.board_id))
        self._armed_ref=self._armed_record;self._bound()

    def status(self,*,disarmed=False,containment=False):
        self._bound();worker=self.worker;serial_type,_,_=_radio_types(self.profile.radio)
        require(type(worker) is serial_type and worker.channel is self.transport
            and worker.identity is self.identity)
        if containment:self.owned()
        else:self.device()
        if type(self.profile.radio) is Gfsk250Profile:return self._status250(disarmed=disarmed,containment=containment)
        request=(worker.journal.command_request(containment=containment) if worker.journal is not None
                 else self.owner._clock()[0])
        deadline=min(self.transport.monotonic()+2,self.profile.child.application_deadline_monotonic_us/1000000) if not containment else self.transport.monotonic()+2
        worker._send(Frame(Message.STATUS,self.identity.boot,request),deadline);reply=worker._wait(request,deadline)
        require(reply.type is Message.STATUS_REPLY and len(reply.body)==49 and reply.boot==self.identity.boot
                and reply.body[46] in (0,1) and reply.body[47:]==bytes(2) and (not disarmed or reply.body[46]==0))
        if reply.body[46]==1:require(reply.body[:30]==self.profile.radio.encode(board_id=self.identity.board_id))
        attempts=int.from_bytes(reply.body[30:34],'big');air=int.from_bytes(reply.body[34:42],'big')
        require(attempts<=self.profile.radio.max_boot_attempts and air<=self.profile.radio.max_boot_estimated_airtime_us)
        if self.last_status is not None:require(attempts>=self.last_status['attempts'] and air>=self.last_status['reserved_us'])
        self.last_status=dict(attempts=attempts,reserved_us=air,disarmed=reply.body[46]==0)
        if not containment:self.device()
        return self.last_status

    def _status250(self,*,disarmed,containment):
        worker=self.worker
        deadline=self.transport.monotonic()+2
        if not containment:deadline=min(deadline,self.profile.child.application_deadline_monotonic_us/1000000)
        commands=(Message.STATUS,Message.GFSK_STATUS,Message.GFSK250_STATUS) if disarmed else (Message.GFSK250_STATUS,)
        base=self.owner._clock()[0] if worker.journal is None else None
        for index,command in enumerate(commands):
            request=worker.journal.command_request(containment=containment) if worker.journal is not None else base+index
            worker._send(Frame(command,self.identity.boot,request),deadline);reply=worker._wait(request,deadline)
            require(reply.boot==self.identity.boot and reply.request==request)
            if reply.type is Message.REFUSED:
                require(disarmed and reply.body==b'\x01' and index+1<len(commands))
                continue
            if command is Message.STATUS:
                require(reply.type is Message.STATUS_REPLY and len(reply.body)==49 and reply.body[46:]==bytes(3))
                attempts=int.from_bytes(reply.body[30:34],'big');air=int.from_bytes(reply.body[34:42],'big');armed=False
            else:
                parser=Gfsk250Status if command is Message.GFSK250_STATUS else GfskStatus
                status=parser.from_reply(reply,boot=self.identity.boot,request=request,board_id=self.identity.board_id)
                require(not status.transmitting and not status.fenced and (not disarmed or not status.armed))
                attempts=status.boot_attempts;air=status.boot_reserved_estimated_airtime_us;armed=status.armed
                if not disarmed:
                    record=self._armed_record
                    require(record is not None and record[0] is worker and record[1] is worker.profile
                        and record[2]==worker.arm_lease_ms and worker.profile==self.profile.radio and armed
                        and status.profile.encode(board_id=self.identity.board_id)==record[3])
            require(attempts<=self.profile.radio.max_boot_attempts and air<=self.profile.radio.max_boot_estimated_airtime_us)
            if self.last_status is not None:require(attempts>=self.last_status['attempts'] and air>=self.last_status['reserved_us'])
            self.last_status=dict(attempts=attempts,reserved_us=air,disarmed=not armed)
            if not containment:self.device()
            return self.last_status
        raise ForwardRadioError()

    def owned(self):
        """Retained FD/source ownership for containment after permission expiry."""
        self._bound();a=self.adoption;require(a is not None)
        require(a._process==(os.getpid(),threading.get_ident()) and a.transport is a._refs[2])
        require(all(getattr(a.transport,n)==fd and _identity(fd)==identity for n,fd,identity in a._fd_refs))
        require(all(name not in a.transport.__dict__ and getattr(type(a.transport),name) is method for name,method in a._method_refs))
        return True

    def current(self,worker):
        self.device();return self._worker_current(worker)

    def _worker_current(self,worker):
        require(worker is self.worker)
        serial_type,hat_type,_=_radio_types(self.profile.radio)
        if type(worker) is serial_type:require(worker.identity is self.identity and not worker.fenced)
        else:require(type(worker) is hat_type and worker.io is self.transport
                     and worker.profile==self.profile.radio and not worker.fenced)
        return True

    def disarmed(self):
        self.owned();w=self.worker
        serial_type,hat_type,_=_radio_types(self.profile.radio)
        if type(w) is serial_type:result=self.status(disarmed=True,containment=True)['disarmed']
        else:
            require(type(w) is hat_type and w.io is self.transport and hasattr(w,'radio'))
            result=not w.radio.fenced and not w.radio.transmitting and (w.radio.status()>>4)&7==2
        self.owned();self.physical_disarmed=result;return result is True


class ForwardRadioFactory:
    def __init__(self,owner,profile,directory,transport,*,observer):
        require(type(owner) is ForwardNativeOwner and type(profile) is ForwardRadioProfile
                and type(observer) is NativeRadioObserver and observer.owner is owner
                and observer.profile is profile and observer.transport is transport)
        require(profile==plan_stage(owner,json.loads(profile.plan_bytes),profile.grant.secret,
                                   slot=profile.child.slot,kind=profile.child.kind))
        self.owner,self.profile,self.directory,self.transport,self.observer=owner,profile,Path(directory),transport,observer
        self._refs=(owner,profile,self.directory,transport,observer);self._thread=(os.getpid(),threading.get_ident())
        self._busy=self._used=self._unknown=self._retired=False;self._handles={};self._transport_refs=None
        self.adoption=self.authority=self.worker=self.journal=self.store=self.flow=self.transfer=self.runner=None
        self._disarmed=False;self._wire_retired=False;self._store_closed=False;self._journal_closed=False
        self._grant_retired=False
        self._snapshot=None
        self._authorize_callback=self._authorize
        self._authorize_callback_ref=self._authorize_callback
        self._owned_callback=self._owned
        self._owned_callback_ref=self._owned_callback
        self._arm_started=False
        self._authority_refs=self._journal_refs=self._store_ref=None
        self._authority_binding=self._authority_binding_ref=None
        self._native_observing=False;self._native_adoption=None
        self._native_methods=tuple((obj,name,getattr(type(obj),name)) for obj,names in (
            (self,('_bound','_native','_native_current','_live','_authorize','_owned','_observed')),
            (observer,('_bound','device','current','_device_facts','_worker_current')),
            (owner,('_available','phase_check')),
            (owner._registry,('_child','available')),
        ) for name in names)
        self._native_method_refs=self._native_methods

    def _bound(self):
        require(self._thread==(os.getpid(),threading.get_ident()) and all(a is b for a,b in zip(self._refs,
                (self.owner,self.profile,self.directory,self.transport,self.observer))))
        self.observer._bound()
        require(self.profile==plan_stage(self.owner,json.loads(self.profile.plan_bytes),self.profile.grant.secret,
                                       slot=self.profile.child.slot,kind=self.profile.child.kind))
        require(all(getattr(self,name) is value for name,value in self._handles.items()))
        require(self._authorize_callback is self._authorize_callback_ref
            and type(self._authorize_callback) is MethodType and self._authorize_callback.__self__ is self
            and self._authorize_callback.__func__ is type(self)._authorize
            and self._owned_callback is self._owned_callback_ref
            and type(self._owned_callback) is MethodType and self._owned_callback.__self__ is self
            and self._owned_callback.__func__ is type(self)._owned
            and self._authority_binding is self._authority_binding_ref)
        if self._authority_binding is not None:
            a,live,clock,owned,grant,digest,secret,profile,deadlines,bounds=self._authority_binding
            bound=a.live
            require(type(a) is RunAuthority and self.authority is a
                and type(bound) is MethodType and bound.__self__ is a and bound.__func__ is live
                and 'live' not in a.__dict__ and type(a).live is live
                and a.clock is clock is self.owner._clock and a.owner_probe is owned is self._owned_callback_ref
                and a.grant is grant and grant.digest==digest and grant.secret is secret
                and a.profile is profile and a.deadlines is deadlines
                and tuple((type(v),v) for v in (a.deadline_us,a.application_expires_epoch_us,
                    a.application_deadline_us,deadlines.application_deadline_monotonic,
                    deadlines.authority_deadline_monotonic,deadlines.authority_expires_epoch))==bounds)
        if self.authority is not None:require(_canonical(self.authority.plan)==self.profile.plan_bytes
            and self.authority.grant.digest==self.profile.grant.digest and self.authority.grant.secret==self.profile.grant.secret
            and self.authority.application_deadline_us==self.profile.child.application_deadline_monotonic_us)
        if self._authority_refs is not None and not self.authority.closed:
            mapping,rows=self._authority_refs
            require(self.authority.fds is mapping and tuple(self.authority.fds.items())==tuple((n,fd)for n,fd,_ in rows)
                    and all(_identity(fd)==identity for _,fd,identity in rows))
        if self._journal_refs is not None and not self._journal_closed:
            db,fd,identity=self._journal_refs
            require(self.journal.db is db and self.journal.lock_fd==fd and _identity(fd)==identity)
        if self._store_ref is not None:require(self.store._db is self._store_ref)
        if self._transport_refs is not None:
            require(all(a is b for a,b in zip(self._transport_refs,(self.authority,self.worker,self.journal,self.store,self.flow,self.transfer)))
                    and self.flow.worker is self.worker and self.worker.journal is self.journal
                    and self.flow.cipher.store is self.store and self.flow.cipher.grant is self.authority.grant
                    and self.transfer.flow is self.flow)
        if self.worker is not None and (self._arm_started or getattr(self.worker,'journal',None) is not None):
            require(self.worker.journal is self.journal and self.worker.authorize is self._authorize_callback)
        if self.worker is not None:
            serial_type,hat_type,_=_radio_types(self.profile.radio)
            require((type(self.worker) is serial_type and self.worker.channel is self._refs[3])
                    or (type(self.worker) is hat_type and (not hasattr(self.worker,'io') or self.worker.io is self._refs[3])))

    def _live(self):
        with self._observed(self.owner.context,self.profile.child,self.transfer,first_current=True) as checked:
            checked(self.observer._device_facts)
            checked(self.adoption.current)
        return True

    def _authorize(self):
        require(type(self.authority) is RunAuthority and self._authority_binding is not None)
        with self._observed(self.owner.context,self.profile.child,self.transfer,first_current=True) as checked:
            checked(self.observer._device_facts)
            checked(self.adoption.current)
            checked(self.authority.live)
            checked(self.adoption.current)
            checked(self.observer._device_facts)
            checked(self.adoption.current)
        return True

    def _owned(self):
        try:return self._live()
        except Exception:return False

    def _open(self,child):
        require(self._busy and child is self.profile.child)
        with self.owner.owner_lease():
            self.adoption=NativeRadioAdoption(self.owner,child,self.directory,self.transport);self._handles['adoption']=self.adoption
            self.owner.resource.adopt_forward_radio(self.adoption)
            a=self.adoption;registry=self.owner._registry
            self._native_adoption=(a,a._child,registry,registry._condition,
                tuple((a,name,getattr(type(a),name)) for name in ('_bound','_guard','_assets','_observe','current')))
            self.observer.bind(self.adoption)
            self.authority=RunAuthority(self.directory,expected_unit=json.loads(self.profile.plan_bytes)['unit'],
                host_boot_id=child.host_boot_id,clock=self.owner._clock,owner_probe=self._owned_callback_ref,radio_admission=admission_for(self.profile.radio))
            a=self.authority
            self._authority_binding=self._authority_binding_ref=(a,type(a).live,a.clock,a.owner_probe,
                a.grant,a.grant.digest,a.grant.secret,a.profile,a.deadlines,
                tuple((type(v),v) for v in (a.deadline_us,a.application_expires_epoch_us,
                    a.application_deadline_us,a.deadlines.application_deadline_monotonic,
                    a.deadlines.authority_deadline_monotonic,a.deadlines.authority_expires_epoch)))
            self._handles['authority']=self.authority;self._live()
            self._authority_refs=(self.authority.fds,tuple((n,fd,_identity(fd))for n,fd in self.authority.fds.items()))
            require(self.authority.profile==self.profile.radio)
            deadline=min(self.authority.deadline_us,self.authority.application_deadline_us)/1000000
            require(self.transport.monotonic()+self.profile.radio.lease_ms/1000+3<deadline)
            self.store=SessionStore(self.directory/'wire.sqlite',self.authority.grant,self.authority.local_peer,
                boot_id=child.host_boot_id,clock=self.owner._clock,create=True);self._handles['store']=self.store
            self._store_ref=self.store._db
            serial_type,hat_type,firmware=_radio_types(self.profile.radio)
            if type(self.transport) is ExclusiveSerial:
                self.worker=serial_type(self.transport);self._handles['worker']=self.worker
                self._live();self.worker.discover(expected_board_id=1,expected_version=firmware);self._live()
                self.observer.discovered(self.worker);status=self.observer.last_status
                require(status['attempts']<self.profile.radio.max_boot_attempts and status['reserved_us']<self.profile.radio.max_boot_estimated_airtime_us)
                binding=serial_binding(self.authority.grant.digest,self.worker.identity,self.profile.radio)
            else:binding=hat_binding(self.authority.grant.digest,child.host_boot_id,self.profile.radio)
            self.journal=AttemptJournal(self.directory/'attempts.sqlite',binding=binding,
                max_attempts=child.cost.radio_attempts,max_airtime_us=child.cost.estimated_airtime_us,create=True);self._handles['journal']=self.journal
            self._journal_refs=(self.journal.db,self.journal.lock_fd,_identity(self.journal.lock_fd))
            self._live()
            self._arm_started=True
            if type(self.transport) is ExclusiveSerial:
                self.worker.arm(journal=self.journal,profile=self.authority.profile,context_digest=self.authority.grant.digest,
                    authorize=self._authorize_callback,authority_deadline=deadline);self._live()
                if type(self.profile.radio) is Gfsk250Profile:self.observer.armed(self.worker)
                self.observer.status()
            else:
                self.worker=hat_type.__new__(hat_type);self._handles['worker']=self.worker;self.observer.worker=self.worker
                hat_type.__init__(self.worker,self.transport,journal=self.journal,profile=self.authority.profile,
                    context_digest=self.authority.grant.digest,authorize=self._authorize_callback,authority_deadline=deadline,
                    antenna_confirmed=json.loads(self.profile.plan_bytes)['antenna_confirmed']);self._live()
            ledger=Ledger(BudgetPolicy(240000000,60000000,120000000,1000000,quantization_us=1),
                          self.authority.ledger_epoch,self.owner._clock()[1])
            self.flow=BenchFlow(authority=self.authority,worker=self.worker,cipher=SessionCipher(self.authority.grant,self.authority.local_peer,self.store),
                journal=self.journal,flow_key=self.authority.flow_key,contract=self.authority.contract,profile=self.authority.carrier_profile,
                ledger=ledger,scheduler_epoch=self.authority.scheduler_epoch,broker_epoch=self.authority.broker_epoch,
                sends=child.sends,clock=self.owner._clock);self._handles['flow']=self.flow
            self.transfer=ServiceTransfer(self.flow);self._handles['transfer']=self.transfer
            self._transport_refs=(self.authority,self.worker,self.journal,self.store,self.flow,self.transfer)
            require(describe_child(self.transfer,slot=child.slot,kind=child.kind)==child);self._live();return self.transfer

    def prepare(self,*,payload=None):
        if self._busy:self._unknown=True;raise ForwardRadioError()
        self._bound();require(not self._used);self._busy=self._used=True
        try:
            self.runner=self.owner.prepare_stage(self.profile.child,open_child=self._open,native_check=self._native,
                retire_child=self._retire,contain_child=self._contain,terminal_check=self._terminal,payload=payload)
            self._live();return self.runner
        except BaseException:
            self._unknown=True;self._cleanup(time.monotonic()+3);raise
        finally:self._busy=False

    def _native(self,context,child,transfer):
        if self._native_observing:self._unknown=True;raise ForwardRadioError()
        self._bound();require(context is self.owner.context and child is self.profile.child and transfer is self.transfer)
        if self._retired:
            require(self._terminal(child,None));self.owner.phase_check(context,child.kind,child.sends)
        else:self._native_current(context,child,transfer)
        self._bound();return True

    def _native_current(self,context,plan,transfer):
        """Three full signed observations around both original factual probes.

        Adjacent wrapper observations are one boundary, not reusable authority.
        SDK guards, source/grant reads and physical probes remain fresh per call.
        """
        with self._observed(context,plan,transfer) as checked:
            checked(self.adoption.current)
            checked(self.observer._device_facts)
            checked(self.adoption.current)
            checked(self.observer._device_facts)
            checked(self.adoption.current)
            checked(self.observer._worker_current,self.worker)

    @contextmanager
    def _observed(self,context,plan,transfer,*,first_current=False):
        """One callback bracket; holds references/ownership, never live results."""
        require(type(first_current) is bool)
        acquired=initial_refusal=False;original=self._native_adoption
        require(type(original) is tuple and len(original)==5)
        a,child,registry,condition,methods=original
        worker=self.worker;native_methods=self._native_method_refs
        authority_binding=self._authority_binding_ref
        handles=self._handles;handle_refs=tuple(handles.items())
        def references(*,refused=False):
            require(self._native_adoption is original and self._native_methods is self._native_method_refs is native_methods
                and type(native_methods) is tuple and len(native_methods)==16
                and type(methods) is tuple and len(methods)==5
                and self._authority_binding is self._authority_binding_ref is authority_binding
                and self._handles is handles and tuple(handles.items())==handle_refs
                and self.adoption is a and a._child is child and self.owner._registry is registry
                and registry._condition is condition and child.owner is self.owner and child.plan is plan
                and self.observer.adoption is a and self.worker is worker
                and context is self.owner.context and plan is self.profile.child and transfer is self.transfer
                and not self._unknown and not self._retired and not a._closed and (refused or not a._uncertain)
                and not registry._closing and not registry._uncertain and not child.unknown and not child.retired
                and not self.owner._closed and (refused or not self.owner._uncertain)
                and not self.owner.resource._closed and not self.owner.resource._closing)
            for obj,name,method in native_methods+methods:
                bound=getattr(obj,name)
                require(type(bound) is MethodType and bound.__self__ is obj and bound.__func__ is method
                    and name not in obj.__dict__ and getattr(type(obj),name) is method)
            if acquired:require(self._native_observing and child.observing and child.observe_thread==threading.get_ident())
        def bindings(*,refused=False):
            references(refused=refused);self._bound();references(refused=refused)
            if not refused:self.owner._available();references()
            registry._child(child);references(refused=refused)
        def checked(callback,*args):
            bindings();value=callback(*args);bindings();require(value is True)
        try:
            bindings(refused=first_current)
            with condition:
                require(not child.observing and not self._native_observing and not registry._closing)
                child.observing=True;child.observe_thread=threading.get_ident()
                self._native_observing=acquired=True
            if first_current:
                bindings(refused=True);value=a.current()
                # A known initial permission refusal occurs before either fact
                # or authority callback. Preserve the old live-mode physical
                # containment result; the adoption itself stays fenced. Caught
                # reentry/cleanup/drain remains an unknown, never authority.
                if value is False and a._uncertain is True and not self._unknown and not registry._closing:
                    bindings(refused=True)
                    require(self.observer.owned() is True);bindings(refused=True)
                    a._assets();bindings(refused=True)
                    initial_refusal=True;raise ForwardRadioError()
                bindings();require(value is True)
            yield checked
            bindings()
        except BaseException as error:
            if not initial_refusal:
                self._unknown=True
                if acquired:a._uncertain=True;child.unknown=True
            if not isinstance(error,Exception):raise
            raise ForwardRadioError() from None
        finally:
            if acquired:
                with condition:
                    child.observing=False;child.observe_thread=None;self._native_observing=False
                    condition.notify_all()

    def _cleanup(self,deadline):
        if self._native_observing:self._unknown=True;return False
        errors=[]
        def attempt(action):
            try:require(time.monotonic()<deadline);action()
            except BaseException as error:errors.append(type(error).__name__)
        a=self._handles.get('adoption');w=self._handles.get('worker');j=self._handles.get('journal');s=self._handles.get('store');f=self._handles.get('flow');authority=self._handles.get('authority')
        if w is not None and not self._disarmed:
            def disarm():
                self.observer.owned();require(time.monotonic()+2<deadline)
                serial_type,hat_type,_=_radio_types(self.profile.radio)
                if type(w) is serial_type:require(w.channel is self._refs[3] and w.journal is j)
                else:require(type(w) is hat_type and w.io is self._refs[3] and w.journal is j)
                w.disarm();require(self.observer.disarmed());self._disarmed=True
            attempt(disarm)
        elif w is None:self._disarmed=True # Factory never created a worker or touched radio commands.
        if f is not None:attempt(f.close)
        if j is not None and not self._journal_closed and self._disarmed:
            def snapshot():
                db,fd,identity=self._journal_refs
                require(j.db is db and j.lock_fd==fd and _identity(fd)==identity)
                self._snapshot=j.snapshot();require(not self._snapshot['fenced'] and not self._snapshot['recovered_unknown'])
            attempt(snapshot)
            def close_journal():
                db,fd,identity=self._journal_refs
                require(j.db is db and j.lock_fd==fd and _identity(fd)==identity)
                j.close();self._journal_closed=True
            attempt(close_journal)
        if s is not None and not self._store_closed and self._disarmed:
            def retire():require(s._db is self._store_ref);s.retire();self._wire_retired=True
            def close_store():require(s._db is self._store_ref);s.close();self._store_closed=True
            attempt(retire);attempt(close_store)
        if authority is not None and not authority.closed and self._disarmed:
            def retire_grant():
                require(a is not None and not authority.held)
                fd=os.open('active',os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=a._directory_ref)
                try:
                    identities={name:identity for name,_,identity in self._authority_refs[1]}
                    require(_identity(fd)==identities['active'])
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    os.ftruncate(fd,0);os.write(fd,b'retired\n');os.fsync(fd);self._grant_retired=True
                finally:os.close(fd)
            attempt(retire_grant)
            def close_authority():
                mapping,rows=self._authority_refs
                require(authority.fds is mapping and tuple(mapping.items())==tuple((n,fd)for n,fd,_ in rows)
                        and all(_identity(fd)==identity for _,fd,identity in rows))
                authority.close()
            attempt(close_authority)
        # Do not drop physical ownership while disarm remains unproved.
        if a is not None and self._disarmed:
            def release():require(a.release())
            attempt(release)
        self._unknown=self._unknown or bool(errors);self._retired=not errors and self._disarmed
        return self._retired and not self._unknown

    def _retire(self,transfer):
        if self._native_observing:self._unknown=True;raise ForwardRadioError()
        self._bound();require(transfer is self.transfer and self.flow.flow.complete)
        if not self.profile.child.sends:
            end=self.owner._clock()[1]+ACK_GRACE_US
            require(end<self.profile.child.application_deadline_monotonic_us)
            while self.owner._clock()[1]<end:
                self._live();self.flow.step();time.sleep(.002 if radio_is_fast(self.profile.radio) else .01)
        return self._cleanup(min(time.monotonic()+3,self.profile.child.application_deadline_monotonic_us/1000000))

    def _contain(self,child,runner,deadline):
        if self._native_observing:self._unknown=True;return False
        require(child is self.profile.child and not self._busy);return self._cleanup(deadline)

    def _terminal(self,child,runner):
        self._bound();return (child is self.profile.child and not self._busy and self._retired and not self._unknown
            and self._disarmed and (self.adoption is None or self.adoption._closed)
            and (self.authority is None or self.authority.closed and self._grant_retired)
            and (self.store is None or self._wire_retired and self._store_closed)
            and (self.journal is None or self._journal_closed))

    def report(self):
        return dict(child=self.profile.child.to_dict(),retired=self._retired,uncertain=self._unknown,
                    physical_disarmed=self.observer.physical_disarmed,attempt_journal=self._snapshot,native_qualified=False)
