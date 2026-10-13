# SPDX-License-Identifier: Apache-2.0
"""Fresh signed-record composition with modeled native/SDK IO; no native qualification."""
from dataclasses import replace
import copy
import uuid

import pytest

from meshticulum.privacy.forward_session import public
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner, ForwardNativeOwnerError
from lantern_meshticulum.sage.noise_native_enrollment import NativeNoiseEnrollmentError
from lantern_meshticulum.sage.noise_purpose import NoisePurposeError
from tests.integrations.sage.test_forward_native_owner import setup
from tests.integrations.sage.test_forward_native_provider import fixture as provider_fixture
from tests.integrations.sage.test_noise_native_enrollment import fixture as enrollment_fixture
from tests.integrations.sage.test_noise_purpose import purpose_rig, rig, bind


def enrollment_exchange(r, enrollment, info):
    return enrollment.exchange_check(info, r.context.responder, public(r.noise[1]), r.context.digest)


def test_combined_native_enrollment_reads_signed_record_again_on_every_call():
    with enrollment_fixture() as (r, p, info, record):
        record()
        saved = copy.deepcopy(r.state.rows)
        reads = []
        get = r.client.workflow_get
        def read(**args):
            reads.append(args['record_id'])
            return get(**args)
        r.client.workflow_get = read
        r.hardware.capture_lease = lambda *args: pytest.fail('observer cannot acquire a lease')
        for _ in range(2):
            assert enrollment_exchange(r, p, info) is True
        assert reads == [p.record_id, p.record_id] and r.state.rows == saved
        record(revision=2, state='revoked')
        with pytest.raises(NativeNoiseEnrollmentError):
            enrollment_exchange(r, p, info)
        assert reads == [p.record_id] * 3 and p._uncertain


@pytest.mark.parametrize('field', ['local_key', 'generation', 'setup_approval', 'intention',
                                  'remote_peer', 'remote_key', 'context'])
def test_combined_enrollment_checks_complete_local_public_and_exact_remote(field):
    with enrollment_fixture() as (r, p, info, record):
        record()
        args = [info, r.context.responder, public(r.noise[1]), r.context.digest]
        if field == 'local_key': args[0] = replace(info, public_key=public(r.noise[1]))
        elif field == 'generation': args[0] = replace(info, binding=replace(info.binding, generation_id=str(uuid.uuid4())))
        elif field == 'setup_approval': args[0] = replace(info, binding=replace(info.binding, approval_digest='f' * 64))
        elif field == 'intention':
            args[0] = replace(info, binding=replace(info.binding, intention=replace(r.intention,
                application_deadline_monotonic_us=r.intention.application_deadline_monotonic_us + 1)))
        elif field == 'remote_peer': args[1] = r.context.initiator
        elif field == 'remote_key': args[2] = info.public_key
        else: args[3] = b'f' * 32
        r.client.workflow_get = lambda **args: pytest.fail('mismatched pins must refuse before IO')
        with pytest.raises(NativeNoiseEnrollmentError):
            p.exchange_check(*args)


@pytest.mark.parametrize('change', ['signature', 'rollback', 'equivocation', 'record_binding'])
def test_combined_enrollment_preserves_signature_revision_and_binding_checks(change):
    with enrollment_fixture() as (r, p, info, record):
        first = record()
        assert enrollment_exchange(r, p, info)
        if change == 'signature': record(revision=2, sign=False)
        elif change == 'rollback':
            record(revision=2)
            assert enrollment_exchange(r, p, info)
            r.state.rows[p.record_id] = first
        elif change == 'equivocation': record(state='revoked')
        else: record(revision=2, change=lambda body: body['binding'].update(remote_public_key='f' * 64))
        with pytest.raises(NativeNoiseEnrollmentError):
            enrollment_exchange(r, p, info)
        assert p._uncertain


@pytest.mark.parametrize('change', ['reentry', 'owner', 'boot', 'storage', 'expiry', 'close'])
def test_combined_enrollment_refuses_drift_inside_signed_read(change):
    with enrollment_fixture() as (r, p, info, record):
        record()
        get = r.client.workflow_get
        def read(**args):
            result = get(**args)
            if change == 'reentry':
                with pytest.raises(NativeNoiseEnrollmentError): enrollment_exchange(r, p, info)
            elif change == 'owner': r.state.owner_live = False
            elif change == 'boot': r.state.boot = str(uuid.uuid4())
            elif change == 'storage': r.state.instance_id = 'f' * 64
            elif change == 'expiry': r.state.now = (r.context.expires_epoch_us, r.state.now[1])
            else: p.close()
            return result
        r.client.workflow_get = read
        with pytest.raises(NativeNoiseEnrollmentError):
            enrollment_exchange(r, p, info)
        assert p._uncertain


def purpose_exchange(r):
    return r.purpose.exchange_check(r.purpose._public.public_key, r.purpose.binding.remote,
                                   public(r.remote_key), r.context.digest)


def joint_purpose(r, hook=None):
    calls = []
    def observe(info, peer, key, digest):
        calls.append((info, peer, key, digest))
        if hook: hook()
        return (r.enrolled and r.remote_enrolled and info is r.purpose._public
                and peer == r.purpose.binding.remote and key == public(r.remote_key)
                and digest == r.context.digest)
    r.purpose = r.new_purpose(exchange_enrollment_check=observe)
    return calls


@pytest.mark.parametrize('joint', [False, True])
def test_purpose_exchange_keeps_default_callbacks_and_joint_path_fresh(purpose_rig, joint):
    r = purpose_rig
    calls = joint_purpose(r) if joint else []
    separate = []
    r.enrollment_hook = lambda: separate.append('local')
    r.pin_hook = lambda: separate.append('remote')
    info, _ = bind(r)
    calls.clear(); separate.clear()
    for _ in range(2): assert purpose_exchange(r)
    if joint:
        assert calls == [(info, r.purpose.binding.remote, public(r.remote_key), r.context.digest)] * 2
        assert separate == []
    else:
        assert separate == ['local', 'remote'] * 2 and calls == []
    r.remote_enrolled = False
    with pytest.raises(NoisePurposeError): purpose_exchange(r)
    assert r.purpose._key is None and r.purpose._uncertain


@pytest.mark.parametrize('field', ['local', 'remote', 'peer', 'digest', 'type'])
def test_purpose_combined_observer_validates_both_arguments(purpose_rig, field):
    r = purpose_rig
    joint_purpose(r)
    info, _ = bind(r)
    args = [info.public_key, r.purpose.binding.remote, public(r.remote_key), r.context.digest]
    if field == 'local': args[0] = public(r.remote_key)
    elif field == 'remote': args[2] = info.public_key
    elif field == 'peer': args[1] = r.purpose.binding.local
    elif field == 'digest': args[3] = b'f' * 32
    else: args[0] = bytearray(info.public_key)
    with pytest.raises(NoisePurposeError): r.purpose.exchange_check(*args)
    assert r.purpose._key is None


@pytest.mark.parametrize('joint', [False, True])
@pytest.mark.parametrize('inside', [False, True])
def test_purpose_optional_observer_mode_cannot_change_even_during_callback(purpose_rig, joint, inside):
    r = purpose_rig
    if joint: joint_purpose(r)
    bind(r)
    def change(): r.purpose._exchange_enrollment = None if joint else lambda *args: True
    if inside: r.native_hook = change
    else: change()
    with pytest.raises(NoisePurposeError): purpose_exchange(r)
    assert r.purpose._key is None and r.purpose._uncertain


@pytest.mark.parametrize('change', ['reentry', 'owner', 'storage', 'expiry', 'boot', 'observer_mode'])
def test_purpose_joint_callback_retains_post_callback_fences(purpose_rig, change):
    r = purpose_rig
    armed = []
    def mutate():
        if not armed: return
        if change == 'reentry':
            with pytest.raises(NoisePurposeError): purpose_exchange(r)
        elif change == 'owner': r.live = False
        elif change == 'storage': r.storage = False
        elif change == 'expiry': r.now = 180
        elif change == 'boot': r.boot = str(uuid.uuid4())
        else: r.purpose._exchange_enrollment = None
    joint_purpose(r, mutate)
    bind(r); armed.append(True)
    with pytest.raises(NoisePurposeError): purpose_exchange(r)
    assert r.purpose._key is None and r.purpose._uncertain


def native_owner(r, joint):
    calls = []
    local, remote = r.context.initiator_noise_public_key, r.context.responder_noise_public_key
    def key(p, d):
        calls.append('local')
        return p == local and d == r.context.digest
    def peer(p, k, d):
        calls.append('remote')
        return p == r.context.responder and k == remote and d == r.context.digest
    def exchange(p, peer, k, d):
        calls.append('exchange')
        return p == local and peer == r.context.responder and k == remote and d == r.context.digest
    owner = ForwardNativeOwner(r.hardware, r.authority, r.native_context,
        context=r.context, intention=r.intention, local_initiator=True,
        key_check=key, peer_key_check=peer, boot_reader=lambda: r.state.boot,
        clock_reader=lambda: r.state.now, exchange_key_check=exchange if joint else None)
    calls.clear()
    return owner, calls


@pytest.mark.parametrize('joint', [False, True])
def test_owner_joint_boundary_is_single_but_key_check_outer_brackets_stay(joint):
    with setup() as r:
        owner, calls = native_owner(r, joint)
        assert owner.access_check()
        assert calls == (['exchange'] if joint else ['local', 'remote'])
        calls.clear()
        assert owner.key_check(r.context.initiator_noise_public_key, r.context.digest)
        assert calls == (['exchange', 'local', 'exchange'] if joint else ['local', 'remote', 'local', 'local', 'remote'])
        assert r.state.rows == {} and not r.hardware._mutex.locked()


@pytest.mark.parametrize('joint', [False, True])
@pytest.mark.parametrize('inside', [False, True])
def test_owner_optional_observer_mode_is_pinned_including_none(joint, inside):
    with setup() as r:
        owner, _ = native_owner(r, joint)
        def change(): owner._exchange_key_check = None if joint else lambda *args: True
        if inside:
            configured = r.hardware.configured
            def during():
                result = configured(); change(); return result
            r.hardware.configured = during
        else: change()
        with pytest.raises(ForwardNativeOwnerError): owner.access_check()
        assert owner._uncertain and not r.state.rows


def test_owner_joint_callback_cannot_substitute_its_own_mode_before_return():
    with setup() as r:
        holder = {}
        def exchange(local, peer, remote, digest):
            assert local == r.context.initiator_noise_public_key and peer == r.context.responder
            assert remote == r.context.responder_noise_public_key and digest == r.context.digest
            if holder: holder['owner']._exchange_key_check = None
            return True
        owner = ForwardNativeOwner(r.hardware, r.authority, r.native_context,
            context=r.context, intention=r.intention, local_initiator=True,
            key_check=lambda *args: pytest.fail('joint observer must be selected'),
            peer_key_check=lambda *args: pytest.fail('joint observer must be selected'),
            exchange_key_check=exchange, boot_reader=lambda: r.state.boot, clock_reader=lambda: r.state.now)
        holder['owner'] = owner
        with pytest.raises(ForwardNativeOwnerError): owner.access_check()
        assert owner._uncertain and not r.state.rows


@pytest.mark.parametrize('initiator', [True, False])
def test_provider_owner_boundary_reads_one_purpose_and_one_enrollment_each_time(initiator):
    with provider_fixture(initiator=initiator) as (r, p, source, bind_provider):
        context, _ = bind_provider()
        owner = ForwardNativeOwner(r.hardware, r.authority, p._exchange,
            context=context, intention=p.intention, local_initiator=initiator,
            key_check=p._purpose.key_check, peer_key_check=p._purpose.peer_key_check,
            exchange_key_check=p._purpose.exchange_check, boot_reader=p._boot, clock_reader=p._clock)
        reads = []
        get = r.client.workflow_get
        def read(**args):
            reads.append(args['record_id'])
            return get(**args)
        r.client.workflow_get = read
        saved = copy.deepcopy(r.state.rows)
        previous = source.observations
        for _ in range(2): assert owner.access_check()
        assert reads == [p._purpose.record_id, p._enrollment.record_id] * 2
        assert source.observations > previous and r.state.rows == saved
        # A later signed-row change cannot reuse the earlier complete observation.
        r.state.rows[p._enrollment.record_id]['payload']['signature'] = 'AA=='
        with pytest.raises(ForwardNativeOwnerError): owner.access_check()
        assert owner._uncertain and p._purpose._key is None
