# SPDX-License-Identifier: Apache-2.0
"""Pinned local metadata controls; synthetic pins/boot, no native authority."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import pytest
from lantern_meshticulum.bench import retained_capture as retained
from lantern_meshticulum.bench import resume_parent_guard as guard
from lantern_meshticulum.bench.history_identity_seal import capture
from lantern_meshticulum.bench.history_metadata_fence import FenceError, HistoricalMetadataFence, _stamp
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from meshticulum.bench.wire import WireError


def sha(raw): return hashlib.sha256(raw).hexdigest()
def write(path,raw): path.write_bytes(raw); path.chmod(0o600)


@pytest.fixture
def state(monkeypatch):
    base=Path(tempfile.mkdtemp(prefix='.retained-capture-control-',dir=Path(__file__).parent))
    root=base/'state'; root.mkdir(mode=0o700)
    for name in ('parents','runs'): (root/name).mkdir(mode=0o700)
    for parent in retained.PAID_HISTORY: (root/'parents'/parent).mkdir(mode=0o700)
    target=root/'parents'/retained.CAPTURE_PARENT; target.mkdir(mode=0o700)
    (target/'prepared-identities').mkdir(mode=0o700)
    strict=canonical(dict(schema='local-synthetic-strict-fixture'))
    legacy=canonical(dict(schema='local-synthetic-legacy-fixture'))
    binding=canonical(dict(schema='meshticulum.private-resume-history-binding.v2',unit='unit-c',
        parent_id=retained.CAPTURE_PARENT,strict_sha256=sha(strict),legacy_sha256=sha(legacy),
        parent_identity=list(_stamp(target.stat()))[:4]))
    phase=dict(epoch_us=retained.CAPTURE_START+1000000,monotonic_us=100000000,
        deadline_monotonic_us=100000000+retained.CAPTURE_END-retained.CAPTURE_START-1000000)
    record=dict(schema='meshticulum.private-resume-actual-capture.v1',unit='unit-c',parent_id=retained.CAPTURE_PARENT,
        history_binding_sha256=sha(binding),review_sha256=retained.CAPTURE_REVIEW,
        facts=dict(unit='unit-c',host_boot_id=retained.CAPTURE_BOOT),phase_start_epoch_us=retained.CAPTURE_START,
        phase_end_epoch_us=retained.CAPTURE_END,native_phase=phase,actual_history_verified=True,
        global_debt_complete=False,original_unknown_actual_cost=None,refunds=0,
        history=dict(previous=[dict(parent_id=parent) for parent in retained.PAID_HISTORY[:3]]))
    raws={'capture.json':canonical(record),'history-binding.json':binding,
          'history-identity-seal.json':strict,'legacy-grant-seal.json':legacy}
    for name,raw in raws.items(): write(target/name,raw)
    monkeypatch.setattr(retained,'CAPTURE_FILES',{name:sha(raw) for name,raw in raws.items()})
    monkeypatch.setattr(retained,'current_boot',lambda:retained.CAPTURE_BOOT)
    clock=lambda:(retained.CAPTURE_END+1,phase['deadline_monotonic_us']+1)
    try: yield root,target,clock
    finally: shutil.rmtree(base)


def test_exact_capture_retained_without_becoming_a_paid_parent(state):
    root,target,clock=state; before={p.name:p.read_bytes() for p in target.iterdir() if p.is_file()}
    assert retained.PAID_HISTORY[:5]==(
        '28765d19f654ce3162525afe9c3f89c5','5ad7e65b7e50901a6f63629c04a776e2',
        '4d1b5d6fe8d4b022c5cf1e21cef7f8ca','d60d8308326314a7753e651fa43a99e3',
        '4559277fa43913ce3935b078d6744a55')
    assert retained.PAID_HISTORY[5:]==(
        '4034f8979b985660df21859606c4f8a7','ba4572e95e598ad6c17fc954c3792db7',
        '1df30b47fa36b8d47328fb886528a7a9','f153a7a87a2803db85078df81a6a1aaf',
        '25a990a2d565c50b237ee45812a8c75f')
    assert len(set(retained.PAID_HISTORY))==10 and retained.CAPTURE_PARENT not in retained.PAID_HISTORY
    assert guard.HISTORY is retained.PAID_HISTORY
    assert [r['parent_id'] for r in json.loads(before['capture.json'])['history']['previous']]==list(retained.PAID_HISTORY[:3])
    assert retained.historical_parent_ids(root,'unit-c',clock,require_retained=True)==retained.PAID_HISTORY+(retained.CAPTURE_PARENT,)
    assert retained.historical_parent_ids(root,'unit-a',clock)==retained.PAID_HISTORY
    assert retained.historical_parent_ids(root,'unit-b',clock)==retained.PAID_HISTORY
    paths=retained.capture_paths(root,'unit-c')
    assert set(paths['files'])=={'parents/'+retained.CAPTURE_PARENT+'/'+name for name in retained.CAPTURE_FILES}
    assert paths['directories'][-1].endswith('/prepared-identities')
    assert before=={p.name:p.read_bytes() for p in target.iterdir() if p.is_file()}


def test_missing_optional_fixture_is_distinct_from_required_native_retention(state):
    root,target,clock=state; target.rename(root/'held-artifact')
    assert retained.historical_parent_ids(root,'unit-c',clock)==retained.PAID_HISTORY
    with pytest.raises(FenceError,match='missing_retained_capture'):
        retained.historical_parent_ids(root,'unit-c',clock,require_retained=True)


@pytest.mark.parametrize('case',['epoch','monotonic','boot','wrong_hash','symlink','hardlink','fifo','mode',
    'extra_file','prepared_child','missing_file','wrong_unit','wrong_phase','refunded','unknown_zero'])
def test_retained_artifact_refuses_mutation_or_unexpired_phase(state,monkeypatch,case):
    root,target,clock=state; observed=clock(); path=target/'capture.json'
    if case=='epoch': clock=lambda:(retained.CAPTURE_END-1,observed[1])
    elif case=='monotonic': clock=lambda:(observed[0],observed[1]-2)
    elif case=='boot': monkeypatch.setattr(retained,'current_boot',lambda:'different-boot')
    elif case=='wrong_hash': path.write_bytes(b'changed')
    elif case=='symlink': path.rename(root/'held-file'); path.symlink_to(root/'held-file')
    elif case=='hardlink': os.link(path,root/'extra-link')
    elif case=='fifo': path.unlink(); os.mkfifo(path,0o600)
    elif case=='mode': path.chmod(0o644)
    elif case=='extra_file': write(target/'native.sqlite',b'no-enrollment')
    elif case=='prepared_child': write(target/'prepared-identities'/('f'*32+'.json'),b'no-claim')
    elif case=='missing_file': path.unlink()
    else:
        value=json.loads(path.read_bytes())
        if case=='wrong_unit': value['unit']='unit-b'
        elif case=='wrong_phase': value['phase_end_epoch_us']+=1
        elif case=='refunded': value['refunds']=1
        else: value['original_unknown_actual_cost']=0
        raw=canonical(value); path.write_bytes(raw)
        monkeypatch.setattr(retained,'CAPTURE_FILES',dict(retained.CAPTURE_FILES,**{'capture.json':sha(raw)}))
    with pytest.raises((FenceError,WireError,OSError)):
        retained.historical_parent_ids(root,'unit-c',clock,require_retained=True)


def expectations(root):
    paths=retained.capture_paths(root,'unit-c'); parent='parents/'+retained.CAPTURE_PARENT
    dirs={'':['parents','runs'],'parents':list(retained.PAID_HISTORY)+[retained.CAPTURE_PARENT],'runs':[]}
    dirs.update({'parents/'+old:[] for old in retained.PAID_HISTORY})
    dirs[parent]=list(retained.CAPTURE_FILES)+['prepared-identities']; dirs[parent+'/prepared-identities']=[]
    return paths['files'],dirs


def test_strict_seal_supports_only_exact_retained_artifact_paths(state):
    root,target,clock=state; files,dirs=expectations(root)
    raw,_=capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
    new='9'*32; (root/'parents'/new).mkdir(mode=0o700)
    legacy=canonical(dict(schema='meshticulum.private-legacy-grant-fence.v1',uid=os.geteuid(),rows={'local':{}}))
    binding,_=guard.install_seals(root,new,'unit-c',raw,legacy,clock=clock)
    assert json.loads(binding)['strict_sha256']==sha(raw)
    with pytest.raises(FenceError,match='invalid_own_identity'):
        guard.install_seals(root,retained.CAPTURE_PARENT,'unit-c',raw,legacy,clock=clock)


@pytest.mark.parametrize('case',['wrong_parent','nonempty'])
def test_nested_scope_is_not_generally_relaxed(state,case):
    root,target,clock=state; files,dirs=expectations(root)
    if case=='wrong_parent': dirs['parents/'+retained.PAID_HISTORY[0]+'/prepared-identities']=[]
    else: dirs['parents/'+retained.CAPTURE_PARENT+'/prepared-identities']=['foreign']
    with pytest.raises(FenceError):
        HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
