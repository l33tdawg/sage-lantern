"""Real protected NativeCaps/SQLite; RF, clock and process facts are models."""
import ast
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from dataclasses import asdict
from types import MethodType

import pytest

from lantern_meshticulum.bench import pilot_native_caps as caps_module
from lantern_meshticulum.bench.pilot_native_caps import _open_existing
from lantern_meshticulum.bench.pilot_parent_caps import Cost
from tests.test_fast_bundle import native,fast_data,one_part
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path,protected_source,clock,facts,BOOT

OUT=Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-stage-validation-dedup-20261014')


def prepare(n,count):
    previous=None
    for index in range(count):
        n['now'][0]=tuple(v+1000000 for v in n['now'][0])
        value,_,_=one_part(n,index,previous);previous=value['fast_progress_proof']
    return _open_existing(n['roots']['unit-c'],n['policy'].parent_id,facts('unit-c'),n['clock'])


@pytest.mark.parametrize('parts',[1,5])
def test_every_sql_row_is_fresh_but_each_stage_is_read_once_per_validation(native,monkeypatch,parts):
    n=native;caps=prepare(n,parts);reader=caps._approve.reader;original=reader.read_held
    reads=[];sql=[]
    def read(path,maximum,**kwargs):
        reads.append(path);return original(path,maximum,**kwargs)
    monkeypatch.setattr(reader,'read_held',read);caps.db.set_trace_callback(sql.append)
    try:
        for iteration in range(2):
            reads.clear();sql.clear();assert caps.current() is True
            assert len(reads)==parts and len(set(reads))==parts
            assert sum(s.startswith('SELECT body FROM enrollment') for s in sql)==1
            assert sum(s.startswith('SELECT id,body,run_id FROM claims') for s in sql)==1
            assert sum(s.startswith('SELECT body FROM resources') for s in sql)==2*parts
            snapshot=caps.snapshot()
            assert snapshot['claims']==snapshot['prepared']==snapshot['starts']==2*parts
            expected=n['policy'].unit('unit-c').prior_debt.plus(Cost(512*parts,120000000*parts))
            assert snapshot['debt']==asdict(expected)
    finally:
        caps.db.set_trace_callback(None);caps.close()


@pytest.mark.parametrize('when',['resource_select','final_pread'])
@pytest.mark.parametrize('fault',['rewrite','replacement','stage_rebind'])
def test_stage_change_during_current_cannot_escape_final_fresh_checks(native,monkeypatch,when,fault):
    n=native;caps=prepare(n,1);reader=caps._approve.reader
    stage=caps.path.parent/'fast-stage-0.json';armed=[True]
    def mutate():
        if not armed[0]:return
        armed[0]=False
        if fault=='rewrite':stage.write_bytes(b'x'*stage.stat().st_size)
        elif fault=='replacement':
            other=stage.with_name('replacement');other.write_bytes(stage.read_bytes());other.chmod(0o600);other.replace(stage)
        else:
            value=json.loads(stage.read_bytes());value['expires_epoch_us']+=1
            stage.write_bytes(caps_module.canonical(value))
    if when=='resource_select':
        def traced(statement):
            if statement.startswith('SELECT body FROM resources'):mutate()
        caps.db.set_trace_callback(traced)
    else:
        fd=reader._file_index[stage][1];original=os.pread;selects=[0]
        def traced(statement):
            if statement.startswith('SELECT body FROM resources'):selects[0]+=1
        caps.db.set_trace_callback(traced)
        def pread(target,*args):
            raw=original(target,*args)
            if target==fd and selects[0]==2:mutate()
            return raw
        monkeypatch.setattr(os,'pread',pread)
    try:
        assert caps.current() is False and caps._uncertain is True
        assert not armed[0]
    finally:
        caps.db.set_trace_callback(None);caps.close()


@pytest.mark.parametrize('fault',['revoked','expired','metadata','campaign'])
def test_current_authority_and_original_bindings_are_not_cached_across_passes(native,fault):
    n=native;caps=prepare(n,1)
    try:
        assert caps.current() is True
        if fault=='revoked':Path(str(caps.path)+'.approval').write_bytes(b'0'*64+b'\n')
        elif fault=='expired':n['now'][0]=(n['policy'].expires_epoch_us,n['policy'].deadline_monotonic_us)
        elif fault=='metadata':caps._approve.bundle.value['stages'][0]['campaign_id']='f'*32
        else:caps._approve.bundle.value['stages'].append(dict(caps._approve.bundle.value['stages'][0]))
        assert caps.current() is False
    finally:caps.close()


@pytest.mark.parametrize('fault',['digest','path','campaign'])
def test_conflicting_stage_bindings_refuse_in_the_same_synchronous_pass(native,fault):
    n=native;caps=prepare(n,2);calls=[0];mutated=[False]
    def traced(statement):
        if not statement.startswith('SELECT body FROM resources'):return
        calls[0]+=1
        if mutated[0]:return
        mutated[0]=True
        stages=caps._approve.bundle.value['stages']
        if fault=='path':stages[1]['index']=stages[0]['index']
        elif fault=='campaign':stages[1]['campaign_id']=stages[0]['campaign_id']
        else:
            # Synthetic append-only rows with a second independently hashed
            # resource spelling the same stage but a conflicting digest.
            claim_id,raw,run=caps.db.execute('SELECT id,body,run_id FROM claims ORDER BY run_id LIMIT 1').fetchone()
            claim=json.loads(raw);claim['resource_id']='f'*64
            claim['resource']['fast_stage_sha256']='0'*64
            raw=caps_module.canonical(claim);new_id=caps_module.hashed(claim)
            # This is a local attack fixture only, never production recovery.
            caps.db.execute('DROP TRIGGER immutable_claims_DELETE')
            caps.db.execute('DELETE FROM claims WHERE id=?',(claim_id,))
            caps.db.execute('INSERT INTO resources VALUES(?,?)',('f'*64,caps_module.canonical(claim['resource'])))
            caps.db.execute('INSERT INTO claims VALUES(?,?,?)',(new_id,raw,run))
    if fault=='digest':
        # Make the conflicting complete raw row visible before SELECT claims;
        # the original approval checks the unchanged protected stage first.
        def digest_trace(statement):
            if statement.startswith('SELECT id,body,run_id FROM claims'):traced('SELECT body FROM resources')
        caps.db.set_trace_callback(digest_trace)
    else:caps.db.set_trace_callback(traced)
    try:
        assert caps.current() is False and mutated[0]
    finally:
        caps.db.set_trace_callback(None);caps.close()


def original_validate():
    """Load only the exact donor method for this isolated source comparison."""
    raw=subprocess.check_output(['git','show','a5e71995f756787688878cca2da7ecc541bf438a:software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_native_caps.py'])
    cls=next(n for n in ast.parse(raw).body if isinstance(n,ast.ClassDef) and n.name=='NativeCaps')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_validate')
    method.decorator_list=[]
    scope=dict(caps_module.__dict__)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])), 'source_only_original_validate','exec'),scope)
    return scope['_validate']


@pytest.mark.parametrize('parts',[1,5])
def test_same_complete_current_with_identical_owner_rows_clocks_and_files(native,parts):
    n=native;caps=prepare(n,parts);original=caps._validate;baseline=original_validate();batches=[]
    try:
        for mode in ('baseline','candidate','candidate','baseline'):
            caps._validate=MethodType(baseline,caps) if mode=='baseline' else original
            for _ in range(6):assert caps.current() is True
            times=[]
            for _ in range(40):
                begin=time.perf_counter_ns();assert caps.current() is True
                times.append((time.perf_counter_ns()-begin)/1000)
            batches.append(dict(mode=mode,elapsed_us=times))
        medians={m:statistics.median([v for row in batches if row['mode']==m for v in row['elapsed_us']])
            for m in ('baseline','candidate')}
        result=dict(source_only=True,native_measurement=False,same_owner_same_rows_same_files=True,
            part_count=parts,resources=parts,claims=2*parts,
            baseline_current_median_us=medians['baseline'],candidate_current_median_us=medians['candidate'],
            reduction_percent=100*(medians['baseline']-medians['candidate'])/medians['baseline'],batches=batches)
        (OUT/('CURRENT_'+str(parts)+'_RESOURCE_MODEL.json')).write_text(json.dumps(result,indent=2)+'\n')
    finally:
        caps._validate=original;caps.close()
