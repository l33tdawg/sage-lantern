"""Exact expired 3195 retention contract; no live authority.

Actual terminal result, original-expiry observations and H36 receipt are pinned. Pure historical
context decoding intentionally does not consult a later live GFSK scope.
"""
import hashlib
from dataclasses import asdict

from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy,Cost,canonical
from .pilot_bundle import BundleContext,public_context_commitment
from .profiles import GFSK_MB1_BUNDLE_KIND,GFSK_NAME,profile_for,profile_record
from .retained_capture import PAID_HISTORY

PARENT='3195cff3fc8fc8ee0b81694fe8f9e389'
FAILED_STARTED_RUNS={'unit-a': ['acb48986aeed5a9c0358312e4574a93e'], 'unit-b': [], 'unit-c': ['acb48986aeed5a9c0358312e4574a93e']}
POLICY_DIGEST='1a7511b250bb052acff2ca0bf7a5ab27a79da94ae4d76cba30a1ccf37160381a'
POLICY_SHA256='cf4f5a1bfefbeb8efc693a4fbad8f6df8d5b122f311b0aaa771299eac82dc47c'
OWNER_SHA256={'unit-a': '9702809c94c480c463b1c74c72aa90ab0f7e4ca5c5837f9f74c65ac483060b7b', 'unit-b': '7c9ebeef211a241c5810f524b1f75cd366e6c14ed0dd96655996f176f12b5935', 'unit-c': 'e163aa6ddaa5eb1610150e08e5bfa5481b369cc80505d539680d372bd29e5aea'}
ORIGINAL_SCOPE_SHA256='52c6d8f774d5cbfa935c5e3f0cc8f71a8c542911d4f56f327ce68f6603653371'
ORIGINAL_RUNTIME_SHA256='ab5ebe428cb3c42019fa55a6843bf1664bb881590ac2ab7d86d76341308a2508'
ORIGINAL_MANIFEST_SHA256='aa84143be1f2fa45d977fc9d81d863c5992807603cc55662c11c691ced35784d'
ORIGINAL_PHASE_END=1791843832158679
H35_PREFIX=PAID_HISTORY[:35]

# Bind only from the actual final local result and independently captured
# original-expiry/native metadata. No success/consumption is presumed here.
FINAL_RESULT_SHA256='e6ac73f8ddd273b1393c33d4b1111f1718bdfc2aee6e1c5f4b133e5038ff1d36'
EXPIRY_COMPLETION_SHA256='1aa99572674cadd24966fb9613ebc9f938f52cab79106f55660601bd8c997afb'
NEXT_HISTORY_RECEIPT_SHA256='11ceb828c787eeed97ec978915b59ff5d34cf0056797035a238673ab407b1249'
NATIVE_DATABASE_SHA256={'unit-a': '354ed73e4cc54504eb2945cf71b1753f42e5c8b0f8ddc759e078b916878305c5', 'unit-b': 'b73aa6aba2ad89d70df5b3a27c51328cdd5f90dc203bc23d39b98b77521af393', 'unit-c': '2db645bf205eb97dcc8434f70667fc17cf0016a212900d01436d048f5ae9efc6'}
NATIVE_OUTCOME_SHA256={'unit-a': {'3444081f1cd8132261ba380cf758c91a': '4e400296e98d3e005426ed4a60e09fc743c933510c33328972f72f470d53f724', 'acb48986aeed5a9c0358312e4574a93e': 'bd9218fbe0dcbdb9504cb6a67ba8693eed1bfe82ca5fa53e515c611686bc27ff'}, 'unit-b': {}, 'unit-c': {'3444081f1cd8132261ba380cf758c91a': '66b33938a5dfa319128959b822a0126c5e109615f4e382f4b1bfacb2ebea8e4a', 'acb48986aeed5a9c0358312e4574a93e': '645c9a286b1620ff5f1df055f9e648af29904113fbc42b598773d098bfde5654'}}


def _sha(value):return hashlib.sha256(canonical(value)).hexdigest()


def declared_partition(policy):
    """Full original reservations, never actual consumption or refundable credit."""
    require(type(policy) is ParentPolicy and policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and _sha(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==GFSK_MB1_BUNDLE_KIND,
        '3195_original_policy_changed')
    rows={}
    for unit in policy.units:
        extra=Cost() if unit.unit=='unit-b' else Cost(2560,600000000)
        require(unit.limits==unit.prior_debt.plus(extra),'3195_original_full_caps_changed')
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
        '3195_original_owner_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(context.transport is not None,'3195_exact_MB1_transport_required')
    require(owner['bundle_capture_commitment']==public_context_commitment(owner['bundle_context'],parent_id=PARENT),
        '3195_original_capture_commitment_changed')
    return context


def retained_context(owner,policy,*,unit,observation):
    """Pure expired-context predicate; an actual collector remains mandatory."""
    context=original_context(owner,policy,unit=unit)
    require(type(observation) is dict and observation.get('status')=='scoped_rows_observed'
        and observation.get('parent_id')==PARENT and observation.get('policy_digest')==POLICY_DIGEST
        and observation.get('unit')==unit and observation.get('policy_expired_at_observation') is True,
        '3195_actual_expired_scope_required')
    epoch,mono=(observation.get(k) for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
    require(type(epoch) is int and type(mono) is int
        and epoch>=ORIGINAL_PHASE_END>=policy.expires_epoch_us
        and mono>=owner['native_phase'][unit]['deadline_monotonic_us'],'3195_original_clocks_live')
    context._check();return context


def require_final_bindings():
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (FINAL_RESULT_SHA256,EXPIRY_COMPLETION_SHA256,NEXT_HISTORY_RECEIPT_SHA256)),
        'pending_3195_final_result_expiry_and_history')
    require(set(NATIVE_DATABASE_SHA256)==set(NATIVE_OUTCOME_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in NATIVE_DATABASE_SHA256.values())
        and all(type(v) is dict for v in NATIVE_OUTCOME_SHA256.values())
        and NATIVE_OUTCOME_SHA256['unit-b']=={}
        and all(type(run) is str and len(run)==32 and pin(value)
            for outcomes in NATIVE_OUTCOME_SHA256.values() for run,value in outcomes.items()),
        'pending_3195_native_terminal_metadata')
    # Outcome cardinality and complete/failed partition are bound only after
    # actual completion. The two retired grants do not imply whole-file application completion.
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)>=36 and PAID_HISTORY[:35]==H35_PREFIX and PAID_HISTORY[35]==PARENT,
        '3195_H35_prefix_changed')
    return PAID_HISTORY[:36]
