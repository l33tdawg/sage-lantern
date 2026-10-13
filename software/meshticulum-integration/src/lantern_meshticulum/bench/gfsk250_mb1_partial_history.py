"""Exact expired 4675 retention contract; no live authority.

The terminal local result, actual expiry observations and the H38 receipt are pinned. Pure historical
context decoding intentionally does not consult a later live GFSK scope.
"""
import hashlib
import sys
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
ORIGINAL_ABSOLUTE_END=ORIGINAL_PHASE_END
QUALIFICATION_KIND=GFSK250_MB1_BUNDLE_KIND
H37_PREFIX=PAID_HISTORY[:37]
require(len(H37_PREFIX)==37 and hashlib.sha256(canonical(H37_PREFIX)).hexdigest()=='31babc50f88e2c0a651435631564d08c4d27eef02f92ef2efa245979cc09ebb1','original_H37_prefix_changed')

# Bind only from the actual final local result and independently captured
# original-expiry/native metadata. No success/consumption is presumed here.
FINAL_RESULT_SHA256='aa9f4ada542a084e8a23761830a57a41b6fc39b0cf653251792623678254cf97'
EXPIRY_COMPLETION_SHA256='6d68b5419581626b4b5162d67336527629682e8b09d007957270151f8fd61bb3'
NEXT_HISTORY_RECEIPT_SHA256='1b74160d43fbac95a959db04a3d731592384c20d24d8c10cb17fc468be9ff0f5'
NATIVE_DATABASE_SHA256={'unit-a': '70ccd11a15cf0862cf00dbe93c90b92de0e5ca55751e31b3f3159f4f13444224', 'unit-b': '64a5aaf5adc14407ffbb3235bd5ae6f84d4c1098791309e1929f086f35fd722b', 'unit-c': '567f45e2c7fc8ce6bbdd2c2de8448c98591da87cbdb4e01df324813e7ac0c31e'}
NATIVE_OUTCOME_SHA256={'unit-a': {'44070ab5613d5d8f9d93b82d5b357f57': 'ffe30472e31e7454f0d7a4c9fcadda1a7869700006bfc79695ce34112c2584fe', '18306a998ce45edde8aa04cf2e936342': 'c6487729674ed5f4b6aa5bd77551778654c3b3a0beaffeffe9d222916c6bbd36'}, 'unit-b': {}, 'unit-c': {'44070ab5613d5d8f9d93b82d5b357f57': 'eb44cb0e76a9fb58e9b82eb36e813da54c8f2e4a91a2987a0a805b354614aa6c', '18306a998ce45edde8aa04cf2e936342': '52a1177825553ee7947ffd8b6646e70681ae89d2f421f02d1cbcff2d445d4d3b'}}


def _sha(value):return hashlib.sha256(canonical(value)).hexdigest()


def _declared_partition(c,policy):
    """Full original reservations, never actual consumption or refundable credit."""
    require(type(policy) is ParentPolicy and policy.parent_id==c.PARENT and policy.digest==c.POLICY_DIGEST
        and _sha(policy.to_dict())==c.POLICY_SHA256 and policy.qualification_kind==c.QUALIFICATION_KIND,
        f'{c.PARENT[:4]}_original_policy_changed')
    rows={}
    for unit in policy.units:
        extra=Cost() if unit.unit=='unit-b' else Cost(2560,600000000)
        require(unit.limits==unit.prior_debt.plus(extra),f'{c.PARENT[:4]}_original_full_caps_changed')
        rows[unit.unit]=dict(full_original_caps=asdict(unit.limits),prior_caps=asdict(unit.prior_debt),
            declared_new_reservation=asdict(extra),actual_consumption=None,refunds=0)
    return rows


def _original_context(c,owner,policy,*,unit):
    """Exact original signed data/clock decoding, with no admission side effect."""
    _declared_partition(c,policy)
    require(unit in c.OWNER_SHA256 and type(owner) is dict and _sha(owner)==c.OWNER_SHA256[unit]
        and owner['policy_digest']==c.POLICY_DIGEST and owner['qualification_kind']==c.QUALIFICATION_KIND
        and owner['radio_profile_name']==owner['radio_modem']==GFSK250_NAME
        and owner['rf_profile']==profile_record(profile_for(GFSK250_NAME))
        and owner['mb1_bundle_scope_sha256']==c.ORIGINAL_SCOPE_SHA256
        and owner['runtime_sha256']==c.ORIGINAL_RUNTIME_SHA256 and owner['manifest_sha256']==c.ORIGINAL_MANIFEST_SHA256
        and owner['phase_start_epoch_us']==policy.issued_epoch_us and owner['phase_end_epoch_us']==c.ORIGINAL_PHASE_END,
        f'{c.PARENT[:4]}_original_owner_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(context.transport is not None,f'{c.PARENT[:4]}_exact_MB1_transport_required')
    require(owner['bundle_capture_commitment']==public_context_commitment(owner['bundle_context'],parent_id=c.PARENT),
        f'{c.PARENT[:4]}_original_capture_commitment_changed')
    return context


def _retained_context(c,owner,policy,*,unit,observation):
    """Pure expired-context predicate; an actual collector remains mandatory."""
    context=_original_context(c,owner,policy,unit=unit)
    require(type(observation) is dict and observation.get('status')=='scoped_rows_observed'
        and observation.get('parent_id')==c.PARENT and observation.get('policy_digest')==c.POLICY_DIGEST
        and observation.get('unit')==unit and observation.get('policy_expired_at_observation') is True,
        f'{c.PARENT[:4]}_actual_expired_scope_required')
    epoch,mono=(observation.get(k) for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
    require(type(epoch) is int and type(mono) is int
        and epoch>=c.ORIGINAL_ABSOLUTE_END>=c.ORIGINAL_PHASE_END>=policy.expires_epoch_us
        and mono>=owner['native_phase'][unit]['deadline_monotonic_us']+c.ORIGINAL_ABSOLUTE_END-c.ORIGINAL_PHASE_END,f'{c.PARENT[:4]}_original_clocks_live')
    context._check();return context


def _require_final_bindings(c):
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (c.FINAL_RESULT_SHA256,c.EXPIRY_COMPLETION_SHA256,c.NEXT_HISTORY_RECEIPT_SHA256)),
        f'pending_{c.PARENT[:4]}_final_result_expiry_and_history')
    require(set(c.NATIVE_DATABASE_SHA256)==set(c.NATIVE_OUTCOME_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in c.NATIVE_DATABASE_SHA256.values())
        and all(type(v) is dict for v in c.NATIVE_OUTCOME_SHA256.values())
        and c.NATIVE_OUTCOME_SHA256['unit-b']=={}
        and all(type(run) is str and len(run)==32 and pin(value)
            for outcomes in c.NATIVE_OUTCOME_SHA256.values() for run,value in outcomes.items()),
        f'pending_{c.PARENT[:4]}_native_terminal_metadata')
    # Outcome cardinality and complete/failed partition are bound only after
    # actual completion. The two retired grants do not imply whole-file application completion.
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)>=38 and PAID_HISTORY[:37]==H37_PREFIX and PAID_HISTORY[37]==PARENT,
        '4675_H37_prefix_changed')
    return PAID_HISTORY[:38]

from functools import partial
_contract=sys.modules[__name__]
declared_partition=partial(_declared_partition,_contract)
original_context=partial(_original_context,_contract)
retained_context=partial(_retained_context,_contract)
require_final_bindings=partial(_require_final_bindings,_contract)
