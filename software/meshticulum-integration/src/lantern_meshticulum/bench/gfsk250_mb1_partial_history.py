"""Exact expired 4675 retention contract; no live authority.

The terminal local result, actual expiry observations and the H38 receipt are pinned. Pure historical
context decoding intentionally does not consult a later live GFSK scope.
"""
import hashlib
from dataclasses import asdict

from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy,Cost,canonical
from .pilot_bundle import BundleContext,public_context_commitment
from .profiles import GFSK250_MB1_BUNDLE_KIND,GFSK250_NAME,profile_for,profile_record
from .retained_capture import PAID_HISTORY

PARENT='4675a437918764d62ddc53d288685367'
FAILED_STARTED_RUNS={'unit-a': ['18306a998ce45edde8aa04cf2e936342'], 'unit-b': [], 'unit-c': []}
POLICY_DIGEST='8349f507f1e208085672dbc19c99436d97a6564d7d73e51a08cf29d43007d8c5'
POLICY_SHA256='7ddf2156ad33e2aad0ebc073941828060abcc60a53f88cb646c3675cd34ba7ba'
OWNER_SHA256={'unit-a': '1038753957e146837e01b494e4a454a01160dcc501e6843ec5143f10666f2b49', 'unit-b': '77f195560bced99c6ecaaa97d4bd4d4bae28a79a191f748aeabe85c156cb7a16', 'unit-c': '897bd6f22fa6ed0ded6af354e9faac96e53044153a798025fdac3c310373d049'}
ORIGINAL_SCOPE_SHA256='791fb4c61b08734ee58830f6bd94d8b9c6db4e995be7d99962934df4e7341a45'
ORIGINAL_RUNTIME_SHA256='9c86a003a7d86f9fe0a55d05901e04c772690009935ef7519abd220d8b7a213a'
ORIGINAL_MANIFEST_SHA256='a819772073a92a4e62e1849f0a64bf8829c927cebeab3c836a9bd99ecbbc02ea'
ORIGINAL_PHASE_END=1791854349857802
H37_PREFIX=('28765d19f654ce3162525afe9c3f89c5', '5ad7e65b7e50901a6f63629c04a776e2', '4d1b5d6fe8d4b022c5cf1e21cef7f8ca', 'd60d8308326314a7753e651fa43a99e3', '4559277fa43913ce3935b078d6744a55', '4034f8979b985660df21859606c4f8a7', 'ba4572e95e598ad6c17fc954c3792db7', '1df30b47fa36b8d47328fb886528a7a9', 'f153a7a87a2803db85078df81a6a1aaf', '25a990a2d565c50b237ee45812a8c75f', 'cb5869e6aa8de545a267007b085d4762', 'f6e147e70ec7ce07f673ea5e9cd2cdc2', 'b8f9b5d9c5479c8d363c7b7e8014bc3b', '806fdca055e79dfc0b86ff8f304bcdf9', '4ea23c6b8fa83366904ac62ffa98e2bf', 'c26cd03a8fd427daf6b8d894e4b1d04c', '8dfcfdfe8f8be2c0b326520d70eab7f5', '6f89e2a21919991e15224e393446928c', '62bcef4f1c74532fd7ab3ada1b8b94e2', '141c892d83b6064a800c11aab6444f28', '05c36e09fd4056c94d341d2dd32243f4', 'bccb99afe6589968190b131eeaf372b3', '487ae574816ff5444de03e3daa4401e4', 'e01508ddcae7389728add074de4876b7', '2429969fcbb0f57b3deaeda7187036c2', '7746ddba2544ce79c01a195f1e8746ce', '57339cca60ddf62b41eafac60147a252', '4aa3ad2bc1346cae82bc90d8abd6dcd4', 'a3ded77ef251c9d7e085ab790b9ac5b3', '76fe38883690e058771940d51d0d57d6', '7630420c0e72181d6e7239c105c165c6', '4ca8d86644aa24e6fefd06872a9cd131', '6be212b13351cb9852ed07ba7998be96', 'e8aac38a787f33daa91c80e39cfc8710', '73c1cda8c02939409d0b1e7e2a39be67', '3195cff3fc8fc8ee0b81694fe8f9e389', '515c662fae6cc761c83e7fdef44cbda0')

# Bind only from the actual final local result and independently captured
# original-expiry/native metadata. No success/consumption is presumed here.
FINAL_RESULT_SHA256='aa9f4ada542a084e8a23761830a57a41b6fc39b0cf653251792623678254cf97'
EXPIRY_COMPLETION_SHA256='6d68b5419581626b4b5162d67336527629682e8b09d007957270151f8fd61bb3'
NEXT_HISTORY_RECEIPT_SHA256='1b74160d43fbac95a959db04a3d731592384c20d24d8c10cb17fc468be9ff0f5'
NATIVE_DATABASE_SHA256={'unit-a': '70ccd11a15cf0862cf00dbe93c90b92de0e5ca55751e31b3f3159f4f13444224', 'unit-b': '64a5aaf5adc14407ffbb3235bd5ae6f84d4c1098791309e1929f086f35fd722b', 'unit-c': '567f45e2c7fc8ce6bbdd2c2de8448c98591da87cbdb4e01df324813e7ac0c31e'}
NATIVE_OUTCOME_SHA256={'unit-a': {'44070ab5613d5d8f9d93b82d5b357f57': 'ffe30472e31e7454f0d7a4c9fcadda1a7869700006bfc79695ce34112c2584fe', '18306a998ce45edde8aa04cf2e936342': 'c6487729674ed5f4b6aa5bd77551778654c3b3a0beaffeffe9d222916c6bbd36'}, 'unit-b': {}, 'unit-c': {'44070ab5613d5d8f9d93b82d5b357f57': 'eb44cb0e76a9fb58e9b82eb36e813da54c8f2e4a91a2987a0a805b354614aa6c', '18306a998ce45edde8aa04cf2e936342': '52a1177825553ee7947ffd8b6646e70681ae89d2f421f02d1cbcff2d445d4d3b'}}


def _sha(value):return hashlib.sha256(canonical(value)).hexdigest()


def declared_partition(policy):
    """Full original reservations, never actual consumption or refundable credit."""
    require(type(policy) is ParentPolicy and policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and _sha(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==GFSK250_MB1_BUNDLE_KIND,
        '4675_original_policy_changed')
    rows={}
    for unit in policy.units:
        extra=Cost() if unit.unit=='unit-b' else Cost(2560,600000000)
        require(unit.limits==unit.prior_debt.plus(extra),'4675_original_full_caps_changed')
        rows[unit.unit]=dict(full_original_caps=asdict(unit.limits),prior_caps=asdict(unit.prior_debt),
            declared_new_reservation=asdict(extra),actual_consumption=None,refunds=0)
    return rows


def original_context(owner,policy,*,unit):
    """Exact original signed data/clock decoding, with no admission side effect."""
    declared_partition(policy)
    require(unit in OWNER_SHA256 and type(owner) is dict and _sha(owner)==OWNER_SHA256[unit]
        and owner['policy_digest']==POLICY_DIGEST and owner['qualification_kind']==GFSK250_MB1_BUNDLE_KIND
        and owner['radio_profile_name']==owner['radio_modem']==GFSK250_NAME
        and owner['rf_profile']==profile_record(profile_for(GFSK250_NAME))
        and owner['mb1_bundle_scope_sha256']==ORIGINAL_SCOPE_SHA256
        and owner['runtime_sha256']==ORIGINAL_RUNTIME_SHA256 and owner['manifest_sha256']==ORIGINAL_MANIFEST_SHA256
        and owner['phase_start_epoch_us']==policy.issued_epoch_us and owner['phase_end_epoch_us']==ORIGINAL_PHASE_END,
        '4675_original_owner_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(context.transport is not None,'4675_exact_MB1_transport_required')
    require(owner['bundle_capture_commitment']==public_context_commitment(owner['bundle_context'],parent_id=PARENT),
        '4675_original_capture_commitment_changed')
    return context


def retained_context(owner,policy,*,unit,observation):
    """Pure expired-context predicate; an actual collector remains mandatory."""
    context=original_context(owner,policy,unit=unit)
    require(type(observation) is dict and observation.get('status')=='scoped_rows_observed'
        and observation.get('parent_id')==PARENT and observation.get('policy_digest')==POLICY_DIGEST
        and observation.get('unit')==unit and observation.get('policy_expired_at_observation') is True,
        '4675_actual_expired_scope_required')
    epoch,mono=(observation.get(k) for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
    require(type(epoch) is int and type(mono) is int
        and epoch>=ORIGINAL_PHASE_END>=policy.expires_epoch_us
        and mono>=owner['native_phase'][unit]['deadline_monotonic_us'],'4675_original_clocks_live')
    context._check();return context


def require_final_bindings():
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (FINAL_RESULT_SHA256,EXPIRY_COMPLETION_SHA256,NEXT_HISTORY_RECEIPT_SHA256)),
        'pending_4675_final_result_expiry_and_history')
    require(set(NATIVE_DATABASE_SHA256)==set(NATIVE_OUTCOME_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in NATIVE_DATABASE_SHA256.values())
        and all(type(v) is dict for v in NATIVE_OUTCOME_SHA256.values())
        and NATIVE_OUTCOME_SHA256['unit-b']=={}
        and all(type(run) is str and len(run)==32 and pin(value)
            for outcomes in NATIVE_OUTCOME_SHA256.values() for run,value in outcomes.items()),
        'pending_4675_native_terminal_metadata')
    # Outcome cardinality and complete/failed partition are bound only after
    # actual completion. The two retired grants do not imply whole-file application completion.
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)==38 and PAID_HISTORY[:37]==H37_PREFIX and PAID_HISTORY[37]==PARENT,
        '4675_H37_prefix_changed')
    return PAID_HISTORY[:38]
