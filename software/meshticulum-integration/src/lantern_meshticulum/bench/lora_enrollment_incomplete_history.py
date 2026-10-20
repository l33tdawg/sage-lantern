# SPDX-License-Identifier: Apache-2.0
"""Exact expired02ec incomplete enrollment, retained as debt, never live authority.

A/B captured history only. C created an uninitialized ledger before timeout.
Absent/schema-unavailable ledgers do not establish zero native claims or cost.
"""
from dataclasses import asdict
import hashlib
import json
import os
import re

from meshticulum.bench.wire import require
from .pilot_parent_caps import Cost, canonical
from . import capture_storage, fast_bundle_360_history as completed

PARENT='02ec9185d218e44740b30227a11bd7cd'
POLICY_DIGEST='0da052d8f4acf70e165bf54c912a36a8f934460bb9d85f11403f42a458119ecd'
POLICY_SHA256='6fcaf25b5fbff865b17fa961eec0ece4fd1ccbbf762c1ce31db7fee01367fefa'
OWNER_SHA256={'unit-a': '9bedbb4c8e46c4c335a42c43b15a56d62c49ca3ac5f04c0e39b628a1c1941de3', 'unit-b': '089fe13bab3b416204fac70efd9f683dd94adfbececaeb38a6de123c410bd360', 'unit-c': 'f25d0b2885843ba5ed2d2212961b73f62c05b4a9177e46ee8cf6f8646f3f06a7'}
PRIOR_SHA256={'unit-a': '8bbba9918a84434d80ff340200d817812097dc667f42eee90cf8bb73419d740e', 'unit-b': '316eb86638f72e0912aaf2242fc37d29a3621ab236987c216a523fa0a5787bc0', 'unit-c': 'd54aba5e8ab13f42645ec9d172361837aaf9306df52af046a8de7e386780311b'}
ORIGINAL_SCOPE_SHA256='52b0019974964144ad41121a5b46199df5d473a7cdfe7d9f08c6c89ead01cd02'
ORIGINAL_MCU_ARTIFACT_SHA256='4bb48e8de4fe1b70eccb6f644250018e98646a0e2642f28949166abd1b42fcb7'
ORIGINAL_HISTORY_RECEIPT_SHA256='cf01a817a18458f0ca2b91dca012afcc29044a9c65bb426ee9316e003cb32826'
ORIGINAL_RUNTIME_SHA256='47ec9c5406a3b3d49fef1df19653ebff8c84b02358e17a6f30189b3edf5081fb'
ORIGINAL_MANIFEST_SHA256='3ebbe052c2d69f47d42d06bc9659ae68f1e36e2d6e6b6b8dea48a0be360361a8'
ORIGINAL_PHASE_START=1792080961524446
ORIGINAL_PHASE_END=1792081561524446
ORIGINAL_ABSOLUTE_END=1792081668352208
ORIGINAL_OUTER_MONOTONIC_END=1169762224566
FINAL_RESULT_SHA256='5335ac8ae533c6a775203d4c0e8481db9da604831a66b34a83ae8cc062be8b8b'
ORIGINAL_OPERATION_SHA256='6d32c20d65de52fea21ac8b239dc0c1eed5f8dbfb79aa9f7cfee6b34aa6700da'
ORIGINAL_WINDOW_EXPIRY_SHA256='eee80fce57aafd8e2f662b947a874581e479b63540e9bd940b467c291490c5cf'
RAW_INTAKE_COMPLETE_SHA256='703a2055ce58f49514dea8ed63b2f556912b59fda87f07c7e78c9a4c7abec34b'
RAW_UNIT_SHA256={'unit-a': 'dc011392724852838e5e85a139d3eb65c64ea108eea678f6022fdba6c93a76bf', 'unit-b': '018fc7e8fd71a0aa826d48449029c5266da2f756b5605e0238d40cac51531b60', 'unit-c': 'c6ad6934f774aab083bd80d14844a5de0ba3312a6a8a60133293547abda9c32f'}
H46_HISTORY_SHA256={'unit-a': '46cc11e5c080f0c7da26dad5f0ad98764a233a593d37a849b9f8f4a88d820f83', 'unit-b': 'b91cdd98efd770afb7682e089c65ded983fbc86ac4e030f7069aebc7a69bf710', 'unit-c': '9b4ee7e8c2490162f6bce32483f7e4fbe91310ffc01a63eacd77e97cef6ca6b9'}
PUBLIC_FILES={'unit-a': {'parents/02ec9185d218e44740b30227a11bd7cd/capture.json': {'sha256': '159de61e584af8ec04b830e383e57715b140a9a91ea1c4fa88e4dbb60e31e4a7', 'bytes': 1856434}, 'parents/02ec9185d218e44740b30227a11bd7cd/history-binding.json': {'sha256': '97da42444942fdf6c2eed1c1c778e577a66d2b7a2fec08e6b40728e5f08bf115', 'bytes': 417}, 'parents/02ec9185d218e44740b30227a11bd7cd/history-identity-seal.json': {'sha256': '4d65375d887ba35786a9a9942d44d4950f221906b7b824d31c8c9ce1df78a276', 'bytes': 481726}, 'parents/02ec9185d218e44740b30227a11bd7cd/legacy-grant-seal.json': {'sha256': '0dfaff049fb4321872fc68c2b29460673805d72f728c8e3e8b3133e9a3c2082e', 'bytes': 67656}}, 'unit-b': {'parents/02ec9185d218e44740b30227a11bd7cd/capture.json': {'sha256': 'c17a1a0b7b19ae5ca9850efe907c907b03bece5e598823ff755035775fb1ed08', 'bytes': 2192373}, 'parents/02ec9185d218e44740b30227a11bd7cd/history-binding.json': {'sha256': '024963663e65aa5d403f10b693dedbe5748baf2403fcf73327fc4ff185d14e8a', 'bytes': 417}, 'parents/02ec9185d218e44740b30227a11bd7cd/history-identity-seal.json': {'sha256': '267e5aad0b8df7b50702ece90c8741d1c7505e50df206a617ece54dbf19dbb99', 'bytes': 208629}, 'parents/02ec9185d218e44740b30227a11bd7cd/legacy-grant-seal.json': {'sha256': '95539e6026afa5b68d01686e15c50661b2d3d1cca5079957d3c682e69f05859a', 'bytes': 102543}}, 'unit-c': {'parents/02ec9185d218e44740b30227a11bd7cd/capture.json': {'sha256': '24161534f1ff829fbbe912453ca062597f87ec0b7d5b2273564524e8a0c32f75', 'bytes': 2379231}, 'parents/02ec9185d218e44740b30227a11bd7cd/history-binding.json': {'sha256': '8a9011c846dcd23db3f7b2c99660a7a87c7bfd2bec4e32747f35240563e4d2e7', 'bytes': 418}, 'parents/02ec9185d218e44740b30227a11bd7cd/history-identity-seal.json': {'sha256': 'fb11e90359cf89191176cc903b77e82b99e69b1848086657821d32f031bbe8f0', 'bytes': 527474}, 'parents/02ec9185d218e44740b30227a11bd7cd/legacy-grant-seal.json': {'sha256': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc', 'bytes': 104449}, 'parents/02ec9185d218e44740b30227a11bd7cd/native.sqlite': {'sha256': '352a9bb23c329c5a9bf235baf56917358b5b64b03f1a11d261fadae9c3cdb721', 'bytes': 4096}, 'parents/02ec9185d218e44740b30227a11bd7cd/native.sqlite.approval': {'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 'bytes': 0}, 'parents/02ec9185d218e44740b30227a11bd7cd/native.sqlite.owner': {'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 'bytes': 0}, 'parents/02ec9185d218e44740b30227a11bd7cd/owner.json': {'sha256': 'f25d0b2885843ba5ed2d2212961b73f62c05b4a9177e46ee8cf6f8646f3f06a7', 'bytes': 10522}, 'parents/02ec9185d218e44740b30227a11bd7cd/policy.json': {'sha256': '6fcaf25b5fbff865b17fa961eec0ece4fd1ccbbf762c1ce31db7fee01367fefa', 'bytes': 2293}, 'parents/02ec9185d218e44740b30227a11bd7cd/prior.json': {'sha256': 'd54aba5e8ab13f42645ec9d172361837aaf9306df52af046a8de7e386780311b', 'bytes': 34591}}}
CONTROLLER_DATABASE_SHA256='d7fd4489d27f485330d045dfce1f03932c7e79d995914ffef6dce777a443f5d1'
NEXT_HISTORY_RECEIPT_SHA256='dcef33079caa071f9ff50dc044f2d42e41513133722ad4d80096de3fa09bdf0c'

H46_PREFIX=completed.H45_PREFIX+(completed.PARENT,)
OWNER_SCHEMA='meshticulum.public-radio-experiment-resume-owner.v1'
KIND='c-lora-sf7-sf5-8k-comparison-v1'


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def declared_partition(policy):
    require(policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and hashlib.sha256(canonical(policy.to_dict())).hexdigest()==POLICY_SHA256
        and policy.qualification_kind==KIND,'02ec_original_policy_changed')
    for local in policy.units:
        extra=Cost() if local.unit=='unit-b' else Cost(1024,240000000)
        require(local.prior_debt.plus(extra)==local.limits,'02ec_full_reservation_changed')
    return True


def owner_view(record,policy,*,unit):
    """Raw experimental schema stays raw; no current Scope or approval lookup."""
    declared_partition(policy)
    require(unit in OWNER_SHA256 and type(record)is dict and digest(record)==OWNER_SHA256[unit],
        '02ec_raw_experiment_owner_changed')
    require(record['schema']==OWNER_SCHEMA and record['policy_digest']==POLICY_DIGEST
        and record['qualification_kind']==KIND and record['comparison_scope_sha256']==ORIGINAL_SCOPE_SHA256
        and record['history_receipt_sha256']==ORIGINAL_HISTORY_RECEIPT_SHA256
        and digest(record['lora_mcu_artifact'])==ORIGINAL_MCU_ARTIFACT_SHA256
        and record['runtime_sha256']==ORIGINAL_RUNTIME_SHA256 and record['manifest_sha256']==ORIGINAL_MANIFEST_SHA256
        and record['actual_cost']is None and record['complete_global_debt']is False
        and all(record[k]is False for k in ('alpha_qualified','full_source_green','private_production_enabled',
            'physical_qualification','rf_qualified')),'02ec_historical_experiment_binding_changed')
    return record


def row_for(unit,policy,owner,prior):
    """Construct only the pinned historical row from actual original inputs."""
    owner_view(owner,policy,unit=unit);local=policy.unit(unit)
    require(type(prior)is dict and digest(prior)==PRIOR_SHA256[unit]
        and prior['previous_parent_ids']==list(H46_PREFIX)
        and prior['full_caps']==asdict(local.prior_debt)
        and prior['actual_cost']is None and prior['complete_global_debt']is False
        and prior['refunds']==0,'02ec_original_prior_changed')
    extra=Cost() if unit=='unit-b' else Cost(1024,240000000)
    return dict(policy=policy.to_dict(),policy_digest=POLICY_DIGEST,owner=owner,
        prior_kind='parent',prior_metadata=prior,database_sha256=(PUBLIC_FILES[unit].get('parents/'+PARENT+'/native.sqlite')or{}).get('sha256'),
        projection_keys=[],projection_sha256=None,controller_endpoint_refs=[],runs=[],retired_unstarted=[],
        preparation_sha256={},legacy_sha256=None,paid_caps=asdict(local.limits),actual_native_paid_caps=None,
        actual_controller_paid_caps=asdict(local.prior_debt),conservative_unused_controller_caps=asdict(extra),
        native_controller_debt_views_may_be_summed=False,require_result_complete=False,
        failed_started_runs=None,original_cleanup_proved=False,public_metadata_sha256={k:v['sha256']for k,v in PUBLIC_FILES[unit].items()},
        enrollment_incomplete=dict(schema='meshticulum.retained-enrollment-incomplete.v1',
            unit=unit,native_state=('schema-unavailable-partial-ledger'if unit=='unit-c'else'capture-only'),
            known_run_scopes=[],prepared_identity_count=0,actual_claim_resource_prepared_start_counts=None,
            actual_cost=None,no_refund=True,physical_quiescence_observed=False,callback_terminal_proved=False,
            raw_intake_complete_sha256=RAW_INTAKE_COMPLETE_SHA256,raw_unit_sha256=RAW_UNIT_SHA256[unit],
            original_result_sha256=FINAL_RESULT_SHA256,original_operation_sha256=ORIGINAL_OPERATION_SHA256,
            original_window_expiry_sha256=ORIGINAL_WINDOW_EXPIRY_SHA256))


def qualify_row(unit,policy,value):
    require(type(value)is dict and canonical(value)==canonical(row_for(unit,policy,value['owner'],value['prior_metadata'])),
        '02ec_incomplete_enrollment_or_debt_changed')
    return value['paid_caps']


def require_final_bindings():
    require(type(NEXT_HISTORY_RECEIPT_SHA256)is str and re.fullmatch('[0-9a-f]{64}',NEXT_HISTORY_RECEIPT_SHA256),
        'pending_02ec_H47_history_receipt')
    return True


def _names(reader,path):
    reader.directory(path)
    fd=os.open('.',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=reader._nodes[path][0])
    try:return sorted(os.listdir(fd))
    finally:os.close(fd);reader.directory(path)


def metadata_files(reader,state,unit,value,*,guard,clock,current_boot,previous_history):
    """Fresh exact public bytes and empty identities; no native constructors/SQL."""
    from .pilot_parent_caps import ParentPolicy
    policy=ParentPolicy.from_dict(value['policy']);qualify_row(unit,policy,value)
    owner=owner_view(value['owner'],policy,unit=unit);root=state/'parents'/PARENT;prefix='parents/'+PARENT+'/'
    expected=PUBLIC_FILES[unit];names=sorted(k[len(prefix):]for k in expected)+['prepared-identities']
    def current():
        guard();sample=clock();original=policy.unit(unit).host_boot_id;now_boot=current_boot()
        from .pilot_parent_caps import boot
        boot(now_boot)
        end=owner['native_phase'][unit]['deadline_monotonic_us']+ORIGINAL_ABSOLUTE_END-ORIGINAL_PHASE_END
        require(type(sample)is tuple and len(sample)==2 and all(type(v)is int and v>0 for v in sample)
            and sample[0]>ORIGINAL_ABSOLUTE_END and (now_boot!=original or sample[1]>end),
            '02ec_original_clocks_not_expired')
    current();require(_names(reader,root)==sorted(names)
        and _names(reader,root/'prepared-identities')==[],'02ec_incomplete_namespace_changed')
    files={};raws={}
    for relative,pin in expected.items():
        current();path=state/relative
        raw=reader.read_held(path,pin['bytes'],lock=path.name in ('native.sqlite.owner','native.sqlite.approval'))
        require(len(raw)==pin['bytes']and hashlib.sha256(raw).hexdigest()==pin['sha256'],
            '02ec_actual_public_metadata_changed')
        files[relative]=pin['sha256'];raws[path.name]=raw
    capture=json.loads(capture_storage.decode(raws['capture.json']))
    require(capture['schema']=='meshticulum.private-resume-actual-capture.v1'
        and capture['parent_id']==PARENT and capture['unit']==unit and capture['actual_history_verified']is True
        and capture['global_debt_complete']is False and capture['original_unknown_actual_cost']is None and capture['refunds']==0
        and digest(capture['history'])==H46_HISTORY_SHA256[unit]
        and canonical(capture['history'])==canonical(previous_history)
        and tuple(v['policy']['parent_id']for v in capture['history']['previous'])==H46_PREFIX
        and capture['native_phase']==owner['native_phase'][unit]
        and capture['history_binding_sha256']==owner['history_binding_sha256'],
        '02ec_original_capture_or_H46_prefix_changed')
    if unit=='unit-c':
        require(raws['policy.json']==canonical(value['policy']) and raws['owner.json']==canonical(value['owner'])
            and raws['prior.json']==canonical(value['prior_metadata']),'02ec_partial_original_input_changed')
    require(_names(reader,root)==sorted(names) and _names(reader,root/'prepared-identities')==[],
        '02ec_incomplete_namespace_changed_after_read')
    reader.check();current();return files


def controller_caps(unit,value,observation):
    """Controller allocation0 is distinct from unknown native enrollment/cost."""
    from .pilot_parent_caps import ParentPolicy
    policy=ParentPolicy.from_dict(value['policy']);qualify_row(unit,policy,value)
    detail=observation['detail'];sample=(observation['observation_original_epoch_us'],observation['observation_original_monotonic_us'])
    require(observation['role']=='controller'and observation['parent_id']==PARENT
        and observation['policy_digest']==POLICY_DIGEST and observation['expected_database_sha256']==CONTROLLER_DATABASE_SHA256
        and observation['status']=='scoped_rows_observed'and observation['scoped_rows_complete']is True
        and observation['policy_expired_at_observation']is True and observation['pending_unknown']is False
        and observation['database_writes']is False and type(detail['allocation_count'])is int and detail['allocation_count']==0
        and detail['allocations']==[] and all(type(v)is int and v>0 for v in sample)
        and sample[0]>ORIGINAL_ABSOLUTE_END
        and (observation['current_host_boot_id']!=policy.controller_boot_id or sample[1]>ORIGINAL_OUTER_MONOTONIC_END),
        '02ec_controller_scope_or_expiry_changed')
    require(len(detail['units'])==3 and {v['unit']for v in detail['units']}==set(OWNER_SHA256)
        and all(canonical(v['limit'])==canonical(asdict(policy.unit(v['unit']).limits))
            and canonical(v['prior_plus_prepaid_caps'])==canonical(asdict(policy.unit(v['unit']).prior_debt))
            and canonical(v['full_prepaid_caps'])==canonical(asdict(Cost()))for v in detail['units']),
        '02ec_controller_prior_only_reservation_changed')
    return value['paid_caps']
