# SPDX-License-Identifier: Apache-2.0
"""Real local seal preparation; old history/OS phase modeled, no native authority."""
import os
from pathlib import Path
import shutil
import tempfile

import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.history_metadata_fence import HistoricalMetadataFence


@pytest.fixture
def prepared(monkeypatch,synthetic_a658_capture):
    base=Path(tempfile.mkdtemp(prefix='capture-seal-control-',dir=Path(__file__).parent))
    state=base/'state';state.mkdir(mode=0o700)
    for name in ('parents','runs'):(state/name).mkdir(mode=0o700)
    for parent in rpc.HISTORY:(state/'parents'/parent).mkdir(mode=0o700)
    known={}
    counts={rpc.LATEST_PAID:9,rpc.COMPLETE_PAID:10,rpc.BULK_PAID:4,rpc.MIXED_PAID:4,
            rpc.SUCCESS_PAID:2,rpc.PARTIAL_PAID:1,rpc.ACCEPTED_BULK_PAID:2,rpc.REVERSE_BULK_PAID:2,rpc.SMALL_BULK_PAID:2,rpc.ZERO_RETRY_BULK_PAID:2,rpc.IMMEDIATE_RX_BULK_PAID:2,rpc.SF7_REPLICATE_PAID:2,rpc.PUBLIC32_PAID:2,rpc.PUBLIC32_RETRY_PAID:2,rpc.PUBLIC32_SENDER_READY_PAID:2,rpc.PUBLIC32_COMPLETE_PAID:2,rpc.ENROLLED_ZERO_PAID:0,rpc.PARTIAL_BUNDLE_PAID:2,rpc.V2_PARTIAL_BUNDLE_PAID:2,rpc.V2_FAILED_BUNDLE_PAID:2,rpc.V3_PARTIAL_BUNDLE_PAID:2,rpc.V3_CHECKPOINT_BUNDLE_PAID:2,rpc.V3_VERIFIED_BUNDLE_PAID:2,rpc.SF5_STARTUP_PAID:1,rpc.SF5_COMPLETED_PAID:2,rpc.PARTIAL_BATCH_PAID:4,rpc.COMPLETED_BATCH_PAID:6,rpc.RX_PRIORITY_BATCH_PAID:6,rpc.EARLY_RX_PARTIAL_PAID:4,rpc.GFSK_COMPLETE_PAID:6,rpc.GFSK_BUNDLE_PAID:10,rpc.GFSK_PARTIAL_BUNDLE_PAID:2,rpc.MB1_PARTIAL_BUNDLE_PAID:2,rpc.MB1_COMPLETE_BUNDLE_PAID:10,rpc.GFSK250_MB1_PARTIAL_PAID:2,rpc.FAST_ZERO_PAID:0,rpc.FAST_UNSTARTED_PAID:1,rpc.fast_partial.PARENT:2,rpc.fast_complete.PARENT:10,rpc.bea_complete.PARENT:10,rpc.d1fd_history.PARENT:0}
    assert set(counts)==set(rpc.GUARDED_PAID)
    if rpc.unstarted_history.UNMATERIALIZED_PARENT in rpc.HISTORY:
        directory=state/'parents'/rpc.unstarted_history.UNMATERIALIZED_PARENT/'prepared-identities'
        directory.mkdir(mode=0o700)
        for name in rpc.unstarted_history.unmaterialized_staging_names('unit-c'):
            (state/'runs'/name).mkdir(mode=0o700)
    for parent,count in counts.items():
        directory=state/'parents'/parent/'prepared-identities';directory.mkdir(mode=0o700)
        for index in range(count):
            path=directory/('%032x.json'%(index+1));raw=(parent+':'+str(index)).encode()
            path.write_bytes(raw);path.chmod(0o600);known[str(path.relative_to(state))]=rpc.sha(raw)
    stage=state/'parents'/rpc.FAST_UNSTARTED_PAID/'fast-stage-0.json'
    stage.write_bytes(b'public stage metadata model');stage.chmod(0o600)
    known[str(stage.relative_to(state))]=rpc.sha(stage.read_bytes())
    target=state/'parents'/rpc.CAPTURE_PARENT;target.mkdir(mode=0o700)
    (target/'prepared-identities').mkdir(mode=0o700)
    for name in ('capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json'):
        path=target/name;path.write_bytes(b'public metadata');path.chmod(0o600)
        known[str(path.relative_to(state))]=rpc.sha(path.read_bytes())
    from lantern_meshticulum.bench import capture_a658 as a658
    model_clock=lambda:(a658.END+1,a658.UNITS['unit-c']['phase']['deadline_monotonic_us']+1)
    extra=synthetic_a658_capture(state,'unit-c',model_clock)
    known.update({'parents/'+a658.PARENT+'/'+name:pin for name,pin in extra['files'].items()})
    monkeypatch.setattr(rpc,'STATE',state)
    try:yield state,known
    finally:shutil.rmtree(base)


def test_complete_H4_through_H19_record_coverage_produces_real_local_strict_seal_inputs(prepared):
    state,known=prepared
    with rpc.Reader() as reader:
        files,dirs=rpc.seal_inputs(reader,'unit-c',known,{}, {})
    assert all(files[key]==pin for key,pin in known.items())
    assert len(dirs['parents/'+rpc.LATEST_PAID+'/prepared-identities'])==9
    assert len(dirs['parents/'+rpc.COMPLETE_PAID+'/prepared-identities'])==10
    assert {parent:len(dirs['parents/'+parent+'/prepared-identities']) for parent in rpc.GUARDED_PAID}=={
        rpc.LATEST_PAID:9,rpc.COMPLETE_PAID:10,rpc.BULK_PAID:4,rpc.MIXED_PAID:4,
        rpc.SUCCESS_PAID:2,rpc.PARTIAL_PAID:1,rpc.ACCEPTED_BULK_PAID:2,rpc.REVERSE_BULK_PAID:2,rpc.SMALL_BULK_PAID:2,rpc.ZERO_RETRY_BULK_PAID:2,rpc.IMMEDIATE_RX_BULK_PAID:2,rpc.SF7_REPLICATE_PAID:2,rpc.PUBLIC32_PAID:2,rpc.PUBLIC32_RETRY_PAID:2,rpc.PUBLIC32_SENDER_READY_PAID:2,rpc.PUBLIC32_COMPLETE_PAID:2,rpc.ENROLLED_ZERO_PAID:0,rpc.PARTIAL_BUNDLE_PAID:2,rpc.V2_PARTIAL_BUNDLE_PAID:2,rpc.V2_FAILED_BUNDLE_PAID:2,rpc.V3_PARTIAL_BUNDLE_PAID:2,rpc.V3_CHECKPOINT_BUNDLE_PAID:2,rpc.V3_VERIFIED_BUNDLE_PAID:2,rpc.SF5_STARTUP_PAID:1,rpc.SF5_COMPLETED_PAID:2,rpc.PARTIAL_BATCH_PAID:4,rpc.COMPLETED_BATCH_PAID:6,rpc.RX_PRIORITY_BATCH_PAID:6,rpc.EARLY_RX_PARTIAL_PAID:4,rpc.GFSK_COMPLETE_PAID:6,rpc.GFSK_BUNDLE_PAID:10,rpc.GFSK_PARTIAL_BUNDLE_PAID:2,rpc.MB1_PARTIAL_BUNDLE_PAID:2,rpc.MB1_COMPLETE_BUNDLE_PAID:10,rpc.GFSK250_MB1_PARTIAL_PAID:2,rpc.FAST_ZERO_PAID:0,rpc.FAST_UNSTARTED_PAID:1,rpc.fast_partial.PARENT:2,rpc.fast_complete.PARENT:10,rpc.bea_complete.PARENT:10,rpc.d1fd_history.PARENT:0}
    with HistoricalMetadataFence(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs) as fence:
        fence.check()


@pytest.mark.parametrize('attack',['changed','missing','unverified','secret'])
def test_H5_mutation_or_unverified_entry_refuses_before_namespace_creation(prepared,monkeypatch,attack):
    state,known=prepared;directory=state/'parents'/rpc.COMPLETE_PAID/'prepared-identities'
    path=directory/('0'*31+'1.json')
    if attack=='changed':path.write_bytes(b'changed')
    elif attack=='missing':path.unlink()
    else:
        path=directory/('secret.bin' if attack=='secret' else 'f'*32+'.json')
        path.write_bytes(b'must not be read');path.chmod(0o600)
    opened=[];original=rpc.Reader.read
    def read(reader,path,maximum,**kwargs):opened.append(path);return original(reader,path,maximum,**kwargs)
    monkeypatch.setattr(rpc.Reader,'read',read)
    monkeypatch.setattr(rpc.os,'mkdir',lambda *a,**k:pytest.fail('namespace creation reached'))
    monkeypatch.setattr(rpc,'full_history',lambda *a,**k:(known,{}, {},[]))
    monkeypatch.setattr(rpc,'phase_check',lambda *a:None)
    with pytest.raises(RuntimeError):
        rpc.capture_history('unit-c',dict(parent_id='a'*32,review_sha256='b'*64,facts={'unit':'unit-c'},
            phase_start_epoch_us=1,phase_end_epoch_us=2,history={}),type('RuntimeModel',(),{'check':lambda _:True})())
    if attack in ('unverified','secret'):assert path not in opened



@pytest.mark.parametrize('parent,fixture',[(rpc.EARLY_RX_PARTIAL_PAID,'current4ca8-partial-batch-metadata.json'),(rpc.GFSK_COMPLETE_PAID,'current6be-gfsk-completed-metadata.json')])
def test_actual_retained_capture_metadata_survives_capture_and_SealedHistory(prepared,parent,fixture):
    import json
    from lantern_meshticulum.bench.history_identity_seal import capture,SealedHistory
    state,known=prepared
    data=json.loads(Path(__file__).with_name('fixtures').joinpath(fixture).read_bytes())
    assert data['policy']['parent_id']==parent
    target=state/'parents'/parent
    # This layer checks the actual namespace, hashes and held identities.
    # Grant/capture schemas remain qualified separately by full_history.
    values={'capture.json':data['owners']['unit-c'],
        'history-binding.json':data['native']['unit-c']['prior_lineage'],
        'history-identity-seal.json':data['native']['unit-c']['files'],
        'legacy-grant-seal.json':data['native']['unit-c']['legacy_runs']}
    for name,value in values.items():
        raw=rpc.canonical(value);path=target/name;path.write_bytes(raw);path.chmod(0o600)
        known[str(path.relative_to(state))]=rpc.sha(raw)
    with rpc.Reader() as reader:files,dirs=rpc.seal_inputs(reader,'unit-c',known,{}, {})
    assert set(values)<={Path(p).name for p in files if str(target.relative_to(state))+'/' in p}
    seal,pin=capture(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
    with SealedHistory(state,owner_uid=os.geteuid(),seal_bytes=seal,expected_sha256=pin) as held:held.check()


@pytest.mark.parametrize('unknown_parent',['4ca8d86644aa24e6fefd06872a9cd132','6be212b13351cb9852ed07ba7998be97'])
def test_adjacent_unknown_parent_cannot_import_retained_capture_allowance(prepared,unknown_parent):
    from lantern_meshticulum.bench.history_metadata_fence import FenceError
    state,known=prepared
    with rpc.Reader() as reader:files,dirs=rpc.seal_inputs(reader,'unit-c',known,{}, {})
    unknown=state/'parents'/unknown_parent;unknown.mkdir(mode=0o700)
    path=unknown/'capture.json';path.write_bytes(b'public metadata');path.chmod(0o600)
    relative=str(path.relative_to(state));files[relative]=rpc.sha(path.read_bytes())
    dirs['parents']=tuple(sorted((*dirs['parents'],unknown.name)))
    dirs[str(unknown.relative_to(state))]=('capture.json',)
    with pytest.raises(FenceError,match='outside_retained_capture_metadata'):
        HistoricalMetadataFence(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)


def test_h40_stage_bytes_survive_real_seal_inputs_and_detect_mutation(prepared):
    from lantern_meshticulum.bench.history_identity_seal import capture,SealedHistory
    from lantern_meshticulum.bench.history_metadata_fence import FenceError
    state,known=prepared;relative='parents/'+rpc.FAST_UNSTARTED_PAID+'/fast-stage-0.json'
    with rpc.Reader() as reader:files,dirs=rpc.seal_inputs(reader,'unit-c',known,{}, {})
    assert files[relative]==known[relative]
    seal,pin=capture(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
    with SealedHistory(state,owner_uid=os.geteuid(),seal_bytes=seal,expected_sha256=pin) as held:
        held.check();(state/relative).write_bytes(b'changed stage')
        with pytest.raises(FenceError):held.check()

@pytest.mark.parametrize('relative',['parents/'+rpc.FAST_ZERO_PAID+'/fast-stage-0.json',
    'parents/'+rpc.FAST_UNSTARTED_PAID+'/fast-stage-1.json',
    'parents/'+rpc.FAST_UNSTARTED_PAID+'/secret.bin'])
def test_h40_stage_allowance_cannot_admit_other_file_or_parent(prepared,relative):
    from lantern_meshticulum.bench.history_metadata_fence import FenceError
    state,known=prepared
    with rpc.Reader() as reader:files,dirs=rpc.seal_inputs(reader,'unit-c',known,{}, {})
    path=state/relative;path.write_bytes(b'public model');path.chmod(0o600)
    parent,name=relative.rsplit('/',1);dirs[parent].append(name);files[relative]=rpc.sha(path.read_bytes())
    with pytest.raises(FenceError,match='prohibited_metadata_file'):
        HistoricalMetadataFence(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
