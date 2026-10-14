"""Held source-model manifest controls; no installed runtime/RF attestation."""
from copy import deepcopy
from pathlib import Path

import pytest

from lantern_meshticulum.bench import mcu_artifact as mcu, pilot_resume_rpc as rpc
from tests.test_pilot_resume_rpc_integration import local_scope, PARENT


def no_enrollment(state):
    assert not (state/'parents'/PARENT/'native.sqlite').exists()


def test_model_manifest_and_owner_pin_bind_actual_held_source_bytes(local_scope):
    state,runtime,p,_,_ = local_scope
    raw = runtime.reader.read(runtime.root/'manifest.json',65536)
    source = runtime.reader.read(runtime.root/'pilot_resume_rpc.py',500000)
    assert runtime.manifest == rpc.parse(raw)
    assert runtime.manifest['schema'] == 'meshticulum.local-runtime-model.v1'
    assert runtime.manifest['local_control_only'] is True
    assert runtime.manifest['private_services_enabled'] is False
    assert runtime.manifest['public_parent_claims_required'] is True
    assert source == Path(rpc.__file__).read_bytes()
    pin = rpc.sha(source)
    assert runtime.manifest['files'] == {'pilot_resume_rpc.py':pin}
    assert runtime.manifest['source_files'] == {'lantern_meshticulum/bench/pilot_resume_rpc.py':pin}
    assert p['owner']['manifest_sha256'] == rpc.sha(raw)
    assert mcu.select_runtime(runtime.manifest) is None
    assert rpc.enrolled_action('enroll','unit-a',p,runtime)['snapshot']['claims'] == 0


@pytest.mark.parametrize('name',['manifest.json','pilot_resume_rpc.py'])
@pytest.mark.parametrize('fault',['bytes','replacement','mode'])
def test_model_held_manifest_or_source_drift_refuses_guard_without_rewriting_debt(local_scope,name,fault):
    state,runtime,p,_,_ = local_scope
    rpc.enrolled_action('enroll','unit-a',p,runtime)
    database = state/'parents'/PARENT/'native.sqlite'
    paid_before = database.read_bytes()
    path = runtime.root/name
    if fault == 'bytes':
        path.write_bytes(path.read_bytes()+b' ')
    elif fault == 'replacement':
        raw = path.read_bytes();path.unlink();rpc.write_new(path.parent,path.name,raw)
    else:
        path.chmod(0o644)
    with pytest.raises(RuntimeError):
        rpc.enrolled_action('guard','unit-a',p,runtime)
    assert database.read_bytes() == paid_before


def selected_manifest(runtime):
    value = deepcopy(runtime.manifest)
    value.update(mcu_artifact=mcu.artifact(),firmware=mcu.FIRMWARE,
        firmware_by_unit={'unit-a':mcu.FIRMWARE},radio_profile_name='gfsk250',
        native_operation_kind=mcu.KIND)
    return value


def test_actual_selected_runtime_guard_refuses_a_legacy_owner(local_scope):
    state,runtime,p,_,_ = local_scope
    runtime.manifest = selected_manifest(runtime)
    with pytest.raises(RuntimeError,match='mcu_runtime_requires_original_owner'):
        rpc.enrolled_action('enroll','unit-a',p,runtime)
    no_enrollment(state)


@pytest.mark.parametrize('fault',['null','artifact','profile'])
def test_invalid_selected_manifest_never_falls_back_to_legacy(local_scope,fault):
    state,runtime,p,_,_ = local_scope
    runtime.manifest = selected_manifest(runtime)
    if fault == 'null':runtime.manifest['mcu_artifact'] = None
    elif fault == 'artifact':runtime.manifest['mcu_artifact']['image_sha256'] = '0'*64
    else:runtime.manifest['radio_profile_name'] = 'gfsk125'
    with pytest.raises(mcu.McuArtifactError):
        rpc.enrolled_action('enroll','unit-a',p,runtime)
    no_enrollment(state)


def test_original_owner_manifest_pin_is_not_inferred_from_model_mapping(local_scope):
    state,runtime,p,_,_ = local_scope
    p['owner']['manifest_sha256'] = '0'*64
    with pytest.raises(RuntimeError,match='wrong_own_parent_scope'):
        rpc.enrolled_action('enroll','unit-a',p,runtime)
    no_enrollment(state)
