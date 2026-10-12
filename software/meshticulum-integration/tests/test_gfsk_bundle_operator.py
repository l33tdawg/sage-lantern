"""Portable whole-file source composition, with no remote or radio calls."""
from copy import deepcopy
import base64
import hashlib
import importlib.util
import json
import lzma
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.test_public32_pilot import tmp_path


@pytest.fixture
def operator(monkeypatch):
    bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    monkeypatch.syspath_prepend(str(bench))
    def load(name):
        spec=importlib.util.spec_from_file_location(name,bench/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    return load('run_public_gfsk125_bundle100k'),load('prepare_public_gfsk125_bundle_fixture')


def test_fresh_signed_whole_fixture_runs_through_portable_loader(operator,tmp_path):
    runner,builder=operator;folder=tmp_path/'fresh';record=builder.build(folder)
    raw=(folder/'FIXTURE.json').read_bytes();op=dict(fixture=dict(path=str(folder),sha256=runner.sha(raw)))
    actual=runner.fixture(op)
    assert actual['whole']['bytes']==100000 and actual['whole']['sha256']==record['whole_sha256']
    assert [p['raw_bytes'] for p in actual['whole']['parts']]==[23900,23900,23900,23900,4400]
    assert len(actual['responses'])==5 and all(len(v)==32768 for v in actual['responses'])
    assert sum(p['decoded_bytes'] for p in record['parts'])==100000
    assert record['publisher_private_key_persisted_or_exported'] is False
    assert set(folder.iterdir())=={folder/'FIXTURE.json',folder/'trust.json',folder/'pack'}


@pytest.mark.parametrize('field,changed',[
    ('whole_sha256','0'*64),('whole_bytes',99999),('private_data',True),
    ('model_or_evaluation_content',True),('signature_sha256','0'*64),('trust_sha256','0'*64)])
def test_rehashed_fixture_cannot_change_signed_whole_or_publisher_metadata(operator,tmp_path,field,changed):
    runner,builder=operator;folder=tmp_path/'fresh';record=builder.build(folder)
    record[field]=changed;raw=runner.canonical(record);(folder/'FIXTURE.json').write_bytes(raw)
    with pytest.raises(RuntimeError):runner.fixture(dict(fixture=dict(path=str(folder),sha256=runner.sha(raw))))


def test_original_stage_schedule_waits_even_when_previous_part_finishes_early(operator):
    runner,_=operator;epoch=100000000;stages=runner.planned_stages(epoch)
    assert [v['issued_epoch_us']-epoch for v in stages]==[105000000,345000000,585000000,825000000,1065000000]
    assert all(v['expires_epoch_us']-v['issued_epoch_us']==180000000 for v in stages)
    assert stages[-1]['handoff_end_epoch_us']==epoch+1255000000
    now=[stages[1]['issued_epoch_us']-250000];checks=[];sleeps=[];before=deepcopy(stages)
    current=SimpleNamespace(current=lambda:checks.append(now[0]))
    def sleep(seconds):sleeps.append(seconds);now[0]+=round(seconds*1000000)
    runner.wait_for_stage(current,stages[1]['issued_epoch_us'],lambda:now[0],sleep)
    assert sleeps==[.1,.1,.05] and checks[-1]==stages[1]['issued_epoch_us'] and stages==before


def test_original_whole_expiry_aborts_stage_wait_without_new_deadline(operator):
    runner,_=operator;sleeps=[]
    def expired():raise RuntimeError('whole_bundle_expired')
    with pytest.raises(RuntimeError,match='whole_bundle_expired'):
        runner.wait_for_stage(SimpleNamespace(current=expired),200,lambda:100,sleeps.append)
    assert not sleeps


def cleanup_fixture():
    links=[dict(run_id=str(i),attempted=['unit-a','unit-c'],prepared=['unit-a','unit-c'],
                retired=['unit-a','unit-c'],phase='complete') for i in range(10)]
    rows=[dict(unit=u,run_id=link['run_id'],retirement=dict(unit=u,run_id=link['run_id'],retired=True))
          for link in links for u in ('unit-a','unit-c')]
    return links,rows


def test_all_twenty_endpoint_receipts_are_required_and_prior_cleanup_errors_remain_fatal(operator):
    runner,_=operator;links,rows=cleanup_fixture()
    runner.require_retired(links,rows,completed=True)
    for fault in ('missing','mismatched','earlier_error','prepared_missing','not_complete'):
        bad_links=deepcopy(links);bad=deepcopy(rows)
        if fault=='missing':bad.pop()
        elif fault=='mismatched':bad[0]['retirement']['unit']='unit-b'
        elif fault=='earlier_error':bad.insert(0,dict(unit='unit-a',run_id='0',retirement_error='RuntimeError'))
        elif fault=='prepared_missing':bad_links[0]['prepared']=[]
        else:bad_links[0]['phase']='failed'
        with pytest.raises(RuntimeError):runner.require_retired(bad_links,bad,completed=True)
        result=dict(complete=True)
        runner.cleanup_current(SimpleNamespace(links=bad_links,retire=lambda:bad),result,completed=True)
        assert result['complete'] is False and result['cleanup_error']=='current_controller_cleanup_unproved'


def test_capture_timeout_is_longer_but_never_renews_original_phase(operator):
    runner,_=operator;end=1000000000
    assert runner.native_callback_seconds('capture',end,end-300000000)==120
    assert runner.native_callback_seconds('audit',end,end-300000000)==45
    assert runner.native_callback_seconds('capture',end,end-20000000)==12
    with pytest.raises(RuntimeError):runner.native_callback_seconds('capture',end,end-8000000)


def test_whole_file_entry_only_extends_compressed_transport_envelope(operator):
    runner,_=operator
    assert "need(len(blob)<=112000,'RPC_encoded_bound')" in runner.RPC_ENTRY
    assert "need(len(compressed)<=84000,'RPC_compressed_bound')" in runner.RPC_ENTRY
    assert runner.BUNDLE_RPC_ENTRY.replace('121336','112000').replace('91000','84000')==runner.RPC_ENTRY
    assert 'max_length=2000001' in runner.BUNDLE_RPC_ENTRY and 'memlimit=33554432' in runner.BUNDLE_RPC_ENTRY
    compile(runner.BUNDLE_RPC_ENTRY,'bounded-gfsk-bundle-rpc','exec')


def test_exact_bundle_decoder_refuses_oversize_wrong_hash_and_trailing_data(operator):
    runner,_=operator
    entry=runner.BUNDLE_RPC_ENTRY
    decoder=entry[entry.index('need(len(blob)<=121336'):entry.index('sys.path.insert(0,str(root))')]
    raw=b'{"public_metadata":true}';compressed=lzma.compress(raw)
    def decode(blob,expected=raw,pin=None):
        env=dict(base64=base64,lzma=lzma,hashlib=hashlib,need=runner.need,
            blob=base64.b64encode(blob).decode(),input_sha=pin or runner.sha(expected))
        exec(compile(decoder,runner.__file__,'exec'),env);return env['raw']
    assert decode(compressed)==raw
    with pytest.raises(RuntimeError,match='RPC_encoded_bound'):decode(bytes(91003))
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):decode(compressed,pin='f'*64)
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):decode(compressed+b'\0')
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):decode(compressed[:-1])
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):
        decode(lzma.compress(b'x'*2000001),expected=b'x'*2000001)


@pytest.mark.parametrize('fault',['schema','parts','phase','whole','app','authorization'])
def test_operator_rejects_wrong_scope_before_source_or_dispatch(operator,tmp_path,fault,monkeypatch):
    runner,_=operator
    value=dict(schema=runner.SCHEMA,runner_sha256=runner.sha(runner.read(Path(runner.__file__),100000)),
        phase_seconds=1355,whole_seconds=1350,application_seconds=180,part_count=5,
        authorization=dict(human_statement='run it bro',count_authorized=1,public_only=True,private_production=False))
    if fault=='schema':value['schema']='meshticulum.public-gfsk125-three-sample-operation.v1'
    elif fault=='parts':value['part_count']=6
    elif fault=='phase':value['phase_seconds']=1356
    elif fault=='whole':value['whole_seconds']=1351
    elif fault=='app':value['application_seconds']=181
    else:value['authorization']['private_production']=True
    raw=runner.canonical(value);path=tmp_path/'operation.json';path.write_bytes(raw)
    monkeypatch.setattr(runner.subprocess,'check_output',lambda *a,**k:pytest.fail('scope refusal must precede source or dispatch'))
    with pytest.raises(RuntimeError):runner.review(path,runner.sha(raw))
