# SPDX-License-Identifier: Apache-2.0
"""Response composition controls only; no root, capture or native qualification."""
import ast
from pathlib import Path

import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc


@pytest.fixture
def capture_writer_result():
    # Evaluate the EXACT final writer return expression, with public source-
    # control locals. No capture_history operation, filesystem or grant occurs.
    tree = ast.parse(Path(rpc.__file__).read_text())
    writer = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='capture_history')
    returned = max((n for n in ast.walk(writer) if isinstance(n,ast.Return)
                    and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name)
                    and n.value.func.id=='dict'),key=lambda n:n.lineno)
    expression = returned.value
    names = dict(binding_pin='a'*64,raw=b'{"local_control_only":true}',
                 phase=dict(epoch_us=1,monotonic_us=2,deadline_monotonic_us=3),
                 reports=[dict(paid_caps=dict(radio_attempts=256,estimated_airtime_us=60000000,
                     ip_frames=0,tx_stream_bytes=0,rx_stream_bytes=0))])
    return eval(compile(ast.Expression(expression),str(rpc.__file__),'eval'),
                {'__builtins__':{},'dict':dict,'sha':rpc.sha},names)


def test_actual_capture_writer_return_no_longer_collides(capture_writer_result):
    result = capture_writer_result; before = dict(result)
    assert result['global_debt_complete'] is False
    # Reproduce the actual deployed failure without running a native writer.
    with pytest.raises(TypeError): dict(global_debt_complete=False,**result)
    response = rpc.response_metadata('capture','unit-c',result)
    assert response['schema']==rpc.CONTRACT_SCHEMA and response['unit']=='unit-c'
    assert response['action']=='capture' and response['global_debt_complete'] is False
    assert response['original_unknown_actual_cost'] is None and response['refunds']==0
    assert all(response[k]==v for k,v in before.items()) and result==before


@pytest.mark.parametrize('action',['capture','enroll','guard','audit','observe-cache'])
def test_all_action_result_shapes_keep_fixed_envelope(action,capture_writer_result):
    result = capture_writer_result if action=='capture' else (
        dict(observation=dict(local_control_only=True)) if action=='observe-cache' else
        dict(parent_id='b'*32,policy_digest='c'*64,snapshot=dict(claims=0),
             **(dict(terminal_observation=None) if action in ('guard','audit') else {})))
    response = rpc.response_metadata(action,'unit-c',result)
    assert response['action']==action and response['unit']=='unit-c'
    for key in ('radio_opened','private_production','global_debt_complete'):
        assert response[key] is False
    assert all(response[k]==v for k,v in result.items())


@pytest.mark.parametrize('key,value',[('schema','foreign'),('unit','unit-b'),('action','audit'),
    ('radio_opened',True),('private_production',True),('global_debt_complete',True),
    ('radio_opened',0),('private_production',0),('global_debt_complete',0)])
def test_conflicting_result_values_or_types_are_refused(key,value,capture_writer_result):
    result = dict(capture_writer_result); result[key]=value
    with pytest.raises(RuntimeError,match='native_resume_result_invariant_conflict'):
        rpc.response_metadata('capture','unit-c',result)


def test_matching_invariants_are_accepted_without_reclassifying_metadata(capture_writer_result):
    repeated = dict(capture_writer_result,schema=rpc.CONTRACT_SCHEMA,unit='unit-c',action='capture',
                    radio_opened=False,private_production=False)
    assert rpc.response_metadata('capture','unit-c',repeated)==repeated


def test_dispatch_uses_checked_composition_at_its_only_final_return():
    tree=ast.parse(Path(rpc.__file__).read_text())
    dispatch=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='dispatch')
    end=dispatch.body[-1]
    assert isinstance(end,ast.Return) and isinstance(end.value,ast.Call)
    assert end.value.func.id=='response_metadata'
    assert [arg.id for arg in end.value.args]==['action','unit','result']


@pytest.mark.parametrize('parent',rpc.CAPTURE_ONLY_PARENTS)
def test_new_capture_cannot_adopt_retained_unenrolled_parent(parent):
    with pytest.raises(RuntimeError,match='invalid_own_parent'):
        rpc.capture_history('unit-c',dict(parent_id=parent),None)


def test_native_history_namespace_requires_exact_retained_artifact():
    # Check the explicit native requirement, since optional absence exists only
    # for reusable local fixtures. This does not run a native history collector.
    tree=ast.parse(Path(rpc.__file__).read_text())
    functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    for name in ('full_history','capture_history'):
        calls=[n for n in ast.walk(functions[name]) if isinstance(n,ast.Call)
               and isinstance(n.func,ast.Name) and n.func.id=='historical_parent_ids']
        assert calls and all(any(k.arg=='require_retained' and isinstance(k.value,ast.Constant)
                                and k.value.value is True for k in call.keywords) for call in calls)
        assert all(any(k.arg=='require_unadmitted' and isinstance(k.value,ast.Constant)
                       and k.value.value is True for k in call.keywords) for call in calls)
