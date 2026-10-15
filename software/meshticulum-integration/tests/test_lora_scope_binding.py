"""Actual source/scope files; neither this fixture nor its pins grant RF."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from types import MappingProxyType

import pytest

from lantern_meshticulum.bench import mcu_artifact as mcu
from lantern_meshticulum.bench.profiles import profile_for
from lantern_meshticulum.bench.retained_capture import PAID_HISTORY

EXPECTED_CORE = 'af5b1fd23a454746122d0b256908a0b2cdfc1741'
EXPECTED_PREFIX = None
EXPECTED_SCOPE_SHA = None
EXPECTED_LOCK_SHA = 'a367469b88cc0bce515d187d7fd29bd1b6d12d462403763623564ed30a11871c'
OLD_SCOPES = (
    ('c922e922b97590a69fc331a83134c6d94f0ee700a2524b803c7e2dc0928c5f1f',
     'meshticulum-h46-hotpath-operation-repaired-final-20261015'),
    ('ba84af81f913d73013fdefa1b550725795f91d0bea11ee0e7367169b72999e50',
     'meshticulum-h46-ip-fairness-operation-preparation-20261015'),
    ('3d88fb98b68c02c865fae66faa935744687227bf6cf9a07f595890ec774f0152',
     'meshticulum-h46-ip-window-operation-preparation-20261015'),
    ('c4ce1ce81748084ede8b8731c1479fc70f36a196ad5601a7651c570e66014c9e',
     'meshticulum-h46-benchflow-operation-preparation-20261015'),
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, maximum=200000):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        assert stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<before.st_size<=maximum
        raw=os.read(fd,maximum+1);after=os.fstat(fd);named=path.lstat()
        stamp=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_gid,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        assert stamp(before)==stamp(after)==stamp(named) and len(raw)==before.st_size
        return raw
    finally:os.close(fd)


def test_explicit_pending_lora_source_binding_refuses_after_any_activation(monkeypatch):
    """Always exercises refusal; a missing positive fixture is not a pass."""
    pins=dict(mcu.LORA_NATIVE_BINDINGS);pins['scope_sha256']=None
    monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(pins))
    with pytest.raises(mcu.McuArtifactError,match='pending_lora_mcu_artifact_native_scope_and_history'):
        mcu.require_lora_selector_scope(mcu.LORA_KIND)


def test_actual_lora_scope_binds_exact_source_history_and_unissued_predecessors():
    configured=os.environ.get('MESHTICULUM_LORA_COMPARISON_FIXTURE')
    if configured is None:
        pytest.skip('Explicit reviewed LoRa Scope fixture is required; no ready or native claim follows this skip.')
    assert all(type(v) is str for v in (EXPECTED_CORE,EXPECTED_PREFIX,EXPECTED_SCOPE_SHA,EXPECTED_LOCK_SHA))
    scope_raw=read(Path(configured)/'SCOPE.json');scope=json.loads(scope_raw)
    selected=mcu.lora_artifact();pins=mcu.require_lora_selector_scope(mcu.LORA_KIND)
    assert sha(scope_raw)==EXPECTED_SCOPE_SHA==pins['scope_sha256']
    assert scope['schema']=='meshticulum.root-reviewed-lora-sf7-sf5-comparison-scope.v1'
    assert scope['core_commit']==selected['runtime_core_revision']==mcu.CORE==EXPECTED_CORE
    assert scope['lantern_commit']==scope['reviewed_implementation_commit']==EXPECTED_PREFIX
    assert scope['lora_mcu_artifact']==selected
    assert scope['source_lock_sha256']==selected['source_lock_sha256']==EXPECTED_LOCK_SHA
    assert selected['core_revision']==mcu.FIRMWARE_CORE=='2b9db9e71dfa7d115d4e465225893b828c760171'
    assert selected['firmware']==scope['firmware']=='mtb1-020e287255'
    assert selected['image_sha256']==scope['firmware_image_sha256']==mcu.IMAGE_SHA256
    assert scope['selector']==mcu.LORA_SELECTOR and scope['qualification_kind']==mcu.LORA_KIND
    assert scope['paid_history_count']==len(PAID_HISTORY)==46
    assert scope['history_parent_ids']==list(PAID_HISTORY)
    assert scope['preceding_parent']==pins['preceding_parent']==PAID_HISTORY[-1]
    assert scope['history_receipt_sha256']==pins['history_receipt_sha256']=='cf01a817a18458f0ca2b91dca012afcc29044a9c65bb426ee9316e003cb32826'
    assert scope['comparison_descriptor']['radio_profile_names']==list(mcu.LORA_PROFILE_NAMES)==['sf7-500','sf5-500']
    assert scope['sample_count']==scope['comparison_descriptor']['sample_count']==2
    assert scope['phase_seconds']==600 and scope['application_seconds']==180 and scope['absolute_window_seconds']==720
    for name,row in zip(mcu.LORA_PROFILE_NAMES,scope['radio_profiles']):
        assert row==dict(name=name,**asdict(profile_for(name)))
    assert scope['new_caps_per_A_C']==dict(radio_attempts=1024,estimated_airtime_us=240000000)
    assert scope['new_caps_B']==0 and scope['physical_boot_caps_cumulative']==dict(radio_attempts=256,estimated_airtime_us=60000000)
    assert scope['expected_mcu_boot']=='000008f6a51b5bf80000000000000038'
    assert scope['reset_permitted'] is scope['native_permission'] is scope['window_issued'] is scope['qualification_complete'] is False
    assert scope['unknown_actual_cost'] is None and scope['global_debt_complete'] is False and scope['refunds']==0
    assert scope['preparation_only'] is scope['not_for_issuance'] is False
    assert all(v is True for v in scope['required_inputs'].values())
    assert len(scope['retained_unissued_scopes'])==4
    for record,(pin,directory) in zip(scope['retained_unissued_scopes'],OLD_SCOPES):
        path=Path(record['path']);assert path.parent.name==directory
        assert record['sha256']==sha(read(path))==pin
        assert record['operation_directory']==str(path.parent)
        assert not (path.parent/('ISSUE_USED-'+pin+'.json')).exists()
        assert not list(path.parent.glob('OPERATION-*.json'))
    lantern=Path(__file__).resolve().parents[3]
    lock_raw=read(lantern/'software/meshticulum-integration/meshticulum-source-lock.json')
    lock=json.loads(lock_raw);assert sha(lock_raw)==EXPECTED_LOCK_SHA and lock['revision']==EXPECTED_CORE
    assert len(lock['files'])==72
    core=Path(os.environ['MESHTICULUM_SOURCE'])
    assert subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip()==EXPECTED_CORE
    assert all(sha((core/name).read_bytes())==pin for name,pin in lock['files'].items())
    subprocess.run(['git','-C',str(lantern),'merge-base','--is-ancestor',EXPECTED_PREFIX,'HEAD'],check=True,timeout=5)
    assert mcu.LEGACY_ARTIFACT_SHA256=='72b1289b782f54940c0bced77e19118e5c8ae47e1fea51ecf112e597419c582b'
    assert mcu.IRQ020_ARTIFACT_SHA256=='9e478abcb07234a2f42ed70966b3ac4d82c303bafd8e20a8c5b08eaf681a1f57'
