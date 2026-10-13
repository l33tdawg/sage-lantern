# SPDX-License-Identifier: Apache-2.0
"""Actual key generation, SDK CAS and crypto; native/enrollment are models."""

import base64
import copy
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
import threading
import uuid

import pytest
pytest.importorskip('sage_sdk', reason='compatible optional SDK required')
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from meshticulum.privacy.forward_session import ForwardContext, ForwardSession, public
from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.privacy.mesh_envelope import Peer
from lantern_meshticulum.sage.forward_receive_journal import ForwardReceiveJournal, ReceiveIntention
from lantern_meshticulum.sage.noise_purpose import (
    NoisePurpose, NoisePurposeError, NoiseKeyHandle, SCHEMA, SIGNATURE_DOMAIN,
)
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_mesh_inbox import rig, KEY, SENDER, SENDER_KEY, RECIPIENT, OPERATION, sealed


BOOT = '21f9c178-6090-40d9-9359-ecf1c7b950b1'


class NoExportKey(X25519PrivateKey):
    """Actual generated key; exporting its scalar is forbidden in this control."""
    def __init__(self, inner):
        self._inner = inner

    def public_key(self):
        return self._inner.public_key()

    def exchange(self, public_key):
        return self._inner.exchange(public_key)

    def private_bytes(self, *args, **kwargs):
        raise AssertionError('private key export forbidden')

    def private_bytes_raw(self):
        raise AssertionError('private key export forbidden')

    def __copy__(self):
        raise AssertionError('private key copying forbidden')

    def __deepcopy__(self, memo):
        raise AssertionError('private key copying forbidden')

    def __repr__(self):
        return '<NoExportKey>'


class SenderCounters:
    """Sender counter model; protected purpose and receiver CAS are actual code."""
    def claim(self, binding):
        self.tx = 0
        return True

    def reserve_transmit(self):
        self.tx += 1
        return self.tx

    def record_receive(self, counter):
        return counter == 1


@pytest.fixture
def purpose_rig(rig, monkeypatch):
    r = rig
    r.now = 100
    r.mono = 50000000
    r.boot = BOOT
    r.native = r.enrolled = r.remote_enrolled = True
    r.lease_active = False
    r.lease_count = 0
    r.after_exit = r.native_hook = r.enrollment_hook = r.pin_hook = None
    r.generations = 0
    original_generate = X25519PrivateKey.generate
    r.remote_key = original_generate()
    def generate():
        r.generations += 1
        return NoExportKey(original_generate())
    monkeypatch.setattr(X25519PrivateKey, 'generate', staticmethod(generate))
    r.packet = to_binary(sealed(issued_at=100, expires_at=180).decode())
    r.intention = ReceiveIntention(OPERATION, hashlib.sha256(r.packet).hexdigest(), len(r.packet),
        'a' * 64, BOOT, 180000000, 130000000)
    r.clock = lambda: (int(r.now * 1000000), r.mono)
    @contextmanager
    def lease():
        assert not r.lease_active
        r.lease_active = True
        r.lease_count += 1
        try:
            yield
        finally:
            r.lease_active = False
            if r.after_exit:
                r.after_exit()
    def native(binding):
        if r.native_hook:
            r.native_hook()
        return r.native and binding.intention == r.intention
    def enrollment(info, digest):
        if r.enrollment_hook:
            r.enrollment_hook()
        return r.enrolled and info.binding.intention == r.intention and digest == r.context.digest
    def pin(peer, key, digest):
        if r.pin_hook:
            r.pin_hook()
        return r.remote_enrolled and peer == SENDER and key == public(r.remote_key) and digest == r.context.digest
    def new(**changes):
        args = dict(local=RECIPIENT, remote=SENDER, local_initiator=False, intention=r.intention,
            signing_key=KEY, issued_epoch_us=100000000, approval_digest='c' * 64,
            owner_lease=lease, access_check=lambda: r.live, storage_check=lambda: r.storage,
            native_check=native, enrollment_check=enrollment, remote_pin_check=pin,
            boot_reader=lambda: r.boot, clock=r.clock)
        args.update(changes)
        return NoisePurpose(r.store, **args)
    r.new_purpose = new
    r.purpose = new()
    r.lease = lease
    def generate_and_context():
        info = r.purpose.generate()
        r.context = ForwardContext(SENDER, RECIPIENT, uuid.uuid4().bytes, bytes.fromhex(r.intention.authority_digest),
            100000000, 180000000, 64, public(r.remote_key), info.public_key)
        return info
    r.generate_and_context = generate_and_context
    return r


def body(r):
    return r.rows[r.purpose.record_id]['payload']['body']


def bind(r):
    info = r.generate_and_context()
    return info, r.purpose.bind(r.context)


def factory(r, *, after=None):
    def create(key):
        # Standalone callback-shape compatibility, without importing/changing
        # ForwardNativeOwner or constructing any native resource.
        assert r.purpose.key_check(public(key), r.context.digest)
        assert r.purpose.peer_key_check(SENDER, public(r.remote_key), r.context.digest)
        journal = ForwardReceiveJournal(r.store, context=r.context, local_initiator=False,
            local_signing_key=KEY, intention=r.intention, boot_id=BOOT, owner_lease=r.lease,
            access_check=lambda: r.live, storage_check=lambda: r.storage,
            peer_check=lambda peer: peer == SENDER, permission_check=lambda intent: r.native,
            clock=r.clock)
        r.session = ForwardSession(context=r.context, initiator=False, signing_key=KEY,
            noise_static_key=key, counters=journal,
            authorize=lambda: r.purpose.key_check(r.context.responder_noise_public_key, r.context.digest),
            clock=r.clock, application_deadline_monotonic_us=r.intention.application_deadline_monotonic_us,
            receive_mode='commit')
        if after:
            after()
        return r.session
    return create


def abort(r):
    r.abort_calls = getattr(r, 'abort_calls', 0) + 1
    if hasattr(r, 'session'):
        r.session.close()
    return True


def start(r, handle, create=None):
    return handle.start(factory(r) if create is None else create, abort=lambda: abort(r))


def test_public_only_signed_generation_binding_one_use_crypto_and_retirement(purpose_rig):
    r = purpose_rig
    info, handle = bind(r)
    assert r.generations == 1 and body(r)['state'] == 'bound'
    assert body(r)['binding']['intention'] == r.intention.to_dict()
    assert body(r)['binding']['generation_id'] == info.binding.generation_id
    assert body(r)['public_key'] == info.public_key.hex()
    assert set(body(r)) == {'record_id', 'revision', 'purpose', 'algorithm', 'binding', 'storage_epoch',
        'prepared_epoch_us', 'prepared_monotonic_us', 'last_epoch_us', 'last_monotonic_us',
        'state', 'public_key', 'context', 'retirement'}
    assert json.loads(json.dumps(info.to_dict()))['public_key'] == info.public_key.hex()
    assert repr(info) == '<NoisePublic>' and repr(handle) == '<NoiseKeyHandle>' and repr(r.purpose) == '<NoisePurpose>'
    leases = r.lease_count
    assert r.purpose.key_check(info.public_key, r.context.digest)
    assert r.purpose.peer_key_check(SENDER, public(r.remote_key), r.context.digest)
    assert r.lease_count == leases
    session = start(r, handle)
    assert session is r.session and r.purpose._key is None and session.state == 'new'
    sender = ForwardSession(context=r.context, initiator=True, signing_key=SENDER_KEY,
        noise_static_key=r.remote_key, counters=SenderCounters(), authorize=lambda: True,
        clock=r.clock, application_deadline_monotonic_us=r.intention.application_deadline_monotonic_us)
    sender.finish(session.respond(sender.start()))
    sender.verify_confirmation(session.accept_confirmation(sender.confirm()))
    session.receive_into(sender.seal(r.packet))
    assert r.purpose.key_check(info.public_key, r.context.digest)
    before = copy.deepcopy(r.rows)
    assert r.purpose.retire() and session._closed
    retired = body(r)
    assert retired['state'] == 'retired' and retired['retirement']
    assert retired['binding'] == before[r.purpose.record_id]['payload']['body']['binding']
    assert all(v == before[k] for k, v in r.rows.items() if k != r.purpose.record_id)
    sender.close()
    with pytest.raises(NoisePurposeError): start(r, handle)
    with pytest.raises(NoisePurposeError): r.purpose.key_check(info.public_key, r.context.digest)


@pytest.mark.parametrize('phase', ['reserve', 'generated', 'bind', 'retire'])
@pytest.mark.parametrize('failure', ['before', 'after', 'conflict'])
def test_cas_uncertainty_fences_key_and_preserves_committed_public_state(purpose_rig, phase, failure):
    r = purpose_rig
    if phase in ('bind', 'retire'):
        r.generate_and_context()
    if phase == 'retire':
        handle = r.purpose.bind(r.context)
        start(r, handle)
    original_put = r.store.put
    def put(*args, **kwargs):
        state = args[3]['body']['state']
        target = 'reserved' if phase == 'reserve' else 'generated' if phase == 'generated' else 'bound' if phase == 'bind' else 'retired'
        if state == target: r.failure = (SCHEMA, failure)
        return original_put(*args, **kwargs)
    r.store.put = put
    action = r.purpose.generate if phase in ('reserve', 'generated') else lambda: r.purpose.bind(r.context) if phase == 'bind' else r.purpose.retire()
    with pytest.raises(NoisePurposeError): action()
    assert r.purpose._disabled and r.purpose._uncertain and r.purpose._key is None
    if phase == 'reserve':
        assert r.generations == 0
        assert (r.purpose.record_id in r.rows) == (failure == 'after')
    elif phase == 'generated':
        assert r.generations == 1 and body(r)['state'] == ('generated' if failure == 'after' else 'reserved')
    elif phase == 'bind':
        assert body(r)['state'] == ('bound' if failure == 'after' else 'generated')
    else:
        assert r.session._closed and body(r)['state'] == ('retired' if failure == 'after' else 'bound')
    r.failure = None
    with pytest.raises(NoisePurposeError): r.purpose.generate()
    if r.purpose.record_id in r.rows:
        count = r.generations
        with pytest.raises(NoisePurposeError): r.new_purpose().generate()
        assert r.generations == count


@pytest.mark.parametrize('change', ['generation', 'role', 'approval', 'operation-context', 'boot', 'lifetime', 'old-schema'])
def test_original_namespace_cannot_be_reset_with_fresh_public_metadata(purpose_rig, change):
    r = purpose_rig; r.purpose.generate(); count = r.generations
    changes = {}
    if change == 'generation': changes['generation_id'] = str(uuid.uuid4())
    elif change == 'role': changes['local_initiator'] = True
    elif change == 'approval': changes['approval_digest'] = 'd' * 64
    elif change == 'operation-context': changes['remote'] = Peer('other', SENDER.public_key)
    elif change == 'boot': changes['intention'] = replace(r.intention, host_boot_id=str(uuid.uuid4()))
    elif change == 'lifetime': changes['intention'] = replace(r.intention, application_expires_epoch_us=180000001)
    else: r.rows[r.purpose.record_id]['payload']['schema'] = 'old-schema'
    fresh = r.new_purpose(**changes)
    assert fresh.record_id == r.purpose.record_id
    with pytest.raises(NoisePurposeError): fresh.generate()
    assert r.generations == count


@pytest.mark.parametrize('gate', ['enrolled', 'remote_enrolled'])
def test_current_independent_enrollment_is_required_even_with_valid_signature(purpose_rig, gate):
    r = purpose_rig; r.generate_and_context(); setattr(r, gate, False)
    with pytest.raises(NoisePurposeError): r.purpose.bind(r.context)
    assert body(r)['state'] == 'generated' and r.purpose._key is None


@pytest.mark.parametrize('mutation', ['context', 'local-key', 'remote-peer', 'remote-key'])
def test_public_observers_reject_substitution_before_crypto_claim(purpose_rig, mutation):
    r = purpose_rig; info, _ = bind(r)
    if mutation == 'context': action = lambda: r.purpose.key_check(info.public_key, b'z' * 32)
    elif mutation == 'local-key': action = lambda: r.purpose.key_check(public(r.remote_key), r.context.digest)
    elif mutation == 'remote-peer': action = lambda: r.purpose.peer_key_check(Peer('other', SENDER.public_key), public(r.remote_key), r.context.digest)
    else: action = lambda: r.purpose.peer_key_check(SENDER, info.public_key, r.context.digest)
    before = copy.deepcopy(r.rows)
    with pytest.raises(NoisePurposeError): action()
    assert r.rows == before and r.purpose._disabled


@pytest.mark.parametrize('gate', ['live', 'storage', 'native', 'enrolled', 'remote_enrolled', 'boot', 'expiry', 'deadline', 'epoch'])
def test_revocation_or_original_lifetime_change_refuses_live_observer(purpose_rig, gate):
    r = purpose_rig; info, handle = bind(r); start(r, handle)
    if gate == 'boot': r.boot = str(uuid.uuid4())
    elif gate == 'expiry': r.now = 180
    elif gate == 'deadline': r.mono = 130000000
    elif gate == 'epoch': r.instance = 'c' * 64
    else: setattr(r, gate, False)
    before = copy.deepcopy(r.rows)
    with pytest.raises(NoisePurposeError): r.purpose.key_check(info.public_key, r.context.digest)
    assert r.rows == before and r.session._closed and r.purpose._key is None


@pytest.mark.parametrize('boundary', ['exit', 'native', 'enrollment', 'pin'])
def test_caught_reentry_cannot_publish_success_or_dispatch_retirement(purpose_rig, boundary):
    r = purpose_rig; info, _ = bind(r); before = copy.deepcopy(r.rows)
    def reenter():
        with pytest.raises(NoisePurposeError): r.purpose.retire()
    if boundary == 'exit':
        r.after_exit = lambda: r.purpose.close()
        # A bind has already happened; retirement must not publish success when
        # its own setup lease exit closes/reenters the purpose owner.
        with pytest.raises(NoisePurposeError): r.purpose.retire()
        assert body(r)['state'] == 'retired'
    else:
        setattr(r, {'native': 'native_hook', 'enrollment': 'enrollment_hook', 'pin': 'pin_hook'}[boundary], reenter)
        with pytest.raises(NoisePurposeError): r.purpose.key_check(info.public_key, r.context.digest)
        assert r.rows == before
    assert r.purpose._disabled and r.purpose._uncertain


@pytest.mark.parametrize('change', ['rollback', 'equivocation', 'unsigned', 'boolean'])
def test_actual_signed_record_revision_and_canonical_fields_are_verified(purpose_rig, change):
    r = purpose_rig; r.generate_and_context(); old = copy.deepcopy(r.rows[r.purpose.record_id])
    r.purpose.bind(r.context)
    row = r.rows[r.purpose.record_id]
    if change == 'rollback': r.rows[r.purpose.record_id] = old
    elif change == 'unsigned': row['payload']['body']['public_key'] = public(r.remote_key).hex()
    else:
        b = row['payload']['body']
        if change == 'boolean': b['binding']['local_initiator'] = 0
        else: b['last_epoch_us'] += 1
        row['payload']['signature'] = base64.b64encode(KEY.sign(SIGNATURE_DOMAIN + _canonical(b)).signature).decode()
    with pytest.raises(NoisePurposeError): r.purpose.key_check(r.context.responder_noise_public_key, r.context.digest)
    assert r.purpose._disabled


def test_factory_observers_do_not_nest_setup_lease_and_handle_cannot_be_reused(purpose_rig):
    r = purpose_rig; _, handle = bind(r); before = r.lease_count
    start(r, handle)
    assert r.lease_count == before + 1  # Actual receiver crypto claim only.
    called = []
    with pytest.raises(NoisePurposeError): start(r, handle, lambda key: called.append(True))
    assert not called and r.session._closed


@pytest.mark.parametrize('failure', ['bad-result', 'post-factory-revocation', 'factory-reentry'])
def test_factory_failure_spends_handle_and_closes_registered_incarnation(purpose_rig, failure):
    r = purpose_rig; _, handle = bind(r)
    if failure == 'bad-result': action = lambda key: key
    elif failure == 'post-factory-revocation': action = factory(r, after=lambda: setattr(r, 'native', False))
    else:
        def action(key):
            with pytest.raises(NoisePurposeError): start(r, handle, lambda key: key)
            return key
    with pytest.raises(NoisePurposeError): start(r, handle, action)
    assert handle._used and r.purpose._key is None and r.purpose._disabled
    if failure == 'post-factory-revocation': assert r.session._closed


def test_forged_handle_and_thread_or_process_copy_cannot_recover_key(purpose_rig):
    r = purpose_rig; _, _ = bind(r)
    with pytest.raises(NoisePurposeError): start(r, NoiseKeyHandle(r.purpose), lambda key: key)
    assert r.purpose._disabled
    # A fresh original operation is used only to test PID/thread ownership;
    # the previous namespace is never deleted or reused.
    r.intention = replace(r.intention, operation_id=str(uuid.uuid4()))
    r.purpose = r.new_purpose(); info, _ = bind(r)
    errors = []
    def other_thread():
        try: r.purpose.key_check(info.public_key, r.context.digest)
        except NoisePurposeError: errors.append(True)
    thread = threading.Thread(target=other_thread); thread.start(); thread.join()
    assert errors == [True] and r.purpose._disabled


def test_retirement_after_expiry_preserves_original_facts_and_closes_crypto(purpose_rig):
    r = purpose_rig; _, handle = bind(r); start(r, handle)
    original = copy.deepcopy(body(r)['binding']); r.now = 181; r.mono = 131000000
    assert r.purpose.retire() and r.session._closed
    assert body(r)['binding'] == original and body(r)['retirement']['epoch_us'] == 181000000
    assert r.purpose.retire()  # Idempotent public cleanup, never reactivation.


def test_close_is_only_local_fence_not_durable_retirement(purpose_rig):
    r = purpose_rig; _, handle = bind(r); start(r, handle); before = copy.deepcopy(r.rows)
    assert r.purpose.close() and r.session._closed and r.rows == before
    assert body(r)['state'] == 'bound'


@pytest.mark.parametrize('cleanup', ['success', 'false', 'exception'])
def test_factory_raises_after_retaining_session_requires_abort_cleanup(purpose_rig, cleanup):
    r = purpose_rig; _, handle = bind(r)
    def create(key):
        factory(r)(key)
        raise RuntimeError('synthetic factory return lost')
    def cleanup_factory():
        abort(r)
        if cleanup == 'exception': raise RuntimeError('synthetic cleanup uncertainty')
        return cleanup == 'success'
    with pytest.raises(NoisePurposeError): handle.start(create, abort=cleanup_factory)
    assert r.session._closed and r.session._s is None and r.session._tx is None
    assert r.abort_calls == 1 and r.purpose._disabled and r.purpose._key is None
    assert r.purpose._close_failed == (cleanup != 'success') and r.purpose._uncertain
    count = r.generations
    with pytest.raises(NoisePurposeError): r.new_purpose().generate()
    assert r.generations == count


def test_caught_construction_fence_defers_abort_until_factory_has_exited(purpose_rig):
    r = purpose_rig; _, handle = bind(r); before = []
    def create(key):
        r.purpose.close()
        before.append(getattr(r, 'abort_calls', 0))
        # A bad factory can retain a later instance after catching cancellation.
        # The registered abort must see this later object, not run too early.
        r.session = ForwardSession(context=r.context, initiator=False, signing_key=KEY,
            noise_static_key=key, counters=SenderCounters(), authorize=lambda: True,
            clock=r.clock, application_deadline_monotonic_us=r.intention.application_deadline_monotonic_us)
        raise RuntimeError('synthetic late construction')
    with pytest.raises(NoisePurposeError): start(r, handle, create)
    assert before == [0] and r.abort_calls == 1 and r.session._closed and r.session._s is None


@pytest.mark.parametrize('mutation', ['context-object', 'context-json'])
def test_bound_context_cannot_diverge_from_signed_record_in_callback(purpose_rig, mutation):
    r = purpose_rig; info, _ = bind(r); before = copy.deepcopy(r.rows)
    def mutate():
        if mutation == 'context-object':
            r.purpose._context = replace(r.context, session_id=b'z' * 16)
        else:
            r.purpose._context_value['session_id'] = (b'z' * 16).hex()
    r.pin_hook = mutate
    with pytest.raises(NoisePurposeError): r.purpose.key_check(info.public_key, r.context.digest)
    assert r.rows == before and r.purpose._disabled and r.purpose._key is None
