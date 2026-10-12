"""Exact expired e8aa retention contract; no live authority.

Actual terminal result, original-expiry observations and H34 receipt are pinned. Pure historical
context decoding intentionally does not consult a later live GFSK scope.
"""
import hashlib
from dataclasses import asdict

from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy,Cost,canonical
from .pilot_bundle import BundleContext,public_context_commitment
from .profiles import GFSK_BUNDLE_KIND,GFSK_NAME,profile_for,profile_record
from .retained_capture import PAID_HISTORY

PARENT='e8aac38a787f33daa91c80e39cfc8710'
POLICY_DIGEST='b86dde057f19d8d38f62743cb56b2d9109a5be4f90fc8d93e2ab2c6381d744eb'
POLICY_SHA256='0b5a0fc30924e050dd473500a165694c164c87c0f7c0dd3f87829da8803ed136'
OWNER_SHA256={
    'unit-a':'e8a6262e99329f22d052844c7ac73f3a3205eafdaa30ae46618420abfcd96a3f',
    'unit-b':'127d05ef8d47f1148cf84f4c38ca2ce3c35883de419f02d92e97fef2de13c756',
    'unit-c':'6f334ffc046dafe6d1b87243311fa75654831bb4fb028f30b603138409eb9cf7',
}
ORIGINAL_SCOPE_SHA256='4698c7f7f7caa20f7c390e6b9b747cbdd7cb481e2407b066718a52b8814507fc'
ORIGINAL_RUNTIME_SHA256='4fbb27f88482e49fe7911cfae8ea5d03c6e83705641cda0594fcd7c8032a550d'
ORIGINAL_MANIFEST_SHA256='e6a111d55f4d9a51c08210eb7a04e7e630f1d27a06a6840e85225fd2f75073f8'
ORIGINAL_PHASE_END=1791831169309719
H33_PREFIX=PAID_HISTORY[:33]

# Bind only from the actual final local result and independently captured
# original-expiry/native metadata. No success/consumption is presumed here.
FINAL_RESULT_SHA256='c71b030dd357aa1e109b716f0e7ae1f9f6fb4ba1a64a0fad01e657aa4f79bcc4'
EXPIRY_COMPLETION_SHA256='b8d4b312298ec0296a6240b52aeda5ee064edc1da309ac931fa985b5134a2bd6'
NEXT_HISTORY_RECEIPT_SHA256='ddebe2592ab011cad4a7c4e0057764e2cddab2350b4e48e37eb125a0f0e5a648'
NATIVE_DATABASE_SHA256={'unit-a': '93d2669d13e3f8dac6e46d429dfad62e94f15dd4b17c7f76a2acf070b62d1cd4', 'unit-b': '3b8cdd1130fdc5928eff569778e24aff88f46ce98b2dfefae09dc4873a9e20ca', 'unit-c': '11b8613ec8861dd7ebaed5db03bd992a3596e186d079ad3b28e46431277a2576'}
NATIVE_OUTCOME_SHA256={'unit-a': {'c7f72631b9658efae6b2195074325be8': '3b7e1c7a970e5f52df8d82251e89446efb2d735271acbc731b464eba208d5f01', '4d5b0c2f618958afd9e1ca5a2a76d431': '360e3f88c0c024e1dd170a1b73ffe3405786575532d3ecefa4c7ac83a4ce13eb', 'c0acfa708c98382c004aa1802b1b0441': '3e7d67326b01d3067eccf19a0fce295cd1e30482e4cfdbef77f1d5dde8a78be3', 'dcd2177faf5117c79e1437156f1c77fc': '78e352072b62d019195372ceb8ebf784fff7915d713b174795358e2993078edb', 'b62a4dacb8437ee261be4b3a9dedc8d5': '7304f29f1f4f569741546874d786cde4e42808d9366dc7bb66215135c3bcb786', 'ae67cf8931d9c0bc8cd51e1361aa2059': 'd2078681928bda9d77b231812207a875e8afdbd103fcda3e464745771f5376a4', 'ce039ecca328cd39d481621101471c27': '1d36ba748e4abee471c88224e15d9d728cc0a9a8b55c73eb356798369517bad0', 'cac37da4942ec1b2447ed12b8ffc3eb4': '70b5ff9b7c2e067f6c1f981bcf5cc1b4de548df3425fd4938d95c2c46d4ad591', '56ac8d3e1d4654ca357a56d6561e32be': '1d824fda4f5aa9d2e4e6541f7ca50d0d36d53cb7ddce849cd61431f1ec8b5b42', '3f0acf0bf40cbd59d25f3d71f1aeb0e7': '33ce159def8176d96b98a05bd93bd1ba2cb6341ac2658c44f31bd22fde77ee9e'}, 'unit-b': {}, 'unit-c': {'c7f72631b9658efae6b2195074325be8': '9cca1f6fe56f42e45435b2d6b7ac60d33c6d64dc15be1228cf8b0c2e0b09a3c3', '4d5b0c2f618958afd9e1ca5a2a76d431': '0c4e6c91d95bb62b38c3604974e4604cf7220f02988fa51cd4e458a374a54798', 'c0acfa708c98382c004aa1802b1b0441': '407932d6adc9126b1e4118b85520df2adf4cd6050216a05abb7a6492feec095f', 'dcd2177faf5117c79e1437156f1c77fc': '8eb796d5219a47a50fa94793d0e5d58b6029a7823f78060b91225f60e88f1256', 'b62a4dacb8437ee261be4b3a9dedc8d5': '51af3bdf6e520098572fbc2e324bf94f1f65fef4f24a01491413cc693fb0a98f', 'ae67cf8931d9c0bc8cd51e1361aa2059': '2a021796130b6febd5e2acf6d82e2c408ee73dfd7834d21834e9605821bd1f01', 'ce039ecca328cd39d481621101471c27': '7590cf33f2d3f3078bf78ef8c601bdcd44a2b080388f82b341d04013dcce1303', 'cac37da4942ec1b2447ed12b8ffc3eb4': 'be8c9ffe3cce50deee1b40f58b3b18b63c59e9c3246013399d0bfba3fd72cc02', '56ac8d3e1d4654ca357a56d6561e32be': '1db47b74fdf6766eb04615ce97e4d7472c4f16af9d4532eb448703fa901eaee7', '3f0acf0bf40cbd59d25f3d71f1aeb0e7': 'c51bf8ed86ce24f2917b22e3c1e307ad2004e2a5217bc2e08c061ab660f3dd93'}}


def _sha(value):return hashlib.sha256(canonical(value)).hexdigest()


def declared_partition(policy):
    """Full original reservations, never actual consumption or refundable credit."""
    require(type(policy) is ParentPolicy and policy.parent_id==PARENT and policy.digest==POLICY_DIGEST
        and _sha(policy.to_dict())==POLICY_SHA256 and policy.qualification_kind==GFSK_BUNDLE_KIND,
        'e8aa_original_policy_changed')
    rows={}
    for unit in policy.units:
        extra=Cost() if unit.unit=='unit-b' else Cost(2560,600000000)
        require(unit.limits==unit.prior_debt.plus(extra),'e8aa_original_full_caps_changed')
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
        'e8aa_original_owner_changed')
    context=BundleContext(owner['bundle_context'],policy)
    require(owner['bundle_capture_commitment']==public_context_commitment(owner['bundle_context'],parent_id=PARENT),
        'e8aa_original_capture_commitment_changed')
    return context


def retained_context(owner,policy,*,unit,observation):
    """Pure expired-context predicate; an actual collector remains mandatory."""
    context=original_context(owner,policy,unit=unit)
    require(type(observation) is dict and observation.get('status')=='scoped_rows_observed'
        and observation.get('parent_id')==PARENT and observation.get('policy_digest')==POLICY_DIGEST
        and observation.get('unit')==unit and observation.get('policy_expired_at_observation') is True,
        'e8aa_actual_expired_scope_required')
    epoch,mono=(observation.get(k) for k in ('observation_original_epoch_us','observation_original_monotonic_us'))
    require(type(epoch) is int and type(mono) is int
        and epoch>=ORIGINAL_PHASE_END>=policy.expires_epoch_us
        and mono>=owner['native_phase'][unit]['deadline_monotonic_us'],'e8aa_original_clocks_live')
    context._check();return context


def require_final_bindings():
    def pin(value):return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(all(pin(v) for v in (FINAL_RESULT_SHA256,EXPIRY_COMPLETION_SHA256,NEXT_HISTORY_RECEIPT_SHA256)),
        'pending_e8aa_final_result_expiry_and_history')
    require(set(NATIVE_DATABASE_SHA256)==set(NATIVE_OUTCOME_SHA256)=={'unit-a','unit-b','unit-c'}
        and all(pin(v) for v in NATIVE_DATABASE_SHA256.values())
        and all(type(v) is dict for v in NATIVE_OUTCOME_SHA256.values())
        and NATIVE_OUTCOME_SHA256['unit-b']=={}
        and all(type(run) is str and len(run)==32 and pin(value)
            for outcomes in NATIVE_OUTCOME_SHA256.values() for run,value in outcomes.items()),
        'pending_e8aa_native_terminal_metadata')
    # Outcome cardinality and complete/failed partition are bound only after
    # actual completion. A currently planned ten-grant bound is not proof.
    return True


def pending_next_roster():
    require_final_bindings()
    require(len(PAID_HISTORY)==34 and PAID_HISTORY[:33]==H33_PREFIX and PAID_HISTORY[-1]==PARENT,
        'e8aa_H33_prefix_changed')
    return PAID_HISTORY
