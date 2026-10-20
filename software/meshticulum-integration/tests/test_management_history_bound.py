"""Exact saved H41 bytes and genuine local parsers/guards; no device authority."""
import ast
import json
import lzma
import os
from pathlib import Path

import pytest

from lantern_meshticulum.bench import management_input as inputs,pilot_resume_rpc as rpc,retained_capture as retained
from lantern_meshticulum.bench.history_metadata_fence import FenceError
from lantern_meshticulum.bench.pilot_bundle import public_context_commitment
from lantern_meshticulum.bench.pilot_native_caps import _open_existing
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests.test_fast_bundle import native,fast_data
from tests.test_fast_management_guard import management_guard,public_namespace_precedes_historical_capture
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path,protected_source,clock,facts
from tests.test_retained_capture import state as retained_state

RECEIPT_SHA='cecd4d2ac76daa094e4234d4bfbc15b0c23acbd01a52da5a4d3ea15e62c1f2bc'


def actual_h41():
    value=os.environ.get('MESHTICULUM_HISTORY41_FIXTURE')
    if not value:pytest.skip('exact saved H41 evidence is an optional external fixture')
    history=Path(value)
    raw=(history/'RECEIPT.json').read_bytes();assert inputs.sha(raw)==RECEIPT_SHA
    receipt=json.loads(raw);raw=(history/'unit-c.json').read_bytes()
    assert len(raw)==2029287 and inputs.sha(raw)==receipt['payload_sha256']['unit-c']
    return json.loads(raw)


def test_exact_h41_history_and_original_context_pass_management_and_capture_commitment(native):
    history=actual_h41();n=native;c=n['context'];p=n['policy']
    # The context's fixed source-control clock is never issued to hardware.
    payload=dict(parent_id=p.parent_id,history=history,facts=facts('unit-c'),
        phase_start_epoch_us=c.value['original_epoch_us'],phase_end_epoch_us=c.value['original_epoch_us']+1355000000,
        review_sha256='a'*64,qualification_kind=p.qualification_kind,bundle_context=c.value,
        bundle_capture_commitment=public_context_commitment(c.value,parent_id=p.parent_id))
    raw=inputs.canonical(payload);assert 2000000<len(raw)<=inputs.RAW_MAX==2750000
    compressed,csha,rsha=inputs.encode(payload)
    assert inputs.decode(compressed,csha,rsha)==payload
    assert rpc.capture_request_commitment(payload)==payload['bundle_capture_commitment']
    assert inputs.sha(inputs.canonical(inputs.decode(compressed,csha,rsha)['history']))==inputs.sha(inputs.canonical(history))


def test_genuine_protected_approval_and_management_guard_reopen_large_capture(native,protected_source):
    n=native;unit='unit-c';directory=n['roots'][unit]/'parents'/n['policy'].parent_id
    captured=json.loads((directory/'capture.json').read_bytes())
    # Extra historical bytes are a source-control fixture, not an admitted H41
    # snapshot. Genuine capture hash/source/owner/clock checks stay unchanged.
    captured['bound_control_h41']=actual_h41();raw=canonical(captured)
    assert 2000000<len(raw)<=inputs.RAW_MAX
    (directory/'capture.json').unlink();rpc.write_new(directory,'capture.json',raw)
    owner=json.loads((directory/'owner.json').read_bytes());owner['capture_record_sha256']=rpc.sha(raw)
    (directory/'owner.json').write_bytes(canonical(owner))
    caps=_open_existing(n['roots'][unit],n['policy'].parent_id,facts(unit),n['clock'])
    try:assert caps.current() is True
    finally:assert caps.close()
    runtime_root,manifest_pin=protected_source
    with rpc.Runtime(runtime_root,manifest_pin) as runtime,management_guard(n,unit,{},runtime) as guard:
        assert guard.check()['claims']==0


@pytest.mark.parametrize('name',['capture.json','history-identity-seal.json'])
def test_retained_capture_observes_large_history_but_other_metadata_keeps_2m_bound(retained_state,monkeypatch,name):
    root,target,clock=retained_state;path=target/name
    if name=='capture.json':
        record=json.loads(path.read_bytes());record['history']=actual_h41();raw=canonical(record)
    else:raw=b'x'*2000001
    assert 2000000<len(raw)<=inputs.RAW_MAX
    path.write_bytes(raw);pins=dict(retained.CAPTURE_FILES,**{name:rpc.sha(raw)})
    monkeypatch.setattr(retained,'CAPTURE_FILES',pins)
    def observe():
        return retained._artifact_present(root,'unit-c',clock,retained.CAPTURE_PARENT,pins,
            retained.CAPTURE_BOOT,retained.CAPTURE_START,retained.CAPTURE_END,retained.CAPTURE_REVIEW,True)
    if name!='capture.json':
        with pytest.raises(FenceError,match='retained_capture_size_bound'):observe()
        return
    assert observe() is True and path.read_bytes()==raw
    path.write_bytes(b'x'*(inputs.RAW_MAX+1))
    with pytest.raises(FenceError,match='retained_capture_size_bound'):observe()


@pytest.mark.parametrize('name',['capture.json','owner.json'])
def test_only_capture_file_write_gets_larger_bound(tmp_path,name):
    target=tmp_path/'metadata';target.mkdir(mode=0o700)
    raw=b'x'*2000001
    if name=='capture.json':
        rpc.write_new(target,name,raw)
        with rpc.Reader() as reader:assert reader.read(target/name,inputs.RAW_MAX)==raw
    else:
        with pytest.raises(RuntimeError,match='RPC_write_bound'):rpc.write_new(target,name,raw)
        assert not (target/name).exists()
    with pytest.raises(RuntimeError,match='RPC_write_bound'):rpc.write_new(target,'capture.json',b'x'*(inputs.RAW_MAX+1))


def test_raw_bound_refuses_larger_valid_json_and_decoder_does_not_allocate_unbounded_output():
    value=dict(history='x'*(inputs.RAW_MAX+1))
    with pytest.raises(RuntimeError,match='raw_bound'):inputs.encode(value)
    raw=inputs.canonical(value);packed=lzma.compress(raw,preset=6)
    with pytest.raises(RuntimeError,match='raw_pin'):inputs.decode(packed,inputs.sha(packed),inputs.sha(raw))
    assert inputs.COMPRESSED_MAX==262144 and inputs.DECODER_MEMORY_MAX==33554432


def test_runtime_image_input_and_existing_argv_contracts_keep_their_original_limits():
    bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    source=(bench/'run_public_gfsk250_mb1_fast_bundle100k.py').read_text()
    assert "archive=read(op['runtime_archive'],2000000)" in source
    # Physical container overhead is tested with actual archives in
    # test_runtime_container_budget; image/input limits remain separate.
    assert "expanded<=2250000,'batch_image_expansion_bound'" in source
    tree=ast.parse((bench/'run_public_sf5_c_bulk_8k.py').read_bytes())
    entry=ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='RPC_ENTRY' for t in n.targets)))
    assert 'max_length=2000001' in entry and 'len(raw)<=2000000' in entry
    assert rpc.MAX_FILE==16*1024*1024
