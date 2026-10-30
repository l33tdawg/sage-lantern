"""Local evidence controls only; no real campaign preparation, grants or RPC.

The API-double tests check the consumer independently of the shared codec. Real
codec closure/fault controls are added when that source hash is accepted.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import common
import controller
import file_run
from test_file_run import RunFixture


def put(path,value):
    raw=value if type(value) is bytes else common.canonical(value)
    path.write_bytes(raw)
    return dict(path=str(path),sha256=common.sha(raw),bytes=len(raw))


def unsized(row):return {k:row[k] for k in ('path','sha256')}


class Evidence:
    def __init__(self,path,monkeypatch,*,recursive=False,traversed=True):
        self.path=path.resolve();self.path.mkdir(exist_ok=True)
        self.leaf=put(self.path/'paid.json',dict(paid=9,unknown_actual_cost=None))
        self.oldroot=None;self.metadata={}
        inherited=[unsized(self.leaf)]
        previous=dict(schema='meshticulum.alpha3-individual-prepared.v1',historical_evidence=inherited)
        if recursive:
            self.oldroot=put(self.path/'prior-index.json',dict(source_only=True,files=[self.leaf]))
            older=put(self.path/'older-prepared.json',b'older prepared bytes')
            reconciled=put(self.path/'older-reconciliation.json',b'older reconciled bytes')
            previous['historical_evidence']=[unsized(self.oldroot)]
            previous['historical_closure']=dict(schema=controller.HISTORY_CLOSURE_SCHEMA,
                root_index=self.oldroot,previous_prepared=older,root_reconciliation=reconciled,
                source_sha256='c'*64,validation=dict(previous_verified=True))
            if traversed:self.metadata[self.oldroot['path']]=(self.path/'prior-index.json').read_bytes()
        self.previous=previous
        self.previous_ref=put(self.path/'previous.json',previous)
        self.scope=dict(prepared_sha256=self.previous_ref['sha256'],controller_boot='same-boot',
            expires_epoch_us=100,original_deadline_monotonic_us=200)
        self.scope_ref=put(self.path/'scope.json',self.scope)
        self.reconciliation=dict(prepared=unsized(self.previous_ref),scope=unsized(self.scope_ref),
            complete=True,automatic_retry=False,trial_closed=True,scope_expired_at_reconciliation=True,all_bursts_drained=True,
            all_radios_unarmed_after_original_remote_deadlines=True,all_serial_channels_closed=True,
            new_rf_authorized=False,refunds=0,global_historical_debt_complete=False,
            original_scope_expires_epoch_us=100,original_scope_deadline_monotonic_us=200,
            historical_unknown_actual_cost=None,expiry_observation=dict(controller_boot='same-boot',
                epoch_us=101,monotonic_us=201))
        self.reconciliation_ref=put(self.path/'reconciliation.json',self.reconciliation)
        self.rows=[self.leaf,self.previous_ref,self.scope_ref,self.reconciliation_ref]
        if self.oldroot:self.rows.append(self.oldroot)
        self.root=put(self.path/'index.json',dict(source_only=True,files=self.rows))
        self.value=dict(schema=controller.HISTORY_CLOSURE_SCHEMA,root_index=self.root,
            previous_prepared=self.previous_ref,root_reconciliation=self.reconciliation_ref)
        self.history=[unsized(self.root)]
        self.calls=[]
        self.refresh()
        monkeypatch.setattr(controller,'HISTORY_CODEC_SHA256','c'*64)
        monkeypatch.setattr(controller,'closure_codec',lambda:(self.resolve,b'source-only API double'))

    def refresh(self):
        self.rows.sort(key=lambda row:row['path'])
        self.stats=dict(index_documents=1+bool(self.metadata),
            index_metadata_bytes=self.root['bytes']+sum(map(len,self.metadata.values())),
            direct_files=len(self.rows),expanded_files=len(self.rows),
            expanded_bytes=sum(row['bytes'] for row in self.rows))

    def resolve(self,raw,read):
        self.calls.append((raw,read))
        assert raw==(self.path/'index.json').read_bytes()
        return deepcopy(self.rows),dict(self.metadata),dict(self.stats)

    def check(self):return controller.checked_historical_closure(self.value,self.history)

    def bound(self):
        return dict(historical_closure=self.check(),historical_evidence=self.history,
            controller_sources={name:'c'*64 for name in controller.HARNESSES+('history_encoding.py',)})

    def mutate_reconciliation(self,change):
        change(self.reconciliation)
        ref=put(self.path/'reconciliation.json',self.reconciliation)
        self.rows=[ref if row['path']==ref['path'] else row for row in self.rows]
        self.value['root_reconciliation']=ref;self.refresh()


def test_unaccepted_codec_refuses_before_reads_imports_or_output(tmp_path,monkeypatch):
    monkeypatch.setattr(controller,'HISTORY_CODEC_SHA256',None)
    monkeypatch.setattr(controller,'read_history',lambda *a,**k:pytest.fail('evidence read'))
    monkeypatch.setattr(controller,'closure_codec',lambda:pytest.fail('codec execution'))
    with pytest.raises(ValueError,match='not_accepted'):
        controller.checked_historical_closure(None,[])
    path=tmp_path/'input.json';path.write_bytes(b'{"historical_closure":null,"historical_evidence":[]}')
    with pytest.raises(ValueError,match='not_accepted'):controller.prepare(path,tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_omitted_option_keeps_twelve_files_and_no_evidence_reads(monkeypatch):
    monkeypatch.setattr(controller,'read_history',lambda *a,**k:pytest.fail('unexpected evidence read'))
    legacy={};assert controller.validate_historical_closure(legacy) is None
    assert legacy=={} and controller.controller_harnesses(legacy)==controller.HARNESSES
    assert len(controller.HARNESSES)==12
    assert controller.controller_harnesses({'historical_closure':{}})==controller.HARNESSES+('history_encoding.py',)
    with pytest.raises(ValueError,match='unexpected_history_codec'):
        controller.validate_historical_closure({'controller_sources':{'history_encoding.py':'c'*64}})


def test_all_leaves_read_and_canonical_summary_contains_no_repaid_authority(tmp_path,monkeypatch):
    f=Evidence(tmp_path,monkeypatch);seen=[];original=controller.read_history
    def record(path,*a,**k):seen.append(path);return original(path,*a,**k)
    monkeypatch.setattr(controller,'read_history',record)
    value=f.check()
    assert set(seen)=={f.root['path'],*(r['path'] for r in f.rows)}
    assert value['validation']['expanded_files']==4
    assert value['validation']['inherited_direct_files']==1
    assert value['validation']['expanded_rows_sha256']==common.sha(common.canonical(f.rows))
    assert value['validation']['global_historical_debt_complete'] is False
    assert value['validation']['historical_unknown_actual_cost'] is None
    assert value['validation']['refunds']==0 and value['validation']['new_rf_authorized'] is False
    assert f.value=={key:value[key] for key in controller.HISTORY_CLOSURE_KEYS}
    assert not list(tmp_path.glob('*CONSUMED*'))


@pytest.mark.parametrize('change',[lambda x:x.update(source_sha256='d'*64),
    lambda x:x['validation'].update(expanded_files=0),lambda x:x.update(extra=True)])
def test_bound_summary_cannot_be_changed_between_actions(tmp_path,monkeypatch,change):
    f=Evidence(tmp_path,monkeypatch);p=f.bound();change(p['historical_closure'])
    with pytest.raises(ValueError):controller.validate_historical_closure(p)


@pytest.mark.parametrize('change',[lambda p:p['controller_sources'].pop('common.py'),
    lambda p:p['controller_sources'].update(other='c'*64),
    lambda p:p['controller_sources'].update(**{'history_encoding.py':'d'*64})])
def test_opt_in_inventory_is_exactly_thirteen_pinned_sources(tmp_path,monkeypatch,change):
    f=Evidence(tmp_path,monkeypatch);p=f.bound();change(p)
    with pytest.raises(ValueError):controller.validate_historical_closure(p)


@pytest.mark.parametrize('action',['load','stage','issue','run','coordinator'])
def test_changed_leaf_stops_each_boundary_before_sdk_clock_grants_rpc_or_consume(tmp_path,monkeypatch,action):
    f=Evidence(tmp_path,monkeypatch);p=f.bound()
    (tmp_path/'paid.json').write_bytes(b'changed')
    monkeypatch.setattr(controller,'BASE',tmp_path.resolve())
    for name in ('sdk','clock','rpc','write_new'):
        monkeypatch.setattr(controller,name,lambda *a,**k:pytest.fail('action after changed history'))
    with pytest.raises(ValueError):
        if action=='load':
            (tmp_path/'PREPARED.json').write_bytes(common.canonical(p));controller.load_run()
        elif action=='stage':controller.stage(p,'never used')
        elif action=='issue':controller.issue(p)
        elif action=='run':controller.run(p,'never used','a'*64)
        else:file_run.run_file(controller,p,'never used','a'*64)


def test_load_reopens_history_safely_if_fifo_replaces_root_after_full_validation(tmp_path,monkeypatch):
    f=Evidence(tmp_path,monkeypatch);p=f.bound();p['files']={}
    monkeypatch.setattr(controller,'BASE',tmp_path.resolve())
    for name in controller.HARNESSES+('history_encoding.py',):
        ref=put(tmp_path/name,b'# source-only mock\n');p['controller_sources'][name]=ref['sha256']
    p['controller_sources']['history_encoding.py']='c'*64
    original=controller.validate_historical_closure
    def validate_then_replace(value):
        result=original(value)
        path=Path(f.root['path']);path.unlink();os.mkfifo(path)
        return result
    monkeypatch.setattr(controller,'validate_historical_closure',validate_then_replace)
    actual_read=controller.read_history
    def source_or_history(path,pin,**kwargs):
        if Path(path).name=='history_encoding.py':return b'# mocked accepted codec source\n'
        return actual_read(path,pin,**kwargs)
    monkeypatch.setattr(controller,'read_history',source_or_history)
    monkeypatch.setattr(controller,'pinned',lambda *a,**k:pytest.fail('unsafe history reopen'))
    monkeypatch.setattr(controller,'read_pinned',lambda *a,**k:pytest.fail('unsafe source reopen'))
    monkeypatch.setattr(controller,'sdk',lambda *a:pytest.fail('SDK after changed history'))
    (tmp_path/'PREPARED.json').write_bytes(common.canonical(p))
    with pytest.raises(ValueError,match='invalid_history_file'):controller.load_run()


@pytest.mark.parametrize('corrupt_window',[False,True])
def test_modeled_issuance_copies_exact_binding_before_any_grant(tmp_path,monkeypatch,corrupt_window):
    f=Evidence(tmp_path/'evidence',monkeypatch);p=f.bound()
    base=tmp_path.resolve();campaign=base/'campaign';campaign.mkdir()
    common.write_new(base/'STAGED.json',{u:{'complete':True} for u in common.UNITS})
    raw=b'source-only response';entry={'sha256':common.sha(raw)}
    common.write_new(base/'prepared/pack/manifest.json',{'chunks':[entry]})
    common.write_new(base/'prepared/pack/chunks'/(entry['sha256']+'.json'),raw,raw=True)
    p.update(id='synthetic',campaign_directory=str(campaign),run_index=0,campaign_id='synthetic',
        policy=dict(scope_seconds=180,max_frames=128,operation_attempt_allowance=10,
            operation_estimated_airtime_allowance_us=300000),
        run_budget={u:dict(attempts=10,estimated_airtime_us=300000) for u in common.UNITS},
        comparison_mode='queued',data_burst=common.data_burst_policy(),sender='unit-a',receiver='unit-b',
        units={u:dict(host_boot_id='synthetic') for u in common.UNITS},peers={u:{} for u in common.UNITS},
        profile={'source_only':True},manifest_sha256='a'*64,object_id='b'*64,useful_bytes=1,
        useful_sha256='d'*64,core_commit='e'*40,part_count=1,order=[0],
        parts=[dict(response_bytes=len(raw),response_sha256=common.sha(raw))])
    bundle=SimpleNamespace(response_for=lambda *a:raw,service_for=lambda *a:{'source_only':True})
    monkeypatch.setitem(__import__('sys').modules,'radio_bundle',bundle)
    monkeypatch.setattr(controller,'BASE',base)
    monkeypatch.setattr(controller,'clock',lambda:(1000,2000))
    monkeypatch.setattr(controller,'boot_id',lambda:'synthetic-clock')
    monkeypatch.setattr(controller,'rpc',lambda *a,**k:pytest.fail('network'))
    writes=[];real_write=controller.write_new
    def write(path,value,**kwargs):
        writes.append(path)
        if path.name=='CAMPAIGN_WINDOW.json':
            assert value['historical_closure']==p['historical_closure']
            if corrupt_window:value['historical_closure']=dict(value['historical_closure'],source_sha256='f'*64)
        return real_write(path,value,**kwargs)
    monkeypatch.setattr(controller,'write_new',write)
    if corrupt_window:
        with pytest.raises(ValueError,match='history_closure_binding_changed'):controller.issue(p)
        assert not (base/'issued').exists() and not (base/'SCOPE.json').exists()
    else:
        controller.issue(p)
        scope=common.strict_json((base/'SCOPE.json').read_bytes())
        window=common.strict_json((campaign/'CAMPAIGN_WINDOW.json').read_bytes())
        assert scope['historical_closure']==window['historical_closure']==p['historical_closure']
        assert scope['expires_epoch_us']==180001000
        assert scope['original_deadline_monotonic_us']==180002000
        assert scope['policy']==p['policy'] and scope['data_burst']==p['data_burst']
        assert (campaign/'CAMPAIGN_CLAIM_0.json') in writes


def test_transitive_predecessor_index_as_ordinary_leaf_does_not_prove_its_closure(tmp_path,monkeypatch):
    f=Evidence(tmp_path,monkeypatch,recursive=True,traversed=False)
    # The preceding PREPARED's direct refs are present, but its old payload is
    # deliberately omitted. Index bytes alone must not make the successor pass.
    f.rows=[r for r in f.rows if r!=f.leaf];f.refresh()
    with pytest.raises(ValueError,match='previous_closure_not_traversed'):f.check()


def test_recursive_predecessor_traversed_in_one_global_call(tmp_path,monkeypatch):
    f=Evidence(tmp_path,monkeypatch,recursive=True)
    assert f.check()['validation']['previous_recursive_closure'] is True
    assert len(f.calls)==1


def test_omitted_legacy_inherited_leaf_refuses_even_when_new_direct_refs_exist(tmp_path,monkeypatch):
    f=Evidence(tmp_path,monkeypatch);f.rows.remove(f.leaf);f.refresh()
    with pytest.raises(ValueError,match='inherited_history_omitted'):f.check()


@pytest.mark.parametrize('change',[lambda r:r['prepared'].update(sha256='d'*64),
    lambda r:r.update(complete=False),lambda r:r.update(automatic_retry=True),
    lambda r:r.update(trial_closed=False),lambda r:r.update(scope_expired_at_reconciliation=False),
    lambda r:r.update(all_bursts_drained=False),lambda r:r.update(all_serial_channels_closed=False),
    lambda r:r.update(new_rf_authorized=True),lambda r:r.update(refunds=False),
    lambda r:r.update(refunds=1),lambda r:r.update(global_historical_debt_complete=True),
    lambda r:r.update(historical_unknown_actual_cost=0),
    lambda r:r['expiry_observation'].update(controller_boot='different'),
    lambda r:r['expiry_observation'].update(epoch_us=99),
    lambda r:r['expiry_observation'].update(monotonic_us=199),
    lambda r:r['expiry_observation'].update(epoch_us=True),
    lambda r:r.update(original_scope_expires_epoch_us=99),
    lambda r:r.update(original_scope_deadline_monotonic_us=201),
    lambda r:r.update(original_scope_expires_epoch_us=True)])
def test_reconciliation_must_bind_closed_expired_original_trial_without_refund(tmp_path,monkeypatch,change):
    f=Evidence(tmp_path,monkeypatch);f.mutate_reconciliation(change)
    with pytest.raises(ValueError):f.check()


@pytest.mark.parametrize('value',[None,True,[],{},0,{'schema':controller.HISTORY_CLOSURE_SCHEMA}])
def test_explicit_partial_or_null_closure_has_no_fallback(tmp_path,monkeypatch,value):
    f=Evidence(tmp_path,monkeypatch)
    with pytest.raises(ValueError):controller.checked_historical_closure(value,f.history)


@pytest.mark.parametrize('field,value',[('bytes',True),('bytes',0),('bytes',8_000_001),
    ('bytes',1.0),('sha256','D'*64),('path','relative'),('path','/a/../b'),
    ('path','//a'),('path','/a//b'),('path','/a/./b'),('path','/a\0b')])
def test_exact_reference_types_and_canonical_paths(tmp_path,monkeypatch,field,value):
    f=Evidence(tmp_path,monkeypatch);f.value['previous_prepared'][field]=value
    with pytest.raises(ValueError):f.check()


def test_direct_history_bound_duplicate_and_root_binding(tmp_path,monkeypatch):
    f=Evidence(tmp_path,monkeypatch)
    for history in ([],f.history*2,f.history*513,[unsized(f.leaf)]):
        with pytest.raises(ValueError):controller.checked_historical_closure(f.value,history)


@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'{"a":{"b":1,"b":2}}',
    b'{"a":NaN}',b'{"a":Infinity}',b'{"a":1e999}',b'{bad}',b'\xff',b'['*40+b'0'+b']'*40])
def test_duplicate_malformed_nonfinite_or_deep_json_refuses(raw):
    with pytest.raises(ValueError):common.strict_json(raw)


def test_json_child_capacity_and_finite_rates():
    assert common.strict_json(b'{"rate":2872.004838426896}')=={'rate':2872.004838426896}
    with pytest.raises(ValueError,match='evidence_json_complexity'):
        common.strict_json(b'['+b'0,'*200_000+b'0]')


@pytest.mark.parametrize('kind',['symlink','parent_symlink','hardlink','fifo','directory'])
def test_leaf_path_must_be_nonblocking_unaliased_regular_file(tmp_path,kind):
    tmp_path=tmp_path.resolve();real=tmp_path/'actual';real.mkdir()
    target=real/'leaf';target.write_bytes(b'paid')
    path=tmp_path/'leaf'
    if kind=='symlink':path.symlink_to(target)
    elif kind=='parent_symlink':
        parent=tmp_path/'alias';parent.symlink_to(real,target_is_directory=True);path=parent/'leaf'
    elif kind=='hardlink':os.link(target,path)
    elif kind=='fifo':os.mkfifo(path)
    else:path.mkdir()
    with pytest.raises((ValueError,OSError)):
        common.read_history(str(path),common.sha(b'paid'),size=4)


def test_replacement_fifo_is_nonblocking_before_fstat(tmp_path,monkeypatch):
    tmp_path=tmp_path.resolve();path=tmp_path/'leaf';path.write_bytes(b'paid')
    real_open=os.open;replaced=False
    def swap(name,flags,*args,**kwargs):
        nonlocal replaced
        if name=='leaf' and not replaced:
            assert flags&os.O_NONBLOCK and flags&os.O_NOFOLLOW
            path.unlink();os.mkfifo(path);replaced=True
        return real_open(name,flags,*args,**kwargs)
    monkeypatch.setattr(common.os,'open',swap)
    with pytest.raises(ValueError,match='invalid_history_file'):
        common.read_history(str(path),common.sha(b'paid'),size=4)
    assert replaced


def test_replacement_after_read_is_detected(tmp_path,monkeypatch):
    tmp_path=tmp_path.resolve();path=tmp_path/'leaf';path.write_bytes(b'paid')
    real_read=os.read;done=False
    def swap(fd,n):
        nonlocal done
        raw=real_read(fd,n)
        if not done:
            path.unlink();path.write_bytes(b'paid');done=True
        return raw
    monkeypatch.setattr(common.os,'read',swap)
    with pytest.raises(ValueError,match='history_(file|path)_changed'):
        common.read_history(str(path),common.sha(b'paid'),size=4)


@pytest.mark.parametrize('mutation',[lambda p:p.pop('historical_closure'),
    lambda p:p['historical_closure'].update(source_sha256='e'*64),
    lambda p:p['historical_closure']['validation'].update(expanded_bytes=0)])
@pytest.mark.parametrize('target',['scope','window'])
def test_scope_or_window_mismatch_never_consumes_or_launches(tmp_path,monkeypatch,mutation,target):
    f=RunFixture(str(tmp_path.resolve()),order=(0,))
    binding={'source_sha256':'c'*64,'validation':{'expanded_bytes':42}}
    f.prepared['historical_closure']=deepcopy(binding)
    f.scope.update(prepared_sha256=common.sha(common.canonical(f.prepared)),historical_closure=deepcopy(binding))
    window=deepcopy(f.scope)
    mutation(f.scope if target=='scope' else window)
    f.pin=common.sha(common.canonical(f.scope))
    (f.base/'SCOPE.json').write_bytes(common.canonical(f.scope))
    (f.campaign/'CAMPAIGN_WINDOW.json').write_bytes(common.canonical(window))
    (f.campaign/'CAMPAIGN_CLAIM_0.json').write_bytes(common.canonical(dict(scope_sha256=f.pin,
        reserved=f.prepared['run_budget'],campaign_window_sha256=common.sha(common.canonical(window)))))
    f.api.validate_historical_closure=lambda p:binding
    f.api.strict_json=common.strict_json;f.api.bind_historical_closure=common.bind_historical_closure
    with pytest.raises(ValueError,match='history_closure_binding_changed'):
        f.run(lambda *a,**k:pytest.fail('RPC before exact closure binding'))
    assert not (f.base/'SCOPE_CONSUMED.json').exists() and not f.calls


def test_checked_codec_bytes_are_pinned_before_execution(tmp_path,monkeypatch):
    tmp_path=tmp_path.resolve();source=b'raise AssertionError("untrusted source executed")\n'
    (tmp_path/'history_encoding.py').write_bytes(source)
    monkeypatch.setattr(controller,'BASE',tmp_path)
    monkeypatch.setattr(controller,'HISTORY_CODEC_SHA256','f'*64)
    with pytest.raises(ValueError,match='history_pin_changed'):controller.closure_codec()


def real_index(rows,parents=()):
    value=dict(schema='meshticulum.app-upgrade-local-history-index.v1',
        files=sorted(rows,key=lambda row:row['path']),pin_mismatches=[],
        global_historical_debt_complete=False,historical_unknown_actual_cost=None,
        install_authority=False,rf_authority=False)
    if parents:value.update(schema='meshticulum.app-upgrade-local-history-index.v2',
        parents=sorted(parents,key=lambda row:row['path']))
    return value


def real_evidence(tmp_path,monkeypatch):
    loader=controller.closure_codec;pin=controller.HISTORY_CODEC_SHA256
    f=Evidence(tmp_path,monkeypatch)
    monkeypatch.setattr(controller,'closure_codec',loader)
    monkeypatch.setattr(controller,'HISTORY_CODEC_SHA256',pin)
    f.root=put(tmp_path/'index.json',real_index(f.rows))
    f.value['root_index']=f.root;f.history=[unsized(f.root)]
    return f


def test_copied_accepted_codec_is_exact_and_real_consumer_rechecks_all_bytes(tmp_path,monkeypatch):
    f=real_evidence(tmp_path,monkeypatch)
    resolve,source=controller.closure_codec()
    assert common.sha(source)==controller.HISTORY_CODEC_SHA256==\
        '3aea3c585e030c5163992cbf76a196c51c04559cf6cb96dd06c853be22747d42'
    result=f.check()
    assert result['validation']['expanded_files']==4
    assert result['validation']['inherited_direct_files']==1
    assert result['validation']['expanded_rows_sha256']==common.sha(common.canonical(f.rows))
    (tmp_path/'paid.json').write_bytes(b'other paid bytes')
    with pytest.raises(ValueError):f.check()


@pytest.mark.parametrize('traverse',[True,False])
def test_real_recursive_predecessor_requires_same_global_parent_traversal(tmp_path,monkeypatch,traverse):
    f=real_evidence(tmp_path,monkeypatch);prior_binding=f.check()
    # This predecessor binding was actually produced by the accepted validator.
    previous=put(tmp_path/'recursive-prepared.json',dict(
        schema='meshticulum.alpha3-individual-prepared.v1',historical_evidence=f.history,
        historical_closure=prior_binding))
    scope=put(tmp_path/'recursive-scope.json',dict(f.scope,prepared_sha256=previous['sha256']))
    reconciliation=put(tmp_path/'recursive-reconciliation.json',dict(f.reconciliation,
        prepared=unsized(previous),scope=unsized(scope)))
    root=put(tmp_path/'next-index.json',real_index([f.root,previous,scope,reconciliation],
        [f.root] if traverse else []))
    option=dict(schema=controller.HISTORY_CLOSURE_SCHEMA,root_index=root,
        previous_prepared=previous,root_reconciliation=reconciliation)
    if not traverse:
        with pytest.raises(ValueError,match='previous_closure_not_traversed'):
            controller.checked_historical_closure(option,[unsized(root)])
    else:
        value=controller.checked_historical_closure(option,[unsized(root)])
        assert value['validation']['previous_recursive_closure'] is True
        assert value['validation']['index_documents']==2
        assert value['validation']['expanded_files']==8
        assert value['validation']['inherited_direct_files']==1
        (tmp_path/'paid.json').unlink()
        with pytest.raises(FileNotFoundError):
            controller.checked_historical_closure(option,[unsized(root)])


def test_real_parent_index_duplicate_json_never_reaches_leaf(tmp_path,monkeypatch):
    f=real_evidence(tmp_path,monkeypatch)
    parent=put(tmp_path/'bad-parent.json',b'{"files":[],"files":[]}')
    root=put(tmp_path/'bad-index.json',real_index([parent],[parent]))
    option=dict(f.value,root_index=root)
    with pytest.raises(ValueError):controller.checked_historical_closure(option,[unsized(root)])


def test_real_shared_closure_conflict_rejects_before_ordinary_payload_reads(tmp_path,monkeypatch):
    f=real_evidence(tmp_path,monkeypatch)
    parent=put(tmp_path/'parent.json',real_index([dict(f.leaf,sha256='e'*64)]))
    root=put(tmp_path/'conflict-index.json',real_index([f.leaf,parent],[parent]))
    option=dict(f.value,root_index=root);seen=[];original=controller.read_history
    def read(path,*args,**kwargs):
        seen.append(path)
        assert path!=f.leaf['path']
        return original(path,*args,**kwargs)
    monkeypatch.setattr(controller,'read_history',read)
    with pytest.raises(ValueError):controller.checked_historical_closure(option,[unsized(root)])
    assert parent['path'] in seen
