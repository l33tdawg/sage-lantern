# SPDX-License-Identifier: Apache-2.0
"""Explicit finite bench choices; sf5-500 is an opt-in, unqualified test profile."""
from meshticulum.bench.serial_protocol import RadioProfile

MODES = {'baseline': (8, 125000), 'sf7-250': (7, 250000), 'sf7-500': (7, 500000),
         'sf5-500': (5, 500000)}


def profile_for(name):
    if name == GFSK_NAME:
        return GfskProfile(921375000,0,90000,256,60000000)
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

# A separate opt-in modem. The old LoRa waveform allowlist stays unchanged.
from dataclasses import asdict
from meshticulum.bench.gfsk_profile import GfskProfile
from meshticulum.bench.admission import GfskAdmission
from meshticulum.bench.wire import require
GFSK_NAME='gfsk125'
GFSK_KIND='c-gfsk125-8k-batch-v1'
GFSK_BUNDLE_KIND='c-gfsk125-bundle100k-v1'
GFSK_MB1_BUNDLE_KIND='c-gfsk125-mb1-bundle100k-v1'
PROFILE_NAMES=frozenset(MODES)|{GFSK_NAME}
GFSK_ADMISSION=GfskAdmission('MY',(921375000,),(0,),10000,180000,256,60000000)

# Actual compiled firmware/source and expired H35 evidence are bound below.
# A fresh native scope still requires its own current capture and owner approval.
GFSK_FIRMWARE='mtb1-b433927ba6'
GFSK_CORE_COMMIT='aec97045d945535deb95075a303a59bb975d21bd'
GFSK_HISTORY_RECEIPT_SHA256='37d961c7e5078fb98a5c4e3e4b31b7aac51d0afa1433966a56b47c00d2de36ca'
GFSK_PRECEDING_PARENT='73c1cda8c02939409d0b1e7e2a39be67'


def require_gfsk_bindings():
    from .retained_capture import PAID_HISTORY
    require(type(GFSK_FIRMWARE) is str and GFSK_FIRMWARE.startswith('mtb1-')
        and len(GFSK_FIRMWARE)==15 and all(c in '0123456789abcdef' for c in GFSK_FIRMWARE[5:])
        and type(GFSK_CORE_COMMIT) is str and len(GFSK_CORE_COMMIT)==40
        and all(c in '0123456789abcdef' for c in GFSK_CORE_COMMIT)
        and type(GFSK_HISTORY_RECEIPT_SHA256) is str and len(GFSK_HISTORY_RECEIPT_SHA256)==64
        and all(c in '0123456789abcdef' for c in GFSK_HISTORY_RECEIPT_SHA256)
        and len(PAID_HISTORY)==35 and PAID_HISTORY[-1]==GFSK_PRECEDING_PARENT,
        'pending_gfsk_firmware_source_and_history_bindings')
    return True


def profile_record(profile):
    if type(profile) is GfskProfile:return profile.to_dict()
    require(type(profile) is RadioProfile,'unsupported_native_radio_profile')
    return asdict(profile)


def profile_from_plan(plan,*,board_id=1):
    if plan.get('radio_modem')==GFSK_NAME:
        return GfskProfile.from_dict(plan['profile'],board_id=board_id)
    require('radio_modem' not in plan,'unreviewed_native_radio_modem')
    profile=RadioProfile(**plan['profile']);profile.encode(board_id=board_id)
    return profile


def admission_for(profile):
    if type(profile) is GfskProfile:return GFSK_ADMISSION
    require(type(profile) is RadioProfile,'unsupported_native_radio_profile')
    return RADIO_ADMISSION


def radio_route(plan):
    route={k:plan[k] for k in ('initiator','responder','session_id','generation','profile','object','sender_unit','country','service')}
    if plan.get('radio_modem')==GFSK_NAME:route['radio_modem']=GFSK_NAME
    else:require('radio_modem' not in plan,'unreviewed_native_radio_modem')
    return route
