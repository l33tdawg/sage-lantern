# SPDX-License-Identifier: Apache-2.0
"""Opt-in faster SX1262 test profile; this does not authorize RF use."""
from dataclasses import asdict, replace
import hashlib

from lantern_meshticulum.bench.profiles import MODES, RADIO_ADMISSION, profile_for, LEGACY_SF5_8_PARENT
from lantern_meshticulum.bench.pilot_parent_caps import child_caps, ParentCapsError
from lantern_meshticulum.bench.pilot_controller import pair_packages
from tests.test_pilot_controller import BOOTS, MANIFEST, PEERS, PERMISSION, REQUEST, Clock, ModelOperator, model_parent
from lantern_meshticulum.bench.pilot_controller import Controller
from lantern_meshticulum.bench.pilot_resume_rpc import owner_radio_profile
from lantern_meshticulum.bench import node
from meshticulum.bench.wire import WireError
from types import SimpleNamespace
import pytest


def test_sf5_500_profile_is_explicit_and_default_stays_conservative():
    assert MODES['baseline'] == (8, 125000)
    profile = profile_for('sf5-500')
    assert (profile.frequency_hz, profile.bandwidth_hz, profile.spreading_factor,
            profile.coding_rate, profile.chip_power_dbm) == (921375000, 500000, 5, 5, 0)
    assert profile.preamble == 12
    assert profile_for('sf7-500').preamble == 8
    RADIO_ADMISSION.check('MY', True, profile)


def test_sf5_profile_is_available_in_the_explicit_pilot_package():
    now = 1_000_000_000
    pair = pair_packages(campaign_id='d' * 32, resource_sha256=MANIFEST,
        application_expires_epoch_us=now + 160_000_000,
        sender='unit-c', receiver='unit-b', transport='lora', format='MR1.request',
        descriptor=dict(bytes=len(REQUEST), sha256=hashlib.sha256(REQUEST).hexdigest()),
        source=None, request=REQUEST, peers=PEERS, boots=BOOTS, now_epoch_us=now,
        radio_permission=PERMISSION, profile='sf5-500')
    assert all(package['grant']['profile'] == asdict(profile_for('sf5-500'))
        for package in pair['packages'].values())


def fast_controller(*, profile='sf5-500'):
    clock = Clock()
    class Operator(ModelOperator):
        def status(self, unit, run):
            return dict(super().status(unit, run), radio_receive_checked=True)
    operator = Operator(clock)
    control = Controller(operator, campaign_id='d' * 32, resource_sha256=MANIFEST,
        expires_epoch_us=1160000000, handoff_deadline_monotonic=200,
        peers=PEERS, boots=BOOTS, parent=model_parent(clock), radio_permission=PERMISSION,
        clock=clock.mono, epoch=clock.epoch, sleep=clock.sleep,
        radio_profile=profile, lora_sender_first=True)
    return control, operator


def test_actual_controller_passes_sf5_profile_into_paid_native_children():
    control, operator = fast_controller()
    control.link('unit-a', 'unit-c', 'lora', 'MR1.request', REQUEST)
    assert [event[1] for event in operator.events if event[0] == 'start'] == ['unit-a', 'unit-c']
    assert control.links[0]['phase'] == 'complete'
    for package in operator.packages.values():
        assert package['grant']['profile'] == asdict(profile_for('sf5-500'))
        assert package['parent']['child']['metadata']['profile'] == package['grant']['profile']


def test_profile_mutation_refuses_before_native_prepare():
    control, operator = fast_controller()
    control.radio_profile = 'sf7-500'
    with pytest.raises(WireError, match='profile_changed'):
        control.link('unit-a', 'unit-c', 'lora', 'MR1.request', REQUEST)
    assert operator.events == []


def test_native_owner_accepts_only_explicit_sf5_c_a_8k_scope():
    owner = dict(radio_profile_name='sf5-500', qualification_kind='c-bulk-8k-v1',
                 rf_profile=asdict(profile_for('sf5-500')))
    assert owner_radio_profile(owner, SimpleNamespace(qualification_kind=None)) == profile_for('sf5-500')
    for kind in (None, 'c-bulk32-v1', 'c-bundle100k-v3', 'mixed-only-v1'):
        with pytest.raises(RuntimeError):
            owner_radio_profile(dict(owner, qualification_kind=kind), SimpleNamespace(qualification_kind=None))
    with pytest.raises(RuntimeError):
        owner_radio_profile(owner, SimpleNamespace(qualification_kind='c-bulk32-v1'))
    with pytest.raises(RuntimeError):
        owner_radio_profile(dict(owner, rf_profile=asdict(profile_for('sf7-500'))), SimpleNamespace(qualification_kind=None))


def test_legacy_owner_without_profile_name_keeps_exact_sf7():
    assert owner_radio_profile(dict(rf_profile=asdict(profile_for('sf7-500'))),
        SimpleNamespace(qualification_kind=None)) == profile_for('sf7-500')


def test_strict_firmware_identity_matches_only_proposed_a_and_baseline_b():
    assert node.FIRMWARE_BY_UNIT == {'unit-a': 'mtb1-e61c2c8979', 'unit-b': 'mtb1-9816f92488'}
    assert node._fast_profile(profile_for('sf5-500'))
    assert not node._fast_profile(profile_for('sf7-500'))


def test_short_sf5_preamble_has_no_live_admission_or_sender_readiness():
    short = replace(profile_for('sf5-500'), preamble=8)
    with pytest.raises(WireError):
        RADIO_ADMISSION.check('MY', True, short)
    assert not node._fast_profile(short)


def test_old_paid_sf5_metadata_keeps_its_original_profile_and_digest():
    clock = Clock()
    parent = model_parent(clock)
    old_policy = replace(parent.policy, parent_id=LEGACY_SF5_8_PARENT)
    pair = pair_packages(campaign_id='d' * 32, resource_sha256=MANIFEST,
        application_expires_epoch_us=1160000000, sender='unit-a', receiver='unit-c',
        transport='lora', format='MR1.request',
        descriptor=dict(bytes=len(REQUEST),sha256=hashlib.sha256(REQUEST).hexdigest()),
        source=None, request=REQUEST, peers=PEERS, boots=BOOTS,
        now_epoch_us=clock.epoch(), radio_permission=PERMISSION,profile='sf5-500')
    package = pair['packages']['unit-a']
    package['grant']['profile']['preamble'] = 8
    old = child_caps(package,old_policy)
    assert old.to_dict()['metadata']['profile']['preamble'] == 8
    with pytest.raises(ParentCapsError):
        child_caps(package,parent.policy)
