"""Explicit source operator entry; no scope issuance, native jobs or RF."""
import ast
from copy import deepcopy
import importlib.util
import base64,lzma
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
    final=source[source.rindex(marker):].replace('+operator.management_cleanup_jobs','')
    final=final.replace("            result['management_cleanup_commands']=operator.management_cleanup_audits\n",'')
    assert final==old_source[old_source.rindex(marker):]


def file_operator(monkeypatch,runner,payload):
    from lantern_meshticulum.bench import management_input as inputs
    state=dict(epoch=1000000000,mono=500.0);events=[]
    monkeypatch.setattr(runner.time,'time_ns',lambda:state['epoch']*1000)
    def stage(unit,action,value,**kw):
        assert value==payload;packed,csha,rsha=inputs.encode(value)
        events.append(dict(unit=unit,action=action,kwargs=kw,compressed_bytes=len(packed)))
        state['epoch']+=3000000;state['mono']+=3
        return dict(schema=inputs.SCHEMA,unit=unit,action=action,parent_id=kw['parent_id'],operation_sha256=kw['operation_sha256'],
            nonce=kw['nonce'],path='/root/'+inputs.DIRECTORY+'/rpc-'+kw['nonce']+'-'+csha+'.xz',compressed_sha256=csha,
            raw_sha256=rsha,original_end_epoch_us=kw['original_end_epoch_us'],owner_uid=0,bytes=len(packed),
            file_identity=[1,2,stat_mode,0,0,1,len(packed),1,1],directory_identity=[1,3,dir_mode,0,0])
    import stat
    stat_mode=stat.S_IFREG|0o600;dir_mode=stat.S_IFDIR|0o700
    return SimpleNamespace(clock=lambda:state['mono'],deadline=700.0,stage_management_input=stage),state,events


def test_separable_rpc_builder_uses_private_file_and_same_original_budget(operator,monkeypatch):
    runner,_=operator;payload=dict(parent_id='d'*32,public_metadata=True)
    op=dict(runtime_sha256='a'*64,manifest_sha256='b'*64,native_adapter_sha256='c'*64)
    cfg=dict(units={'unit-c':dict(python='/usr/bin/python3')},bundle_parent_id='d'*32,handoff_end_epoch_us=1200000000)
    owner,state,events=file_operator(monkeypatch,runner,payload)
    command=runner.rpc_command(owner,cfg,op,'unit-c','capture',payload,120,operation_sha256='e'*64)
    assert command[:3]==['/usr/bin/timeout','--kill-after=2s','117s']
    assert command.original_end_epoch_us==1120000000 and command.local_deadline==620.0
    assert command.staged['raw_sha256']==runner.sha(runner.canonical(payload))
    assert 'module.dispatch(action,unit,value,runtime_root=root,manifest_sha256=pin)' in command[7]
    assert command.staged['path'] in command and runner.canonical(payload).decode() not in command
    assert events[0]['kwargs']['original_end_epoch_us']==1120000000 and state['mono']==503.0


def test_large_management_metadata_exceeds_old_inline_bound_without_raising_radio_limits(operator,monkeypatch):
    import random
    runner,_=operator;payload=dict(parent_id='d'*32,metadata=base64.b64encode(random.Random(51).randbytes(180000)).decode())
    owner,state,events=file_operator(monkeypatch,runner,payload)
    op=dict(runtime_sha256='a'*64,manifest_sha256='b'*64,native_adapter_sha256='c'*64)
    cfg=dict(units={'unit-c':dict(python='/usr/bin/python3')},bundle_parent_id='d'*32,handoff_end_epoch_us=1200000000)
    command=runner.rpc_command(owner,cfg,op,'unit-c','capture',payload,120,operation_sha256='e'*64)
    assert 91000<events[0]['compressed_bytes']<=262144 and len(__import__('shlex').join(command).encode())<8000
    assert len(runner.canonical(payload))<2000000
    assert 'len(compressed)<=91000' in runner.BUNDLE_RPC_ENTRY


def test_call_nonce_and_descriptor_are_not_shared_across_prepare_calls(operator,monkeypatch):
    runner,_=operator;payload=dict(parent_id='d'*32);owner,state,events=file_operator(monkeypatch,runner,payload)
    op=dict(runtime_sha256='a'*64,manifest_sha256='b'*64,native_adapter_sha256='c'*64)
    cfg=dict(units={'unit-c':dict(python='/usr/bin/python3')},bundle_parent_id='d'*32,handoff_end_epoch_us=1200000000)
    first=runner.rpc_command(owner,cfg,op,'unit-c','guard',payload,45,operation_sha256='e'*64)
    second=runner.rpc_command(owner,cfg,op,'unit-c','guard',payload,45,operation_sha256='e'*64)
    assert first.staged['nonce']!=second.staged['nonce'] and first.staged is not second.staged
    assert first.local_deadline==545.0 and second.local_deadline==548.0


def test_cleanup_bound_uses_existing_owned_sudo_path_with_no_dispatch(operator,monkeypatch):
    runner,_=operator;payload=dict(parent_id='d'*32);owner,state,events=file_operator(monkeypatch,runner,payload)
    op=dict(runtime_sha256='a'*64,manifest_sha256='b'*64,native_adapter_sha256='c'*64)
    cfg=dict(units={'unit-c':dict(python='/usr/bin/python3')},bundle_parent_id='d'*32,handoff_end_epoch_us=1200000000)
    request=runner.rpc_command(owner,cfg,op,'unit-c','guard',payload,45,operation_sha256='e'*64);calls=[]
    def cleanup(unit,command,schema,**kw):
        calls.append((unit,command,schema,kw))
        return dict(unit=unit,nonce=request.staged['nonce'],cleaned=True,native_dispatch_called=False,already_absent=True)
    owner._command=cleanup
    state['epoch']=request.original_end_epoch_us+1
    result=runner.cleanup_rpc_input(owner,cfg,op,request.staged)
    assert result['already_absent'] and calls[0][1][2]=='5s'
    assert calls[0][3]==dict(root=True,cleanup=True,cleanup_transport_headroom=2) and 'cleanup-management-input' in calls[0][1]


@pytest.mark.parametrize('failure',['none','native','start','close','cleanup','native_cleanup'])
def test_actual_native_callback_reaps_then_cleans_file_and_preserves_primary_error(operator,monkeypatch,failure):
    runner,_=operator;payload=dict(parent_id='d'*32);owner,state,events=file_operator(monkeypatch,runner,payload)
    op=dict(runtime_sha256='a'*64,manifest_sha256='b'*64,native_adapter_sha256='c'*64)
    cfg=dict(units={'unit-c':dict(python='/usr/bin/python3')},bundle_parent_id='d'*32,handoff_end_epoch_us=1200000000)
    owner.passwords={};owner._argv=lambda unit,command,**kw:['inert-SSH',__import__('shlex').join(command)]
    lifecycle=[];owned=[];result=dict(rpc_clients=[])
    class Job:
        def __init__(self,argv,**kw):
            lifecycle.append(('start',kw['deadline']));assert kw['deadline']==620.0
            if failure=='start':raise RuntimeError('owned_start_failure')
            self.output=b'metadata-only';self.process=SimpleNamespace(returncode=124 if 'native' in failure else 0,
                poll=lambda:124 if 'native' in failure else 0)
        def wait(self):return self.process.returncode
        def close(self):
            lifecycle.append(('close',))
            if failure=='close':raise RuntimeError('owned_close_failure')
    def cleanup(unit,command,schema,**kw):
        lifecycle.append(('cleanup',));assert kw==dict(root=True,cleanup=True,cleanup_transport_headroom=2)
        if 'cleanup' in failure:raise RuntimeError('cleanup_failed')
        # The exact command passes nonce as an explicit small argument.
        nonce=command[15]
        return dict(unit=unit,nonce=nonce,cleaned=True,native_dispatch_called=False,already_absent=True)
    owner._command=cleanup
    native=next(n for n in ast.walk(ast.parse(Path(runner.__file__).read_bytes())) if isinstance(n,ast.FunctionDef) and n.name=='native')
    env=dict(operator=owner,cfg=cfg,op=op,args=SimpleNamespace(operation_sha256='e'*64),
        end=1200000000,time=runner.time,sys=__import__('sys'),owned=owned,result=result,
        native_callback_seconds=runner.native_callback_seconds,rpc_command=runner.rpc_command,Job=Job,
        need=runner.need,cleanup_rpc_input=runner.cleanup_rpc_input,
        one_record=lambda raw,schema:dict(unit='unit-c',action='capture',radio_opened=False,private_production=False))
    exec(compile(ast.Module(body=[native],type_ignores=[]),'exact-native-callback','exec'),env)
    if failure=='none':
        assert env['native']('unit-c','capture',payload)['unit']=='unit-c'
    else:
        with pytest.raises(RuntimeError) as caught:env['native']('unit-c','capture',payload)
        if failure=='native_cleanup':assert str(caught.value)=='batch_native_RPC_failed'
    assert lifecycle[-1]==('cleanup',)
    if failure!='start':assert lifecycle[-2]==('close',)
    audit=result['rpc_clients'][0]
    assert audit['management_input_cleanup_confirmed'] is ('cleanup' not in failure)
    assert audit['remote_terminal'] is (failure not in ('native','native_cleanup','start'))
