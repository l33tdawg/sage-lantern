# SPDX-License-Identifier: Apache-2.0
"""Exact incomplete32 history controls; no native admission or RF."""
import ast
from copy import deepcopy
import importlib.util
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.history_metadata_fence import FenceError,HistoricalMetadataFence

_spec=importlib.util.spec_from_file_location('paid_seal_fixture',Path(__file__).with_name('test_history_paid_parent_seal.py'))
_helpers=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_helpers)
fixture=_helpers.fixture


def observed(unit,runs=None):
    ids=list(rpc.PUBLIC32_RESULT_CANONICAL[unit]) if runs is None else runs
    value=dict(detail=dict(claim_count=len(ids),prepared_count=len(ids),start_count=len(ids),
        claims=[dict(child=dict(run_id=run),prepared=True,started=True) for run in ids]))
    return value,[dict(run_id=run) for run in ids]


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_exact_failed_parent_keeps_all_started_endpoints(unit):
    value,runs=observed(unit)
    rpc.qualify_history_claims(SimpleNamespace(parent_id=rpc.PUBLIC32_PAID),unit,value,runs,[])


@pytest.mark.parametrize('unit,runs',[('unit-a',[]),('unit-c',[rpc.PUBLIC32_REQUEST]),
    ('unit-b',[rpc.PUBLIC32_REQUEST]),('unit-a',[rpc.PUBLIC32_REQUEST,'f'*32])])
def test_omission_or_foreign_run_cannot_retire_failed_paid_scope(unit,runs):
    value,started=observed(unit,runs)
    with pytest.raises(RuntimeError,match='public32_claim_scope_changed'):
        rpc.qualify_history_claims(SimpleNamespace(parent_id=rpc.PUBLIC32_PAID),unit,value,started,[])


@pytest.mark.parametrize('count',[0,2])
def test_held_H16_identity_fence_matches_scoped_zero_or_two(tmp_path,count):
    root,files,dirs=fixture(tmp_path,count,'parents/'+rpc.PUBLIC32_PAID)
    with HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs) as fence:fence.check()


@pytest.mark.parametrize('count',[1,3])
def test_H16_other_identity_counts_are_refused(tmp_path,count):
    root,files,dirs=fixture(tmp_path,count,'parents/'+rpc.PUBLIC32_PAID)
    with pytest.raises(FenceError,match='invalid_historical_prepared_identity_name'):
        HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)


def test_same_bytes_replacement_remains_an_inode_refusal(tmp_path):
    parent='parents/'+rpc.PUBLIC32_PAID;root,files,dirs=fixture(tmp_path,2,parent)
    name=dirs[parent+'/prepared-identities'][0];path=root/parent/'prepared-identities'/name
    with HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs) as fence:
        replacement=root/'replacement';replacement.write_bytes(path.read_bytes());replacement.chmod(0o600);os.replace(replacement,path)
        with pytest.raises(FenceError):fence.check()


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
@pytest.mark.parametrize('changed',['failed_set','promote_complete'])
@pytest.mark.parametrize('parent,response,request_run',[
    (rpc.PUBLIC32_PAID,rpc.PUBLIC32_RESPONSE,rpc.PUBLIC32_REQUEST),
    (rpc.PUBLIC32_RETRY_PAID,rpc.PUBLIC32_RETRY_RESPONSE,rpc.PUBLIC32_RETRY_REQUEST)])
def test_full_history_failed_set_cannot_be_reclassified(unit,changed,parent,response,request_run):
    row=dict(failed_started_runs=[] if unit=='unit-b' else [response],require_result_complete=False)
    rpc.qualify_public32_failure_scope(unit,parent,row)
    if changed=='failed_set':row['failed_started_runs']=[request_run]
    else:row['require_result_complete']=True
    with pytest.raises(RuntimeError,match='public32_failure_scope_changed'):
        rpc.qualify_public32_failure_scope(unit,parent,row)


def test_qualifier_uses_exact_outcome_hash_not_a_caller_complete_boolean(monkeypatch):
    result=dict(complete=False,disarmed_on_exit=True,parent=dict(started=True,parent_id=rpc.PUBLIC32_PAID,
        policy_digest=rpc.OLD_DIGESTS[15],run_id=rpc.PUBLIC32_RESPONSE))
    expected=deepcopy(rpc.PUBLIC32_RESULT_CANONICAL)
    expected['unit-a'][rpc.PUBLIC32_RESPONSE]=rpc.sha(rpc.canonical(result))
    monkeypatch.setattr(rpc,'PUBLIC32_RESULT_CANONICAL',expected)
    rpc.qualify_public32_result('unit-a',rpc.PUBLIC32_RESPONSE,result)
    for key in ('complete','disarmed_on_exit'):
        bad=deepcopy(result);bad[key]=not bad[key]
        with pytest.raises(RuntimeError):rpc.qualify_public32_result('unit-a',rpc.PUBLIC32_RESPONSE,bad)
