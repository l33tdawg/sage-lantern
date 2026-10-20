"""Exact 9f90 prepared, unstarted history; full reservation and no new RF authority."""
import hashlib
import os
import re
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

QUALIFICATION_KIND=GFSK250_FAST_BUNDLE_KIND
from . import gfsk250_mb1_partial_history as shared
from functools import partial
import sys
declared_partition=partial(shared._declared_partition,sys.modules[__name__])
original_context=partial(shared._original_context,sys.modules[__name__])
retained_context=partial(shared._retained_context,sys.modules[__name__])

def require_final_bindings():
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (FINAL_RESULT_SHA256,EXPIRY_COMPLETION_SHA256,NEXT_HISTORY_RECEIPT_SHA256))
        and set(NATIVE_DATABASE_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in NATIVE_DATABASE_SHA256.values()),'pending_9f90_actual_database_expiry_history')
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)>=40 and PAID_HISTORY[:39]==H39_PREFIX and PAID_HISTORY[39]==PARENT,
        '9f90_H39_prefix_changed')
    return PAID_HISTORY[:40]


def qualify_costs(unit,policy,observed,value):
    """Retain the whole enrollment; one paid claim is not measured radio use."""
    from .pilot_resume_rpc import historical_monotonic_expired
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
        and historical_monotonic_expired(policy,unit,observed,detail['enrollment']['deadline_monotonic_us'])
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
            and historical_monotonic_expired(policy,unit,observed,resource['fast_stage_deadline_monotonic_us']),
            '9f90_prepared_stage_changed')


def retired_metadata(reader,state,unit,policy,observed,row,now,boot_reader):
    """Read the exact public prepared metadata and stage, never keys or payload."""
    from .pilot_resume_rpc import parse,sha,_child_ref,historical_clock_expired
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
        and receipt['host_boot_id']==policy.unit(unit).host_boot_id
        and receipt['native_plan_sha256']==claim['native_plan_sha256']
        and provision['schema']=='meshticulum.provisioned-public-pilot.v2'
        and provision['unit']==unit and provision['run_id']==RUN and provision['transport']=='lora',
        '9f90_retired_provision_changed')
    meta=receipt['child_caps']['metadata'];sample=now()
    require(plan['schema']=='meshticulum.service-run.v1' and plan['unit']==unit and plan['session_id']==RUN
        and plan['host_boot_id']==receipt['host_boot_id'] and plan['service']['format']=='MR1.request'
        and plan['service']['application_expires_epoch_us']==receipt['application_expires_epoch_us']
        and plan['expires_epoch_us']<sample[0] and receipt['application_expires_epoch_us']<sample[0]
        and plan['application_deadline_monotonic_us']==receipt['application_deadline_monotonic_us']
        and historical_clock_expired(receipt['host_boot_id'],receipt['application_expires_epoch_us'],
            receipt['application_deadline_monotonic_us'],sample,boot_reader())
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
        and stage['native_deadline_monotonic_us']==resource['fast_stage_deadline_monotonic_us']
        and historical_clock_expired(receipt['host_boot_id'],stage['expires_epoch_us'],
            stage['native_deadline_monotonic_us'],sample,boot_reader())
        and stage['expires_epoch_us']<sample[0] and stage['previous_verified_proof_sha256'] is None,
        '9f90_retired_fast_stage_changed')
    reader.check()
    return dict(run_id=RUN,prepared=True,started=False,retired=True,actual_cost=None,
        full_prepaid_caps=claim['child']['full_caps'],no_refund=True,separate_debt_sum_permitted=False,
        physical_quiescence_observed=False,live_terminal_ownership_observed=False,files=files)


def stage_files(reader,state,unit,value,contract=None):
    """Bind the one original stage, including exact absence on the audit peer."""
    parent=state/'parents'/(PARENT if contract is None else contract.PARENT)
    pins=FAST_STAGE_SHA256 if contract is None else contract.FAST_STAGE_SHA256;reader.directory(parent)
    expected={} if unit=='unit-b' else {'fast-stage-0.json':pins[unit]}
    require(unit in pins and value['fast_stage_sha256']==expected
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

# Exact later prepared/unmaterialized case. Original 9f90 rules above stay intact.
UNMATERIALIZED_PARENT='e2449e5bcd4af15d45f7a756dff7d997'
UNMATERIALIZED_RUN='911fb4a3f81bfe8cbf785c3d26d68aea'
UNMATERIALIZED_POLICY_DIGEST='0e89cbe48425763e06882ff08e368870acffd39771c529a6f35fcb8444ac13da'
UNMATERIALIZED_NATIVE_OBSERVATION_SHA256='997b79f65b85ffafa531371b539d314204d8461f0354390814ee7248630f07f7'
UNMATERIALIZED_ORIGINAL_HISTORY_SHA256='04f6e18c9f9425796dc31fb203b7d92d826b6ef775a597903b2466aaf91cbee4'
UNMATERIALIZED_NEXT_HISTORY_SHA256='00d22791b573cfa525e105a8ee69d41f80fb134790ceff0d599c9fe425be822a'
UNMATERIALIZED_METADATA_OBSERVATION_SHA256='31b1cb6c2ec226d49ba9edddd0de7c53c7bef3d15e11698aaa353e57bf38b38d'
UNMATERIALIZED_PUBLIC_FILES={'unit-a': {'parents/e2449e5bcd4af15d45f7a756dff7d997/capture.json': '4e2757ec495c1f199b326f83e22f75010e395bc2128df10913ff5d75513ee54f',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/fast-stage-0.json': '3818269b277a3c7b418beb60da0d609412bebc344011d97ef0af004b8fdfbe56',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/history-binding.json': '83fdb291ccf16dedeb514c33601cc69bccf0edcc60416d96ebd602941fc5fb12',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/history-identity-seal.json': '0b6c109d1cc61e27eaae9316c82743c4df2965665096d7143ca130a0f28e0653',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/legacy-grant-seal.json': '0dfaff049fb4321872fc68c2b29460673805d72f728c8e3e8b3133e9a3c2082e',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/owner.json': '2fa56b9d73e2b7277233cfc86785aae368eee37bc99862ae62e9d26edf082b1a',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/policy.json': '57830ee9c4c58285e901bf75f36fab479bd70eb4d2dd99b5ba212fd1c9759a1a',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/prior.json': '4f4ebb530184ad1ad3fe29543219214e6ceba17900dcf2eb04cb9302b85aa8e1',
            'runs/.pilot-ijo_53s0/active': '45df5ad5e0ecfa54d3226343e0e6857337494ba6e32f189d1174070665d8c659',
            'runs/.pilot-ijo_53s0/grant.json': '421e93b7214da4e8ca003e63d898b801b0d7b6ea6d5c2f8a5046ae82708c53c1',
            'runs/.pilot-ijo_53s0/provision.json': 'd664394aba39425c6359ecd521b7959d5f32210e8bf0b6347f63a97c35f3f59c'},
 'unit-b': {'parents/e2449e5bcd4af15d45f7a756dff7d997/capture.json': 'c6665ec8c61f4b1ebe1059aae42c48f48a2b66a69308210357b811dd727e93d4',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/history-binding.json': 'd4c893c7261345439b103ba2ad580d09b72581b401ba985eb15593964f51ea1e',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/history-identity-seal.json': '81bc9c73d0a1a9ce31b11e3264a70cb8f798a69c437e3e4f7caff6fe84d69515',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/legacy-grant-seal.json': '95539e6026afa5b68d01686e15c50661b2d3d1cca5079957d3c682e69f05859a',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/owner.json': '9e6d49c85ce21a0cb6d412b65aadd010dad5af2bf78b674d333ccc3cb6a737c4',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/policy.json': '57830ee9c4c58285e901bf75f36fab479bd70eb4d2dd99b5ba212fd1c9759a1a',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/prior.json': '46932b0acc107c367fe550d5ced448ebbba241e1fe6a2f35e2e9fa757b3b617a'},
 'unit-c': {'parents/e2449e5bcd4af15d45f7a756dff7d997/capture.json': '51b3b4e1b9288e7700444693a02402dd50c0be7fbe2394eeac888a648b6bc893',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/fast-stage-0.json': '2080f12d893214e7970c5e040a28de07457aaff6a5d0f661b1cbab48a79561e7',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/history-binding.json': '9eba567bec4a5dd1d55a565b37996d7cb7568d07c3ac8fd8ee9fe2db9b302a82',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/history-identity-seal.json': 'a67aceb971d4c820345f1e7e7d059b6ac7a7f285b34e74655e77d26a41f22f4b',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/legacy-grant-seal.json': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/owner.json': 'b850d46881f2334d44277348c6b3ffe3baf6cc92482cea878fc545cafd5d7104',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/policy.json': '57830ee9c4c58285e901bf75f36fab479bd70eb4d2dd99b5ba212fd1c9759a1a',
            'parents/e2449e5bcd4af15d45f7a756dff7d997/prior.json': 'b8adce0828d5ea96024ce62e9e0acd6b7a9fa8fb94eadbffe243a58ff3f7d3f1',
            'runs/.pilot-x5ddkfyz/active': '45df5ad5e0ecfa54d3226343e0e6857337494ba6e32f189d1174070665d8c659',
            'runs/.pilot-x5ddkfyz/grant.json': 'c4502e8c6748371adf1481eb19141c85003b1355cb88f21cc046383547074c28',
            'runs/.pilot-x5ddkfyz/provision.json': 'ebfc4157d4620a584d6647fad8b87ef12de465bc2868192bc51baf2c07b5035b'}}
UNMATERIALIZED_STAGING={'unit-a': {'.pilot-ijo_53s0': {'file_names': ['active',
                                               'grant.json',
                                               'provision.json',
                                               'secret.bin',
                                               'sender.bin'],
                                'identity': {'bytes': 4096,
                                             'ctime_ns': 1792022984356348309,
                                             'dev': 66307,
                                             'gid': 0,
                                             'inode': 6414783,
                                             'mode': 448,
                                             'mtime_ns': 1792022984356348309,
                                             'nlink': 2,
                                             'type': 16384,
                                             'uid': 0},
                                'name': '.pilot-ijo_53s0',
                                'native_claim_id': '25b23501f14617306a48d5c1d3b2d8b9f2f08d706e639646482795b08fcfe293',
                                'opaque_bytes_read': False,
                                'opaque_leaf_stats': {'secret.bin': {'bytes': 32,
                                                                     'ctime_ns': 1792022984328347732,
                                                                     'dev': 66307,
                                                                     'gid': 0,
                                                                     'inode': 6414785,
                                                                     'mode': 384,
                                                                     'mtime_ns': 1792022984328347732,
                                                                     'nlink': 1,
                                                                     'type': 32768,
                                                                     'uid': 0},
                                                      'sender.bin': {'bytes': 74,
                                                                     'ctime_ns': 1792022984344348062,
                                                                     'dev': 66307,
                                                                     'gid': 0,
                                                                     'inode': 6414787,
                                                                     'mode': 384,
                                                                     'mtime_ns': 1792022984344348062,
                                                                     'nlink': 1,
                                                                     'type': 32768,
                                                                     'uid': 0}},
                                'parent_id': 'e2449e5bcd4af15d45f7a756dff7d997',
                                'prepared_row_sha256': 'd51d730181b5c1079c9453e73bf1c63dd88240f48dea6ffcfbfea0ed535b7803',
                                'public_files': {'active': '45df5ad5e0ecfa54d3226343e0e6857337494ba6e32f189d1174070665d8c659',
                                                 'grant.json': '421e93b7214da4e8ca003e63d898b801b0d7b6ea6d5c2f8a5046ae82708c53c1',
                                                 'provision.json': 'd664394aba39425c6359ecd521b7959d5f32210e8bf0b6347f63a97c35f3f59c'},
                                'run_id': '911fb4a3f81bfe8cbf785c3d26d68aea'}},
 'unit-b': {},
 'unit-c': {'.pilot-x5ddkfyz': {'file_names': ['active', 'grant.json', 'provision.json', 'secret.bin'],
                                'identity': {'bytes': 4096,
                                             'ctime_ns': 1792022985532709651,
                                             'dev': 66305,
                                             'gid': 0,
                                             'inode': 11029974,
                                             'mode': 448,
                                             'mtime_ns': 1792022985532709651,
                                             'nlink': 2,
                                             'type': 16384,
                                             'uid': 0},
                                'name': '.pilot-x5ddkfyz',
                                'native_claim_id': 'db9e08a8a013ffcbcf4131ee7b89303e9d19db0796f252848daf27117725637a',
                                'opaque_bytes_read': False,
                                'opaque_leaf_stats': {'secret.bin': {'bytes': 32,
                                                                     'ctime_ns': 1792022985509376465,
                                                                     'dev': 66305,
                                                                     'gid': 0,
                                                                     'inode': 11029976,
                                                                     'mode': 384,
                                                                     'mtime_ns': 1792022985509376465,
                                                                     'nlink': 1,
                                                                     'type': 32768,
                                                                     'uid': 0}},
                                'parent_id': 'e2449e5bcd4af15d45f7a756dff7d997',
                                'prepared_row_sha256': 'dbd2b365d6fd5e08ba73f3fb65f317f40cf9e6dbc539097ab05ba8826d619353',
                                'public_files': {'active': '45df5ad5e0ecfa54d3226343e0e6857337494ba6e32f189d1174070665d8c659',
                                                 'grant.json': 'c4502e8c6748371adf1481eb19141c85003b1355cb88f21cc046383547074c28',
                                                 'provision.json': 'ebfc4157d4620a584d6647fad8b87ef12de465bc2868192bc51baf2c07b5035b'},
                                'run_id': '911fb4a3f81bfe8cbf785c3d26d68aea'}}}
UNMATERIALIZED_RAW_OWNERS={'unit-a':'2fa56b9d73e2b7277233cfc86785aae368eee37bc99862ae62e9d26edf082b1a',
    'unit-b':'9e6d49c85ce21a0cb6d412b65aadd010dad5af2bf78b674d333ccc3cb6a737c4',
    'unit-c':'b850d46881f2334d44277348c6b3ffe3baf6cc92482cea878fc545cafd5d7104'}
UNMATERIALIZED_DATABASES={'unit-a':'9081e9f3720d655d998ff76087bc6427ca859d6e925db1c88ee41b775be3f3d9',
    'unit-b':'0f082fa31e77d9476db734de8265aaa47484d0d9eb3ab3f0d349d0294da6ad0a',
    'unit-c':'738dd98b8dfd61e54f7365b2fe0ed94a50633f7cc9c402c48c1c7f300fde86c5'}
UNMATERIALIZED_CLAIMS={'unit-a':'a770ca0f758daad1e241fc3a0d5d4a20210b89f87467e5ab05c46339b3ca9f41',
    'unit-b':'4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    'unit-c':'4436ccb65f78ff6bb9168ef3ca152f3a90d9c9df75705e09e0c6f13395c96fb2'}
UNMATERIALIZED_RESOURCES={'unit-a':'f31dcf90b04291d0f5c0c1c0702788ee21a228bb0b27009dcf7eefb897360aa7',
    'unit-b':'4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    'unit-c':'22e10d547dcec11b50211cce3b324983e784a9df5911239c7f2cfcf15d260674'}


def require_unmaterialized_bindings():
    def pin(v):return type(v)is str and len(v)==64 and all(c in '0123456789abcdef'for c in v)
    require(pin(UNMATERIALIZED_NEXT_HISTORY_SHA256) and pin(UNMATERIALIZED_METADATA_OBSERVATION_SHA256)
        and type(UNMATERIALIZED_PUBLIC_FILES)is dict and type(UNMATERIALIZED_STAGING)is dict
        and set(UNMATERIALIZED_PUBLIC_FILES)==set(UNMATERIALIZED_STAGING)=={'unit-a','unit-b','unit-c'},
        'pending_e244_actual_metadata_and_history')
    for unit,files in UNMATERIALIZED_PUBLIC_FILES.items():
        staging=UNMATERIALIZED_STAGING[unit]
        parent='parents/'+UNMATERIALIZED_PARENT+'/'
        expected={parent+n for n in ('policy.json','owner.json','prior.json','capture.json',
            'history-binding.json','history-identity-seal.json','legacy-grant-seal.json')}
        if unit!='unit-b':expected.add(parent+'fast-stage-0.json')
        require(type(staging)is dict and len(staging)<=32
            and all(type(n)is str and re.fullmatch(r'\.pilot-[A-Za-z0-9_-]{1,64}',n)for n in staging),
            'e244_staging_scope_changed')
        for name,row in staging.items():
            require(type(row)is dict and row['name']==name and row['parent_id']==UNMATERIALIZED_PARENT
                and row['run_id']==UNMATERIALIZED_RUN and row['opaque_bytes_read']is False
                and set(row['opaque_leaf_stats'])==set(row['file_names'])-{'grant.json','provision.json','active'}
                and set(row['opaque_leaf_stats'])<= {'secret.bin','sender.bin'}
                and 'secret.bin'in row['opaque_leaf_stats']
                and set(row['public_files'])=={'grant.json','provision.json','active'},
                'e244_staging_identity_changed')
            require(all(files.get('runs/'+name+'/'+leaf)==value and pin(value)for leaf,value in row['public_files'].items()),
                'e244_staging_public_pin_changed')
            expected.update('runs/'+name+'/'+leaf for leaf in row['public_files'])
        require(type(files)is dict and set(files)==expected and all(pin(p)for p in files.values())
            and (unit!='unit-b' or not staging),'e244_public_file_scope_changed')
    return True


def unmaterialized_staging_names(unit):
    require_unmaterialized_bindings()
    require(unit in UNMATERIALIZED_STAGING,'e244_unit_changed')
    return tuple(UNMATERIALIZED_STAGING[unit])


def unmaterialized_owner_view(record,policy,*,unit):
    """Decode exact persisted v2 bytes locally; retain the signed original scope."""
    from .mcu_artifact import decode_owner
    require(unit in UNMATERIALIZED_RAW_OWNERS and type(record)is dict
        and _sha(record)==UNMATERIALIZED_RAW_OWNERS[unit]
        and type(policy)is ParentPolicy and policy.parent_id==UNMATERIALIZED_PARENT
        and policy.digest==UNMATERIALIZED_POLICY_DIGEST
        and _sha(policy.to_dict())=='57830ee9c4c58285e901bf75f36fab479bd70eb4d2dd99b5ba212fd1c9759a1a',
        'e244_raw_owner_or_policy_changed')
    owner,selected=decode_owner(record)
    require(selected is not None and owner['policy_digest']==policy.digest
        and owner['history_receipt_sha256']==UNMATERIALIZED_ORIGINAL_HISTORY_SHA256
        and owner['qualification_kind']==policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND,
        'e244_original_owner_scope_changed')
    for local in policy.units:
        require(local.limits==local.prior_debt.plus(Cost()if local.unit=='unit-b'else Cost(2560,600000000)),
            'e244_full_original_caps_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(context.transport is not None
        and owner['bundle_capture_commitment']==public_context_commitment(owner['bundle_context'],parent_id=policy.parent_id),
        'e244_original_capture_commitment_changed')
    context._check();return owner


def qualify_unmaterialized_costs(unit,policy,observed,value):
    """Full reservation once; actual prepared rows do not imply materialized runs."""
    from .pilot_resume_rpc import historical_monotonic_expired
    require_unmaterialized_bindings()
    owner=unmaterialized_owner_view(value['owner'],policy,unit=unit);detail=observed['detail'];local=policy.unit(unit)
    count=0 if unit=='unit-b'else 1;claimed=Cost()if not count else Cost(256,60000000)
    unused=Cost()if not count else Cost(2304,540000000)
    require(policy.parent_id==UNMATERIALIZED_PARENT and policy.digest==UNMATERIALIZED_POLICY_DIGEST
        and policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND
        and owner['policy_digest']==policy.digest and owner['history_receipt_sha256']==UNMATERIALIZED_ORIGINAL_HISTORY_SHA256
        and observed['policy_expired_at_observation']is True and observed['pending_unknown']is False
        and observed['scoped_rows_complete']is True and observed['observation_original_epoch_us']>=1792024207143316
        and historical_monotonic_expired(policy,unit,observed,owner['native_phase'][unit]['deadline_monotonic_us']
            +1792024207143316-owner['phase_end_epoch_us'])
        and historical_monotonic_expired(policy,unit,observed,detail['enrollment']['deadline_monotonic_us'])
        and observed['expected_database_sha256']==value['database_sha256']==UNMATERIALIZED_DATABASES[unit]
        and _sha(detail['claims'])==UNMATERIALIZED_CLAIMS[unit]
        and _sha(detail['resources'])==UNMATERIALIZED_RESOURCES[unit]
        and all(type(detail[k])is int and detail[k]==count for k in ('claim_count','resource_count','prepared_count'))
        and type(detail['start_count'])is int and detail['start_count']==0
        and detail['full_prepaid_caps']==asdict(claimed)
        and detail['approval_bytes_match_enrollment']is True
        and detail['enrollment']['history_binding_sha256']==owner['history_binding_sha256']
        and observed['legacy_runs']is observed['legacy_relation']is None,'e244_actual_unmaterialized_rows_changed')
    require(detail['prior_plus_prepaid_caps']==asdict(local.prior_debt.plus(claimed))==value['actual_native_paid_caps']
        and value['actual_controller_paid_caps']==value['actual_native_paid_caps']
        and detail['limit']==asdict(local.limits)==value['paid_caps']
        and value['conservative_unused_enrollment_caps']==value['conservative_unused_controller_caps']==asdict(unused)
        and value['native_controller_debt_views_may_be_summed']is False
        and value['runs']==value['retired_unstarted']==value['failed_started_runs']==[]
        and value['preparation_sha256']=={} and value['legacy_sha256']is None
        and value['fast_final_ack_sha256']==value['verified_piece_sha256']=={}
        and value['require_result_complete']is value['original_cleanup_proved']is False,
        'e244_full_reservation_or_failure_flags_changed')
    rows=value['unmaterialized_prepared'];claims=detail['claims']
    require(len(rows)==count and value['controller_endpoint_refs']==[
        dict(controller_allocation_id=c['controller_allocation_id'],child=c['child'])for c in claims],
        'e244_actual_child_refs_changed')
    if count:
        row=rows[0];claim=claims[0];resource=detail['resources'][0]
        require(row['run_id']==claim['child']['run_id']==UNMATERIALIZED_RUN
            and row['native_claim_id']==claim['native_claim_id']
            and row['prepared_row_sha256']==claim['prepared_row_sha256'] and row['child']==claim['child']
            and row['prepared']is claim['prepared']is True and row['started']is claim['started']is False
            and claim['start_row_sha256']is None and row['run_directory_present']is row['guarded_identity_present']is False
            and row['original_clocks_expired']is row['no_refund']is True and row['actual_cost']is None
            and row['separate_debt_sum_permitted']is row['physical_quiescence_observed']is False
            and all(record['native_claim_id']==claim['native_claim_id']
                and record['prepared_row_sha256']==claim['prepared_row_sha256']
                for record in UNMATERIALIZED_STAGING[unit].values())
            and observed['observation_original_epoch_us']>=claim['child']['application_expires_epoch_us']
            and historical_monotonic_expired(policy,unit,observed,max(claim['application_deadline_monotonic_us'],
                resource['application_deadline_monotonic_us'],resource['fast_stage_deadline_monotonic_us'])),
            'e244_unmaterialized_identity_or_clock_changed')


def unmaterialized_metadata(reader,state,unit,value):
    """Fresh public bytes and exact staging membership; opaque leaves are stat-only."""
    require_unmaterialized_bindings()
    files=UNMATERIALIZED_PUBLIC_FILES[unit];staging=UNMATERIALIZED_STAGING[unit]
    require(value['public_metadata_sha256']==files and value['unmaterialized_staging']==staging,
        'e244_metadata_scope_changed')
    parent=state/'parents'/UNMATERIALIZED_PARENT;reader.directory(parent);reader.directory(parent/'prepared-identities')
    expected={path.rsplit('/',1)[1]for path in files if path.startswith('parents/')}
    require(value['fast_stage_sha256']=={name:files['parents/'+UNMATERIALIZED_PARENT+'/'+name]
            for name in expected if name.startswith('fast-stage-')}
        and set(os.listdir(parent))==expected|{'native.sqlite','native.sqlite.owner','native.sqlite.approval','prepared-identities'}
        and os.listdir(parent/'prepared-identities')==[] and not os.path.lexists(state/'runs'/UNMATERIALIZED_RUN),
        'e244_missing_materialization_changed')
    for relative,pin in files.items():
        path=state/relative;reader.directory(path.parent)
        maximum=2500000 if path.name=='capture.json'else 2000000
        require(hashlib.sha256(reader.read_held(path,maximum)).hexdigest()==pin,'e244_public_metadata_changed')
    def stats(s):
        import stat
        return dict(dev=s.st_dev,inode=s.st_ino,type=stat.S_IFMT(s.st_mode),mode=stat.S_IMODE(s.st_mode),
            uid=s.st_uid,gid=s.st_gid,nlink=s.st_nlink,bytes=s.st_size,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns)
    opaque=[]
    for name,record in staging.items():
        path=state/'runs'/name;fd=reader.directory(path)
        require(stats(os.fstat(fd))==record['identity'] and sorted(os.listdir(path))==record['file_names'],
            'e244_staging_namespace_changed')
        for leaf,stamp in record['opaque_leaf_stats'].items():
            require(leaf in ('secret.bin','sender.bin'),'e244_opaque_leaf_scope_changed')
            require(stats(os.stat(leaf,dir_fd=fd,follow_symlinks=False))==stamp,
                'e244_opaque_leaf_changed')
            opaque.append((fd,leaf,stamp))
    reader.check()
    require(all(stats(os.stat(leaf,dir_fd=fd,follow_symlinks=False))==stamp for fd,leaf,stamp in opaque),
        'e244_opaque_leaf_final_changed')
    return files
