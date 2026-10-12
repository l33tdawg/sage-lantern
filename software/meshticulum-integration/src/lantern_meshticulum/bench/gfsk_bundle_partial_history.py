"""Exact expired 73c1 retention contract; no live authority.

Actual terminal result, original-expiry observations and H35 receipt are pinned. Pure historical
context decoding intentionally does not consult a later live GFSK scope.
"""
import hashlib
from dataclasses import asdict

from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy,Cost,canonical
from .pilot_bundle import BundleContext,public_context_commitment
from .profiles import GFSK_BUNDLE_KIND,GFSK_NAME,profile_for,profile_record
from .retained_capture import PAID_HISTORY

PARENT='73c1cda8c02939409d0b1e7e2a39be67'
FAILED_STARTED_RUNS={'unit-a':['a0d6a932b22ba84884e03317fde00bc8'],'unit-b':[],'unit-c':[]}
POLICY_DIGEST='5ca7ccb7c509daeebe6c05ab4c1024bb94df8593a4ad5d1b8f5f5637e4549617'
POLICY_SHA256='1efe3d7c8004d17797f1883d9fd0be648a8b67388fcdf9f8f54d1630a704d3f0'
OWNER_SHA256={'unit-a': '01e9f0001c180f333a58e3417444262eaabb35e54dc3499ddb25de37ef0c94a1', 'unit-b': '2e36fa56fa05c602ec2db15364acd224fec84423135b2d20902f56a7f1c6418b', 'unit-c': 'd136b9d9e8c6376a28b21929931023b9cb14b35b74d2922e67bf5bfb9e1e145d'}
ORIGINAL_SCOPE_SHA256='3e386a2fcd001e1a4faa2ffaabf258398f1d7391130fd742854ed3638ba64227'
ORIGINAL_RUNTIME_SHA256='86c12084111fb68d3ecc2cb137229c86b115ee88c86daf5f3542bfa4738aaabc'
ORIGINAL_MANIFEST_SHA256='39ca67ba9ff2e33df8fea601f0e000e1dc82c4ad5c76a16a600167ab161ad3a4'
ORIGINAL_PHASE_END=1791836281102724
H34_PREFIX=PAID_HISTORY[:34]

# Bind only from the actual final local result and independently captured
# original-expiry/native metadata. No success/consumption is presumed here.
FINAL_RESULT_SHA256='6e3f5fc761d5f75222ad83d55cb6cd972a5cfa192b001dc57956bb1eac6789dd'
EXPIRY_COMPLETION_SHA256='81adc351f395637b980de2c0519f386d790a1068b26b79bdd6cf7361233d7da6'
NEXT_HISTORY_RECEIPT_SHA256='37d961c7e5078fb98a5c4e3e4b31b7aac51d0afa1433966a56b47c00d2de36ca'
NATIVE_DATABASE_SHA256={'unit-a': '56f2745df02981ca797b88cc5f1d148677b6042d679d856fe1f43092cf776275', 'unit-b': 'dffd97426d2d8789a234fc3018f872791430eb09b3734dfb0b6cd513da974b10', 'unit-c': '2f980ceb203df04bce7aa5edbbb927e45b96f185413fa37947caee56f9c038ff'}
NATIVE_OUTCOME_SHA256={'unit-a': {'547ba896626043acb56b7596b0483f94': 'b78e1635f2ed10ae0edb0a5d17474edf15f29491dd1654497a541899ad80e17e', 'a0d6a932b22ba84884e03317fde00bc8': '10987fa62862de9d604fbbf9f743691516c7950681db2dc7cdadfc751c1daa40'}, 'unit-b': {}, 'unit-c': {'547ba896626043acb56b7596b0483f94': '633ce57f22d2aa8083c3d249b3815107529449f177faff91064b4aefc6166c90', 'a0d6a932b22ba84884e03317fde00bc8': '117e7dd495db8c2ed591f9a5ae17c5ab3cd365d8619cd02d6e4efa359ee395f3'}}


def _sha(value):return hashlib.sha256(canonical(value)).hexdigest()


def declared_partition(policy):
    """Full original reservations, never actual consumption or refundable credit."""
    require(type(policy) is ParentPolicy and policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and _sha(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==GFSK_BUNDLE_KIND,
        '73c1_original_policy_changed')
    rows={}
    for unit in policy.units:
        extra=Cost() if unit.unit=='unit-b' else Cost(2560,600000000)
        require(unit.limits==unit.prior_debt.plus(extra),'73c1_original_full_caps_changed')
        rows[unit.unit]=dict(full_original_caps=asdict(unit.limits),prior_caps=asdict(unit.prior_debt),
            declared_new_reservation=asdict(extra),actual_consumption=None,refunds=0)
    return rows


def original_context(owner,policy,*,unit):
    """Exact original signed data/clock decoding, with no admission side effect."""
    declared_partition(policy)
    require(unit in OWNER_SHA256 and type(owner) is dict and _sha(owner)==OWNER_SHA256[unit]
        and owner['policy_digest']==POLICY_DIGEST and owner['qualification_kind']==GFSK_BUNDLE_KIND
        and owner['radio_profile_name']==owner['radio_modem']==GFSK_NAME
        and owner['rf_profile']==profile_record(profile_for(GFSK_NAME))
        and owner['gfsk_bundle_scope_sha256']==ORIGINAL_SCOPE_SHA256
        and owner['runtime_sha256']==ORIGINAL_RUNTIME_SHA256 and owner['manifest_sha256']==ORIGINAL_MANIFEST_SHA256
        and owner['phase_start_epoch_us']==policy.issued_epoch_us and owner['phase_end_epoch_us']==ORIGINAL_PHASE_END,
        '73c1_original_owner_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(owner['bundle_capture_commitment']==public_context_commitment(owner['bundle_context'],parent_id=PARENT),
        '73c1_original_capture_commitment_changed')
    return context


def retained_context(owner,policy,*,unit,observation):
    """Pure expired-context predicate; an actual collector remains mandatory."""
    context=original_context(owner,policy,unit=unit)
    require(type(observation) is dict and observation.get('status')=='scoped_rows_observed'
        and observation.get('parent_id')==PARENT and observation.get('policy_digest')==POLICY_DIGEST
        and observation.get('unit')==unit and observation.get('policy_expired_at_observation') is True,
        '73c1_actual_expired_scope_required')
    epoch,mono=(observation.get(k) for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
    require(type(epoch) is int and type(mono) is int
        and epoch>=ORIGINAL_PHASE_END>=policy.expires_epoch_us
        and mono>=owner['native_phase'][unit]['deadline_monotonic_us'],'73c1_original_clocks_live')
    context._check();return context


def require_final_bindings():
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (FINAL_RESULT_SHA256,EXPIRY_COMPLETION_SHA256,NEXT_HISTORY_RECEIPT_SHA256)),
        'pending_73c1_final_result_expiry_and_history')
    require(set(NATIVE_DATABASE_SHA256)==set(NATIVE_OUTCOME_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in NATIVE_DATABASE_SHA256.values())
        and all(type(v) is dict for v in NATIVE_OUTCOME_SHA256.values())
        and NATIVE_OUTCOME_SHA256['unit-b']=={}
        and all(type(run) is str and len(run)==32 and pin(value)
            for outcomes in NATIVE_OUTCOME_SHA256.values() for run,value in outcomes.items()),
        'pending_73c1_native_terminal_metadata')
    # Outcome cardinality and complete/failed partition are bound only after
    # actual completion. The two retired grants do not imply whole-file application completion.
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)==35 and PAID_HISTORY[:34]==H34_PREFIX and PAID_HISTORY[-1]==PARENT,
        '73c1_H34_prefix_changed')
    return PAID_HISTORY
