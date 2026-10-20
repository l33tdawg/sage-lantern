"""Protected source composition with modeled facts; never physical RF."""
from copy import deepcopy
from dataclasses import replace
import base64

import pytest

from meshticulum.resources import knowledge_pack as kp
from lantern_meshticulum.bench import pilot_bundle as bundle,gfsk250_mb1_bundle as mode
from lantern_meshticulum.bench.profiles import GFSK250_MB1_BUNDLE_KIND,GFSK250_NAME
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests import test_native_mb1_bundle100k as legacy
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path,protected_source


@pytest.fixture(autouse=True)
def explicit250_model_helpers(monkeypatch):
    # Only test helpers are redirected. Production125 tables remain unchanged.
    monkeypatch.setattr(legacy,'GFSK_MB1_BUNDLE_KIND',GFSK250_MB1_BUNDLE_KIND)
    monkeypatch.setattr(legacy,'GFSK_NAME',GFSK250_NAME)
    monkeypatch.setattr(legacy,'mode',mode)


@pytest.fixture
def data250(data):
    payload,chunks,old,p=data
    stages=[dict(campaign_id=v['campaign_id'],issued_epoch_us=p.issued_epoch_us+(150 if i==0 else 105+i*240)*1000000,
        expires_epoch_us=p.issued_epoch_us+(330 if i==0 else 285+i*240)*1000000,
        handoff_end_epoch_us=p.issued_epoch_us+(340 if i==0 else 345+i*240 if i<4 else 1255)*1000000)
        for i,v in enumerate(old['stages'])]
    value=bundle.context_value(base64.b64decode(old['manifest_base64']),base64.b64decode(old['signature_base64']),
        old['trust'],old['manifest_sha256'],chunks=chunks,
        policy_clock=(p.issued_epoch_us,p.original_monotonic_us,p.issued_epoch_us+1350000000,p.original_monotonic_us+1350000000),
        stages=stages,qualification_kind=GFSK250_MB1_BUNDLE_KIND)
    p=replace(p,qualification_kind=GFSK250_MB1_BUNDLE_KIND,expires_epoch_us=value['expires_epoch_us'],
        deadline_monotonic_us=value['deadline_monotonic_us'],bundle_context_sha256=kp.sha256_hex(canonical(value)))
    return payload,chunks,value,p


def test_default250_scope_is_unbound_and_cannot_enable_native(monkeypatch):
    from types import MappingProxyType
    monkeypatch.setattr(mode,'PHYSICAL_BINDINGS',MappingProxyType(dict(mode.PHYSICAL_BINDINGS,
        scope_sha256=None,history_receipt_sha256=None,preceding_parent=None)))
    assert mode.PHYSICAL_BINDINGS['scope_sha256'] is None
    assert mode.PHYSICAL_BINDINGS['history_receipt_sha256'] is None
    assert mode.PHYSICAL_BINDINGS['preceding_parent'] is None
    with pytest.raises(Exception,match='pending_gfsk250_native_scope'):mode.require_bindings()


@pytest.mark.parametrize('fault',[None,'receive_hash','receive_length','expired'])
def test_protected250_five_parts_whole_SHA_and_all_paid_caps(data250,tmp_path,protected_source,monkeypatch,fault):
    legacy.test_protected_A_C_native_serve_verify_five_parts_whole_SHA_and_full_caps(
        data250,tmp_path,protected_source,monkeypatch,fault)


@pytest.mark.parametrize('field',['runtime_sha256','manifest_sha256'])
def test_250_current_runtime_and_manifest_cannot_be_relabelled(data250,tmp_path,protected_source,monkeypatch,field):
    legacy.test_caller_supplied_runtime_or_manifest_must_match_actual_protected_source(
        data250,tmp_path,protected_source,monkeypatch,field)


@pytest.mark.parametrize('fault',['descriptor_pin','stage_length','stage_hash','kind','expiry','one_MB'])
def test_250_context_has_same_bounded_transport_and_deadline_refusals(data250,fault):
    legacy.test_native_context_rejects_relabel_rehash_length_hash_and_window_changes(data250,fault)


def test_250_schema_or_modem_cannot_upgrade_a125_policy(data250):
    _,_,value,p=data250
    wrong=deepcopy(value);wrong['schema']=bundle.SCHEMA_GFSK_MB1
    with pytest.raises(Exception):bundle.BundleContext(wrong,replace(p,bundle_context_sha256=kp.sha256_hex(canonical(wrong))))
    legacy.test_new_grant_capability_kind_and_variable_object_are_bound_before_prepayment(data250)


def test_actual_enrolled_RF_predicate_accepts250_without_lora_attribute_access():
    import ast
    from pathlib import Path
    from lantern_meshticulum.bench import pilot_resume_rpc
    from lantern_meshticulum.bench.profiles import profile_for,profile_record,GFSK_NAME
    tree=ast.parse(Path(pilot_resume_rpc.__file__).read_bytes())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='enrolled_action')
    call=next(n for n in ast.walk(function) if isinstance(n,ast.Call) and len(n.args)==2
        and isinstance(n.args[1],ast.Constant) and n.args[1].value=='owner_RF_basis_changed')
    expression=compile(ast.Expression(call.args[0]),'<actual-enrolled-rf-predicate>','eval')
    rp=profile_for(GFSK250_NAME)
    rf=dict(country='MY',C_transmission_allowed=True,A_B_transmission_allowed=True,
        units={u:dict(antenna_connected=True) for u in ('unit-a','unit-b','unit-c')})
    owner=dict(radio_modem=GFSK250_NAME,rf_profile=profile_record(rp),
        rf_record_sha256='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9')
    values=dict(owner=owner,rf=rf,rp=rp,profile_record=profile_record,GFSK_NAME=GFSK_NAME,GFSK250_NAME=GFSK250_NAME)
    assert eval(expression,values) is True
    owner['rf_profile']=dict(owner['rf_profile'],bit_rate_bps=125000)
    assert eval(expression,values) is False
