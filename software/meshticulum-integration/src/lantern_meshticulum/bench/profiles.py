# SPDX-License-Identifier: Apache-2.0
"""Explicit finite bench choices; sf5-500 is an opt-in, unqualified test profile."""
from meshticulum.bench.serial_protocol import RadioProfile

MODES = {'baseline': (8, 125000), 'sf7-250': (7, 250000), 'sf7-500': (7, 500000),
         'sf5-500': (5, 500000)}


def profile_for(name):
    sf, bandwidth = MODES[name]
    # SX1261/2 DS6.1.1.1: SF5/6 need twelve preamble symbols for reliable
    # synchronization. Keep the already qualified SF7+ profiles at eight.
    return RadioProfile(921375000, bandwidth, sf, 5, 0, 12 if sf <= 6 else 8,
                        0x42, 90000, 256, 60000000)

from meshticulum.bench.admission import RadioAdmission
RADIO_ADMISSION = RadioAdmission('MY', tuple((921375000, bw, sf, 5, 0, 12 if sf <= 6 else 8, 0x42)
    for sf, bw in MODES.values()), 10000, 180000, 256, 60000000)

# Decode this original paid trial's retired metadata without changing its
# profile, grant digest or attempt debt. This is never live radio admission.
LEGACY_SF5_8_PARENT = '57339cca60ddf62b41eafac60147a252'
LEGACY_SF5_8_ADMISSION = RadioAdmission('MY', ((921375000,500000,5,5,0,8,0x42),),
                                      10000,180000,256,60000000)
