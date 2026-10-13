# SPDX-License-Identifier: Apache-2.0
"""Fresh journal boundaries with real crypto/CAS and modeled native/backend IO."""
import base64
import copy
from contextlib import contextmanager
from dataclasses import replace
import threading
from types import SimpleNamespace
import uuid

import pytest
pytest.importorskip('sage_sdk', reason='compatible optional SDK required')

from meshticulum.privacy.forward_session import ForwardSessionError
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_native_owner import SCOPES
from lantern_meshticulum.sage.forward_receive_journal import ForwardJournalError, ForwardReceiveJournal, SCHEMA
from lantern_meshticulum.sage.forward_receiver import ForwardReceiver, ForwardReceiverError
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_mesh_inbox import rig, KEY, SENDER, RECIPIENT
from tests.integrations.sage.test_forward_receive_journal import journal_rig, BOOT, accepted, ready, body
from tests.integrations.sage.test_forward_receiver import receiver_rig, complete


@pytest.fixture
def boundary(journal_rig):
    r = journal_rig
    state = SimpleNamespace(full=[], actions=[], workflow=[], hook=None, clock_hook=None,
        exit_hook=None, keys=True, read=True, storage_epoch=r.store.storage.snapshot().epoch)
    original_lease = r.journal._lease
    @contextmanager
    def lease():
        with original_lease(): yield
        if state.exit_hook is not None: state.exit_hook()
    def full(name, value):
        state.full.append(name)
        return value
    def aggregate(*args):
        state.actions.append((args, r.lease_active, r.puts))
        if state.hook is not None: state.hook()
        return all((r.live, state.read, r.storage, r.peer == SENDER, r.permission, state.keys,
                    r.store.storage.snapshot().epoch == state.storage_epoch))
    def workflow():
        state.workflow.append((r.lease_active, r.puts, r.instance))
        return all((r.lease_active, r.live, state.read, r.storage, r.permission))
    def clock():
        if state.clock_hook is not None: state.clock_hook()
        return r.clock()
    def new(callback=aggregate, *, boot=BOOT, metadata=workflow):
        return ForwardReceiveJournal(r.store, context=r.context, local_initiator=False,
            local_signing_key=KEY, intention=r.intention, boot_id=boot, owner_lease=lease,
            access_check=lambda:full('access', r.live and state.read),
            storage_check=lambda:full('storage', r.storage),
            peer_check=lambda peer:full('peer', peer == r.peer),
            permission_check=lambda intention:full('permission', r.permission and intention is r.intention),
            clock=clock, workflow_access_check=metadata, journal_action_check=callback)
    state.aggregate, state.new = aggregate, new
    r.journal = new()
    r.action = state
    return r


@pytest.mark.parametrize('callback', [False, 1, object()])
def test_noncallable_action_is_refused_before_any_record(boundary, callback):
    with pytest.raises(ForwardJournalError): boundary.action.new(callback)
    assert boundary.rows == {} and boundary.puts == 0


@pytest.mark.parametrize('fresh', [True, False])
def test_none_retains_original_callback_order(boundary, fresh):
    r = boundary; journal = r.action.new(None)
    journal._guard(fresh=fresh)
    assert r.action.full == ['access', 'storage', 'peer'] + (['permission'] if fresh else [])
    assert not r.action.actions and not r.action.workflow and not r.rows


def test_each_original_claim_boundary_is_fresh_and_sdk_epochs_remain_bracketed(boundary):
    r = boundary; journal = r.journal
    assert journal.claim(journal.binding) is True
    assert [(held, puts) for _, held, puts in r.action.actions] == [(True, 0), (True, 0), (True, 1), (False, 1)]
    assert all(j is journal and c is r.context and i is r.intention and p is journal.remote
        and role is False for (j, c, i, p, role), _, _ in r.action.actions)
    assert not r.action.full and len(r.action.workflow) > 4
    assert all(held and instance == r.instance for held, _, instance in r.action.workflow)
    assert body(r)['tx'] == 0 and not journal._guarding and not journal._busy
    assert journal._action_run is None and not r.lease_active
    r.action.actions.clear()
    assert journal.reserve_transmit() == 1
    assert [(held, puts) for _, held, puts in r.action.actions] == [(True, 1), (True, 1), (True, 2), (False, 2)]


@pytest.mark.parametrize('initial_none', [True, False])
def test_callback_mode_is_pinned_including_none(boundary, initial_none):
    r = boundary; journal = r.action.new(None if initial_none else r.action.aggregate)
    journal._journal_action = r.action.aggregate if initial_none else None
    with pytest.raises(ForwardJournalError): journal.claim(journal.binding)
    assert not r.action.actions and not r.rows


@pytest.mark.parametrize('mutation', ['store', 'context', 'intention', 'remote', 'role', 'key', 'lease',
    'clock', 'callback', 'binding', 'record', 'context_value', 'fresh', 'run', 'epoch', 'revision',
    'claimed', 'last_clock', 'close', 'reentry', 'guard_reentry', 'raise'])
def test_callback_drift_and_caught_reentry_fence_before_write(boundary, mutation, monkeypatch):
    r = boundary; j = r.journal
    def change():
        if mutation == 'store': monkeypatch.setattr(j, 'store', copy.copy(j.store))
        elif mutation == 'context': monkeypatch.setattr(j, 'context', replace(j.context))
        elif mutation == 'intention': monkeypatch.setattr(j, 'intention', replace(j.intention))
        elif mutation == 'remote': monkeypatch.setattr(j, 'remote', RECIPIENT)
        elif mutation == 'role': monkeypatch.setattr(j, 'local_initiator', 0)
        elif mutation == 'key': monkeypatch.setattr(j, '_key', copy.copy(j._key))
        elif mutation == 'lease': monkeypatch.setattr(j, '_lease', lambda:None)
        elif mutation == 'clock': monkeypatch.setattr(j, '_clock', r.clock)
        elif mutation == 'callback': monkeypatch.setattr(j, '_journal_action', lambda *args:True)
        elif mutation == 'binding': monkeypatch.setattr(j, 'binding', b'x' * 32)
        elif mutation == 'record': monkeypatch.setattr(j, 'record_id', str(uuid.uuid4()))
        elif mutation == 'context_value': monkeypatch.setitem(j._context_value, 'max_records', 32)
        elif mutation == 'fresh': j._fresh = False
        elif mutation == 'run': j._workflow_run = object()
        elif mutation == 'epoch': j._workflow_epoch = 'changed'
        elif mutation == 'revision': j._highest_revision = 1
        elif mutation == 'claimed': j._claimed = True
        elif mutation == 'last_clock': j._last_clock = (150000000, 100000000)
        elif mutation == 'close': j.close()
        elif mutation == 'reentry':
            with pytest.raises(ForwardJournalError): j.recover()
        elif mutation == 'guard_reentry':
            with pytest.raises(ForwardJournalError): j._guard(fresh=True)
        else: raise RuntimeError('modeled aggregate interruption')
    r.action.hook = change
    with pytest.raises(ForwardJournalError): j.claim(j.binding)
    assert j._uncertain and not j._guarding and not j._busy and not r.lease_active
    assert not r.rows and r.puts == 0
    monkeypatch.undo(); r.action.hook = None
    with pytest.raises(ForwardJournalError): j.claim(j.binding)


@pytest.mark.parametrize('guard', [1, 2, 3, 4])
def test_revocation_at_every_original_guard_never_publishes_success(boundary, guard):
    r = boundary
    def revoke():
        if len(r.action.actions) == guard: r.action.keys = False
    r.action.hook = revoke
    with pytest.raises(ForwardJournalError): r.journal.claim(r.journal.binding)
    assert len(r.action.actions) == guard and r.journal._uncertain and not r.lease_active
    # A completed CAS is retained even when a later observation refuses success.
    assert (r.journal.record_id in r.rows) is (guard >= 3)
    if guard >= 3: assert body(r)['tx'] == 0 and body(r)['revision'] == 1


@pytest.mark.parametrize('when', ['first', 'final'])
@pytest.mark.parametrize('mutation', ['fresh', 'clock', 'permission', 'expiry', 'storage'])
def test_clock_callback_cannot_switch_run_mode_or_publish_stale_success(boundary, when, mutation):
    r = boundary; calls = []
    def hook():
        if calls or (when == 'final' and (r.lease_active or r.puts == 0)): return
        calls.append(True)
        if mutation == 'fresh': r.journal._fresh = False
        elif mutation == 'clock': r.journal._clock = r.clock
        elif mutation == 'permission': r.permission = False
        elif mutation == 'expiry': r.now = 180
        else: r.instance = 'c' * 64
    r.action.clock_hook = hook
    with pytest.raises(ForwardJournalError): r.journal.claim(r.journal.binding)
    assert calls == [True] and r.journal._uncertain and not r.lease_active
    assert (r.journal.record_id in r.rows) is (when == 'final')


@pytest.mark.parametrize('change', ['read', 'keys', 'dispatch', 'epoch', 'deadline'])
def test_lease_exit_keeps_committed_state_but_refuses_stale_success(boundary, change):
    r = boundary
    def leave():
        if change == 'read': r.action.read = False
        elif change == 'keys': r.action.keys = False
        elif change == 'dispatch': r.permission = False
        elif change == 'epoch': r.instance = 'c' * 64
        else: r.monotonic = r.intention.application_deadline_monotonic_us
    r.action.exit_hook = leave
    with pytest.raises(ForwardJournalError): r.journal.claim(r.journal.binding)
    assert body(r)['revision'] == 1 and r.journal._uncertain and not r.lease_active


def test_real_session_capsule_replay_and_expired_recovery_keep_old_read_semantics(boundary):
    r = boundary; sender, receiver, outer = accepted(r)
    original = copy.deepcopy(r.rows)
    with pytest.raises(ForwardSessionError): receiver.receive_into(outer)
    assert r.rows == original
    sender.close(); receiver.close(); r.journal.close()
    r.now = 200; r.monotonic = 1; r.permission = r.action.keys = False
    def forbidden(*args): pytest.fail('historical read requested fresh dispatch/key authority')
    r.action.actions.clear(); r.action.full.clear(); r.action.workflow.clear()
    reader = r.action.new(forbidden, boot=str(uuid.uuid4()), metadata=forbidden)
    recovered = reader.recover()
    assert recovered.sealed_record == r.packet and recovered.intention == r.intention
    assert r.rows == original and not r.action.actions and not r.action.workflow
    assert r.action.full == ['access', 'storage', 'peer'] * (len(r.action.full) // 3)
    fresh = r.action.new(boot=str(uuid.uuid4()))
    with pytest.raises(ForwardJournalError): fresh.claim(fresh.binding)
    assert r.rows == original


@pytest.mark.parametrize('point', ['before_get', 'after_get', 'before_put', 'after_put'])
def test_sdk_before_and_after_storage_epochs_still_fence_the_action(boundary, point):
    r = boundary; client = r.store.client
    original_get, original_put = client.workflow_get, client.workflow_put
    changed = []
    def get(**kwargs):
        if point == 'before_get': r.instance = 'c' * 64; changed.append(True)
        try: return original_get(**kwargs)
        finally:
            if point == 'after_get': r.instance = 'c' * 64; changed.append(True)
    def put(**kwargs):
        if point == 'before_put': r.instance = 'c' * 64; changed.append(True)
        result = original_put(**kwargs)
        if point == 'after_put': r.instance = 'c' * 64; changed.append(True)
        return result
    client.workflow_get, client.workflow_put = get, put
    with pytest.raises(ForwardJournalError): r.journal.claim(r.journal.binding)
    assert changed == [True] and r.journal._uncertain and not r.lease_active
    assert bool(r.rows) is (point in ('before_put', 'after_put'))


def test_receiver_plumbs_exact_hook_but_borrowed_import_never_requests_fresh_keys(receiver_rig):
    r = receiver_rig; calls = []
    old = r.receiver
    def new(action):
        return ForwardReceiver(old.client, context=old.context, local_initiator=old.local_initiator,
            intention=old.intention, boot_id=old.boot_id, endpoint=old.client._base_url, signing_key=old._key,
            owner_lease=old._lease, access_check=old._access, storage_check=old._storage,
            peer_check=old._peer, dispatch_check=old._dispatch, read_check=old._read,
            key_check=old._purpose, clock=old._clock, journal_action_check=action)
    def action(journal, context, intention, remote, role):
        calls.append(journal)
        assert context is r.context and intention is r.intention and remote is r.context.initiator and role is False
        return all((r.live, r.storage, r.read_ok, r.purpose, r.permission, r.peer == remote))
    r.receiver = new(action)
    assert r.receiver.journal._journal_action is r.receiver._new_journal()._journal_action is action
    complete(r); assert calls
    original = copy.deepcopy(r.rows[r.receiver.journal.record_id])
    r.receiver.close(); r.purpose = r.permission = False; r.now = 180
    def forbidden(*args): pytest.fail('borrowed recovery invoked a fresh journal action')
    restored = new(forbidden)
    assert restored.import_pending() == 'captured'
    result = restored.settle()
    assert result.status == 'rejected' and result.reason == 'application_expired'
    assert r.rows[restored.journal.record_id] == original and not old.client._client.is_closed


@pytest.mark.parametrize('original_none', [True, False])
def test_receiver_hook_mode_cannot_change_before_new_reader(receiver_rig, original_none):
    receiver = receiver_rig.receiver
    if not original_none:
        # Use the public constructor, never manufacture an originally registered callback.
        receiver = ForwardReceiver(receiver.client, context=receiver.context, local_initiator=False,
            intention=receiver.intention, boot_id=BOOT, endpoint=receiver.client._base_url, signing_key=KEY,
            owner_lease=receiver._lease, access_check=receiver._access, storage_check=receiver._storage,
            peer_check=receiver._peer, dispatch_check=receiver._dispatch, read_check=receiver._read,
            key_check=receiver._purpose, clock=receiver._clock, journal_action_check=lambda *args:True)
    receiver._journal_action = (lambda *args:True) if original_none else None
    with pytest.raises(ForwardReceiverError): receiver._new_journal()
    assert not receiver_rig.rows


@pytest.mark.parametrize('phase', ['claim', 'transmit', 'confirmation', 'data'])
@pytest.mark.parametrize('failure', ['before', 'after', 'conflict'])
def test_unknown_cas_keeps_signed_counters_and_capsule_without_retry(boundary, phase, failure):
    r = boundary
    if phase == 'claim': action = lambda:r.journal.claim(r.journal.binding)
    elif phase == 'confirmation':
        r.journal.claim(r.journal.binding); action = lambda:r.journal.record_receive(1)
    else:
        sender, receiver = ready(r)
        action = r.journal.reserve_transmit if phase == 'transmit' else lambda:receiver.receive_into(sender.seal(r.packet))
    before = copy.deepcopy(r.rows); puts = r.puts
    r.failure = (SCHEMA, failure)
    with pytest.raises((ForwardJournalError, ForwardSessionError)): action()
    assert r.journal._uncertain and not r.lease_active and r.puts == puts + 1
    if failure != 'after': assert r.rows == before
    elif phase == 'claim': assert body(r)['revision'] == 1
    elif phase == 'transmit': assert body(r)['tx'] == before[r.journal.record_id]['payload']['body']['tx'] + 1
    elif phase == 'confirmation': assert body(r)['confirmation_received'] is True
    else: assert body(r)['capsule'] is not None
    retained = copy.deepcopy(r.rows); r.failure = None
    with pytest.raises(ForwardJournalError): r.journal.reserve_transmit()
    assert r.rows == retained and r.puts == puts + 1


@contextmanager
def native(*, initiator=True, callback=None):
    from tests.integrations.sage.test_forward_native_owner import setup
    holder = {}; observations = []
    def peer(*args):
        owner = holder.get('owner'); journal = owner._composition.journal if owner and owner._composition else None
        if journal is not None and journal._guarding:
            observations.append((args, owner._session, owner._operation, owner._held))
            if callback is not None: callback(holder)
        return True
    with setup(initiator=initiator, peer_observer=peer) as r:
        owner = r.create(); holder.update(r=r, owner=owner)
        op = owner.operation(r.noise[0 if initiator else 1], limits=Cost(2000, 300000000),
            prior_debt=Cost(3, 1000), sealed_record=r.packet if initiator else None)
        holder.update(op=op, journal=owner._composition.journal)
        try: yield r, owner, op, observations, holder
        finally: owner.close()


@pytest.mark.parametrize('initiator', [True, False])
def test_native_initial_claim_works_before_session_install_and_reobserves_each_counter_action(initiator):
    with native(initiator=initiator) as (r, owner, op, observations, holder):
        assert len(observations) == 4
        assert all(session is operation is None for _, session, operation, _ in observations)
        assert [held for _, _, _, held in observations] == [True, True, True, False]
        j = op.session.counters; observations.clear()
        assert j.reserve_transmit() == 1
        assert len(observations) == 4 and all(session is op.session and operation is op
            for _, session, operation, _ in observations)
        assert all(peer is owner.remote and digest == r.context.digest for (peer, _, digest), _, _, _ in observations)
        assert op.budget.prior == Cost(3, 1000) and not r.hardware._mutex.locked()
        assert not owner._busy and not j._busy and not j._guarding


@pytest.mark.parametrize('mutation', ['receiver_close', 'receiver_uncertain', 'client_close', 'callback_mode',
    'authority', 'vault', 'boot', 'epoch', 'deadline', 'reentry', 'close'])
def test_native_post_key_callback_retains_receiver_and_current_native_checks(mutation):
    hooks = []
    def callback(h):
        if hooks: hooks[0](h)
    with native(callback=callback) as (r, owner, op, observations, holder):
        j = op.session.counters; before = copy.deepcopy(r.state.rows)
        def mutate(h):
            if mutation == 'receiver_close': owner._composition.close()
            elif mutation == 'receiver_uncertain': owner._composition._uncertain = True
            elif mutation == 'client_close': r.client._client.close()
            elif mutation == 'callback_mode': owner._composition._crypto_action = None
            elif mutation == 'authority': r.state.owner_live = False
            elif mutation == 'vault': r.state.instance_id = 'f' * 64
            elif mutation == 'boot': r.state.boot = str(uuid.uuid4())
            elif mutation == 'epoch': r.state.now = (r.context.expires_epoch_us, r.state.now[1])
            elif mutation == 'deadline': r.state.now = (r.state.now[0], r.intention.application_deadline_monotonic_us)
            elif mutation == 'close': assert owner.close() is False
            else:
                with pytest.raises(Exception): owner.journal_action_check(j, r.context, r.intention, owner.remote, True)
        hooks.append(mutate)
        with pytest.raises(ForwardJournalError): j.reserve_transmit()
        assert r.state.rows == before and op.budget.prior == Cost(3, 1000)
        assert owner._uncertain and j._uncertain and not r.hardware._mutex.locked()
        assert not owner._busy and not j._busy and not j._guarding


@pytest.mark.parametrize('missing', SCOPES)
def test_every_actual_native_scope_is_rechecked_after_signed_pair_observation(missing):
    armed = []
    def callback(holder):
        if not armed: return
        r = holder['r']
        with r.authority._mutex:
            record = r.authority._grants[id(r.native_context)]
            record['operations'] = record['operations'] - {missing}
    with native(callback=callback) as (r, owner, op, observations, holder):
        before = copy.deepcopy(r.state.rows); armed.append(True)
        with pytest.raises(ForwardJournalError): op.session.counters.reserve_transmit()
        assert r.state.rows == before and op.session.counters._uncertain and owner._uncertain
        assert not r.hardware._mutex.locked()


@pytest.mark.parametrize('method', ['receiver_instance', 'receiver_class', 'owner_instance', 'owner_class'])
def test_native_original_receiver_and_action_method_identities_are_required(method, monkeypatch):
    with native() as (r, owner, op, observations, holder):
        j = op.session.counters; before = copy.deepcopy(r.state.rows); receiver = owner._composition
        target, name = dict(receiver_instance=(receiver, '_available'), receiver_class=(type(receiver), '_available'),
            owner_instance=(owner, 'journal_action_check'), owner_class=(type(owner), 'journal_action_check'))[method]
        monkeypatch.setattr(target, name, lambda *args:True)
        with pytest.raises(ForwardJournalError): j.reserve_transmit()
        assert r.state.rows == before and j._uncertain and not r.hardware._mutex.locked()
        monkeypatch.undo()


@pytest.mark.parametrize('mutation', ['closed', 'uncertain', 'callback', 'client_identity'])
def test_final_receiver_binding_callback_cannot_mutate_after_its_initial_availability_check(mutation, monkeypatch):
    armed = []
    def callback(holder):
        if 'op' in holder: armed.append(True)
    with native(callback=callback) as (r, owner, op, observations, holder):
        receiver = owner._composition; j = op.session.counters; before = copy.deepcopy(r.state.rows)
        original = receiver.probe._binding; changed = []
        def binding():
            result = original()
            if armed and j._guarding and not changed:
                changed.append(True)
                if mutation == 'closed': receiver._closed = True
                elif mutation == 'uncertain': receiver._uncertain = True
                elif mutation == 'callback': receiver._crypto_action = None
                else: monkeypatch.setattr(receiver.client, '_identity', copy.copy(receiver.client._identity))
            return result
        monkeypatch.setattr(receiver.probe, '_binding', binding)
        with pytest.raises(ForwardJournalError): j.reserve_transmit()
        assert changed == [True] and r.state.rows == before and j._uncertain
        assert not r.hardware._mutex.locked()
        monkeypatch.undo()


def test_registry_foreign_drain_waits_for_journal_callback_and_does_not_close_live_components():
    hooks = []; results = []
    def callback(holder):
        if hooks: hooks[0](holder)
    with native(callback=callback) as (r, owner, op, observations, holder):
        j = op.session.counters; before = copy.deepcopy(r.state.rows)
        def drain(holder):
            assert j._busy and j._guarding and owner._busy
            worker = threading.Thread(target=lambda:results.append(owner._registry.close(.02)))
            worker.start(); worker.join(2)
            assert not worker.is_alive() and not owner._composition._closed and not op.session._closed
        hooks.append(drain)
        with pytest.raises(ForwardJournalError): j.reserve_transmit()
        assert results == [False] and r.state.rows == before
        assert not owner._busy and not j._busy and not r.hardware._mutex.locked()


@pytest.mark.parametrize('record_kind', ['purpose', 'enrollment'])
def test_actual_signed_revocation_is_read_at_next_journal_guard(record_kind):
    from lantern_meshticulum.sage import noise_native_enrollment, noise_purpose
    from tests.integrations.sage.test_forward_native_provider import fixture, DEBT
    with fixture() as (r, provider, source, bind):
        bind(); op = provider.start(r.packet); j = op.session.counters
        assert j.reserve_transmit() == 1
        component = provider._purpose if record_kind == 'purpose' else provider._enrollment
        row = r.state.rows[component.record_id]; row['revision'] += 1
        value = row['payload']['body']; value['revision'] = row['revision']
        if record_kind == 'purpose':
            value.update(state='retired', retirement=dict(epoch_us=value['last_epoch_us'], monotonic_us=value['last_monotonic_us']))
            domain = noise_purpose.SIGNATURE_DOMAIN
        else:
            value['state'] = 'revoked'; domain = noise_native_enrollment.SIGNATURE_DOMAIN
        row['payload']['signature'] = base64.b64encode(r.hardware.identity._signing_key.sign(domain + _canonical(value)).signature).decode()
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardJournalError): j.reserve_transmit()
        assert r.state.rows == before and j._uncertain and op.budget.prior == DEBT and source.actual is None
        assert not r.hardware._mutex.locked()


def test_closed_actual_native_grant_cannot_authorize_another_counter():
    from sage_lantern.mesh_community import MeshRequestAccess
    from lantern_meshticulum.sage.forward_native_owner import approval_binding
    from lantern_meshticulum.sage.forward_native_provider import SETUP_SCOPES
    from tests.integrations.sage.test_forward_native_provider import fixture
    with fixture() as (r, provider, source, bind):
        info = provider.generate(); context = replace(r.context, initiator_noise_public_key=info.public_key)
        digest = approval_binding(context, r.intention, True, config_revision=provider.config_revision,
            broker_id=provider.broker_id, storage_epoch=provider.storage_epoch)
        with r.authority.grant(r.hardware.actor,
                MeshRequestAccess(r.state.epoch, lambda:r.state.owner_live), SETUP_SCOPES,
                approval_binding=digest) as grant:
            provider.bind(context, grant, remote_generation_id=str(uuid.uuid4()))
            op = provider.start(r.packet); assert op.session.counters.reserve_transmit() == 1
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardJournalError): op.session.counters.reserve_transmit()
        assert r.state.rows == before and op.session.counters._uncertain and source.actual is None
        assert not r.hardware._mutex.locked()


def test_unknown_actual_provider_counter_return_keeps_paid_debt_and_committed_reservation():
    from tests.integrations.sage.test_forward_native_provider import fixture, DEBT
    with fixture() as (r, provider, source, bind):
        bind(); op = provider.start(r.packet); j = op.session.counters
        original = r.client.workflow_put; calls = []
        def lost(**kwargs):
            result = original(**kwargs)
            if kwargs.get('record_id') == j.record_id:
                calls.append(True); raise RuntimeError('modeled loss after actual fixture CAS')
            return result
        r.client.workflow_put = lost
        with pytest.raises(ForwardJournalError): j.reserve_transmit()
        assert calls == [True] and r.state.rows[j.record_id]['payload']['body']['tx'] == 1
        assert op.budget.prior == DEBT and source.actual is None and j._uncertain
        assert not r.hardware._mutex.locked()
