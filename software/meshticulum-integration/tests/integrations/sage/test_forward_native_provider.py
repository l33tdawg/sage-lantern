# SPDX-License-Identifier: Apache-2.0
"""Real authority/enrollment/crypto composition; native and SDK IO modeled.

No device, socket, production store or native qualification. The mandatory
accounting test source is explicitly modeled, never deployment evidence.
"""
from contextlib import contextmanager, ExitStack
from dataclasses import asdict, replace
import base64
import copy
import hashlib
import uuid

import pytest

from tests.integrations.sage.test_forward_native_owner import setup
from sage_lantern.mesh_community import MeshRequestAccess
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_native_owner import approval_binding
from lantern_meshticulum.sage.forward_native_provider import (
    ForwardNativeProvider, ForwardNativeProviderError, NativeAccountingSource,
    NativeAccountingObservation, SETUP_SCOPES, setup_approval_binding,
)
from lantern_meshticulum.sage.mesh_inbox import _canonical

LIMITS = Cost(2000, 300000000, 2000, 100000, 100000)
DEBT = Cost(17, 200000, 5, 100, 200)


class ModeledAccounting(NativeAccountingSource):
    def __init__(self, r):
        self.r = r
        self.debt = DEBT
        self.actual = None
        self.evidence = 'e' * 64
        self.boot = r.state.boot
        self.clock_offset = 0
        self.closed = False
        self.close_calls = 0
        self.close_result = True
        self.observations = 0
        self.provider = None
        self.hook = None

    def observe(self, resource, intention):
        assert not self.closed and resource is self.r.hardware and intention is self.r.intention
        self.observations += 1
        if self.hook:
            self.hook()
        return NativeAccountingObservation(self.debt, self.actual, self.evidence, self.boot,
            self.r.state.now[0] + self.clock_offset, self.r.state.now[1] + self.clock_offset)

    def close(self):
        self.close_calls += 1
        if self.provider is not None:
            p = self.provider
            assert not p._busy and not p._observing and not p._held
            assert p._purpose is None or p._purpose._key is None
            assert p._operation is None or p._operation.session._closed
            assert p._enrollment is None or p._enrollment._closed and not p._enrollment._busy
        self.closed = True
        return self.close_result


def setup_digest(r, source, limits=LIMITS):
    return setup_approval_binding(local=r.context.initiator if r.context.initiator.public_key.hex() == r.client._identity.agent_id else r.context.responder,
        remote=r.context.responder if r.context.initiator.public_key.hex() == r.client._identity.agent_id else r.context.initiator,
        local_initiator=r.context.initiator.public_key.hex() == r.client._identity.agent_id,
        intention=r.intention, issued_epoch_us=r.context.issued_epoch_us, limits=limits,
        accounting=source.observe(r.hardware, r.intention),
        config_revision=hashlib.sha256(_canonical(r.hardware.config)).hexdigest(),
        broker_id=r.supervisor.broker_id, storage_epoch=r.hardware.probe.snapshot().epoch)


@contextmanager
def fixture(*, initiator=True, scopes=SETUP_SCOPES, setup_change=None, source_change=None):
    with setup(initiator=initiator) as r, ExitStack() as grants:
        source = ModeledAccounting(r)
        approved = setup_digest(r, source)
        if setup_change:
            approved = setup_change(approved)
        native = grants.enter_context(r.authority.grant(r.hardware.actor,
            MeshRequestAccess(r.state.epoch, lambda: r.state.owner_live), scopes,
            approval_binding=approved))
        if source_change:
            source_change(source)
        provider = None
        try:
            provider = ForwardNativeProvider(r.hardware, r.authority, native,
                remote=r.context.responder if initiator else r.context.initiator,
                local_initiator=initiator, intention=r.intention, issued_epoch_us=r.context.issued_epoch_us,
                limits=LIMITS, accounting=source, boot_reader=lambda: r.state.boot,
                clock_reader=lambda: r.state.now)
            source.provider = provider
            def bind(scopes=SETUP_SCOPES, context_change=None, approval_change=None):
                info = provider.generate()
                context = replace(r.context, **{
                    'initiator_noise_public_key' if initiator else 'responder_noise_public_key': info.public_key})
                if context_change:
                    context = context_change(context)
                approved = approval_binding(context, r.intention, initiator,
                    config_revision=provider.config_revision, broker_id=provider.broker_id,
                    storage_epoch=provider.storage_epoch)
                native = grants.enter_context(r.authority.grant(r.hardware.actor,
                    MeshRequestAccess(r.state.epoch, lambda: r.state.owner_live), scopes,
                    approval_binding=approval_change(approved) if approval_change else approved))
                provider.bind(context, native, remote_generation_id=str(uuid.uuid4()))
                return context, info
            yield r, provider, source, bind
        finally:
            if provider is not None:
                provider.close()


@pytest.mark.parametrize('initiator', [True, False])
def test_provider_composes_real_grants_enrollment_purpose_and_shared_counter_budget(initiator):
    with fixture(initiator=initiator) as (r, p, source, bind):
        assert r.state.rows == {} and r.hardware._provider is None
        context, info = bind()
        key = p._purpose._key.private_bytes_raw()
        operation = p.start(r.packet if initiator else None)
        assert p.owner is p._owner and p.operation is operation
        assert operation.session.context is context
        assert operation.session.counters is p.owner._composition.journal
        assert operation.budget.store is operation.session.counters.store
        assert operation.budget.prior == DEBT and operation.budget.limits == LIMITS
        row = r.state.rows[operation.budget.record_id]['payload']['body']
        assert row['binding']['prior_debt'] == asdict(DEBT)
        assert source.actual is None and p._purpose._key is None
        encoded = _canonical(list(r.state.rows.values()))
        assert key not in encoded and key.hex().encode() not in encoded
        assert base64.b64encode(key) not in encoded
        assert info.to_dict()['public_key'].encode() in encoded
        assert len(r.state.rows) == 5 and source.observations > 5
        assert p.retire() is True and operation.session._closed
        assert source.closed and source.close_calls == 1
        assert r.client._client.is_closed is False and not r.hardware._mutex.locked()
        assert r.hardware._provider is None


@pytest.mark.parametrize('missing', SETUP_SCOPES)
def test_every_actual_setup_scope_is_required_before_key_record(missing):
    with pytest.raises(ForwardNativeProviderError):
        with fixture(scopes=tuple(s for s in SETUP_SCOPES if s != missing)):
            pytest.fail('missing setup permission accepted')


@pytest.mark.parametrize('missing', SETUP_SCOPES)
def test_every_full_exchange_scope_is_required_before_enrollment(missing):
    with fixture() as (r, p, source, bind):
        with pytest.raises(ForwardNativeProviderError):
            bind(scopes=tuple(s for s in SETUP_SCOPES if s != missing))
        assert len(r.state.rows) == 1
        assert next(iter(r.state.rows.values()))['payload']['body']['state'] == 'generated'
        assert source.closed and p._purpose._key is None


@pytest.mark.parametrize('change', ['setup_approval', 'exchange_approval', 'expiry', 'peer', 'local_key'])
def test_original_setup_and_full_context_approval_cannot_be_substituted(change):
    if change == 'setup_approval':
        with pytest.raises(ForwardNativeProviderError):
            with fixture(setup_change=lambda _: 'f' * 64):
                pass
        return
    with fixture() as (r, p, source, bind):
        mutations = dict(expiry=lambda c: replace(c, expires_epoch_us=c.expires_epoch_us + 1),
            peer=lambda c: replace(c, initiator=c.responder, responder=c.initiator),
            local_key=lambda c: replace(c, initiator_noise_public_key=r.context.initiator_noise_public_key))
        with pytest.raises(ForwardNativeProviderError):
            bind(context_change=mutations.get(change),
                 approval_change=(lambda _: 'f' * 64) if change == 'exchange_approval' else None)
        assert p._purpose._key is None and source.closed
        assert p._owner is None


@pytest.mark.parametrize('field', ['debt', 'actual', 'evidence', 'boot', 'stale', 'future'])
def test_accounting_is_fresh_exact_and_unknown_actual_never_refunds_retained_debt(field):
    with fixture() as (r, p, source, bind):
        bind()
        before = copy.deepcopy(r.state.rows)
        if field == 'debt': source.debt = Cost()
        elif field == 'actual': source.actual = Cost()
        elif field == 'evidence': source.evidence = 'f' * 64
        elif field == 'boot': source.boot = str(uuid.uuid4())
        elif field == 'stale': source.clock_offset = -1
        else: source.clock_offset = 1
        with pytest.raises(ForwardNativeProviderError):
            p.start(r.packet)
        assert r.state.rows == before and p._owner is None
        assert source.closed and p._purpose._key is None


@pytest.mark.parametrize('field', ['accounting', 'source_method', 'source_close_method', 'limits', 'retained_debt', 'setup_context'])
def test_original_accounting_source_and_declared_approval_cannot_change(field, monkeypatch):
    with fixture() as (r, p, source, bind):
        bind()
        if field == 'accounting': p.accounting = ModeledAccounting(r)
        elif field == 'source_method': monkeypatch.setattr(ModeledAccounting, 'observe', lambda *a: True)
        elif field == 'source_close_method': monkeypatch.setattr(ModeledAccounting, 'close', lambda *a: True)
        elif field == 'limits': p.limits = LIMITS.plus(Cost(1))
        elif field == 'retained_debt': p._retained_debt = Cost()
        else: p.setup_context = r.native_context
        with pytest.raises(ForwardNativeProviderError):
            p.start(r.packet)
        assert source.closed and source.close_calls == 1


@pytest.mark.parametrize('field', ['consent', 'access', 'boot', 'storage', 'config', 'epoch_expiry', 'mono_expiry'])
def test_actual_native_lifecycle_changes_abort_before_budget_commit(field):
    with fixture() as (r, p, source, bind):
        bind()
        before = copy.deepcopy(r.state.rows)
        if field == 'consent': r.hardware.config['dispatch_enabled'] = False
        elif field == 'access': r.state.owner_live = False
        elif field == 'boot': r.state.boot = str(uuid.uuid4())
        elif field == 'storage': r.state.instance_id = 'f' * 64
        elif field == 'config': r.hardware.config['peers'] = []
        elif field == 'epoch_expiry': r.state.now = (r.intention.application_expires_epoch_us, r.state.now[1])
        else: r.state.now = (r.state.now[0], r.intention.application_deadline_monotonic_us)
        with pytest.raises(ForwardNativeProviderError):
            p.start(r.packet)
        assert r.state.rows == before and p._owner is None and source.closed
        assert not r.hardware._mutex.locked()


def test_unknown_committed_purpose_return_keeps_namespace_and_closes_source_last():
    with fixture() as (r, p, source, bind):
        original = r.client.workflow_put
        def lost(**kwargs):
            original(**kwargs)
            raise RuntimeError('modeled return loss after real fixture CAS')
        r.client.workflow_put = lost
        with pytest.raises(ForwardNativeProviderError):
            p.generate()
        assert len(r.state.rows) == 1
        row = next(iter(r.state.rows.values()))
        assert row['payload']['body']['state'] == 'reserved'
        assert source.closed and source.close_calls == 1 and p._purpose._key is None


def test_reentry_or_close_during_accounting_cannot_publish_key():
    for mode in ('reentry', 'close'):
        with fixture() as (r, p, source, bind):
            def hook():
                if mode == 'close': p.close()
                else:
                    with pytest.raises(ForwardNativeProviderError): p.generate()
            source.hook = hook
            with pytest.raises(ForwardNativeProviderError):
                p.generate()
            assert r.state.rows == {} and source.closed and p._purpose._key is None


def test_same_original_intention_cannot_regenerate_after_interruption_or_retirement():
    with fixture() as (r, p, source, bind):
        bind()
        p.start(r.packet)
        assert p.retire() is True
        before = copy.deepcopy(r.state.rows)
        fresh_source = ModeledAccounting(r)
        fresh = ForwardNativeProvider(r.hardware, r.authority, p.setup_context,
            remote=p.remote, local_initiator=True, intention=r.intention,
            issued_epoch_us=p.issued_epoch_us, limits=LIMITS, accounting=fresh_source,
            boot_reader=lambda: r.state.boot, clock_reader=lambda: r.state.now)
        fresh_source.provider = fresh
        with pytest.raises(ForwardNativeProviderError):
            fresh.generate()
        assert r.state.rows == before and fresh_source.closed
        assert fresh.intention.operation_id == r.intention.operation_id
        assert fresh.intention.application_deadline_monotonic_us == r.intention.application_deadline_monotonic_us


def test_plain_cost_dictionary_boolean_and_abstract_sources_cannot_start_provider():
    with fixture() as (r, p, source, bind):
        for invalid in (Cost(), {}, True):
            with pytest.raises(ForwardNativeProviderError):
                ForwardNativeProvider(r.hardware, r.authority, p.setup_context, remote=p.remote,
                    local_initiator=True, intention=r.intention, issued_epoch_us=p.issued_epoch_us,
                    limits=LIMITS, accounting=invalid)
        with pytest.raises(TypeError): NativeAccountingSource()
        assert not r.state.rows


def test_constructor_native_failure_releases_transferred_source_without_writes():
    with setup() as r:
        source = ModeledAccounting(r)
        def unavailable():
            raise RuntimeError('modeled native consent failure')
        r.hardware.consent = unavailable
        with pytest.raises(RuntimeError, match='modeled native consent failure'):
            ForwardNativeProvider(r.hardware, r.authority, r.native_context,
                remote=r.context.responder, local_initiator=True, intention=r.intention,
                issued_epoch_us=r.context.issued_epoch_us, limits=LIMITS, accounting=source)
        assert source.closed and source.close_calls == 1 and not r.state.rows


def test_closed_full_approval_context_cannot_keep_an_established_endpoint_live():
    with fixture() as (r, p, source, bind):
        info = p.generate()
        context = replace(r.context, initiator_noise_public_key=info.public_key)
        approved = approval_binding(context, r.intention, True,
            config_revision=p.config_revision, broker_id=p.broker_id, storage_epoch=p.storage_epoch)
        with r.authority.grant(r.hardware.actor,
                MeshRequestAccess(r.state.epoch, lambda: r.state.owner_live), SETUP_SCOPES,
                approval_binding=approved) as native:
            p.bind(context, native, remote_generation_id=str(uuid.uuid4()))
            operation = p.start(r.packet)
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardNativeProviderError):
            p.owner
        assert operation.session._closed and source.closed
        assert r.state.rows == before and not r.hardware._mutex.locked()


def test_signed_enrollment_revocation_fences_owner_accessor_and_releases_source_last():
    from lantern_meshticulum.sage.noise_native_enrollment import SIGNATURE_DOMAIN
    with fixture() as (r, p, source, bind):
        bind()
        operation = p.start(r.packet)
        row = r.state.rows[p._enrollment.record_id]
        row['revision'] += 1
        body = row['payload']['body']
        body.update(revision=row['revision'], state='revoked')
        row['payload']['signature'] = base64.b64encode(
            r.hardware.identity._signing_key.sign(SIGNATURE_DOMAIN + _canonical(body)).signature).decode()
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardNativeProviderError):
            p.owner
        assert operation.session._closed and p._purpose._key is None
        assert source.closed and source.close_calls == 1 and r.state.rows == before


def test_revocation_after_budget_cas_retains_full_original_debt_and_no_child_permission():
    from lantern_meshticulum.sage.forward_budget import SCHEMA
    with fixture() as (r, p, source, bind):
        bind()
        original = r.client.workflow_put
        def revoke_after_commit(**kwargs):
            result = original(**kwargs)
            if kwargs['payload'].get('schema') == SCHEMA:
                r.state.owner_live = False
            return result
        r.client.workflow_put = revoke_after_commit
        with pytest.raises(ForwardNativeProviderError):
            p.start(r.packet)
        parents = [v['payload']['body'] for v in r.state.rows.values() if v['payload']['schema'] == SCHEMA]
        assert len(parents) == 1 and parents[0]['binding']['prior_debt'] == asdict(DEBT)
        assert parents[0]['allocations'] == []
        assert source.closed and p._purpose._key is None and not r.hardware._mutex.locked()


def test_cleanup_unknown_is_not_success_and_actual_usage_cannot_exceed_retained_maxima():
    with fixture() as (r, p, source, bind):
        bind()
        operation = p.start(r.packet)
        source.close_result = False
        assert p.close() is False
        assert operation.session._closed and source.closed and source.close_calls == 1
        assert p.close() is False and source.close_calls == 1
    with pytest.raises(ForwardNativeProviderError):
        NativeAccountingObservation(Cost(), Cost(1), 'a' * 64, r.state.boot, 1, 1)
