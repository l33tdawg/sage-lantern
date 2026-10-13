# SPDX-License-Identifier: Apache-2.0
"""Protected per-unit public-pilot claims; no enrollment from a package.

Explicit enrollment requires independent native facts, approval and prior-debt
checks. Adapters open that retained enrollment and its revocable protected
approval flag; neither creates policy or erases debt. Complete child maxima are
charged before provision, and a separate append-only start tombstone precedes
runtime construction. Receipts are inspection, never bearer grants. No socket,
radio, process, controller or production service is opened by this module.
"""
from dataclasses import dataclass, asdict, fields
from functools import lru_cache
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from types import FunctionType
from .phase_timing import phase,call as timed_call
from .management_input import RAW_MAX

from meshticulum.contracts.identity import PeerID
from .pilot_parent_caps import (BUNDLE_KINDS, PUBLIC32_KINDS, Cost, ParentPolicy, UnitCaps, child_caps, canonical,
                                private, digest, identifier, boot, MAX, DOMAIN as POLICY_DOMAIN)

DOMAIN = b'meshticulum.native-parent-caps.v1\0'


class NativeCapsError(RuntimeError):
    def __init__(self): super().__init__('public_native_caps_unavailable')


def require(value):
    if not value: raise NativeCapsError()


def hashed(value): return hashlib.sha256(DOMAIN + canonical(value)).hexdigest()


def _claim_representation(claim_id,raw,run,unit,host_boot_id,policy_digest):
    """Validate immutable row bytes; never retain permission or a debt total."""
    claim=json.loads(raw);require(canonical(claim)==raw
        and hashlib.sha256(DOMAIN+raw).hexdigest()==claim_id)
    meta=claim['child']['metadata'];cost=Cost(**claim['child']['cost'])
    require(meta['unit']==unit and meta['host_boot_id']==host_boot_id
            and meta['policy_digest']==policy_digest and meta['run_id']==run)
    return (claim['resource_id'],canonical(claim['resource']),
            (cost.radio_attempts,cost.estimated_airtime_us,cost.ip_frames,
             cost.tx_stream_bytes,cost.rx_stream_bytes))


def _state(value):
    """Capture immutable typed values for fresh live comparisons."""
    kind = type(value)
    if kind in (str, int, bool, type(None)):
        return kind, value
    if kind in (ParentPolicy, UnitCaps, Cost, PeerID):
        return kind, tuple((field.name, _state(getattr(value, field.name))) for field in fields(kind))
    if kind in (tuple, list):
        return kind, tuple(_state(item) for item in value)
    require(kind is dict and all(type(key) is str for key in value))
    return kind, tuple((key, _state(value[key])) for key in sorted(value))


def _unchanged(value, state):
    kind, expected = state
    if type(value) is not kind:return False
    if kind in (str, int, bool, type(None)):return value == expected
    if kind in (ParentPolicy, UnitCaps, Cost, PeerID):
        return all(_unchanged(getattr(value, name), item) for name, item in expected)
    if kind in (tuple, list):
        return len(value)==len(expected) and all(_unchanged(item, old) for item, old in zip(value, expected))
    return (len(value)==len(expected) and all(type(key) is str for key in value)
            and all(key in value and _unchanged(value[key], item) for key, item in expected))


def _state_comparator(state):
    """Compile immutable comparison structure, never an authority result.

    The AST only reads/checks actual fields on each invocation. Descriptor
    values are bound as immutable defaults, never inserted as source text.
    All types, keys, lengths and leaves checked by _unchanged remain checked.
    """
    constants=[];body=[];counter=0
    def name(value):return ast.Name(id=value,ctx=ast.Load())
    def constant(value):
        index=len(constants);constants.append(value)
        return ast.Subscript(value=name('_constants'),slice=ast.Constant(index),ctx=ast.Load())
    def refuse(condition):body.append(ast.If(test=condition,body=[ast.Return(ast.Constant(False))],orelse=[]))
    def compare(left,operator,right):return ast.Compare(left=left,ops=[operator],comparators=[right])
    def call(function,value):return ast.Call(func=name(function),args=[value],keywords=[])
    def visit(variable,descriptor):
        nonlocal counter
        kind,expected=descriptor;value=name(variable)
        refuse(compare(call('_type',value),ast.IsNot(),constant(kind)))
        if kind in (str,int,bool,type(None)):
            refuse(compare(value,ast.NotEq(),constant(expected)));return
        if kind in (tuple,list,dict):
            refuse(compare(call('_len',value),ast.NotEq(),ast.Constant(len(expected))))
        if kind is dict:
            keys=ast.GeneratorExp(elt=compare(call('_type',name('_key')),ast.Is(),name('_str')),
                generators=[ast.comprehension(target=ast.Name(id='_key',ctx=ast.Store()),iter=value,ifs=[],is_async=0)])
            refuse(ast.UnaryOp(op=ast.Not(),operand=call('_all',keys)))
        for key,item in (enumerate(expected) if kind in (tuple,list) else expected):
            counter+=1;child='field_'+str(counter)
            if kind in (ParentPolicy,UnitCaps,Cost,PeerID):
                expression=ast.Attribute(value=value,attr=key,ctx=ast.Load())
            else:
                if kind is dict:refuse(compare(constant(key),ast.NotIn(),value))
                expression=ast.Subscript(value=value,slice=constant(key),ctx=ast.Load())
            body.append(ast.Assign(targets=[ast.Name(id=child,ctx=ast.Store())],value=expression))
            visit(child,item)
    visit('value',state);body.append(ast.Return(ast.Constant(True)))
    arguments=ast.arguments(posonlyargs=[],args=[ast.arg(arg=v) for v in
        ('value','_constants','_type','_len','_all','_str')],vararg=None,kwonlyargs=[],kw_defaults=[],kwarg=None,defaults=[])
    tree=ast.Module(body=[ast.FunctionDef(name='compare',args=arguments,body=body,decorator_list=[])],type_ignores=[])
    namespace={'__builtins__':{}}
    exec(compile(ast.fix_missing_locations(tree),'<native-typed-state-comparison>','exec'),namespace)
    comparator=namespace['compare'];comparator.__defaults__=(tuple(constants),type,len,all,str)
    return comparator,comparator.__code__,comparator.__defaults__


def _compare_state(value,comparator,pinned):
    require(type(comparator) is FunctionType and type(pinned) is tuple and len(pinned)==3
            and comparator is pinned[0] and comparator.__code__ is pinned[1]
            and comparator.__defaults__ is pinned[2] and comparator.__closure__ is None)
    return comparator(value)


@dataclass(frozen=True, slots=True)
class NativeFacts:
    unit: str
    host_boot_id: str
    peer: PeerID
    def __post_init__(self):
        require(self.unit in ('unit-a','unit-b','unit-c') and type(self.peer) is PeerID)
        boot(self.host_boot_id)


class Preparation:
    """Exact instance-owned claim; construction or a copied receipt is no grant."""
    def __init__(self, owner, claim_id, raw):
        self._owner, self._claim_id, self._raw = owner, claim_id, raw
    @property
    def run_id(self): return json.loads(self._raw)['child']['metadata']['run_id']
    @property
    def application_expires_epoch_us(self): return json.loads(self._raw)['resource']['application_expires_epoch_us']
    @property
    def application_deadline_monotonic_us(self): return json.loads(self._raw)['resource']['application_deadline_monotonic_us']
    def __repr__(self): return '<NativePublicPreparation>'


class _Public32Approval:
    """Held original owner/source records, never an owner supplied in a package."""
    def __init__(self,directory,policy,unit,facts,clock):
        from .pilot_resume_rpc import Reader,Runtime,parse,sha,stable,endpoint_budget
        from .profiles import profile_for,profile_record,GFSK_BUNDLE_KIND,GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND,GFSK250_FAST_BUNDLE_KIND,GFSK_NAME,profile_name_for_kind
        self.reader=self.runtime=None;self._closed=False
        self.policy,self.unit,self.facts,self.clock=policy,unit,facts,clock
        self._pid,self._thread=os.getpid(),threading.get_ident()
        self._owner_ref=(self._pid,self._thread);self._reader_ref=self._runtime_ref=None
        self._captured_refs=None
        self._owned_close=lambda:None
        try:
            self.reader=self._reader_ref=Reader()
            reader,own=self.reader,self._owner_ref
            self._owned_close=lambda:reader.close() if own==(os.getpid(),threading.get_ident()) else None
            raw=self.reader.read(directory/'owner.json',200000);owner=parse(raw)
            require(owner['schema']=='meshticulum.finite-public-resume-owner.v1'
                    and owner['qualification_kind']==policy.qualification_kind and policy.qualification_kind in PUBLIC32_KINDS and owner['policy_digest']==policy.digest)
            image_helper=sys.modules.get('_lantern_execution_runtime')
            runtime_root=(image_helper.module_root(__name__) if image_helper is not None
                          else Path(__file__).absolute().parents[2])
            self.runtime=self._runtime_ref=Runtime(runtime_root,owner['manifest_sha256'])
            runtime=self.runtime
            def close_owned():
                if own==(os.getpid(),threading.get_ident()):
                    try:runtime.close()
                    finally:reader.close()
            self._owned_close=close_owned
            require(self.runtime.root.name=='runtime-'+owner['runtime_sha256'][:16]);digest(owner['runtime_sha256'])
            prior_raw=self.reader.read(directory/'prior.json',200000);prior=parse(prior_raw)
            require(self.reader.read(directory/'policy.json',20000)==canonical(policy.to_dict()))
            captured_raw=self.reader.read(directory/'capture.json',RAW_MAX);captured=parse(captured_raw)
            binding_raw=self.reader.read(directory/'history-binding.json',16384);binding=parse(binding_raw)
            require(sha(self.reader.read(directory/'history-identity-seal.json',2000000))==binding['strict_sha256']
                    and sha(self.reader.read(directory/'legacy-grant-seal.json',2000000))==binding['legacy_sha256'])
            from .pilot_bundle import validate_capture_commitment
            validate_capture_commitment(owner,captured,policy)
            local=policy.unit(unit);history_pin=sha(binding_raw)
            require(sha(captured_raw)==owner['capture_record_sha256'] and captured['unit']==unit
                    and captured['parent_id']==policy.parent_id and captured['actual_history_verified'] is True
                    and captured['history_binding_sha256']==owner['history_binding_sha256']==prior['history_binding_sha256']==history_pin
                    and binding['schema']=='meshticulum.private-resume-history-binding.v2'
                    and binding['parent_id']==policy.parent_id and binding['unit']==unit
                    and binding['parent_identity']==self.reader.directories[directory]
                    and captured['review_sha256']==owner['review_sha256']
                    and captured['facts']==owner['facts'][unit] and captured['native_phase']==owner['native_phase'][unit]
                    and captured['phase_start_epoch_us']==owner['phase_start_epoch_us']
                    and captured['phase_end_epoch_us']==owner['phase_end_epoch_us'])
            require(prior['full_caps']==captured['history']['previous'][-1]['paid_caps']==asdict(local.prior_debt)
                    and prior['legacy_inventory']==captured['history']['legacy_inventory']
                    and prior['complete_global_debt'] is False and prior['actual_cost'] is None and prior['refunds']==0
                    and sha(prior_raw)==local.prior_snapshot_sha256
                    and owner['prior_registry'][unit]['observation_sha256']==sha(canonical(prior))
                    and sha(canonical(owner['prior_registry']))==policy.prior_snapshot_sha256)
            auth=owner['authorization'];gfsk=policy.qualification_kind in (GFSK_BUNDLE_KIND,GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND,GFSK250_FAST_BUNDLE_KIND)
            rp=profile_for(profile_name_for_kind(policy.qualification_kind) if gfsk else 'sf7-500');rf=owner['rf_record']
            if gfsk:
                if policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND:
                    from .fast_bundle import require_owner
                elif policy.qualification_kind==GFSK250_MB1_BUNDLE_KIND:
                    from .gfsk250_mb1_bundle import require_owner
                elif policy.qualification_kind==GFSK_MB1_BUNDLE_KIND:
                    from .gfsk_mb1_bundle import require_owner
                else:from .gfsk_bundle import require_owner
                require_owner(owner,policy)
            require(auth['human_statement']=='run it bro' and type(auth['count_authorized']) is int and auth['count_authorized']==1
                    and auth['public_only'] is True and auth['private_production'] is False
                    and owner['rf_record_sha256']=='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9'
                    and rf['country']=='MY' and rf['C_transmission_allowed'] is True and rf['A_B_transmission_allowed'] is True
                    and all(rf['units'][u]['antenna_connected'] is True for u in ('unit-a','unit-b','unit-c'))
                    and owner['rf_profile']==(profile_record(rp) if gfsk else asdict(rp))
                    and local.limits==local.prior_debt.plus(endpoint_budget(unit,policy.qualification_kind,Cost(256,60000000),Cost()))
                    and policy.issued_epoch_us==owner['phase_start_epoch_us']
                    and policy.expires_epoch_us==owner['phase_end_epoch_us']-5000000
                    and stable(facts)==owner['facts'][unit] and facts['host_boot_id']==local.host_boot_id)
            self.bundle = None
            if policy.qualification_kind in BUNDLE_KINDS:
                from .pilot_bundle import BundleContext
                self.bundle = BundleContext(owner['bundle_context'],policy)
            self._bundle_ref=self.bundle
            if policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND:
                for index in range(5):
                    stage_path=directory/('fast-stage-'+str(index)+'.json')
                    if os.path.lexists(stage_path):self.reader.read(stage_path,8192)
            self._binding=history_pin;self._phase=canonical(captured['native_phase'])
            self._facts_bytes=canonical(owner['facts'][unit])
            self._start,self._end=owner['phase_start_epoch_us'],owner['phase_end_epoch_us']
            self._reader_ref,self._runtime_ref=self.reader,self.runtime
            self._policy_state=_state(policy)
            self._facts_state=_state({k:facts[k] for k in ('unit','host_boot_id','services','service_lifetimes')})
            self._policy_comparison=_state_comparator(self._policy_state)
            self._facts_comparison=_state_comparator(self._facts_state)
            self._phase_bounds=(captured['native_phase']['epoch_us'],captured['native_phase']['monotonic_us'],
                                captured['native_phase']['deadline_monotonic_us'])
            self._bounds_ref=self._phase_bounds
            refs=(self.policy,self.unit,self.facts,self.clock,self.reader,self.runtime,
                  self._binding,self._phase,self._facts_bytes,self._start,self._end,self._pid,self._thread,
                  self._owner_ref,self._policy_state,self._facts_state,self._phase_bounds,self.bundle,
                  self._policy_comparison,self._facts_comparison)
            self._state_refs=self._state_refs_ref=refs
            self._captured_refs=lambda:refs
            self._last=None;self.check()
        except BaseException:self.close();raise
    @phase('approval_bound')
    def _bound(self,last):
        refs=self._captured_refs()
        current=(self.policy,self.unit,self.facts,self.clock,self.reader,self.runtime,
                 self._binding,self._phase,self._facts_bytes,self._start,self._end,self._pid,self._thread,
                 self._owner_ref,self._policy_state,self._facts_state,self._phase_bounds,self.bundle,
                 self._policy_comparison,self._facts_comparison)
        require(not self._closed and type(self._state_refs) is tuple and len(self._state_refs)==20
                and self._state_refs is refs and self._state_refs_ref is refs
                and len(current)==len(refs) and all(a is b for a,b in zip(current,refs))
                and self._owner_ref==(os.getpid(),threading.get_ident())
                and self.reader is self._reader_ref is refs[4] and self.runtime is self._runtime_ref is refs[5]
                and self._phase_bounds is self._bounds_ref is refs[16] and self._last is last
                and _compare_state(self.policy,self._policy_comparison[0],refs[18]) and type(self.facts) is dict
                and _compare_state({k:self.facts[k] for k in ('unit','host_boot_id','services','service_lifetimes')},
                    self._facts_comparison[0],refs[19])
                and self.bundle is self._bundle_ref is refs[17]
                and (self.policy.qualification_kind=='c-bulk32-v1') is (refs[17] is None))
    def _time(self,last,previous):
        self._bound(last);now=self.clock();self._bound(last)
        if self.bundle is not None:self.bundle.current(now)
        self._bound(last)
        epoch,mono,end=self._phase_bounds
        require(type(now) is tuple and len(now)==2 and all(type(v) is int for v in now)
                and self._start<=now[0]<self._end and epoch<=now[0] and mono<=now[1]<end
                and (previous is None or all(a>=b for a,b in zip(now,previous))))
        return now
    def _verified(self,last,now):
        # No callback result is cached. Every callback re-reads every held
        # owner/source byte and checks full stamps, names and ancestry, after
        # both external clocks and binding parsing have finished.
        self._bound(last);timed_call(getattr(self,'_phase_timing',None),'owner_reader',self.reader.check);self._bound(last)
        timed_call(getattr(self,'_phase_timing',None),'runtime_reader',self.runtime.check);self._bound(last)
        self._last=now;return True
    def check(self):
        last=self._last
        try:
            first=self._time(last,last);second=self._time(last,first)
            return self._verified(last,second)
        except BaseException:self.close();raise
    def __call__(self,raw):
        from .pilot_resume_rpc import parse
        last=self._last
        try:
            first=self._time(last,last);value=parse(raw);self._bound(last)
            e=value['enrollment'];local=self.policy.unit(self.unit)
            require(value['phase'] in ('enroll','current','prepare','finalize','start') and e['schema']=='meshticulum.native-parent-enrollment.v2'
                    and canonical(e['policy'])==canonical(self.policy.to_dict()) and e['history_binding_sha256']==self._binding
                    and e['unit']==self.unit and e['host_boot_id']==local.host_boot_id
                    and self._start<=e['original_epoch_us']<self.policy.expires_epoch_us
                    and e['deadline_monotonic_us']==e['original_monotonic_us']+self.policy.expires_epoch_us-e['original_epoch_us']
                    and e['deadline_monotonic_us']<self._phase_bounds[2])
            self._bound(last);second=self._time(last,first)
            return self._verified(last,second)
        except BaseException:self.close();raise
    def close(self):
        # The closure retains the constructor's original owners independently
        # of mutable reader/runtime fields or comparison metadata.
        self._closed=True;self._owned_close()


from .profiles import GFSK250_FAST_BUNDLE_KIND


class NativeCaps:
    @classmethod
    def enroll(cls,path,policy,*,unit,clock,facts,approve,prior_check,history_binding_sha256=None):
        """Optional guarded history is hashed into enrollment and approval.

        None retains the original v1 body. A digest explicitly opts into v2;
        opening a retained enrollment never supplies or changes that digest.
        """
        return cls(path,policy,unit=unit,clock=clock,facts=facts,approve=approve,prior_check=prior_check,
                   enroll=True,history_binding_sha256=history_binding_sha256)
    @classmethod
    def open(cls,path,policy,*,unit,clock,facts,approve):
        return cls(path,policy,unit=unit,clock=clock,facts=facts,approve=approve,prior_check=None,enroll=False)
    def __init__(self,path,policy,*,unit,clock,facts,approve,prior_check,enroll,history_binding_sha256=None):
        require(type(policy) is ParentPolicy and callable(clock) and callable(facts) and callable(approve)
                and (not enroll or callable(prior_check)))
        if history_binding_sha256 is not None:
            require(enroll);digest(history_binding_sha256)
        self.path=Path(path).absolute();self.policy=policy;self.unit=policy.unit(unit)
        # Local cap enforcement implies an aggregate bound only with this
        # conservative partition. Controller receipts are not global authority.
        total=Cost()
        for candidate in policy.units:total=total.plus(candidate.limits)
        require(total.within(policy.aggregate_limits))
        self._clock,self._facts,self._approve,self._prior=clock,facts,approve,prior_check
        self._pid,self._thread=os.getpid(),threading.get_ident()
        self._closed=self._uncertain=False;self._busy=True;self._preparations={};self._active=self._active_ref=None
        self.pending=Path(str(self.path)+'.pending');self._fds={};self._fd_refs={};self._ids={}
        self.db=self._db_ref=None;self._enrollment=None;self._approval_bytes=None;self._last=None
        self._policy_bytes=canonical(policy.to_dict());self._policy_state=_state(policy)
        self._policy_comparison=_state_comparator(self._policy_state)
        self._policy_comparator=self._policy_comparison[0]
        self._policy_digest=hashlib.sha256(POLICY_DOMAIN+self._policy_bytes).hexdigest()
        self._pending_written=False
        try:
            private(self.path.parent.lstat(),True);require(self.path.parent.resolve()==self.path.parent)
            for name,target in self._names().items():
                flags=os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC
                if enroll:flags|=os.O_CREAT|os.O_EXCL
                fd=os.open(target,flags,0o600);self._fds[name]=self._fd_refs[name]=fd
                info=os.fstat(fd);private(info);self._ids[name]=(info.st_dev,info.st_ino)
                if name=='owner':fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.db=self._db_ref=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,isolation_level=None,timeout=.25)
            require(self.db.execute('PRAGMA journal_mode=WAL').fetchone()==('wal',))
            self.db.execute('PRAGMA synchronous=FULL')
            require(self.db.execute('PRAGMA synchronous').fetchone()==(2,))
            self._refs=(self.path,self.pending,policy,self.unit,clock,facts,approve,prior_check,self._fds,self._ids,
                        tuple(self._fds.items()),tuple(self._ids.items()),self.db,
                        self._policy_bytes,self._policy_state,self._policy_digest,self._policy_comparison)
            if enroll:
                now=self._sample()
                require(policy.issued_epoch_us<=now[0]<policy.expires_epoch_us)
                deadline=now[1]+policy.expires_epoch_us-now[0];require(deadline<=MAX)
                self._enrollment=dict(schema='meshticulum.native-parent-enrollment.v1',policy=policy.to_dict(),
                    unit=unit,host_boot_id=self.unit.host_boot_id,original_epoch_us=now[0],
                    original_monotonic_us=now[1],deadline_monotonic_us=deadline)
                if history_binding_sha256 is not None:
                    self._enrollment.update(schema='meshticulum.native-parent-enrollment.v2',
                                            history_binding_sha256=history_binding_sha256)
                prior=canonical(dict(parent_id=policy.parent_id,policy_digest=policy.digest,unit=asdict(self.unit),
                                     prior_snapshot_sha256=policy.prior_snapshot_sha256))
                require(self._prior(prior) is True);self._available()
                self._observe('enroll')
                self._marker();self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('CREATE TABLE enrollment(id INTEGER PRIMARY KEY CHECK(id=1),body BLOB NOT NULL)')
                self.db.execute('INSERT INTO enrollment VALUES(1,?)',(canonical(self._enrollment),))
                self.db.execute('CREATE TABLE clock(id INTEGER PRIMARY KEY CHECK(id=1),epoch INTEGER,mono INTEGER)')
                self.db.execute('INSERT INTO clock VALUES(1,?,?)',now)
                for table in ('resources','claims','prepared','starts'):
                    extra=',run_id TEXT UNIQUE' if table=='claims' else ''
                    self.db.execute('CREATE TABLE '+table+'(id TEXT PRIMARY KEY,body BLOB NOT NULL'+extra+')')
                    for action in ('UPDATE','DELETE'):
                        self.db.execute('CREATE TRIGGER immutable_'+table+'_'+action+' BEFORE '+action+' ON '+table+
                                        " BEGIN SELECT RAISE(ABORT,'append_only'); END")
                self._commit()
                self._approval_bytes=(hashed(self._enrollment)+'\n').encode()
                os.write(self._fds['approval'],self._approval_bytes);os.fsync(self._fds['approval']);self._sync()
                final=self._observe('enroll');self._save_clock(final);self._available();self._clear_marker()
            else:
                row=self.db.execute('SELECT body FROM enrollment WHERE id=1').fetchone();require(row is not None)
                self._enrollment=json.loads(row[0]);require(canonical(self._enrollment)==row[0]
                    and canonical(self._enrollment['policy'])==self._policy_bytes
                    and self._enrollment['unit']==unit and self._enrollment['host_boot_id']==self.unit.host_boot_id)
                self._history_binding()
                self._approval_bytes=(hashed(self._enrollment)+'\n').encode()
            self._enrollment_bytes=canonical(self._enrollment)
            self._enrollment_state=_state(self._enrollment)
            self._enrollment_comparison=_state_comparator(self._enrollment_state)
            self._enrollment_comparator=self._enrollment_comparison[0]
            self._refs+=(self._enrollment_bytes,self._enrollment_state,self._enrollment_comparison)
            self._claim_rows=lru_cache(maxsize=4096,typed=True)(_claim_representation)
            self._claim_rows_binding=(type(self._claim_rows),_claim_representation,
                                      _claim_representation.__code__,_claim_representation.__defaults__)
            self._refs+=(self._claim_rows,self._claim_rows_binding)
            self._validate();self._uncertain=self.pending.exists() or self.pending.is_symlink()
        except BaseException as error:
            if self.db is self._db_ref and self.db is not None and self.db.in_transaction:self.db.execute('ROLLBACK')
            self._busy=False;self.close()
            if not isinstance(error,Exception):raise
            raise NativeCapsError() from None
        finally:self._busy=False
    def __repr__(self):return '<NativePublicCaps>'
    def _history_binding(self):
        fields={'schema','policy','unit','host_boot_id','original_epoch_us','original_monotonic_us','deadline_monotonic_us'}
        value=self._enrollment
        require(type(value) is dict)
        if value.get('schema')=='meshticulum.native-parent-enrollment.v1':
            require(set(value)==fields);return None
        require(value.get('schema')=='meshticulum.native-parent-enrollment.v2'
                and set(value)==fields|{'history_binding_sha256'})
        digest(value['history_binding_sha256']);return value['history_binding_sha256']
    @property
    def history_binding_sha256(self):
        """Classification comes from the protected, approval-bound enrollment."""
        self._validate();return self._history_binding()
    def _names(self):return {'owner':Path(str(self.path)+'.owner'),'db':self.path,'approval':Path(str(self.path)+'.approval')}
    @phase('native_available')
    def _available(self):
        require(not self._closed and self._pid==os.getpid() and self._thread==threading.get_ident())
        r=self._refs
        require(all(a is b for a,b in zip((self.path,self.pending,self.policy,self.unit,self._clock,self._facts,
                    self._approve,self._prior,self._fds,self._ids),r[:10]))
                and tuple(self._fds.items())==r[10] and tuple(self._ids.items())==r[11]
                and self._fds==self._fd_refs and self.db is self._db_ref is r[12]
                and self._policy_bytes is r[13] and self._policy_state is r[14] and self._policy_digest is r[15]
                and self._policy_comparison is r[16]
                and _compare_state(self.policy,self._policy_comparator,r[16]) and self._active is self._active_ref)
        private(self.path.parent.lstat(),True);require(self.path.parent.resolve()==self.path.parent)
        for name,target in self._names().items():
            held,named=os.fstat(self._fds[name]),target.lstat();private(held);private(named)
            require((held.st_dev,held.st_ino)==(named.st_dev,named.st_ino)==self._ids[name])
        if self._approval_bytes is not None:
            require(os.pread(self._fds['approval'],129,0)==self._approval_bytes)
        if hasattr(self,'_enrollment_bytes'):
            require(self._enrollment_bytes is r[17] and self._enrollment_state is r[18]
                    and self._enrollment_comparison is r[19]
                    and _compare_state(self._enrollment,self._enrollment_comparator,r[19]))
    def _sample(self):
        self._available();facts=self._facts();self._available()
        require(type(facts) is NativeFacts and facts==NativeFacts(self.unit.unit,self.unit.host_boot_id,self.unit.peer))
        now=self._clock();self._available()
        require(type(now) is tuple and len(now)==2 and all(type(v) is int and 0<v<=MAX for v in now))
        return now
    def _live(self,now,limits=None):
        e=self._enrollment
        require(not self._uncertain and self.policy.issued_epoch_us<=now[0]<self.policy.expires_epoch_us
                and e['original_epoch_us']<=now[0] and e['original_monotonic_us']<=now[1]<e['deadline_monotonic_us'])
        if self._last is not None:require(all(a>=b for a,b in zip(now,self._last)))
        if limits is not None:require(now[0]<limits[0] and now[1]<limits[1])
    def _observe(self,phase,limits=None):
        first=self._sample();self._live(first,limits)
        # _sample freshly checks the full typed enrollment against its retained
        # canonical bytes. Reuse only that encoding, never an approval result.
        if hasattr(self,'_enrollment_bytes'):
            binding=b'{"enrollment":'+self._enrollment_bytes+b',"phase":'+canonical(phase)+b'}'
        else:binding=canonical(dict(enrollment=self._enrollment,phase=phase))
        require(self._approve(binding) is True);self._available();require(not self._uncertain)
        now=self._sample();self._live(now,limits);require(all(a>=b for a,b in zip(now,first)))
        self._last=now;return now
    def _sync(self):
        fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
    def _marker(self):
        if self._pending_written:return
        fd=os.open(self.pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:os.write(fd,self.policy.digest.encode());os.fsync(fd)
        finally:os.close(fd)
        self._pending_written=True;self._sync()
    def _clear_marker(self):
        os.unlink(self.pending);self._sync();self._pending_written=False
    def _commit(self):self.db.execute('COMMIT')
    def _save_clock(self,now):
        self.db.execute('BEGIN IMMEDIATE');self.db.execute('UPDATE clock SET epoch=?,mono=? WHERE id=1',now);self._commit()
    @phase('native_validate')
    def _validate(self):
        self._available()
        binding=self._refs[21];validator=binding[1]
        require(self._claim_rows is self._refs[20] and self._claim_rows_binding is binding
                and type(self._claim_rows) is binding[0] and self._claim_rows.__wrapped__ is validator
                and type(validator) is FunctionType and validator.__code__ is binding[2]
                and validator.__defaults__ is binding[3] and validator.__closure__ is None)
        profile=getattr(self,'_phase_timing',None)
        row=timed_call(profile,'sql_enrollment',lambda:self.db.execute('SELECT body FROM enrollment WHERE id=1').fetchone())
        require(row==(self._enrollment_bytes,))
        used=self.unit.prior_debt;rows=timed_call(profile,'sql_claims',lambda:self.db.execute('SELECT id,body,run_id FROM claims').fetchall());require(len(rows)<=4096)
        for claim_id,raw,run in rows:
            # Every call selects complete current rows. Only pure interpretation
            # of an exact immutable row/context key is reused; changed/new rows
            # undergo full validation. Resource bytes and all costs remain live.
            resource_id,resource_bytes,cost=self._claim_rows(claim_id,raw,run,
                self.unit.unit,self.unit.host_boot_id,self._policy_digest)
            resource=timed_call(profile,'sql_resource',lambda:self.db.execute('SELECT body FROM resources WHERE id=?',(resource_id,)).fetchone())
            require(resource==(resource_bytes,))
            if self.policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND:
                value=json.loads(resource_bytes)
                declared=self._approve.bundle.value['stages']
                matches=[stage['index'] for stage in declared if stage['campaign_id']==value['campaign_id']]
                require(len(matches)==1)
                raw_stage=self._approve.reader.read(self.path.parent/('fast-stage-'+str(matches[0])+'.json'),8192)
                require(hashlib.sha256(raw_stage).hexdigest()==value['fast_stage_sha256'])
            used=used.plus(Cost(*cost))
        require(used.within(self.unit.limits));return rows,used
    def _run(self,phase,action,verify):
        entered=False
        try:
            self._available()
            if self._busy:self._uncertain=True;raise NativeCapsError()
            require(not self._uncertain and not self.pending.exists())
            self._busy=entered=True
            now=self._observe(phase);previous=self.db.execute('SELECT epoch,mono FROM clock WHERE id=1').fetchone()
            require(previous is not None and all(a>=b for a,b in zip(now,previous)))
            result,limits=action(now)
            after=self._observe(phase,limits);self._validate()
            require(verify() is True);self._available();require(not self._uncertain)
            self._marker();self._save_clock(after);self._available();require(not self._uncertain)
            self._clear_marker();return result
        except BaseException as error:
            if entered:self._uncertain=True
            if entered and self.db is self._db_ref and self.db is not None and self.db.in_transaction:self.db.execute('ROLLBACK')
            if self._pending_written and not self.pending.exists():
                self._pending_written=False
                try:self._marker()
                except Exception:pass
            if not isinstance(error,Exception):raise
            raise NativeCapsError() from None
        finally:
            if entered:self._busy=False
    def _package(self,package):
        fields={'schema','unit','campaign_id','resource_sha256','application_expires_epoch_us',
                'transport','grant','secret_base64','payload_base64','source_run_id','parent'}
        require(type(package) is dict and set(package)==fields and package['schema']=='meshticulum.public-pilot-package.v2')
        parent=package['parent'];require(type(parent) is dict and set(parent)=={'schema','parent_id','policy_digest','allocation_id','child'}
            and parent['schema']=='meshticulum.public-parent-allocation.v1' and parent['parent_id']==self.policy.parent_id
            and parent['policy_digest']==self.policy.digest and package['unit']==self.unit.unit)
        digest(parent['allocation_id']);base={k:v for k,v in package.items() if k!='parent'};base['schema']='meshticulum.public-pilot-package.v1'
        child=child_caps(base,self.policy);require(child.to_dict()==parent['child'])
        from .sf5_batch import BatchApproval
        from .gfsk_batch import BatchApproval as GfskApproval
        if self.policy.qualification_kind=='c-gfsk125-8k-batch-v1':
            require(type(self._approve) is GfskApproval);self._approve.context.package(package)
        if self.policy.qualification_kind=='c-bulk-8k-batch-v1':
            require(type(self._approve) is BatchApproval);self._approve.context.package(package)
        return child,parent['allocation_id']
    def prepare_claim(self,package):
        input_sha=hashlib.sha256(canonical(package)).hexdigest()
        def prepare(now):
            child,allocation=self._package(package);meta=json.loads(child.metadata)
            bundle = getattr(self._approve,'bundle',None)
            from .sf5_batch import BatchApproval
            from .gfsk_batch import BatchApproval as GfskApproval
            if type(self._approve) in (BatchApproval,GfskApproval):
                self._approve.context.metadata(meta)
                for prior_claim, in self.db.execute('SELECT body FROM claims'):
                    old=json.loads(prior_claim)['child']['metadata']
                    require((old['campaign_id'],old['format']) != (meta['campaign_id'],meta['format']))
            if self.policy.qualification_kind in BUNDLE_KINDS:
                from .pilot_bundle import BundleContext
                require(type(self._approve) is _Public32Approval and type(bundle) is BundleContext and bundle is self._approve._bundle_ref)
                stage=bundle.package(package,now[0])
                if self.policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND:
                    from .fast_bundle import stage_receipt
                    stage=stage_receipt(self,stage['index'],child.expires_epoch_us,now)
                first,stage_deadline,_=bundle.monotonic_bounds(stage,self._enrollment)
                if self.policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND:stage_deadline=min(stage_deadline,stage['native_deadline_monotonic_us'])
                require(first<=now[1]<stage_deadline)
                for prior_claim, in self.db.execute('SELECT body FROM claims'):
                    prior_meta=json.loads(prior_claim)['child']['metadata']
                    require((prior_meta['campaign_id'],prior_meta['format']) != (meta['campaign_id'],meta['format']))
            require(now[0]<child.expires_epoch_us and package['grant']['issued_epoch_us']<=now[0])
            resource_binding=dict(parent_id=self.policy.parent_id,unit=self.unit.unit,resource_sha256=meta['resource_sha256'])
            if bundle is not None: resource_binding.update(campaign_id=meta['campaign_id'],bundle_context_sha256=self.policy.bundle_context_sha256)
            resource_id=hashed(resource_binding)
            row=self.db.execute('SELECT body FROM resources WHERE id=?',(resource_id,)).fetchone()
            if row is None:
                resource=dict(resource_sha256=meta['resource_sha256'],application_expires_epoch_us=child.expires_epoch_us,
                    original_epoch_us=now[0],original_monotonic_us=now[1],
                    application_deadline_monotonic_us=min(self._enrollment['deadline_monotonic_us'],now[1]+child.expires_epoch_us-now[0]))
            else:resource=json.loads(row[0]);require(canonical(resource)==row[0] and resource['application_expires_epoch_us']==child.expires_epoch_us)
            if bundle is not None:
                resource.update(campaign_id=meta['campaign_id'],bundle_context_sha256=self.policy.bundle_context_sha256,
                    bundle_original_epoch_us=self._enrollment['original_epoch_us'],
                    bundle_original_monotonic_us=self._enrollment['original_monotonic_us'],
                    bundle_original_deadline_monotonic_us=self._enrollment['deadline_monotonic_us'])
                if row is None:resource['application_deadline_monotonic_us']=min(resource['application_deadline_monotonic_us'],stage_deadline)
                require(resource['application_deadline_monotonic_us']<=stage_deadline)
            if self.policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND:
                stage_raw=canonical(stage)
                if row is None:resource.update(fast_stage_sha256=hashlib.sha256(stage_raw).hexdigest(),
                    fast_stage_deadline_monotonic_us=stage['native_deadline_monotonic_us'])
                require(resource['fast_stage_sha256']==hashlib.sha256(stage_raw).hexdigest()
                    and resource['fast_stage_deadline_monotonic_us']==stage['native_deadline_monotonic_us'])
            limits=(child.expires_epoch_us,resource['application_deadline_monotonic_us']);self._live(now,limits)
            require(now[0]>=resource['original_epoch_us'] and now[1]>=resource['original_monotonic_us'])
            rows,used=self._validate();require(len(rows)<4096 and not any(run==child.run_id for _,_,run in rows))
            require(used.plus(child.cost).within(self.unit.limits))
            claim=dict(child=child.to_dict(),controller_allocation_id=allocation,resource_id=resource_id,resource=resource)
            raw=canonical(claim);claim_id=hashed(claim)
            self._marker();self.db.execute('BEGIN IMMEDIATE')
            if row is None:self.db.execute('INSERT INTO resources VALUES(?,?)',(resource_id,canonical(resource)))
            self.db.execute('INSERT INTO claims VALUES(?,?,?)',(claim_id,raw,child.run_id));self._commit()
            preparation=Preparation(self,claim_id,raw)
            self._preparations[id(preparation)]=(preparation,claim_id,raw,False)
            return preparation,limits
        return self._run('prepare',prepare,lambda:hashlib.sha256(canonical(package)).hexdigest()==input_sha)
    def _receipt(self,claim_id,claim,native_plan,now):
        meta=claim['child']['metadata'];require(type(native_plan) is dict)
        deadline=native_plan.get('application_deadline_monotonic_us')
        require(type(deadline) is int and now[1]<deadline<=claim['resource']['application_deadline_monotonic_us'])
        normalized=dict(native_plan)
        if meta['transport']=='tcp':normalized['application_deadline_monotonic_us']=None
        else:normalized.pop('application_deadline_monotonic_us',None)
        require(hashlib.sha256(canonical(normalized)).hexdigest()==meta['plan_sha256'])
        return dict(schema='meshticulum.native-parent-claim.v1',parent_id=self.policy.parent_id,
            policy_digest=self.policy.digest,controller_allocation_id=claim['controller_allocation_id'],native_claim_id=claim_id,
            unit=self.unit.unit,host_boot_id=self.unit.host_boot_id,run_id=meta['run_id'],resource_sha256=meta['resource_sha256'],
            application_expires_epoch_us=meta['application_expires_epoch_us'],application_deadline_monotonic_us=deadline,
            child_caps=claim['child'],original_plan_sha256=meta['plan_sha256'],
            native_plan_sha256=hashlib.sha256(canonical(native_plan)).hexdigest(),secret_sha256=meta['secret_sha256'],
            grant_context_sha256=meta['grant_digest'],prepared=True)
    def finalize_provision(self,preparation,native_plan):
        input_sha=hashlib.sha256(canonical(native_plan)).hexdigest()
        def finalize(now):
            require(type(preparation) is Preparation and preparation._owner is self
                and self._preparations.get(id(preparation))==(preparation,preparation._claim_id,preparation._raw,False))
            claim_id=preparation._claim_id;claim=json.loads(preparation._raw)
            require(self.db.execute('SELECT body FROM claims WHERE id=?',(claim_id,)).fetchone()==(preparation._raw,))
            receipt=self._receipt(claim_id,claim,native_plan,now)
            self._preparations[id(preparation)]=(preparation,claim_id,preparation._raw,True)
            self._marker();self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT INTO prepared VALUES(?,?)',(claim_id,canonical(receipt)));self._commit()
            return receipt,(receipt['application_expires_epoch_us'],receipt['application_deadline_monotonic_us'])
        return self._run('finalize',finalize,lambda:hashlib.sha256(canonical(native_plan)).hexdigest()==input_sha)
    def consume_start(self,run_id,native_plan,secret):
        input_sha=hashlib.sha256(canonical(native_plan)).hexdigest()
        def consume(now):
            identifier(run_id);require(self._active is None and type(secret) is bytes and len(secret)==32)
            row=self.db.execute('SELECT id,body FROM claims WHERE run_id=?',(run_id,)).fetchone();require(row is not None)
            claim_id,raw=row;claim=json.loads(raw);expected=self._receipt(claim_id,claim,native_plan,now)
            require(hashlib.sha256(secret).hexdigest()==expected['secret_sha256']
                and self.db.execute('SELECT body FROM prepared WHERE id=?',(claim_id,)).fetchone()==(canonical(expected),))
            receipt=dict(expected,schema='meshticulum.native-parent-start.v1',started=True)
            self._marker();self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT INTO starts VALUES(?,?)',(claim_id,canonical(receipt)));self._commit()
            self._active=self._active_ref=canonical(receipt)
            return receipt,(receipt['application_expires_epoch_us'],receipt['application_deadline_monotonic_us'])
        return self._run('start',consume,lambda:hashlib.sha256(canonical(native_plan)).hexdigest()==input_sha)
    @phase('native_current')
    def current(self):
        entered=False
        try:
            self._available();require(not self._busy and not self._uncertain and not self.pending.exists())
            self._busy=entered=True;limits=None
            if self._active is not None:
                receipt=json.loads(self._active);limits=(receipt['application_expires_epoch_us'],receipt['application_deadline_monotonic_us'])
                require(timed_call(getattr(self,'_phase_timing',None),'sql_active_start',
                    lambda:self.db.execute('SELECT body FROM starts WHERE id=?',(receipt['native_claim_id'],)).fetchone())==(self._active,))
            self._observe('current',limits);self._validate();return True
        except Exception:
            if entered or self._busy:self._uncertain=True
            return False
        finally:
            if entered:self._busy=False
    def snapshot(self):
        self._available();require(not self._busy);rows,used=self._validate()
        return dict(parent_id=self.policy.parent_id,policy_digest=self.policy.digest,unit=self.unit.unit,
            claims=len(rows),prepared=self.db.execute('SELECT COUNT(*) FROM prepared').fetchone()[0],
            starts=self.db.execute('SELECT COUNT(*) FROM starts').fetchone()[0],debt=asdict(used),
            uncertain=self._uncertain or self.pending.exists(),physical_handoff_proven=False)
    def close(self):
        if self._pid!=os.getpid() or self._thread!=threading.get_ident():return False
        self._closed=True
        if self._busy:return False
        if self._db_ref is not None:self._db_ref.close();self.db=self._db_ref=None
        valid=self._fds==self._fd_refs
        for name,fd in list(self._fd_refs.items()):
            try:
                info=os.fstat(fd);require((info.st_dev,info.st_ino)==self._ids[name]);os.close(fd)
            except Exception:valid=False
        self._fds.clear();self._fd_refs.clear()
        approval=self._refs[6] if hasattr(self,'_refs') else self._approve
        from .sf5_batch import BatchApproval
        from .gfsk_batch import BatchApproval as GfskApproval
        if type(approval) in (_Public32Approval,BatchApproval,GfskApproval):approval.close()
        return valid and not self._uncertain

    def public32_owner_current(self):
        require(type(self._approve) is _Public32Approval)
        return self.current()


def _open_existing(state,parent_id,facts,clock):
    """Protected local enrollment, not a policy transported by management."""
    try:
        identifier(parent_id);require(type(facts) is dict and callable(clock))
        state=Path(state).absolute();private(state.lstat(),True);require(state.resolve()==state)
        parents=state/'parents';directory=parents/parent_id
        private(parents.lstat(),True);private(directory.lstat(),True)
        path=directory/'native.sqlite';fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        connection=None
        try:
            private(os.fstat(fd));connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,isolation_level=None)
            row=connection.execute('SELECT body FROM enrollment WHERE id=1').fetchone();require(row is not None)
            value=json.loads(row[0]);policy=ParentPolicy.from_dict(value['policy']);require(policy.parent_id==parent_id and value['unit']==facts['unit'])
        finally:
            if connection is not None:connection.close()
            os.close(fd)
        unit=policy.unit(facts['unit']);require(facts['host_boot_id']==unit.host_boot_id)
        # A retained protected enrollment flag supplies current approval; the
        # adapter neither invokes enrollment nor fabricates a prior snapshot.
        def observed():return NativeFacts(facts['unit'],facts['host_boot_id'],unit.peer)
        approve=lambda _:True
        from .sf5_batch import BatchApproval,KIND as BATCH_KIND
        from .gfsk_batch import BatchApproval as GfskApproval,KIND as GFSK_KIND
        if policy.qualification_kind==GFSK_KIND:
            approve=GfskApproval(directory,policy,unit.unit,facts,clock)
        if policy.qualification_kind==BATCH_KIND:
            approve=BatchApproval(directory,policy,unit.unit,facts,clock)
        if policy.qualification_kind in PUBLIC32_KINDS:
            approve=_Public32Approval(directory,policy,unit.unit,facts,clock)
        try:return NativeCaps.open(path,policy,unit=unit.unit,clock=clock,facts=observed,approve=approve)
        except BaseException:
            if type(approve) in (_Public32Approval,BatchApproval,GfskApproval):approve.close()
            raise
    except Exception:raise NativeCapsError() from None


def open_for_provision(state,facts,clock,package):
    try:
        require(type(package) is dict and package['schema']=='meshticulum.public-pilot-package.v2')
        caps=_open_existing(state,package['parent']['parent_id'],facts,clock)
        try:caps._package(package);require(caps.current());return caps
        except BaseException:caps.close();raise
    except Exception:raise NativeCapsError() from None


def open_for_run(state,directory,facts,clock):
    try:
        directory=Path(directory).absolute();private(directory.lstat(),True)
        fd=os.open(directory/'provision.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:private(os.fstat(fd));raw=os.read(fd,32769)
        finally:os.close(fd)
        require(len(raw)<=32768);receipt=json.loads(raw)
        require(receipt['schema']=='meshticulum.provisioned-public-pilot.v2' and receipt['unit']==facts['unit'])
        expected=receipt['parent'];caps=_open_existing(state,expected['parent_id'],facts,clock)
        try:
            require(caps.db.execute('SELECT body FROM prepared WHERE id=?',(expected['native_claim_id'],)).fetchone()==(canonical(expected),)
                    and expected['run_id']==receipt['run_id'] and expected['unit']==facts['unit'] and caps.current())
            return caps
        except BaseException:caps.close();raise
    except Exception:raise NativeCapsError() from None
