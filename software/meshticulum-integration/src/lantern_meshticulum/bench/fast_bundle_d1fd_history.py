"""Exact failed d1fd reservation; raw MCU owner, zero native children, no refund.

Historical decoding grants no current scope. Metadata/next receipt pins remain
pending until the original public files and all 44 controllers are retained.
"""
import hashlib
import os
import sys
from dataclasses import asdict
from functools import partial

from meshticulum.bench.wire import require
from . import gfsk250_mb1_partial_history as shared, fast_bundle_bea_history as bea
from .mcu_artifact import decode_owner
from .pilot_parent_caps import Cost, canonical
from .profiles import GFSK250_FAST_BUNDLE_KIND as QUALIFICATION_KIND

PARENT = 'd1fd73cd772ff055140f17ca986a07d0'
POLICY_DIGEST = '1ce4ceb934200048b1210568022532ddb578793e5e849bf7cf199161af40ffc0'
POLICY_SHA256 = '73a1bdf3aed5a811ee2bdfa1ce706df3927baf7ad57011987c88cb4218e25d83'
ORIGINAL_SCOPE_SHA256 = '16f73f63ce91875dfcb907733ebae16b97a78450c7c769a3c9066b9752eac78e'
ORIGINAL_RUNTIME_SHA256 = '71a1b8e5445eebb1cbe227999b61f97e5c0217e3fb44bcdca21fdfa2cd6d9ded'
ORIGINAL_MANIFEST_SHA256 = '25762fd3bfdbc7844e315b037c3460374674a777a178036247ce0d329aacc1a2'
ORIGINAL_MCU_ARTIFACT_SHA256 = '72b1289b782f54940c0bced77e19118e5c8ae47e1fea51ecf112e597419c582b'
ORIGINAL_HISTORY_RECEIPT_SHA256 = bea.NEXT_HISTORY_RECEIPT_SHA256
ORIGINAL_PHASE_END = 1792010822091446
ORIGINAL_ABSOLUTE_END = 1792010917411073
FINAL_RESULT_SHA256 = '6027e9c624c4d556d59cd138ed28f75aa0bb66a0bbc716e97694fc64d5341066'
NATIVE_OBSERVATION_SHA256 = '415ab77709dae6d9efec7e1017484624f927ad37632eaeb597c8edaba53abb10'
CONTROLLER_OBSERVATION_SHA256 = '8ad65e5c59800426815fe9aa2bc7d81c9610e94e5a0a5ee9fa2c5221403e4ef1'
RAW_OWNER_SHA256 = {
    'unit-a': 'b138c6a4dd363763bb4acb47107072e5bfc9dcbe914fcd4e5990a668acb4b3e8',
    'unit-b': '118b9156b98cf998bf21998d75cb6d4b6971a618f47c503509a762651a190726',
    'unit-c': 'f875e865c63ab16f07894f624dc0b5b3c09293a812c27d8ba36153f196560bbe'}
OWNER_SHA256 = {
    'unit-a': '0fcd2f4619479d2ad18e89fcc0497c411aff87152ba16f400ec4a742909912d1',
    'unit-b': 'ad59b37eae1efcf1a1f12e85b271129a928af33def95b27f1a9c40de94a85913',
    'unit-c': '8a4211ae9984c6e9a892c0e4df9b73ecf9b0fa3915ba796fd16baddd9e2cd847'}
NATIVE_DATABASE_SHA256 = {
    'unit-a': '5593cf6c82a5bde714dd33428ba91113da350ac1932f06adf57cd76c77f81ec6',
    'unit-b': '26c8b0887247feb7691803fbb16a3aa5f7e662f17835cbe899cb58dfb4f87537',
    'unit-c': '42cf1658ec40c2571a56baa639edd64a26766f97bd1600fea167180772746b70'}
PUBLIC_METADATA_SHA256 = {'unit-a': {'capture.json': 'd11db882ec3b49bb046e449a474160e72d4f6fa98b803061da46ca1e25e30e86', 'fast-stage-0.json': '0e9247f74a0e243adabcddb4988b0ccbf0898d27af9ce11fc91d6b488d690e84', 'history-binding.json': '249a597516330feda67f8f7edb118a9e44e34050acfab70c91996ebfac433a32', 'history-identity-seal.json': 'ebe54cf7fa0866753fdf8a10a0305b1a49166fc0e3ed4ce3639bf1ee8e5b292a', 'legacy-grant-seal.json': '0dfaff049fb4321872fc68c2b29460673805d72f728c8e3e8b3133e9a3c2082e', 'owner.json': 'b138c6a4dd363763bb4acb47107072e5bfc9dcbe914fcd4e5990a668acb4b3e8', 'policy.json': '73a1bdf3aed5a811ee2bdfa1ce706df3927baf7ad57011987c88cb4218e25d83', 'prior.json': '202b43dc83745535baf623a3b0917c8b96bb41b284974d0428a28689e154a462'}, 'unit-b': {'capture.json': 'a2f9f21ed99f8410bd8f747a145d69c2afbedc975615cc92d392724849ff966f', 'history-binding.json': '0e87da04a4149a233b160d6a192ac536dd5bae780c05a9984f3a3fda1c5280d3', 'history-identity-seal.json': 'b7da0b7a6a6d73367640063ab28f93adf715184fa03aa705a508ab3536d5c348', 'legacy-grant-seal.json': '95539e6026afa5b68d01686e15c50661b2d3d1cca5079957d3c682e69f05859a', 'owner.json': '118b9156b98cf998bf21998d75cb6d4b6971a618f47c503509a762651a190726', 'policy.json': '73a1bdf3aed5a811ee2bdfa1ce706df3927baf7ad57011987c88cb4218e25d83', 'prior.json': 'da17bc2bf2f22d2e936cec3cc36aae68477bbb87ca22290e888f95535eeeb341'}, 'unit-c': {'capture.json': '731ed6e00d35c60dbc9bfcfaaabbe9f58c6b7f77a5c60b6872dc18c9c3b32b80', 'fast-stage-0.json': '0d3c3fd3023361b29a76f9158b24f6c8b04edfdd25d573fda2360f78c8e84112', 'history-binding.json': 'f0ee2cdb6a1fa6f714e26cdf07a7007e8113e414fd5036a138c8a830d2706bd3', 'history-identity-seal.json': 'e29588411a69bdc45c4ddf3b62f86d69474bc70f609a3f51cc2ad8ca3b54fef3', 'legacy-grant-seal.json': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc', 'owner.json': 'f875e865c63ab16f07894f624dc0b5b3c09293a812c27d8ba36153f196560bbe', 'policy.json': '73a1bdf3aed5a811ee2bdfa1ce706df3927baf7ad57011987c88cb4218e25d83', 'prior.json': '5fd28b645f92601c87653b21c826309250e4d340d79bd7656e1662f1ddd93ce6'}}
METADATA_SUPPLEMENT_SHA256 = 'f55738b239f92f3fde7c8ca984d8edd228603f5a7601a741bac953d52f7f6292'
NEXT_HISTORY_RECEIPT_SHA256 = '04f6e18c9f9425796dc31fb203b7d92d826b6ef775a597903b2466aaf91cbee4'
CONTROLLER_ENDPOINT_REFS_SHA256 = {'unit-a': '8ecab9245b0fed16ad6733243c96a432c594fe75ffc2bf040a00fb9f6c97a9b2', 'unit-b': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945', 'unit-c': 'f46cf434b8691fe9a4f105cc7cf9a50acc8ae7034aec036a43d92d15a90db65a'}
H43_PREFIX = bea.H42_PREFIX + (bea.PARENT,)
ORIGINAL_RUN_COMPLETE = False
ORIGINAL_CLEANUP_PROVED = False


def _sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


declared_partition = partial(shared._declared_partition, sys.modules[__name__])


def owner_view(record, policy, *, unit):
    """Strict semantic view while the persisted/hash-bound record stays v2."""
    declared_partition(policy)
    require(unit in RAW_OWNER_SHA256 and type(record) is dict
            and _sha(record) == RAW_OWNER_SHA256[unit], 'd1fd_raw_owner_changed')
    owner, _ = decode_owner(record)
    require(_sha(record['mcu_artifact']) == ORIGINAL_MCU_ARTIFACT_SHA256
            and owner['history_receipt_sha256'] == ORIGINAL_HISTORY_RECEIPT_SHA256,
            'd1fd_original_artifact_or_history_changed')
    shared._original_context(sys.modules[__name__], owner, policy, unit=unit)
    return owner


def original_context(record, policy, *, unit):
    owner = owner_view(record, policy, unit=unit)
    return shared._original_context(sys.modules[__name__], owner, policy, unit=unit)


def retained_context(record, policy, *, unit, observation):
    owner = owner_view(record, policy, unit=unit)
    return shared._retained_context(sys.modules[__name__], owner, policy, unit=unit, observation=observation)


def require_final_bindings():
    def pin(value):
        return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)
    require(pin(METADATA_SUPPLEMENT_SHA256) and pin(NEXT_HISTORY_RECEIPT_SHA256)
            and set(PUBLIC_METADATA_SHA256) == {'unit-a', 'unit-b', 'unit-c'}
            and all(type(v) is dict and set(v) == {'policy.json', 'owner.json', 'prior.json', 'capture.json',
                'history-binding.json', 'history-identity-seal.json', 'legacy-grant-seal.json'}
                | (set() if u == 'unit-b' else {'fast-stage-0.json'})
                and all(pin(p) for p in v.values()) for u,v in PUBLIC_METADATA_SHA256.items()),
            'pending_d1fd_public_metadata_and_history')
    return True


def pending_next_roster():
    from .retained_capture import PAID_HISTORY
    require_final_bindings()
    require(len(PAID_HISTORY) >= 44 and PAID_HISTORY[:43] == H43_PREFIX and PAID_HISTORY[43] == PARENT,
            'd1fd_H43_prefix_changed')
    return PAID_HISTORY[:44]


def qualify_costs(unit, policy, observed, value):
    """Keep full enrollment and separate native/controller prepaid observations."""
    require_final_bindings(); retained_context(value['owner'], policy, unit=unit, observation=observed)
    detail = observed['detail']; local = policy.unit(unit)
    extra = Cost() if unit == 'unit-b' else Cost(2560, 600000000)
    controller = Cost() if unit == 'unit-b' else Cost(256, 60000000)
    controller_unused = Cost() if unit == 'unit-b' else Cost(2304, 540000000)
    require(all(type(detail[k]) is int and detail[k] == 0 for k in
                ('claim_count', 'resource_count', 'prepared_count', 'start_count'))
            and detail['claims'] == detail['resources'] == []
            and detail['full_prepaid_caps'] == asdict(Cost())
            and detail['prior_plus_prepaid_caps'] == asdict(local.prior_debt) == value['actual_native_paid_caps']
            and detail['limit'] == value['paid_caps'] == asdict(local.limits)
            and value['actual_controller_paid_caps'] == asdict(local.prior_debt.plus(controller))
            and value['conservative_unused_enrollment_caps'] == asdict(extra)
            and value['conservative_unused_controller_caps'] == asdict(controller_unused)
            and _sha(value['controller_observed_endpoint_refs']) == CONTROLLER_ENDPOINT_REFS_SHA256[unit]
            and value['native_controller_debt_views_may_be_summed'] is False,
            'd1fd_zero_children_or_full_debt_changed')
    owner = owner_view(value['owner'], policy, unit=unit)
    require(observed['scoped_rows_complete'] is True and observed['pending_unknown'] is False
            and detail['approval_bytes_match_enrollment'] is True
            and detail['enrollment']['history_binding_sha256'] == owner['history_binding_sha256']
            and observed['observation_original_monotonic_us'] >= detail['enrollment']['deadline_monotonic_us']
            and observed['expected_database_sha256'] == value['database_sha256'] == NATIVE_DATABASE_SHA256[unit]
            and value['runs'] == value['retired_unstarted'] == value['failed_started_runs'] == []
            and value['preparation_sha256'] == {} and value['controller_endpoint_refs'] == []
            and value['legacy_sha256'] is observed['legacy_runs'] is observed['legacy_relation'] is None
            and value['require_result_complete'] is value['original_cleanup_proved'] is False,
            'd1fd_original_failed_retention_changed')


def metadata_files(reader, state, unit, value):
    """Exact retained public files, including the unconsumed stage on A/C."""
    require_final_bindings()
    target = state/'parents'/PARENT; identities = target/'prepared-identities'
    reader.directory(target); reader.directory(identities)
    pins = PUBLIC_METADATA_SHA256[unit]
    stages = {n:p for n,p in pins.items() if n.startswith('fast-stage-')}
    require(value['fast_stage_sha256'] == stages
            and set(os.listdir(target)) == set(pins) | {'native.sqlite', 'native.sqlite.owner', 'native.sqlite.approval', 'prepared-identities'}
            and not os.listdir(identities), 'd1fd_metadata_namespace_changed')
    files = {}
    for name, pin in pins.items():
        maximum = 2500000 if name == 'capture.json' else (8192 if name == 'fast-stage-0.json' else 2000000)
        raw = reader.read_held(target/name, maximum)
        require(hashlib.sha256(raw).hexdigest() == pin, 'd1fd_public_metadata_bytes_changed')
        files[str((target/name).relative_to(state))] = pin
    reader.check(); return files
