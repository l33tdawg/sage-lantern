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
        '25a990a2d565c50b237ee45812a8c75f','cb5869e6aa8de545a267007b085d4762','f6e147e70ec7ce07f673ea5e9cd2cdc2','b8f9b5d9c5479c8d363c7b7e8014bc3b','806fdca055e79dfc0b86ff8f304bcdf9','4ea23c6b8fa83366904ac62ffa98e2bf','c26cd03a8fd427daf6b8d894e4b1d04c','8dfcfdfe8f8be2c0b326520d70eab7f5','6f89e2a21919991e15224e393446928c','62bcef4f1c74532fd7ab3ada1b8b94e2','141c892d83b6064a800c11aab6444f28','05c36e09fd4056c94d341d2dd32243f4','bccb99afe6589968190b131eeaf372b3','487ae574816ff5444de03e3daa4401e4')
    assert len(set(retained.PAID_HISTORY))==23 and retained.CAPTURE_PARENT not in retained.PAID_HISTORY
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


@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def unadmitted(state, monkeypatch, request):
    """Real held filesystem; synthetic immutable pins/ownership, never native authority."""
    root, original, old_clock = state; unit=request.param
    if unit != 'unit-c': original.rename(root.parent/'original-c-only-fixture')
    target=root/'parents'/retained.UNADMITTED_PARENT; target.mkdir(mode=0o700)
    (target/'prepared-identities').mkdir(mode=0o700)
    strict=canonical(dict(schema='local-synthetic-strict-fixture',unit=unit))
    legacy=canonical(dict(schema='local-synthetic-legacy-fixture',unit=unit))
    binding=canonical(dict(schema='meshticulum.private-resume-history-binding.v2',unit=unit,
        parent_id=retained.UNADMITTED_PARENT,strict_sha256=sha(strict),legacy_sha256=sha(legacy),
        parent_identity=list(_stamp(target.stat()))[:4]))
    row=dict(retained.UNADMITTED[unit]); phase=dict(row['phase'])
    record=dict(schema='meshticulum.private-resume-actual-capture.v1',unit=unit,
        parent_id=retained.UNADMITTED_PARENT,history_binding_sha256=sha(binding),
        review_sha256=retained.UNADMITTED_REVIEW,facts=dict(unit=unit,host_boot_id=row['boot']),
        phase_start_epoch_us=retained.UNADMITTED_START,phase_end_epoch_us=retained.UNADMITTED_END,
        native_phase=phase,actual_history_verified=True,global_debt_complete=False,
        original_unknown_actual_cost=None,refunds=0,
        history=dict(previous=[dict(policy=dict(parent_id=p)) for p in retained.UNADMITTED_PAID_HISTORY]))
    raws={'capture.json':canonical(record),'history-binding.json':binding,
          'history-identity-seal.json':strict,'legacy-grant-seal.json':legacy}
    for name,raw in raws.items():write(target/name,raw)
    row['files']={name:sha(raw) for name,raw in raws.items()}
    monkeypatch.setattr(retained,'UNADMITTED',dict(retained.UNADMITTED,**{unit:row}))
    monkeypatch.setattr(retained,'current_boot',lambda:row['boot'])
    clock=lambda:(retained.UNADMITTED_END+1,phase['deadline_monotonic_us']+1)
    return root,target,unit,clock


def test_unadmitted_capture_is_expired_metadata_without_paid_debt_or_new_admission(unadmitted):
    root,target,unit,clock=unadmitted
    before={p.name:p.read_bytes() for p in target.iterdir() if p.is_file()}
    expected=retained.PAID_HISTORY+((retained.CAPTURE_PARENT,) if unit=='unit-c' else ())+(retained.UNADMITTED_PARENT,)
    assert retained.historical_parent_ids(root,unit,clock,require_retained=True,
                                         require_unadmitted=True)==expected
    record=json.loads((target/'capture.json').read_bytes())
    assert len(record['history']['previous'])==12 and len(retained.PAID_HISTORY)==23
    assert tuple(v['policy']['parent_id'] for v in record['history']['previous'])==retained.UNADMITTED_PAID_HISTORY
    assert retained.PAID_HISTORY[-1] not in retained.UNADMITTED_PAID_HISTORY
    assert not set(retained.CAPTURE_ONLY_PARENTS)&set(retained.PAID_HISTORY)
    paths=retained.capture_paths(root,unit)
    assert all('parents/'+retained.UNADMITTED_PARENT+'/'+name in paths['files'] for name in before)
    assert before=={p.name:p.read_bytes() for p in target.iterdir() if p.is_file()}
    assert not (target/'native.sqlite').exists() and not list((target/'prepared-identities').iterdir())
    dirs={'':['parents','runs'],'parents':list(expected),'runs':[]}
    dirs.update({'parents/'+paid:[] for paid in retained.PAID_HISTORY})
    for parent in expected[len(retained.PAID_HISTORY):]:
        prefix='parents/'+parent;dirs[prefix]=list(retained.CAPTURE_FILES)+['prepared-identities']
        dirs[prefix+'/prepared-identities']=[]
    raw,_=capture(root,owner_uid=os.geteuid(),expected_files=paths['files'],expected_directories=dirs)
    new='9'*32;(root/'parents'/new).mkdir(mode=0o700)
    legacy=canonical(dict(schema='meshticulum.private-legacy-grant-fence.v1',uid=os.geteuid(),rows={'local':{}}))
    guard.install_seals(root,new,unit,raw,legacy,clock=clock)
    for retained_id in retained.CAPTURE_ONLY_PARENTS:
        with pytest.raises(FenceError,match='invalid_own_identity'):
            guard.install_seals(root,retained_id,unit,raw,legacy,clock=clock)


@pytest.mark.parametrize('case',['epoch','monotonic','boot','wrong_hash','prepared_child','owner',
    'policy','prior','native_sqlite','sidecar','wrong_unit','wrong_phase','wrong_native_phase',
    'paid_history','refunded','unknown_zero','rebound'])
def test_unadmitted_capture_refuses_growth_rebinding_or_changed_original_scope(unadmitted,monkeypatch,case):
    root,target,unit,clock=unadmitted;observed=clock();path=target/'capture.json'
    if case=='epoch':clock=lambda:(retained.UNADMITTED_END-1,observed[1])
    elif case=='monotonic':clock=lambda:(observed[0],observed[1]-2)
    elif case=='boot':monkeypatch.setattr(retained,'current_boot',lambda:'changed-current-boot')
    elif case=='wrong_hash':path.write_bytes(b'changed')
    elif case=='prepared_child':write(target/'prepared-identities'/('f'*32+'.json'),b'no-claim')
    elif case in ('owner','policy','prior','native_sqlite','sidecar'):
        name={'owner':'owner.json','policy':'policy.json','prior':'prior.json',
              'native_sqlite':'native.sqlite','sidecar':'native.sqlite-wal'}[case]
        write(target/name,b'no-enrollment')
    elif case=='rebound':
        held=root/'held-unadmitted';target.rename(held);shutil.copytree(held,target)
    else:
        value=json.loads(path.read_bytes())
        if case=='wrong_unit':value['unit']='unit-b' if unit!='unit-b' else 'unit-a'
        elif case=='wrong_phase':value['phase_end_epoch_us']+=1
        elif case=='wrong_native_phase':value['native_phase']['monotonic_us']+=1
        elif case=='paid_history':value['history']['previous'].pop()
        elif case=='refunded':value['refunds']=1
        else:value['original_unknown_actual_cost']=0
        raw=canonical(value);path.write_bytes(raw)
        row=dict(retained.UNADMITTED[unit]);row['files']=dict(row['files'],**{'capture.json':sha(raw)})
        monkeypatch.setattr(retained,'UNADMITTED',dict(retained.UNADMITTED,**{unit:row}))
    with pytest.raises((FenceError,WireError,OSError)):
        retained.historical_parent_ids(root,unit,clock,require_retained=True,require_unadmitted=True)


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_native_requirement_refuses_missing_unadmitted_capture(state,unit):
    root,target,clock=state
    with pytest.raises(FenceError,match='missing_retained_capture'):
        retained.historical_parent_ids(root,unit,clock,require_retained=True,require_unadmitted=True)
