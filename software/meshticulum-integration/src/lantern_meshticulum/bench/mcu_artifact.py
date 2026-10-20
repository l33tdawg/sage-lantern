# SPDX-License-Identifier: Apache-2.0
"""Explicit source-artifact selection; never RF or device authorization.

The old modem/firmware tables remain the default. The new public envelope binds
this selection before prepayment. Source pins identify the reviewed finite
scope and retained history; each native operation still requires current
ownership, a fresh device inspection and its own original finite admission.
"""
import hashlib
import json
from types import MappingProxyType

class McuArtifactError(ValueError):
    pass


def require(value, reason):
    if not value: raise McuArtifactError(reason)

LEGACY_SELECTOR = 'gfsk250-usb-rx2048-v1'
PLAN_SCHEMA = 'meshticulum.public-mcu-pilot-package.v1'
PAID_SCHEMA = 'meshticulum.public-mcu-pilot-package.v2'
PROVISION_SCHEMA = 'meshticulum.provisioned-public-mcu-pilot.v1'
OWNER_SCHEMA = 'meshticulum.finite-public-mcu-resume-owner.v2'
META_SCHEMA = 'meshticulum.public-mcu-child-metadata.v1'
OPERATION_SCHEMA = 'meshticulum.public-gfsk250-usb-rx2048-irq-mb1-fast-bundle100k-operation.v2'
SSH_SCHEMA = 'meshticulum.finite-mcu-gfsk250-mb1-fast-ssh-pilot.v1'
KIND = 'c-gfsk250-mb1-fast-bundle100k-v1'
LEGACY_CORE = '2eacc798db2841c20b9466500825b79886992dd1'
LEGACY_FIRMWARE = 'mtb1-4f6cfc6b63'
LEGACY_IMAGE_SHA256 = '73d44a3194dec9493a101c8e041beb61a410e1e781d741e7ecc9d4cf7e1e0b5d'

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

_ARTIFACT = canonical(dict(
    schema='meshticulum.explicit-mcu-artifact.v1', selector=LEGACY_SELECTOR,
    local_unit='unit-a', target='mtbench_heltec_v4', board_id=1,
    firmware=LEGACY_FIRMWARE, image_bytes=391424, image_sha256=LEGACY_IMAGE_SHA256,
    radio_modem='gfsk250', capability_record_version=2,
    core_revision=LEGACY_CORE, public_source_revision='36091801f8e182d678195ad6b205c9aa1e3d0640',
    source_lock_sha256='bfe550244ae41d5e11cbb5009dd4a5558f178e6ac134adfc6b39ea889903182e',
    recipe_sha256='92eab8413b0e4dce22ff57ff3dc3abf4aa0909a7f30f1d269b7a79bb58116805',
    input_lock_sha256='9d2d534dbf784db3c13b729ab91d20832e0dd536dd8edf7ce2ccd79985c6dfb2',
    application=dict(
        **{'main.cpp':'45eef0423fbc364204f3d6e764f176d2a7eb3be4e42e8c3e217c6111269a5eea',
        'protocol.hpp':'9664157bd3c526739317e18f6120c332f078026012bdb1271bd6254fe3419ad7',
        'gfsk_profile.hpp':'eae3b0883490663c89bdd6b636aa054e0031e294651185e52cf4ebd292cb4d82',
        'gfsk250_profile.hpp':'379c0023940a6091a93c20f2199f2dcb0f70e7678c680681107622ea71cd6aba'}),
    reproduced_build_sha256=[
        'c26b89b9f233ca4c2e20406c9718821bfa276c889a528f44c6053fe4154571c7',
        'e3568639d3dc423ea86a89369eeba7b2d5f1d10da7a8ed283d78196251d130f3'],
    reproduced_source_sha256=[
        'f9da4ff556dcde50ca924d6e476ec0eb38a37552394bb068a880307ceb5b4422',
        'a06451074dd60c052df1fbfc8f2ce21a830b020acf1daf95980626bd345dc9a1'],
    verified_artifacts={
        'firmware.bin':LEGACY_IMAGE_SHA256,
        'firmware.elf':'ec5a17b8772dd28156aca391dda31a2e07e37657a6e1d6053f378d77579010c2',
        'bootloader.bin':'2a71d69b471e20c2bac7fb469f3c6a807b3ebee780e348e5889db0da849ca363',
        'partitions.bin':'bd0f7954aca2ef7d925ee21aaa1f3dc8822d1d6ce5cbbd26a135e5886bfff6ce'},
    source_only=True, flashed=False, rf_qualified=False, notices_complete=False))
LEGACY_ARTIFACT_SHA256 = hashlib.sha256(_ARTIFACT).hexdigest()
IRQ020_SELECTOR = 'gfsk250-usb-rx2048-irq-v1'
FIRMWARE_CORE = '2b9db9e71dfa7d115d4e465225893b828c760171'
FIRMWARE = 'mtb1-020e287255'
IMAGE_SHA256 = 'eca7772095cc9c9e63c9f6e6d9fe48ba3c376cd6bfef557ffcc441621a846fa2'
_IRQ_ARTIFACT = canonical(dict(
    schema='meshticulum.explicit-mcu-artifact.v2', selector=IRQ020_SELECTOR,
    local_unit='unit-a', target='mtbench_heltec_v4', board_id=1,
    firmware=FIRMWARE, image_bytes=391600, image_sha256=IMAGE_SHA256,
    radio_modem='gfsk250', capability_record_version=2,
    core_revision=FIRMWARE_CORE, runtime_core_revision=FIRMWARE_CORE,
    source_lock_sha256='b504150554abdf068eeabe1d140db1a0c132ecb93c84c3b351049cf78ca4d904',
    recipe_sha256='92eab8413b0e4dce22ff57ff3dc3abf4aa0909a7f30f1d269b7a79bb58116805',
    input_lock_sha256='9d2d534dbf784db3c13b729ab91d20832e0dd536dd8edf7ce2ccd79985c6dfb2',
    application={
        'main.cpp':'d52db685d92cffad86a6245fe5818ab35cf258b9606d8dee44d5b2481f2e5062',
        'protocol.hpp':'9664157bd3c526739317e18f6120c332f078026012bdb1271bd6254fe3419ad7',
        'gfsk_profile.hpp':'eae3b0883490663c89bdd6b636aa054e0031e294651185e52cf4ebd292cb4d82',
        'gfsk250_profile.hpp':'379c0023940a6091a93c20f2199f2dcb0f70e7678c680681107622ea71cd6aba'},
    reproduced_build_sha256=[
        'f17fd745de786f52a8a1c0a8092ab4c91e92b7c9e4d37eca52d354ba20e083a6',
        '3727db379dbf032b34dba02990d94b804b12f11626b5fb1d3cf221324239d2f6'],
    reproduced_source_sha256=[
        '73b3bbd6fc62b97bfa49ff34c6f083a3b5c9115b294dec3fb3e6fc2a35556645',
        '9e99b7486914452bb770be28933407bfac2905e004e109ceab94f9f02c32dd2b'],
    verified_artifacts={
        'firmware.bin':IMAGE_SHA256,
        'firmware.elf':'0fb4a8c777c7d2f30d3e21439e6c959add702676f17e3028207235b5249827d1',
        'bootloader.bin':'2a71d69b471e20c2bac7fb469f3c6a807b3ebee780e348e5889db0da849ca363',
        'partitions.bin':'bd0f7954aca2ef7d925ee21aaa1f3dc8822d1d6ce5cbbd26a135e5886bfff6ce'},
    source_only=True, flashed=False, rf_qualified=False, notices_complete=False))
IRQ020_ARTIFACT_SHA256 = hashlib.sha256(_IRQ_ARTIFACT).hexdigest()
SELECTOR = 'gfsk250-usb-rx2048-irq-hostfair-v1'
CORE = 'fb1653c6c9cb344cf819e666e6c9c0af998307cf'
_CURRENT_ARTIFACT = canonical(dict(json.loads(_IRQ_ARTIFACT), selector=SELECTOR,
    runtime_core_revision=CORE, source_lock_sha256='61416c2271061ee8870eb49cfa3a7e83f9a6a6bb277879c9c48fdae0c12b1616'))
ARTIFACT_SHA256 = hashlib.sha256(_CURRENT_ARTIFACT).hexdigest()
_ARTIFACTS = MappingProxyType({LEGACY_SELECTOR:_ARTIFACT,
    IRQ020_SELECTOR:_IRQ_ARTIFACT, SELECTOR:_CURRENT_ARTIFACT})
PENDING_NATIVE_BINDINGS = MappingProxyType(dict(
    scope_sha256='d438401237d4fc2b7d187d6505beeefe663ab1fdd221fb6c38a25995e30a08bf',
    history_receipt_sha256='cf01a817a18458f0ca2b91dca012afcc29044a9c65bb426ee9316e003cb32826',
    preceding_parent='360514104bf80fa187276f24d6aa9d9c'))


def artifact(selector=SELECTOR):
    require(type(selector) is str and selector in _ARTIFACTS, 'unreviewed_mcu_artifact_selector')
    return json.loads(_ARTIFACTS[selector])


def current_artifact(selector=SELECTOR):
    require(type(selector) is str and selector == SELECTOR, 'historical_mcu_artifact_not_current')
    return artifact(selector)


def validate_artifact(value):
    require(type(value) is dict and type(value.get('selector')) is str
        and value['selector'] in _ARTIFACTS and canonical(value) == _ARTIFACTS[value['selector']],
        'mcu_artifact_binding_changed')
    return value


def select_runtime(manifest):
    value = manifest.get('mcu_artifact')
    if value is None:
        require('mcu_artifact' not in manifest, 'invalid_mcu_artifact_manifest')
        return None
    validate_artifact(value)
    require(manifest.get('radio_profile_name') == 'gfsk250'
        and manifest.get('native_operation_kind') == KIND
        and manifest.get('firmware') == value['firmware']
        and manifest.get('firmware_by_unit', {}).get('unit-a') == value['firmware'],
        'mcu_artifact_runtime_mismatch')
    if value['selector'] in (IRQ020_SELECTOR, SELECTOR):
        source_files = manifest.get('source_files', manifest.get('files'))
        require(type(source_files) is dict and source_files.get(
            'provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json') == value['source_lock_sha256'],
            'mcu_artifact_runtime_source_changed')
    return value


def owner_envelope(original_owner, selector):
    selected = artifact(selector)
    require(type(original_owner) is dict
        and original_owner.get('schema') == 'meshticulum.finite-public-resume-owner.v1'
        and original_owner.get('qualification_kind') == KIND
        and original_owner.get('core_commit') == selected.get('runtime_core_revision', selected['core_revision']),
        'mcu_original_owner_mismatch')
    return dict(schema=OWNER_SCHEMA, original_owner=json.loads(canonical(original_owner)),
        mcu_artifact=selected)


def decode_owner(value):
    require(type(value) is dict and set(value) == {'schema','original_owner','mcu_artifact'}
        and value['schema'] == OWNER_SCHEMA, 'invalid_mcu_owner_envelope')
    validate_artifact(value['mcu_artifact'])
    require(canonical(owner_envelope(value['original_owner'], value['mcu_artifact']['selector'])) == canonical(value),
        'mcu_original_owner_mismatch')
    return value['original_owner'], value['mcu_artifact']


def require_native_scope(value, policy, manifest):
    owner, selected = decode_owner(value)
    require(selected['selector'] == SELECTOR, 'historical_mcu_artifact_not_current')
    require(select_runtime(manifest) == selected and owner['policy_digest'] == policy.digest
        and owner['qualification_kind'] == policy.qualification_kind == KIND,
        'mcu_owner_runtime_or_policy_changed')
    # No claim of current consent or physical history is manufactured here.
    require_selector_scope(KIND)
    pins = dict(PENDING_NATIVE_BINDINGS)
    require(owner.get('mb1_bundle_scope_sha256') == pins['scope_sha256']
        and owner.get('history_receipt_sha256') == pins['history_receipt_sha256'],
        'mcu_artifact_native_scope_or_history_changed')
    return selected


def require_selector_scope(kind):
    from .retained_capture import PAID_HISTORY
    pins = dict(PENDING_NATIVE_BINDINGS)
    require(kind == KIND and all(type(pins[k]) is str and len(pins[k]) == 64
        and all(c in '0123456789abcdef' for c in pins[k])
        for k in ('scope_sha256','history_receipt_sha256'))
        and pins['preceding_parent'] == PAID_HISTORY[-1],
        'pending_mcu_artifact_native_scope_and_history')
    return pins


def validate_metadata(meta):
    require(type(meta) is dict and meta.get('schema') == META_SCHEMA and meta.get('transport') == 'lora'
        and meta.get('radio_modem') == 'gfsk250', 'invalid_mcu_child_metadata')
    validate_artifact(meta['mcu_artifact'])
    require(meta.get('mcu_artifact_sha256') == hashlib.sha256(canonical(meta['mcu_artifact'])).hexdigest(),
        'mcu_child_artifact_changed')
    return meta['mcu_artifact']


def validate_original_claim(parent, plan, selected):
    validate_artifact(selected)
    require(type(parent) is dict and type(plan) is dict, 'invalid_mcu_original_claim')
    child=parent['child_caps'];meta=child['metadata']
    require(validate_metadata(meta)==selected and plan.get('radio_modem')=='gfsk250'
        and plan.get('schema')=='meshticulum.service-run.v1', 'mcu_original_plan_changed')
    normalized=dict(plan);normalized.pop('application_deadline_monotonic_us',None)
    from .pilot_parent_caps import DOMAIN
    require(hashlib.sha256(canonical(normalized)).hexdigest()==meta['plan_sha256']==parent['original_plan_sha256']
        and hashlib.sha256(canonical(plan)).hexdigest()==parent['native_plan_sha256']
        and parent['grant_context_sha256']==meta['grant_digest']
        and hashlib.sha256(DOMAIN+b'child\0'+canonical(meta)).hexdigest()==child['digest'],
        'mcu_original_context_or_child_changed')
    return selected


# A separate LoRa source choice; the three paid GFSK descriptors above stay exact.
LORA_SELECTOR = 'lora-sf7-sf5-usb-rx2048-irq-v1'
LORA_KIND = 'c-lora-sf7-sf5-8k-comparison-v1'
LORA_RUNTIME_PROFILE = 'lora-sf7-sf5'
LORA_PROFILE_NAMES = ('sf7-500', 'sf5-500')
_LORA_ARTIFACT = canonical(dict(json.loads(_CURRENT_ARTIFACT),
    schema='meshticulum.explicit-lora-mcu-artifact.v1', selector=LORA_SELECTOR,
    radio_modem='lora', radio_profile_names=list(LORA_PROFILE_NAMES)))
LORA_ARTIFACT_SHA256 = hashlib.sha256(_LORA_ARTIFACT).hexdigest()
LORA_NATIVE_BINDINGS = MappingProxyType({'scope_sha256': '7826a42e6b642eb6b10a9c3b6394f39564c78e260d91838781a550ac66ee1671', 'history_receipt_sha256': '25d5b0ee2c7c6f5f3e4c70fdb96fbe868d67399e1ef4b2c7765fc81455569a6f', 'preceding_parent': '291fcf787d7b336134735a239a6e334d'})


def lora_artifact(selector=LORA_SELECTOR):
    require(type(selector) is str and selector == LORA_SELECTOR,
        'unreviewed_lora_mcu_artifact_selector')
    return json.loads(_LORA_ARTIFACT)


def validate_lora_artifact(value):
    require(type(value) is dict and canonical(value) == _LORA_ARTIFACT,
        'lora_mcu_artifact_binding_changed')
    return value


def select_lora_runtime(manifest):
    require(type(manifest) is dict, 'invalid_lora_runtime_manifest')
    value = manifest.get('lora_mcu_artifact')
    if value is None:
        require('lora_mcu_artifact' not in manifest
            and manifest.get('radio_profile_name') != LORA_RUNTIME_PROFILE,
            'lora_runtime_requires_explicit_artifact')
        return None
    validate_lora_artifact(value)
    source_files = manifest.get('source_files', manifest.get('files'))
    require('mcu_artifact' not in manifest
        and manifest.get('radio_profile_name') == LORA_RUNTIME_PROFILE
        and manifest.get('radio_profile_names') == list(LORA_PROFILE_NAMES)
        and manifest.get('native_operation_kind') == LORA_KIND
        and manifest.get('firmware') == value['firmware']
        and manifest.get('firmware_by_unit', {}).get('unit-a') == value['firmware']
        and type(source_files) is dict and source_files.get(
            'provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json') == value['source_lock_sha256'],
        'lora_mcu_artifact_runtime_mismatch')
    return value


def lora_profile_name(profile):
    from dataclasses import asdict
    from meshticulum.bench.serial_protocol import RadioProfile
    from .profiles import profile_for
    require(type(profile) is RadioProfile, 'lora_comparison_profile_required')
    profile.encode(board_id=1)
    matches = []
    for name in LORA_PROFILE_NAMES:
        original = profile_for(name)
        current = asdict(profile)
        # The original pair producer may tighten its lease to the remaining
        # application interval. No other profile or budget field may change.
        if type(profile.lease_ms) is int and 10000 <= profile.lease_ms <= original.lease_ms:
            current['lease_ms'] = original.lease_ms
            if canonical(current) == canonical(asdict(original)):
                matches.append(name)
    require(len(matches) == 1, 'lora_comparison_profile_not_reviewed')
    return matches[0]


def require_lora_runtime(manifest, kind, profile=None):
    selected = select_lora_runtime(manifest)
    require(selected is not None and kind == LORA_KIND,
        'lora_comparison_runtime_or_kind_required')
    if profile is not None:
        lora_profile_name(profile)
    return selected


def require_lora_selector_scope(kind):
    from .retained_capture import PAID_HISTORY
    pins = dict(LORA_NATIVE_BINDINGS)
    require(kind == LORA_KIND and all(type(pins[k]) is str and len(pins[k]) == 64
        and all(c in '0123456789abcdef' for c in pins[k])
        for k in ('scope_sha256', 'history_receipt_sha256'))
        and pins['preceding_parent'] == PAID_HISTORY[-1],
        'pending_lora_mcu_artifact_native_scope_and_history')
    return pins
