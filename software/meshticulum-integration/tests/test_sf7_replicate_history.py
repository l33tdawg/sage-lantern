# SPDX-License-Identifier: Apache-2.0
"""Exact paid endpoint and held namespace controls; no native admission or I/O."""
import os
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.history_metadata_fence import FenceError, HistoricalMetadataFence
_spec = importlib.util.spec_from_file_location('paid_seal_fixture', Path(__file__).with_name('test_history_paid_parent_seal.py'))
_fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture_module)
fixture = _fixture_module.fixture

RUNS = ('ad817046664eef590433541ac976ba34', 'c64aede596ce549e1af12542affca4cc')


def observed(unit, runs=None):
    ids = ([] if unit == 'unit-b' else list(RUNS)) if runs is None else runs
    detail = dict(claim_count=len(ids), prepared_count=len(ids), start_count=len(ids),
                  claims=[dict(child=dict(run_id=run), prepared=True, started=True) for run in ids])
    return dict(detail=detail), [dict(run_id=run) for run in ids]


@pytest.mark.parametrize('unit', ['unit-a', 'unit-b', 'unit-c'])
def test_exact_successful_paid_endpoint_sets_are_retained(unit):
    value, runs = observed(unit)
    rpc.qualify_history_claims(SimpleNamespace(parent_id=rpc.SF7_REPLICATE_PAID), unit, value, runs, [])


@pytest.mark.parametrize('unit,runs', [('unit-a', []), ('unit-c', [RUNS[0]]),
                                     ('unit-b', list(RUNS)), ('unit-a', [RUNS[0], 'f'*32])])
def test_missing_or_foreign_endpoint_never_satisfies_paid_history(unit, runs):
    value, started = observed(unit, runs)
    with pytest.raises(RuntimeError, match='sf7_replicate_claim_scope_changed'):
        rpc.qualify_history_claims(SimpleNamespace(parent_id=rpc.SF7_REPLICATE_PAID), unit, value, started, [])


@pytest.mark.parametrize('count', [0, 2])
def test_actual_held_metadata_fence_accepts_only_scoped_identity_count(tmp_path, count):
    parent = 'parents/'+rpc.SF7_REPLICATE_PAID
    root, files, directories = fixture(tmp_path, count, parent)
    with HistoricalMetadataFence(root, owner_uid=os.geteuid(), expected_files=files,
                                 expected_directories=directories) as fence:
        fence.check()


@pytest.mark.parametrize('count', [1, 3])
def test_other_identity_counts_are_refused(tmp_path, count):
    root, files, directories = fixture(tmp_path, count, 'parents/'+rpc.SF7_REPLICATE_PAID)
    with pytest.raises(FenceError, match='invalid_historical_prepared_identity_name'):
        HistoricalMetadataFence(root, owner_uid=os.geteuid(), expected_files=files,
                                expected_directories=directories)


def test_replacing_prepared_metadata_keeps_original_inode_fence(tmp_path):
    parent = 'parents/'+rpc.SF7_REPLICATE_PAID
    root, files, directories = fixture(tmp_path, 2, parent)
    path = root/parent/'prepared-identities'/directories[parent+'/prepared-identities'][0]
    with HistoricalMetadataFence(root, owner_uid=os.geteuid(), expected_files=files,
                                 expected_directories=directories) as fence:
        replacement = root/'replacement'; replacement.write_bytes(path.read_bytes()); replacement.chmod(0o600)
        os.replace(replacement, path)
        with pytest.raises(FenceError): fence.check()


@pytest.mark.parametrize('failed', [[RUNS[0]], ['f'*32], None])
def test_successful_H15_cannot_be_reclassified_as_failed_by_caller(failed):
    import ast
    import inspect
    # Execute the actual full_history success-scope predicate before metadata
    # interpretation. This is a source contract, never a native Snapshot.
    tree = ast.parse(inspect.getsource(rpc.full_history))
    gates = [n for n in ast.walk(tree) if isinstance(n, ast.If)
        and len(n.body) == 1 and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Call)
        and any(isinstance(arg, ast.Constant)
                and arg.value == 'successful_mixed_failure_scope_changed'
                for arg in n.body[0].value.args)]
    assert len(gates) == 1
    gate = compile(ast.Module(body=gates, type_ignores=[]), rpc.__file__, 'exec')
    scope = dict(policy=SimpleNamespace(parent_id=rpc.SF7_REPLICATE_PAID),
                 v=dict(failed_started_runs=failed))
    with pytest.raises(RuntimeError, match='successful_mixed_failure_scope_changed'):
        exec(gate, rpc.__dict__, scope)
    scope['v']['failed_started_runs'] = []
    exec(gate, rpc.__dict__, scope)
