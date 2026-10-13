# SPDX-License-Identifier: Apache-2.0
"""Explicit A-owned TCP hop for the private opaque relay.

A's independent, signed native enrollment binds the B-adjacent hop and original
protected grant. The complete pair of child maxima is prepaid before opening a
socket. No endpoint owner, Noise key, grant/enrollment writer, DNS, discovery or
production enablement is supplied here. Kernel byte acceptance and end-to-end
private admission remain separate facts.
"""
import base64
from dataclasses import asdict, dataclass
import errno
import fcntl
import hashlib
import importlib
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time

from nacl.signing import VerifyKey
from meshticulum.bench.services import ServiceTransfer
from meshticulum.contracts.identity import PeerID
from meshticulum.ip.authority import IpAuthority, grant_from_plan
from meshticulum.ip.flow import IpFlow
from meshticulum.ip.journal import IpJournal, private
from meshticulum.ip.state import SessionStore
from meshticulum.ip.worker import TcpWorker
from meshticulum.ip.wire import IpGrant
from meshticulum.privacy.forward_relay import RelayHop
from .forward_budget import Admission, ChildPlan, Cost
from .forward_native_relay import (
    ForwardNativeRelay, NativeRelayFactory, RelayChild, RelayPrepared, RelayTerminal)
from .forward_operation import describe_child
from .forward_tcp import ForwardTcpStage
from .mesh_inbox import _canonical, _id
from .workflow_store import WorkflowStore

SCHEMA = 'sage.lantern.native-relay-ip-adoption.v1'
DOMAIN = (SCHEMA + '/signature\0').encode()
SCOPE = 'meshticulum_forward_relay_ip_adopt'
SOURCE_MODULES = (
    'lantern_meshticulum.sage.forward_native_relay_ip',
    'lantern_meshticulum.sage.forward_native_relay',
    'lantern_meshticulum.sage.forward_native_relay_radio',
    'lantern_meshticulum.sage.forward_native_provider',
    'lantern_meshticulum.sage.forward_children',
    'lantern_meshticulum.sage.forward_budget',
    'lantern_meshticulum.sage.forward_operation',
    'lantern_meshticulum.sage.forward_tcp',
    'lantern_meshticulum.sage.workflow_store',
    'meshticulum.privacy.forward_relay', 'meshticulum.privacy.forward_session',
    'meshticulum.bench.services', 'meshticulum.ip.authority', 'meshticulum.ip.flow',
    'meshticulum.ip.journal', 'meshticulum.ip.state', 'meshticulum.ip.worker',
    'meshticulum.ip.wire', 'meshticulum.flow.flow', 'meshticulum.flow.flow_port',
    'sage_lantern.mesh_daemon', 'sage_lantern.mesh_community',
)


class RelayIpError(RuntimeError):
    def __init__(self): super().__init__('native_relay_ip_unavailable')


def require(value):
    if not value: raise RelayIpError()


def identity(fd):
    require(type(fd) is int and fd >= 0)
    value = os.fstat(fd)
    return value.st_dev, value.st_ino


def released(fd, expected):
    try: return identity(fd) != expected
    except OSError as error:
        if error.errno == errno.EBADF: return True
        raise


def source_pins():
    return {name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()
            for name in SOURCE_MODULES}


def hop_value(hop):
    return dict(carrier=hop.carrier, initiator=asdict(hop.session.initiator),
        responder=asdict(hop.session.responder), session_id=hop.session.session_id.hex(),
        generation=hop.session.session_generation.hex(), route_binding_digest=hop.route_binding_digest.hex(),
        grant_digest=hop.grant_digest.hex())


@dataclass(frozen=True, slots=True, repr=False)
class RelayIpProfile:
    context_digest: bytes
    intention_digest: bytes
    approval: str
    plan_bytes: bytes
    grant: IpGrant
    child: ChildPlan
    hop: RelayHop


def plan_ip_hop(owner, native_plan, secret, *, slot, kind, sends):
    """Pure original A↔B plan, never a socket, permission or endpoint owner."""
    require(type(owner) is ForwardNativeRelay and type(native_plan) is dict
        and type(kind) is int and 1 <= kind <= 5 and type(sends) is bool
        and sends is (kind % 2 == 1) and type(slot) is int and slot == (kind - 1) * 2 + int(sends))
    try:
        raw = _canonical(native_plan); p = json.loads(raw)
        endpoints = tuple(PeerID(x.chain_id, x.public_key.hex())
                          for x in (owner.context.initiator, owner.context.responder))
        first, last = (owner.local, endpoints[1]) if sends else (endpoints[1], owner.local)
        grant = grant_from_plan(p, secret, local_peer=owner.local, host_boot_id=owner.intention.host_boot_id)
        require(owner.local == owner.description.relay_peer and owner.local not in endpoints
            and grant.session.initiator == first and grant.session.responder == last
            and (grant.direction(owner.local) == 0) is sends and grant.format == 'MPF2.record'
            and grant.route_binding_digest == owner.context.digest
            and grant.issued_epoch_us == owner.context.issued_epoch_us
            and grant.expires_epoch_us == grant.application_expires_epoch_us == owner.context.expires_epoch_us
            and type(p['application_deadline_monotonic_us']) is int
            and 0 < p['application_deadline_monotonic_us'] <= min(
                owner.intention.application_deadline_monotonic_us - 1000000,
                int(owner.description.deadlines.application_deadline_monotonic * 1000000))
            and grant.object_bytes <= owner.description.max_packet_bytes
            and ipaddress.ip_address(grant.initiator_address[0]).version
                == ipaddress.ip_address(grant.responder_address[0]).version)
        caps = grant.limits
        child = ChildPlan(slot, kind, sends, grant.digest.hex(), grant.object_sha256.hex(),
            grant.object_bytes, 'tcp', grant.digest.hex(), owner.intention.host_boot_id,
            owner.context.expires_epoch_us, p['application_deadline_monotonic_us'],
            Cost(ip_frames=caps.max_frames, tx_stream_bytes=caps.max_tx_stream_bytes,
                 rx_stream_bytes=caps.max_rx_stream_bytes), caps.max_frames)
        return RelayIpProfile(owner.context.digest, hashlib.sha256(_canonical(owner.intention.to_dict())).digest(),
            owner._approval, raw, grant, child, RelayHop('ip', grant.session, grant.route_binding_digest, grant.digest))
    except Exception:
        raise RelayIpError() from None


class RelayIpAdoption:
    """Fresh signed A scope/enrollment and retained protected original grant."""
    def __init__(self, owner, profile, directory):
        require(type(owner) is ForwardNativeRelay and type(profile) is RelayIpProfile
            and type(owner._store) is WorkflowStore)
        self.owner, self.profile, self.child, self.directory = owner, profile, profile.child, Path(directory)
        self.store = owner._store
        require(self.directory.is_absolute() and '..' not in self.directory.parts
            and self.store.client is self.store.storage.client is owner.resource.probe.client)
        self._refs = (owner, profile, self.child, self.directory, self.store, self.store.client,
                      self.store.storage, owner.resource, owner.authority, owner.native_context)
        self._process = os.getpid(), threading.get_ident()
        self._busy = self._adopted = self._closed = self._uncertain = False
        self._child = None; self._highest = 0; self._record = None; self._last_time = None
        self._files = {}; self._directory_fd = None
        self.record_id = _id(SCHEMA, [asdict(owner.local), owner.intention.operation_id, self.child.slot])
        try:
            self._guard(); self._read_grant()
            self._binding = _canonical(dict(context_digest=owner.context.digest.hex(), relay=asdict(owner.local),
                exchange_approval=owner._approval, child=self.child.to_dict(), hop=hop_value(profile.hop),
                config_revision=owner.config_revision, broker_id=owner.broker_id, storage_epoch=owner.storage_epoch,
                directory=str(self.directory), directory_identity=list(self._directory_identity),
                grant_files=self._file_hashes, grant_file_identities={n:list(v[1]) for n,v in self._files.items()},
                initiator_address=list(profile.grant.initiator_address),
                responder_address=list(profile.grant.responder_address), sources=source_pins()))
            self._binding_ref = self._binding; self._guard()
        except BaseException:
            self.release(); raise

    def _bound(self):
        require(not self._closed and not self._uncertain and self._process == (os.getpid(), threading.get_ident())
            and all(a is b for a,b in zip(self._refs, (self.owner, self.profile, self.child, self.directory,
                self.store, self.store.client, self.store.storage, self.owner.resource, self.owner.authority,
                self.owner.native_context))))
        require(self.profile == plan_ip_hop(self.owner, json.loads(self.profile.plan_bytes), self.profile.grant.secret,
            slot=self.child.slot, kind=self.child.kind, sends=self.child.sends))
        if hasattr(self, '_binding_ref'): require(self._binding is self._binding_ref)

    def _guard(self):
        self._bound(); o = self.owner; h = o.resource
        require(o.access_check() is True and o.authority.authorized(o.native_context, SCOPE, None) is True)
        o._available(); self._bound()
        require(o._position < 5 and self.child.kind == o._position + 1
            and self.child.slot == o._position * 2 + int(self.child.sends)
            and h._provider is None and getattr(h, '_coordinator', None) is None
            and not getattr(h, '_initializing', False) and getattr(h, '_radio_observer', None) is None
            and (getattr(h, '_forward_relay_ip', None) is None or h._forward_relay_ip is self))
        radio = getattr(h, '_forward_radio', None)
        if radio is not None:
            from .forward_native_relay_radio import RelayRadioAdoption
            require(type(radio) is RelayRadioAdoption and radio.owner is o)
        now = o._sample()
        require(now[1] < self.child.application_deadline_monotonic_us
            and (self._last_time is None or all(a >= b for a,b in zip(now,self._last_time))))
        self._last_time = now
        return True

    def _read_grant(self):
        for path in (self.directory, *self.directory.parents):
            info = path.lstat()
            require(path.resolve() == path and stat.S_ISDIR(info.st_mode) and info.st_uid in (0,os.geteuid())
                and (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
        self._directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        self._directory_identity = identity(self._directory_fd); private(os.fstat(self._directory_fd), True)
        self._file_hashes = {}; values = {}
        for name,maximum in (('grant.json',16384),('secret.bin',32),('active',7)):
            self._guard(); fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._directory_fd)
            self._files[name] = (fd,identity(fd)); private(os.fstat(fd))
            raw = os.pread(fd,maximum+1,0); require(0 < len(raw) <= maximum)
            values[name] = raw; self._file_hashes[name] = hashlib.sha256(raw).hexdigest(); self._guard()
        require(values['active'] == b'active\n' and plan_ip_hop(self.owner, json.loads(values['grant.json']),
            values['secret.bin'], slot=self.child.slot, kind=self.child.kind, sends=self.child.sends) == self.profile)
        self._file_refs = tuple(self._files.items()); self._hash_refs = tuple(sorted(self._file_hashes.items()))
        self._directory_ref = self._directory_fd

    def _assets(self):
        require(tuple(self._files.items()) == self._file_refs and tuple(sorted(self._file_hashes.items())) == self._hash_refs
            and self._directory_fd == self._directory_ref and identity(self._directory_fd) == self._directory_identity)
        for path in (self.directory,*self.directory.parents):
            info = path.lstat()
            require(path.resolve() == path and stat.S_ISDIR(info.st_mode) and info.st_uid in (0,os.geteuid())
                and (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
        private(self.directory.lstat(),True)
        require((self.directory.lstat().st_dev,self.directory.lstat().st_ino) == self._directory_identity)
        for name,(fd,expected) in self._files.items():
            require(identity(fd) == expected); private(os.fstat(fd))
            info = os.stat(name,dir_fd=self._directory_fd,follow_symlinks=False); private(info)
            require((info.st_dev,info.st_ino) == expected
                and hashlib.sha256(os.pread(fd,16385,0)).hexdigest() == self._file_hashes[name])
        require(source_pins() == json.loads(self._binding)['sources'])

    def observe(self):
        self._guard(); self._assets()
        record = self.store.get(self.record_id,kind='mesh_outbound',access_check=self._guard)
        self._guard(); value = record['payload']; body = value['body']
        require(set(value) == {'schema','body','signature'} and value['schema'] == SCHEMA)
        signature = base64.b64decode(value['signature'],validate=True)
        require(len(signature) == 64 and base64.b64encode(signature).decode() == value['signature'])
        VerifyKey(bytes.fromhex(self.owner.local.agent_id)).verify(DOMAIN + _canonical(body),signature)
        require(set(body) == {'record_id','revision','binding','state'} and body['record_id'] == record['record_id'] == self.record_id
            and type(body['revision']) is int and body['revision'] == record['revision'] > 0
            and _canonical(body['binding']) == self._binding and body['state'] == 'active')
        raw = _canonical(body)
        require(body['revision'] >= self._highest and (body['revision'] != self._highest or raw == self._record))
        self._highest,self._record = body['revision'],raw
        self._assets(); self._guard(); return True

    def adopt(self, resource):
        require(resource is self.owner.resource and not self._busy and not self._adopted)
        self._busy = True
        try:
            self.observe(); o = self.owner
            require(o._held and o._native_refs[-1].locked() and o._busy and o._active is not None)
            children = [c for c in o._active[1:] if c.plan is self.child]; require(len(children) == 1)
            child = children[0]; child.bound()
            require(type(child) is RelayChild and child.owner is o and child.hop is self.profile.hop
                and type(child.admission) is Admission and child.admission.owner is o._budget
                and child.admission.plan is self.child and not child.admission.bound
                and o._budget._permits.get(id(child.admission)) is child.admission)
            registered = child.registration; o._registry._child(registered)
            require(registered.owner is o and registered.plan is self.child and registered.opening
                and registered.open_thread == threading.get_ident() and not registered.unknown
                and getattr(resource,'_forward_relay_ip',None) is None)
            self._child = registered; resource._forward_relay_ip = self; self._adopted = True
            self.observe(); return self
        except BaseException:
            self._uncertain = True; self.release(); raise
        finally: self._busy = False

    def current(self):
        if self._busy: self._uncertain = True; return False
        self._busy = True
        try:
            require(self._adopted and self.owner.resource._forward_relay_ip is self)
            self.owner._registry._child(self._child)
            return self.observe()
        except Exception:
            self._uncertain = True; return False
        finally: self._busy = False

    def cleanup_allowed(self):
        require(os.getpid() == self._process[0])
        if self._process != (os.getpid(),threading.get_ident()):
            o = self.owner; o._available(cleanup=True)
            require(o._closed and o._drain_pending and not o._draining_busy()
                and self._child is not None and self._child.observing
                and self._child.observe_thread == threading.get_ident())

    def release(self):
        self.cleanup_allowed()
        if self._closed: return not self._uncertain
        ok = True
        for _,(fd,expected) in getattr(self,'_file_refs',tuple(self._files.items())):
            try: require(identity(fd) == expected); os.close(fd)
            except Exception: ok = False
        self._files.clear()
        if self._directory_fd is not None:
            try: require(identity(self._directory_fd) == self._directory_identity); os.close(self._directory_fd)
            except Exception: ok = False
            self._directory_fd = None
        if self._adopted and self.owner.resource._forward_relay_ip is self and ok:
            self.owner.resource._forward_relay_ip = None
        self._closed = True; self._uncertain = self._uncertain or not ok
        return ok


class NativeRelayIpFactory(NativeRelayFactory):
    def __init__(self, owner, profile, directory, *, ready=None):
        require(type(self) is NativeRelayIpFactory and type(owner) is ForwardNativeRelay
            and type(profile) is RelayIpProfile and (ready is None or callable(ready)))
        require(profile == plan_ip_hop(owner,json.loads(profile.plan_bytes),profile.grant.secret,
            slot=profile.child.slot,kind=profile.child.kind,sends=profile.child.sends))
        self.owner,self.profile,self.directory,self._ready = owner,profile,Path(directory),ready
        self._thread = os.getpid(),threading.get_ident()
        self._busy = self._used = self._unknown = self._retired = False
        self._handles = {}; self._child = self._evidence = self._binding = None
        self.adoption = self.authority = self.worker = self.journal = self.store = self.flow = self.transfer = None
        self._sockets = []; self._socket_refs = (); self._listener = self._connected = self._worker_socket = None
        self._authority_fd_refs = self._journal_refs = self._store_ref = self._transport_refs = None
        self._directory_fd = self._directory_identity = None
        self._wire_retired = self._grant_retired = self._store_closed = False
        self._snapshot = self._retirement = None
        self._probe = self._owned; self._clock_ref = owner._clock
        self._refs = (owner,profile,self.directory,ready,self._probe,self._clock_ref)

    # Only the ordinary, independently guarded TCP connection mechanics are
    # reused. Their endpoint owner and permissive callback policy are not.
    _socket = ForwardTcpStage._socket
    _wait = ForwardTcpStage._wait
    _connect = ForwardTcpStage._connect
    _directory_owned = ForwardTcpStage._directory_owned

    def _track_socket(self, value):
        value = ForwardTcpStage._track_socket(self,value)
        self._socket_refs = tuple(self._sockets)
        return value

    def _cleanup_allowed(self):
        require(os.getpid() == self._thread[0])
        if self._thread != (os.getpid(),threading.get_ident()):
            self.owner._available(cleanup=True)
            require(self._child is not None and self._child.registration.observing
                and self._child.registration.observe_thread == threading.get_ident())

    def _bound(self, *, cleanup=False):
        if cleanup: self._cleanup_allowed()
        else: require(self._thread == (os.getpid(),threading.get_ident()))
        require(all(a is b for a,b in zip(self._refs,
            (self.owner,self.profile,self.directory,self._ready,self._probe,self._clock_ref)))
            and tuple(self._sockets) == self._socket_refs)
        require(self.profile == plan_ip_hop(self.owner,json.loads(self.profile.plan_bytes),self.profile.grant.secret,
            slot=self.profile.child.slot,kind=self.profile.child.kind,sends=self.profile.child.sends))
        require(all(getattr(self,n) is value for n,value in self._handles.items()))
        if self._binding is not None: require(source_pins() == json.loads(self._binding)['sources'])
        if self.authority is not None:
            require(type(self.authority) is IpAuthority and _canonical(self.authority.plan) == self.profile.plan_bytes
                and self.authority.grant.digest == self.profile.grant.digest
                and self.authority.grant.secret == self.profile.grant.secret and self.authority.local_peer == self.owner.local
                and self.authority.application_deadline_us == self.profile.child.application_deadline_monotonic_us
                and self.authority.owner_probe is self._probe and self.authority.clock is self._clock_ref)
        if self._transport_refs is not None:
            require(all(a is b for a,b in zip(self._transport_refs,
                (self.authority,self.worker,self.journal,self.store,self.flow,self.transfer)))
                and self.transfer.flow is self.flow and self.flow.worker is self.worker
                and self.worker.journal is self.journal and self.worker.sock is self._worker_socket
                and self.flow.store is self.store and self.flow.cipher.store is self.store
                and self.flow.cipher.grant is self.authority.grant and self.worker.grant is self.authority.grant
                and self.journal.grant is self.authority.grant and self.store.grant is self.authority.grant)

    def _live(self):
        self._bound(); require(not self._unknown and not self._retired and self.adoption.current())
        if self.worker is not None: require(self.worker.owned())
        self._bound(); return True

    def _owned(self):
        try:
            self._live()
            # A read-only service/current check may run outside the lease.
            # Every dispatch lease (including a direct transfer.send/step)
            # additionally needs the original held native relay producer.
            if self.authority is None or self.authority.held:
                require(self.owner._held and self.owner._busy and self.owner._native_refs[-1].locked())
            return self._directory_owned() and (self.worker.owned() if self.worker is not None
                else self.journal is None or self.journal.owned())
        except Exception: return False

    def _setup_live(self):
        require(self.owner._held and self.owner._busy and self.owner._native_refs[-1].locked())
        self._live(); self.authority.require_dispatch()

    def _observed(self, *, cleanup=False):
        return RelayPrepared(self.profile.child,self.profile.hop,self._evidence,
            self.owner.intention.host_boot_id,*self.owner._sample(live=not cleanup))

    def prepared(self, owner, plan, hop):
        self._bound(); require(owner is self.owner and plan is self.profile.child and hop is self.profile.hop
            and not self._used and not self._busy and not self._unknown)
        adoption = RelayIpAdoption(owner,self.profile,self.directory)
        try:
            adoption.observe()
            if self._binding is None:
                self._binding = adoption._binding; self._evidence = hashlib.sha256(self._binding).hexdigest()
            require(self._binding == adoption._binding)
            return self._observed()
        finally: require(adoption.release())

    def open(self, owner, child):
        self._bound(); require(owner is self.owner and type(child) is RelayChild and child.owner is owner
            and child.plan is self.profile.child and child.hop is self.profile.hop and child.factory is self
            and owner._held and owner._busy and not self._busy and not self._used and self._binding is not None)
        self._busy = self._used = True; self._child = child
        try:
            self.adoption = RelayIpAdoption(owner,self.profile,self.directory); self._handles['adoption'] = self.adoption
            require(self.adoption._binding == self._binding)
            owner.resource.adopt_forward_relay_ip(self.adoption)
            self._directory_fd = os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            self._directory_identity = identity(self._directory_fd); private(os.fstat(self._directory_fd),True)
            require(self._directory_identity == self.adoption._directory_identity and not (self.directory/'sender.bin').exists())
            self.authority = IpAuthority(self.directory,local_peer=owner.local,host_boot_id=child.plan.host_boot_id,
                clock=self._clock_ref,owner_probe=self._probe); self._handles['authority'] = self.authority
            self._authority_fd_refs = tuple((n,fd,identity(fd)) for n,fd in self.authority.fds.items())
            self._live()
            self.journal = IpJournal(self.directory/'bytes.sqlite',self.authority.grant,create=True)
            self._handles['journal'] = self.journal
            self._journal_refs = self.journal.db,self.journal.lock_fd,identity(self.journal.lock_fd)
            with self.authority.application_lease(): connected = self._connect()
            self.worker = TcpWorker(connected,self.authority.grant,owner.local,self.journal)
            self._handles['worker'] = self.worker; self._worker_socket = self.worker.sock; self._track_socket(self.worker.sock)
            self.store = SessionStore(self.directory/'wire.sqlite',self.authority.grant,owner.local,
                boot_id=child.plan.host_boot_id,clock=self._clock_ref,
                original_deadline_us=self.authority.application_deadline_us,create=True)
            self._handles['store'] = self.store; self._store_ref = self.store._db
            self.flow = IpFlow(authority=self.authority,worker=self.worker,store=self.store); self._handles['flow'] = self.flow
            self.transfer = ServiceTransfer(self.flow); self._handles['transfer'] = self.transfer
            self._transport_refs = self.authority,self.worker,self.journal,self.store,self.flow,self.transfer
            require(describe_child(self.transfer,slot=child.plan.slot,kind=child.plan.kind) == child.plan)
            self._live(); return self.transfer
        except BaseException:
            self._unknown = True
            # Partial handles remain registered for bounded containment. An
            # uncertain open never refunds either prepaid child maximum.
            raise
        finally: self._busy = False

    def current(self, owner, child):
        require(owner is self.owner and child is self._child and child.transfer is self.transfer)
        self._live(); return self._observed()

    def _cleanup(self, deadline):
        self._cleanup_allowed(); errors = []
        def attempt(callback):
            try: require(time.monotonic() < deadline); callback()
            except BaseException as error: errors.append(type(error).__name__)
        a,w,j,s,f,authority = (self._handles.get(n) for n in ('adoption','worker','journal','store','flow','authority'))
        # A stale socket object must not close a reused foreign descriptor,
        # even after the finite cleanup window has elapsed.
        for sock,expected in self._socket_refs:
            if sock.fileno() >= 0:
                try: require(identity(sock.fileno()) == expected)
                except BaseException as error: sock.detach(); errors.append(type(error).__name__)
        attempt(lambda:self._bound(cleanup=True))
        if f is not None: attempt(f.close)
        for sock,expected in self._socket_refs:
            if sock.fileno() >= 0:
                def close_socket(sock=sock,expected=expected):
                    try: require(identity(sock.fileno()) == expected)
                    except BaseException: sock.detach(); raise
                    sock.close()
                attempt(close_socket)
        if w is not None:
            def close_worker(): require(w.sock is self._worker_socket); w.close()
            attempt(close_worker)
        if j is not None and not j.closed:
            def snapshot():
                db,fd,expected = self._journal_refs
                require(j.db is db and j.lock_fd == fd and identity(fd) == expected)
                self._snapshot = _canonical(j.snapshot()); value = json.loads(self._snapshot)
                require(not value['fenced'] and not value['pending_receive_stream_bytes']
                    and not value['unknown_receive_stream_bytes'] and set(value['outcomes']) <= {'KERNEL_ACCEPTED','NOT_SENT'})
            attempt(snapshot)
        if s is not None and not self._store_closed:
            def retire_store(): require(s._db is self._store_ref); s.retire(); self._wire_retired = True
            def close_store(): require(s._db is self._store_ref); s.close(); self._store_closed = True
            attempt(retire_store); attempt(close_store)
        if j is not None and not j.closed: attempt(j.close)
        if authority is not None and not authority.closed:
            def retire_grant():
                require(self._directory_owned() and not authority.held)
                fd = os.open('active',os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=self._directory_fd)
                try:
                    private(os.fstat(fd)); require(identity(fd) == dict((n,v) for n,_,v in self._authority_fd_refs)['active'])
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    os.ftruncate(fd,0); os.write(fd,b'retired\n'); os.fsync(fd); self._grant_retired = True
                finally: os.close(fd)
            attempt(retire_grant)
            def close_authority():
                expected = {n:(fd,v) for n,fd,v in self._authority_fd_refs}; extra = []
                for n,fd in list(authority.fds.items()):
                    if n not in expected or fd != expected[n][0]:
                        authority.fds.pop(n); errors.append('authority_mapping_changed')
                for n,(fd,v) in expected.items():
                    if released(fd,v): authority.fds.pop(n,None); errors.append('authority_fd_changed')
                    elif authority.fds.get(n) != fd: extra.append((fd,v)); errors.append('authority_mapping_changed')
                require(not authority.held); authority.close()
                for fd,v in extra: require(identity(fd) == v); os.close(fd)
            attempt(close_authority)
        if self._directory_fd is not None:
            def close_directory():
                require(identity(self._directory_fd) == self._directory_identity)
                os.close(self._directory_fd); self._directory_fd = None
            attempt(close_directory)
        if a is not None: attempt(lambda:require(a.release()))
        self._unknown = self._unknown or bool(errors); self._retired = not errors
        if self._retired and self._retirement is None:
            self._retirement = _canonical(dict(evidence=self._evidence,journal_sha256=
                hashlib.sha256(self._snapshot).hexdigest() if self._snapshot is not None else None,
                sockets=[list(v) for _,v in self._socket_refs],grant_retired=self._grant_retired,wire_retired=self._wire_retired))
        return self._retired and not self._unknown

    def retire(self, owner, child, deadline):
        self._bound(); require(owner is self.owner and child is self._child and owner._held
            and not self._busy and self.flow.flow.complete and not self.flow.flow.failure)
        if not child.plan.sends:
            while True:
                require(time.monotonic() < deadline); self._live()
                if self.flow.finish_receive_ack(): break
                time.sleep(.001)
        require(self._cleanup(deadline))

    def contain(self, owner, child, deadline):
        self._cleanup_allowed(); require(owner is self.owner and child is self._child
            and owner._closed and not owner._draining_busy() and not self._busy)
        require(self._cleanup(deadline))

    def terminal(self, owner, child):
        self._bound(cleanup=True); require(owner is self.owner and child is self._child and not self._busy
            and self._retired and not self._unknown and self._retirement is not None and self._snapshot is not None
            and self._grant_retired and self._wire_retired and self._store_closed and self.flow.closed
            and self.authority.closed and self.adoption._closed and self.worker.closed and self.journal.closed
            and self.journal.lock_fd is None and self._directory_fd is None
            and all(sock.fileno() == -1 for sock,_ in self._socket_refs))
        for db in (self._journal_refs[0],self._store_ref):
            try: db.execute('SELECT 1')
            except sqlite3.ProgrammingError: pass
            else: raise RelayIpError()
        require(all(released(fd,v) for _,fd,v in self._authority_fd_refs)
            and released(self._journal_refs[1],self._journal_refs[2]))
        # Re-observe the same private directory/tombstone, never a live grant.
        fd = os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            private(os.fstat(fd),True); require(identity(fd) == self._directory_identity)
            active = os.open('active',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            try:
                private(os.fstat(active)); require(identity(active) == dict((n,v) for n,_,v in self._authority_fd_refs)['active']
                    and os.pread(active,16,0) == b'retired\n')
            finally: os.close(active)
        finally: os.close(fd)
        return RelayTerminal(self._observed(cleanup=True),json.loads(self._snapshot),
            hashlib.sha256(self._snapshot).hexdigest(),hashlib.sha256(self._retirement).hexdigest(),True,False,None)
