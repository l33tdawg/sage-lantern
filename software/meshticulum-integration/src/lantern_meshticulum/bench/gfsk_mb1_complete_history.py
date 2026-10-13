"""Exact expired 515c retention contract; no live authority.

Actual terminal result, original-expiry observations and H37 receipt are pinned. Pure historical
context decoding intentionally does not consult a later live GFSK scope.
"""
import hashlib
from dataclasses import asdict

from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy,Cost,canonical
from .pilot_bundle import BundleContext,public_context_commitment
from .profiles import GFSK_MB1_BUNDLE_KIND,GFSK_NAME,profile_for,profile_record
from .retained_capture import PAID_HISTORY

PARENT='515c662fae6cc761c83e7fdef44cbda0'
FAILED_STARTED_RUNS={'unit-a': [], 'unit-b': [], 'unit-c': []}
POLICY_DIGEST='8e727113809884e808988d8f3236e3aa8cb35f1b87bd0e3610d39a8a29754e3f'
POLICY_SHA256='39989a7740f68d6395247c183a43aa0e64056d40d0a848aaeefe9d0bc19f6601'
OWNER_SHA256={'unit-a': '9119567097b4688bed43cf09401cd77e43ea9049a2b01d40309b698a66a0c25e', 'unit-b': '753164b0cdfe6def60bb35bbf41ca760e388a7ecf13803476bd444ec71940b93', 'unit-c': 'bba905f8e4709eed784d137d28a597c21d9e74e69d11c448765926624f8183d1'}
ORIGINAL_SCOPE_SHA256='539d2ddc872fd6f0567abad76c0d067deea714837e9ebda5c15553a860b04c79'
ORIGINAL_RUNTIME_SHA256='cca242be7140fb7718ca3497d56330843b9b44aa72624b214209c60da03a574f'
ORIGINAL_MANIFEST_SHA256='e35c736b5749e84e3b313a0327a6b0e38e0ef70fcc6a339386269f359cb27f1b'
ORIGINAL_PHASE_END=1791848615970474
H36_PREFIX=PAID_HISTORY[:36]

# Bind only from the actual final local result and independently captured
# original-expiry/native metadata. No success/consumption is presumed here.
FINAL_RESULT_SHA256='383aedae96a98c95e59c2873d5740a007ff7eeee165e8b198fd5c8d1711a7389'
EXPIRY_COMPLETION_SHA256='0693ba6f7f0711003f63e358b135716e12a8941b7c0cd1bf4d4900be431fe2a9'
NEXT_HISTORY_RECEIPT_SHA256='238cc3c36c83540418da14a5b59b0901cdd24e4c7bce5c55e689ce6884762ceb'
NATIVE_DATABASE_SHA256={'unit-a': 'd74921694ede4d10d4c76b45f2033dd89d9f14cda9b702cf2cd0bfcd9e23cd2d', 'unit-b': 'd857bed157887229096fde7d53f472ba1a2c1e018a93c4051aae2b810fae6453', 'unit-c': 'c8eecd81d1ec02bdcf82a1f39a95835ed0f18ff4dae63e2649059fc30720ebd1'}
NATIVE_OUTCOME_SHA256={'unit-a': {'6ee1772980676fffca4a0b806ca6741f': '5f8847ba34bfca11841738ff06a4d7a8bc8caee305d510d287687ad966f460fa', 'd14f61601bb67785528e4f072ba6c407': '6133ecce7c5a0f3d621adf52f4afaef296c83b492d68a7eb836b95c3af6338ee', '8cc7f43d357b77ad987bb19fa5ef161f': 'a157c7fe98fe53c753e36a1a88c4ee7f963c0e882b738e0431612666a3516e63', '2741537efbe9abc9632dbbb2649227be': '0147dbc24f6816a54435a03d7be7672368ce5a1e58172c91ac9714b181eaa42d', '31e169772cf695821bf9369fa1d296a1': 'a0a7787301beb1c2a78bbcb6f81a7980ce5020a32fdcde8e54a6c667899f2c55', '6763423370094a7e352a1aad393c8f3b': '45dcaf8a5a6710361ab1e1fbfae6b1bce605881e40c321042299b90ae60ddb7a', '8082e878107b7d848f33f0ac65185cef': '7c7c8f652381c2414162620deee466ae13a8a2f60dc13d4a219a2d6437dee67e', 'ec1a8cdd20c53400c786d796812ec7fb': '0fc22b8ad14d708b4c78193b2f0ea586044d22236da3fc6d5b4f710c9ee9dd19', '70c79046b41f0d3919db4f71ac4b57f3': '91df1b457b3b88ac0d6fcb8fbb1cc282aa74cc3bb32934d90d215223d963c157', '324fe3d6fbd0a57e94f38f4da8571002': '98f6f50750caea881e0035f827db897988f9feb229a7eeb4ca877245874d0979'}, 'unit-b': {}, 'unit-c': {'6ee1772980676fffca4a0b806ca6741f': '84ac3aa044bb50cb8e18b07677d63dd0711a92bda7d7b75693ebb2404b9f8f96', 'd14f61601bb67785528e4f072ba6c407': '170c3c150ff11f91d080b0d6d5df1ce8de50a68c8ed468c674f534594bea5957', '8cc7f43d357b77ad987bb19fa5ef161f': 'a72f66ccb8819f96efa5f3e4483f4d0d6aa0eec518bee85a5ff87a051d23c5e6', '2741537efbe9abc9632dbbb2649227be': '578c0339cc17a81acfac9e63e87e35a8e95e81714f678cb6b4f1909231e46b40', '31e169772cf695821bf9369fa1d296a1': '7485d3033bb792494d2560eb674d800c0c3f40e25a694d91d4b9583bb69e1229', '6763423370094a7e352a1aad393c8f3b': '8d18f3c689116a24d712c1c3bbe03ecbacdcafc6f0390f57190e15e97f778537', '8082e878107b7d848f33f0ac65185cef': 'f1565be248bd340df8d9d35fca918b8c511689d48512f667bd01e9e5d5309fc7', 'ec1a8cdd20c53400c786d796812ec7fb': '03dd4bf53ca0330c9309926a6c628d38cb7faaa6a8fde4e2307ebe1e780b18b7', '70c79046b41f0d3919db4f71ac4b57f3': '1332356cf5848a21960edffa462b31470c8e514c1dabc3c30abf7ba82fa05def', '324fe3d6fbd0a57e94f38f4da8571002': '8ad2d70c51db55e8a4b4389965f3226d49e089362a4176b548e8e7d13fc56b5a'}}


def _sha(value):return hashlib.sha256(canonical(value)).hexdigest()


def declared_partition(policy):
    """Full original reservations, never actual consumption or refundable credit."""
    require(type(policy) is ParentPolicy and policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and _sha(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==GFSK_MB1_BUNDLE_KIND,
        '515c_original_policy_changed')
    rows={}
    for unit in policy.units:
        extra=Cost() if unit.unit=='unit-b' else Cost(2560,600000000)
        require(unit.limits==unit.prior_debt.plus(extra),'515c_original_full_caps_changed')
        rows[unit.unit]=dict(full_original_caps=asdict(unit.limits),prior_caps=asdict(unit.prior_debt),
            declared_new_reservation=asdict(extra),actual_consumption=None,refunds=0)
    return rows


def original_context(owner,policy,*,unit):
    """Exact original signed data/clock decoding, with no admission side effect."""
    declared_partition(policy)
    require(unit in OWNER_SHA256 and type(owner) is dict and _sha(owner)==OWNER_SHA256[unit]
        and owner['policy_digest']==POLICY_DIGEST and owner['qualification_kind']==GFSK_MB1_BUNDLE_KIND
        and owner['radio_profile_name']==owner['radio_modem']==GFSK_NAME
        and owner['rf_profile']==profile_record(profile_for(GFSK_NAME))
        and owner['mb1_bundle_scope_sha256']==ORIGINAL_SCOPE_SHA256
        and owner['runtime_sha256']==ORIGINAL_RUNTIME_SHA256 and owner['manifest_sha256']==ORIGINAL_MANIFEST_SHA256
        and owner['phase_start_epoch_us']==policy.issued_epoch_us and owner['phase_end_epoch_us']==ORIGINAL_PHASE_END,
        '515c_original_owner_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(context.transport is not None,'515c_exact_MB1_transport_required')
    require(owner['bundle_capture_commitment']==public_context_commitment(owner['bundle_context'],parent_id=PARENT),
        '515c_original_capture_commitment_changed')
    return context


def retained_context(owner,policy,*,unit,observation):
    """Pure expired-context predicate; an actual collector remains mandatory."""
    context=original_context(owner,policy,unit=unit)
    require(type(observation) is dict and observation.get('status')=='scoped_rows_observed'
        and observation.get('parent_id')==PARENT and observation.get('policy_digest')==POLICY_DIGEST
        and observation.get('unit')==unit and observation.get('policy_expired_at_observation') is True,
        '515c_actual_expired_scope_required')
    epoch,mono=(observation.get(k) for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
    require(type(epoch) is int and type(mono) is int
        and epoch>=ORIGINAL_PHASE_END>=policy.expires_epoch_us
        and mono>=owner['native_phase'][unit]['deadline_monotonic_us'],'515c_original_clocks_live')
    context._check();return context


def require_final_bindings():
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (FINAL_RESULT_SHA256,EXPIRY_COMPLETION_SHA256,NEXT_HISTORY_RECEIPT_SHA256)),
        'pending_515c_final_result_expiry_and_history')
    require(set(NATIVE_DATABASE_SHA256)==set(NATIVE_OUTCOME_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in NATIVE_DATABASE_SHA256.values())
        and all(type(v) is dict for v in NATIVE_OUTCOME_SHA256.values())
        and NATIVE_OUTCOME_SHA256['unit-b']=={}
        and all(type(run) is str and len(run)==32 and pin(value)
            for outcomes in NATIVE_OUTCOME_SHA256.values() for run,value in outcomes.items()),
        'pending_515c_native_terminal_metadata')
    # Outcome cardinality and complete/failed partition are bound only after
    # actual completion. The10 retired grants alone do not supply a qualification claim.
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)>=37 and PAID_HISTORY[:36]==H36_PREFIX and PAID_HISTORY[36]==PARENT,
        '515c_H36_prefix_changed')
    return PAID_HISTORY[:37]
