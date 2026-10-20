# SPDX-License-Identifier: Apache-2.0
"""Experimental protected MPF2 counters and one recoverable receive intention.

One recipient-signed WorkflowStore CAS record owns the retired session/role
claim, transmit reservations, confirmation replay and one DATA capsule. No
separate replay database or capture RPC follows DATA acceptance. This adapter
opens no device/key/backend, supplies no native authority, persists no session
keys and does not admit the inner envelope, project it, or issue a receipt.

The native owner supplies the exclusive lease and current permission callbacks.
Guards must support observations both inside the lease and after its exit; lease
ownership and current read/write permission are independent checks.
A failed/unknown operation fences this incarnation; a fresh authorized owner can
read the signed original capsule but cannot reclaim or resume its session. The
bounded record is retained, including its claim, even after the session closes.
"""
import base64
from dataclasses import dataclass, field
import hashlib
import os
import threading

from nacl.signing import SigningKey
from meshticulum.privacy.forward_session import (
    COUNTER_CLAIM_DOMAIN, DOMAIN as FORWARD_DOMAIN, ForwardContext, ReceiveCandidate)
from meshticulum.privacy.mesh_binary_envelope import MAX_BINARY_BYTES, to_binary, to_text
from lantern_meshticulum.sage.mesh_binary_capture import _Lease, MAX_TIME_US
from lantern_meshticulum.sage.mesh_inbox import _canonical, _id, _uuid
from lantern_meshticulum.sage.workflow_store import WorkflowNotFound, WorkflowStore
import json

SCHEMA = 'sage.lantern.forward-receive-journal.v1'
SIGNATURE_DOMAIN = b'sage.lantern.forward-receive-journal.v1/signature\0'
# This identity must survive later schema/wire revisions. Context, operation and
# mode are intentionally absent so changing them cannot evade a retired claim.
CLAIM_NAMESPACE = 'sage.lantern.forward-private-session-claim.v1'


class ForwardJournalError(RuntimeError):
    def __init__(self):
        super().__init__('forward_receive_journal_unavailable')


def _require(value):
    if not value:
        raise ForwardJournalError()


def _digest(value):
    _require(type(value) is str and len(value) == 64
             and all(c in '0123456789abcdef' for c in value))
    return value


def _integer(value, low=0, high=MAX_TIME_US):
    _require(type(value) is int and low <= value <= high)
    return value


@dataclass(frozen=True)
class ReceiveIntention:
    """Native-owner input, not bearer authority; persisted before flow dispatch."""
    operation_id: str
    packet_sha256: str
    packet_bytes: int
    authority_digest: str
    host_boot_id: str
    application_expires_epoch_us: int
    application_deadline_monotonic_us: int

    def __post_init__(self):
        _uuid(self.operation_id); _uuid(self.host_boot_id)
        _digest(self.packet_sha256); _digest(self.authority_digest)
        _integer(self.packet_bytes, 52, MAX_BINARY_BYTES)
        _integer(self.application_expires_epoch_us, 1)
        _integer(self.application_deadline_monotonic_us, 1)

    def to_dict(self):
        return dict(operation_id=self.operation_id, packet_sha256=self.packet_sha256,
            packet_bytes=self.packet_bytes, authority_digest=self.authority_digest,
            host_boot_id=self.host_boot_id,
            application_expires_epoch_us=self.application_expires_epoch_us,
            application_deadline_monotonic_us=self.application_deadline_monotonic_us)


@dataclass(frozen=True)
class ReceiveRecovery:
    """Verified signed quarantine, never inner-envelope admission or permission."""
    journal_id: str
    revision: int
    intention: ReceiveIntention
    context: ForwardContext
    local_initiator: bool
    counter: int
    handshake_hash: str
    outer_sha256: str
    prepared_epoch_us: int
    prepared_monotonic_us: int
    observed_epoch_us: int
    observed_monotonic_us: int
    sealed_record: bytes = field(repr=False)


class ForwardReceiveJournal:
    """One original expected DATA record, plus this session's confirmation.

    Claim is create-only. record_receive accepts only confirmation counter 1;
    DATA counter 2 requires commit_receive. More DATA requires a separately
    prepared original intention/session, never automatic rotation or renewal.
    The caller cannot resume crypto with a reconstructed counter provider.
    """
    def __init__(self, store, *, context, local_initiator, local_signing_key,
                 intention, boot_id, owner_lease, access_check, storage_check,
                 peer_check, permission_check, clock, workflow_access_check=None,
                 journal_action_check=None):
        _require(type(store) is WorkflowStore and type(context) is ForwardContext
                 and type(local_initiator) is bool and type(intention) is ReceiveIntention
                 and isinstance(local_signing_key, SigningKey)
                 and all(callable(c) for c in (owner_lease, access_check, storage_check,
                                               peer_check, permission_check, clock))
                 and (workflow_access_check is None or callable(workflow_access_check))
                 and (journal_action_check is None or callable(journal_action_check)))
        _uuid(boot_id)
        local = context.initiator if local_initiator else context.responder
        remote = context.responder if local_initiator else context.initiator
        _require(bytes(local_signing_key.verify_key) == local.public_key
                 and store.storage.actor.chain_id == local.chain_id
                 and store.storage.actor.agent_id == local.public_key.hex()
                 and intention.application_expires_epoch_us == context.expires_epoch_us
                 and intention.authority_digest == context.binding_digest.hex())
        self.store, self.context, self.local_initiator = store, context, local_initiator
        self.local, self.remote, self.intention = local, remote, intention
        self._key, self.boot_id = local_signing_key, boot_id
        self.binding = hashlib.sha256(COUNTER_CLAIM_DOMAIN + context.session_id
                                      + bytes([local_initiator])).digest()
        self.record_id = _id(CLAIM_NAMESPACE, [self.binding.hex()])
        self._context_value = json.loads(context.prologue[len(FORWARD_DOMAIN):])
        self._lease, self._access, self._storage = owner_lease, access_check, storage_check
        self._peer, self._permission, self._clock = peer_check, permission_check, clock
        self._workflow_access = self._workflow_access_ref = workflow_access_check
        self._journal_action = self._journal_action_ref = journal_action_check
        self._action_refs = (store, store.storage, store.client, context, intention, local, remote,
            local_signing_key, owner_lease, access_check, storage_check, peer_check, permission_check,
            clock, workflow_access_check)
        self._action_values = (context.digest, _canonical(intention.to_dict()), local_initiator,
            boot_id, self.binding, self.record_id, _canonical(self._context_value))
        self._guarding = False
        self._action_run = None
        self._workflow_epoch = self._workflow_run = None
        self._owner = (os.getpid(), threading.get_ident())
        self._busy = self._closed = self._uncertain = self._claimed = False
        self._epoch = None
        self._fresh = True
        self._last_clock = None
        self._highest_revision = 0
        self._highest_body = None

    def __repr__(self):
        return '<ForwardReceiveJournal>'

    def _available(self):
        _require(not self._closed and not self._uncertain
                 and self._owner == (os.getpid(), threading.get_ident())
                 and self._workflow_access is self._workflow_access_ref
                 and self._journal_action is self._journal_action_ref)
        if self._journal_action_ref is not None:
            self._action_bindings()
            if self._action_run is not None:
                _require(self._busy is True and self._fresh is self._action_run)

    def _action_bindings(self):
        _require(all(a is b for a,b in zip(self._action_refs,
            (self.store, self.store.storage, self.store.client, self.context, self.intention,
             self.local, self.remote, self._key, self._lease, self._access, self._storage, self._peer,
             self._permission, self._clock, self._workflow_access)))
            and type(self.local_initiator) is bool
            and self._action_values == (self.context.digest, _canonical(self.intention.to_dict()),
                self.local_initiator, self.boot_id, self.binding, self.record_id, _canonical(self._context_value))
            and bytes(self._key.verify_key) == self.local.public_key
            and self.store.storage.actor.chain_id == self.local.chain_id
            and self.store.storage.actor.agent_id == self.local.public_key.hex())

    def _guard_action(self):
        """One fresh observation at this original guard, never a reusable grant."""
        if self._guarding:
            self._uncertain = True
            raise ForwardJournalError()
        self._guarding = True
        try:
            self._available()
            _require(self._busy is True and self._fresh is self._action_run is True)
            original = (self._workflow_epoch, self._workflow_run, self._claimed, self._epoch,
                        self._highest_revision, self._highest_body, self._last_clock)
            self._checked(self._journal_action, self, self.context, self.intention, self.remote,
                          self.local_initiator)
            _require(self._busy is True and self._fresh is self._action_run is True
                and all(a is b for a,b in zip(original,
                    (self._workflow_epoch, self._workflow_run, self._claimed, self._epoch,
                     self._highest_revision, self._highest_body, self._last_clock))))
        except BaseException:
            self._uncertain = True
            raise
        finally:
            self._guarding = False

    def _checked(self, callback, *args):
        self._available()
        result = callback(*args)
        self._available()
        _require(result is True)
        return True

    def _storage_allowed(self):
        return self._checked(self._storage)

    def _guard(self, *, fresh):
        if fresh is True and self._journal_action_ref is not None:
            return self._guard_action()
        self._checked(self._access)
        self._storage_allowed()
        self._checked(self._peer, self.remote)
        if fresh:
            self._checked(self._permission, self.intention)

    def _store_allowed(self, epoch, run):
        # WorkflowStore invokes this immediately before each backend dispatch,
        # including its storage-status probes. Recheck native permission and
        # original lifetime, not only storage availability.
        self._available()
        _require(self._busy and type(epoch) is str and self._workflow_epoch == epoch
                 and run is not None and self._workflow_run is run)
        if self._workflow_access is None or not self._fresh:
            self._guard(fresh=self._fresh)
        else:
            self._live()
            self._checked(self._workflow_access)
        if self._fresh:
            self._live()
        _require(self._workflow_epoch == epoch and self._workflow_run is run)
        return True

    def _borrowed_read_allowed(self):
        # BinaryCapture owns this separate read-only lease. No optional live
        # workflow observer or fresh spending action is admitted by this path.
        self._available()
        _require(self._busy and not self._fresh and self._workflow_epoch is None and self._workflow_run is None)
        self._guard(fresh=False)
        return True

    def _time(self):
        self._available()
        value = self._clock()
        self._available()
        _require(type(value) is tuple and len(value) == 2)
        for number in value:
            _integer(number)
        _require(self._last_clock is None or all(a >= b for a,b in zip(value,self._last_clock)))
        self._last_clock = value
        return value

    def _live(self):
        now = self._time()
        _require(self.boot_id == self.intention.host_boot_id
                 and self.context.issued_epoch_us <= now[0] < self.intention.application_expires_epoch_us
                 and now[1] < self.intention.application_deadline_monotonic_us)
        return now

    def _snapshot(self):
        self._available()
        epoch = self.store.storage.snapshot().epoch
        self._available()
        return epoch

    def _run(self, action, *, fresh=True):
        entered = False
        try:
            self._available()
            if self._busy:
                # Even a callback catching this refusal cannot make its outer
                # operation succeed or dispatch another mutation.
                self._uncertain = True
                raise ForwardJournalError()
            self._busy = entered = True
            self._fresh = fresh
            if self._journal_action_ref is not None:
                self._action_run = fresh
            self._available()
            lease = self._lease()
            self._available()
            with _Lease(lease):
                self._guard(fresh=fresh)
                epoch = self._snapshot()
                if fresh:
                    self._live()
                    if self._epoch is not None:
                        _require(epoch == self._epoch)
                _require(self._workflow_epoch is None and self._workflow_run is None)
                self._workflow_epoch = epoch
                self._workflow_run = run = object()
                result = action(epoch)
                _require(self._workflow_epoch == epoch and self._workflow_run is run)
                self._guard(fresh=fresh)
                _require(self._snapshot() == epoch)
                if fresh:
                    self._live()
            self._available()
            # A lease exit may revoke access, advance time or replace storage
            # without calling close(). It cannot publish a stale success.
            _require(self._snapshot() == epoch)
            if fresh:
                self._live()
            else:
                self._time()
            # The snapshot/clock callbacks above can revoke permission too.
            # Current permission is the final observation before publication.
            self._guard(fresh=fresh)
            return result
        except BaseException as error:
            if entered:
                self._uncertain = True
            if not isinstance(error, Exception):
                raise
            raise ForwardJournalError() from None
        finally:
            if entered:
                self._workflow_epoch = self._workflow_run = None
                self._action_run = None
                self._busy = False

    def _validate(self, record):
        value = record['payload']
        _require(type(value) is dict and set(value) == {'schema','body','signature'}
                 and value['schema'] == SCHEMA and type(value['signature']) is str)
        signature = base64.b64decode(value['signature'], validate=True)
        _require(len(signature) == 64 and base64.b64encode(signature).decode('ascii') == value['signature'])
        body = value['body']
        self._key.verify_key.verify(SIGNATURE_DOMAIN + _canonical(body), signature)
        fields = {'record_id','revision','binding','context','local_initiator','intention',
                  'storage_epoch','prepared_epoch_us','prepared_monotonic_us',
                  'last_epoch_us','last_monotonic_us','tx','confirmation_received','capsule'}
        _require(type(body) is dict and set(body) == fields
                 and type(body['revision']) is int and body['revision'] == record['revision']
                 and body['record_id'] == record['record_id'] == self.record_id
                 and body['binding'] == self.binding.hex()
                 and _canonical(body['context']) == _canonical(self._context_value)
                 and type(body['local_initiator']) is bool
                 and body['local_initiator'] == self.local_initiator
                 and _canonical(body['intention']) == _canonical(self.intention.to_dict())
                 and type(body['storage_epoch']) is str and 1 <= len(body['storage_epoch']) <= 100
                 and type(body['confirmation_received']) is bool)
        for name in ('prepared_epoch_us','prepared_monotonic_us','last_epoch_us','last_monotonic_us'):
            _integer(body[name])
        _integer(body['tx'],0,self.context.max_records)
        _require(self.context.issued_epoch_us <= body['prepared_epoch_us'] <= body['last_epoch_us']
                 < self.intention.application_expires_epoch_us
                 and body['prepared_monotonic_us'] <= body['last_monotonic_us']
                 < self.intention.application_deadline_monotonic_us)
        capsule = body['capsule']
        if capsule is not None:
            _require(type(capsule) is dict and set(capsule) == {'counter','handshake_hash',
                'outer_sha256','observed_epoch_us','observed_monotonic_us','wire'}
                and type(capsule['counter']) is int and capsule['counter'] == 2
                and body['confirmation_received'] is True and body['tx'] >= 1)
            _digest(capsule['handshake_hash']); _digest(capsule['outer_sha256'])
            for name in ('observed_epoch_us','observed_monotonic_us'):
                _integer(capsule[name])
            _require(body['prepared_epoch_us'] <= capsule['observed_epoch_us'] <= body['last_epoch_us']
                     and body['prepared_monotonic_us'] <= capsule['observed_monotonic_us'] <= body['last_monotonic_us'])
            packet = to_binary(capsule['wire'])
            _require(len(packet) == self.intention.packet_bytes
                     and hashlib.sha256(packet).hexdigest() == self.intention.packet_sha256)
        return body

    def _observe(self, body):
        encoded = _canonical(body)
        _require(body['revision'] >= self._highest_revision
                 and (body['revision'] != self._highest_revision or encoded == self._highest_body))
        self._highest_revision, self._highest_body = body['revision'], encoded
        return body

    def _read(self, epoch=None):
        if epoch is None:
            self._borrowed_read_allowed()
            epoch = self._snapshot()
            allowed = self._borrowed_read_allowed
        else:
            run = self._workflow_run
            allowed = lambda:self._store_allowed(epoch,run)
        record = self.store.get(self.record_id,kind='mesh_inbound',
            access_check=allowed,expected_epoch=epoch)
        body = self._validate(record)
        return self._observe(body)

    def _write(self, body, epoch):
        self._guard(fresh=True)
        now = self._live()
        _require(body['storage_epoch'] == epoch == self._snapshot()
                 and now[0] >= body['last_epoch_us'] and now[1] >= body['last_monotonic_us'])
        body = {**body,'revision':body['revision']+1,'last_epoch_us':now[0],'last_monotonic_us':now[1]}
        signature = self._key.sign(SIGNATURE_DOMAIN + _canonical(body)).signature
        run = self._workflow_run
        record = self.store.put(self.record_id,'mesh_inbound',body['revision']-1,
            dict(schema=SCHEMA,body=body,signature=base64.b64encode(signature).decode('ascii')),
            access_check=lambda:self._store_allowed(epoch,run),expected_epoch=epoch)
        checked = self._validate(record)
        _require(checked == body)
        return self._observe(checked)

    def claim(self, binding):
        def claim(epoch):
            _require(type(binding) is bytes and binding == self.binding and not self._claimed)
            run = self._workflow_run
            try:
                self.store.get(self.record_id,kind='mesh_inbound',
                    access_check=lambda:self._store_allowed(epoch,run),expected_epoch=epoch)
            except WorkflowNotFound:
                now = self._live()
                body = dict(record_id=self.record_id,revision=0,binding=self.binding.hex(),
                    context=self._context_value,local_initiator=self.local_initiator,
                    intention=self.intention.to_dict(),storage_epoch=epoch,
                    prepared_epoch_us=now[0],prepared_monotonic_us=now[1],
                    last_epoch_us=now[0],last_monotonic_us=now[1],tx=0,
                    confirmation_received=False,capsule=None)
                self._write(body,epoch)
                self._claimed,self._epoch = True,epoch
                return True
            # Any existing namespace (even invalid/old-schema content) fences
            # reuse. A changed context/UUID/mode never overwrites the record.
            raise ForwardJournalError()
        return self._run(claim)

    def _active(self, epoch):
        _require(self._claimed and self._epoch == epoch)
        body = self._read(epoch)
        _require(body['storage_epoch'] == epoch)
        return body

    def reserve_transmit(self):
        def reserve(epoch):
            body = self._active(epoch)
            _require(body['tx'] < self.context.max_records)
            return self._write({**body,'tx':body['tx']+1},epoch)['tx']
        return self._run(reserve)

    def record_receive(self, counter):
        def receive(epoch):
            body = self._active(epoch)
            _require(type(counter) is int and counter == 1 and not body['confirmation_received'])
            self._write({**body,'confirmation_received':True},epoch)
            return True
        return self._run(receive)

    def commit_receive(self, candidate):
        def commit(epoch):
            body = self._active(epoch)
            _require(type(candidate) is ReceiveCandidate and candidate.context == self.context
                     and type(candidate.local_initiator) is bool
                     and candidate.local_initiator == self.local_initiator
                     and type(candidate.counter_binding) is bytes and candidate.counter_binding == self.binding
                     and type(candidate.counter) is int and candidate.counter == 2
                     and body['confirmation_received'] is True and body['tx'] >= 1 and body['capsule'] is None
                     and type(candidate.application_deadline_monotonic_us) is int
                     and candidate.application_deadline_monotonic_us == self.intention.application_deadline_monotonic_us
                     and type(candidate.handshake_hash) is bytes and len(candidate.handshake_hash) == 32
                     and type(candidate.packet_sha256) is bytes and len(candidate.packet_sha256) == 32
                     and type(candidate.sealed_record) is bytes)
            now = self._live()
            _integer(candidate.observed_epoch_us); _integer(candidate.observed_monotonic_us)
            _require(body['prepared_epoch_us'] <= candidate.observed_epoch_us <= now[0]
                     and body['prepared_monotonic_us'] <= candidate.observed_monotonic_us <= now[1]
                     and len(candidate.sealed_record) == self.intention.packet_bytes
                     and candidate.sealed_sha256.hex() == self.intention.packet_sha256)
            capsule = dict(counter=2,handshake_hash=candidate.handshake_hash.hex(),
                outer_sha256=candidate.packet_sha256.hex(),observed_epoch_us=candidate.observed_epoch_us,
                observed_monotonic_us=candidate.observed_monotonic_us,wire=to_text(candidate.sealed_record))
            # Capsule presence IS the DATA replay acceptance. Both are signed
            # inside this single CAS; no later write can strand accepted bytes.
            self._write({**body,'capsule':capsule},epoch)
            return True
        return self._run(commit)

    def recover(self):
        """Read quarantine with current read permission; grant no fresh admission.

        This works after original expiry/boot change when protected reads are
        currently permitted. No write, new claim, key recovery or deadline reset.
        A fresh owner supplies the exact original context and intention.
        """
        def recover(epoch):
            body = self._read(epoch)
            now = self._time()
            _require(now[0] >= body['last_epoch_us'])
            if self.boot_id == self.intention.host_boot_id:
                _require(now[1] >= body['last_monotonic_us'])
            capsule = body['capsule']
            if capsule is None:
                return None
            return ReceiveRecovery(self.record_id,body['revision'],self.intention,self.context,
                self.local_initiator,2,capsule['handshake_hash'],capsule['outer_sha256'],
                body['prepared_epoch_us'],body['prepared_monotonic_us'],
                capsule['observed_epoch_us'],capsule['observed_monotonic_us'],to_binary(capsule['wire']))
        return self._run(recover,fresh=False)

    def close(self):
        """Fence caller-owned callbacks; retain all claims/counters/capsules."""
        self._closed = True
        return not self._busy and not self._uncertain
