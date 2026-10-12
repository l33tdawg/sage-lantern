"""Explicit source operator entry; no scope issuance, native jobs or RF."""
import ast
from copy import deepcopy
import importlib.util
import base64,lzma
from pathlib import Path

import pytest
from tests.test_public32_pilot import tmp_path


@pytest.fixture
def operator(monkeypatch):
    bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    monkeypatch.syspath_prepend(str(bench))
    def load(name):
        spec=importlib.util.spec_from_file_location(name,bench/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    return load('run_public_gfsk125_mb1_bundle100k'),load('prepare_public_gfsk125_mb1_bundle_fixture')


def test_explicit_fresh_fixture_loads_exact_variable_transport_and_signed_whole(operator,tmp_path):
    runner,builder=operator;folder=tmp_path/'fresh';record=builder.build(folder)
    op=dict(fixture=dict(path=str(folder),sha256=runner.sha((folder/'FIXTURE.json').read_bytes())))
    piece=runner.fixture(op)
    assert piece['whole']['bytes']==100000 and piece['whole']['sha256']==record['whole_sha256']
    assert len(piece['responses'])==5 and sum(map(len,piece['responses']))==103345
    assert all(len(v)<32768 for v in piece['responses'])
    assert record['publisher_private_key_persisted_or_exported'] is False
    assert set(folder.iterdir())=={folder/'FIXTURE.json',folder/'trust.json',folder/'pack',folder/'TRANSPORT.json'}


@pytest.mark.parametrize('fault',['kind','descriptor','response_length','response_hash','legacy_schema'])
def test_rehashed_fixture_cannot_relabel_descriptor_or_wire_shape(operator,tmp_path,fault):
    runner,builder=operator;folder=tmp_path/'fresh';record=builder.build(folder)
    if fault=='kind':record['transport_kind']='legacy-json'
    elif fault=='descriptor':record['transport_descriptor_sha256']='f'*64
    elif fault=='response_length':record['parts'][0]['response_bytes']+=1
    elif fault=='response_hash':record['parts'][0]['response_sha256']='f'*64
    else:record['schema']='meshticulum.synthetic-public100k-gfsk125-fixture.v1'
    raw=runner.canonical(record);(folder/'FIXTURE.json').write_bytes(raw)
    with pytest.raises(Exception):runner.fixture(dict(fixture=dict(path=str(folder),sha256=runner.sha(raw))))


def test_first_stage_moves_only_in_a_fresh_schedule_with_all_original_bounds(operator):
    runner,_=operator;epoch=100000000;stages=runner.planned_stages(epoch)
    assert [v['issued_epoch_us']-epoch for v in stages]==[150000000,345000000,585000000,825000000,1065000000]
    assert [v['expires_epoch_us']-epoch for v in stages]==[330000000,525000000,765000000,1005000000,1245000000]
    assert stages[0]['handoff_end_epoch_us']==epoch+340000000
    assert all(v['expires_epoch_us']-v['issued_epoch_us']==180000000 for v in stages)
    assert stages[-1]['handoff_end_epoch_us']==epoch+1255000000


def test_cleanup_and_remote_timeout_logic_are_identical_to_existing_runner(operator):
    runner,_=operator;base=Path(runner.__file__).parent
    def functions(path):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.FunctionDef)}
    old=functions(base/'run_public_gfsk125_bundle100k.py');new=functions(Path(runner.__file__))
    for name in ('phase_timing_mode','wait_for_stage','require_retired','cleanup_current','native_callback_seconds'):
        assert new[name]==old[name]
    source=Path(runner.__file__).read_text()
    assert "op['phase_seconds']==1355 and op['whole_seconds']==1350 and op['application_seconds']==180" in source
    assert 'decoded_useful_bytes' in source and 'len(argv[-1].encode())<125000' in source
    old_source=(base/'run_public_gfsk125_bundle100k.py').read_text()
    marker='    finally:\n        password=None\n'
    assert source[source.rindex(marker):]==old_source[old_source.rindex(marker):]


def test_separable_rpc_builder_preserves_bounded_input_hash_and_original_timeout(operator):
    runner,_=operator;payload=dict(public_metadata=True)
    op=dict(runtime_sha256='a'*64,manifest_sha256='b'*64,native_adapter_sha256='c'*64)
    cfg=dict(units={'unit-c':dict(python='/usr/bin/python3')})
    command=runner.rpc_command(None,cfg,op,'unit-c','capture',payload,120)
    assert command[:3]==['/usr/bin/timeout','--kill-after=2s','120s']
    assert command[-3]=='unit-c' and command[-1]==runner.sha(runner.canonical(payload))
    assert lzma.decompress(base64.b64decode(command[-2],validate=True))==runner.canonical(payload)
    with pytest.raises(RuntimeError,match='batch_RPC_bound'):
        runner.rpc_command(None,cfg,op,'unit-c','capture',dict(public_metadata='x'*2000000),120)
