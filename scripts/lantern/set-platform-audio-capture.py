#!/usr/bin/env python3
"""Apply and verify the C–F platform codec capture path (ES8388 onboard mic).

Reviewed, idempotent mixer configuration only. The board boots with the capture
path on a differential line input at 0 dB gain, where the onboard microphone is
inaudible; this helper sets the exact control values the bench qualification
used and then reads every one of them back — a mismatch fails. It never writes
files, never touches playback controls and never changes anything outside
SETTINGS.

Usage:
  python3 set-platform-audio-capture.py --card rockchipes8388 [--wait-seconds 20]
  python3 set-platform-audio-capture.py --check          # read-back only

Measured on unit C 2026-09-29 with this path: speech peaks −14.5 dBFS, ambient
floor ≈ −41 dBFS, real speech transcribed by the board's whisper build.
"""

import argparse
import json
import re
import subprocess
import sys
import time


AMIXER = '/usr/bin/amixer'
CARD = 'rockchipes8388'
# Application order: input muxes, ADC routing, microphone bias, gains, and the
# unmute last so the path is only live once every stage is set.
SETTINGS = (
    ('Left PGA Mux', 'Line 2L'),
    ('Right PGA Mux', 'Line 2R'),
    ('ADC Data Select', 'Left Right'),
    ('Main Mic', 'on'),
    ('Headset Mic', 'off'),
    ('Capture Digital', '192'),
    ('Left Channel', '8'),
    ('Right Channel', '8'),
    ('Capture Mute', 'off'),
)
MAX_WAIT_SECONDS = 60
COMMAND_TIMEOUT = 10.0


class CapturePathError(Exception):
    pass


def amixer(run, arguments, *, timeout=COMMAND_TIMEOUT):
    result = run([AMIXER, *arguments], capture_output=True, text=True,
                 timeout=timeout, check=False)
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise CapturePathError('amixer_failed')
    return result.stdout


def parse_values(text):
    """Every channel/item value in one `amixer sget` result, in order."""
    values = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Only "Item0:"/… carry the current enum value; the "Items:" line above
        # lists every choice and must never be read as the observed value.
        if re.match(r'item[0-9]+:', line, re.IGNORECASE):
            _, _, value = line.partition(':')
            values.append(value.strip().strip("'"))
            continue
        percent = re.search(r'\[[0-9]+%\]', line)
        if percent:
            values.append(line[:percent.start()].split()[-1])
            continue
        switch = re.search(r'\[(on|off)\]', line)
        if switch:
            values.append(switch.group(1))
    if not values:
        raise CapturePathError('unparsed_control')
    return values


def read_control(run, card, control):
    return parse_values(amixer(run, ['-c', card, 'sget', control]))


def verify(run, card):
    observed = {}
    for control, expected in SETTINGS:
        values = read_control(run, card, control)
        if any(value != expected for value in values):
            raise CapturePathError('capture_path_mismatch:' + control)
        observed[control] = values[0] if len(values) == 1 else values
    return observed


def apply(run, card):
    for control, value in SETTINGS:
        amixer(run, ['-c', card, 'sset', control, value])
    return verify(run, card)


def wait_for_card(run, card, seconds, *, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + seconds
    while True:
        try:
            amixer(run, ['-c', card, 'info'])
            return
        except (CapturePathError, OSError, subprocess.SubprocessError):
            if clock() >= deadline:
                raise CapturePathError('sound_card_unavailable') from None
            sleep(0.5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--card', default=CARD)
    parser.add_argument('--check', action='store_true',
                        help='Read-back verification only; never sets a control.')
    parser.add_argument('--wait-seconds', type=int, default=15)
    args = parser.parse_args(argv)
    if not 0 <= args.wait_seconds <= MAX_WAIT_SECONDS:
        print('Invalid --wait-seconds.', file=sys.stderr)
        return 2
    action = 'refused'
    try:
        wait_for_card(subprocess.run, args.card, args.wait_seconds)
        if args.check:
            observed = verify(subprocess.run, args.card)
            action = 'verified'
        else:
            observed = apply(subprocess.run, args.card)
            action = 'applied'
    except CapturePathError as error:
        print(json.dumps(dict(card=args.card, action=action, reason=str(error)), sort_keys=True))
        return 1
    print(json.dumps(dict(card=args.card, action=action, capture_path=observed), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
