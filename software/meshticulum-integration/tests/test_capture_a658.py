"""Actual proof pins and held synthetic files; no native grants, debt or RF."""
from copy import deepcopy
import hashlib,json,os,shutil,tempfile
from pathlib import Path

import pytest
from lantern_meshticulum.bench import capture_a658 as pinned,retained_capture as retained,capture_8120
from lantern_meshticulum.bench.history_metadata_fence import FenceError,HistoricalMetadataFence
from lantern_meshticulum.bench.history_identity_seal import capture,SealedHistory
from lantern_meshticulum.bench.pilot_parent_caps import canonical

@pytest.fixture(params=['unit-b','unit-c'])
def sample(request,synthetic_a658_capture):
    base=Path(tempfile.mkdtemp(prefix='.a658-capture-',dir=Path(__file__).parent));state=base/'state';state.mkdir(mode=0o700)
    for name in ('parents','runs'):(state/name).mkdir(mode=0o700)
    for parent in retained.PAID_HISTORY:(state/'parents'/parent).mkdir(mode=0o700)
    unit=request.param;now=(pinned.END+1,pinned.UNITS[unit]['phase']['deadline_monotonic_us']+1)
    clock=lambda:now
    row=synthetic_a658_capture(state,unit,clock)
    try:yield state,unit,row,clock
    finally:shutil.rmtree(base)

def test_actual_scope_retains_captures_without_a_paid_scope():
    assert len(retained.PAID_HISTORY)==42 and retained.PAID_HISTORY[34]=='73c1cda8c02939409d0b1e7e2a39be67' and retained.PAID_HISTORY[35]=='3195cff3fc8fc8ee0b81694fe8f9e389'
    assert pinned.PARENT not in retained.PAID_HISTORY
    assert pinned.EXPIRY_COMPLETION_SHA256=='ac43ad90fdb5aab5c1e544ee5c8063bef76ce390af4e0dfb8555b65167ccf295'
    assert pinned.H35_RECEIPT_SHA256=='37d961c7e5078fb98a5c4e3e4b31b7aac51d0afa1433966a56b47c00d2de36ca'
    assert pinned.row('unit-a') is None
    assert [len(retained.expected_capture_only_parents(u)) for u in ('unit-a','unit-b','unit-c')]==[2,3,4]
    for unit in ('unit-b','unit-c'):
        row=pinned.row(unit)
        assert row['phase']['deadline_monotonic_us']==row['phase']['monotonic_us']+pinned.END-row['phase']['epoch_us']
        assert len(row['files'])==4


def test_exact_held_capture_enters_the_seal_and_keeps_unknown_cost(sample):
    state,unit,row,clock=sample;target=state/'parents'/pinned.PARENT
    before={name:(target/name).read_bytes() for name in row['files']}
    assert retained.historical_parent_ids(state,unit,clock)==retained.PAID_HISTORY+(pinned.PARENT,retained.CAPTURE8120_PARENT)
    paths=retained.capture_paths(state,unit)
    # The optional older C artifact is not present in this dedicated local fixture.
    files={path:pin for path,pin in paths['files'].items()
        if path.split('/')[1] in (pinned.PARENT,capture_8120.PARENT)}
    directories={'':['parents','runs'],'parents':list(retained.PAID_HISTORY)+[pinned.PARENT,capture_8120.PARENT],'runs':[]}
    directories.update({'parents/'+p:[] for p in retained.PAID_HISTORY})
    directories['parents/'+pinned.PARENT]=list(row['files'])+['prepared-identities'];directories['parents/'+pinned.PARENT+'/prepared-identities']=[]
    prefix='parents/'+capture_8120.PARENT
    directories[prefix]=list(capture_8120.row(unit)['files'])+['prepared-identities'];directories[prefix+'/prepared-identities']=[]
    raw,pin=capture(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)
    with SealedHistory(state,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin) as held:held.check()
    assert before=={name:(target/name).read_bytes() for name in row['files']}
    record=json.loads(before['capture.json'])
    assert record['refunds']==0 and record['global_debt_complete'] is False and record['original_unknown_actual_cost'] is None
    assert len(record['history']['previous'])==35 and not (target/'native.sqlite').exists()


@pytest.mark.parametrize('case',['missing','symlink','extra','prepared','schema','history','epoch','monotonic','phase','boot','refund','pending'])
def test_capture_growth_rebinding_or_unexpired_original_clocks_refuse(sample,monkeypatch,case):
    state,unit,row,clock=sample;target=state/'parents'/pinned.PARENT;path=target/'capture.json';now=clock()
    if case=='missing':shutil.rmtree(target)
    elif case=='symlink':path.rename(state/'held');path.symlink_to(state/'held')
    elif case=='extra':(target/'native.sqlite').write_bytes(b'no-enrollment')
    elif case=='prepared':(target/'prepared-identities'/('a'*32+'.json')).write_bytes(b'no-claim')
    elif case=='epoch':clock=lambda:(pinned.END-1,now[1])
    elif case=='monotonic':clock=lambda:(now[0],row['phase']['deadline_monotonic_us']-1)
    elif case=='boot':monkeypatch.setattr(retained,'current_boot',lambda:'changed')
    elif case=='pending':monkeypatch.setattr(pinned,'EXPIRY_COMPLETION_SHA256',None)
    else:
        value=json.loads(path.read_bytes())
        if case=='schema':value['schema']='meshticulum.private-resume-actual-capture.v1'
        elif case=='history':value['history']['previous'].pop()
        elif case=='phase':value['native_phase']['epoch_us']+=1
        elif case=='refund':value['refunds']=1
        raw=canonical(value);path.write_bytes(raw)
        changed=deepcopy(row);changed['files']['capture.json']=hashlib.sha256(raw).hexdigest()
        monkeypatch.setattr(pinned,'UNITS',dict(pinned.UNITS,**{unit:changed}))
    with pytest.raises((FenceError,OSError)):
        retained.historical_parent_ids(state,unit,clock)


@pytest.mark.parametrize('case',['directory','dangling_symlink'])
def test_unreached_A_capture_must_be_absent(tmp_path,case):
    state=tmp_path/'state';(state/'parents').mkdir(parents=True)
    target=state/'parents'/pinned.PARENT
    if case=='directory':target.mkdir()
    else:target.symlink_to(state/'missing')
    with pytest.raises(FenceError,match='unreached_A_capture_present'):
        retained.historical_parent_ids(state,'unit-a',lambda:(pinned.END+1,1))


def test_adjacent_capture_parent_cannot_import_the_allowlist(sample):
    state,unit,row,clock=sample;wrong='a658ae21c653c6f5cadfaa1b28b57499';target=state/'parents'/wrong;target.mkdir(mode=0o700)
    (target/'capture.json').write_bytes(b'{}');(target/'capture.json').chmod(0o600)
    files={'parents/'+wrong+'/capture.json':hashlib.sha256(b'{}').hexdigest()}
    directories={'':['parents','runs'],'parents':list(retained.PAID_HISTORY)+[pinned.PARENT,wrong],'runs':[]}
    directories.update({'parents/'+p:[] for p in retained.PAID_HISTORY})
    directories['parents/'+pinned.PARENT]=list(row['files'])+['prepared-identities'];directories['parents/'+pinned.PARENT+'/prepared-identities']=[]
    directories['parents/'+wrong]=['capture.json']
    with pytest.raises(FenceError,match='outside_retained_capture_metadata'):
        HistoricalMetadataFence(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)
