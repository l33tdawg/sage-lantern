# SPDX-License-Identifier: Apache-2.0
"""One in-process, explicitly approved MPF2 TCP stage.

Planning opens nothing. The native owner prepays the complete enforced caps and
registers cleanup before this factory adopts an existing protected grant and
opens a socket. No public node, radio, SDK client, enrollment, key export or grant
file writer is used. A native admission observer is mandatory; this component
does not establish that observer's production provenance or enable private mode.

Endpoints are canonical numeric addresses, independently approved by root.
Received application bytes stay in the existing receive-commit journal path.
Kernel acceptance, authenticated flow ACK, capture and inbox admission remain
different outcomes. Unknown byte debt and all grant/parent tombstones survive
cleanup. Physical/native provisioning qualification remains external.
"""

from dataclasses import dataclass, field, asdict
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import select
import socket
import stat
import threading
import time

from meshticulum.bench.services import ServiceTransfer
from meshticulum.contracts.identity import PeerID
from meshticulum.ip.authority import IpAuthority, grant_from_plan
from meshticulum.ip.flow import IpFlow
from meshticulum.ip.journal import IpJournal, private
from meshticulum.ip.state import SessionStore
from meshticulum.ip.worker import TcpWorker
from meshticulum.ip.wire import IpGrant
from meshticulum.privacy.forward_session import H1, H2, CONFIRM_I, CONFIRM_R, DATA

from .forward_budget import ChildPlan, Cost
from .forward_native_owner import ForwardNativeOwner
from .forward_operation import describe_child
from .mesh_inbox import _canonical

MODE = 'meshticulum.forward-private.tcp.v1'
PROFILE_DOMAIN = b'sage.lantern.forward-tcp-profile.v1\0'
KINDS = (H1, H2, CONFIRM_I, CONFIRM_R, DATA)
MIN_CLOCK_MARGIN_US = 1000000


class ForwardTcpError(RuntimeError):
    def __init__(self):
        super().__init__('forward_tcp_unavailable')


def require(value):
    if not value:
        raise ForwardTcpError()


@dataclass(frozen=True, slots=True, repr=False)
class ForwardTcpProfile:
    """Immutable description, never a bearer permission or connection handle."""
    context_digest: bytes
    intention_digest: bytes
    local_initiator: bool
    approval_binding: str
    local_peer: PeerID
    grant: IpGrant = field(repr=False)
    plan_bytes: bytes = field(repr=False)
    child: ChildPlan

    @property
    def digest(self):
        # IpGrant.digest does not include its secret. Root approval must also
        # bind this independently provisioned secret without disclosing it.
        value = dict(mode=MODE, context=self.context_digest.hex(),
            intention=self.intention_digest.hex(), local_initiator=self.local_initiator,
            approval=self.approval_binding, child=self.child.to_dict(),
            secret_sha256=hashlib.sha256(self.grant.secret).hexdigest())
        return hashlib.sha256(PROFILE_DOMAIN + _canonical(value)).digest()

    def __repr__(self):
        return '<ForwardTcpProfile>'


def plan_stage(owner, native_plan, secret, *, slot, kind):
    """Pure validation and full-cap ChildPlan, before any I/O or state creation.

    The caller supplies an independently approved grant plan/secret. Each wire
    sender is the IP session initiator, including reverse MPF2 stages. A child
    must declare a child deadline at least one second below the original, so
    ordinary separate host clock observations do not silently change its plan.
    This margin is not a clock-provenance proof: further native tightening still
    refuses binding, preserving prepaid debt. Epoch expiry is unchanged. Grant
    setup and endpoint approval remain native responsibilities.
    """
    require(type(owner) is ForwardNativeOwner and type(native_plan) is dict
            and type(slot) is int and 0 <= slot < 5 and kind == KINDS[slot])
    try:
        raw = _canonical(native_plan)
        p = json.loads(raw)
        local = PeerID(owner.local.chain_id, owner.local.public_key.hex())
        grant = grant_from_plan(p, secret, local_peer=local,
                                host_boot_id=owner.intention.host_boot_id)
        first = owner.context.initiator if kind % 2 else owner.context.responder
        second = owner.context.responder if kind % 2 else owner.context.initiator
        sends = (kind % 2 == 1) == owner.local_initiator
        require(grant.format == 'MPF2.record'
            and grant.session.initiator == PeerID(first.chain_id, first.public_key.hex())
            and grant.session.responder == PeerID(second.chain_id, second.public_key.hex())
            and (grant.direction(local) == 0) == sends
            and grant.route_binding_digest == owner.context.digest
            and grant.issued_epoch_us == owner.context.issued_epoch_us
            and grant.expires_epoch_us == grant.application_expires_epoch_us
                == owner.context.expires_epoch_us
            and type(p['application_deadline_monotonic_us']) is int
            and 0 < p['application_deadline_monotonic_us']
                <= owner.intention.application_deadline_monotonic_us - MIN_CLOCK_MARGIN_US
            and ipaddress.ip_address(grant.initiator_address[0]).version
                == ipaddress.ip_address(grant.responder_address[0]).version)
        caps = grant.limits
        deadline = p['application_deadline_monotonic_us']
        child = ChildPlan(slot, kind, sends, grant.digest.hex(), grant.object_sha256.hex(),
            grant.object_bytes, 'tcp', grant.digest.hex(), owner.intention.host_boot_id,
            owner.context.expires_epoch_us, deadline,
            Cost(ip_frames=caps.max_frames, tx_stream_bytes=caps.max_tx_stream_bytes,
                 rx_stream_bytes=caps.max_rx_stream_bytes), caps.max_frames)
        return ForwardTcpProfile(owner.context.digest,
            hashlib.sha256(_canonical(owner.intention.to_dict())).digest(),
            owner.local_initiator, owner._approval, local, grant, raw, child)
    except Exception:
        raise ForwardTcpError() from None


class ForwardTcpStage:
    """One-use tracked factory, owning only this stage's TCP/native handles.

    grant_directory must already contain exact root-approved grant.json,
    secret.bin and active files. The factory creates only create-once byte and
    wire journals after parent admission. native_admission(profile) must observe
    actual endpoint/grant authorization, independent of a held native lease;
    it is called before and after callback boundaries and dispatch. It has no
    permissive default. ready(profile) optionally reports a bound listener for
    independently coordinated peers; it may carry metadata only, never payload.
    """
    def __init__(self, owner, profile, grant_directory, *, native_admission=None, ready=None):
        require(type(owner) is ForwardNativeOwner and type(profile) is ForwardTcpProfile
                and callable(native_admission) and (ready is None or callable(ready)))
        require(profile == plan_stage(owner, json.loads(profile.plan_bytes), profile.grant.secret,
                                      slot=profile.child.slot, kind=profile.child.kind))
        directory = Path(grant_directory)
        require(directory.is_absolute() and '..' not in directory.parts)
        self.owner, self.profile, self.directory = owner, profile, directory
        self._approval, self._ready = native_admission, ready
        self._refs = (owner, profile, directory, native_admission, ready)
        self._owner = (os.getpid(), threading.get_ident())
        self._busy = self._used = self._unknown = self._retired = False
        self._directory_fd = self._directory_identity = None
        self._sockets = []
        self._listener = self._connected = None
        self.authority = self.journal = self.store = self.worker = self.flow = self.transfer = None
        self.runner = None
        self._transport_refs = None
        self._handles = {}
        self._worker_socket = None
        self._snapshot = None
        self._wire_retired = self._store_closed = False
        self._last_child_clock = None
        self._authority_fd_refs = None
        self._stage_observation = None

    def __repr__(self):
        return '<ForwardTcpStage>'

    def _bound(self):
        require(os.getpid() == self._owner[0]
            and all(a is b for a, b in zip(self._refs,
                (self.owner, self.profile, self.directory, self._approval, self._ready))))
        p = self.profile
        require(p.context_digest == self.owner.context.digest
            and p.intention_digest == hashlib.sha256(_canonical(self.owner.intention.to_dict())).digest()
            and p.local_initiator is self.owner.local_initiator
            and p.approval_binding == self.owner._approval)
        require(all(getattr(self, name) is value for name, value in self._handles.items()))
        if self.authority is not None:
            require(self.authority.grant.digest == p.grant.digest
                and self.authority.grant.secret == p.grant.secret
                and self.authority.local_peer == p.local_peer
                and _canonical(self.authority.plan) == p.plan_bytes)
        if self._transport_refs is not None:
            require(all(a is b for a, b in zip(self._transport_refs,
                (self.authority, self.journal, self.store, self.worker, self.flow, self.transfer)))
                and self.worker.grant is self.authority.grant
                and self.worker.journal is self.journal and self.flow.worker is self.worker
                and self.worker.sock is self._worker_socket
                and self.journal.grant is self.authority.grant
                and self.store.grant is self.authority.grant
                and self.flow.cipher.grant is self.authority.grant
                and self.flow.authority is self.authority and self.flow.store is self.store
                and self.transfer.flow is self.flow and self.transfer.authority is self.authority)

    def _live(self):
        if self._stage_observation is not None:
            self._unknown = True
            raise ForwardTcpError()
        self._bound()
        require(not self._unknown and not self._retired
                and self._owner == (os.getpid(), threading.get_ident()))
        self.owner.phase_check(self.owner.context, self.profile.child.kind, self.profile.child.sends)
        self._child_time()
        require(self._approval(self.profile) is True)
        self._bound()
        require(not self._unknown and not self._retired)
        self.owner._available()
        self._child_time()

    def _child_time(self):
        now = self._clock()
        self._bound(); self.owner._available()
        require(not self._unknown and type(now) is tuple and len(now) == 2
            and all(type(v) is int and 0 <= v < 2**63 for v in now)
            and self.profile.grant.issued_epoch_us <= now[0] < self.profile.grant.application_expires_epoch_us
            and now[1] < self.profile.child.application_deadline_monotonic_us
            and (self._last_child_clock is None or all(a >= b for a, b in zip(now, self._last_child_clock))))
        self._last_child_clock = now

    def _setup_live(self):
        self._live()
        # This requires the already-held IP lease and checks its current files
        # and deadlines. _owned does not call authority.live: no recursion.
        self.authority.require_dispatch()

    def _directory_owned(self):
        if self._directory_fd is None:
            return False
        held = os.fstat(self._directory_fd)
        named = self.directory.lstat()
        private(held, True); private(named, True)
        return ((held.st_dev, held.st_ino) == self._directory_identity
                == (named.st_dev, named.st_ino))

    def _owned(self):
        try:
            self._live()
            return (self._directory_owned()
                and (self.worker.owned() if self.worker is not None
                     else self.journal is None or self.journal.owned()))
        except Exception:
            return False

    def _clock(self):
        # Independent native clock, without recursively entering an IP lease.
        return self.owner._clock()

    def _track_socket(self, value):
        try:
            info = os.fstat(value.fileno())
        except BaseException:
            value.close()
            raise
        self._sockets.append((value, (info.st_dev, info.st_ino)))
        return value

    def _socket(self):
        family = socket.AF_INET if ipaddress.ip_address(self.profile.grant.initiator_address[0]).version == 4 else socket.AF_INET6
        value = self._track_socket(socket.socket(family, socket.SOCK_STREAM))
        value.setblocking(False)
        return value

    def _wait(self, value, *, writing=False):
        self._setup_live()
        left = self.profile.child.application_deadline_monotonic_us / 1000000 - time.monotonic()
        require(left > 0)
        select.select([] if writing else [value], [value] if writing else [], [], min(left, .01))
        self._setup_live()

    def _connect(self):
        grant = self.profile.grant
        if self.profile.child.sends:
            value = self._socket(); self._connected = value
            self._setup_live()
            value.bind(grant.initiator_address)
            self._setup_live()
            result = value.connect_ex(grant.responder_address)
            self._setup_live()
            require(result in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY))
            while result:
                self._wait(value, writing=True)
                result = value.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                require(result in (0, errno.EINPROGRESS, errno.EALREADY))
            return value
        listener = self._socket(); self._listener = listener
        self._setup_live(); listener.bind(grant.responder_address)
        self._setup_live(); listener.listen(1)
        self._setup_live()
        if self._ready is not None:
            require(self._ready(self.profile) is True); self._setup_live()
        while True:
            self._wait(listener)
            try:
                self._setup_live()
                value, peer = listener.accept()
            except BlockingIOError:
                continue
            value = self._track_socket(value); self._connected = value
            value.setblocking(False)
            require(peer[:2] == grant.initiator_address)
            listener.close(); self._setup_live()
            return value

    def _open(self, child):
        require(child is self.profile.child and self._busy)
        with self.owner.owner_lease():
            return self._open_held(child)

    def _open_held(self, child):
        self._live()
        for ancestor in (self.directory, *self.directory.parents):
            info = ancestor.lstat()
            require(ancestor.resolve() == ancestor and stat.S_ISDIR(info.st_mode)
                and info.st_uid in (0, os.geteuid())
                and (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
        self._directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(self._directory_fd); private(info, True)
        self._directory_identity = info.st_dev, info.st_ino
        require(not (self.directory / 'sender.bin').exists())
        self.authority = IpAuthority(self.directory, local_peer=self.profile.local_peer,
            host_boot_id=child.host_boot_id, clock=self._clock, owner_probe=self._owned)
        self._handles['authority'] = self.authority
        self._authority_fd_refs = tuple((name, fd, self.authority.file_ids[name])
                                       for name, fd in self.authority.fds.items())
        require(_canonical(self.authority.plan) == self.profile.plan_bytes
                and self.authority.grant.secret == self.profile.grant.secret
                and self.authority.application_deadline_us
                    == json.loads(self.profile.plan_bytes)['application_deadline_monotonic_us'])
        self.journal = IpJournal(self.directory / 'bytes.sqlite', self.authority.grant, create=True)
        self._handles['journal'] = self.journal
        with self.authority.application_lease():
            connected = self._connect()
        self.worker = TcpWorker(connected, self.authority.grant, self.profile.local_peer, self.journal)
        self._handles['worker'] = self.worker
        self._worker_socket = self.worker.sock
        self._track_socket(self.worker.sock)
        self.store = SessionStore(self.directory / 'wire.sqlite', self.authority.grant,
            self.profile.local_peer, boot_id=child.host_boot_id, clock=self._clock,
            original_deadline_us=self.authority.application_deadline_us, create=True)
        self._handles['store'] = self.store
        self.flow = IpFlow(authority=self.authority, worker=self.worker, store=self.store)
        self._handles['flow'] = self.flow
        self.transfer = ServiceTransfer(self.flow)
        self._handles['transfer'] = self.transfer
        self._transport_refs = (self.authority, self.journal, self.store, self.worker, self.flow, self.transfer)
        require(describe_child(self.transfer, slot=child.slot, kind=child.kind) == child)
        self._live()
        return self.transfer

    def prepare(self, *, payload=None):
        if self._busy:
            self._unknown = True
            raise ForwardTcpError()
        self._live(); require(not self._used)
        self._busy = self._used = True
        try:
            self.runner = self.owner.prepare_stage(self.profile.child, open_child=self._open,
                native_check=self._native, retire_child=self._retire,
                contain_child=self._contain, terminal_check=self._terminal, payload=payload)
            self._live(); return self.runner
        except BaseException:
            self._unknown = True
            self._cleanup(time.monotonic() + 1)
            raise
        finally:
            self._busy = False

    def _native(self, context, child, transfer):
        if self._stage_observation is not None:
            self._unknown = True
            raise ForwardTcpError()
        self._bound()
        require(context == self.owner.context and child is self.profile.child and transfer is self.transfer)
        if self._retired:
            require(self._terminal(child, None))
            self.owner.phase_check(context, child.kind, child.sends)
            require(self._approval(self.profile) is True)
            self._bound(); self.owner._available()
        else:
            self._live(); require(self._owned())
        return True

    @contextmanager
    def _observe_stage_read(self, token):
        """Contain cleanup during one pinned provider metadata interval."""
        from .forward_operation import _StageRead
        from .forward_native_provider import _STAGE_NATIVE_METHODS
        if self._stage_observation is not None:
            self._unknown=True;raise ForwardTcpError()
        require(type(token) is _StageRead and token.owner is self.owner
            and token.runner is self.runner and token.plan is self.profile.child
            and not token._active and not token._finished and not token._failed
            and self.runner._executing and self.owner._operation.active is self.runner)
        entry=next(e for e in _STAGE_NATIVE_METHODS if e[0] is type(self))
        require(type(self)._observe_stage_read is entry[3] and entry[3].__code__ is entry[4]
            and '_observe_stage_read' not in self.__dict__)
        registry=self.owner._registry;condition=registry._condition
        children=[c for c in registry._children if c.owner is self.owner and c.runner is self.runner]
        require(len(children)==1);child=children[0];acquired=False
        try:
            self._bound();registry._paid_bound(child)
            with condition:
                require(self._stage_observation is None and not child.observing
                    and not registry._closing and not registry._uncertain and not self._unknown)
                self._stage_observation=token;child.observing=True
                child.observe_thread=threading.get_ident();acquired=True
            yield
            require(self._stage_observation is token and self.owner._registry is registry
                and registry._condition is condition and child.observing
                and child.observe_thread==threading.get_ident() and not self._unknown
                and not registry._closing and not registry._uncertain)
            self._bound();registry._paid_bound(child)
        except BaseException:
            self._unknown=True
            if acquired:child.unknown=True
            raise
        finally:
            if acquired:
                with condition:
                    if self._stage_observation is token:self._stage_observation=None
                    if child.observing and child.observe_thread==threading.get_ident():
                        child.observing=False;child.observe_thread=None
                    condition.notify_all()

    def _cleanup(self, deadline):
        if self._stage_observation is not None:
            self._unknown = True
            return False
        errors = []
        # Dispose the actual retained handles even if a callback replaced a
        # public field. Never close the replacement or silently restore binding.
        authority = self._handles.get('authority')
        journal = self._handles.get('journal')
        store = self._handles.get('store')
        worker = self._handles.get('worker')
        flow = self._handles.get('flow')
        # Sanitize stale references even when the cleanup deadline has already
        # expired. Python socket finalization must never close a reused FD.
        for value, identity in self._sockets:
            if value.fileno() >= 0:
                try:
                    info = os.fstat(value.fileno())
                    require((info.st_dev, info.st_ino) == identity)
                except BaseException as error:
                    value.detach(); errors.append(type(error).__name__)
        extra_authority_fds = []
        if authority is not None and not authority.closed:
            expected = {name: (fd, identity) for name, fd, identity in self._authority_fd_refs}
            if set(authority.fds) != set(expected):
                errors.append('ForwardTcpError')
            for name, fd in list(authority.fds.items()):
                if name not in expected or fd != expected[name][0]:
                    authority.fds.pop(name, None); errors.append('ForwardTcpError')
            for name, (fd, identity) in expected.items():
                try:
                    info = os.fstat(fd)
                    require((info.st_dev, info.st_ino) == identity)
                except BaseException as error:
                    authority.fds.pop(name, None); errors.append(type(error).__name__)
                else:
                    if authority.fds.get(name) != fd:
                        # Retained original remains ours; close it separately,
                        # without treating a removed binding as restored.
                        extra_authority_fds.append((fd, identity))
        def attempt(action):
            try:
                require(time.monotonic() < deadline); action()
            except BaseException as error:
                errors.append(type(error).__name__)
        attempt(self._bound)
        if flow is not None:
            attempt(flow.close)
        for value, identity in self._sockets:
            if value.fileno() >= 0:
                def close_socket(value=value, identity=identity):
                    try:
                        info = os.fstat(value.fileno())
                        require((info.st_dev, info.st_ino) == identity)
                    except BaseException:
                        # A stale socket object must not close a foreign FD on
                        # worker.close or later garbage collection.
                        value.detach()
                        raise
                    value.close()
                attempt(close_socket)
        if worker is not None:
            def close_worker():
                require(worker.sock is self._worker_socket)
                worker.close()
            attempt(close_worker)
        if journal is not None and not journal.closed:
            def snapshot():
                self._snapshot = _canonical(journal.snapshot())
                value = json.loads(self._snapshot)
                require(not value['pending_receive_stream_bytes']
                        and not value['outcomes'].get('RESERVED', 0))
                if value['fenced'] or value['unknown_receive_stream_bytes'] or value['outcomes'].get('UNKNOWN', 0):
                    self._unknown = True
            attempt(snapshot)
        if store is not None and not self._wire_retired:
            def retire_wire():
                store.retire(); self._wire_retired = True
            attempt(retire_wire)
        if store is not None and not self._store_closed:
            def close_store():
                store.close(); self._store_closed = True
            attempt(close_store)
        if journal is not None and not journal.closed:
            attempt(journal.close)
        if authority is not None and not authority.closed:
            def retire_authority():
                require(self._directory_owned() and not authority.held)
                fd = os.open('active', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=self._directory_fd)
                try:
                    info = os.fstat(fd); private(info)
                    require((info.st_dev, info.st_ino) == authority.file_ids['active'])
                    # Never block drain behind a foreign/shared revocation holder.
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    os.ftruncate(fd, 0); os.write(fd, b'retired\n'); os.fsync(fd)
                finally:
                    os.close(fd)
            attempt(retire_authority)
            def close_authority():
                # Snapshot/retirement work happened after early sanitization.
                # Revalidate immediately before Core's numeric-FD close.
                expected = {name: (fd, identity) for name, fd, identity in self._authority_fd_refs}
                for name, fd in list(authority.fds.items()):
                    try:
                        original, identity = expected[name]
                        info = os.fstat(fd)
                        require(fd == original and (info.st_dev, info.st_ino) == identity)
                    except BaseException as error:
                        authority.fds.pop(name, None); errors.append(type(error).__name__)
                authority.close()
            attempt(close_authority)
        for fd, identity in extra_authority_fds:
            def close_original(fd=fd, identity=identity):
                info = os.fstat(fd); require((info.st_dev, info.st_ino) == identity)
                os.close(fd)
            attempt(close_original)
        if self._directory_fd is not None:
            descriptor, identity = self._directory_fd, self._directory_identity
            def close_directory():
                info = os.fstat(descriptor)
                require((info.st_dev, info.st_ino) == identity)
                os.close(descriptor); self._directory_fd = None
            attempt(close_directory)
        if errors:
            self._unknown = True
        self._retired = not errors
        return self._retired and not self._unknown

    def _retire(self, transfer):
        self._bound(); require(transfer is self.transfer and self.flow.flow.complete)
        return self._cleanup(min(time.monotonic() + 1,
            self.profile.child.application_deadline_monotonic_us / 1000000))

    def _contain(self, child, runner, deadline):
        self._bound(); require(child is self.profile.child and not self._busy
                              and (runner is None or runner.transfer is self.transfer))
        return self._cleanup(deadline)

    def _terminal(self, child, runner):
        self._bound()
        return (child is self.profile.child and self._retired and not self._unknown and not self._busy
            and self._directory_fd is None and all(value.fileno() == -1 for value, _ in self._sockets)
            and (self.worker is None or self.worker.closed)
            and (self.journal is None or self.journal.closed)
            and (self.store is None or self._wire_retired and self._store_closed)
            and (self.authority is None or self.authority.closed))

    def report(self):
        """Metadata only; byte reservations are not delivery or link capacity."""
        self._bound()
        return dict(mode=MODE, profile_sha256=self.profile.digest.hex(),
            child=self.profile.child.to_dict(), retired=self._retired, uncertain=self._unknown,
            byte_journal=json.loads(self._snapshot) if self._snapshot is not None else None,
            measured_airtime_us=None, native_provisioning_qualified=False)
