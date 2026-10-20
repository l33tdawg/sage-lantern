"""Fresh native path checks using retained lexical names, never resolved state."""
import ast
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from types import ModuleType
import threading
import time

import pytest
from lantern_meshticulum.bench import pilot_native_caps as nc
from tests.test_pilot_native_caps import state,policy,Observers,enroll,open_direct,prepared
from tests.test_fast_bundle import native,fast_data,one_part
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path,protected_source,clock,facts,BOOT

OUT=Path(__file__).resolve().parents[4]


def test_every_boundary_freshly_observes_each_ancestry_prefix_and_original_name(state,monkeypatch):
    caps,obs,p,path,root=state;seen=[];original=os.lstat
    def observed(value,*a,**kw):seen.append(os.fspath(value));return original(value,*a,**kw)
    monkeypatch.setattr(os,'lstat',observed)
    for _ in range(2):caps._available()
    counts=Counter(seen)
    for prefix in caps._lexical_paths[3]:assert counts[prefix]>=2
    for _,name in caps._lexical_paths[4]:assert counts[name]==2
    assert counts[str(path.parent)]==4
    assert caps.current() is True


@pytest.mark.parametrize('form',['dotdot','double_root','symlink_ancestor','symlink_parent'])
def test_noncanonical_names_or_symlinks_never_open_retained_native_state(state,form):
    caps,obs,p,path,root=state;assert caps.close()
    alias=root.parent/'alias';target=path
    if form=='dotdot':target=path.parent/'..'/path.parent.name/path.name
    elif form=='double_root':target=Path('/'+str(path))
    elif form=='symlink_ancestor':alias.symlink_to(root,target_is_directory=True);target=alias/'parents'/p.parent_id/path.name
    else:alias.symlink_to(path.parent,target_is_directory=True);target=alias/path.name
    try:
        with pytest.raises(nc.NativeCapsError):open_direct(target,p,obs)
    finally:
        if alias.is_symlink():alias.unlink()


@pytest.mark.parametrize('change',['ancestor_symlink','parent_symlink','parent_replaced','parent_mode','file_mode','owner_inode','approval_bytes'])
def test_fresh_path_and_held_file_checks_refuse_mutation_and_preserve_paid_rows(state,change):
    caps,obs,p,path,root=state;prepared(caps,p);before=caps.snapshot();restore=lambda:None
    parent=path.parent;held=parent.with_name(parent.name+'-held')
    try:
        if change=='ancestor_symlink':
            backup=root.with_name(root.name+'-held');root.rename(backup);root.symlink_to(backup,target_is_directory=True)
            def restore():root.unlink();backup.rename(root)
        elif change=='parent_symlink':
            parent.rename(held);parent.symlink_to(held,target_is_directory=True)
            def restore():parent.unlink();held.rename(parent)
        elif change=='parent_replaced':
            parent.rename(held);parent.mkdir(mode=0o700)
            for name in (path.name,path.name+'.owner',path.name+'.approval'):
                f=parent/name;f.write_bytes(b'replacement');f.chmod(0o600)
            def restore():shutil.rmtree(parent);held.rename(parent)
        elif change=='parent_mode':
            parent.chmod(0o750)
            def restore():parent.chmod(0o700)
        elif change=='file_mode':
            Path(str(path)+'.owner').chmod(0o640)
            def restore():Path(str(path)+'.owner').chmod(0o600)
        elif change=='owner_inode':
            target=Path(str(path)+'.owner');old=target.with_suffix('.saved');target.rename(old);target.write_bytes(b'');target.chmod(0o600)
            def restore():target.unlink();old.rename(target)
        else:
            target=Path(str(path)+'.approval');raw=target.read_bytes();target.write_bytes(b'0'*64+b'\n')
            def restore():target.write_bytes(raw)
        assert caps.current() is False
    finally:restore()
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone()==(before['claims'],)
    assert caps.db.execute('SELECT COUNT(*) FROM starts').fetchone()==(before['starts'],)


def test_ancestor_symlink_inserted_after_first_parent_observation_is_refused(state,monkeypatch):
    caps,obs,p,path,root=state;original=os.lstat;backup=root.with_name(root.name+'-held');changed=[]
    def racing(value,*a,**kw):
        result=original(value,*a,**kw)
        if os.fspath(value)==str(path.parent) and not changed:
            root.rename(backup);root.symlink_to(backup,target_is_directory=True);changed.append(True)
        return result
    monkeypatch.setattr(os,'lstat',racing)
    try:assert caps.current() is False and changed==[True]
    finally:
        if changed:root.unlink();backup.rename(root)


def test_clock_callback_cannot_reuse_an_earlier_successful_ancestry_observation(state):
    caps,obs,p,path,root=state;backup=root.with_name(root.name+'-held');changed=[]
    def racing(count):
        if not changed:
            root.rename(backup);root.symlink_to(backup,target_is_directory=True);changed.append(True)
    obs.hook=racing
    try:assert caps.current() is False and changed==[True]
    finally:
        obs.hook=None
        if changed:root.unlink();backup.rename(root)


@pytest.mark.parametrize('change',['new_path','lexical_tuple','text_representation','parts_representation'])
def test_path_or_lexical_metadata_substitution_refuses_before_retargeting(state,change):
    caps,obs,p,path,root=state;restore=lambda:None
    if change=='new_path':
        old=caps.path;caps.path=Path(str(path))
        def restore():caps.path=old
    elif change=='lexical_tuple':
        names=caps._lexical_paths;caps._lexical_paths=names[:3]+((),)+names[4:]
    elif change=='text_representation':
        old=caps.path._str;caps.path._str=old+'-rebound'
        def restore():caps.path._str=old
    else:
        # Public Path.parts is checked as well as its cached display string.
        key='_tail_cached' if hasattr(caps.path,'_tail_cached') else '_parts'
        old=getattr(caps.path,key);setattr(caps.path,key,old[:-1]+['other.sqlite'])
        def restore():setattr(caps.path,key,old)
    try:assert caps.current() is False
    finally:restore()


@pytest.mark.parametrize('observer',['pid','thread'])
def test_wrong_process_or_thread_does_not_cleanup_real_owner(state,monkeypatch,observer):
    caps,obs,p,path,root=state
    with monkeypatch.context() as patch:
        if observer=='pid':
            pid=os.getpid();patch.setattr(os,'getpid',lambda:pid+1)
        else:
            ident=threading.get_ident();patch.setattr(threading,'get_ident',lambda:ident+1)
        assert caps.current() is False and caps.close() is False
    assert not caps._closed and caps.current() is True


@pytest.fixture
def protected_native(native):return native


def test_consistent_lexical_and_state_reference_substitution_still_refuses(state):
    caps,obs,p,path,root=state;prepared(caps,p)
    names=caps._lexical_paths;caps._lexical_paths=names[:3]+((),)+names[4:]
    caps._policy_state=nc._state((caps.policy,caps._lexical_paths))
    refs=list(caps._refs);refs[14]=caps._policy_state;caps._refs=tuple(refs)
    assert caps.current() is False
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone()==(1,)
    assert caps.db.execute('SELECT COUNT(*) FROM starts').fetchone()==(0,)


def test_same_owner_lexical_names_warm_workload(native,monkeypatch):
    n=native;one_part(n,0);repo=Path(nc.__file__).parents[5]
    # The fixture maps __file__ to its held Runtime. Git is selected explicitly.
    repo=OUT/'sage-lantern'
    source=subprocess.check_output(['git','-C',str(repo),'show','32d9452d329039beb122320dd528529a6e8ce985:software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_native_caps.py'])
    name='lantern_meshticulum.bench._exact32d_caps_source_control'
    original=ModuleType(name);original.__package__='lantern_meshticulum.bench';original.__file__=nc.__file__
    monkeypatch.setitem(sys.modules,name,original)
    exec(compile(source,'<exact32d-native-caps-source-control>','exec'),original.__dict__)
    assert original.NativeCaps is not nc.NativeCaps
    batches=[];opened=[]
    for mode in ('original','candidate','candidate','original','candidate','original','original','candidate'):
        module=original if mode=='original' else nc
        caps=module._open_existing(n['roots']['unit-c'],n['policy'].parent_id,facts('unit-c'),n['clock'])
        try:
            assert type(caps) is module.NativeCaps
            for _ in range(8):assert caps.current() is True
            elapsed=[]
            for _ in range(60):
                start=time.perf_counter_ns();assert caps.current() is True;elapsed.append(time.perf_counter_ns()-start)
            batches.append(dict(mode=mode,nanoseconds=elapsed))
            opened.append(dict(mode=mode,claims=caps.snapshot()['claims']))
        finally:assert caps.close()
    medians={mode:statistics.median([v for row in batches if row['mode']==mode for v in row['nanoseconds']]) for mode in ('original','candidate')}
    result=dict(schema='source-native-lexical-path-exact-class-warm-workload.v1',native_permission=False,hardware=False,
        benchmark_note='Exact unmodified32d module source/class versus candidate class. Owners alternate, closing before next open, on identical protected files/SQLite and declared model clocks/facts. Constructor cost excluded; all warm NativeCaps.current guards included.',
        original_source_sha256=__import__('hashlib').sha256(source).hexdigest(),
        original_class_module=name,candidate_class_module=nc.__name__,iterations_per_mode=240,
        original_median_current_ns=medians['original'],candidate_median_current_ns=medians['candidate'],
        reduction_percent=100*(medians['original']-medians['candidate'])/medians['original'],batches=batches,opened=opened)
    (OUT/'WARM_PATH_COMPARISON_EXACT.json').write_text(json.dumps(result,indent=2)+'\n')
