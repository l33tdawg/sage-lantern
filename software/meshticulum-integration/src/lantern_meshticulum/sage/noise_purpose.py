# SPDX-License-Identifier: Apache-2.0
"""Operation-scoped Noise keys in RAM and signed public-only purpose records.

The setup lease and all observers are independent trusted-owner inputs. They
must observe current access, consent, storage and enrollment inside and after
the lease; never implement them by calling ForwardNativeOwner access methods
which themselves call this component. Read observers acquire no setup lease.
An authenticated public record or a callback returning True is not enrollment
authority by construction. No native provider or physical qualification follows.

There is no private-key export, persistence, import or recovery API. A stable
actor/operation namespace is reserved before generation. Any existing record
prevents generation, including unfinished and retired records. Fresh-process
retirement is not provided; public records never recover a live key or handle.
Dropping references does not prove forensic memory erasure or backend antirollback.
"""

import base64
from dataclasses import dataclass
import hashlib
import json
import os
import threading
import uuid

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from nacl.signing import SigningKey

from meshticulum.privacy.forward_session import (
    DOMAIN as FORWARD_DOMAIN, ForwardContext, ForwardSession, canonical_x25519, public,
)
from meshticulum.privacy.mesh_envelope import Peer
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.mesh_binary_capture import MAX_TIME_US, _Lease
from lantern_meshticulum.sage.mesh_inbox import _canonical, _id, _uuid
from lantern_meshticulum.sage.workflow_store import WorkflowNotFound, WorkflowStore


SCHEMA = 'sage.lantern.noise-purpose.v1'
SIGNATURE_DOMAIN = b'sage.lantern.noise-purpose.v1/signature\0'
PURPOSE = 'meshticulum.forward-private.noise-static'
ALGORITHM = 'X25519'
KIND = 'mesh_outbound'


class NoisePurposeError(RuntimeError):
    def __init__(self):
        super().__init__('noise_purpose_unavailable')


def _require(value):
    if not value:
        raise NoisePurposeError()


def _digest(value):
    _require(type(value) is str and len(value) == 64
             and all(c in '0123456789abcdef' for c in value))
    return value


def _integer(value):
    _require(type(value) is int and 0 <= value <= MAX_TIME_US)
    return value


@dataclass(frozen=True, repr=False)
class NoiseBinding:
    local: Peer
    remote: Peer
    local_initiator: bool
    generation_id: str
    intention: ReceiveIntention
    issued_epoch_us: int
    approval_digest: str

    def __repr__(self):
        return '<NoiseBinding>'

    def to_dict(self):
        return dict(local=self.local.to_dict(), remote=self.remote.to_dict(),
            local_initiator=self.local_initiator, generation_id=self.generation_id,
            intention=self.intention.to_dict(), issued_epoch_us=self.issued_epoch_us,
            approval_digest=self.approval_digest)


@dataclass(frozen=True, repr=False)
class NoisePublic:
    """Public enrollment input, not permission or a recoverable private key."""
    binding: NoiseBinding
    public_key: bytes

    def __repr__(self):
        return '<NoisePublic>'

    def to_dict(self):
        return dict(purpose=PURPOSE, algorithm=ALGORITHM,
                    binding=self.binding.to_dict(), public_key=self.public_key.hex())


class NoiseKeyHandle:
    """One instance-owned handoff to a fresh session/operation factory."""
    def __init__(self, owner):
        self._owner = owner
        self._used = False

    def __repr__(self):
        return '<NoiseKeyHandle>'

    def start(self, factory, *, abort):
        """Pass the key only to the factory; return a session or operation only.

        The factory may call key_check/peer_key_check while creating the native
        owner and crypto claim. It must not start a handshake before returning
        the new session. abort must close any retained or unreturned incarnation
        and other factory resources, then return literal True. It is registered
        before handoff and is called at most once after the factory exits; a
        failed cleanup remains uncertain even if registered sessions are closed.
        """
        return self._owner._start(self, factory, abort)


class NoisePurpose:
    def __init__(self, store, *, local, remote, local_initiator, intention,
                 signing_key, issued_epoch_us, approval_digest, owner_lease,
                 access_check, storage_check, native_check, enrollment_check,
                 remote_pin_check, boot_reader, clock, generation_id=None,
                 exchange_enrollment_check=None):
        _require(type(store) is WorkflowStore and type(local) is Peer and type(remote) is Peer
                 and local != remote and local.public_key != remote.public_key
                 and type(local_initiator) is bool and type(intention) is ReceiveIntention
                 and isinstance(signing_key, SigningKey)
                 and all(callable(c) for c in (owner_lease, access_check, storage_check,
                     native_check, enrollment_check, remote_pin_check, boot_reader, clock))
                 and (exchange_enrollment_check is None or callable(exchange_enrollment_check)))
        _integer(issued_epoch_us)
        _require(issued_epoch_us < intention.application_expires_epoch_us
                 and bytes(signing_key.verify_key) == local.public_key
                 and store.storage.actor.chain_id == local.chain_id
                 and store.storage.actor.agent_id == local.public_key.hex())
        _digest(approval_digest)
        generation_id = str(uuid.uuid4()) if generation_id is None else generation_id
        _uuid(generation_id)
        self.store, self._signer = store, signing_key
        self.binding = NoiseBinding(local, remote, local_initiator, generation_id,
                                    intention, issued_epoch_us, approval_digest)
        self._binding_bytes = _canonical(self.binding.to_dict())
        # Changing role, context, generation, boot, approval or lifetime cannot
        # create a replacement namespace for this original actor/operation.
        self.record_id = _id(SCHEMA, [local.to_dict(), intention.operation_id])
        self._refs = (store, store.storage, store.client, signing_key, self.binding)
        self._lease, self._access, self._storage = owner_lease, access_check, storage_check
        self._native, self._enrollment, self._pin = native_check, enrollment_check, remote_pin_check
        self._exchange_enrollment = self._exchange_enrollment_ref = exchange_enrollment_check
        self._boot, self._clock = boot_reader, clock
        self._owner = (os.getpid(), threading.get_ident())
        self._busy = None
        self._observing = False
        self._factory_active = False
        self._disabled = self._uncertain = False
        self._cancel_serial = 0
        self._cleanup_serial = None
        self._closing_sessions = False
        self._close_failed = False
        self._key = self._public = self._context = self._context_value = self._handle = None
        self._public_ref = self._context_ref = self._context_digest = self._context_canonical = None
        self._abort = None
        self._abort_called = False
        self._sessions = []
        self._epoch = self._last_clock = None
        self._highest_revision = 0
        self._highest_body = None
        self._fresh = True

    def __repr__(self):
        return '<NoisePurpose>'

    def _available(self):
        _require(self._owner == (os.getpid(), threading.get_ident()))
        _require(self._exchange_enrollment is self._exchange_enrollment_ref)
        store, storage, client, signer, binding = self._refs
        _require(self.store is store and store.storage is storage and store.client is client
                 and self._signer is signer and self.binding is binding
                 and storage.actor.chain_id == binding.local.chain_id
                 and storage.actor.agent_id == binding.local.public_key.hex()
                 and bytes(signer.verify_key) == binding.local.public_key
                 and _canonical(binding.to_dict()) == self._binding_bytes)
        if self._public_ref is None:
            _require(self._public is None)
        else:
            _require(self._public is self._public_ref and self._public.binding is binding)
        if self._context_ref is None:
            _require(self._context is None and self._context_value is None)
        else:
            _require(self._context is self._context_ref and self._context.digest == self._context_digest
                     and _canonical(self._context_value) == self._context_canonical
                     and self._context.prologue == FORWARD_DOMAIN + self._context_canonical)
        if self._busy == 'retire':
            _require(self._cleanup_serial == self._cancel_serial)
        else:
            _require(not self._disabled and not self._uncertain)

    def _fence(self):
        self._disabled = True
        self._cancel_serial += 1
        self._key = None
        if self._closing_sessions:
            return
        self._closing_sessions = True
        try:
            for session in self._sessions:
                try:
                    session.close()
                    _require(session._closed)
                except BaseException:
                    self._close_failed = True
            # A callback may fence during construction and the factory may
            # catch that refusal. Run cleanup only once it has fully exited,
            # so later retained construction cannot escape an early abort.
            if self._abort is not None and not self._abort_called and not self._factory_active:
                self._abort_called = True
                try:
                    _require(self._abort() is True)
                except BaseException:
                    self._close_failed = self._uncertain = True
        finally:
            self._closing_sessions = False

    def _checked(self, callback, *args):
        self._available()
        result = callback(*args)
        self._available()
        _require(result is True)
        return True

    def _guard(self):
        self._checked(self._access)
        self._checked(self._storage)
        self._checked(self._native, self.binding)

    def _time(self):
        self._available()
        boot = self._boot()
        self._available()
        _uuid(boot)
        _require(boot == self.binding.intention.host_boot_id)
        now = self._clock()
        self._available()
        _require(type(now) is tuple and len(now) == 2)
        for value in now:
            _integer(value)
        _require(self._last_clock is None or all(a >= b for a, b in zip(now, self._last_clock)))
        self._last_clock = now
        if self._fresh:
            _require(self.binding.issued_epoch_us <= now[0] < self.binding.intention.application_expires_epoch_us
                     and now[1] < self.binding.intention.application_deadline_monotonic_us)
        return now

    def _snapshot(self):
        self._available()
        epoch = self.store.storage.snapshot().epoch
        self._available()
        _require(self._epoch is None or epoch == self._epoch)
        return epoch

    def _allowed(self):
        self._guard()
        self._time()
        self._guard()
        return True

    def _run(self, name, action, *, lease=True, fresh=True):
        entered = False
        try:
            if self._busy is not None or self._observing:
                self._uncertain = True
                self._fence()
                raise NoisePurposeError()
            if name == 'retire':
                self._cleanup_serial = self._cancel_serial
            self._busy = name
            entered = True
            self._fresh = fresh
            self._available()
            self._guard()
            self._time()
            if lease:
                held = self._lease()
                self._available()
                with _Lease(held):
                    self._guard()
                    epoch = self._snapshot()
                    self._time()
                    result = action(epoch)
                    _require(self._snapshot() == epoch)
                    self._time()
                    self._guard()
            else:
                epoch = self._snapshot()
                result = action(epoch)
            _require(self._snapshot() == epoch)
            self._time()
            _require(self._snapshot() == epoch)
            self._guard()
            self._available()
            return result
        except BaseException as error:
            if entered:
                self._uncertain = True
                self._fence()
            if not isinstance(error, Exception):
                raise
            raise NoisePurposeError() from None
        finally:
            if entered:
                self._busy = None
                self._cleanup_serial = None

    def _validate(self, record):
        value = record['payload']
        _require(type(value) is dict and set(value) == {'schema', 'body', 'signature'}
                 and value['schema'] == SCHEMA and type(value['signature']) is str)
        signature = base64.b64decode(value['signature'], validate=True)
        _require(len(signature) == 64 and base64.b64encode(signature).decode('ascii') == value['signature'])
        body = value['body']
        self._signer.verify_key.verify(SIGNATURE_DOMAIN + _canonical(body), signature)
        fields = {'record_id', 'revision', 'purpose', 'algorithm', 'binding', 'storage_epoch',
                  'prepared_epoch_us', 'prepared_monotonic_us', 'last_epoch_us', 'last_monotonic_us',
                  'state', 'public_key', 'context', 'retirement'}
        _require(type(body) is dict and set(body) == fields and body['record_id'] == record['record_id'] == self.record_id
                 and type(body['revision']) is int and body['revision'] == record['revision']
                 and body['purpose'] == PURPOSE and body['algorithm'] == ALGORITHM
                 and _canonical(body['binding']) == self._binding_bytes
                 and type(body['storage_epoch']) is str and 1 <= len(body['storage_epoch']) <= 100
                 and body['state'] in ('reserved', 'generated', 'bound', 'retired'))
        for name in ('prepared_epoch_us', 'prepared_monotonic_us', 'last_epoch_us', 'last_monotonic_us'):
            _integer(body[name])
        _require(self.binding.issued_epoch_us <= body['prepared_epoch_us'] <= body['last_epoch_us']
                 and body['prepared_epoch_us'] < self.binding.intention.application_expires_epoch_us
                 and body['prepared_monotonic_us'] <= body['last_monotonic_us']
                 and body['prepared_monotonic_us'] < self.binding.intention.application_deadline_monotonic_us)
        if body['state'] != 'retired':
            _require(body['last_epoch_us'] < self.binding.intention.application_expires_epoch_us
                     and body['last_monotonic_us'] < self.binding.intention.application_deadline_monotonic_us
                     and body['retirement'] is None)
        key = body['public_key']
        if key is not None:
            _digest(key)
            _require(canonical_x25519(bytes.fromhex(key)))
            if self._public is not None:
                _require(bytes.fromhex(key) == self._public.public_key)
        context = body['context']
        if context is not None:
            _require(type(context) is dict and self._context_value is not None
                     and _canonical(context) == self._context_canonical)
        if body['state'] == 'reserved':
            _require(key is None and context is None)
        elif body['state'] == 'generated':
            _require(key is not None and context is None)
        elif body['state'] == 'bound':
            _require(key is not None and context is not None)
        else:
            retirement = body['retirement']
            _require(type(retirement) is dict and set(retirement) == {'epoch_us', 'monotonic_us'}
                     and retirement['epoch_us'] == body['last_epoch_us']
                     and retirement['monotonic_us'] == body['last_monotonic_us'])
            _integer(retirement['epoch_us']); _integer(retirement['monotonic_us'])
        encoded = _canonical(body)
        _require(body['revision'] >= self._highest_revision
                 and (body['revision'] != self._highest_revision or encoded == self._highest_body))
        self._highest_revision, self._highest_body = body['revision'], encoded
        return body

    def _read(self, epoch):
        body = self._validate(self.store.get(self.record_id, kind=KIND, access_check=self._allowed))
        _require(body['storage_epoch'] == epoch)
        self._epoch = epoch
        return body

    def _write(self, body, epoch):
        self._guard()
        now = self._time()
        _require(body['storage_epoch'] == epoch == self._snapshot()
                 and now[0] >= body['last_epoch_us'] and now[1] >= body['last_monotonic_us'])
        body = {**body, 'revision': body['revision'] + 1,
                'last_epoch_us': now[0], 'last_monotonic_us': now[1]}
        if body['state'] == 'retired':
            body['retirement'] = dict(epoch_us=now[0], monotonic_us=now[1])
        signature = self._signer.sign(SIGNATURE_DOMAIN + _canonical(body)).signature
        result = self.store.put(self.record_id, KIND, body['revision'] - 1,
            dict(schema=SCHEMA, body=body, signature=base64.b64encode(signature).decode('ascii')),
            access_check=self._allowed)
        checked = self._validate(result)
        _require(_canonical(checked) == _canonical(body))
        self._epoch = epoch
        return checked

    def generate(self):
        """Reserve this original namespace, then generate one independent key."""
        def generate(epoch):
            _require(self._key is None and self._public is None and self._handle is None)
            try:
                self.store.get(self.record_id, kind=KIND, access_check=self._allowed)
            except WorkflowNotFound:
                pass
            else:
                raise NoisePurposeError()  # Even invalid/old-schema rows retire the namespace.
            now = self._time()
            body = dict(record_id=self.record_id, revision=0, purpose=PURPOSE, algorithm=ALGORITHM,
                binding=self.binding.to_dict(), storage_epoch=epoch, prepared_epoch_us=now[0],
                prepared_monotonic_us=now[1], last_epoch_us=now[0], last_monotonic_us=now[1],
                state='reserved', public_key=None, context=None, retirement=None)
            body = self._write(body, epoch)
            self._allowed()
            key = X25519PrivateKey.generate()
            self._available()
            raw_public = public(key)
            _require(canonical_x25519(raw_public))
            self._public = NoisePublic(self.binding, raw_public)
            self._public_ref = self._public
            self._write({**body, 'state': 'generated', 'public_key': raw_public.hex()}, epoch)
            self._key = key
            return self._public
        return self._run('generate', generate)

    def _enrolled(self):
        _require(self._public is not None and self._context is not None)
        remote_key = (self._context.responder_noise_public_key if self.binding.local_initiator
                      else self._context.initiator_noise_public_key)
        if self._exchange_enrollment is None:
            self._checked(self._enrollment, self._public, self._context.digest)
            self._checked(self._pin, self.binding.remote, remote_key, self._context.digest)
        else:
            self._checked(self._exchange_enrollment, self._public, self.binding.remote,
                          remote_key, self._context.digest)

    def bind(self, context):
        """Bind once after both pins have independent current enrollment."""
        def bind(epoch):
            _require(type(context) is ForwardContext and self._key is not None
                     and self._public is not None and self._context is None and self._handle is None)
            local = context.initiator if self.binding.local_initiator else context.responder
            remote = context.responder if self.binding.local_initiator else context.initiator
            local_key = (context.initiator_noise_public_key if self.binding.local_initiator
                         else context.responder_noise_public_key)
            _require(local == self.binding.local and remote == self.binding.remote
                     and local_key == self._public.public_key == public(self._key)
                     and context.binding_digest.hex() == self.binding.intention.authority_digest
                     and context.issued_epoch_us == self.binding.issued_epoch_us
                     and context.expires_epoch_us == self.binding.intention.application_expires_epoch_us)
            self._context = context
            self._context_ref = context
            self._context_digest = context.digest
            self._context_value = json.loads(context.prologue[len(FORWARD_DOMAIN):])
            self._context_canonical = _canonical(self._context_value)
            body = self._read(epoch)
            _require(body['state'] == 'generated')
            self._enrolled()
            self._write({**body, 'state': 'bound', 'context': self._context_value}, epoch)
            self._enrolled()
            return True
        self._run('bind', bind)
        self._handle = NoiseKeyHandle(self)
        return self._handle

    def _observe(self, epoch):
        body = self._read(epoch)
        _require(body['state'] == 'bound' and self._public is not None and self._context is not None)
        _require(self._key is not None or self._factory_active
                 or any(not session._closed for session in self._sessions))
        self._enrolled()
        self._time()
        self._guard()
        return True

    def _observer(self, action):
        # A factory may invoke native owner observation while the one-use
        # handoff is active. No mutation or recursively nested observer may run.
        if self._observing or self._busy is not None and not (self._busy == 'start' and self._factory_active):
            self._uncertain = True
            self._fence()
            raise NoisePurposeError()
        self._observing = True
        try:
            self._available()
            self._guard()
            self._time()
            epoch = self._snapshot()
            action()
            self._observe(epoch)
            _require(self._snapshot() == epoch)
            self._time()
            _require(self._snapshot() == epoch)
            self._guard()
            self._available()
            return True
        except BaseException as error:
            self._uncertain = True
            self._fence()
            if not isinstance(error, Exception):
                raise
            raise NoisePurposeError() from None
        finally:
            self._observing = False

    def key_check(self, public_key, context_digest):
        """ForwardNativeOwner local-purpose observer; never acquires a lease."""
        def check():
            _require(type(public_key) is bytes and self._public is not None
                     and public_key == self._public.public_key and type(context_digest) is bytes
                     and self._context is not None and context_digest == self._context.digest)
        return self._observer(check)

    def peer_key_check(self, peer, public_key, context_digest):
        """ForwardNativeOwner remote-pin observer; signature alone is insufficient."""
        def check():
            _require(type(peer) is Peer and peer == self.binding.remote and type(public_key) is bytes
                     and self._context is not None and type(context_digest) is bytes
                     and context_digest == self._context.digest)
            expected = (self._context.responder_noise_public_key if self.binding.local_initiator
                        else self._context.initiator_noise_public_key)
            _require(public_key == expected)
        return self._observer(check)

    def exchange_check(self, local_public_key, remote_peer, remote_public_key, context_digest):
        """Observe both exact purpose pins at one native owner boundary."""
        def check():
            _require(type(local_public_key) is bytes and self._public is not None
                     and local_public_key == self._public.public_key
                     and type(remote_peer) is Peer and remote_peer == self.binding.remote
                     and type(remote_public_key) is bytes and self._context is not None
                     and type(context_digest) is bytes and context_digest == self._context.digest)
            expected = (self._context.responder_noise_public_key if self.binding.local_initiator
                        else self._context.initiator_noise_public_key)
            _require(remote_public_key == expected)
        return self._observer(check)

    def _start(self, handle, factory, abort):
        def start(epoch):
            _require(type(handle) is NoiseKeyHandle and handle is self._handle and handle._owner is self
                     and not handle._used and callable(factory) and callable(abort) and self._key is not None)
            self._observe(epoch)
            handle._used = True  # Spent before any factory side effect.
            self._abort = abort
            key = self._key
            self._factory_active = True
            try:
                result = factory(key)
            finally:
                self._factory_active = False
            if type(result) is ForwardSession:
                session = result
            else:
                from lantern_meshticulum.sage.forward_operation import ForwardOperation
                _require(type(result) is ForwardOperation)
                session = result.session
            _require(type(session) is ForwardSession)
            self._sessions.append(session)  # Closure precedes post-factory publication checks.
            self._available()
            _require(not session._closed and session.state == 'new' and session.context == self._context
                     and session.initiator is self.binding.local_initiator
                     and session.deadline == self.binding.intention.application_deadline_monotonic_us
                     and session._s is key and session._owner == self._owner)
            self._observe(epoch)
            self._key = None  # The registered crypto incarnation now owns the reference.
            return result
        return self._run('start', start, lease=False)

    def retire(self):
        """Fence locally first; explicitly persist public retirement if permitted.

        This never reactivates a key, handle, session, counter or budget. A failed
        return remains uncertain even if its signed tombstone was committed.
        Expired work may be retired under current independent cleanup permission;
        changed boot/storage epochs still refuse this owner's mutation.
        """
        self._fence()
        def retire(epoch):
            body = self._read(epoch)
            if body['state'] != 'retired':
                self._write({**body, 'state': 'retired', 'retirement': {}}, epoch)
            _require(not self._close_failed)
            return True
        return self._run('retire', retire, fresh=False)

    def close(self):
        """Local cancellation only; never claims a durable retirement or drain."""
        self._fence()
        return (self._owner == (os.getpid(), threading.get_ident()) and self._busy is None
                and not self._observing and not self._uncertain and not self._close_failed)
