# SPDX-License-Identifier: Apache-2.0
"""Fresh same-client pairing; native lifecycle/backend/transport are models."""
import ast
import base64
from contextlib import contextmanager
import copy
from dataclasses import replace
from functools import partial
import inspect
import json
import textwrap

import pytest
pytest.importorskip('sage_sdk', reason='compatible optional SDK required')

from tests.integrations.sage.test_forward_native_owner import setup
from sage_lantern.message_storage import MessageStorageProbe as NativeProbe
from lantern_meshticulum.sage.message_contacts import RecipientID
from lantern_meshticulum.sage.message_storage import MessageStorageProbe, MessageStorageSnapshot
from lantern_meshticulum.sage.workflow_store import WorkflowStore
from lantern_meshticulum.sage.bound_storage_pair import BoundStoragePair, BoundStoragePairError
from lantern_meshticulum.sage.forward_native_provider import ForwardNativeProvider, ForwardNativeProviderError
from lantern_meshticulum.sage.noise_native_enrollment import NativeNoiseEnrollmentError
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwnerError
from lantern_meshticulum.sage.mesh_inbox import _canonical
from lantern_meshticulum.sage import noise_native_enrollment, noise_purpose
from tests.integrations.sage import test_forward_native_provider as provider_fixture


@pytest.fixture
def paired():
    with setup() as r:
        probe = MessageStorageProbe(r.client,
            actor=RecipientID(r.hardware.actor.chain_id, r.hardware.actor.agent_id), endpoint=r.hardware.probe.endpoint)
        store = WorkflowStore(r.client, storage=probe)
        pair = BoundStoragePair(r.hardware.probe, store)
        requests = []
        r.client._client.event_hooks['response'].append(lambda response: requests.append(response.request.url.path))
        yield r, store, pair, r.state.instance_id + ':1', requests


@contextmanager
def provider_mode(factory=BoundStoragePair):
    # Select constructor data through the dedicated fixture's alias only;
    # production class/methods and the real grant/enrollment path stay intact.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(provider_fixture, 'ForwardNativeProvider',
                      partial(ForwardNativeProvider, storage_pair_factory=factory))
        with provider_fixture.fixture() as values: yield values


def test_native_and_integration_bindings_and_nine_field_parsers_are_identical():
    for name in ('_binding', 'snapshot'):
        trees = [ast.dump(ast.parse(textwrap.dedent(inspect.getsource(getattr(cls, name)))),
                          include_attributes=False) for cls in (NativeProbe, MessageStorageProbe)]
        assert trees[0] == trees[1]
    snapshot = ast.parse(textwrap.dedent(inspect.getsource(MessageStorageProbe.snapshot)))
    fields = next(n.value for n in ast.walk(snapshot) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'fields' for t in n.targets))
    assert len(fields.elts) == 9


def test_each_observation_reads_once_and_retains_no_positive_result(paired):
    r, store, pair, epoch, requests = paired
    assert pair.matches(r.hardware.probe, store) is True
    assert pair.observe(epoch) == epoch and pair.observe(epoch) == epoch
    assert requests == ['/v1/messages/storage'] * 2 and not r.state.rows
    assert not hasattr(pair, '__dict__') and not hasattr(pair, 'epoch')
    with pytest.raises(BoundStoragePairError): pair.cached_epoch = epoch
    with pytest.raises(BoundStoragePairError): pair.observe(epoch + '0')
    count = len(requests)
    with pytest.raises(BoundStoragePairError): pair.observe(epoch)
    assert len(requests) == count


@pytest.mark.parametrize('changed', ['native_chain', 'integration_chain', 'native_agent',
    'actor_ref', 'store_actor_ref', 'endpoint_type', 'native_client', 'transport', 'inner_transport', 'identity'])
def test_original_actor_client_endpoint_and_held_objects_cannot_be_substituted(paired, changed, monkeypatch):
    r, store, pair, epoch, requests = paired
    if changed == 'native_chain': object.__setattr__(r.hardware.probe.actor, 'chain_id', 'other-chain')
    elif changed == 'integration_chain': object.__setattr__(store.storage.actor, 'chain_id', 'other-chain')
    elif changed == 'native_agent': object.__setattr__(r.hardware.probe.actor, 'agent_id', 'f' * 64)
    elif changed == 'actor_ref': store.storage.actor = replace(store.storage.actor)
    elif changed == 'store_actor_ref': store._actor = replace(store._actor)
    elif changed == 'endpoint_type':
        class EqualString(str): pass
        store.storage.endpoint = EqualString(store.storage.endpoint)
    elif changed == 'native_client': r.hardware.probe.client = object()
    elif changed == 'transport': monkeypatch.setattr(r.client, '_client', object())
    elif changed == 'inner_transport': monkeypatch.setattr(r.client._client, '_transport', object())
    else: monkeypatch.setattr(r.client, '_identity', object())
    with pytest.raises(BoundStoragePairError): pair.observe(epoch)
    assert not requests and not r.state.rows


@pytest.mark.parametrize('target', ['native_binding', 'native_snapshot', 'integration_binding',
                                   'integration_snapshot', 'sdk_status', 'epoch_property'])
def test_original_parser_and_dispatch_methods_are_pinned(paired, target, monkeypatch):
    r, store, pair, epoch, requests = paired
    if target.startswith('native_'):
        monkeypatch.setattr(NativeProbe, '_binding' if target.endswith('binding') else 'snapshot', lambda *a: True)
    elif target.startswith('integration_'):
        monkeypatch.setattr(MessageStorageProbe, '_binding' if target.endswith('binding') else 'snapshot', lambda *a: True)
    elif target == 'sdk_status': monkeypatch.setattr(r.client, 'message_storage_status', lambda: True)
    else: monkeypatch.setattr(MessageStorageSnapshot, 'epoch', property(lambda _: epoch))
    with pytest.raises(BoundStoragePairError): pair.observe(epoch)
    assert not requests


@pytest.mark.parametrize('field,value', [('schema', 'unknown'), ('agent_id', 'f' * 64),
    ('instance_id', 'f' * 64), ('vault_generation', '2'), ('stable', False), ('vault_active', False),
    ('canonical_send_idempotency', False), ('encrypted_send_admission', False), ('encryption_expected', False)])
def test_current_epoch_and_every_storage_capability_are_checked(paired, field, value):
    r, store, pair, epoch, requests = paired
    def changed(response):
        body = response.json(); body[field] = value
        response._content = json.dumps(body).encode()
    r.client._client.event_hooks['response'].append(changed)
    with pytest.raises(BoundStoragePairError): pair.observe(epoch)
    assert len(requests) == 1 and not r.state.rows


@pytest.mark.parametrize('mutation', ['actor_chain', 'inner_transport', 'delegate_observe', 'delegate_matches', 'reentry'])
def test_sdk_callback_drift_and_caught_reentry_cannot_publish_a_status(paired, mutation, monkeypatch):
    r, store, pair, epoch, requests = paired
    def changed(response):
        if mutation == 'actor_chain': object.__setattr__(store.storage.actor, 'chain_id', 'other-chain')
        elif mutation == 'inner_transport': monkeypatch.setattr(r.client._client, '_transport', object())
        elif mutation.startswith('delegate_'):
            object.__setattr__(pair, '_BoundStoragePair__' + mutation.split('_')[1], lambda *a: epoch)
        else:
            with pytest.raises(BoundStoragePairError): pair.observe(epoch)
    r.client._client.event_hooks['response'].append(changed)
    with pytest.raises(BoundStoragePairError): pair.observe(epoch)
    assert len(requests) == 1 and not r.state.rows


@pytest.mark.parametrize('mutation', ['chain', 'transport'])
def test_final_binding_timeout_callback_is_fenced_after_it_returns(paired, mutation, monkeypatch):
    r, store, pair, epoch, requests = paired
    original = r.client._client.timeout.as_dict; replied = [False]; after = [0]
    r.client._client.event_hooks['response'].append(lambda response: replied.__setitem__(0, True))
    def changed():
        value = original()
        if replied[0]:
            after[0] += 1
            if after[0] == 3:  # snapshot's binding, then native + integration final bindings.
                if mutation == 'chain': object.__setattr__(store.storage.actor, 'chain_id', 'other-chain')
                else: monkeypatch.setattr(r.client._client, '_transport', object())
        return value
    monkeypatch.setattr(r.client._client.timeout, 'as_dict', changed)
    with pytest.raises(BoundStoragePairError): pair.observe(epoch)
    assert len(requests) == 1 and after[0] == 3 and not r.state.rows


@pytest.mark.parametrize('factory,expected', [(None, 35), (BoundStoragePair, 23)])
def test_real_joint_key_observer_keeps_records_and_sdk_epoch_brackets_with_fewer_status_reads(factory, expected):
    with provider_mode(factory) as (r, provider, source, bind):
        bind(); operation = provider.start(r.packet); owner = provider.owner
        requests = []
        r.client._client.event_hooks['response'].append(lambda response: requests.append(response.request.url.path))
        args = (owner.context, owner.intention, owner.remote, owner.context.initiator_noise_public_key, owner.context.digest)
        assert owner.crypto_action_check(*args) is True
        assert len(requests) == expected and requests.count('/v1/messages/storage') == expected - 2
        assert sum(path != '/v1/messages/storage' for path in requests) == 2
        assert (provider._storage_pair is None) == (factory is None)
        assert provider._enrollment._storage_pair is provider._storage_pair
        assert operation.budget.prior == provider_fixture.DEBT and source.actual is None


@pytest.mark.parametrize('consumer', ['provider', 'enrollment'])
@pytest.mark.parametrize('delegate', ['matches', 'observe'])
def test_consumer_checks_original_private_delegate_before_any_dispatch(consumer, delegate):
    with provider_mode() as (r, provider, source, bind):
        bind(); pair = provider._storage_pair; called = []
        object.__setattr__(pair, '_BoundStoragePair__' + delegate, lambda *a: called.append(True) or provider.storage_epoch)
        if consumer == 'provider':
            with pytest.raises(ForwardNativeProviderError): provider._storage_check()
        else:
            with pytest.raises(NativeNoiseEnrollmentError):
                provider._enrollment.exchange_check(provider._public, provider.remote,
                    provider._context.responder_noise_public_key, provider._context.digest)
        assert not called


@pytest.mark.parametrize('factory', [lambda *a: None, type('PairSubclass', (BoundStoragePair,), {})])
def test_unknown_pair_factory_is_refused_before_any_key_or_record(factory):
    with pytest.raises(ForwardNativeProviderError):
        with provider_mode(factory): pytest.fail('unknown factory accepted')


@pytest.mark.parametrize('factory', [None, BoundStoragePair])
@pytest.mark.parametrize('field', ['factory', 'callback'])
def test_provider_mode_and_callback_including_none_cannot_change(factory, field):
    with provider_mode(factory) as (r, provider, source, bind):
        bind(); before = copy.deepcopy(r.state.rows); requests = []
        r.client._client.event_hooks['response'].append(lambda response: requests.append(True))
        if field == 'factory': provider._storage_pair_factory = BoundStoragePair if factory is None else None
        else: provider._storage_pair_check = lambda *a: provider.storage_epoch
        with pytest.raises(ForwardNativeProviderError): provider._storage_check()
        assert not requests and r.state.rows == before and source.closed


@pytest.mark.parametrize('drift', ['lifecycle', 'supervisor_epoch'])
def test_independent_native_supervisor_check_remains_required_before_storage_io(drift):
    with provider_mode() as (r, provider, source, bind):
        bind(); before = copy.deepcopy(r.state.rows); requests = []
        r.client._client.event_hooks['response'].append(lambda response: requests.append(True))
        if drift == 'lifecycle': r.supervisor.resource_lifecycle_check = lambda: False
        else: r.supervisor.verified_epoch = lambda: 'f' * 64 + ':1'
        with pytest.raises(ForwardNativeProviderError): provider._storage_check()
        assert not requests and r.state.rows == before and source.closed


def test_interruption_propagates_and_cannot_leave_a_reusable_observation(paired):
    r, store, pair, epoch, requests = paired
    def interrupted(response): raise KeyboardInterrupt()
    r.client._client.event_hooks['response'].append(interrupted)
    with pytest.raises(KeyboardInterrupt): pair.observe(epoch)
    with pytest.raises(BoundStoragePairError): pair.observe(epoch)
    assert len(requests) == 1 and not r.state.rows


@pytest.mark.parametrize('kind', ['purpose', 'enrollment'])
def test_paired_observer_rereads_signed_revocation_with_unchanged_paid_debt(kind):
    with provider_mode() as (r, provider, source, bind):
        bind(); operation = provider.start(r.packet); owner = provider.owner
        component = provider._purpose if kind == 'purpose' else provider._enrollment
        row = r.state.rows[component.record_id]; row['revision'] += 1
        body = row['payload']['body']; body['revision'] = row['revision']
        if kind == 'purpose':
            body.update(state='retired', retirement=dict(epoch_us=body['last_epoch_us'], monotonic_us=body['last_monotonic_us']))
            domain = noise_purpose.SIGNATURE_DOMAIN
        else: body['state'] = 'revoked'; domain = noise_native_enrollment.SIGNATURE_DOMAIN
        row['payload']['signature'] = base64.b64encode(
            r.hardware.identity._signing_key.sign(domain + _canonical(body)).signature).decode()
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardNativeOwnerError):
            operation.session.authorize()
        assert r.state.rows == before and operation.budget.prior == provider_fixture.DEBT
        assert source.actual is None and not r.hardware._mutex.locked()
