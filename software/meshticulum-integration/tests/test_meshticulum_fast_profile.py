# SPDX-License-Identifier: Apache-2.0
"""Opt-in faster SX1262 test profile; this does not authorize RF use."""
from lantern_meshticulum.bench.profiles import MODES, RADIO_ADMISSION, profile_for


def test_sf5_500_profile_is_explicit_and_default_stays_conservative():
    assert MODES['baseline'] == (8, 125000)
    profile = profile_for('sf5-500')
    assert (profile.frequency_hz, profile.bandwidth_hz, profile.spreading_factor,
            profile.coding_rate, profile.chip_power_dbm) == (921375000, 500000, 5, 5, 0)
    RADIO_ADMISSION.check('MY', True, profile)
