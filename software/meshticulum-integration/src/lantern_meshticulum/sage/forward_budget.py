# SPDX-License-Identifier: Apache-2.0
"""Protected conservative parent allocations, not native dispatch authority.

Prepay complete enforced child maxima before opening/arming a child. One-use
stage/direction slots and child digests prevent reset by fresh grants. No refund,
merge/retry or inference of worker retirement follows an uncertain result.
Native ownership, prior debt observations and grant/profile limits are external.
"""
import base64
from dataclasses import dataclass,asdict
import hashlib
import os
import threading
from nacl.signing import SigningKey
from meshticulum.privacy.forward_session import ForwardContext,H1,H2,CONFIRM_I,CONFIRM_R,DATA
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.mesh_binary_capture import _Lease,MAX_TIME_US
from lantern_meshticulum.sage.mesh_inbox import _canonical,_id,_uuid
from lantern_meshticulum.sage.workflow_store import WorkflowStore,WorkflowNotFound

SCHEMA='sage.lantern.forward-operation-budget.v1'
DOMAIN=b'sage.lantern.forward-operation-budget.v1/signature\0'
KINDS=(H1,H2,CONFIRM_I,CONFIRM_R,DATA)

class ForwardBudgetError(RuntimeError):
    def __init__(self):super().__init__('forward_operation_budget_unavailable')

def require(value):
    if not value:raise ForwardBudgetError()

def digest(value):
    require(type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value))
    return value

@dataclass(frozen=True)
class Cost:
    radio_attempts:int=0
    estimated_airtime_us:int=0
    ip_frames:int=0
    tx_stream_bytes:int=0
    rx_stream_bytes:int=0
    def __post_init__(self):
        require(all(type(v) is int and 0<=v<=MAX_TIME_US for v in asdict(self).values()))
    def plus(self,other):return Cost(**{k:asdict(self)[k]+asdict(other)[k] for k in asdict(self)})
    def within(self,limit):return all(asdict(self)[k]<=asdict(limit)[k] for k in asdict(self))

@dataclass(frozen=True)
class ChildPlan:
    slot:int
    kind:int
    sends:bool
    grant_digest:str
    packet_sha256:str
    packet_bytes:int
    carrier:str
    profile_digest:str
    host_boot_id:str
    application_expires_epoch_us:int
    application_deadline_monotonic_us:int
    cost:Cost
    cipher_frame_limit:int
    def __post_init__(self):
        require(type(self.slot) is int and 0<=self.slot<16 and type(self.kind) is int and self.kind in KINDS
                and type(self.sends) is bool and self.carrier in ('lora','tcp') and type(self.cost) is Cost
                and type(self.packet_bytes) is int and 1<=self.packet_bytes<=2144
                and type(self.cipher_frame_limit) is int and 1<=self.cipher_frame_limit<=1000000)
        for v in (self.grant_digest,self.packet_sha256,self.profile_digest):digest(v)
        _uuid(self.host_boot_id)
        require(all(type(v) is int and 0<v<=MAX_TIME_US for v in
                    (self.application_expires_epoch_us,self.application_deadline_monotonic_us)))
        c=self.cost
        if self.carrier=='lora':require(c.radio_attempts>0 and c.estimated_airtime_us>0
            and c.ip_frames==c.tx_stream_bytes==c.rx_stream_bytes==0)
        else:require(c.ip_frames>0 and c.tx_stream_bytes>=128 and c.rx_stream_bytes>=128
            and c.radio_attempts==c.estimated_airtime_us==0)
    def to_dict(self):return asdict(self)

class Admission:
    """Instance-owned handle; its JSON/fields alone cannot authorize dispatch."""
    def __init__(self,owner,plan,revision):self.owner,self.plan,self.revision=owner,plan,revision;self.bound=False
    def __repr__(self):return '<ForwardChildAdmission>'

class ForwardBudget:
    def __init__(self,store,*,context,intention,signing_key,boot_id,limits,prior_debt,
                 slots,owner_lease,access_check,storage_check,native_check,clock,workflow_access_check=None,
                 budget_action_check=None):
        require(type(store) is WorkflowStore and type(context) is ForwardContext
                and type(intention) is ReceiveIntention and isinstance(signing_key,SigningKey)
                and type(limits) is Cost and type(prior_debt) is Cost and prior_debt.within(limits)
                and type(slots) is tuple and 1<=len(slots)<=16
                and all(type(v) is tuple and len(v)==2 and type(v[0]) is int and v[0] in KINDS
                        and type(v[1]) is bool for v in slots)
                and all(callable(c) for c in (owner_lease,access_check,storage_check,native_check,clock))
                and (workflow_access_check is None or callable(workflow_access_check))
                and (budget_action_check is None or callable(budget_action_check)))
        _uuid(boot_id)
        require(store.storage.actor.agent_id==bytes(signing_key.verify_key).hex()
                and intention.host_boot_id==boot_id
                and intention.application_expires_epoch_us==context.expires_epoch_us
                and intention.authority_digest==context.binding_digest.hex())
        self.store,self.context,self.intention,self.boot_id=store,context,intention,boot_id
        self.key,self.limits,self.prior,self.slots=signing_key,limits,prior_debt,slots
        self._lease,self._access,self._storage,self._native,self._clock=owner_lease,access_check,storage_check,native_check,clock
        self._workflow_access=self._workflow_access_ref=workflow_access_check
        self._budget_action=self._budget_action_ref=budget_action_check
        self._guarding=False
        self._workflow_epoch=self._workflow_run=None
        self._bound_read=None
        actor=dict(chain_id=store.storage.actor.chain_id,agent_id=store.storage.actor.agent_id)
        # Never include mutable context, lifetime, boot or limits in identity.
        self.record_id=_id(SCHEMA,[actor,intention.operation_id])
        self.binding=dict(actor=actor,context_digest=context.digest.hex(),intention=intention.to_dict(),
            limits=asdict(limits),prior_debt=asdict(prior_debt),slots=[[k,s] for k,s in slots])
        self._action_refs=(store,context,intention,signing_key,limits,prior_debt,slots)
        self._action_value=(context.digest,intention.to_dict(),boot_id,asdict(limits),asdict(prior_debt),
                            _canonical(self.binding),self.record_id)
        self._owner=(os.getpid(),threading.get_ident())
        self._busy=self._closed=self._uncertain=False
        self._permits={};self._epoch=None;self._last_time=None;self._revision=0;self._body=None
    def __repr__(self):return '<ForwardBudget>'
    def _available(self):
        require(not self._closed and not self._uncertain and self._owner==(os.getpid(),threading.get_ident())
            and self._workflow_access is self._workflow_access_ref
            and self._budget_action is self._budget_action_ref)
    def _checked(self,check,*args):
        self._available();v=check(*args);self._available();require(v is True);return True
    def _guard(self,plan):
        if self._budget_action is not None:
            self._available()
            if self._guarding:self._uncertain=True;raise ForwardBudgetError()
            require(self._busy and (plan is None or type(plan) is ChildPlan))
            value=None if plan is None else _canonical(plan.to_dict())
            active=getattr(self,'_active_plan',None)
            def bound():
                self._available()
                require(all(a is b for a,b in zip(self._action_refs,
                    (self.store,self.context,self.intention,self.key,self.limits,self.prior,self.slots)))
                    and self._action_value==(self.context.digest,self.intention.to_dict(),self.boot_id,
                        asdict(self.limits),asdict(self.prior),_canonical(self.binding),self.record_id)
                    and (None if plan is None else _canonical(plan.to_dict()))==value
                    and getattr(self,'_active_plan',None) is active)
            bound();self._guarding=True
            try:
                self._checked(self._budget_action,self.context,self.intention,plan)
                bound();return
            finally:self._guarding=False
        self._checked(self._access);self._checked(self._storage)
        self._checked(self._native,self.context,self.intention,plan)
    def _time(self):
        self._available();v=self._clock();self._available()
        require(type(v) is tuple and len(v)==2 and all(type(x) is int and 0<=x<=MAX_TIME_US for x in v)
            and (self._last_time is None or all(a>=b for a,b in zip(v,self._last_time))))
        self._last_time=v;return v
    def _live(self):
        v=self._time();require(self.context.issued_epoch_us<=v[0]<self.intention.application_expires_epoch_us
            and v[1]<self.intention.application_deadline_monotonic_us);return v
    def _allowed(self):
        self._guard(self._active_plan);self._live();return True
    def _workflow_allowed(self,epoch,run):
        # Only an active action's epoch crosses this callback. Full action and
        # native spending guards remain outside each fresh HTTP epoch bracket.
        self._available();require(self._busy and type(epoch) is str and self._workflow_epoch==epoch
            and run is not None and self._workflow_run is run)
        if self._workflow_access is None:self._allowed()
        else:
            self._live();self._checked(self._workflow_access);self._live()
        require(self._workflow_epoch==epoch and self._workflow_run is run);return True
    def _snapshot(self):
        self._available();v=self.store.storage.snapshot().epoch;self._available();return v
    def _run(self,plan,action):
        entered=False
        try:
            self._available()
            if self._busy:self._uncertain=True;raise ForwardBudgetError()
            self._busy=entered=True;self._active_plan=plan
            self._guard(plan);self._live()
            lease=self._lease();self._available()
            with _Lease(lease):
                self._guard(plan);epoch=self._snapshot();self._live()
                require(self._epoch is None or self._epoch==epoch)
                require(self._workflow_epoch is None and self._workflow_run is None)
                self._workflow_epoch=epoch;self._workflow_run=run=object()
                result=action(epoch)
                require(self._workflow_epoch==epoch and self._workflow_run is run)
                require(self._snapshot()==epoch);self._live();self._guard(plan)
            require(self._snapshot()==epoch);self._live();require(self._snapshot()==epoch)
            self._guard(plan);require(self._snapshot()==epoch);self._available();return result
        except BaseException as e:
            if entered:self._uncertain=True
            if not isinstance(e,Exception):raise
            raise ForwardBudgetError() from None
        finally:
            if entered:self._workflow_epoch=self._workflow_run=None;self._busy=False
    def _validate(self,record):
        p=record['payload'];require(type(p) is dict and set(p)=={'schema','body','signature'} and p['schema']==SCHEMA)
        signature=base64.b64decode(p['signature'],validate=True);require(len(signature)==64
            and base64.b64encode(signature).decode()==p['signature'])
        b=p['body'];self.key.verify_key.verify(DOMAIN+_canonical(b),signature)
        require(type(b) is dict and set(b)=={'record_id','revision','binding','storage_epoch','last_epoch_us','last_monotonic_us','allocations'}
            and record['record_id']==b['record_id']==self.record_id and type(b['revision']) is int
            and b['revision']==record['revision'] and _canonical(b['binding'])==_canonical(self.binding)
            and type(b['allocations']) is list and len(b['allocations'])<=len(self.slots))
        require(type(b['storage_epoch']) is str and type(b['last_epoch_us']) is int and type(b['last_monotonic_us']) is int
            and self.context.issued_epoch_us<=b['last_epoch_us']<self.context.expires_epoch_us
            and 0<=b['last_monotonic_us']<self.intention.application_deadline_monotonic_us)
        spent=self.prior;seen=set()
        for index,row in enumerate(b['allocations']):
            require(type(row) is dict);data=dict(row);cost=data.pop('cost');require(type(cost) is dict)
            child=ChildPlan(**data,cost=Cost(**cost))
            require(child.slot==index and self.slots[index]==(child.kind,child.sends)
                and child.host_boot_id==self.boot_id and child.application_expires_epoch_us==self.context.expires_epoch_us
                and child.application_deadline_monotonic_us<=self.intention.application_deadline_monotonic_us
                and child.grant_digest not in seen)
            seen.add(child.grant_digest);spent=spent.plus(child.cost)
        require(spent.within(self.limits))
        encoded=_canonical(b);require(b['revision']>=self._revision
            and (b['revision']!=self._revision or encoded==self._body))
        self._revision,self._body=b['revision'],encoded
        return b
    def _read(self,epoch):
        run=self._workflow_run
        try:r=self.store.get(self.record_id,kind='mesh_outbound',
            access_check=lambda:self._workflow_allowed(epoch,run),expected_epoch=epoch)
        except WorkflowNotFound:
            now=self._live()
            b=dict(record_id=self.record_id,revision=0,binding=self.binding,storage_epoch=epoch,
                last_epoch_us=now[0],last_monotonic_us=now[1],allocations=[])
            return self._write(b,epoch)
        b=self._validate(r);require(b['storage_epoch']==epoch)
        self._epoch=epoch
        return b
    def _write(self,b,epoch):
        self._guard(self._active_plan);now=self._live()
        require(b['storage_epoch']==epoch==self._snapshot()
            and now[0]>=b['last_epoch_us'] and now[1]>=b['last_monotonic_us'])
        b={**b,'revision':b['revision']+1,'last_epoch_us':now[0],'last_monotonic_us':now[1]}
        signature=base64.b64encode(self.key.sign(DOMAIN+_canonical(b)).signature).decode()
        run=self._workflow_run
        r=self.store.put(self.record_id,'mesh_outbound',b['revision']-1,
            dict(schema=SCHEMA,body=b,signature=signature),
            access_check=lambda:self._workflow_allowed(epoch,run),expected_epoch=epoch)
        checked=self._validate(r);require(_canonical(checked)==_canonical(b));self._epoch=epoch;return checked
    def prepare(self):return self._run(None,lambda epoch:self._read(epoch)['revision'])
    def pre_admit(self,plan):
        """Call BEFORE opening/arming child; full maxima remain spent forever."""
        require(type(plan) is ChildPlan)
        def reserve(epoch):
            b=self._read(epoch)
            require(plan.slot==len(b['allocations']) and plan.slot<len(self.slots)
                and self.slots[plan.slot]==(plan.kind,plan.sends)
                and plan.host_boot_id==self.boot_id and plan.application_expires_epoch_us==self.context.expires_epoch_us
                and plan.application_deadline_monotonic_us<=self.intention.application_deadline_monotonic_us
                and not any(r['grant_digest']==plan.grant_digest for r in b['allocations']))
            spent=self.prior
            for row in b['allocations']:spent=spent.plus(Cost(**row['cost']))
            require(spent.plus(plan.cost).within(self.limits))
            written=self._write({**b,'allocations':[*b['allocations'],plan.to_dict()]},epoch)
            return Admission(self,plan,written['revision'])
        admission=self._run(plan,reserve)
        self._permits[id(admission)]=admission
        return admission
    def check_pending(self,admission):
        """Revalidate prepaid debt immediately before the native child factory."""
        require(type(admission) is Admission and admission.owner is self
            and self._permits.get(id(admission)) is admission and not admission.bound)
        def pending(epoch):
            b=self._read(epoch);p=admission.plan
            require(p.slot<len(b['allocations']) and _canonical(b['allocations'][p.slot])==_canonical(p.to_dict()))
            return True
        result=self._run(admission.plan,pending)
        require(self._permits.get(id(admission)) is admission and not admission.bound)
        return result
    def consume(self,admission):
        """One owner binds the handle; a recovered allocation is only debt."""
        require(type(admission) is Admission and admission.owner is self
            and self._permits.get(id(admission)) is admission and not admission.bound)
        def consume(epoch):
            b=self._read(epoch);p=admission.plan
            require(p.slot<len(b['allocations']) and _canonical(b['allocations'][p.slot])==_canonical(p.to_dict()))
            return True
        self._run(admission.plan,consume);admission.bound=True;return admission.plan
    def check(self,admission):
        require(type(admission) is Admission and admission.owner is self
                and self._permits.get(id(admission)) is admission and admission.bound)
        entered=False
        try:
            self._available()
            if self._busy:self._uncertain=True;raise ForwardBudgetError()
            self._busy=entered=True
            self._guard(admission.plan);require(self._epoch is not None and self._snapshot()==self._epoch)
            self._live();require(self._snapshot()==self._epoch);self._guard(admission.plan)
            return True
        except BaseException:
            if entered:self._uncertain=True
            raise
        finally:
            if entered:self._busy=False
    def check_bound_read(self,admission,token,clock):
        """Exact committed read only; no signed-row mutation or spending."""
        from .forward_operation import _StageRead
        require(type(token) is _StageRead and token.budget is self
            and token.runner.admission is admission and type(admission) is Admission
            and admission.owner is self and self._permits.get(id(admission)) is admission
            and admission.bound is True)
        entered=False;binding=token._bindings
        try:
            self._available()
            if self._busy or self._bound_read is not None:
                self._uncertain=True;raise ForwardBudgetError()
            self._busy=entered=True;self._bound_read=token
            token.bound();token._clock_valid(clock);token.operation.check_bound_read(token,clock)
            token.fence();require(self._epoch is not None and self._snapshot()==self._epoch)
            require(token._bindings is binding);token.bound();clock('budget',self._live);token.bound()
            require(self._snapshot()==self._epoch and token._bindings is binding);token.bound();token.fence()
            return True
        except BaseException:
            if entered:self._uncertain=True
            raise
        finally:
            if entered and self._bound_read is token:self._bound_read=None;self._busy=False
    def close(self):self._closed=True;return not self._busy and not self._uncertain
