"""Pure binding interpretation reuse; every authority observation remains fresh."""
import ast
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import time

import pytest
from lantern_meshticulum.bench import pilot_native_caps as native_caps
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests.test_fast_bundle import native,fast_data,one_part
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path,protected_source,clock,facts,BOOT

OUT=Path(__file__).resolve().parents[4]


def current_owner(n):
    return native_caps._open_existing(n['roots']['unit-c'],n['policy'].parent_id,facts('unit-c'),n['clock'])


def binding(caps):
    return canonical(dict(enrollment=caps._enrollment,phase='current'))


def test_warm_decode_reuse_preserves_every_fresh_sql_and_reader_observation(native,monkeypatch):
    n=native;one_part(n,0);caps=current_owner(n)
    queries=[];counts=dict(owner=0,runtime=0)
    caps.db.set_trace_callback(queries.append)
    for label,reader in [('owner',caps._approve.reader),('runtime',caps._approve.runtime)]:
        old=reader.check
        def check(_old=old,_label=label):counts[_label]+=1;return _old()
        monkeypatch.setattr(reader,'check',check)
    try:
        assert caps.current() is True
        first=tuple(queries);queries.clear();before=caps._approve._binding_rows.cache_info()
        assert caps.current() is True
        after=caps._approve._binding_rows.cache_info()
        assert tuple(queries)==first and counts==dict(owner=2,runtime=2)
        assert after.hits==before.hits+1 and after.misses==before.misses
        assert sum('SELECT body FROM resources' in query for query in first)==2
        assert sum('SELECT body FROM enrollment' in query for query in first)==1
        assert sum('SELECT id,body,run_id FROM claims' in query for query in first)==1
        assert caps.snapshot()['debt']==asdict(caps.unit.prior_debt.plus(native_caps.Cost(512,120000000)))
    finally:assert caps.close()


@pytest.mark.parametrize('fault',['history','policy','unit','host','expiry','deadline','phase'])
def test_changed_exact_binding_bytes_never_reuse_a_previous_decision(native,fault):
    n=native;caps=current_owner(n);raw=binding(caps)
    try:
        assert caps._approve(raw) is True and caps._approve(raw) is True
        value=json.loads(raw);e=value['enrollment']
        if fault=='history':e['history_binding_sha256']='0'*64
        elif fault=='policy':e['policy']['expires_epoch_us']+=1
        elif fault=='unit':e['unit']='unit-a'
        elif fault=='host':e['host_boot_id']='00000000-0000-4000-8000-000000000099'
        elif fault=='expiry':e['original_epoch_us']=caps.policy.expires_epoch_us
        elif fault=='deadline':e['deadline_monotonic_us']+=1
        else:value['phase']='retired'
        with pytest.raises(Exception):caps._approve(canonical(value))
    finally:assert caps.close()


@pytest.mark.parametrize('fault',['same_connection','external_commit','expiry','approval','owner','boot','clock_regression','source'])
def test_reuse_does_not_hide_current_storage_clock_or_authority_drift(native,fault):
    n=native;one_part(n,0);caps=current_owner(n)
    try:
        assert caps.current() is True and caps.current() is True
        if fault in ('same_connection','external_commit'):
            db=caps.db if fault=='same_connection' else sqlite3.connect(caps.path)
            try:
                table='resources' if fault=='same_connection' else 'claims'
                row_id,raw=db.execute('SELECT id,body FROM '+table+' LIMIT 1').fetchone();value=json.loads(raw)
                if table=='resources':value['application_expires_epoch_us']-=1
                else:value['child']['cost']['radio_attempts']=0
                db.execute('DROP TRIGGER immutable_'+table+'_UPDATE')
                db.execute('UPDATE '+table+' SET body=? WHERE id=?',(canonical(value),row_id))
                if fault=='external_commit':db.commit()
            finally:
                if fault=='external_commit':db.close()
        elif fault=='expiry':n['now'][0]=(caps.policy.expires_epoch_us,n['now'][0][1]+1350000000)
        elif fault=='approval':Path(str(caps.path)+'.approval').write_bytes(b'0'*64+b'\n')
        elif fault=='owner':(caps.path.parent/'owner.json').write_bytes((caps.path.parent/'owner.json').read_bytes()+b'\n')
        elif fault=='boot':caps._approve.facts['host_boot_id']='00000000-0000-4000-8000-000000000099'
        elif fault=='clock_regression':n['now'][0]=tuple(v-1 for v in n['now'][0])
        else:
            path=caps._approve.runtime.root/'lantern_meshticulum/bench/pilot_native_caps.py'
            path.write_bytes(path.read_bytes()+b'\n# modeled source drift\n')
        assert caps.current() is False
    finally:
        if caps.db.in_transaction:caps.db.rollback()
        caps.close()


def test_rollback_still_uses_fresh_rows_and_preserves_original_debt(native):
    n=native;one_part(n,0);caps=current_owner(n)
    try:
        before=caps.snapshot();assert caps.current() is True
        original=caps.db.execute('SELECT body FROM enrollment WHERE id=1').fetchone()[0]
        caps.db.execute('BEGIN');caps.db.execute('UPDATE enrollment SET body=? WHERE id=1',(b'{}',));caps.db.rollback()
        assert caps.db.execute('SELECT body FROM enrollment WHERE id=1').fetchone()==(original,)
        assert caps.current() is True and caps.snapshot()['debt']==before['debt']
    finally:assert caps.close()


@pytest.mark.parametrize('fault',['cache','decoder_code','policy_encoding'])
def test_representation_cache_or_decoder_substitution_refuses(native,fault):
    n=native;caps=current_owner(n)
    try:
        assert caps.current() is True
        if fault=='cache':caps._approve._binding_rows=lambda raw:None
        elif fault=='policy_encoding':caps._approve._policy_bytes=b'{}'
        else:
            fn=caps._approve._binding_rows.__wrapped__;old=fn.__code__
            def other(raw):return None
            fn.__code__=other.__code__
        try:assert caps.current() is False
        finally:
            if fault=='decoder_code':fn.__code__=old
    finally:caps.close()


def test_bounded_same_owner_original_method_comparison(native,monkeypatch):
    n=native;one_part(n,0);caps=current_owner(n)
    repo=Path(native_caps.__file__).parents[5]
    source=subprocess.check_output(['git','-C',str(repo),'show','32d9452d329039beb122320dd528529a6e8ce985:software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_native_caps.py'],text=True)
    tree=ast.parse(source);cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='_Public32Approval')
    method=next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='__call__')
    scope=dict(native_caps.__dict__);exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),'<exact-32d-original-approval-call>','exec'),scope)
    old,new=scope['__call__'],native_caps._Public32Approval.__call__;batches=[]
    try:
        for mode in ('original','candidate','candidate','original','candidate','original','original','candidate'):
            monkeypatch.setattr(native_caps._Public32Approval,'__call__',old if mode=='original' else new)
            for _ in range(5):assert caps.current() is True
            elapsed=[]
            for _ in range(40):
                start=time.perf_counter_ns();assert caps.current() is True;elapsed.append(time.perf_counter_ns()-start)
            batches.append(dict(mode=mode,nanoseconds=elapsed))
        medians={mode:statistics.median([v for row in batches if row['mode']==mode for v in row['nanoseconds']]) for mode in ('original','candidate')}
        result=dict(schema='source-native-binding-same-owner-warm-workload.v1',native_permission=False,hardware=False,
            benchmark_note='Only original/candidate approval __call__ changes; all candidate bound/source/SQL checks run in both modes. Declared model clocks/facts.',
            iterations_per_mode=160,original_median_current_ns=medians['original'],candidate_median_current_ns=medians['candidate'],
            reduction_percent=100*(medians['original']-medians['candidate'])/medians['original'],batches=batches)
        (OUT/'WARM_CURRENT_COMPARISON.json').write_text(json.dumps(result,indent=2)+'\n')
    finally:
        monkeypatch.setattr(native_caps._Public32Approval,'__call__',new);assert caps.close()
