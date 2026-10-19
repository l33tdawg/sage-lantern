#!/usr/bin/env python3
"""Read-only inventory of a sound card's controls, for wiring an output path.

Why this exists rather than a finished mixer script: the capture sibling
(`set-platform-audio-capture.py`) can hard-code nine control names because they
were read off real hardware before it was written. Nothing equivalent exists for
the **output** side of the C–F platform codec. That script says so itself — it
"never touches playback controls" — and its settings are all input controls, so
the DAC, output mux, speaker and headphone gains, and their mutes are configured
by nothing and their names are unverified.

Guessing those names and shipping a script that sets them would be exactly the
mistake this repository keeps warning about. So this prints what the card
actually has, and the output path gets written from that reading.

Usage:
  python3 enumerate-platform-audio-controls.py --card rockchipes8388
  python3 enumerate-platform-audio-controls.py --card rockchipes8388 --json

Read-only: it runs `amixer scontrols` and `amixer sget <control>`. It never sets
a control, never writes a file and never changes device state, so it is safe to
run on the unit as the service account.
"""

import argparse
import json
import re
import subprocess
import sys

AMIXER = '/usr/bin/amixer'
CARD = 'rockchipes8388'
COMMAND_TIMEOUT = 10.0
# Control names that mean output rather than input. Kept as a filter, not as a
# list of controls to set — the point is to show the operator which rows matter,
# not to decide for them.
OUTPUT_HINTS = ('dac', 'output', 'speaker', 'spk', 'hp', 'headphone', 'lout', 'rout',
                'playback', 'pcm', 'mixer', 'stereo', 'mono', 'bypass')
INPUT_HINTS = ('adc', 'pga', 'mux', 'mic', 'capture', 'input', 'line', 'diff')


class EnumerateError(Exception):
    pass


def amixer(run, arguments, *, timeout=COMMAND_TIMEOUT):
    result = run([AMIXER, *arguments], capture_output=True, text=True,
                 timeout=timeout, check=False)
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise EnumerateError('amixer_failed')
    return result.stdout


def control_names(run, card):
    """Every simple control name the card exposes, in amixer's own order."""
    names = []
    for line in amixer(run, ['-c', card, 'scontrols']).splitlines():
        match = re.match(r"simple control '(.+)'", line.strip())
        if match:
            names.append(match.group(1))
    if not names:
        raise EnumerateError('no_controls_reported')
    return names


def read_control(run, card, control):
    """The current value(s) of one control, as amixer prints them."""
    text = amixer(run, ['-c', card, 'sget', control])
    values = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # "Item0:"/… carry the current enum value; the "Items:" line above them
        # lists every choice and must never be read as the observed value.
        if re.match(r'item[0-9]+:', line, re.IGNORECASE):
            values.append(line.partition(':')[2].strip().strip("'"))
            continue
        percent = re.search(r'\[[0-9]+%\]', line)
        if percent:
            values.append(line[:percent.start()].split()[-1])
            continue
        switch = re.search(r'\[(on|off)\]', line)
        if switch:
            values.append(switch.group(1))
    return values


def classify(name):
    lowered = name.lower()
    # Output wins a tie: several controls carry words from both lists, and the
    # operator wants to see a candidate twice rather than miss one.
    if any(hint in lowered for hint in OUTPUT_HINTS):
        return 'output'
    if any(hint in lowered for hint in INPUT_HINTS):
        return 'input'
    return 'unknown'


def inventory(run, card):
    rows = []
    for name in control_names(run, card):
        try:
            values = read_control(run, card, name)
        except (EnumerateError, OSError, subprocess.SubprocessError):
            # A control that will not read is a fact worth reporting, not a
            # reason to abandon the inventory half way through.
            values = None
        rows.append({'control': name, 'kind': classify(name),
                     'values': values, 'state': 'read' if values else 'unreadable'})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--card', default=CARD)
    parser.add_argument('--json', action='store_true', help='machine-readable output')
    parser.add_argument('--only', choices=['output', 'input', 'unknown', 'all'], default='all')
    args = parser.parse_args(argv)
    try:
        rows = inventory(subprocess.run, args.card)
    except (EnumerateError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'card': args.card, 'action': 'refused', 'reason': str(error)},
                         sort_keys=True))
        return 1
    selected = rows if args.only == 'all' else [row for row in rows if row['kind'] == args.only]
    if args.json:
        print(json.dumps({'card': args.card, 'action': 'read', 'controls': selected,
                          'total': len(rows)}, sort_keys=True))
        return 0
    counts = {kind: sum(1 for row in rows if row['kind'] == kind)
              for kind in ('output', 'input', 'unknown')}
    print(f"card {args.card}: {len(rows)} controls "
          f"({counts['output']} output, {counts['input']} input, {counts['unknown']} unknown)")
    for row in selected:
        values = 'UNREADABLE' if row['values'] is None else ', '.join(row['values']) or '(no value)'
        print(f"  [{row['kind']:7}] {row['control']} = {values}")
    if counts['output'] == 0:
        print("\nNo output controls were found. Do not write an output-path script for this "
              "card until that is explained — an output path cannot be configured blind.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
