# SPDX-License-Identifier: Apache-2.0
"""Protected CAS parent prepayment; native policy/caps are modeled."""
from dataclasses import replace
import copy
import pytest
pytest.importorskip('sage_sdk',reason='compatible optional SDK required')
from lantern_meshticulum.sage.forward_budget import ForwardBudget,ForwardBudgetError,Cost,ChildPlan,Admission,SCHEMA
from tests.integrations.sage.test_mesh_inbox import rig,KEY
from tests.integrations.sage.test_forward_receive_journal import journal_rig,BOOT

@pytest.fixture
def budget_rig(journal_rig):
    r=journal_rig;r.budget_live=True
    def new(limits=Cost(600,120000000,2048,2000000,2000000),prior=Cost(3,1000)):
        return ForwardBudget(r.store,context=r.context,intention=r.intention,signing_key=KEY,boot_id=BOOT,
            limits=limits,prior_debt=prior,slots=((1,False),(2,True),(3,False),(4,True),(5,False)),
            owner_lease=r.journal._lease,access_check=lambda:r.live,storage_check=lambda:r.storage,
            native_check=lambda ctx,intention,plan:r.budget_live,clock=r.clock)
    r.new_budget=new;r.budget=new();return r

def plan(r,slot=0,**changes):
    kind,sends=r.budget.slots[slot]
    p=ChildPlan(slot,kind,sends,('ab' if slot==0 else f'{slot:02x}')*32,'cd'*32,77,'lora','ef'*32,
        BOOT,180000000,130000000,Cost(100,10000000),512)
    return replace(p,**changes)

def body(r):return r.rows[r.budget.record_id]['payload']['body']

def test_full_maxima_are_prepaid_once_with_prior_debt_and_no_handle_recovery(budget_rig):
    r=budget_rig;r.budget.prepare();a=r.budget.pre_admit(plan(r));before=copy.deepcopy(r.rows)
    assert body(r)['binding']['prior_debt']['radio_attempts']==3
    assert body(r)['allocations'][0]['cost']==plan(r).to_dict()['cost']
    assert r.budget.consume(a)==plan(r) and r.budget.check(a)
    with pytest.raises(ForwardBudgetError):r.budget.consume(a)
    assert r.rows==before
    fresh=r.new_budget()
    with pytest.raises(ForwardBudgetError):fresh.consume(Admission(fresh,a.plan,a.revision))
    assert r.rows==before

@pytest.mark.parametrize('failure',['before','after','conflict'])
def test_unknown_allocation_return_fences_and_never_restores_spent_permission(budget_rig,failure):
    r=budget_rig;r.budget.prepare();r.failure=(SCHEMA,failure)
    with pytest.raises(ForwardBudgetError):r.budget.pre_admit(plan(r))
    assert r.budget._uncertain and (len(body(r)['allocations'])==1)==(failure=='after')
    r.failure=None
    fresh=r.new_budget()
    if failure=='after':
        with pytest.raises(ForwardBudgetError):fresh.pre_admit(plan(r))
    else:assert fresh.pre_admit(plan(r)).plan==plan(r)

@pytest.mark.parametrize('dimension',['radio_attempts','estimated_airtime_us','ip_frames','tx_stream_bytes','rx_stream_bytes'])
def test_each_parent_dimension_exhausts_before_second_child(budget_rig,dimension):
    r=budget_rig
    cost=Cost(100,10000000) if dimension.startswith(('radio','estimated')) else Cost(ip_frames=10,tx_stream_bytes=1000,rx_stream_bytes=1000)
    limits=dict(r.budget.limits.__dict__);limits[dimension]=getattr(cost,dimension)
    r.budget=r.new_budget(Cost(**limits),Cost());p=plan(r,cost=cost,carrier='lora' if cost.radio_attempts else 'tcp')
    r.budget.pre_admit(p);before=copy.deepcopy(r.rows)
    with pytest.raises(ForwardBudgetError):r.budget.pre_admit(plan(r,1,cost=cost,carrier=p.carrier))
    assert r.rows==before

@pytest.mark.parametrize('change',['stage','direction','digest','expiry','deadline','boot'])
def test_ordered_slot_and_original_binding_cannot_be_evaded_by_fresh_descriptor(budget_rig,change):
    r=budget_rig;r.budget.pre_admit(plan(r));before=copy.deepcopy(r.rows);p=plan(r,1)
    if change=='stage':p=replace(p,kind=1)
    elif change=='direction':p=replace(p,sends=False)
    elif change=='digest':p=replace(p,grant_digest=plan(r).grant_digest)
    elif change=='expiry':p=replace(p,application_expires_epoch_us=180000001)
    elif change=='deadline':p=replace(p,application_deadline_monotonic_us=130000001)
    else:p=replace(p,host_boot_id='31f9c178-6090-40d9-9359-ecf1c7b950b1')
    with pytest.raises(ForwardBudgetError):r.budget.pre_admit(p)
    assert r.rows==before

def test_changed_context_or_limits_do_not_create_new_parent_namespace(budget_rig):
    r=budget_rig;r.budget.prepare();before=copy.deepcopy(r.rows)
    for fresh in (r.new_budget(Cost(601,120000000,2048,2000000,2000000)),r.new_budget()):
        if fresh.limits==r.budget.limits:
            fresh.context=replace(r.context,max_records=32);fresh.binding['context_digest']=fresh.context.digest.hex()
        assert fresh.record_id==r.budget.record_id
        with pytest.raises(ForwardBudgetError):fresh.prepare()
        assert r.rows==before


@pytest.mark.parametrize('operation',['consume','check','check_pending'])
def test_stable_vault_epoch_replacement_cannot_reuse_live_allocation(budget_rig,operation):
    r=budget_rig;a=r.budget.pre_admit(plan(r))
    if operation=='check':r.budget.consume(a)
    before=copy.deepcopy(r.rows);r.instance='c'*64
    with pytest.raises(ForwardBudgetError):getattr(r.budget,operation)(a)
    assert r.rows==before and r.budget._uncertain


def test_pending_snapshot_reentry_cannot_consume_prepaid_factory_permit(budget_rig):
    r=budget_rig;a=r.budget.pre_admit(plan(r));before=copy.deepcopy(r.rows)
    original=r.budget._snapshot;observed=[]
    def snapshot():
        if not r.budget._busy and not observed:
            observed.append(True)
            r.budget.consume(a)
        return original()
    r.budget._snapshot=snapshot
    assert r.budget.check_pending(a) is True
    assert not observed and not a.bound and r.rows==before
