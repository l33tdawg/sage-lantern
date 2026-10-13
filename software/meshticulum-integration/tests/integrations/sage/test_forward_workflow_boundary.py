# SPDX-License-Identifier: Apache-2.0
"""Workflow callback boundaries; native/backend/RF remain explicit models."""
from collections import Counter
import copy
import hashlib
import json
import time
import uuid

import pytest
pytest.importorskip('sage_sdk', reason='compatible optional SDK required')

from meshticulum.privacy.forward_session import ForwardSessionError
from lantern_meshticulum.sage.forward_budget import ForwardBudget, ForwardBudgetError, Cost, SCHEMA as BUDGET_SCHEMA
from lantern_meshticulum.sage.forward_receive_journal import ForwardReceiveJournal, ForwardJournalError, SCHEMA as JOURNAL_SCHEMA
from lantern_meshticulum.sage.forward_receiver import ForwardReceiver, ForwardReceiverError
from tests.integrations.sage.test_mesh_inbox import rig, KEY, SENDER
from tests.integrations.sage.test_forward_receive_journal import journal_rig, BOOT, accepted, body as journal_body
from tests.integrations.sage.test_forward_budget import plan


@pytest.fixture
def boundary(journal_rig):
    r = journal_rig
    r.workflow_live = r.keys_live = r.read_live = True
    r.workflow_calls = 0
    r.hook = None
    r.full_calls = Counter()

    def full(name, value=True):
        r.full_calls[name] += 1
        return value and r.keys_live

    def workflow():
        assert r.lease_active
        r.workflow_calls += 1
        if r.hook is not None:
            r.hook()
        return r.workflow_live and r.live and r.permission

    def new(kind, callback=workflow, *, boot=BOOT):
        common = dict(context=r.context, intention=r.intention, boot_id=boot,
            owner_lease=r.journal._lease, access_check=lambda: full('access', r.live),
            storage_check=lambda: full('storage', r.storage), clock=r.clock,
            workflow_access_check=callback)
        if kind == 'budget':
            return ForwardBudget(r.store, signing_key=KEY, limits=Cost(600, 120000000, 2048, 2000000, 2000000),
                prior_debt=Cost(3, 1000), slots=((1, False), (2, True), (3, False), (4, True), (5, False)),
                native_check=lambda c, i, p: full('native', r.permission), **common)
        if kind == 'journal':
            return ForwardReceiveJournal(r.store, local_initiator=False, local_signing_key=KEY,
                peer_check=lambda peer: full('peer', peer == r.peer),
                permission_check=lambda i: full('permission', r.permission), **common)
        return ForwardReceiver(r.store.client, local_initiator=False, signing_key=KEY,
            endpoint=r.store.client._base_url, peer_check=lambda peer: full('peer', peer == r.peer),
            dispatch_check=lambda c, i: full('dispatch', r.permission),
            read_check=lambda c, i: full('read', r.read_live),
            key_check=lambda *args: full('purpose'), **common)

    r.new_boundary, r.workflow = new, workflow
    return r


def first(kind, value):
    return value.prepare() if kind == 'budget' else value.claim(value.binding)


@pytest.mark.parametrize('kind', ['budget', 'journal', 'receiver'])
@pytest.mark.parametrize('callback', [False, 1, object()])
def test_noncallable_optional_observer_is_refused_before_storage(boundary, kind, callback):
    with pytest.raises((ForwardBudgetError, ForwardJournalError, ForwardReceiverError)):
        boundary.new_boundary(kind, callback)
    assert not boundary.rows


@pytest.mark.parametrize('kind', ['budget', 'journal', 'receiver'])
@pytest.mark.parametrize('original_none', [False, True])
def test_optional_identity_including_none_cannot_be_substituted(boundary, kind, original_none):
    r = boundary
    value = r.new_boundary(kind, None if original_none else r.workflow)
    value._workflow_access = r.workflow if original_none else None
    with pytest.raises((ForwardBudgetError, ForwardJournalError, ForwardReceiverError)):
        value._new_journal() if kind == 'receiver' else first(kind, value)
    assert not r.rows


def test_receiver_forwards_exact_callback_to_each_journal_and_keeps_full_crypto_checks(boundary):
    r = boundary
    receiver = r.new_boundary('receiver')
    assert receiver._workflow_access is receiver.journal._workflow_access is r.workflow
    assert receiver._new_journal()._workflow_access is r.workflow
    assert receiver._authorize_crypto() is True
    assert set(r.full_calls) >= {'access', 'storage', 'peer', 'read', 'dispatch', 'purpose'}
    assert r.workflow_calls == 0
    r.keys_live = False
    with pytest.raises(ForwardReceiverError): receiver._authorize_crypto()
    assert not r.rows


@pytest.mark.parametrize('kind', ['budget', 'journal'])
def test_active_epoch_is_fresh_per_action_and_cannot_authorize_between_actions(boundary, kind):
    r = boundary; value = r.new_boundary(kind)
    observed = []
    original_get = value.store.get
    def get(*args, **kwargs):
        epoch = value._workflow_epoch
        assert value._busy and epoch == kwargs['expected_epoch'] == r.instance + ':1'
        observed.append(kwargs['access_check'])
        return original_get(*args, **kwargs)
    value.store.get = get
    first(kind, value)
    assert r.workflow_calls and value._workflow_epoch is None and not value._busy
    for callback in observed:
        with pytest.raises((ForwardBudgetError, ForwardJournalError)): callback()
    before = copy.deepcopy(r.rows)
    r.workflow_live = False
    with pytest.raises((ForwardBudgetError, ForwardJournalError)):
        value.prepare() if kind == 'budget' else value.reserve_transmit()
    assert r.rows == before and value._workflow_epoch is None and value._uncertain


@pytest.mark.parametrize('kind', ['budget', 'journal'])
def test_callback_from_prior_action_is_refused_during_a_later_action_in_same_epoch(boundary, kind):
    r = boundary; value = r.new_boundary(kind); retained = []
    original = value.store.get
    def get(*args, **kwargs):
        if not retained: retained.append(kwargs['access_check'])
        return original(*args, **kwargs)
    value.store.get = get
    first(kind, value)
    refused = []
    def hook():
        with pytest.raises((ForwardBudgetError, ForwardJournalError)): retained[0]()
        refused.append(True)
    r.hook = hook
    value.prepare() if kind == 'budget' else value.reserve_transmit()
    assert refused and value._workflow_epoch is value._workflow_run is None


@pytest.mark.parametrize('kind', ['budget', 'journal'])
@pytest.mark.parametrize('drift', ['revoked', 'epoch', 'monotonic', 'close', 'callback', 'run_epoch', 'reenter'])
def test_callback_drift_refuses_before_any_metadata_write(boundary, kind, drift):
    r = boundary; value = r.new_boundary(kind)
    def hook():
        if drift == 'revoked': r.workflow_live = False
        elif drift == 'epoch': r.now = r.intention.application_expires_epoch_us / 1000000
        elif drift == 'monotonic': r.monotonic = r.intention.application_deadline_monotonic_us
        elif drift == 'close': value.close()
        elif drift == 'callback': value._workflow_access = lambda: True
        elif drift == 'run_epoch': value._workflow_epoch = 'changed:1'
        else:
            with pytest.raises((ForwardBudgetError, ForwardJournalError)): first(kind, value)
    r.hook = hook
    with pytest.raises((ForwardBudgetError, ForwardJournalError)): first(kind, value)
    assert not r.rows and r.puts == 0 and value._workflow_epoch is None
    assert not r.lease_active


@pytest.mark.parametrize('kind', ['budget', 'journal'])
def test_original_action_epoch_blocks_put_after_guard_before_first_snapshot(boundary, kind):
    r = boundary; value = r.new_boundary(kind); original = value.store.put
    def put(*args, **kwargs):
        assert kwargs['expected_epoch'] == value._workflow_epoch == r.instance + ':1'
        r.instance = 'f' * 64
        return original(*args, **kwargs)
    value.store.put = put
    with pytest.raises((ForwardBudgetError, ForwardJournalError)): first(kind, value)
    assert not r.rows and r.puts == 0 and value._workflow_epoch is None


@pytest.mark.parametrize('kind', ['budget', 'journal'])
def test_key_revocation_during_get_is_caught_by_full_guard_before_write(boundary, kind):
    r = boundary; value = r.new_boundary(kind); original = value.store.client.workflow_get
    def get(**kwargs):
        try: return original(**kwargs)
        finally: r.keys_live = False
    value.store.client.workflow_get = get
    with pytest.raises((ForwardBudgetError, ForwardJournalError)): first(kind, value)
    assert not r.rows and r.puts == 0 and value._workflow_epoch is None


@pytest.mark.parametrize('kind', ['budget', 'journal'])
@pytest.mark.parametrize('failure', ['before', 'after', 'conflict'])
def test_unknown_commit_keeps_prepaid_maxima_and_counter_without_usable_permission(boundary, kind, failure):
    r = boundary; value = r.new_boundary(kind)
    first(kind, value)
    if kind == 'budget':
        r.budget = value; child = plan(r); schema = BUDGET_SCHEMA
        action = lambda: value.pre_admit(child)
    else:
        schema = JOURNAL_SCHEMA; action = value.reserve_transmit
    r.failure = schema, failure
    with pytest.raises((ForwardBudgetError, ForwardJournalError)): action()
    data = r.rows[value.record_id]['payload']['body']
    if kind == 'budget':
        assert data['binding']['prior_debt'] == Cost(3, 1000).__dict__
        assert data['allocations'] == ([child.to_dict()] if failure == 'after' else [])
        assert not value._permits
    else: assert data['tx'] == int(failure == 'after')
    assert value._uncertain and value._workflow_epoch is None and not r.lease_active
    before = copy.deepcopy(r.rows)
    r.failure = None
    with pytest.raises((ForwardBudgetError, ForwardJournalError)): action()
    assert r.rows == before


def test_optimized_journal_preserves_real_replay_capsule_and_expired_read_only_recovery(boundary):
    r = boundary; r.journal = r.new_boundary('journal')
    sender, receiver, outer = accepted(r)
    assert r.workflow_calls and journal_body(r)['tx'] >= 1 and journal_body(r)['confirmation_received']
    before = copy.deepcopy(r.rows)
    with pytest.raises(ForwardSessionError): receiver.receive_into(outer)
    assert r.rows == before
    sender.close(); receiver.close(); r.journal.close()
    r.now = 200; r.monotonic = 1; r.permission = False
    calls = r.workflow_calls
    def no_live_callback(): raise AssertionError('read-only recovery called live workflow observer')
    recovered = r.new_boundary('journal', no_live_callback, boot=str(uuid.uuid4())).recover()
    assert recovered.sealed_record == r.packet and r.rows == before and r.workflow_calls == calls


def test_capture_borrowed_read_keeps_full_legacy_guards_with_optimized_journal(boundary):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from tests.integrations.sage.test_forward_receiver import complete
    r = boundary; r.receiver = r.new_boundary('receiver')
    r.noise = X25519PrivateKey.from_private_bytes(hashlib.sha256(b'fixture journal responder Noise').digest())
    complete(r)
    original = copy.deepcopy(r.rows[r.receiver.journal.record_id])
    calls = r.workflow_calls
    assert r.receiver.import_pending() == 'captured'
    assert r.workflow_calls == calls and r.rows[r.receiver.journal.record_id] == original
    r.now = 200; r.permission = False
    fresh = r.new_boundary('receiver', lambda: pytest.fail('borrowed read used live workflow observer'))
    assert fresh.import_pending() == 'captured'
    assert r.rows[fresh.journal.record_id] == original


def test_modeled_native_startup_and_first_stage_requests_decrease_without_changed_debt(tmp_path, monkeypatch, record_property):
    """Same actual authority/crypto paths; only SDK/RF/native IO is modeled."""
    import lantern_meshticulum.sage.forward_native_provider as provider_module
    from tests.integrations.sage.native_canary_helpers import generated_canary_pair, modeled_stage
    from tests.integrations.sage.test_forward_operation import backend
    reports = []
    for optimized in (False, True):
        counts = []
        def counted_backend(key):
            state, client, endpoint = backend(key)
            counter = Counter(); counts.append(counter)
            for name in ('message_storage_status', 'workflow_get', 'workflow_put', 'workflow_list'):
                original = getattr(client, name)
                def invoke(*args, _method=original, _name=name, **kwargs):
                    counter[_name] += 1
                    return _method(*args, **kwargs)
                setattr(client, name, invoke)
            return state, client, endpoint
        with monkeypatch.context() as patch:
            if not optimized:
                for cls in (ForwardBudget, ForwardReceiveJournal, ForwardReceiver):
                    original = cls.__init__
                    def legacy(self, *args, _init=original, **kwargs):
                        kwargs['workflow_access_check'] = None
                        return _init(self, *args, **kwargs)
                    patch.setattr(cls, '__init__', legacy)
            started = time.monotonic()
            with generated_canary_pair(provider_module, backend_factory=counted_backend) as pair:
                startup = [dict(c) for c in counts]
                startup_elapsed = time.monotonic() - started
                before = [c.copy() for c in counts]
                started = time.monotonic()
                directory = tmp_path / ('optimized' if optimized else 'legacy')
                directory.mkdir()
                result = modeled_stage(directory, pair, 0)
                assert result['physical_qualified'] is False and all(n > 0 for n in result['attempt_counts'])
                assert all(o.position == 1 for o in pair.operations)
                for index, owner in enumerate(pair.owners):
                    assert owner._budget.prior == pair.sources[index].retained_debt
                    row = pair.endpoints[index].state.rows[owner._budget.record_id]
                    assert row['payload']['body']['allocations'][0]['cost']['radio_attempts'] == 256
                reports.append(dict(optimized=optimized, startup=startup,
                    startup_elapsed_seconds=startup_elapsed,
                    first_stage=[dict(c - b) for c, b in zip(counts, before)],
                    first_stage_elapsed_seconds=time.monotonic() - started))
            assert all(source.closed for source in pair.sources)
    for phase in ('startup', 'first_stage'):
        old, new = (sum(sum(c.values()) for c in report[phase]) for report in reports)
        assert 0 < new < old
    record_property('modeled_workflow_requests', json.dumps(reports, sort_keys=True))
    print('modeled_workflow_requests=' + json.dumps(reports, sort_keys=True), flush=True)
