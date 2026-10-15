"""Current 020 LoRa source admission; no native, USB or RF qualification."""
from copy import deepcopy
from dataclasses import asdict, replace
import importlib.util
from pathlib import Path
import struct
from types import MappingProxyType

import pytest

from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, SerialError
from meshticulum.bench.serial_worker import SerialWorker
from lantern_meshticulum.bench import mcu_artifact as mcu, node
from lantern_meshticulum.bench.profiles import RADIO_ADMISSION, profile_for

HOST_BOOT = '00000000-0000-4000-8000-000000000001'
MCU_BOOT = bytes.fromhex('000008f6a51b5bf80000000000000038')


def manifest():
    return dict(lora_mcu_artifact=mcu.lora_artifact(), firmware=mcu.FIRMWARE,
        firmware_by_unit={'unit-a':mcu.FIRMWARE}, radio_profile_name=mcu.LORA_RUNTIME_PROFILE,
        radio_profile_names=list(mcu.LORA_PROFILE_NAMES), native_operation_kind=mcu.LORA_KIND,
        source_files={'provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json':mcu.lora_artifact()['source_lock_sha256']})


def test_paid_gfsk_descriptors_and_default_selection_remain_exact():
    assert mcu.LEGACY_ARTIFACT_SHA256=='72b1289b782f54940c0bced77e19118e5c8ae47e1fea51ecf112e597419c582b'
    assert mcu.IRQ020_ARTIFACT_SHA256=='9e478abcb07234a2f42ed70966b3ac4d82c303bafd8e20a8c5b08eaf681a1f57'
    assert mcu.ARTIFACT_SHA256=='efa3989be1de3e9515a3696031f8e97fdab35760462d3353e0b293d36d6a4fdb'
    selected=mcu.lora_artifact(); original=mcu.artifact()
    ignored={'schema','selector','radio_modem','radio_profile_names'}
    assert {k:v for k,v in selected.items() if k not in ignored}=={k:v for k,v in original.items() if k not in ignored}
    assert mcu.select_runtime(manifest()) is None
    assert mcu.select_lora_runtime({'firmware':'mtb1-b433927ba6'}) is None
    with pytest.raises(mcu.McuArtifactError):mcu.artifact(mcu.LORA_SELECTOR)
    for selector in (mcu.SELECTOR,mcu.IRQ020_SELECTOR,mcu.LEGACY_SELECTOR):
        with pytest.raises(mcu.McuArtifactError):mcu.lora_artifact(selector)


@pytest.mark.parametrize('damage',['null','firmware','unit-firmware','profile','profiles-order','kind',
    'source','source-missing','gfsk-selection','artifact-image','artifact-core','artifact-selector'])
def test_runtime_binding_refuses_substitution(damage):
    value=manifest()
    if damage=='null':value['lora_mcu_artifact']=None
    elif damage=='firmware':value['firmware']='mtb1-b433927ba6'
    elif damage=='unit-firmware':value['firmware_by_unit']['unit-a']='mtb1-b433927ba6'
    elif damage=='profile':value['radio_profile_name']='gfsk250'
    elif damage=='profiles-order':value['radio_profile_names'].reverse()
    elif damage=='kind':value['native_operation_kind']=mcu.KIND
    elif damage=='source':value['source_files']['provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json']='0'*64
    elif damage=='source-missing':value.pop('source_files')
    elif damage=='gfsk-selection':value['mcu_artifact']=mcu.artifact()
    else:value['lora_mcu_artifact'][{'artifact-image':'image_sha256','artifact-core':'runtime_core_revision',
        'artifact-selector':'selector'}[damage]]='0'*64
    with pytest.raises(mcu.McuArtifactError):mcu.select_lora_runtime(value)


def test_new_runtime_without_explicit_selector_and_pending_scope_refuse(monkeypatch):
    value=manifest();value.pop('lora_mcu_artifact')
    with pytest.raises(mcu.McuArtifactError):mcu.select_lora_runtime(value)
    assert mcu.select_lora_runtime(manifest())==mcu.lora_artifact()
    monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256=None,history_receipt_sha256=None,preceding_parent=None)))
    with pytest.raises(mcu.McuArtifactError,match='pending_lora'):
        mcu.require_lora_selector_scope(mcu.LORA_KIND)


@pytest.mark.parametrize('name',mcu.LORA_PROFILE_NAMES)
def test_only_existing_same_power_profiles_and_tightened_leases_are_admitted(name):
    profile=profile_for(name)
    assert mcu.lora_profile_name(profile)==name
    assert mcu.lora_profile_name(replace(profile,lease_ms=10000))==name
    assert profile.chip_power_dbm==0 and profile.max_boot_attempts==256
    assert profile.max_boot_estimated_airtime_us==60000000


@pytest.mark.parametrize('damage', ['power','frequency','sf','preamble','attempts','airtime','lease','modem'])
def test_profile_and_budget_substitution_refuse(damage):
    profile=profile_for('sf5-500')
    changes={'power':dict(chip_power_dbm=1),'frequency':dict(frequency_hz=921875000),
        'sf':dict(spreading_factor=6),'preamble':dict(preamble=8),'attempts':dict(max_boot_attempts=257),
        'airtime':dict(max_boot_estimated_airtime_us=60000001),'lease':dict(lease_ms=90001)}
    profile=profile_for('gfsk250') if damage=='modem' else replace(profile,**changes[damage])
    with pytest.raises(ValueError):mcu.lora_profile_name(profile)


@pytest.fixture
def authority(tmp_path):
    made=[]
    def create(name):
        now=[100000000,50000000]; profile=profile_for(name)
        peer=lambda char:dict(chain_id='lora-source-control',agent_id=char*64)
        plan=dict(schema='meshticulum.service-run.v1',unit='unit-a',host_boot_id=HOST_BOOT,
            local_peer=peer('a'),initiator=peer('c'),responder=peer('a'),session_id='1'*32,generation='2'*32,
            issued_epoch_us=99000000,expires_epoch_us=160000000,profile=asdict(profile),
            object=dict(bytes=8192,sha256='3'*64,chunk_index=0,object_id='3'*64),sender_unit='unit-c',
            country='MY',antenna_confirmed=True,max_frames=512,
            service=dict(kind='knowledge',format='MR1.response',application_expires_epoch_us=160000000))
        directory=tmp_path/name;directory.mkdir(mode=0o700)
        for filename,raw in [('grant.json',mcu.canonical(plan)),('secret.bin',bytes(range(32))),('active',b'active\n')]:
            path=directory/filename;path.write_bytes(raw);path.chmod(0o600)
        current=RunAuthority(directory,expected_unit='unit-a',host_boot_id=HOST_BOOT,clock=lambda:tuple(now),
            owner_probe=lambda:False,radio_admission=RADIO_ADMISSION)
        made.append(current)
        return current,now,directory
    yield create
    for current in made:current.close()


@pytest.fixture
def source_scope(monkeypatch):
    from lantern_meshticulum.bench.retained_capture import PAID_HISTORY
    # Declared static source model only; no native enrollment or physical grant.
    monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256='e'*64,history_receipt_sha256='f'*64,preceding_parent=PAID_HISTORY[-1])))


class ModelSerial:
    """Only codec-compatible HELLO/STATUS replies; no device, ARM or transmit."""
    version=mcu.FIRMWARE
    def __init__(self,*args,**kwargs):
        self.pending=b'';self.commands=[];self.closed=False
    def monotonic(self):return 50.0
    def write(self,raw,deadline):
        frame,=Decoder().feed(raw);self.commands.append(frame.type)
        if frame.type is Message.HELLO:
            reply=Frame(Message.HELLO_REPLY,MCU_BOOT,frame.request,
                struct.pack('!BBH',1,1,255)+self.version.encode()+b'\0')
        else:
            assert frame.type is Message.STATUS
            reply=Frame(Message.STATUS_REPLY,MCU_BOOT,frame.request,bytes(49))
        self.pending+=reply.encode()
    def read(self,timeout):
        raw,self.pending=self.pending,b'';return raw
    def close(self):self.closed=True


@pytest.mark.parametrize('name',mcu.LORA_PROFILE_NAMES)
def test_actual_authority_and_worker_use_020_without_arm_or_spending(authority,monkeypatch,source_scope,name):
    current,now,directory=authority(name);channels=[];checks=[]
    def channel(*args,**kwargs):
        value=ModelSerial(*args,**kwargs);channels.append(value);return value
    monkeypatch.setattr(node,'ExclusiveSerial',channel)
    chan,worker,status=node._serial('unit-a',qualification_kind=mcu.LORA_KIND,
        lora_mcu_artifact_selector=mcu.LORA_SELECTOR,lora_manifest=manifest(),lora_profile=current.profile,
        lora_authority=current,lora_parent_current=lambda:checks.append('fresh') or True)
    try:
        assert type(worker) is SerialWorker and worker.identity.version==mcu.FIRMWARE
        assert status['mcu_boot']==MCU_BOOT.hex() and status['disarmed'] is True
        assert chan.commands==[Message.HELLO,Message.STATUS] and len(checks)==2
        assert not current.owner_probe() and current.live() and worker.journal is None
        assert current.application_deadline_us==110000000 and current.grant.max_frames==512
        assert (directory/'started').is_file() and not (directory/'attempts.sqlite').exists()
    finally:chan.close()


@pytest.mark.parametrize('damage',['missing','expired','retired','grant-mutated','foreign-profile','parent-false','source','selector','route','pending'])
def test_no_serial_open_without_original_finite_authority_and_source(authority,monkeypatch,source_scope,damage):
    current,now,directory=authority('sf5-500')
    kwargs=dict(qualification_kind=mcu.LORA_KIND,lora_mcu_artifact_selector=mcu.LORA_SELECTOR,
        lora_manifest=manifest(),lora_profile=current.profile,lora_authority=current,lora_parent_current=lambda:True)
    if damage=='missing':kwargs['lora_authority']=None
    elif damage=='expired':now[:]=[160000000,110000000]
    elif damage=='retired':(directory/'active').write_bytes(b'retired\n')
    elif damage=='grant-mutated':(directory/'grant.json').write_bytes(b'{}')
    elif damage=='foreign-profile':kwargs['lora_profile']=deepcopy(current.profile)
    elif damage=='parent-false':kwargs['lora_parent_current']=lambda:False
    elif damage=='source':kwargs['lora_manifest']['source_files'].clear()
    elif damage=='selector':kwargs['lora_mcu_artifact_selector']=mcu.SELECTOR
    elif damage=='pending':monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256=None,history_receipt_sha256=None,preceding_parent=None)))
    else:kwargs['radio_modem']='gfsk250'
    monkeypatch.setattr(node,'ExclusiveSerial',lambda *a,**k:pytest.fail('USB must remain unopened'))
    with pytest.raises(ValueError):node._serial('unit-a',**kwargs)
    assert not (directory/'attempts.sqlite').exists()


def test_wrong_actual_hello_firmware_closes_owned_channel(authority,monkeypatch,source_scope):
    current,_,_=authority('sf7-500');channel=ModelSerial();channel.version='mtb1-b433927ba6'
    monkeypatch.setattr(node,'ExclusiveSerial',lambda *a,**k:channel)
    with pytest.raises(SerialError):
        node._serial('unit-a',qualification_kind=mcu.LORA_KIND,lora_mcu_artifact_selector=mcu.LORA_SELECTOR,
            lora_manifest=manifest(),lora_profile=current.profile,lora_authority=current,lora_parent_current=lambda:True)
    assert channel.closed and channel.commands==[Message.HELLO]


def test_readonly_inspection_has_separate_source_route_without_authority(monkeypatch):
    channel=ModelSerial();monkeypatch.setattr(node,'ExclusiveSerial',lambda *a,**k:channel)
    chan,worker,status=node._serial('unit-a',qualification_kind=mcu.LORA_KIND,
        lora_mcu_artifact_selector=mcu.LORA_SELECTOR,lora_manifest=manifest(),lora_readonly=True)
    try:assert status['disarmed'] and channel.commands==[Message.HELLO,Message.STATUS]
    finally:chan.close()


def test_build_selection_is_distinct_and_has_no_gfsk_fallback(monkeypatch):
    script=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench/build_runtime.py'
    monkeypatch.syspath_prepend(str(script.parent))
    spec=importlib.util.spec_from_file_location('lora_runtime_build_source_control',script)
    build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
    assert build.selected_mcu_artifact(mcu.LORA_RUNTIME_PROFILE,False,mcu.LORA_SELECTOR)==mcu.lora_artifact()
    assert build.selected_mcu_artifact('gfsk250',True,mcu.SELECTOR)==mcu.artifact()
    assert build.selected_mcu_artifact('gfsk125',False,None) is None
    for profile,fast,selector in [(mcu.LORA_RUNTIME_PROFILE,False,None),('gfsk250',True,mcu.LORA_SELECTOR),
        (mcu.LORA_RUNTIME_PROFILE,True,mcu.LORA_SELECTOR),(mcu.LORA_RUNTIME_PROFILE,False,mcu.SELECTOR)]:
        with pytest.raises(RuntimeError):build.selected_mcu_artifact(profile,fast,selector)
