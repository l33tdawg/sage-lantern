# SPDX-License-Identifier: Apache-2.0
"""Real relay authority and signed CAS; accounting, backend and RF are models."""
from contextlib import contextmanager
from dataclasses import asdict
import copy
import sys
import threading
import uuid

import pytest
from lantern_meshticulum.sage.forward_budget import Cost, ForwardBudget
from lantern_meshticulum.sage.forward_native_relay import ForwardNativeRelay, ForwardNativeRelayError, RELAY_SCOPES
from tests.integrations.sage.test_forward_native_relay import (
    Accounting, DEBT, relay_rig, prepare_stage, begin,
)


@contextmanager
def guarded_trace(owner):
    """Count original code entries, without substituting any native method."""
    assert sys.getprofile() is None
    records = []; active = []
    methods = {ForwardNativeRelay.budget_action_check.__code__: 'aggregate',
               ForwardNativeRelay.access_check.__code__: 'access',
               ForwardNativeRelay._native.__code__: 'native',
               Accounting.observe.__code__: 'accounting'}
    def observe(frame, event, argument):
        if frame.f_code is ForwardBudget._guard.__code__ and frame.f_locals.get('self') is owner._budget:
            if event == 'call':
                row = dict(aggregate=0, access=0, native=0, accounting=0,
                           slot=None if frame.f_locals['plan'] is None else frame.f_locals['plan'].slot)
                records.append(row); active.append(row)
            elif event == 'return': active.pop()
        elif event == 'call' and active and frame.f_code in methods:
            active[-1][methods[frame.f_code]] += 1
    sys.setprofile(observe)
    try: yield records
    finally: sys.setprofile(None)
    assert not active


def test_every_guard_keeps_one_complete_fresh_observation_and_both_prepayments(tmp_path):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        with guarded_trace(r.owner) as records:
            children = begin(r, stage, incoming, outgoing)
        assert {row['slot'] for row in records} == {None, 0, 1}
        assert records and all((row['aggregate'], row['access'], row['native'], row['accounting']) ==
                               (1, 1, 2, 1) for row in records)
        body = r.state.rows[r.owner._budget.record_id]['payload']['body']
        assert body['binding']['prior_debt'] == asdict(DEBT)
        assert body['allocations'] == [incoming.plan.to_dict(), outgoing.plan.to_dict()]
        assert all(child.admission.bound for child in children)
        assert r.accounting.actual is None and not r.owner._budget_observing
        assert all(plan['cost'] == asdict(Cost(256, 60000000)) for plan in body['allocations'])
        # Another action must observe again; no positive result is retained.
        before = r.accounting.calls
        with guarded_trace(r.owner) as again: r.owner.step()
        assert r.accounting.calls > before and again
        assert all(row['accounting'] == row['aggregate'] == 1 for row in again)


@pytest.mark.parametrize('side', [0, 1])
@pytest.mark.parametrize('field,value', [('slot', 'float'), ('kind', True), ('sends', 'int'),
    ('application_expires_epoch_us', 'float'), ('application_deadline_monotonic_us', 'float'),
    ('packet_bytes', 'float'), ('cipher_frame_limit', 'float'), ('cost_attempts', 256.0),
    ('cost_zero', False)])
def test_same_value_noncanonical_types_never_prepay_either_adjacent_child(tmp_path, side, field, value):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        plan = (incoming, outgoing)[side].plan
        target, name = (plan.cost, 'radio_attempts' if field == 'cost_attempts' else 'ip_frames') if field.startswith('cost_') else (plan, field)
        original = getattr(target, name)
        changed = float(original) if value == 'float' else int(original) if value == 'int' else value
        object.__setattr__(target, name, changed)
        try:
            with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
            assert not r.state.rows and not incoming.opened and not outgoing.opened
        finally: object.__setattr__(target, name, original)


@pytest.mark.parametrize('scope', RELAY_SCOPES)
def test_each_original_scope_revoked_during_budget_observation_prevents_write(tmp_path, scope):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        def revoke():
            if r.owner._budget._guarding:
                record = r.authority._grants[id(r.owner.native_context)]
                record['operations'] = record['operations'] - {scope}
        r.accounting.hook = revoke
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert not r.state.rows and not incoming.opened and not outgoing.opened
        assert r.accounting.actual is None and r.accounting.debt is DEBT


@pytest.mark.parametrize('change', ['owner', 'boot', 'vault', 'native_epoch', 'expiry', 'accounting_binding',
    'unknown_usage', 'debt', 'source_object', 'source_method', 'store', 'store_method', 'lease',
    'callback', 'access_method', 'budget_context', 'plan_cost', 'plan_deadline', 'active_stage',
    'closed_budget', 'close', 'caught_reentry', 'foreign_drain'])
def test_callback_drift_is_fenced_before_any_write_or_carrier_open(tmp_path, monkeypatch, change):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        fired = []; results = []
        with monkeypatch.context() as patch:
            def mutate():
                owner = r.owner; budget = owner._budget
                if not budget._guarding or fired: return
                fired.append(True)
                if change == 'owner': r.state.owner_live = False
                elif change == 'boot': r.state.boot = str(uuid.uuid4())
                elif change == 'vault': r.state.instance_id = 'f' * 64
                elif change == 'native_epoch': r.state.epoch = 'f' * 32
                elif change == 'expiry': r.clock.advance(100000000)
                elif change == 'accounting_binding': r.accounting.evidence = 'f' * 64
                elif change == 'unknown_usage': r.accounting.actual = Cost()
                elif change == 'debt': r.accounting.debt = Cost()
                elif change == 'source_object': patch.setattr(owner, 'accounting', Accounting(r))
                elif change == 'source_method': patch.setattr(Accounting, 'observe', lambda *args: True)
                elif change == 'store': patch.setattr(owner, '_store', copy.copy(owner._store))
                elif change == 'store_method': patch.setattr(type(owner._store), 'get', lambda *args, **kw: None)
                elif change == 'lease': patch.setattr(owner, 'owner_lease', lambda: None)
                elif change == 'callback': patch.setattr(budget, '_budget_action', None)
                elif change == 'access_method': patch.setattr(owner, 'access_check', lambda: True)
                elif change == 'budget_context': patch.setattr(budget, 'context', copy.copy(r.context))
                elif change == 'plan_cost': object.__setattr__(incoming.plan.cost, 'radio_attempts', 1)
                elif change == 'plan_deadline': object.__setattr__(outgoing.plan, 'application_deadline_monotonic_us', 1)
                elif change == 'active_stage': patch.setattr(owner, '_active', tuple(list(owner._active)))
                elif change == 'closed_budget': results.append(budget.close())
                elif change == 'close': results.append(owner.close())
                elif change == 'caught_reentry':
                    with pytest.raises(ForwardNativeRelayError): owner.budget_action_check(r.context, r.intention, None)
                else:
                    thread = threading.Thread(target=lambda: results.append(r.hardware.close(.01)))
                    thread.start(); thread.join(1)
                    assert not thread.is_alive() and not r.accounting.closed
            r.accounting.hook = mutate
            with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
            assert fired and not r.state.rows and not incoming.opened and not outgoing.opened
            assert r.owner._uncertain and not r.owner._budget_observing and not r.owner._budget._guarding
            assert not r.owner._budget._busy and not r.hardware._mutex.locked()
            if change in ('closed_budget', 'close', 'foreign_drain'): assert results == [False]
            r.accounting.hook = None


@pytest.mark.parametrize('mode', ['before', 'after', 'conflict'])
def test_actual_signed_allocation_cas_failure_retains_unknown_full_debt(tmp_path, monkeypatch, mode):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        original = r.client.workflow_put
        def put(**arguments):
            if arguments['record_id'] == r.owner._budget.record_id and arguments['payload']['body']['allocations']:
                if mode == 'before': raise RuntimeError('modeled dispatch failure')
                if mode == 'conflict': return original(**{**arguments, 'expected_revision': arguments['expected_revision'] + 1})
                original(**arguments)
                raise RuntimeError('modeled lost return after actual signed CAS')
            return original(**arguments)
        monkeypatch.setattr(r.client, 'workflow_put', put)
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        body = r.state.rows[r.owner._budget.record_id]['payload']['body']
        assert body['binding']['prior_debt'] == asdict(DEBT)
        assert body['allocations'] == ([incoming.plan.to_dict()] if mode == 'after' else [])
        assert not r.owner._budget._permits and not incoming.opened and not outgoing.opened
        assert r.accounting.actual is None and r.accounting.debt is DEBT
        saved = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert r.state.rows == saved


@pytest.mark.parametrize('slot,name', [(0, '_access'), (1, '_storage'), (2, '_native'), (3, '_clock')])
@pytest.mark.parametrize('during', [False, True])
def test_consistent_stored_callback_and_budget_slot_substitution_is_refused(tmp_path, slot, name, during):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        changed = []; called = []
        def replace():
            if changed: return
            changed.append(True)
            def counterfeit(*args): called.append(True); return True
            callbacks = list(r.owner._budget_callbacks); callbacks[slot] = counterfeit
            r.owner._budget_callbacks = tuple(callbacks)
            setattr(r.owner._budget, name, counterfeit)
        if during:
            r.accounting.hook = lambda: replace() if r.owner._budget._guarding else None
        else: replace()
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert changed and not called and not r.state.rows and not incoming.opened and not outgoing.opened
        assert r.owner._uncertain and not r.owner._budget_observing


def test_callable_with_forged_bound_method_attributes_cannot_supply_access(tmp_path):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        called = []
        class Counterfeit:
            def __init__(self):
                self.__self__ = r.owner; self.__func__ = ForwardNativeRelay.access_check
            def __call__(self): called.append(True); return True
        callback = Counterfeit()
        r.owner._budget_callbacks = (callback, *r.owner._budget_callbacks[1:])
        r.owner._budget._access = callback
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert not called and not r.state.rows and not incoming.opened and not outgoing.opened


@pytest.mark.parametrize('shape', ['short', 'long', 'list'])
@pytest.mark.parametrize('during', [False, True])
def test_callback_tuple_shape_cannot_omit_a_trailing_clock_pin(tmp_path, shape, during):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        changed = []
        def replace():
            if changed: return
            changed.append(True); original = r.owner._budget_callbacks
            r.owner._budget_callbacks = original[:3] if shape == 'short' else (*original, original[-1]) if shape == 'long' else list(original)
        if during:
            r.accounting.hook = lambda: replace() if r.owner._budget._guarding else None
        else: replace()
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert changed and not r.state.rows and not incoming.opened and not outgoing.opened
        assert r.owner._uncertain and not r.owner._budget_observing
