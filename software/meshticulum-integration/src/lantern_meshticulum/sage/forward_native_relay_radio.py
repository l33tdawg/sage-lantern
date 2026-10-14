# SPDX-License-Identifier: Apache-2.0
"""Explicit A-owned GFSK250 hop for the native opaque relay.

The adjacent grant is independently provisioned. A's signed enrollment binds
its own identity, original relay approval, exact hop, source and held device
FDs. No endpoint owner, Noise key, opener, enrollment writer or public pilot
permission is constructed here. Source controls are not RF qualification.
"""
import base64
from dataclasses import asdict, dataclass
import errno
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time

from nacl.signing import VerifyKey
from meshticulum.bench.authority import RunAuthority, canonical
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.gfsk250_profile import Gfsk250Profile
from meshticulum.bench.hat_worker import Gfsk250HATWorker, journal_binding as hat_binding
from meshticulum.bench.linux_hat import LinuxHAT
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.serial_worker import Gfsk250SerialWorker, journal_binding as serial_binding
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import BenchGrant, SessionCipher
from meshticulum.contracts.identity import PeerID, SessionKey, ServiceKind
from meshticulum.privacy.forward_relay import RelayHop
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from .forward_budget import Admission, ChildPlan, Cost
from .forward_native_relay import (
    ForwardNativeRelay, NativeRelayFactory, RelayChild, RelayPrepared, RelayTerminal)
from .forward_operation import describe_child
from .forward_radio import NativeRadioObserver, ACK_GRACE_US
from .mesh_inbox import _canonical, _id
from .radio_native_adoption import (
    NativeRadioAdoption, _held_radio_binding, _identity, _exclusive, private_radio_profile,
    _HELD_RADIO_BINDING as _ORIGINAL_HELD_RADIO_BINDING,
    source_pins as endpoint_source_pins)
from .workflow_store import WorkflowStore
from ..bench.profiles import GFSK250_FIRMWARE, admission_for, profile_record, radio_route

SCHEMA = 'sage.lantern.native-relay-radio-adoption.v1'
DOMAIN = (SCHEMA + '/signature\0').encode()
SCOPE = 'meshticulum_forward_relay_radio_adopt'
SOURCE_MODULES = (
    'lantern_meshticulum.sage.forward_native_relay_radio',
    'lantern_meshticulum.sage.forward_native_relay',
    'lantern_meshticulum.sage.forward_native_provider',
    'lantern_meshticulum.sage.forward_children',
    'meshticulum.privacy.forward_relay', 'meshticulum.bench.attempts',
    'sage_lantern.mesh_daemon', 'sage_lantern.mesh_community',
)


class RelayRadioError(RuntimeError):
    def __init__(self): super().__init__('native_relay_radio_unavailable')


def require(value):
    if not value: raise RelayRadioError()


def released(fd, identity):
    try: return _identity(fd) != identity
    except OSError as error:
        if error.errno == errno.EBADF: return True
        raise


def source_pins():
    return {name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()
            for name in SOURCE_MODULES}


def hop_value(hop):
    return dict(carrier=hop.carrier, initiator=asdict(hop.session.initiator), responder=asdict(hop.session.responder),
        session_id=hop.session.session_id.hex(), generation=hop.session.session_generation.hex(),
        route_binding_digest=hop.route_binding_digest.hex(), grant_digest=hop.grant_digest.hex())


@dataclass(frozen=True, slots=True, repr=False)
class RelayRadioProfile:
    context_digest: bytes
    intention_digest: bytes
    approval: str
    plan_bytes: bytes
    grant: BenchGrant
    radio: Gfsk250Profile
    child: ChildPlan
    hop: RelayHop


def plan_radio_hop(owner, native_plan, secret, *, slot, kind, sends):
    """Describe A's exact adjacent hop; this opens nothing and grants no rights."""
    require(type(owner) is ForwardNativeRelay and type(native_plan) is dict
            and type(kind) is int and 1 <= kind <= 5 and type(sends) is bool
            and type(slot) is int and slot == (kind - 1) * 2 + int(sends))
    try:
        raw = _canonical(native_plan); p = json.loads(raw)
        keys = {'schema','unit','host_boot_id','local_peer','initiator','responder','session_id',
            'generation','issued_epoch_us','expires_epoch_us','profile','object','sender_unit','country',
            'antenna_confirmed','max_frames','service','application_deadline_monotonic_us','radio_modem'}
        endpoints = tuple(PeerID(x.chain_id, x.public_key.hex())
                          for x in (owner.context.initiator, owner.context.responder))
        first, last = endpoints if kind % 2 else endpoints[::-1]
        first, last = (owner.local, last) if sends else (first, owner.local)
        require(set(p) == keys and p['radio_modem'] == 'gfsk250'
            and p['schema'] == 'meshticulum.service-run.v1'
            and owner.local == owner.description.relay_peer and owner.local not in endpoints
            and PeerID(**p['local_peer']) == owner.local
            and PeerID(**p['initiator']) == first and PeerID(**p['responder']) == last
            and type(p['unit']) is str and type(p['sender_unit']) is str
            and (p['sender_unit'] == p['unit']) is sends
            and p['host_boot_id'] == owner.intention.host_boot_id
            and p['issued_epoch_us'] == owner.context.issued_epoch_us
            and p['expires_epoch_us'] == owner.context.expires_epoch_us
            and type(p['application_deadline_monotonic_us']) is int
            and 0 < p['application_deadline_monotonic_us'] <= min(
                owner.intention.application_deadline_monotonic_us - 1000000,
                int(owner.description.deadlines.application_deadline_monotonic * 1000000))
            and p['service'] == dict(kind=ServiceKind.MESSAGE.value, format='MPF2.record',
                application_expires_epoch_us=owner.context.expires_epoch_us)
            and set(p['object']) == {'bytes','sha256','chunk_index','object_id'}
            and p['object']['chunk_index'] == slot and p['object']['object_id'] == owner.context.digest.hex())
        radio = private_radio_profile(p)
        require(type(radio) is Gfsk250Profile)
        grant = BenchGrant(SessionKey(first, last, bytes.fromhex(p['session_id']), bytes.fromhex(p['generation'])),
            'bench_transfer', hashlib.sha256(canonical(radio_route(p))).digest(),
            p['issued_epoch_us'], p['expires_epoch_us'], 208, p['max_frames'], secret)
        require(grant.max_frames <= 512 and grant.expires_epoch_us - grant.issued_epoch_us <= 240000000)
        child = ChildPlan(slot, kind, sends, grant.digest.hex(), p['object']['sha256'], p['object']['bytes'],
            'lora', hashlib.sha256(canonical(profile_record(radio))).hexdigest(), p['host_boot_id'],
            owner.context.expires_epoch_us, p['application_deadline_monotonic_us'],
            Cost(radio.max_boot_attempts, radio.max_boot_estimated_airtime_us), grant.max_frames)
        hop = RelayHop('radio', grant.session, grant.route_binding_digest, grant.digest)
        return RelayRadioProfile(owner.context.digest, hashlib.sha256(_canonical(owner.intention.to_dict())).digest(),
            owner._approval, raw, grant, radio, child, hop)
    except Exception:
        raise RelayRadioError() from None


class RelayRadioAdoption:
    """Fresh signed A enrollment and exact held FDs; never an endpoint owner."""
    def __init__(self, owner, profile, directory, transport):
        require(type(owner) is ForwardNativeRelay and type(profile) is RelayRadioProfile
            and type(transport) in (ExclusiveSerial, LinuxHAT) and type(owner._store) is WorkflowStore)
        self.owner, self.profile, self.child, self.transport = owner, profile, profile.child, transport
        self.store, self.directory = owner._store, Path(directory)
        require(self.directory.is_absolute() and '..' not in self.directory.parts
            and self.store.client is self.store.storage.client is owner.resource.probe.client)
        self._refs = (owner, self.child, transport, self.store, self.store.client, self.store.storage,
                      self.directory, owner.resource, owner.authority, owner.native_context)
        self._process = os.getpid(), threading.get_ident()
        self._busy = self._adopted = self._closed = self._uncertain = False
        self._child = None; self._highest = 0; self._record = None; self._files = {}; self._directory_fd = None
        self._last_time = None; self._child_value = _canonical(self.child.to_dict())
        names = ('fd','lock_fd') if type(transport) is ExclusiveSerial else ('spi_fd','chip_fd','lines_fd','lock_fd')
        self._fds = tuple((name, getattr(transport, name), _identity(getattr(transport, name))) for name in names)
        device, lock = self._fds[0][2], self._fds[-1][2]
        require(stat.S_ISCHR(device[3]) and device[4] == os.geteuid()
            and stat.S_ISREG(lock[3]) and stat.S_IMODE(lock[3]) == 0o600 and lock[4] == os.geteuid())
        methods = (('read','write','close','monotonic') if type(transport) is ExclusiveSerial
                   else ('transfer','close','monotonic','reset','receive_switch'))
        self._methods = tuple((name, getattr(type(transport), name)) for name in methods)
        self._fd_refs, self._method_refs = self._fds, self._methods
        self.record_id = _id(SCHEMA, [asdict(owner.local), owner.intention.operation_id, self.child.slot])
        try:
            self._guard(); self._read_grant()
            self._binding = _canonical(dict(context_digest=owner.context.digest.hex(), exchange_approval=owner._approval,
                child=self.child.to_dict(), hop=hop_value(profile.hop), config_revision=owner.config_revision,
                broker_id=owner.broker_id, storage_epoch=owner.storage_epoch, directory=str(self.directory),
                grant_files=self._file_hashes, transport=type(transport).__name__,
                device_fds=[[n,list(identity)] for n,_,identity in self._fds],
                sources=endpoint_source_pins(), relay_sources=source_pins()))
            self._binding_ref = self._binding
            self._guard()
        except BaseException:
            self.release(); raise

    # Reuse the exact protected path/file reader. The owner/plan/enrollment
    # policy below remains separate; no endpoint type check is relaxed.
    _read_grant = NativeRadioAdoption._read_grant

    def _bound(self):
        require(_held_radio_binding is _ORIGINAL_HELD_RADIO_BINDING)
        _held_radio_binding(self)
        require(type(self.profile) is RelayRadioProfile and self.profile.child is self.child)

    def _guard(self):
        self._bound(); o = self.owner; h = o.resource
        require(o.access_check() is True)
        require(o.authority.authorized(o.native_context, SCOPE, None) is True)
        o._available(); self._bound()
        require(o._position < 5 and self.child.kind == o._position + 1
            and self.child.slot == o._position * 2 + int(self.child.sends)
            and h._provider is None and getattr(h, '_coordinator', None) is None
            and not getattr(h, '_initializing', False) and getattr(h, '_radio_observer', None) is None
            and (getattr(h, '_forward_radio', None) is None or h._forward_radio is self))
        now = o._sample()
        require(now[1] < self.child.application_deadline_monotonic_us
            and (self._last_time is None or all(a >= b for a,b in zip(now, self._last_time))))
        self._last_time = now
        if type(self.transport) is LinuxHAT: require(self.transport.boot_id == self.child.host_boot_id)
        return True

    def _validate_plan(self, p, secret):
        current = plan_radio_hop(self.owner, p, secret, slot=self.child.slot,
                                kind=self.child.kind, sends=self.child.sends)
        require(current == self.profile)

    def _assets(self):
        NativeRadioAdoption._assets(self)
        require(source_pins() == json.loads(self._binding)['relay_sources'])

    def observe(self):
        self._guard(); self._assets()
        record = self.store.get(self.record_id, kind='mesh_outbound', access_check=self._guard)
        self._guard(); value = record['payload']; body = value['body']
        require(set(value) == {'schema','body','signature'} and value['schema'] == SCHEMA)
        signature = base64.b64decode(value['signature'], validate=True)
        require(len(signature) == 64 and base64.b64encode(signature).decode() == value['signature'])
        VerifyKey(bytes.fromhex(self.owner.local.agent_id)).verify(DOMAIN + _canonical(body), signature)
        require(set(body) == {'record_id','revision','binding','state'}
            and body['record_id'] == record['record_id'] == self.record_id
            and type(body['revision']) is int and body['revision'] == record['revision'] > 0
            and _canonical(body['binding']) == self._binding and body['state'] == 'active')
        raw = _canonical(body)
        require(body['revision'] >= self._highest and (body['revision'] != self._highest or raw == self._record))
        self._highest, self._record = body['revision'], raw
        self._assets(); self._guard(); return True

    def adopt(self, resource):
        require(resource is self.owner.resource and not self._busy and not self._adopted)
        self._busy = True
        try:
            self.observe(); o = self.owner
            require(o._held and o._native_refs[-1].locked() and o._busy and o._active is not None)
            children = [c for c in o._active[1:] if c.plan is self.child]
            require(len(children) == 1)
            child = children[0]; child.bound()
            require(type(child) is RelayChild and child.owner is o and child.hop is self.profile.hop
                and type(child.admission) is Admission and child.admission.owner is o._budget
                and child.admission.plan is self.child and not child.admission.bound
                and o._budget._permits.get(id(child.admission)) is child.admission)
            # ForwardNativeRelay checks both protected allocations before it
            # takes this owner lease. Reentering check_pending here would try
            # to acquire that same non-reentrant lease, not add authority.
            registered = child.registration; o._registry._child(registered)
            require(registered.owner is o and registered.plan is self.child and registered.opening
                and registered.open_thread == threading.get_ident() and not registered.unknown
                and getattr(resource, '_forward_radio', None) is None)
            self._child = registered; resource._forward_radio = self; self._adopted = True
            self.observe(); return self
        except BaseException:
            self._uncertain = True; self.release(); raise
        finally: self._busy = False

    def current(self):
        if self._busy: self._uncertain = True; return False
        self._busy = True
        try:
            require(self._adopted and self.owner.resource._forward_radio is self)
            self.owner._registry._child(self._child)
            return self.observe()
        except Exception:
            self._uncertain = True; return False
        finally: self._busy = False

    def owned(self):
        """Exact original FDs, with foreign cleanup limited to active registry drain."""
        require(os.getpid() == self._process[0])
        if self._process != (os.getpid(), threading.get_ident()):
            o = self.owner; o._available(cleanup=True)
            require(o._closed and o._drain_pending and not o._draining_busy()
                and self._child is not None and self._child.observing
                and self._child.observe_thread == threading.get_ident())
        require(self.transport is self._refs[2] and self._fds is self._fd_refs
            and self._methods is self._method_refs)
        require(all(getattr(self.transport,n) == fd and _identity(fd) == identity for n,fd,identity in self._fd_refs))
        require(all(name not in self.transport.__dict__ and getattr(type(self.transport),name) is method
                    for name,method in self._method_refs))
        return True

    def release(self):
        if self._closed: return not self._uncertain
        ok = True
        try:
            if self._adopted: self.owned()
            else: require(self._process == (os.getpid(), threading.get_ident()))
        except Exception: return False
        if self._adopted:
            for name,fd,identity in self._fd_refs:
                try:
                    require(getattr(self.transport,name) == fd and _identity(fd) == identity); os.close(fd)
                except Exception: ok = False
                finally:
                    if getattr(self.transport,name) == fd: setattr(self.transport,name,None)
            if self.owner.resource._forward_radio is self and ok: self.owner.resource._forward_radio = None
        for _,(fd,identity) in getattr(self,'_file_refs',tuple(self._files.items())):
            try: require(_identity(fd) == identity); os.close(fd)
            except Exception: ok = False
        self._files.clear()
        if self._directory_fd is not None:
            try: require(_identity(self._directory_fd) == self._directory_identity); os.close(self._directory_fd)
            except Exception: ok = False
            self._directory_fd = None
        self._closed = True; self._uncertain = self._uncertain or not ok
        return ok


class RelayRadioObserver(NativeRadioObserver):
    """Reuse unchanged modem/boot/ARM/status parsing with A's own adoption."""
    def __init__(self, owner, profile, transport):
        require(type(owner) is ForwardNativeRelay and type(profile) is RelayRadioProfile
            and type(transport) in (ExclusiveSerial, LinuxHAT))
        self.owner,self.profile,self.transport = owner,profile,transport
        self._refs = (owner,profile,transport); self.adoption = self.worker = self.identity = None
        self.last_status = self.physical_disarmed = None; self._used = False
        self._armed_record = self._armed_ref = None
        self._methods = tuple((name,getattr(type(self),name)) for name in
            ('device','discovered','armed','status','_status250','owned','current','disarmed','bind'))

    def bind(self, adoption):
        self._bound(); require(not self._used and type(adoption) is RelayRadioAdoption)
        self._used = True; self.adoption = adoption; self._bound(); self.device()

    def owned(self):
        self._bound(); require(type(self.adoption) is RelayRadioAdoption)
        return self.adoption.owned()


class NativeRelayRadioFactory(NativeRelayFactory):
    def __init__(self, owner, profile, directory, transport, *, observer):
        require(type(self) is NativeRelayRadioFactory and type(owner) is ForwardNativeRelay and type(profile) is RelayRadioProfile
            and type(observer) is RelayRadioObserver and observer.owner is owner
            and observer.profile is profile and observer.transport is transport)
        self.owner,self.profile,self.directory,self.transport,self.observer = owner,profile,Path(directory),transport,observer
        self._refs = (owner,profile,self.directory,transport,observer)
        self._thread = os.getpid(),threading.get_ident()
        self._busy = self._used = self._unknown = self._retired = False
        self._handles = {}; self._child = self._evidence = self._binding = None
        self.adoption = self.authority = self.worker = self.journal = self.store = self.flow = self.transfer = None
        self._authorize_callback = self._authorize
        self._authority_refs = self._journal_refs = self._store_ref = self._transport_refs = None
        self._disarmed = self._arm_started = self._grant_retired = self._wire_retired = False
        self._snapshot = self._retirement = None
        self._store_closed = self._journal_closed = False

    def _bound(self, *, cleanup=False):
        require(os.getpid() == self._thread[0] and all(a is b for a,b in zip(self._refs,
            (self.owner,self.profile,self.directory,self.transport,self.observer))))
        if self._thread != (os.getpid(),threading.get_ident()):
            require(cleanup); self.owner._available(cleanup=True)
            require(self._child is not None and self._child.registration.observing
                and self._child.registration.observe_thread == threading.get_ident())
        self.observer._bound()
        require(self.profile == plan_radio_hop(self.owner,json.loads(self.profile.plan_bytes),self.profile.grant.secret,
            slot=self.profile.child.slot,kind=self.profile.child.kind,sends=self.profile.child.sends))
        require(all(getattr(self,n) is value for n,value in self._handles.items()))
        if self.authority is not None:
            require(_canonical(self.authority.plan) == self.profile.plan_bytes
                and self.authority.grant.digest == self.profile.grant.digest
                and self.authority.application_deadline_us == self.profile.child.application_deadline_monotonic_us)
        if self._transport_refs is not None:
            require(all(a is b for a,b in zip(self._transport_refs,
                (self.authority,self.worker,self.journal,self.store,self.flow,self.transfer)))
                and self.transfer.flow is self.flow and self.flow.worker is self.worker
                and self.worker.journal is self.journal and self.flow.cipher.store is self.store
                and self.flow.cipher.grant is self.authority.grant)
        if self.worker is not None and self._arm_started:
            require(self.worker.journal is self.journal and self.worker.authorize is self._authorize_callback)
        if self.worker is not None:
            require(type(self.worker) is (Gfsk250SerialWorker if type(self.transport) is ExclusiveSerial else Gfsk250HATWorker))
        if self._binding is not None:
            value = json.loads(self._binding)
            require(value['sources'] == endpoint_source_pins() and value['relay_sources'] == source_pins())

    def _live(self):
        self._bound(); require(not self._unknown and not self._retired and self.adoption.current())
        self.observer.device(); self._bound(); return True

    def _authorize(self):
        require(self.owner._held and self.owner._busy and self.owner._native_refs[-1].locked())
        self._live(); require(self.authority.live()); self._live(); return True

    def _owned(self):
        try: return self._live()
        except Exception: return False

    def _observed(self, *, cleanup=False):
        now = self.owner._sample(live=not cleanup)
        return RelayPrepared(self.profile.child,self.profile.hop,self._evidence,
            self.owner.intention.host_boot_id,*now)

    def prepared(self, owner, plan, hop):
        self._bound(); require(owner is self.owner and plan is self.profile.child and hop is self.profile.hop
            and not self._used and not self._busy and not self._unknown)
        adoption = RelayRadioAdoption(owner,self.profile,self.directory,self.transport)
        try:
            adoption.observe()
            if self._binding is None:
                self._binding = adoption._binding; self._evidence = hashlib.sha256(self._binding).hexdigest()
            require(self._binding == adoption._binding)
            return self._observed()
        finally: require(adoption.release())

    def open(self, owner, child):
        self._bound(); require(owner is self.owner and type(child) is RelayChild
            and child.owner is owner and child.plan is self.profile.child and child.hop is self.profile.hop
            and child.factory is self and owner._held and owner._busy and not self._busy and not self._used
            and self._binding is not None)
        self._busy = self._used = True; self._child = child
        try:
            self.adoption = RelayRadioAdoption(owner,self.profile,self.directory,self.transport)
            self._handles['adoption'] = self.adoption
            require(self.adoption._binding == self._binding)
            owner.resource.adopt_forward_relay_radio(self.adoption); self.observer.bind(self.adoption)
            self.authority = RunAuthority(self.directory,expected_unit=json.loads(self.profile.plan_bytes)['unit'],
                host_boot_id=child.plan.host_boot_id,clock=owner._clock,owner_probe=self._owned,
                radio_admission=admission_for(self.profile.radio))
            self._handles['authority'] = self.authority
            self._authority_refs = (self.authority.fds,tuple((n,fd,_identity(fd)) for n,fd in self.authority.fds.items()))
            self._live(); require(self.authority.profile == self.profile.radio)
            deadline = min(self.authority.deadline_us,self.authority.application_deadline_us)/1000000
            require(self.transport.monotonic()+self.profile.radio.lease_ms/1000+3 < deadline)
            self.store = SessionStore(self.directory/'wire.sqlite',self.authority.grant,self.authority.local_peer,
                boot_id=child.plan.host_boot_id,clock=owner._clock,create=True)
            self._handles['store'] = self.store; self._store_ref = self.store._db
            if type(self.transport) is ExclusiveSerial:
                self.worker = Gfsk250SerialWorker(self.transport); self._handles['worker'] = self.worker
                self._live(); self.worker.discover(expected_board_id=1,expected_version=GFSK250_FIRMWARE)
                self._live(); self.observer.discovered(self.worker)
                status = self.observer.last_status
                require(status['attempts'] < self.profile.radio.max_boot_attempts
                    and status['reserved_us'] < self.profile.radio.max_boot_estimated_airtime_us)
                binding = serial_binding(self.authority.grant.digest,self.worker.identity,self.profile.radio)
            else: binding = hat_binding(self.authority.grant.digest,child.plan.host_boot_id,self.profile.radio)
            self.journal = AttemptJournal(self.directory/'attempts.sqlite',binding=binding,
                max_attempts=child.plan.cost.radio_attempts,max_airtime_us=child.plan.cost.estimated_airtime_us,create=True)
            self._handles['journal'] = self.journal
            self._journal_refs = (self.journal.db,self.journal.lock_fd,_identity(self.journal.lock_fd))
            self._live(); self._arm_started = True
            if type(self.transport) is ExclusiveSerial:
                self.worker.arm(journal=self.journal,profile=self.authority.profile,context_digest=self.authority.grant.digest,
                    authorize=self._authorize_callback,authority_deadline=deadline)
                self._live(); self.observer.armed(self.worker); self.observer.status()
            else:
                self.worker = Gfsk250HATWorker.__new__(Gfsk250HATWorker)
                self._handles['worker'] = self.worker; self.observer.worker = self.worker
                Gfsk250HATWorker.__init__(self.worker,self.transport,journal=self.journal,profile=self.authority.profile,
                    context_digest=self.authority.grant.digest,authorize=self._authorize_callback,authority_deadline=deadline,
                    antenna_confirmed=json.loads(self.profile.plan_bytes)['antenna_confirmed'])
                self._live()
            ledger = Ledger(BudgetPolicy(240000000,60000000,120000000,1000000,quantization_us=1),
                self.authority.ledger_epoch,owner._clock()[1])
            self.flow = BenchFlow(authority=self.authority,worker=self.worker,
                cipher=SessionCipher(self.authority.grant,self.authority.local_peer,self.store),journal=self.journal,
                flow_key=self.authority.flow_key,contract=self.authority.contract,profile=self.authority.carrier_profile,
                ledger=ledger,scheduler_epoch=self.authority.scheduler_epoch,broker_epoch=self.authority.broker_epoch,
                sends=child.plan.sends,clock=owner._clock)
            self._handles['flow'] = self.flow; self.transfer = ServiceTransfer(self.flow)
            self._handles['transfer'] = self.transfer
            self._transport_refs = (self.authority,self.worker,self.journal,self.store,self.flow,self.transfer)
            require(describe_child(self.transfer,slot=child.plan.slot,kind=child.plan.kind) == child.plan)
            self._live(); return self.transfer
        except BaseException:
            self._unknown = True
            # The registry retains this factory even if the caller never gets
            # a transfer. Never release a possibly armed device on failure.
            raise
        finally: self._busy = False

    def current(self, owner, child):
        require(owner is self.owner and child is self._child and child.transfer is self.transfer)
        self._live(); self.observer.current(self.worker); return self._observed()

    def _cleanup(self, deadline):
        self._bound(cleanup=True); errors = []
        def attempt(callback):
            try: require(time.monotonic() < deadline); callback()
            except BaseException as error: errors.append(type(error).__name__)
        a,w,j,s,f,authority = (self._handles.get(n) for n in ('adoption','worker','journal','store','flow','authority'))
        if w is not None and not self._disarmed:
            def disarm():
                self.observer.owned(); require(time.monotonic()+4.1 < deadline)
                if self._arm_started:
                    require(w.journal is j); w.disarm()
                # A discovered but never armed worker may only observe the
                # previously disarmed device; never disarm another producer.
                require(self.observer.disarmed()); self._disarmed = True
            attempt(disarm)
        elif w is None: self._disarmed = True
        if f is not None: attempt(f.close)
        if self._disarmed:
            if j is not None and not self._journal_closed:
                def snapshot():
                    db,fd,identity = self._journal_refs
                    require(j.db is db and j.lock_fd == fd and _identity(fd) == identity)
                    self._snapshot = j.snapshot()
                    self._snapshot['outcomes'] = dict(j.db.execute('SELECT state,count(*) FROM attempts GROUP BY state'))
                    require(not self._snapshot['fenced'] and not self._snapshot['recovered_unknown']
                        and set(self._snapshot['outcomes']) <= {'RF_DONE','NOT_SENT','REFUSED'})
                attempt(snapshot)
                def close_journal():
                    db,fd,identity = self._journal_refs
                    require(j.db is db and j.lock_fd == fd and _identity(fd) == identity)
                    j.close(); self._journal_closed = True
                attempt(close_journal)
            if s is not None and not self._store_closed:
                def retire(): require(s._db is self._store_ref); s.retire(); self._wire_retired = True
                def close_store(): require(s._db is self._store_ref); s.close(); self._store_closed = True
                attempt(retire); attempt(close_store)
            if authority is not None and not authority.closed:
                def retire_grant():
                    require(a is not None and not authority.held)
                    fd = os.open('active',os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=a._directory_ref)
                    try:
                        identities = {n:identity for n,_,identity in self._authority_refs[1]}
                        require(_identity(fd) == identities['active'])
                        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        os.ftruncate(fd,0); os.write(fd,b'retired\n'); os.fsync(fd); self._grant_retired = True
                    finally: os.close(fd)
                attempt(retire_grant)
                def close_authority():
                    mapping,rows = self._authority_refs
                    require(authority.fds is mapping and tuple(mapping.items()) == tuple((n,fd) for n,fd,_ in rows)
                        and all(_identity(fd) == identity for _,fd,identity in rows))
                    authority.close()
                attempt(close_authority)
            if a is not None: attempt(lambda: require(a.release()))
        self._unknown = self._unknown or bool(errors)
        self._retired = self._disarmed and not errors
        if self._retired and self._retirement is None:
            self._retirement = _canonical(dict(evidence=self._evidence,journal=self._snapshot,
                status=self.observer.last_status,physical_disarmed=self.observer.physical_disarmed,
                grant_retired=self._grant_retired,wire_retired=self._wire_retired))
        return self._retired and not self._unknown

    def retire(self, owner, child, deadline):
        self._bound(); require(owner is self.owner and child is self._child and owner._held
            and not self._busy and self.flow.flow.complete and not self.flow.flow.failure)
        if not child.plan.sends:
            end = owner._clock()[1] + ACK_GRACE_US
            require(end < child.plan.application_deadline_monotonic_us)
            while owner._clock()[1] < end:
                require(time.monotonic()+4.1 < deadline)
                self._live(); self.flow.step(); time.sleep(.002)
        require(self._cleanup(deadline))

    def contain(self, owner, child, deadline):
        self._bound(cleanup=True); require(owner is self.owner and child is self._child
            and owner._closed and not owner._draining_busy() and not self._busy)
        require(self._cleanup(deadline))

    def terminal(self, owner, child):
        self._bound(cleanup=True); require(owner is self.owner and child is self._child and not self._busy
            and self._retired and not self._unknown and self._disarmed and self._retirement is not None
            and self._snapshot is not None and self._journal_closed and self._store_closed
            and self._grant_retired and self._wire_retired and self.flow.closed and self.authority.closed
            and self.adoption._closed and self.journal.lock_fd is None
            and all(getattr(self.transport,n) is None for n,_,_ in self.adoption._fd_refs))
        retained = [(fd,identity) for _,fd,identity in self.adoption._fd_refs]
        retained += [(fd,identity) for _,(fd,identity) in self.adoption._file_refs]
        retained += [(fd,identity) for _,fd,identity in self._authority_refs[1]]
        retained += [(self.adoption._directory_ref,self.adoption._directory_identity),
                     (self._journal_refs[1],self._journal_refs[2])]
        require(all(released(fd,identity) for fd,identity in retained))
        for db in (self.journal.db,self.store._db):
            if db is None: continue
            try: db.execute('SELECT 1')
            except sqlite3.ProgrammingError: pass
            else: raise RelayRadioError()
        directory_fd = os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            require(_identity(directory_fd) == self.adoption._directory_identity)
            active_fd = os.open('active',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory_fd)
            try:
                identities = {n:identity for n,_,identity in self._authority_refs[1]}
                require(_identity(active_fd) == identities['active'] and os.pread(active_fd,9,0) == b'retired\n')
            finally: os.close(active_fd)
        finally: os.close(directory_fd)
        require(self._retirement == _canonical(dict(evidence=self._evidence,journal=self._snapshot,
            status=self.observer.last_status,physical_disarmed=self.observer.physical_disarmed,
            grant_retired=self._grant_retired,wire_retired=self._wire_retired)))
        return RelayTerminal(self._observed(cleanup=True),json.loads(_canonical(self._snapshot)),
            hashlib.sha256(_canonical(self._snapshot)).hexdigest(),hashlib.sha256(self._retirement).hexdigest(),
            True,False,True)
