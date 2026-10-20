"""Portable250 fixture/operator preparation, no native jobs or RF."""
import ast
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def operators(monkeypatch):
    bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    monkeypatch.syspath_prepend(str(bench))
    def load(name):
        spec=importlib.util.spec_from_file_location(name,bench/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    return (load('run_public_gfsk250_mb1_bundle100k'),
        load('prepare_public_gfsk250_mb1_bundle_fixture'),
        load('prepare_public_gfsk125_mb1_bundle_fixture'))


def test_verified_same_file_uses_fresh_manifest_and_exact_wire_budget(operators,tmp_path):
    runner,new_builder,old_builder=operators
    old=tmp_path/'old';first=old_builder.build(old);pin=runner.sha((old/'FIXTURE.json').read_bytes())
    fresh=tmp_path/'fresh';second=new_builder.build(fresh,baseline_fixture=old,baseline_fixture_sha256=pin)
    op=dict(fixture=dict(path=str(fresh),sha256=runner.sha((fresh/'FIXTURE.json').read_bytes())),
        baseline_fixture=dict(path=str(old),sha256=pin))
    piece=runner.fixture(op)
    assert first['whole_sha256']==second['whole_sha256']==piece['whole']['sha256']
    assert first['manifest_sha256']!=second['manifest_sha256']
    assert len(piece['responses'])==5 and sum(map(len,piece['responses']))==103345
    assert second['baseline_fixture_sha256']==pin


def test_baseline_hash_or_namespace_change_refuses_without_output(operators,tmp_path):
    runner,builder,old_builder=operators;old=tmp_path/'old';old_builder.build(old)
    with pytest.raises(Exception,match='baseline_fixture_changed'):
        builder.build(tmp_path/'never',baseline_fixture=old,baseline_fixture_sha256='f'*64)
    assert not (tmp_path/'never').exists()


def test250_retirement_cleanup_deadlines_and_stage_schedule_match125(operators):
    runner,_,_=operators;base=Path(runner.__file__).parent
    def functions(path):
        return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.FunctionDef)}
    old=functions(base/'run_public_gfsk125_mb1_bundle100k.py');new=functions(Path(runner.__file__))
    for name in ('phase_timing_mode','wait_for_stage','require_retired','cleanup_current','native_callback_seconds','planned_stages','cleanup_rpc_input'):
        assert old[name]==new[name]
    text=Path(runner.__file__).read_text()
    assert "op.get('require_same_boot_gfsk250_capability') is True" in text
    assert "op.get('first_inspect_before_claims') is True" in text
    assert text.index("inspection=operator._action('unit-a','inspect'") < text.index("absent=native('unit-a','observe-cache'")
    assert 'same_boot_gfsk250_capability_required' in text
