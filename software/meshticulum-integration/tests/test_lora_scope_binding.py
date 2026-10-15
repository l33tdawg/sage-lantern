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

from lantern_meshticulum.bench import mcu_artifact as mcu,lora_enrollment_incomplete_history as incomplete,lora_endpoint_incomplete_history as endpoint,lora_management_incomplete_history as management,management_input
from lantern_meshticulum.bench.profiles import profile_for
from lantern_meshticulum.bench.retained_capture import PAID_HISTORY

EXPECTED_CORE = 'b9ce259a915e1336ee2eda5c2cbe413d370e6b9d'
EXPECTED_PREFIX = '224d77bd0eccbbe83e04acf9ba703d79a3cfe832'
EXPECTED_SCOPE_SHA = '8919e7264027fe054276c48e8ed53e4c10dbaaafc6852d95ad204fd09f46adb3'
EXPECTED_LOCK_SHA = '3b37a55820ed9b7759359778a42b302f0e57500c328ec076023983d5ed05de76'
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
    lantern_source=Path(__file__).resolve().parents[3]
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
    assert scope['paid_history_count']==len(PAID_HISTORY)==49
    assert scope['history_parent_ids']==list(PAID_HISTORY)
    assert scope['preceding_parent']==pins['preceding_parent']==PAID_HISTORY[-1]==management.PARENT
    assert scope['history_receipt_sha256']==pins['history_receipt_sha256']==management.NEXT_HISTORY_RECEIPT_SHA256=='20550444f2a434ad64f40a3d1c019df4066572823e1c797529e5f5e11074dfd0'
    assert management_input.RAW_MAX==2750000
    history_root=Path(os.environ['MESHTICULUM_H49_FIXTURE'])/'resume-history-preparation-49'
    receipt_raw=read(history_root/'RECEIPT.json');receipt=json.loads(receipt_raw)
    assert sha(receipt_raw)==management.NEXT_HISTORY_RECEIPT_SHA256
    assert receipt['schema']=='meshticulum.local-forty-nine-paid-parent-history-preparation.v1'
    assert receipt['latest_parent_id']==management.PARENT
    assert receipt['local_controllers_reobserved']==49 and receipt['native_counts']==management.NATIVE_COUNTS
    assert receipt['original_run_complete']is receipt['original_cleanup_proved']is False
    assert receipt['no_refund']is True and receipt['actual_unknown_cost']is None
    proof_raw=read(history_root/'INPUT_PROOF.json');proof=json.loads(proof_raw)
    assert sha(proof_raw)==receipt['input_proof_sha256']==scope['history_input_proof']['sha256']=='1fbb0f75c19eb929869fa7afdcb2eb6f32ed833a36e5dbb8b91837f191914722'
    assert proof['schema']=='meshticulum.exact-H49-management-failure-input-proof.v1'
    assert proof['parents']==list(PAID_HISTORY) and proof['latest_parent_id']==management.PARENT
    assert proof['native_counts']==management.NATIVE_COUNTS and proof['actual_cost']is None
    assert proof['no_refund']is True and proof['global_debt_complete']is False
    payloads={}
    for unit,pin in receipt['payload_sha256'].items():
        raw=read(history_root/(unit+'.json'),management_input.RAW_MAX)
        assert sha(raw)==pin
        payloads[unit]=json.loads(raw)
    assert scope['full_H49_prior_caps']==proof['full_paid_caps']==receipt['full_paid_caps']=={u:value['previous'][-1]['paid_caps']for u,value in payloads.items()}
    retained48=scope['retained_H48_binding']
    assert retained48['history_receipt_sha256']==management.ORIGINAL_HISTORY_RECEIPT_SHA256==endpoint.NEXT_HISTORY_RECEIPT_SHA256
    assert retained48['preceding_parent']==endpoint.PARENT
    old48_root=Path(os.environ['MESHTICULUM_H48_FIXTURE'])/'resume-history-preparation-48'
    old48_raw=read(old48_root/'RECEIPT.json');old48_receipt=json.loads(old48_raw)
    assert sha(old48_raw)==retained48['history_receipt_sha256'] and old48_receipt['native_counts']==endpoint.NATIVE_COUNTS
    assert old48_receipt['local_controllers_reobserved']==48
    assert retained48['full_H48_prior_caps']=={u:json.loads(read(old48_root/(u+'.json'),2500000))['previous'][-1]['paid_caps']for u in old48_receipt['payload_sha256']}
    assert scope['retained_H46_binding']['history_receipt_sha256']==incomplete.ORIGINAL_HISTORY_RECEIPT_SHA256
    assert scope['retained_consumed_attempt']['parent_id']==incomplete.PARENT
    assert scope['retained_consumed_attempt']['native_counts']is None
    assert scope['required_inputs']['fresh_full49_preflight']is True
    assert scope['required_inputs']['actual_consumed_1a02_window_expiry']is True
    assert scope['required_inputs']['actual_consumed_ea4d_window_expiry']is True
    retained=scope['retained_H47_binding']
    assert retained['history_receipt_sha256']==endpoint.ORIGINAL_HISTORY_RECEIPT_SHA256==incomplete.NEXT_HISTORY_RECEIPT_SHA256
    assert retained['preceding_parent']==incomplete.PARENT
    old_root=Path(os.environ['MESHTICULUM_H47_FIXTURE'])/'resume-history-preparation-47'
    old_receipt_raw=read(old_root/'RECEIPT.json');old_receipt=json.loads(old_receipt_raw)
    assert sha(old_receipt_raw)==retained['history_receipt_sha256'] and old_receipt['native_counts']is None
    assert retained['full_H47_prior_caps']=={u:json.loads(read(old_root/(u+'.json'),2500000))['previous'][-1]['paid_caps']for u in old_receipt['payload_sha256']}
    actual=scope['retained_consumed_ea4d_attempt']
    assert actual['parent_id']==endpoint.PARENT and actual['native_counts']==endpoint.NATIVE_COUNTS
    assert actual['actual_cost']is None and actual['physical_quiescence_observed']is False and actual['refunds']==0
    consumed=scope['retained_consumed_1a02_attempt']
    assert consumed['parent_id']==management.PARENT and consumed['native_counts']==management.NATIVE_COUNTS
    assert consumed['actual_cost']is None and consumed['physical_quiescence_observed']is False and consumed['refunds']==0
    old_scope_raw=read(Path(consumed['scope']['path']));old_scope=json.loads(old_scope_raw)
    assert sha(old_scope_raw)==consumed['scope']['sha256']==management.ORIGINAL_SCOPE_SHA256
    old_operation_raw=read(Path(consumed['operation']['path']));old_operation=json.loads(old_operation_raw)
    assert sha(old_operation_raw)==consumed['operation']['sha256']==management.ORIGINAL_OPERATION_SHA256
    freshness=scope['campaign_freshness']
    assert freshness['schema']=='meshticulum.reviewed-comparison-campaign-freshness.v1'
    assert freshness['source_scope_sha256']==consumed['scope']['sha256']
    assert freshness['original_context_reuse_permitted']is freshness['window_issued']is False
    retained_ids=set();pending=[*payloads.values(),old_scope,old_operation]
    while pending:
        value=pending.pop()
        if type(value)is dict:
            campaign=value.get('campaign_id')
            if type(campaign)is str and len(campaign)==32 and all(c in '0123456789abcdef'for c in campaign):
                retained_ids.add(campaign)
            pending.extend(value.values())
        elif type(value)is list:
            pending.extend(value)
    assert len(retained_ids)==138 and freshness['retained_known_campaign_ids']==sorted(retained_ids)
    campaigns=[sample['campaign_id']for sample in scope['comparison_descriptor']['samples']]
    assert campaigns==freshness['new_campaign_ids'] and len(set(campaigns))==2
    assert all(type(v)is str and len(v)==32 and all(c in '0123456789abcdef'for c in v) and int(v,16)>0 for v in campaigns)
    assert set(campaigns).isdisjoint(retained_ids)
    restored=json.loads(json.dumps(scope['comparison_descriptor']))
    for sample,old in zip(restored['samples'],old_scope['comparison_descriptor']['samples']):
        sample['campaign_id']=old['campaign_id']
    assert restored==old_scope['comparison_descriptor']==old_operation['comparison_descriptor']
    assert scope['sender_readiness_source']['sha256']==sha(read(lantern_source/'software/meshticulum-integration/src/lantern_meshticulum/bench/node.py',200000))
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
