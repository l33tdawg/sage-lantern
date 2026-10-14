"""Source-only selection and debt controls; no device or native approval."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import os
from types import MappingProxyType, SimpleNamespace

import pytest

from meshticulum.contracts.identity import PeerID
from lantern_meshticulum.bench import mcu_artifact as mcu, pilot_job, profiles, node
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_parent_caps import (
    Cost, UnitCaps, ParentPolicy, ParentCaps, child_caps, canonical, ParentCapsError)

BOOT='00000000-0000-4000-8000-000000000001'
LAN=Path(__file__).resolve().parents[3]


@pytest.fixture
def pending_selector_bindings(monkeypatch):
    """One requested refusal case; independent of deployed activation pins."""
    monkeypatch.setattr(mcu,'PENDING_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256=None,history_receipt_sha256=None,preceding_parent=None)))


def selection_plan():
    units=tuple(UnitCaps(u,BOOT,PeerID('mcu-source-control',c*64),Cost(1024,240000000),
        Cost(1,1000),c*64) for u,c in [('unit-a','a'),('unit-c','c')])
    policy=ParentPolicy('1'*32,BOOT,90000000,180000000,40000000,130000000,units,
        Cost(2048,480000000),'b'*64,mcu.KIND,'c'*64)
    def pair(selector=None):
        return pair_packages(campaign_id='2'*32,resource_sha256='3'*64,
            application_expires_epoch_us=160000000,sender='unit-c',receiver='unit-a',
            transport='lora',format='MR1.response',descriptor=dict(bytes=128,sha256='4'*64),
            source='5'*32,request=None,peers={u.unit:asdict(u.peer)for u in units},
            boots={u.unit:BOOT for u in units},now_epoch_us=100000000,
            profile='gfsk250',radio_permission=dict(country='MY',antenna_confirmed=True),
            public32=True,mb1_descriptor_sha256='6'*64,mcu_artifact_selector=selector)
    return policy,pair


@pytest.mark.parametrize('field', ['selector','image_sha256','board_id','target','core_revision',
    'recipe_sha256','input_lock_sha256','application','verified_artifacts','capability_record_version'])
def test_selector_refuses_artifact_or_source_substitution(field):
    value=mcu.artifact();value[field]=False
    with pytest.raises(mcu.McuArtifactError):mcu.validate_artifact(value)


def test_legacy_firmware_and_default_plans_remain_old():
    assert profiles.GFSK250_FIRMWARE=='mtb1-def16514fc'
    assert profiles.GFSK_FIRMWARE=='mtb1-b433927ba6'
    assert mcu.select_runtime({'firmware':profiles.GFSK250_FIRMWARE}) is None
    policy,pair=selection_plan()
    original=pair()['packages']['unit-a']
    assert original['schema']=='meshticulum.public-pilot-package.v1'
    assert 'mcu_artifact'not in original
    assert 'schema'not in json.loads(child_caps(original,policy).metadata)
    original['mcu_artifact']=mcu.artifact()
    with pytest.raises(ParentCapsError):child_caps(original,policy)


def test_original_selection_changes_child_binding_before_full_prepayment(tmp_path):
    policy,pair=selection_plan();legacy=pair();fresh=pair(mcu.SELECTOR)
    a=child_caps(legacy['packages']['unit-a'],policy);b=child_caps(fresh['packages']['unit-a'],policy)
    assert a.cost==b.cost==Cost(256,60000000)
    assert a.digest!=b.digest and a.expires_epoch_us==b.expires_epoch_us
    assert mcu.validate_metadata(json.loads(b.metadata))==mcu.artifact()
    directory=tmp_path/'controller';directory.mkdir(mode=0o700)
    parent=ParentCaps.enroll(directory/'caps.sqlite',policy,clock=lambda:(100000000,50000000),boot_reader=lambda:BOOT)
    try:
        permit=parent.prepay_pair(tuple(fresh['packages'].values()))
        snapshot=parent.snapshot()
        assert snapshot['allocations']==1
        assert snapshot['aggregate']==asdict(Cost(514,120002000))
        changed=deepcopy(fresh['packages']['unit-a']);changed['mcu_artifact']['board_id']=2
        with pytest.raises(ParentCapsError):parent.prepay_pair((changed,fresh['packages']['unit-c']))
        after=parent.snapshot()
        assert all(after[k]==snapshot[k] for k in ('allocations','aggregate','per_unit'))
        assert permit.children[0].cost==permit.children[1].cost==Cost(256,60000000)
    finally:parent.close()


@pytest.mark.parametrize('damage',['firmware','profile','kind','board','null'])
def test_runtime_selection_is_exact_and_has_no_legacy_fallback(damage):
    manifest=dict(mcu_artifact=mcu.artifact(),firmware=mcu.FIRMWARE,
        firmware_by_unit={'unit-a':mcu.FIRMWARE},radio_profile_name='gfsk250',native_operation_kind=mcu.KIND,
        source_files={'provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json':mcu.artifact()['source_lock_sha256']})
    if damage=='firmware':manifest['firmware_by_unit']['unit-a']=profiles.GFSK250_FIRMWARE
    elif damage=='profile':manifest['radio_profile_name']='gfsk125'
    elif damage=='kind':manifest['native_operation_kind']='another'
    elif damage=='null':manifest['mcu_artifact']=None
    else:manifest['mcu_artifact']['board_id']=2
    with pytest.raises(mcu.McuArtifactError):mcu.select_runtime(manifest)


def test_original_owner_is_versioned_and_pending_scope_never_supplies_permission(pending_selector_bindings):
    base=dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind=mcu.KIND,
        core_commit=mcu.CORE,policy_digest='a'*64)
    owner=mcu.owner_envelope(base,mcu.SELECTOR)
    assert mcu.decode_owner(owner)==(base,mcu.artifact())
    manifest=dict(mcu_artifact=mcu.artifact(),firmware=mcu.FIRMWARE,
        firmware_by_unit={'unit-a':mcu.FIRMWARE},radio_profile_name='gfsk250',native_operation_kind=mcu.KIND,
        source_files={'provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json':mcu.artifact()['source_lock_sha256']})
    with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):
        mcu.require_native_scope(owner,SimpleNamespace(digest='a'*64,qualification_kind=mcu.KIND),manifest)
    owner['original_owner']['core_commit']='0'*40
    with pytest.raises(mcu.McuArtifactError):mcu.decode_owner(owner)


def test_new_selector_never_opens_serial_before_missing_native_scope(monkeypatch,pending_selector_bindings):
    monkeypatch.setattr(node,'ExclusiveSerial',lambda *a,**k:pytest.fail('serial must not open'))
    with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):
        node._serial('unit-a',radio_modem='gfsk250',qualification_kind=mcu.KIND,mcu_artifact_selector=mcu.SELECTOR)
    with pytest.raises(Exception):
        node._serial('unit-b',radio_modem='gfsk250',qualification_kind=mcu.KIND,mcu_artifact_selector=mcu.SELECTOR)


def test_retirement_checks_original_artifact_context_and_preserves_debt(tmp_path):
    policy,pair=selection_plan();package=pair(mcu.SELECTOR)['packages']['unit-a'];child=child_caps(package,policy).to_dict()
    plan=deepcopy(package['grant']);plan['application_deadline_monotonic_us']=120000000
    parent=dict(schema='meshticulum.native-parent-claim.v1',prepared=True,unit='unit-a',host_boot_id=BOOT,
        run_id=plan['session_id'],native_plan_sha256=hashlib.sha256(canonical(plan)).hexdigest(),
        original_plan_sha256=child['metadata']['plan_sha256'],grant_context_sha256=child['metadata']['grant_digest'],child_caps=child)
    provision=dict(schema=mcu.PROVISION_SCHEMA,unit='unit-a',run_id=plan['session_id'],transport='lora',
        parent=parent,mcu_artifact=mcu.artifact())
    directory=tmp_path/'run';directory.mkdir(mode=0o700)
    def write(name,value):
        p=directory/name;p.write_bytes(value if type(value)is bytes else canonical(value));p.chmod(0o600)
    write('grant.json',plan);write('provision.json',provision);write('active',b'active\n');write('attempts.sqlite',b'unchanged source debt')
    before=(directory/'attempts.sqlite').read_bytes()
    result=pilot_job.retire(directory,unit='unit-a',run_id=plan['session_id'],boot=BOOT)
    assert result['retired']and not result['debt_refunded']and (directory/'attempts.sqlite').read_bytes()==before
    write('active',b'active\n');parent['grant_context_sha256']='0'*64;write('provision.json',provision)
    with pytest.raises(mcu.McuArtifactError):pilot_job.retire(directory,unit='unit-a',run_id=plan['session_id'],boot=BOOT)
    assert (directory/'active').read_bytes()==b'active\n'


def packet_module():
    script=LAN/'firmware/meshticulum-bench/build_mcu_artifact_packet.py'
    import sys
    sys.path.insert(0,str(script.parent))
    spec=importlib.util.spec_from_file_location('mcu_packet_source_control',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_packet_generation_requires_explicit_pinned_current_rollback(tmp_path):
    module=packet_module();raw=b'\xe9'+bytes(11)+b'\x09\0'+bytes(18)
    image=tmp_path/'prior.bin';image.write_bytes(raw)
    record=dict(schema='meshticulum.independently-verified-current-app.v1',unit='unit-a',board_id=1,
        target='mtbench_heltec_v4',usb_serial='F8:5B:1B:A5:F6:08',firmware='mtb1-00000000000',
        image_sha256=hashlib.sha256(raw).hexdigest(),image_bytes=len(raw),
        current_app_md5=hashlib.md5(raw).hexdigest(),independently_verified=True)
    record['firmware']='mtb1-0000000000'
    path=tmp_path/'prior.json';path.write_bytes(canonical(record));pin=hashlib.sha256(path.read_bytes()).hexdigest()
    assert module.rollback(image,path,pin)[0]==raw
    for damage in ('pin','image','board','target','attestation'):
        value=deepcopy(record)
        if damage=='image':value['image_sha256']='0'*64
        elif damage=='board':value['board_id']=2
        elif damage=='target':value['target']='mtbench_xiao_s3_wio'
        elif damage=='attestation':value['independently_verified']=False
        path.write_bytes(canonical(value));new_pin=hashlib.sha256(path.read_bytes()).hexdigest()
        with pytest.raises(ValueError):module.rollback(image,path,'0'*64 if damage=='pin'else new_pin)


def test_mixed_legacy_and_selected_pair_refuses_before_allocation(tmp_path):
    policy,pair=selection_plan();legacy=pair();fresh=pair(mcu.SELECTOR)
    directory=tmp_path/'controller';directory.mkdir(mode=0o700)
    parent=ParentCaps.enroll(directory/'caps.sqlite',policy,clock=lambda:(100000000,50000000),boot_reader=lambda:BOOT)
    try:
        with pytest.raises(ParentCapsError):parent.prepay_pair((fresh['packages']['unit-a'],legacy['packages']['unit-c']))
        snapshot=parent.snapshot()
        assert snapshot['allocations']==0 and snapshot['aggregate']==asdict(Cost(2,2000))
    finally:parent.close()


@pytest.fixture
def reproduced_builds():
    configured=os.environ.get('MESHTICULUM_MCU_REPRODUCED_BUILD_DIRS')
    if configured is None:
        pytest.skip('Real source-build evidence requires explicit MESHTICULUM_MCU_REPRODUCED_BUILD_DIRS JSON with two reviewed build directories.')
    paths=json.loads(configured)
    assert type(paths)is list and len(paths)==2 and all(type(p)is str for p in paths)
    paths=[Path(p)for p in paths]
    if not all(p.is_dir()for p in paths):
        pytest.skip('Explicit real source-build evidence directories are unavailable in this checkout.')
    return paths


def test_actual_reproduced_records_and_application_sources_match_selected_artifact(reproduced_builds):
    module=packet_module();selected=mcu.artifact(mcu.LEGACY_SELECTOR)
    core=Path(os.environ['MESHTICULUM_SOURCE'])
    # Current IRQ source cannot be relabelled as the historical 4f artifact.
    with pytest.raises(ValueError,match='mcu_application_source_changed'):module.validate_source(core,selected)
    original,second=reproduced_builds
    image,files,records=module.reproduced([original,second],selected)
    assert len(image)==391424 and hashlib.sha256(image).hexdigest()==mcu.LEGACY_IMAGE_SHA256
    assert len(files)==4 and len(records)==2
    with pytest.raises(ValueError):module.reproduced([original,original],selected)
    damaged=deepcopy(selected);damaged['verified_artifacts']['firmware.bin']='0'*64
    with pytest.raises(ValueError):module.reproduced([original,second],damaged)


@pytest.fixture
def irq_reproduced_builds():
    configured=os.environ.get('MESHTICULUM_MCU_IRQ_REPRODUCED_BUILD_DIRS')
    if configured is None:
        pytest.skip('IRQ build evidence requires explicit MESHTICULUM_MCU_IRQ_REPRODUCED_BUILD_DIRS JSON with two reviewed directories.')
    paths=json.loads(configured)
    assert type(paths)is list and len(paths)==2 and all(type(p)is str for p in paths)
    paths=[Path(p)for p in paths]
    assert all(p.is_dir()for p in paths)
    return paths


def test_packet_generation_has_pinned_sources_and_no_physical_claim(tmp_path,irq_reproduced_builds):
    """Synthetic rollback tests the generator only; no installed-app evidence."""
    import io
    import tarfile
    module=packet_module();raw=b'\xe9'+bytes(11)+b'\x09\0'+bytes(18)
    image=tmp_path/'synthetic-rollback.bin';image.write_bytes(raw)
    prior=dict(schema='meshticulum.independently-verified-current-app.v1',unit='unit-a',board_id=1,
        target='mtbench_heltec_v4',usb_serial='F8:5B:1B:A5:F6:08',firmware='mtb1-0000000000',
        image_sha256=hashlib.sha256(raw).hexdigest(),image_bytes=len(raw),
        current_app_md5=hashlib.md5(raw).hexdigest(),independently_verified=True)
    record=tmp_path/'synthetic-rollback.json';record.write_bytes(canonical(prior))
    pin=hashlib.sha256(record.read_bytes()).hexdigest()
    arguments=dict(core=Path(os.environ['MESHTICULUM_SOURCE']),selector=mcu.SELECTOR,
        build_dirs=irq_reproduced_builds,rollback_image=image,rollback_record=record,
        rollback_record_sha256=pin,output=tmp_path/'packet')
    receipt=module.generate(**arguments)
    data=(arguments['output']/'packet.tar.gz').read_bytes()
    assert hashlib.sha256(data).hexdigest()==receipt['packet_sha256']
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz')as archive:
        files={member.name:archive.extractfile(member).read()for member in archive}
    manifest=json.loads(files.pop('manifest.json'))
    assert {name:hashlib.sha256(value).hexdigest()for name,value in files.items()}==manifest['files']
    assert hashlib.sha256(files['firmware.bin']).hexdigest()==mcu.IMAGE_SHA256
    assert hashlib.sha256(files['provenance/meshticulum-source-lock.json']).hexdigest()==mcu.artifact()['source_lock_sha256']
    assert files['rollback-current.bin']==raw and manifest['rollback_record_sha256']==pin
    assert manifest['trusted_flash_transaction_required']is True and manifest['current_physical_image_match']is None
    assert manifest['install_authorization']is manifest['flashed']is manifest['rf_qualified']is manifest['notices_complete']is False
    with pytest.raises(ValueError,match='packet_output_already_exists'):module.generate(**arguments)
    arguments.update(output=tmp_path/'missing-pin',rollback_record_sha256='0'*64)
    with pytest.raises(ValueError,match='independent_rollback_record_pin_required'):module.generate(**arguments)
    assert not arguments['output'].exists()


def test_pending_selector_constructor_closes_actual_held_runtime_and_owner(monkeypatch,pending_selector_bindings):
    """Actual readers/source files; only the deployment location is modeled."""
    import shutil
    import tempfile
    from lantern_meshticulum.bench import pilot_native_caps as native,pilot_resume_rpc as rpc
    from tests.test_public32_pilot import protected_source
    protected=Path(tempfile.mkdtemp(prefix='.mcu-refusal-control-',dir=Path(__file__).parent))
    readers=[];original=rpc.Reader.__init__
    def capture(reader):
        original(reader);readers.append(reader)
    try:
        root,_=protected_source.__wrapped__(protected,monkeypatch,None)
        path=root/'manifest.json';manifest=json.loads(path.read_bytes())
        manifest.update(mcu_artifact=mcu.artifact(),firmware=mcu.FIRMWARE,
            firmware_by_unit={'unit-a':mcu.FIRMWARE},radio_profile_name='gfsk250',native_operation_kind=mcu.KIND)
        path.write_bytes(canonical(manifest));pin=hashlib.sha256(path.read_bytes()).hexdigest()
        policy,_=selection_plan()
        owner=mcu.owner_envelope(dict(schema='meshticulum.finite-public-resume-owner.v1',
            qualification_kind=mcu.KIND,core_commit=mcu.CORE,policy_digest=policy.digest,
            manifest_sha256=pin),mcu.SELECTOR)
        directory=protected/'parent';directory.mkdir(mode=0o700)
        path=directory/'owner.json';path.write_bytes(canonical(owner));path.chmod(0o600)
        monkeypatch.setattr(rpc.Reader,'__init__',capture)
        with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):
            native._Public32Approval(directory,policy,'unit-a',{},lambda:(100000000,50000000))
        assert len(readers)==2 and all(reader._closed and reader.held for reader in readers)
        for reader in readers:
            for fd in reader._tokens:
                with pytest.raises(OSError):os.fstat(fd)
    finally:
        for reader in readers:reader.close()
        shutil.rmtree(protected)
