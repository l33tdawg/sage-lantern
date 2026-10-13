"""Exact 9f90 prepared, unstarted history; full reservation and no new RF authority."""
import hashlib
import os
from dataclasses import asdict
from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy,Cost,canonical
from .pilot_bundle import BundleContext,public_context_commitment
from .profiles import GFSK250_FAST_BUNDLE_KIND,GFSK250_NAME,profile_for,profile_record
from .retained_capture import PAID_HISTORY

PARENT='9f90bdec3df97edbbe1e77cfc8ab836e'
POLICY_DIGEST='b69fa11456944fc1bd3d35983eefc50e5dddf0b666d9d9cb2ab036bca4e04ad1'
POLICY_SHA256='41e7c910bb779380484b439ab2035ac5587d0819ff9abeb5c6dffc549ae90b2d'
OWNER_SHA256={'unit-a': 'd8f4e78b8c4fed5b715f21ea92a93e6785f383625b6cc786663411b0d5e8fc03', 'unit-b': 'f880d3cce2166f55b32225ff5d61b3d3a6c9ab78030ae23fc898fdd5cc23336e', 'unit-c': '35d3378273a617f5bd723822a762de766c702374ab4cce2dbc86c94da114ef7c'}
ORIGINAL_SCOPE_SHA256='0b02997bd6e118af967f9a0acd20d6b3a618ab520c86f7ed827cd4f9d2a6cefd'
ORIGINAL_RUNTIME_SHA256='42f63f27fdb6ff96028dfa092c9d95a5b135f62ce362ad5abf51fc36f3a50b2c'
ORIGINAL_MANIFEST_SHA256='7b7414bdbde91eed24fdc375263a8f906387648d8ea2255f2830abce3e0162e4'
ORIGINAL_CORE_COMMIT='a45c051aafd0be061f5b8160687277fe2b5eaca9'
ORIGINAL_PHASE_END=1791866984758171
from .fast_bundle_zero_history import H38_PREFIX, PARENT as H39_LAST
H39_PREFIX=H38_PREFIX+(H39_LAST,)
RUN='1140ada832368459a91a5d390d7c3e52'
ORIGINAL_ABSOLUTE_END=1791867089798337
FINAL_RESULT_SHA256='eeabb80708fd2202dc3a43e214d356127231c3b77b1ba90f21821e9b5923de31'
EXPIRY_COMPLETION_SHA256='ecf611c2d1fc1fb01909d17a560b3a323e79ca81a4a054c5fe27e4cd5f898b2d'
NEXT_HISTORY_RECEIPT_SHA256='96d0e323dca4da489f4798c8f07e1378d327af67640b10a60d60ce04a6e68771'
NATIVE_DATABASE_SHA256={'unit-a': '9ee5ffa6369622fc0075e22f473feaeb2d0e90328e3ab00b91704edac47981fa', 'unit-b': 'f610523b4872a120931a66cc4522803fe0c6b20985fa3ae2eed2a1636cb6805d', 'unit-c': '887397a9ca31ee4b95dd156f87f08ceaf214753de805ee23925b51239b4a9c17'}

def _sha(value):return hashlib.sha256(canonical(value)).hexdigest()

def declared_partition(policy):
    """Full original reservations, never actual consumption or refundable credit."""
    require(type(policy) is ParentPolicy and policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and _sha(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND,
        '9f90_original_policy_changed')
    rows={}
    for unit in policy.units:
        extra=Cost() if unit.unit=='unit-b' else Cost(2560,600000000)
        require(unit.limits==unit.prior_debt.plus(extra),'9f90_original_full_caps_changed')
        rows[unit.unit]=dict(full_original_caps=asdict(unit.limits),prior_caps=asdict(unit.prior_debt),
            declared_new_reservation=asdict(extra),actual_consumption=None,refunds=0)
    return rows

def original_context(owner,policy,*,unit):
    """Exact original signed data/clock decoding, with no admission side effect."""
    declared_partition(policy)
    require(unit in OWNER_SHA256 and type(owner) is dict and _sha(owner)==OWNER_SHA256[unit]
        and owner['policy_digest']==POLICY_DIGEST and owner['qualification_kind']==GFSK250_FAST_BUNDLE_KIND
        and owner['radio_profile_name']==owner['radio_modem']==GFSK250_NAME
        and owner['rf_profile']==profile_record(profile_for(GFSK250_NAME))
        and owner['mb1_bundle_scope_sha256']==ORIGINAL_SCOPE_SHA256
        and owner['runtime_sha256']==ORIGINAL_RUNTIME_SHA256 and owner['manifest_sha256']==ORIGINAL_MANIFEST_SHA256
        and owner['phase_start_epoch_us']==policy.issued_epoch_us and owner['phase_end_epoch_us']==ORIGINAL_PHASE_END,
        '9f90_original_owner_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(context.transport is not None,'9f90_exact_MB1_transport_required')
    require(owner['bundle_capture_commitment']==public_context_commitment(owner['bundle_context'],parent_id=PARENT),
        '9f90_original_capture_commitment_changed')
    return context

def retained_context(owner,policy,*,unit,observation):
    """Pure expired-context predicate; an actual collector remains mandatory."""
    context=original_context(owner,policy,unit=unit)
    require(type(observation) is dict and observation.get('status')=='scoped_rows_observed'
        and observation.get('parent_id')==PARENT and observation.get('policy_digest')==POLICY_DIGEST
        and observation.get('unit')==unit and observation.get('policy_expired_at_observation') is True,
        '9f90_actual_expired_scope_required')
    epoch,mono=(observation.get(k) for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
    require(type(epoch) is int and type(mono) is int
        and epoch>=ORIGINAL_ABSOLUTE_END>=ORIGINAL_PHASE_END>=policy.expires_epoch_us
        and mono>=owner['native_phase'][unit]['deadline_monotonic_us']+ORIGINAL_ABSOLUTE_END-ORIGINAL_PHASE_END,'9f90_original_clocks_live')
    context._check();return context

def require_final_bindings():
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (FINAL_RESULT_SHA256,EXPIRY_COMPLETION_SHA256,NEXT_HISTORY_RECEIPT_SHA256))
        and set(NATIVE_DATABASE_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in NATIVE_DATABASE_SHA256.values()),'pending_9f90_actual_database_expiry_history')
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)==40 and PAID_HISTORY[:39]==H39_PREFIX and PAID_HISTORY[39]==PARENT,
        '9f90_H39_prefix_changed')
    return PAID_HISTORY[:40]


def qualify_costs(unit,policy,observed,value):
    """Retain the whole enrollment; one paid claim is not measured radio use."""
    require_final_bindings()
    retained_context(value['owner'],policy,unit=unit,observation=observed)
    detail=observed['detail'];local=policy.unit(unit);count=0 if unit=='unit-b' else 1
    claimed=Cost() if not count else Cost(256,60000000)
    unused=Cost() if not count else Cost(2304,540000000)
    require(all(type(detail[k]) is int and detail[k]==count for k in
        ('claim_count','prepared_count','resource_count'))
        and observed['scoped_rows_complete'] is True and observed['pending_unknown'] is False
        and type(detail['start_count']) is int and detail['start_count']==0
        and _sha(detail['claims'])==CLAIMS_SHA256[unit]
        and _sha(detail['resources'])==RESOURCES_SHA256[unit]
        and detail['full_prepaid_caps']==asdict(claimed)
        and detail['approval_bytes_match_enrollment'] is True
        and detail['enrollment']['history_binding_sha256']==value['owner']['history_binding_sha256']
        and observed['observation_original_monotonic_us']>=detail['enrollment']['deadline_monotonic_us']
        and observed['expected_database_sha256']==value['database_sha256']==NATIVE_DATABASE_SHA256[unit]
        and observed['legacy_runs'] is None and observed['legacy_relation'] is None,
        '9f90_actual_rows_changed')
    require(detail['prior_plus_prepaid_caps']==asdict(local.prior_debt.plus(claimed))
        ==value['actual_native_paid_caps']==value['actual_controller_paid_caps']
        and detail['limit']==asdict(local.limits)
        and value['conservative_unused_enrollment_caps']==asdict(unused)
        and value['paid_caps']==asdict(local.limits), '9f90_conservative_bound_changed')
    refs=[dict(controller_allocation_id=c['controller_allocation_id'],child=c['child'])
          for c in detail['claims']]
    require(value['runs']==value['failed_started_runs']==[] and value['legacy_sha256'] is None
        and value['require_result_complete'] is False and value['controller_endpoint_refs']==refs
        and value['preparation_sha256']==({RUN:PREPARATION_SHA256[unit]} if count else {})
        and value['fast_stage_sha256']==({'fast-stage-0.json':FAST_STAGE_SHA256[unit]} if count else {})
        and len(value['retired_unstarted'])==count,'9f90_retention_scope_changed')
    if count:
        claim=detail['claims'][0];resource=detail['resources'][0];row=value['retired_unstarted'][0]
        require(claim['prepared'] is True and claim['started'] is False and claim['start_row_sha256'] is None
            and claim['prepared_row_sha256']==PREPARED_SHA256[unit]
            and claim['child']['run_id']==row['run_id']==RUN and row['transport']=='lora'
            and row['host_boot_id']==local.host_boot_id
            and type(row['grant_sha256']) is str and len(row['grant_sha256'])==64
            and row['prepared_identity_sha256']==PREPARATION_SHA256[unit]
            and resource['fast_stage_sha256']==FAST_STAGE_SHA256[unit]
            and observed['observation_original_monotonic_us']>=resource['fast_stage_deadline_monotonic_us'],
            '9f90_prepared_stage_changed')


def retired_metadata(reader,state,unit,policy,observed,row,now,boot_reader):
    """Read the exact public prepared metadata and stage, never keys or payload."""
    from .pilot_resume_rpc import parse,sha,_child_ref
    declared_partition(policy)
    require(unit in ('unit-a','unit-c') and row['run_id']==RUN and row['transport']=='lora',
        '9f90_retired_scope_changed')
    claims=observed['detail']['claims'];resources=observed['detail']['resources']
    require(_sha(claims)==CLAIMS_SHA256[unit] and _sha(resources)==RESOURCES_SHA256[unit],
        '9f90_retired_native_rows_changed')
    claim=claims[0];resource=resources[0];target=state/'runs'/RUN;parent=state/'parents'/PARENT;files={}
    def public(path,maximum,lock=False):
        raw=reader.read(path,maximum,lock=lock);files[str(path.relative_to(state))]=sha(raw);return raw
    reader.directory(target)
    # A prepared receiver has no management-delivered sender payload.
    expected={'grant.json','active','provision.json','secret.bin'}|({'sender.bin'} if unit=='unit-a' else set())
    require(set(os.listdir(target))==expected,'9f90_retired_runtime_namespace_changed')
    grant_raw=public(target/'grant.json',16384);plan=parse(grant_raw)
    provision_raw=public(target/'provision.json',32768);provision=parse(provision_raw);receipt=provision['parent']
    require(grant_raw==canonical(plan)+b'\n' and sha(grant_raw)==row['grant_sha256']
        and _sha(plan)==claim['native_plan_sha256']
        and _sha(receipt)==PREPARED_SHA256[unit]==claim['prepared_row_sha256']
        and _child_ref(receipt['child_caps'])==claim['child'] and receipt['prepared'] is True
        and receipt['native_claim_id']==claim['native_claim_id']
        and receipt['parent_id']==PARENT and receipt['policy_digest']==POLICY_DIGEST
        and receipt['unit']==unit and receipt['run_id']==RUN
        and receipt['host_boot_id']==policy.unit(unit).host_boot_id==boot_reader()
        and receipt['native_plan_sha256']==claim['native_plan_sha256']
        and provision['schema']=='meshticulum.provisioned-public-pilot.v2'
        and provision['unit']==unit and provision['run_id']==RUN and provision['transport']=='lora',
        '9f90_retired_provision_changed')
    meta=receipt['child_caps']['metadata'];sample=now()
    require(plan['schema']=='meshticulum.service-run.v1' and plan['unit']==unit and plan['session_id']==RUN
        and plan['host_boot_id']==receipt['host_boot_id'] and plan['service']['format']=='MR1.request'
        and plan['service']['application_expires_epoch_us']==receipt['application_expires_epoch_us']
        and plan['expires_epoch_us']<sample[0] and receipt['application_expires_epoch_us']<sample[0]
        and plan['application_deadline_monotonic_us']==receipt['application_deadline_monotonic_us']<sample[1]
        and _sha({k:v for k,v in plan.items() if k!='application_deadline_monotonic_us'})==receipt['original_plan_sha256']==meta['plan_sha256']
        and plan['profile']==meta['profile'] and {k:plan['object'][k] for k in ('bytes','sha256')}==meta['object']
        and public(target/'active',128,lock=True)==b'retired\n', '9f90_retired_plan_or_clock_changed')
    identity_raw=public(parent/'prepared-identities'/(RUN+'.json'),4096);identity=parse(identity_raw)
    require(sha(identity_raw)==row['prepared_identity_sha256']==PREPARATION_SHA256[unit]
        and identity['parent_id']==PARENT and identity['policy_digest']==POLICY_DIGEST
        and identity['unit']==unit and identity['run_id']==RUN
        and identity['directory_identity']==reader.directories[target]
        and identity['prepared_sha256']==_sha(receipt) and identity['provision_sha256']==sha(provision_raw),
        '9f90_retired_identity_changed')
    stage_raw=public(parent/'fast-stage-0.json',8192);stage=parse(stage_raw)
    require(stage_raw==canonical(stage) and sha(stage_raw)==FAST_STAGE_SHA256[unit]==resource['fast_stage_sha256']
        and stage['schema']=='meshticulum.native-fast-part-admission.v1'
        and stage['parent_id']==PARENT and stage['policy_digest']==POLICY_DIGEST and stage['unit']==unit
        and type(stage['index']) is int and stage['index']==0
        and stage['bundle_context_sha256']==policy.bundle_context_sha256==resource['bundle_context_sha256']
        and stage['campaign_id']==resource['campaign_id']==meta['campaign_id']
        and stage['expires_epoch_us']==resource['application_expires_epoch_us']==receipt['application_expires_epoch_us']
        and stage['native_deadline_monotonic_us']==resource['fast_stage_deadline_monotonic_us']<sample[1]
        and stage['expires_epoch_us']<sample[0] and stage['previous_verified_proof_sha256'] is None,
        '9f90_retired_fast_stage_changed')
    reader.check()
    return dict(run_id=RUN,prepared=True,started=False,retired=True,actual_cost=None,
        full_prepaid_caps=claim['child']['full_caps'],no_refund=True,separate_debt_sum_permitted=False,
        physical_quiescence_observed=False,live_terminal_ownership_observed=False,files=files)


def stage_files(reader,state,unit,value):
    """Bind the one original stage, including exact absence on the audit peer."""
    parent=state/'parents'/PARENT;reader.directory(parent)
    expected={} if unit=='unit-b' else {'fast-stage-0.json':FAST_STAGE_SHA256[unit]}
    require(unit in FAST_STAGE_SHA256 and value['fast_stage_sha256']==expected
        and {name for name in os.listdir(parent) if name.startswith('fast-stage-')}==set(expected),
        '9f90_stage_namespace_changed')
    files={}
    for name,pin in expected.items():
        raw=reader.read(parent/name,8192)
        require(hashlib.sha256(raw).hexdigest()==pin,'9f90_stage_bytes_changed')
        files[str((parent/name).relative_to(state))]=pin
    reader.check();return files

PREPARED_SHA256={'unit-a': '113e494e54894c519ff68da45a5f06037c00ca6947d06d8c747794441414cdd8', 'unit-c': '829a2a9fec26eeee7c8ccb480f57ec95ff4046efdc17993d2afcae499f4b5878'}
PREPARATION_SHA256={'unit-a': '6c79d873812a6710e7bcde3a20ea3e62153a12bebb8741abc83ea37eeef4f08d', 'unit-b': None, 'unit-c': 'b964405b5b6f901fed0509a6b8cc7eb2d85f6face36bf82aec13351f33ebbde9'}
FAST_STAGE_SHA256={'unit-a': 'ad772fc357feb3c985120849423e713541615222f12b525119b15459ad091bc2', 'unit-b': None, 'unit-c': 'bff1755ee62c997d9fff1484a836b19add1e4464e8c8b4eb98c748b55e569168'}
CLAIMS_SHA256={'unit-a': 'b880e75f37ad354a5fb63de0c1db34545f38cc27c5867445f065b0c54498dc31', 'unit-b': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945', 'unit-c': '63f1c5f7e309cd60a9a13db049afa1cf698a6bd0a92d6ccf699be9a62616a9fd'}
RESOURCES_SHA256={'unit-a': 'eba60b6989aa5b0d2056d9e773e69db99748d52b968cbb67ce0f1ecfc9713a7c', 'unit-b': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945', 'unit-c': '060d12019cb2e8d76ea4132b33c541e633298c99c27b28608b06bd60c633b37c'}
