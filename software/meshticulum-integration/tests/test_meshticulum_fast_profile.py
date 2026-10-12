# SPDX-License-Identifier: Apache-2.0
"""Opt-in faster SX1262 test profile; this does not authorize RF use."""
from dataclasses import asdict
import hashlib

from lantern_meshticulum.bench.profiles import MODES, RADIO_ADMISSION, profile_for
from lantern_meshticulum.bench.pilot_controller import pair_packages
from tests.test_pilot_controller import BOOTS, MANIFEST, PEERS, PERMISSION, REQUEST


def test_sf5_500_profile_is_explicit_and_default_stays_conservative():
    assert MODES['baseline'] == (8, 125000)
    profile = profile_for('sf5-500')
    assert (profile.frequency_hz, profile.bandwidth_hz, profile.spreading_factor,
            profile.coding_rate, profile.chip_power_dbm) == (921375000, 500000, 5, 5, 0)
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
