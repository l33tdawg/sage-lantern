"""Explicit source models and genuine held native records; no RF or admission."""
from copy import deepcopy
import json
from types import MappingProxyType

import pytest

from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import lora_comparison as comparison
from lantern_meshticulum.bench import mcu_artifact as mcu
from lantern_meshticulum.bench.pilot_native_caps import open_for_provision
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests.test_lora_comparison import native, pair, qualified, value, owner_for
from tests.test_pilot_parent_snapshot import root_observation

MODEL_SCOPE='e'*64
MODEL_HISTORY='f'*64

@pytest.fixture(autouse=True)
def source_scope(monkeypatch):
    # Explicit source-model scope. Actual immutable-file checks are separate.
    from lantern_meshticulum.bench.retained_capture import PAID_HISTORY
    monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256=MODEL_SCOPE,
        history_receipt_sha256=MODEL_HISTORY,
        preceding_parent=PAID_HISTORY[-1])))


def admission():
    names=('experiment_source_run_sha256','experiment_source_tests_sha256',
        'experiment_source_provenance_sha256','experiment_suite_sha256',
        'experiment_exception_sha256','experiment_runner_sha256','experiment_validator_sha256')
    return dict(schema=comparison.EXPERIMENT_ADMISSION_SCHEMA,
        admission_mode=comparison.EXPERIMENT_MODE,
        operation_schema='meshticulum.public-radio-experiment-lora-operation.v1',
        ready_schema='meshticulum.public-radio-experiment-source-ready.v1',
        comparison_scope_sha256=MODEL_SCOPE,
        history_receipt_sha256=MODEL_HISTORY,
        full_source_green=False,alpha_qualified=False,private_production_enabled=False,
        physical_qualification=False,rf_qualified=False,ip_allowed=False,
        flash_allowed=False,reset_allowed=False,private_gate_currently_failing=True,
        **{name:format(i+1,'x')*64 for i,name in enumerate(names)})


def as_experiment(owner):
    owner=deepcopy(owner)
    owner.update(schema=comparison.EXPERIMENT_OWNER_SCHEMA,
        admission_mode=comparison.EXPERIMENT_MODE,experiment_admission=admission(),
        comparison_scope_sha256=MODEL_SCOPE,
        history_receipt_sha256=MODEL_HISTORY,
        full_source_green=False,alpha_qualified=False,private_production_enabled=False,
        physical_qualification=False,rf_qualified=False,private_gate_currently_failing=True)
    return owner


def test_distinct_owner_reaches_genuine_context_without_rewriting_schema():
    context,_,_=value();owner=as_experiment(owner_for(context));before=canonical(owner)
    result=comparison.ComparisonContext.from_owner(owner,context.policy)
    assert result.experiment_admission is owner['experiment_admission']
    assert owner['schema']==comparison.EXPERIMENT_OWNER_SCHEMA and canonical(owner)==before
    assert result.check() is True


@pytest.mark.parametrize('field,new',[
    ('full_source_green',True),('alpha_qualified',True),('private_production_enabled',True),
    ('physical_qualification',True),('rf_qualified',True),('ip_allowed',True),
    ('flash_allowed',True),('reset_allowed',True),('private_gate_currently_failing',False),
    ('operation_schema','meshticulum.public-lora-sf7-sf5-comparison-operation.v1'),
    ('ready_schema','meshticulum.selected-host-runtime-source-ready.v1'),
    ('comparison_scope_sha256','a'*64),('history_receipt_sha256','b'*64),
    ('experiment_source_run_sha256',None),('experiment_exception_sha256','pending')])
def test_experiment_admission_refuses_claims_or_missing_pins(field,new):
    payload=admission();payload[field]=new
    with pytest.raises(WireError):comparison.validate_experiment_admission(payload)


def test_standard_schema_cannot_launder_an_experiment_owner():
    context,_,_=value();owner=as_experiment(owner_for(context))
    owner['schema']='meshticulum.finite-public-resume-owner.v1'
    with pytest.raises(WireError,match='standard_comparison_owner_cannot_consume_experiment'):
        comparison.ComparisonContext.from_owner(owner,context.policy)


def test_old_standard_owner_path_stays_explicit_and_unchanged():
    context,_,_=value();owner=owner_for(context)
    owner.update(comparison_scope_sha256=MODEL_SCOPE,history_receipt_sha256=MODEL_HISTORY)
    before=canonical(owner);new=comparison.ComparisonContext.from_owner(owner,context.policy)
    assert new.experiment_admission is None and canonical(owner)==before


def test_admission_cannot_be_dropped_or_mutated_after_context_construction():
    context,_,_=value();owner=as_experiment(owner_for(context))
    result=comparison.ComparisonContext.from_owner(owner,context.policy)
    result.experiment_admission=None
    with pytest.raises(WireError,match='public_experiment_context_rebound'):result.check()
    result=comparison.ComparisonContext.from_owner(owner,context.policy)
    owner['experiment_admission']['alpha_qualified']=True
    with pytest.raises(WireError):result.check()


def test_experiment_refuses_fresh_scope_map_drift(monkeypatch):
    context,_,_=value();owner=as_experiment(owner_for(context))
    result=comparison.ComparisonContext.from_owner(owner,context.policy)
    changed=dict(mcu.LORA_NATIVE_BINDINGS,scope_sha256='b'*64)
    monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(changed))
    with pytest.raises(WireError):result.check()
    with pytest.raises(WireError):comparison.ComparisonContext.from_owner(owner,context.policy)


def test_pending_scope_refuses_experiment_before_owner_or_native_access(monkeypatch):
    context,_,_=value();owner=as_experiment(owner_for(context))
    pending=dict(mcu.LORA_NATIVE_BINDINGS,scope_sha256=None)
    monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(pending))
    with pytest.raises(mcu.McuArtifactError,match='pending_lora_mcu_artifact_native_scope_and_history'):
        comparison.ComparisonContext.from_owner(owner,context.policy)


def test_genuine_native_claim_reads_distinct_held_owner_without_schema_conversion(native):
    state,parent,context,payloads,now,clock,host,controller=native
    owner_path=parent/'owner.json';owner=as_experiment(json.loads(owner_path.read_bytes()))
    owner_path.write_bytes(canonical(owner));before=owner_path.read_bytes()
    approval=comparison.ComparisonApproval(parent,context.policy,host['unit'],host,clock)
    try:
        assert approval.context.experiment_admission==owner['experiment_admission']
        assert approval.check() is True and owner_path.read_bytes()==before
    finally:approval.close()
    package=qualified(controller,pair(context,payloads[0][0],0,now))[host['unit']]
    caps=open_for_provision(state,host,clock,package)
    try:
        claim=caps.prepare_claim(package)
        assert type(claim.run_id) is str and len(claim.run_id)==32
        snapshot=caps.snapshot()
        assert snapshot['claims']==1 and snapshot['starts']==0
        assert owner_path.read_bytes()==before
    finally:assert caps.close()


def test_held_owner_mode_mutation_is_refused_before_native_claim(native):
    _,parent,context,_,_,clock,host,_=native
    path=parent/'owner.json';owner=as_experiment(json.loads(path.read_bytes()))
    path.write_bytes(canonical(owner))
    approval=comparison.ComparisonApproval(parent,context.policy,host['unit'],host,clock)
    try:
        owner['alpha_qualified']=True;path.write_bytes(canonical(owner))
        with pytest.raises(RuntimeError,match='held_rpc_metadata_changed'):approval.check()
    finally:approval.close()


def test_distinct_owner_enroll_reaches_original_held_capture_guard(native,monkeypatch):
    """Genuine enrolled_action and Reader; stop at its unchanged capture fence."""
    from types import SimpleNamespace
    from lantern_meshticulum.bench import pilot_resume_rpc as rpc
    from tests.test_lora_mcu_admission import manifest
    state,parent,context,_,_,_,host,_=native
    owner=as_experiment(json.loads((parent/'owner.json').read_bytes()))
    prior=json.loads((parent/'prior.json').read_bytes())
    runtime_root=parent.parent.parent.parent/('runtime-'+'d'*16)
    runtime_root.mkdir(mode=0o700)
    raw=canonical(manifest());path=runtime_root/'manifest.json';path.write_bytes(raw);path.chmod(0o600)
    owner['manifest_sha256']=rpc.sha(raw)
    owner['capture_record_sha256']='0'*64
    monkeypatch.setattr(rpc,'STATE',state)
    with rpc.Reader() as reader:
        runtime=SimpleNamespace(root=runtime_root,manifest=json.loads(raw),reader=reader)
        with pytest.raises(RuntimeError,match='actual_capture_record_changed'):
            rpc.enrolled_action('enroll',host['unit'],dict(policy=context.policy.to_dict(),owner=owner,prior=prior),runtime)
    assert owner['schema']==comparison.EXPERIMENT_OWNER_SCHEMA
