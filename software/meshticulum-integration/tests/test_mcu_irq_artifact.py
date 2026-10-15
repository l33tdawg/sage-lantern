"""Explicit IRQ source binding; no device operation or native permission."""
from copy import deepcopy
import hashlib
import json
from types import MappingProxyType, SimpleNamespace

import pytest

from lantern_meshticulum.bench import mcu_artifact as mcu, node
from lantern_meshticulum.bench.pilot_parent_caps import child_caps
from tests.test_mcu_artifact_selection import selection_plan, packet_module, irq_reproduced_builds


def manifest(selector=mcu.SELECTOR):
    selected=mcu.artifact(selector)
    return dict(mcu_artifact=selected,firmware=selected['firmware'],
        firmware_by_unit={'unit-a':selected['firmware']},radio_profile_name='gfsk250',
        native_operation_kind=mcu.KIND,
        source_files={'provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json':selected['source_lock_sha256']})


def owner(selector):
    selected=mcu.artifact(selector)
    return mcu.owner_envelope(dict(schema='meshticulum.finite-public-resume-owner.v1',
        qualification_kind=mcu.KIND,core_commit=selected.get('runtime_core_revision',selected['core_revision']),policy_digest='a'*64),selector)


def test_original_4f_envelope_and_artifact_bytes_remain_exact():
    selected=mcu.artifact(mcu.LEGACY_SELECTOR)
    assert hashlib.sha256(mcu.canonical(selected)).hexdigest()==(
        '72b1289b782f54940c0bced77e19118e5c8ae47e1fea51ecf112e597419c582b')
    wrapped=owner(mcu.LEGACY_SELECTOR);before=mcu.canonical(wrapped)
    original,decoded=mcu.decode_owner(json.loads(before))
    assert decoded==selected and original['core_commit']==mcu.LEGACY_CORE
    assert mcu.canonical(mcu.owner_envelope(original,mcu.LEGACY_SELECTOR))==before
    assert mcu.select_runtime(manifest(mcu.LEGACY_SELECTOR))==selected
    with pytest.raises(mcu.McuArtifactError,match='historical_mcu_artifact_not_current'):
        mcu.require_native_scope(wrapped,SimpleNamespace(digest='a'*64,qualification_kind=mcu.KIND),manifest(mcu.LEGACY_SELECTOR))


def test_each_original_child_binds_its_selected_artifact_without_cost_change():
    policy,pair=selection_plan()
    old=child_caps(pair(mcu.LEGACY_SELECTOR)['packages']['unit-a'],policy)
    new=child_caps(pair(mcu.SELECTOR)['packages']['unit-a'],policy)
    assert old.cost==new.cost and old.expires_epoch_us==new.expires_epoch_us and old.digest!=new.digest
    for child in (old,new):
        meta=json.loads(child.metadata)
        assert mcu.validate_metadata(meta)==meta['mcu_artifact']
        assert meta['mcu_artifact_sha256']==hashlib.sha256(mcu.canonical(meta['mcu_artifact'])).hexdigest()


@pytest.mark.parametrize('damage',['no_source_map','old_lock','missing_lock','null_source_map'])
def test_irq_runtime_requires_exact_current_source_lock(damage):
    value=manifest()
    if damage=='no_source_map':value.pop('source_files')
    elif damage=='old_lock':value['source_files']['provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json']=mcu.artifact(mcu.LEGACY_SELECTOR)['source_lock_sha256']
    elif damage=='missing_lock':value['source_files'].clear()
    else:value['source_files']=None
    with pytest.raises(mcu.McuArtifactError,match='mcu_artifact_runtime_source_changed'):mcu.select_runtime(value)


@pytest.mark.parametrize('selector',[mcu.SELECTOR,mcu.LEGACY_SELECTOR])
def test_registered_owner_cannot_cross_bind_core_revision(selector):
    wrapped=owner(selector)
    other=mcu.LEGACY_CORE if selector==mcu.SELECTOR else mcu.CORE
    wrapped['original_owner']['core_commit']=other
    with pytest.raises(mcu.McuArtifactError,match='mcu_original_owner_mismatch'):mcu.decode_owner(wrapped)


def test_old_selector_cannot_open_serial_under_new_scope(monkeypatch):
    monkeypatch.setattr(node,'ExclusiveSerial',lambda *a,**k:pytest.fail('serial must remain closed'))
    monkeypatch.setattr(mcu,'require_selector_scope',lambda *a:pytest.fail('historical selector must refuse first'))
    with pytest.raises(mcu.McuArtifactError,match='historical_mcu_artifact_not_current'):
        node._serial('unit-a',radio_modem='gfsk250',qualification_kind=mcu.KIND,mcu_artifact_selector=mcu.LEGACY_SELECTOR)


def test_real_h44_roster_refuses_consumed_h43_scope(monkeypatch):
    from lantern_meshticulum.bench.retained_capture import PAID_HISTORY
    monkeypatch.setattr(mcu,'PENDING_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256='16f73f63ce91875dfcb907733ebae16b97a78450c7c769a3c9066b9752eac78e',
        history_receipt_sha256='4d27db070dc0106d067bc49372919b32431ff04a31894bff3d14746d4bca4594',
        preceding_parent='bea52c6aa6a5a95501997a46247b514f')))
    assert PAID_HISTORY[43]=='d1fd73cd772ff055140f17ca986a07d0' and PAID_HISTORY[44]=='e2449e5bcd4af15d45f7a756dff7d997' and PAID_HISTORY[45]=='360514104bf80fa187276f24d6aa9d9c' and PAID_HISTORY[46]=='02ec9185d218e44740b30227a11bd7cd' and PAID_HISTORY[47]=='ea4d3adb8508e75f5a0e2e822ecc1cd0' and PAID_HISTORY[-1]=='1a02ac26e525311caa32e180596d7160'
    assert mcu.PENDING_NATIVE_BINDINGS['preceding_parent']=='bea52c6aa6a5a95501997a46247b514f'
    with pytest.raises(mcu.McuArtifactError,match='pending_mcu_artifact_native_scope_and_history'):
        mcu.require_selector_scope(mcu.KIND)


def test_irq_artifact_binds_actual_two_reproductions_and_exact_current_source(irq_reproduced_builds):
    import os
    from pathlib import Path
    selected=mcu.artifact();module=packet_module();core=Path(os.environ['MESHTICULUM_SOURCE'])
    assert module.validate_source(core,selected)==core.resolve()
    raw,files,records=module.reproduced(irq_reproduced_builds,selected)
    assert len(raw)==391600 and hashlib.sha256(raw).hexdigest()==mcu.IMAGE_SHA256
    assert len(files)==4 and len(records)==2 and selected['runtime_core_revision']==mcu.CORE
    changed=deepcopy(selected);changed['application']['main.cpp']=mcu.artifact(mcu.LEGACY_SELECTOR)['application']['main.cpp']
    with pytest.raises(mcu.McuArtifactError,match='mcu_artifact_binding_changed'):module.validate_source(core,changed)
