# SPDX-License-Identifier: Apache-2.0
"""Fresh aggregate budget gates; native/backend/RF IO remain modeled."""
import base64
import copy
from dataclasses import asdict, replace
from types import SimpleNamespace
import uuid

import pytest
pytest.importorskip('sage_sdk', reason='compatible optional SDK required')
from lantern_meshticulum.sage.forward_budget import ForwardBudget, ForwardBudgetError, Cost, SCHEMA
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_mesh_inbox import rig, KEY
from tests.integrations.sage.test_forward_receive_journal import journal_rig
from tests.integrations.sage.test_forward_budget import budget_rig, plan, body


@pytest.fixture
def boundary(budget_rig):
    r = budget_rig
    r.full_calls = []; r.action_calls = []; r.hook = None
    def full(name, result):
        r.full_calls.append(name)
        return result
    def action(*args):
        r.action_calls.append(args)
        if r.hook is not None: r.hook()
        return r.live and r.storage and r.budget_live
    def new(callback=action):
        return ForwardBudget(r.store, context=r.context, intention=r.intention,
            signing_key=KEY, boot_id=r.budget.boot_id, limits=r.budget.limits,
            prior_debt=r.budget.prior, slots=r.budget.slots, owner_lease=r.journal._lease,
            access_check=lambda: full('access', r.live),
            storage_check=lambda: full('storage', r.storage),
            native_check=lambda *a: full('native', r.budget_live), clock=r.clock,
            workflow_access_check=lambda: r.live and r.storage,
            budget_action_check=callback)
    r.action, r.new_action = action, new
    return r


@pytest.mark.parametrize('callback', [False, 1, object()])
def test_noncallable_action_is_refused_without_storage(boundary, callback):
    with pytest.raises(ForwardBudgetError): boundary.new_action(callback)
    assert not boundary.rows


def test_none_default_retains_three_independent_callbacks_in_original_order(boundary):
    r = boundary; budget = r.new_action(None)
    budget.prepare()
    assert r.full_calls and len(r.full_calls) % 3 == 0
    assert r.full_calls == ['access', 'storage', 'native'] * (len(r.full_calls) // 3)
    assert not r.action_calls


def test_prepare_pending_consume_and_current_checks_reobserve_exact_original_arguments(boundary):
    r = boundary; r.budget = r.new_action(); b = r.budget
    b.prepare(); prior_calls = len(r.action_calls)
    child = plan(r); admission = b.pre_admit(child)
    assert b.check_pending(admission) is True and b.consume(admission) is child
    assert b.check(admission) is True and len(r.action_calls) > prior_calls
    assert all(c is r.context and i is r.intention and (p is None or p is child)
               for c, i, p in r.action_calls)
    assert not r.full_calls and not b._guarding
    before = copy.deepcopy(r.rows); r.budget_live = False
    with pytest.raises(ForwardBudgetError): b.check(admission)
    assert r.rows == before and b._uncertain and not b._guarding
    assert body(r)['allocations'][0]['cost'] == asdict(child.cost)


@pytest.mark.parametrize('original_none', [True, False])
def test_action_callback_identity_including_none_is_pinned(boundary, original_none):
    r = boundary; b = r.new_action(None if original_none else r.action)
    b._budget_action = r.action if original_none else None
    with pytest.raises(ForwardBudgetError): b.prepare()
    assert not r.rows and not r.action_calls and not r.full_calls


@pytest.mark.parametrize('field', ['context', 'intention', 'limits', 'prior', 'slots'])
def test_original_budget_objects_cannot_be_substituted_before_action(boundary, field):
    r = boundary; b = r.new_action()
    value = dict(context=replace(r.context), intention=replace(r.intention), limits=replace(b.limits),
                 prior=replace(b.prior), slots=tuple(list(b.slots)))[field]
    setattr(b, field, value)
    with pytest.raises(ForwardBudgetError): b.prepare()
    assert not r.rows and not r.action_calls


@pytest.mark.parametrize('mutation', ['context_value', 'intention_value', 'limits_value', 'prior_value',
    'plan_cost', 'plan_direction', 'plan_digest', 'active_plan', 'callback', 'close',
    'reenter_guard', 'reenter_prepare', 'raise'])
def test_callback_drift_cannot_write_or_publish_replacement_costs(boundary, mutation):
    r = boundary; r.budget = r.new_action(); b = r.budget
    b.prepare(); child = plan(r); before = copy.deepcopy(r.rows); seen = []
    def hook():
        if mutation == 'context_value': object.__setattr__(r.context, 'max_records', 31)
        elif mutation == 'intention_value':
            object.__setattr__(r.intention, 'application_deadline_monotonic_us', 140000000)
        elif mutation == 'limits_value': object.__setattr__(b.limits, 'radio_attempts', 601)
        elif mutation == 'prior_value': object.__setattr__(b.prior, 'radio_attempts', 0)
        elif mutation == 'plan_cost': object.__setattr__(child.cost, 'radio_attempts', 1)
        elif mutation == 'plan_direction': object.__setattr__(child, 'sends', not child.sends)
        elif mutation == 'plan_digest': object.__setattr__(child, 'grant_digest', 'a' * 64)
        elif mutation == 'active_plan': b._active_plan = replace(child)
        elif mutation == 'callback': b._budget_action = lambda *a: True
        elif mutation == 'close': seen.append(b.close())
        elif mutation == 'raise': raise RuntimeError('modeled action failure')
        else:
            with pytest.raises(ForwardBudgetError):
                b._guard(child) if mutation == 'reenter_guard' else b.prepare()
    r.hook = hook
    with pytest.raises(ForwardBudgetError): b.pre_admit(child)
    assert r.rows == before and b._uncertain and not b._guarding and not b._busy
    assert not b._permits and not r.lease_active
    if mutation == 'close': assert seen == [False]


@pytest.mark.parametrize('failure', ['before', 'after', 'conflict'])
def test_lost_cas_return_keeps_original_prepaid_costs_and_never_returns_handle(boundary, failure):
    r = boundary; r.budget = r.new_action(); b = r.budget
    b.prepare(); child = plan(r); r.failure = SCHEMA, failure
    with pytest.raises(ForwardBudgetError): b.pre_admit(child)
    data = body(r)
    assert data['binding']['prior_debt'] == asdict(b.prior)
    assert data['allocations'] == ([child.to_dict()] if failure == 'after' else [])
    assert not b._permits and b._uncertain and not b._guarding and b._workflow_epoch is None
    before = copy.deepcopy(r.rows); r.failure = None
    with pytest.raises(ForwardBudgetError): b.pre_admit(child)
    assert r.rows == before


@pytest.mark.parametrize('drift', ['authority', 'vault', 'boot', 'epoch', 'deadline'])
def test_actual_owner_callback_drift_blocks_current_budget_with_no_new_write(drift):
    from tests.integrations.sage.test_forward_native_owner import setup
    hooks = []
    def peer(*args):
        if hooks: hooks[0]()
        return True
    with setup(peer_observer=peer) as r:
        owner = r.create()
        op = owner.operation(r.noise[0], limits=Cost(600,120000000), prior_debt=Cost(3,1000),
                             sealed_record=r.packet)
        before = copy.deepcopy(r.state.rows)
        def mutate():
            if drift == 'authority': r.state.owner_live = False
            elif drift == 'vault': r.state.instance_id = 'f' * 64
            elif drift == 'boot': r.state.boot = str(uuid.uuid4())
            elif drift == 'epoch': r.state.now = (r.intention.application_expires_epoch_us, r.state.now[1])
            else: r.state.now = (r.state.now[0], r.intention.application_deadline_monotonic_us)
        hooks.append(mutate)
        with pytest.raises(ForwardBudgetError): op.budget.prepare()
        assert r.state.rows == before and not r.hardware._mutex.locked() and not op.budget._guarding
        owner.close()


@pytest.mark.parametrize('record_kind', ['purpose', 'enrollment'])
def test_actual_signed_key_revocation_refuses_budget_with_unchanged_debt(record_kind):
    from lantern_meshticulum.sage import noise_native_enrollment, noise_purpose
    from tests.integrations.sage.test_forward_native_provider import fixture, DEBT
    with fixture() as (r, provider, source, bind):
        bind(); op = provider.start(r.packet); assert op.budget._budget_action is not None
        op.budget.prepare()
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
        with pytest.raises(ForwardBudgetError): op.budget.prepare()
        assert r.state.rows == before and op.budget.prior == DEBT and source.actual is None
        assert not r.hardware._mutex.locked() and not op.budget._guarding


def test_actual_provider_lost_allocation_return_retains_full_child_and_prior_debt():
    from tests.integrations.sage.test_forward_native_provider import fixture, DEBT
    with fixture() as (r, provider, source, bind):
        bind(); op = provider.start(r.packet)
        child = plan(SimpleNamespace(budget=op.budget)); original = r.client.workflow_put
        def lost(**arguments):
            value = original(**arguments)
            if arguments['record_id'] == op.budget.record_id:
                raise RuntimeError('modeled return lost after actual signed CAS')
            return value
        r.client.workflow_put = lost
        with pytest.raises(ForwardBudgetError): op.budget.pre_admit(child)
        data = r.state.rows[op.budget.record_id]['payload']['body']
        assert data['allocations'] == [child.to_dict()] and data['binding']['prior_debt'] == asdict(DEBT)
        assert not op.budget._permits and op.budget._uncertain and not r.hardware._mutex.locked()


def test_closed_actual_full_approval_cannot_reauthorize_original_budget():
    from sage_lantern.mesh_community import MeshRequestAccess
    from lantern_meshticulum.sage.forward_native_owner import approval_binding
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
            op = provider.start(r.packet); op.budget.prepare()
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardBudgetError): op.budget.prepare()
        assert r.state.rows == before and op.budget._uncertain and not op.budget._guarding
        assert not r.hardware._mutex.locked()
