# SPDX-License-Identifier: Apache-2.0
"""Aggregate action controls; native/backend/RF IO remain explicit models."""
import base64
import copy
from dataclasses import replace
import uuid

import pytest
pytest.importorskip('sage_sdk', reason='compatible optional SDK required')

from lantern_meshticulum.sage.forward_receiver import ForwardReceiver, ForwardReceiverError
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_mesh_inbox import rig, KEY
from tests.integrations.sage.test_forward_receive_journal import journal_rig
from tests.integrations.sage.test_forward_receiver import receiver_rig


@pytest.fixture
def boundary(receiver_rig):
    r = receiver_rig
    r.full_calls = []
    r.aggregate_calls = []
    r.hook = None
    r.allowed = True

    def full(name, value):
        r.full_calls.append(name)
        return value

    def aggregate(*arguments):
        r.aggregate_calls.append(arguments)
        if r.hook is not None:
            r.hook()
        return r.allowed

    def new(callback=aggregate):
        return ForwardReceiver(r.store.client, context=r.context, local_initiator=False,
            intention=r.intention, boot_id=r.receiver.boot_id, endpoint=r.store.client._base_url,
            signing_key=KEY, owner_lease=r.journal._lease,
            access_check=lambda: full('access', r.live),
            storage_check=lambda: full('storage', r.storage),
            peer_check=lambda p: full('peer', p == r.peer),
            dispatch_check=lambda c, i: full('dispatch', r.permission),
            read_check=lambda c, i: full('read', r.read_ok),
            key_check=lambda p, d: full('purpose', r.purpose), clock=r.clock,
            crypto_action_check=callback)

    r.aggregate, r.new_aggregate = aggregate, new
    return r


@pytest.mark.parametrize('callback', [False, 1, object()])
def test_invalid_aggregate_callback_is_refused_without_storage(boundary, callback):
    with pytest.raises(ForwardReceiverError): boundary.new_aggregate(callback)
    assert not boundary.rows


def test_none_default_keeps_all_independent_guards_in_original_order(boundary):
    r = boundary
    receiver = r.new_aggregate(None)
    assert receiver._authorize_crypto() is True
    assert r.full_calls == ['access', 'read', 'storage', 'peer', 'dispatch', 'purpose']
    assert not r.aggregate_calls and not r.rows
    r.read_ok = False
    with pytest.raises(ForwardReceiverError): receiver.create_session(r.noise)
    assert not r.rows


def test_one_fresh_aggregate_per_call_has_exact_original_arguments(boundary):
    r = boundary
    receiver = r.new_aggregate()
    for _ in range(2):
        assert receiver._authorize_crypto() is True
        assert not receiver._crypto_observing
    assert len(r.aggregate_calls) == 2 and not r.full_calls
    for context, intention, peer, key, digest in r.aggregate_calls:
        assert context is r.context and intention is r.intention and peer is r.context.initiator
        assert key == r.context.responder_noise_public_key and digest == r.context.digest
    r.allowed = False
    with pytest.raises(ForwardReceiverError): receiver.create_session(r.noise)
    assert len(r.aggregate_calls) == 3 and not r.rows and not receiver._crypto_observing


@pytest.mark.parametrize('original_none', [True, False])
def test_callback_identity_including_none_cannot_be_changed(boundary, original_none):
    r = boundary
    receiver = r.new_aggregate(None if original_none else r.aggregate)
    receiver._crypto_action = r.aggregate if original_none else None
    with pytest.raises(ForwardReceiverError): receiver.create_session(r.noise)
    assert not r.aggregate_calls and not r.full_calls and not r.rows


@pytest.mark.parametrize('field', ['context', 'intention', 'remote', 'local', 'local_initiator'])
def test_original_argument_identity_cannot_be_replaced_before_observation(boundary, field):
    r = boundary
    receiver = r.new_aggregate()
    value = dict(context=replace(r.context), intention=replace(r.intention), remote=receiver.local,
                 local=receiver.remote, local_initiator=True)[field]
    setattr(receiver, field, value)
    with pytest.raises(ForwardReceiverError): receiver._authorize_crypto()
    assert not r.aggregate_calls and not r.rows


@pytest.mark.parametrize('mutation', ['context_value', 'intention_value', 'callback', 'endpoint',
                                     'close', 'reenter_authorize', 'reenter_import', 'raise'])
def test_callback_drift_or_caught_reentry_refuses_before_claim_and_cleans_up(boundary, mutation):
    r = boundary
    receiver = r.new_aggregate()
    seen = []

    def hook():
        if mutation == 'context_value':
            object.__setattr__(r.context, 'max_records', r.context.max_records + 1)
        elif mutation == 'intention_value':
            object.__setattr__(r.intention, 'application_deadline_monotonic_us',
                               r.intention.application_deadline_monotonic_us + 1)
        elif mutation == 'callback': receiver._crypto_action = lambda *a: True
        elif mutation == 'endpoint': receiver.client._base_url = 'http://127.0.0.1:19091'
        elif mutation == 'close': seen.append(receiver.close())
        elif mutation == 'raise': raise RuntimeError('modeled callback failure')
        else:
            with pytest.raises(ForwardReceiverError):
                receiver._authorize_crypto() if mutation == 'reenter_authorize' else receiver.import_pending()

    r.hook = hook
    with pytest.raises(ForwardReceiverError): receiver.create_session(r.noise)
    assert len(r.aggregate_calls) == 1 and not r.rows and not receiver._crypto_observing
    assert receiver._uncertain and not r.store.client._client.is_closed
    if mutation == 'close': assert seen == [False]


@pytest.mark.parametrize('record_kind', ['purpose', 'enrollment'])
def test_actual_signed_revocation_is_read_fresh_by_aggregate_and_keeps_original_debt(record_kind):
    from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwnerError
    from lantern_meshticulum.sage import noise_native_enrollment, noise_purpose
    from tests.integrations.sage.test_forward_native_provider import fixture, DEBT
    with fixture() as (r, provider, source, bind):
        bind()
        operation = provider.start(r.packet)
        receiver = provider._owner._composition
        assert receiver._crypto_action is not None
        assert operation.session.authorize() is True
        component = provider._purpose if record_kind == 'purpose' else provider._enrollment
        row = r.state.rows[component.record_id]
        row['revision'] += 1
        body = row['payload']['body']
        body['revision'] = row['revision']
        if record_kind == 'purpose':
            body.update(state='retired', retirement=dict(epoch_us=body['last_epoch_us'],
                                                        monotonic_us=body['last_monotonic_us']))
            domain = noise_purpose.SIGNATURE_DOMAIN
        else:
            body['state'] = 'revoked'
            domain = noise_native_enrollment.SIGNATURE_DOMAIN
        row['payload']['signature'] = base64.b64encode(
            r.hardware.identity._signing_key.sign(domain + _canonical(body)).signature).decode()
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardNativeOwnerError): operation.session.authorize()
        assert r.state.rows == before and operation.budget.prior == DEBT and source.actual is None
        assert not receiver._crypto_observing and not r.hardware._mutex.locked()
        assert provider._purpose._key is None


def test_actual_full_approval_exit_refuses_aggregate_without_new_claim_or_counter():
    from sage_lantern.mesh_community import MeshRequestAccess
    from lantern_meshticulum.sage.forward_native_owner import approval_binding, ForwardNativeOwnerError
    from lantern_meshticulum.sage.forward_native_provider import SETUP_SCOPES
    from tests.integrations.sage.test_forward_native_provider import fixture
    with fixture() as (r, provider, source, bind):
        info = provider.generate()
        context = replace(r.context, initiator_noise_public_key=info.public_key)
        digest = approval_binding(context, r.intention, True, config_revision=provider.config_revision,
            broker_id=provider.broker_id, storage_epoch=provider.storage_epoch)
        with r.authority.grant(r.hardware.actor,
                MeshRequestAccess(r.state.epoch, lambda: r.state.owner_live), SETUP_SCOPES,
                approval_binding=digest) as native:
            provider.bind(context, native, remote_generation_id=str(uuid.uuid4()))
            operation = provider.start(r.packet)
            assert operation.session.authorize() is True
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardNativeOwnerError): operation.session.authorize()
        assert r.state.rows == before and not provider._owner._composition._crypto_observing
        assert not r.hardware._mutex.locked() and not r.client._client.is_closed
