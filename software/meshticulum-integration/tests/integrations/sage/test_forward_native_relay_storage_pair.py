# SPDX-License-Identifier: Apache-2.0
"""One fresh paired relay read; SDK, lifecycle and accounting are source models."""
from contextlib import contextmanager
import copy
from functools import partial
import json

import pytest

from lantern_meshticulum.sage.bound_storage_pair import BoundStoragePair, BoundStoragePairError
from lantern_meshticulum.sage.forward_native_relay import ForwardNativeRelay, ForwardNativeRelayError, RELAY_SCOPES
from tests.integrations.sage import test_forward_native_relay as fixture


@contextmanager
def relay_mode(path, factory=BoundStoragePair):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fixture, 'ForwardNativeRelay', partial(ForwardNativeRelay, storage_pair_factory=factory))
        with fixture.relay_rig(path) as r:
            requests = []
            r.client._client.event_hooks['response'].append(lambda response: requests.append(response.request.url.path))
            yield r, requests


@pytest.mark.parametrize('factory,expected', [(None, 4), (BoundStoragePair, 2)])
def test_each_access_keeps_accounting_and_both_scope_bookends_with_fewer_storage_reads(
        tmp_path, monkeypatch, record_property, factory, expected):
    with relay_mode(tmp_path, factory) as (r, requests):
        scopes = []; original = r.authority.authorized
        def authorized(context, scope, action):
            scopes.append(scope); return original(context, scope, action)
        monkeypatch.setattr(r.authority, 'authorized', authorized)
        debt = r.owner._retained_debt; binding = copy.deepcopy(r.owner._accounting_binding)
        for _ in range(2):
            requests.clear(); scopes.clear(); accounting = r.accounting.calls
            assert r.owner.access_check() is True
            assert requests == ['/v1/messages/storage'] * expected
            assert scopes == list(RELAY_SCOPES) * 2 and r.accounting.calls == accounting + 1
            assert r.owner._retained_debt is debt and r.owner._accounting_binding == binding
            assert r.accounting.actual is None and not r.state.rows and not r.hardware._mutex.locked()
        record_property('storage_status_calls_per_access', expected)
        record_property('accounting_observations_per_access', 1)
        record_property('scope_checks_per_access', len(RELAY_SCOPES) * 2)


@pytest.mark.parametrize('factory', [None, BoundStoragePair])
@pytest.mark.parametrize('field', ['factory', 'pair', 'callback', 'delegates', 'matches'])
def test_original_pair_mode_and_callback_cannot_change_before_dispatch(tmp_path, factory, field):
    with relay_mode(tmp_path, factory) as (r, requests):
        before = copy.deepcopy(r.state.rows)
        if field == 'factory': r.owner._storage_pair_factory = BoundStoragePair if factory is None else None
        elif field == 'pair': r.owner._storage_pair = object()
        elif field == 'callback': r.owner._storage_pair_check = lambda *a: r.owner.storage_epoch
        elif field == 'delegates': r.owner._storage_pair_delegates = ()
        else: r.owner._storage_pair_matches = lambda *a: True
        with pytest.raises(ForwardNativeRelayError): r.owner.access_check()
        assert not requests and r.state.rows == before and r.owner._uncertain


@pytest.mark.parametrize('delegate', ['matches', 'observe'])
def test_original_private_delegate_is_checked_before_it_can_run(tmp_path, delegate):
    with relay_mode(tmp_path) as (r, requests):
        called = []; pair = r.owner._storage_pair
        object.__setattr__(pair, '_BoundStoragePair__' + delegate,
                           lambda *a: called.append(True) or r.owner.storage_epoch)
        with pytest.raises(ForwardNativeRelayError): r.owner.access_check()
        assert not called and not requests and not r.state.rows


@pytest.mark.parametrize('method', ['matches', 'observe'])
def test_original_public_pair_method_is_checked_before_any_dispatch(tmp_path, monkeypatch, method):
    with relay_mode(tmp_path) as (r, requests):
        called = []
        with monkeypatch.context() as drift:
            drift.setattr(BoundStoragePair, method, lambda *a: called.append(True) or r.owner.storage_epoch)
            with pytest.raises(ForwardNativeRelayError): r.owner.access_check()
            assert not called and not requests and not r.state.rows


@pytest.mark.parametrize('changed', ['native_chain', 'integration_actor', 'native_client', 'inner_transport'])
def test_original_probe_actor_and_client_bindings_cannot_be_substituted(tmp_path, monkeypatch, changed):
    with relay_mode(tmp_path) as (r, requests):
        with monkeypatch.context() as drift:
            if changed == 'native_chain': object.__setattr__(r.hardware.probe.actor, 'chain_id', 'foreign')
            elif changed == 'integration_actor':
                from dataclasses import replace
                r.owner._store.storage.actor = replace(r.owner._store.storage.actor)
            elif changed == 'native_client': r.hardware.probe.client = object()
            else: drift.setattr(r.client._client, '_transport', object())
            with pytest.raises(ForwardNativeRelayError): r.owner.access_check()
            assert not requests and not r.state.rows


@pytest.mark.parametrize('field,value', [('schema', 'foreign'), ('agent_id', 'f' * 64),
    ('instance_id', 'f' * 64), ('vault_generation', '2'), ('stable', False), ('vault_active', False),
    ('canonical_send_idempotency', False), ('encrypted_send_admission', False), ('encryption_expected', False)])
def test_storage_epoch_and_all_capabilities_remain_fresh_after_a_successful_access(tmp_path, field, value):
    with relay_mode(tmp_path) as (r, requests):
        assert r.owner.access_check() is True; requests.clear()
        def changed(response):
            body = response.json(); body[field] = value; response._content = json.dumps(body).encode()
        r.client._client.event_hooks['response'].append(changed)
        before = r.accounting.calls
        with pytest.raises(ForwardNativeRelayError): r.owner.access_check()
        assert requests == ['/v1/messages/storage'] and r.accounting.calls == before
        assert not r.state.rows and r.accounting.actual is None and r.owner._uncertain


@pytest.mark.parametrize('changed', ['delegate_matches', 'delegate_observe', 'inner_transport', 'reentry'])
def test_callback_drift_and_caught_reentry_cannot_publish_the_observation(tmp_path, monkeypatch, changed):
    with relay_mode(tmp_path) as (r, requests):
        with monkeypatch.context() as drift:
            def mutation(response):
                if changed.startswith('delegate_'):
                    object.__setattr__(r.owner._storage_pair, '_BoundStoragePair__' + changed.split('_')[1],
                                       lambda *a: r.owner.storage_epoch)
                elif changed == 'inner_transport': drift.setattr(r.client._client, '_transport', object())
                else:
                    with pytest.raises(ForwardNativeRelayError): r.owner.access_check()
            r.client._client.event_hooks['response'].append(mutation)
            with pytest.raises(ForwardNativeRelayError): r.owner.access_check()
            assert requests == ['/v1/messages/storage'] and not r.state.rows and r.owner._uncertain


@pytest.mark.parametrize('changed', ['lifecycle', 'supervisor_epoch', 'scope', 'debt', 'unknown_usage'])
def test_independent_lifecycle_scope_and_original_accounting_checks_remain_required(tmp_path, changed):
    with relay_mode(tmp_path) as (r, requests):
        before = copy.deepcopy(r.owner._accounting_binding)
        if changed == 'lifecycle': r.hardware.supervisor.resource_lifecycle_check = lambda: False
        elif changed == 'supervisor_epoch':
            r.hardware.supervisor.verified_epoch = lambda: 'f' * 64 + ':1'
            r.hardware.supervisor.admission_epoch = lambda: 'f' * 64 + ':1'
        elif changed == 'scope': r.state.owner_live = False
        elif changed == 'debt': r.accounting.debt = fixture.DEBT.plus(fixture.Cost(1, 1))
        else: r.accounting.actual = fixture.Cost()
        with pytest.raises(ForwardNativeRelayError): r.owner.access_check()
        assert not r.state.rows and r.owner._accounting_binding == before and r.owner._uncertain
        if changed in ('lifecycle', 'supervisor_epoch'): assert not requests


@pytest.mark.parametrize('factory', [lambda *a: None, type('UnknownPair', (BoundStoragePair,), {})])
def test_unknown_pair_factory_is_refused_before_relay_construction(tmp_path, factory):
    with pytest.raises(ForwardNativeRelayError):
        with relay_mode(tmp_path, factory): pytest.fail('foreign pair factory accepted')


def test_exact_pair_constructor_refusal_closes_original_accounting_once(tmp_path):
    from dataclasses import replace
    held = []

    class CloseCount(fixture.Accounting):
        def __init__(self, r):
            super().__init__(r)
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            return super().close()

    def mismatch(r):
        held.append(r)
        # Both probes remain their exact required types. Their actors differ.
        r.hardware.probe.actor = replace(r.hardware.actor, chain_id='foreign')

    with pytest.raises(ForwardNativeRelayError) as refused:
        with fixture.relay_rig(tmp_path, accounting_factory=CloseCount, before_create=mismatch):
            pytest.fail('mismatched actors accepted by storage pair')
    assert type(refused.value.__context__) is BoundStoragePairError
    r, = held
    assert r.accounting.closed and r.accounting.close_calls == 1
    assert r.accounting.debt is fixture.DEBT and r.accounting.actual is None
    assert r.owner is None and not r.factories and not r.state.rows
