"""Original2756 zero-allocation fast scope; historical decoding only.

The final controller result records noRF/allocations. Its full2560/600M perA/C
reservation remains owed. Native database/expiry/history pins stay pending until
Root observes them; no live scope is inferred from these source descriptors.
"""
import hashlib
from dataclasses import asdict
from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy,Cost,canonical
from .pilot_bundle import BundleContext,public_context_commitment
from .profiles import GFSK250_FAST_BUNDLE_KIND,GFSK250_NAME,profile_for,profile_record
from .retained_capture import PAID_HISTORY

PARENT='27563497b7f6d89526f7249f2bf9005a'
POLICY_DIGEST='0e2675102aa9259d5969af5b592c49728138e07b331ae014735e3c63ce99c315'
POLICY_SHA256='8a7491a9031c6a3fa1c23975ff2e0391c39cc91245f2a777910f150d51e9af76'
OWNER_SHA256={'unit-a': 'ad6e867be2b55cbfca760ec4169e51e4de0cc06f0c4a5bc39e7a4e11609a42f9', 'unit-b': '77ec2e93f65174cebb7927bf7d2ce020136f06cefc14691c400db5eb60e30aa5', 'unit-c': 'fe8c41555bcecf5fd7fbfd3f7f9556af6ae5c911115e63067a2c678e33b1aac2'}
ORIGINAL_SCOPE_SHA256='d4280ec13e6352c0aba0c19978952556c07df495e3f879f1110ef38681c7a7be'
ORIGINAL_RUNTIME_SHA256='4b7d4c3092765c2957455eb2f343ebf82bea2d9da25ee9c9c999360d73225168'
ORIGINAL_MANIFEST_SHA256='853e1f55ff1b9cc699613b0f80bb3a0feb11617b7be61419faf526a50127200e'
ORIGINAL_CORE_COMMIT='a45c051aafd0be061f5b8160687277fe2b5eaca9'
ORIGINAL_PHASE_END=1791861289055042
H38_PREFIX=('28765d19f654ce3162525afe9c3f89c5', '5ad7e65b7e50901a6f63629c04a776e2', '4d1b5d6fe8d4b022c5cf1e21cef7f8ca', 'd60d8308326314a7753e651fa43a99e3', '4559277fa43913ce3935b078d6744a55', '4034f8979b985660df21859606c4f8a7', 'ba4572e95e598ad6c17fc954c3792db7', '1df30b47fa36b8d47328fb886528a7a9', 'f153a7a87a2803db85078df81a6a1aaf', '25a990a2d565c50b237ee45812a8c75f', 'cb5869e6aa8de545a267007b085d4762', 'f6e147e70ec7ce07f673ea5e9cd2cdc2', 'b8f9b5d9c5479c8d363c7b7e8014bc3b', '806fdca055e79dfc0b86ff8f304bcdf9', '4ea23c6b8fa83366904ac62ffa98e2bf', 'c26cd03a8fd427daf6b8d894e4b1d04c', '8dfcfdfe8f8be2c0b326520d70eab7f5', '6f89e2a21919991e15224e393446928c', '62bcef4f1c74532fd7ab3ada1b8b94e2', '141c892d83b6064a800c11aab6444f28', '05c36e09fd4056c94d341d2dd32243f4', 'bccb99afe6589968190b131eeaf372b3', '487ae574816ff5444de03e3daa4401e4', 'e01508ddcae7389728add074de4876b7', '2429969fcbb0f57b3deaeda7187036c2', '7746ddba2544ce79c01a195f1e8746ce', '57339cca60ddf62b41eafac60147a252', '4aa3ad2bc1346cae82bc90d8abd6dcd4', 'a3ded77ef251c9d7e085ab790b9ac5b3', '76fe38883690e058771940d51d0d57d6', '7630420c0e72181d6e7239c105c165c6', '4ca8d86644aa24e6fefd06872a9cd131', '6be212b13351cb9852ed07ba7998be96', 'e8aac38a787f33daa91c80e39cfc8710', '73c1cda8c02939409d0b1e7e2a39be67', '3195cff3fc8fc8ee0b81694fe8f9e389', '515c662fae6cc761c83e7fdef44cbda0', '4675a437918764d62ddc53d288685367')
FINAL_RESULT_SHA256='103b3517aa965840230eb2ec1b609e93a9ccb5c5bd8b64687f8f89ce9f770b03'
EXPIRY_COMPLETION_SHA256=None
NEXT_HISTORY_RECEIPT_SHA256=None
NATIVE_DATABASE_SHA256={'unit-a': None, 'unit-b': None, 'unit-c': None}

def _sha(value):return hashlib.sha256(canonical(value)).hexdigest()

def declared_partition(policy):
    """Full original reservations, never actual consumption or refundable credit."""
    require(type(policy) is ParentPolicy and policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and _sha(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND,
        '2756_original_policy_changed')
    rows={}
    for unit in policy.units:
        extra=Cost() if unit.unit=='unit-b' else Cost(2560,600000000)
        require(unit.limits==unit.prior_debt.plus(extra),'2756_original_full_caps_changed')
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
        '2756_original_owner_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(context.transport is not None,'2756_exact_MB1_transport_required')
    require(owner['bundle_capture_commitment']==public_context_commitment(owner['bundle_context'],parent_id=PARENT),
        '2756_original_capture_commitment_changed')
    return context

def retained_context(owner,policy,*,unit,observation):
    """Pure expired-context predicate; an actual collector remains mandatory."""
    context=original_context(owner,policy,unit=unit)
    require(type(observation) is dict and observation.get('status')=='scoped_rows_observed'
        and observation.get('parent_id')==PARENT and observation.get('policy_digest')==POLICY_DIGEST
        and observation.get('unit')==unit and observation.get('policy_expired_at_observation') is True,
        '2756_actual_expired_scope_required')
    epoch,mono=(observation.get(k) for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
    require(type(epoch) is int and type(mono) is int
        and epoch>=ORIGINAL_PHASE_END>=policy.expires_epoch_us
        and mono>=owner['native_phase'][unit]['deadline_monotonic_us'],'2756_original_clocks_live')
    context._check();return context

def require_final_bindings():
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (FINAL_RESULT_SHA256,EXPIRY_COMPLETION_SHA256,NEXT_HISTORY_RECEIPT_SHA256))
        and set(NATIVE_DATABASE_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in NATIVE_DATABASE_SHA256.values()),'pending_2756_actual_database_expiry_history')
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)==39 and PAID_HISTORY[:38]==H38_PREFIX and PAID_HISTORY[38]==PARENT,
        '2756_H38_prefix_changed')
    return PAID_HISTORY[:39]
