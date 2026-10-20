# SPDX-License-Identifier: Apache-2.0
"""Fresh operation boundaries with actual crypto/journals and modeled native IO."""
import base64
import copy
from contextlib import contextmanager
from dataclasses import replace
import threading
from types import SimpleNamespace
import uuid

import pytest
pytest.importorskip('sage_sdk', reason='compatible optional SDK required')

from meshticulum.bench.services import ServiceTransfer
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_operation import (
    ForwardOperation, ForwardOperationError, ForwardStageRunner, describe_child)
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_forward_operation import operation_pair, stage_pair
from tests.test_meshticulum_private_carrier import carrier_pair


@pytest.fixture
def actions(operation_pair):
    p = operation_pair
    controls = []
    for index, old in enumerate(p.operations):
        state = SimpleNamespace(events=[], calls=[], hook=None, clock_hook=None, allowed=True)
        original_authorize, original_clock = old.session.authorize, old.session.clock
        def authorize(state=state, original=original_authorize):
            state.events.append('authorize')
            return original()
        def clock(state=state, original=original_clock):
            state.events.append('time')
            if state.clock_hook is not None: state.clock_hook()
            return original()
        old.session.authorize, old.session.clock = authorize, clock
        def native(*args, state=state):
            state.events.append('native')
            return True
        def aggregate(*args, state=state):
            state.events.append('action'); state.calls.append(args)
            if state.hook is not None: state.hook()
            return args[-1]() if state.allowed is True else state.allowed
        def new(callback=aggregate, old=old, native=native, index=index):
            return ForwardOperation(old.session, old.budget,
                sealed_record=p.packet if index == 0 else None, receiver=old.receiver,
                native_check=native, operation_action_check=callback)
        state.aggregate, state.new = aggregate, new
        p.operations[index] = new()
        controls.append(state)
    p.actions = controls
    return p


@pytest.mark.parametrize('callback', [False, 1, object()])
def test_noncallable_operation_action_refuses_before_any_new_claim(actions, callback):
    p = actions; before = copy.deepcopy(p.states[0].rows)
    with pytest.raises(ForwardOperationError): p.actions[0].new(callback)
    assert p.states[0].rows == before and not p.actions[0].events


def test_none_retains_original_native_authorize_time_order(actions):
    p = actions; op = p.actions[0].new(None)
    before = copy.deepcopy(p.states[0].rows)
    op._check(1, True)
    assert p.actions[0].events == ['native', 'authorize', 'time']
    assert not p.actions[0].calls and p.states[0].rows == before


@pytest.mark.parametrize('index', [0, 1])
def test_each_boundary_invokes_original_authorizer_once_with_original_objects(actions, index):
    p = actions; op = p.operations[index]; r = p.actions[index]
    before = copy.deepcopy(p.states[index].rows)
    for _ in range(2): op._check(1, index == 0)
    assert r.events == ['action', 'authorize', 'time'] * 2
    assert len(r.calls) == 2 and p.states[index].rows == before
    assert all(c is op.session.context and i is op.budget.intention and k == 1
        and s is (index == 0) and session is op.session and authorize is op.session.authorize
        for c, i, k, s, session, authorize in r.calls)
    assert not op._action_observing and not op._busy


@pytest.mark.parametrize('original_none', [True, False])
def test_action_identity_including_none_cannot_be_substituted(actions, original_none):
    p = actions; r = p.actions[0]; op = r.new(None if original_none else r.aggregate)
    op._operation_action = r.aggregate if original_none else None
    before = copy.deepcopy(p.states[0].rows)
    with pytest.raises(ForwardOperationError): op._check(1, True)
    assert not r.events and p.states[0].rows == before


@pytest.mark.parametrize('kind,sends', [(True, True), (0, True), (6, False), (1, 1), (1, False)])
def test_phase_requires_exact_int_bool_and_original_endpoint_role(actions, kind, sends):
    p = actions; before = copy.deepcopy(p.states[0].rows)
    with pytest.raises(ForwardOperationError): p.operations[0]._check(kind, sends)
    assert not p.actions[0].events and p.states[0].rows == before


@pytest.mark.parametrize('mutation', ['session', 'budget', 'receiver', 'context', 'budget_context',
    'intention', 'native', 'authorize', 'clock', 'counter', 'role', 'deadline', 'callback',
    'context_value', 'intention_value', 'position', 'active', 'cached', 'close',
    'reenter_check', 'reenter_packet', 'raise'])
def test_callback_mutation_or_caught_reentry_cannot_return_permission(actions, mutation, monkeypatch):
    p = actions; op = p.operations[0]; r = p.actions[0]
    before = copy.deepcopy(p.states[0].rows)
    original_session = op.session
    def hook():
        if mutation == 'session': monkeypatch.setattr(op, 'session', copy.copy(op.session))
        elif mutation == 'budget': monkeypatch.setattr(op, 'budget', copy.copy(op.budget))
        elif mutation == 'receiver': monkeypatch.setattr(op, 'receiver', p.owners[0])
        elif mutation == 'context': monkeypatch.setattr(op.session, 'context', replace(p.context))
        elif mutation == 'budget_context': monkeypatch.setattr(op.budget, 'context', replace(p.context))
        elif mutation == 'intention': monkeypatch.setattr(op.budget, 'intention', replace(p.intention))
        elif mutation == 'native': monkeypatch.setattr(op, '_native', lambda *args: True)
        elif mutation == 'authorize': monkeypatch.setattr(op.session, 'authorize', lambda: True)
        elif mutation == 'clock': monkeypatch.setattr(op.session, 'clock', lambda: p.clock())
        elif mutation == 'counter': monkeypatch.setattr(op.session, 'counters', copy.copy(op.session.counters))
        elif mutation == 'role': monkeypatch.setattr(op.session, 'initiator', 1)
        elif mutation == 'deadline': monkeypatch.setattr(op.session, 'deadline', op.session.deadline + 1)
        elif mutation == 'callback': monkeypatch.setattr(op, '_operation_action', lambda *args: True)
        elif mutation == 'context_value': object.__setattr__(p.context, 'max_records', 65)
        elif mutation == 'intention_value': object.__setattr__(p.intention, 'packet_bytes', p.intention.packet_bytes + 1)
        elif mutation == 'position': op.position += 1
        elif mutation == 'active': monkeypatch.setattr(op, 'active', object())
        elif mutation == 'cached': op.cached = b'changed'
        elif mutation == 'close': assert op.close() is False
        elif mutation == 'raise': raise RuntimeError('modeled operation callback failure')
        else:
            with pytest.raises(ForwardOperationError):
                op._check(1, True) if mutation == 'reenter_check' else op.packet()
    r.hook = hook
    with pytest.raises(Exception): op._check(1, True)
    assert p.states[0].rows == before and op._uncertain and op._closed and original_session._closed
    assert not op._action_observing and not op._busy
    monkeypatch.undo()


@pytest.mark.parametrize('allowed', [False, 1, None])
def test_nontrue_action_result_never_reaches_time_or_counter(actions, allowed):
    p = actions; r = p.actions[0]; r.allowed = allowed
    before = copy.deepcopy(p.states[0].rows)
    with pytest.raises(ForwardOperationError): p.operations[0]._check(1, True)
    assert r.events == ['action'] and p.states[0].rows == before


@pytest.mark.parametrize('drift', ['epoch_expired', 'deadline', 'epoch_regressed', 'mono_regressed'])
def test_original_time_limits_and_monotonicity_are_checked_after_action(actions, drift):
    p = actions; op = p.operations[0]; r = p.actions[0]
    op._check(1, True); before = copy.deepcopy(p.states[0].rows)
    epoch, mono = op._last_clock
    def change():
        if drift == 'epoch_expired': p.clock.epoch = p.context.expires_epoch_us
        elif drift == 'deadline': p.clock.mono = op.session.deadline
        elif drift == 'epoch_regressed': p.clock.epoch = epoch - 1
        else: p.clock.mono = mono - 1
    r.hook = change
    with pytest.raises(Exception): op._check(1, True)
    assert op._closed and p.states[0].rows == before and not op._action_observing


@pytest.mark.parametrize('mutation', ['context', 'authorize', 'clock', 'position', 'reentry', 'close'])
def test_final_clock_callback_cannot_replace_original_bindings_or_hide_reentry(actions, mutation, monkeypatch):
    p = actions; op = p.operations[0]; r = p.actions[0]
    before = copy.deepcopy(p.states[0].rows)
    def hook():
        if mutation == 'context': monkeypatch.setattr(op.session, 'context', replace(p.context))
        elif mutation == 'authorize': monkeypatch.setattr(op.session, 'authorize', lambda: True)
        elif mutation == 'clock': monkeypatch.setattr(op.session, 'clock', p.clock)
        elif mutation == 'position': op.position += 1
        elif mutation == 'close': assert op.close() is False
        else:
            with pytest.raises(ForwardOperationError): op._check(1, True)
    r.clock_hook = hook
    with pytest.raises(Exception): op._check(1, True)
    assert r.events == ['action', 'authorize', 'time']
    assert op._closed and op._uncertain and p.states[0].rows == before
    assert not op._busy and not op._action_observing
    monkeypatch.undo()


@pytest.mark.parametrize('foreign', ['thread', 'pid'])
def test_foreign_owner_cannot_observe_or_reauthorize(actions, foreign, monkeypatch):
    p = actions; op = p.operations[0]; before = copy.deepcopy(p.states[0].rows); errors = []
    def call():
        try: op._check(1, True)
        except ForwardOperationError: errors.append(True)
    if foreign == 'thread':
        worker = threading.Thread(target=call); worker.start(); worker.join(2)
        assert not worker.is_alive()
    else:
        monkeypatch.setattr(op, '_owner', (op._owner[0] + 1, op._owner[1])); call()
    assert errors == [True] and not p.actions[0].events and p.states[0].rows == before


@contextmanager
def held_runner(tmp_path, p):
    op = p.operations[0]; packet = op.packet()
    service = dict(kind='protected_message', format='MPF2.record',
                   application_expires_epoch_us=p.context.expires_epoch_us)
    with carrier_pair(tmp_path/'carrier', packet, p.clock, 'unit-a', 'unit-b', 83,
            service=service, original_expires_epoch_us=p.context.expires_epoch_us) as pair:
        transfer = ServiceTransfer(pair.sender); plan = describe_child(transfer, slot=0, kind=1)
        admission = op.budget.pre_admit(plan); child_calls = []
        def native(*args): child_calls.append(p.states[0].held); return True
        runner = ForwardStageRunner(transfer, op, admission, native_check=native,
            retire_child=lambda t: True, payload=packet)
        yield runner, pair, child_calls


def test_all_four_existing_held_boundaries_keep_operation_budget_and_native_child_checks(tmp_path, actions):
    p = actions; r = p.actions[0]
    with held_runner(tmp_path, p) as (runner, pair, child_calls):
        seen = []; r.hook = lambda: seen.append(('check', p.states[0].held))
        budget_calls = []; original = runner.operation.budget._native
        def budget_native(*args):
            budget_calls.append(p.states[0].held)
            return original(*args)
        runner.operation.budget._native = budget_native
        before = copy.deepcopy(p.states[0].rows)
        with runner._held(): seen.append(('body', True))
        assert seen == [('check', False), ('check', True), ('body', True), ('check', True), ('check', False)]
        assert child_calls == [False, True, True, False]
        assert budget_calls == [False, False, True, True, True, True, False, False]
        assert p.states[0].rows == before and not p.states[0].held
        assert not any(worker.sent for worker in pair.workers)


@pytest.mark.parametrize('boundary', [1, 2, 3, 4])
def test_each_held_boundary_freshly_refuses_revocation_and_retains_prepaid_debt(tmp_path, actions, boundary):
    p = actions; r = p.actions[0]
    with held_runner(tmp_path, p) as (runner, pair, child_calls):
        seen = []; body = []; before = copy.deepcopy(p.states[0].rows)
        def hook():
            seen.append(p.states[0].held)
            if len(seen) == boundary: p.states[0].live = False
        r.hook = hook
        with pytest.raises(Exception):
            with runner._held(): body.append(True)
        assert len(seen) == boundary and len(child_calls) == boundary - 1
        assert body == ([] if boundary <= 2 else [True])
        assert p.states[0].rows == before and not p.states[0].held
        assert len(before[p.budgets[0].record_id]['payload']['body']['allocations']) == 1
        assert not any(worker.sent for worker in pair.workers)


def test_real_crypto_all_five_modeled_stages_include_next_reply_before_position_advance(tmp_path, actions):
    p = actions; seen = [[], []]
    for index, r in enumerate(p.actions):
        r.hook = lambda index=index: seen[index].append((p.operations[index].position,
                                                        p.actions[index].calls[-1][2:4]))
    for index in range(5): stage_pair(tmp_path, p, index, 'loss')
    assert (0, (2, True)) in seen[1] and (2, (4, True)) in seen[1]
    assert p.operations[1].settle().status == 'admitted'
    for index, budget in enumerate(p.budgets):
        data = p.states[index].rows[budget.record_id]['payload']['body']
        assert len(data['allocations']) == 5
        assert sum(x['cost']['radio_attempts'] for x in data['allocations']) == 1280
        assert sum(x['cost']['estimated_airtime_us'] for x in data['allocations']) == 300000000


@contextmanager
def native_operation(*, initiator=True, peer=None):
    from tests.integrations.sage.test_forward_native_owner import setup
    with setup(initiator=initiator, peer_observer=peer) as r:
        owner = r.create()
        op = owner.operation(r.noise[0 if initiator else 1], limits=Cost(2000,300000000),
            prior_debt=Cost(3,1000), sealed_record=r.packet if initiator else None)
        try: yield r, owner, op
        finally: owner.close()


@pytest.mark.parametrize('initiator', [True, False])
def test_native_operation_reads_full_key_pair_once_per_current_or_next_reply_boundary(initiator):
    seen = []
    with native_operation(initiator=initiator, peer=lambda *args: seen.append(args) or True) as (r, owner, op):
        seen.clear(); before = copy.deepcopy(r.state.rows)
        for kind in (1, 2, 3, 4, 5):
            op._check(kind, (kind % 2 == 1) == initiator)
        assert len(seen) == 5 and op.position == 0 and r.state.rows == before
        assert not owner._operation_observing and not owner._busy and not op._busy
        assert all(d == r.context.digest for _, _, d in seen)


@pytest.mark.parametrize('drift', ['authority', 'vault', 'boot', 'epoch', 'deadline', 'reentry', 'close'])
def test_native_callback_drift_is_refused_without_new_write_or_debt_change(drift):
    hooks = []
    def peer(*args):
        if hooks: hooks[0]()
        return True
    with native_operation(peer=peer) as (r, owner, op):
        before = copy.deepcopy(r.state.rows)
        def mutate():
            if drift == 'authority': r.state.owner_live = False
            elif drift == 'vault': r.state.instance_id = 'f' * 64
            elif drift == 'boot': r.state.boot = str(uuid.uuid4())
            elif drift == 'epoch': r.state.now = (r.context.expires_epoch_us, r.state.now[1])
            elif drift == 'deadline': r.state.now = (r.state.now[0], r.intention.application_deadline_monotonic_us)
            elif drift == 'close': assert owner.close() is False
            else:
                with pytest.raises(Exception):
                    owner.operation_action_check(r.context, r.intention, 1, True, op.session, op.session.authorize)
        hooks.append(mutate)
        with pytest.raises(Exception): op._check(1, True)
        assert r.state.rows == before and op.budget.prior == Cost(3,1000)
        assert owner._uncertain and op._uncertain and not r.hardware._mutex.locked()
        assert not owner._operation_observing and not owner._busy and not op._action_observing


@pytest.mark.parametrize('method', ['receiver_instance', 'receiver_class', 'owner_instance', 'owner_class'])
def test_native_original_bound_authorizer_and_action_methods_are_required(method, monkeypatch):
    with native_operation() as (r, owner, op):
        before = copy.deepcopy(r.state.rows); receiver = owner._composition
        target, name = dict(receiver_instance=(receiver, '_authorize_crypto'),
            receiver_class=(type(receiver), '_authorize_crypto'), owner_instance=(owner, 'operation_action_check'),
            owner_class=(type(owner), 'operation_action_check'))[method]
        monkeypatch.setattr(target, name, lambda *args: True)
        with pytest.raises(Exception): op._check(1, True)
        assert r.state.rows == before and op._uncertain and not owner._operation_observing
        monkeypatch.undo()


def test_native_direct_action_call_is_not_a_standalone_permission_or_cache():
    with native_operation() as (r, owner, op):
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(Exception):
            owner.operation_action_check(r.context, r.intention, 1, True, op.session, op.session.authorize)
        assert owner._uncertain and r.state.rows == before
        with pytest.raises(Exception): op._check(1, True)


def test_registry_foreign_drain_does_not_close_components_while_action_callback_is_active():
    hooks = []; observations = []
    def peer(*args):
        if hooks: hooks[0]()
        return True
    with native_operation(peer=peer) as (r, owner, op):
        before = copy.deepcopy(r.state.rows)
        def drain():
            assert op._busy and owner._operation_observing
            worker = threading.Thread(target=lambda: observations.append(owner._registry.close(.02)))
            worker.start(); worker.join(2); assert not worker.is_alive()
            assert not owner._composition._closed and not op.session._closed
        hooks.append(drain)
        with pytest.raises(Exception): op._check(1, True)
        assert observations == [False] and r.state.rows == before
        assert not owner._operation_observing and not op._busy and not r.hardware._mutex.locked()


@pytest.mark.parametrize('record_kind', ['purpose', 'enrollment'])
def test_actual_signed_revocation_is_observed_at_next_operation_boundary(record_kind):
    from lantern_meshticulum.sage import noise_native_enrollment, noise_purpose
    from tests.integrations.sage.test_forward_native_provider import fixture, DEBT
    with fixture() as (r, provider, source, bind):
        bind(); op = provider.start(r.packet); op._check(1, True)
        component = provider._purpose if record_kind == 'purpose' else provider._enrollment
        row = r.state.rows[component.record_id]; row['revision'] += 1
        body = row['payload']['body']; body['revision'] = row['revision']
        if record_kind == 'purpose':
            body.update(state='retired', retirement=dict(epoch_us=body['last_epoch_us'],
                                                        monotonic_us=body['last_monotonic_us']))
            domain = noise_purpose.SIGNATURE_DOMAIN
        else:
            body['state'] = 'revoked'; domain = noise_native_enrollment.SIGNATURE_DOMAIN
        row['payload']['signature'] = base64.b64encode(
            r.hardware.identity._signing_key.sign(domain + _canonical(body)).signature).decode()
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(Exception): op._check(1, True)
        assert r.state.rows == before and op.budget.prior == DEBT and source.actual is None
        assert op._uncertain and not provider._owner._operation_observing
        assert not r.hardware._mutex.locked() and provider._purpose._key is None


def test_closed_actual_native_approval_cannot_reauthorize_at_next_boundary():
    from sage_lantern.mesh_community import MeshRequestAccess
    from lantern_meshticulum.sage.forward_native_owner import approval_binding
    from lantern_meshticulum.sage.forward_native_provider import SETUP_SCOPES
    from tests.integrations.sage.test_forward_native_provider import fixture
    with fixture() as (r, provider, source, bind):
        info = provider.generate(); context = replace(r.context, initiator_noise_public_key=info.public_key)
        digest = approval_binding(context, r.intention, True, config_revision=provider.config_revision,
            broker_id=provider.broker_id, storage_epoch=provider.storage_epoch)
        with r.authority.grant(r.hardware.actor,
                MeshRequestAccess(r.state.epoch, lambda: r.state.owner_live), SETUP_SCOPES,
                approval_binding=digest) as native:
            provider.bind(context, native, remote_generation_id=str(uuid.uuid4()))
            op = provider.start(r.packet); op._check(1, True)
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(Exception): op._check(1, True)
        assert op._uncertain and r.state.rows == before
        assert not provider._owner._operation_observing and not r.hardware._mutex.locked()
