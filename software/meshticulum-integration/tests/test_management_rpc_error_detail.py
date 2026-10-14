"""Pure failure telemetry; no SSH, protected observations or native dispatch."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

BENCH = Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
OLD_ERROR_HANDLER = """kind=type(error).__name__
print(json.dumps(dict(schema='meshticulum.management-file-RPC-error.v1',
 error_type=kind if re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',kind) else 'Exception',
 reason='management_file_RPC_refused')),flush=True)
raise SystemExit(1) from None
"""


@pytest.fixture
def adapter():
    spec = importlib.util.spec_from_file_location('file_rpc_error_controls',BENCH/'management_file_rpc.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner():
    source = (BENCH/'run_public_gfsk250_mb1_fast_bundle100k.py').read_bytes()
    names = {'rpc_error_detail','rpc_failure_diagnostic','error_context_chain'}
    nodes = [n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == names
    namespace = dict(re=re,json=json,sha=lambda raw:hashlib.sha256(raw).hexdigest())
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'runner-error-controls','exec'),namespace)
    return SimpleNamespace(**namespace)


def traced(error, depth=6, filename='/private/input/guard.py'):
    namespace = {}
    exec(compile('def records(depth,error):\n if depth:return records(depth-1,error)\n raise error\n',filename,'exec'),namespace)
    try:namespace['records'](depth,error)
    except BaseException as caught:return caught


def emit(handler,error,capsys):
    capsys.readouterr()
    with pytest.raises(SystemExit) as outcome:
        exec(compile(ast.Module(body=handler,type_ignores=[]),'error-handler','exec'),dict(error=error,json=json,re=re,os=os))
    assert outcome.value.code == 1
    raw = capsys.readouterr().out.encode()
    assert len(raw.splitlines()) == 1 and len(raw) <= 1024
    return json.loads(raw),raw


def handler(adapter):
    return ast.parse(adapter.RPC_FILE_ENTRY).body[0].handlers[0].body


def test_real_failure_handler_old_loses_key_new_retains_bounded_key_and_frames(adapter,runner,capsys):
    error = traced(KeyError('history_binding_sha256'))
    old,_ = emit(ast.parse(OLD_ERROR_HANDLER).body,error,capsys)
    assert set(old) == {'schema','error_type','reason'}
    new,raw = emit(handler(adapter),error,capsys)
    detail = new['error_detail']
    assert detail['key_error_field'] == 'history_binding_sha256'
    assert len(detail['source_frames']) == 3
    assert all(row['file'] == 'guard.py' and row['function'] == 'records'
               for row in detail['source_frames'])
    assert [row['line'] for row in detail['source_frames']] == [2,2,3]
    assert b'/private/input' not in raw
    checked = runner.rpc_failure_diagnostic(raw,1)
    assert checked['failure_kind'] == 'remote_callback_failed'
    assert checked['remote_error_type'] == 'KeyError'
    assert checked['remote_key_error_field'] == 'history_binding_sha256'
    assert checked['remote_source_frames'] == detail['source_frames']


def test_every_original_native_guard_and_command_budget_is_unchanged(adapter):
    native = ast.Module(body=ast.parse(adapter.RPC_FILE_ENTRY).body[0].body,type_ignores=[])
    command = next(n for n in ast.parse((BENCH/'management_file_rpc.py').read_bytes()).body
                   if isinstance(n,ast.FunctionDef) and n.name == 'command')
    digest = lambda node:hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()
    assert digest(native) == 'dd925c94f61f5fb416ffa516d05a526f2afd6b06168c2d3ed9d60e04d4d855c0'
    assert digest(command) == 'f9ce7bcbe0668c9e49c4c14aa0b3b1da385e3cb9fb33ce9beece0eb64e7d8b0a'


@pytest.mark.parametrize('key',['/private/password=value','field with space','x'*65,'',True,123,b'secret-bytes'])
def test_unsafe_or_nonfield_key_never_echoes_message_args_or_locals(adapter,runner,capsys,key):
    error = traced(KeyError(key))
    value,raw = emit(handler(adapter),error,capsys)
    assert value['error_detail']['key_error_field'] is None
    assert b'password=value' not in raw and b'secret-bytes' not in raw
    assert 'key_error_field' not in runner.error_context_chain(error)[0]


def test_key_error_does_not_call_repr_on_opaque_args(adapter,capsys):
    class Opaque:
        def __repr__(self):raise AssertionError('opaque_input_was_formatted')
    value,_ = emit(handler(adapter),traced(KeyError(Opaque())),capsys)
    assert value['error_detail']['key_error_field'] is None


def test_other_exception_preserves_exact_original_v1_record(adapter,capsys):
    value,raw = emit(handler(adapter),traced(RuntimeError('private payload canary')),capsys)
    assert value == dict(schema='meshticulum.management-file-RPC-error.v1',error_type='RuntimeError',
                         reason='management_file_RPC_refused')
    assert b'private payload canary' not in raw


def record():
    return dict(schema='meshticulum.management-file-RPC-error.v1',error_type='KeyError',reason='management_file_RPC_refused',
        error_detail=dict(key_error_field='history_binding_sha256',source_frames=[dict(file='guard.py',function='records',line=348)]))


@pytest.mark.parametrize('fault',['extra','message','bad_key','bool_key','long_key','too_many_frames','extra_frame',
                                'absolute_file','long_file','bad_function','long_function','bool_line','zero_line','float_line','large_line'])
def test_host_rejects_malformed_or_private_extended_records(runner,fault):
    value = record();detail = value['error_detail'];frame = detail['source_frames'][0]
    if fault == 'extra':value['input'] = 'private metadata canary'
    elif fault == 'message':detail['message'] = 'private metadata canary'
    elif fault == 'bad_key':detail['key_error_field'] = '/private/metadata'
    elif fault == 'bool_key':detail['key_error_field'] = True
    elif fault == 'long_key':detail['key_error_field'] = 'x'*65
    elif fault == 'too_many_frames':detail['source_frames'] *= 4
    elif fault == 'extra_frame':frame['locals'] = dict(secret='private metadata canary')
    elif fault == 'absolute_file':frame['file'] = '/private/guard.py'
    elif fault == 'long_file':frame['file'] = 'x'*65
    elif fault == 'bad_function':frame['function'] = 'private function'
    elif fault == 'long_function':frame['function'] = 'x'*65
    elif fault == 'bool_line':frame['line'] = True
    elif fault == 'zero_line':frame['line'] = 0
    elif fault == 'float_line':frame['line'] = 1.0
    elif fault == 'large_line':frame['line'] = 1000001
    checked = runner.rpc_failure_diagnostic(json.dumps(value).encode(),1)
    assert checked['remote_error_type'] is checked['remote_reason'] is None
    assert 'remote_key_error_field' not in checked and 'remote_source_frames' not in checked


@pytest.mark.parametrize('code,kind',[(1,'remote_callback_failed'),(124,'remote_callback_timeout'),(None,'callback_outcome_unproved')])
def test_original_v1_and_failure_classification_remain_unchanged(runner,code,kind):
    value = record();value.pop('error_detail');raw = json.dumps(value).encode()
    checked = runner.rpc_failure_diagnostic(raw,code)
    assert checked == dict(output_sha256=hashlib.sha256(raw).hexdigest(),failure_kind=kind,
                          remote_error_type='KeyError',remote_reason='management_file_RPC_refused')


def test_maximum_extended_labels_fit_in_original_1024_byte_line_bound(runner):
    value = record();value['error_detail'] = dict(key_error_field='x'*64,
        source_frames=[dict(file='x'*64,function='x'*64,line=1000000) for _ in range(3)])
    raw = json.dumps(value).encode();assert len(raw) <= 1024
    checked = runner.rpc_failure_diagnostic(raw,1)
    assert checked['remote_key_error_field'] == 'x'*64 and len(checked['remote_source_frames']) == 3


def test_multiple_records_or_oversized_line_cannot_supply_diagnostic(runner):
    raw = json.dumps(record()).encode()
    for output in (raw+b'\n'+raw,b' '*1025+raw):
        checked = runner.rpc_failure_diagnostic(output,1)
        assert checked['remote_error_type'] is None and 'remote_source_frames' not in checked


def test_actual_fast_part_finally_stays_fatal_while_context_chain_is_retained(runner):
    from lantern_meshticulum.bench import fast_bundle
    method = next(n for n in ast.walk(ast.parse(Path(fast_bundle.__file__).read_bytes()))
                  if isinstance(n,ast.FunctionDef) and n.name == 'transfer_part')
    block = next(n for n in method.body if isinstance(n,ast.Try))
    def direct_piece(*args,**kwargs):
        try:raise KeyError('history_binding_sha256')
        except KeyError:raise RuntimeError('batch_native_RPC_failed')
    controller = SimpleNamespace(direct_piece=direct_piece,retire=lambda:[dict(retirement_error='WireError')],links=[])
    namespace = dict(controller=controller,self=SimpleNamespace(links=[]),request=b'',response=b'',
                     require=fast_bundle.require,index=0)
    with pytest.raises(Exception) as outcome:
        exec(compile(ast.Module(body=[block],type_ignores=[]),'actual-fast-part-cleanup','exec'),namespace)
    assert type(outcome.value).__name__ == 'WireError' and str(outcome.value) == 'fast_part_cleanup_unproved'
    assert runner.error_context_chain(outcome.value) == [dict(error_type='WireError'),dict(error_type='RuntimeError'),
                                                        dict(error_type='KeyError',key_error_field='history_binding_sha256')]


def test_explicit_cause_cycles_and_depth_are_bounded_without_messages(runner):
    errors = [RuntimeError('private payload canary') for _ in range(5)]
    for first,second in zip(errors,errors[1:]):first.__cause__ = second
    chain = runner.error_context_chain(errors[0]);assert len(chain) == 3
    assert all(row == dict(error_type='RuntimeError') for row in chain)
    errors[0].__cause__ = errors[0]
    assert runner.error_context_chain(errors[0]) == [dict(error_type='RuntimeError')]
    assert 'private payload canary' not in json.dumps(chain)


def test_main_records_context_chain_without_changing_top_failure_or_finally(runner):
    main = next(n for n in ast.parse((BENCH/'run_public_gfsk250_mb1_fast_bundle100k.py').read_bytes()).body
                if isinstance(n,ast.FunctionDef) and n.name == 'main')
    outer = next(n for n in main.body if isinstance(n,ast.Try))
    error = RuntimeError('batch_native_RPC_failed');error.__context__ = KeyError('history_binding_sha256')
    result = dict(complete=False)
    exec(compile(ast.Module(body=outer.handlers[0].body,type_ignores=[]),'actual-main-failure-record','exec'),
         dict(e=error,result=result,error_context_chain=runner.error_context_chain))
    assert result['complete'] is False and result['error'] == 'batch_native_RPC_failed' and result['error_type'] == 'RuntimeError'
    assert result['error_chain'] == [dict(error_type='RuntimeError'),dict(error_type='KeyError',key_error_field='history_binding_sha256')]
