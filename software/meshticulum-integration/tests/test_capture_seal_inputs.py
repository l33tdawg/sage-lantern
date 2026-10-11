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
def prepared(monkeypatch):
    base=Path(tempfile.mkdtemp(prefix='capture-seal-control-',dir=Path(__file__).parent))
    state=base/'state';state.mkdir(mode=0o700)
    for name in ('parents','runs'):(state/name).mkdir(mode=0o700)
    for parent in rpc.HISTORY:(state/'parents'/parent).mkdir(mode=0o700)
    known={}
    counts={rpc.LATEST_PAID:9,rpc.COMPLETE_PAID:10,rpc.BULK_PAID:4,rpc.MIXED_PAID:4,
            rpc.SUCCESS_PAID:2,rpc.PARTIAL_PAID:1,rpc.ACCEPTED_BULK_PAID:2,rpc.REVERSE_BULK_PAID:2,rpc.SMALL_BULK_PAID:2,rpc.ZERO_RETRY_BULK_PAID:2,rpc.IMMEDIATE_RX_BULK_PAID:2,rpc.SF7_REPLICATE_PAID:2,rpc.PUBLIC32_PAID:2,rpc.PUBLIC32_RETRY_PAID:2,rpc.PUBLIC32_SENDER_READY_PAID:2,rpc.PUBLIC32_COMPLETE_PAID:2,rpc.ENROLLED_ZERO_PAID:0,rpc.PARTIAL_BUNDLE_PAID:2,rpc.V2_PARTIAL_BUNDLE_PAID:2,rpc.V2_FAILED_BUNDLE_PAID:2,rpc.V3_PARTIAL_BUNDLE_PAID:2}
    assert set(counts)==set(rpc.GUARDED_PAID)
    for parent,count in counts.items():
        directory=state/'parents'/parent/'prepared-identities';directory.mkdir(mode=0o700)
        for index in range(count):
            path=directory/('%032x.json'%(index+1));raw=(parent+':'+str(index)).encode()
            path.write_bytes(raw);path.chmod(0o600);known[str(path.relative_to(state))]=rpc.sha(raw)
    target=state/'parents'/rpc.CAPTURE_PARENT;target.mkdir(mode=0o700)
    (target/'prepared-identities').mkdir(mode=0o700)
    for name in ('capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json'):
        path=target/name;path.write_bytes(b'public metadata');path.chmod(0o600)
        known[str(path.relative_to(state))]=rpc.sha(path.read_bytes())
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
        rpc.SUCCESS_PAID:2,rpc.PARTIAL_PAID:1,rpc.ACCEPTED_BULK_PAID:2,rpc.REVERSE_BULK_PAID:2,rpc.SMALL_BULK_PAID:2,rpc.ZERO_RETRY_BULK_PAID:2,rpc.IMMEDIATE_RX_BULK_PAID:2,rpc.SF7_REPLICATE_PAID:2,rpc.PUBLIC32_PAID:2,rpc.PUBLIC32_RETRY_PAID:2,rpc.PUBLIC32_SENDER_READY_PAID:2,rpc.PUBLIC32_COMPLETE_PAID:2,rpc.ENROLLED_ZERO_PAID:0,rpc.PARTIAL_BUNDLE_PAID:2,rpc.V2_PARTIAL_BUNDLE_PAID:2,rpc.V2_FAILED_BUNDLE_PAID:2,rpc.V3_PARTIAL_BUNDLE_PAID:2}
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
