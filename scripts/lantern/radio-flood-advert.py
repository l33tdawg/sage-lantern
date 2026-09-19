"""Read the companion's counters and, with an explicit consent, send ONE flood
advertisement from it.

This is the forwarding measurement instrument for a two-node fleet: with the
peer's repeat bit set, a flood advert sent here is re-transmitted by that peer,
which shows up as sent_flood / packets_sent / tx_airtime on the relay's own
stats read (through the peer's runtime) and as a re-heard flood on this radio.

Runs on the appliance as root with the mesh runtime stopped, so nothing else
owns the serial device. Without --send it is read-only and prints the three
firmware stats frames plus the identity snapshot. With --send it transmits one
self-advertisement as a flood packet (the same packet the runtime sends every
~60 s, but flooded instead of zero-hop) between two stats reads.

    python3 radio-flood-advert.py --serial <usb-serial> --stats
    python3 radio-flood-advert.py --serial <usb-serial> --send \
        --operator-reviewed-open-close-and-stopped-producers \
        --consent FLOOD-ADVERT-<usb-serial> --apply

It claims nothing about delivery: the evidence is the counter delta on the
relay, read through the relay's own runtime.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import sys
import time


SPEC = importlib.util.spec_from_file_location(
    'set_radio_repeat', Path(__file__).with_name('set-radio-repeat.py'))
REPEAT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPEAT)
require = REPEAT.require
QUERY, APP_START = REPEAT.QUERY, REPEAT.APP_START
ADVERT = b'\x07\x01'
SUBTYPE = dict(core=0, radio=1, packets=2)
STATS = {kind: bytes((56, subtype)) for kind, subtype in SUBTYPE.items()}
LAYOUTS = dict(core=('<HIHB', ('battery_mv', 'uptime_seconds', 'error_flags', 'tx_queue')),
               radio=('<hbbII', ('noise_floor_dbm', 'last_rssi_dbm', 'last_snr_quarter_db',
                                 'tx_airtime_seconds', 'rx_airtime_seconds')),
               packets=('<IIIIIII', ('packets_received', 'packets_sent', 'sent_flood', 'sent_direct',
                                     'received_flood', 'received_direct', 'receive_errors')))
JOURNAL_ROOT = Path('/var/lib/sage-lantern-radio-flood')


def parse_stats(kind, response):
    layout, names = LAYOUTS[kind]
    require(type(response) is bytes and len(response) == 2 + struct.calcsize(layout), 'stats frame length')
    require(response[:2] == bytes((24, SUBTYPE[kind])), 'stats frame type')
    return dict(zip(names, struct.unpack(layout, response[2:])))


def counters(exchange):
    return {kind: parse_stats(kind, exchange(command)) for kind, command in STATS.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--serial', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--stats', action='store_true')
    mode.add_argument('--send', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--operator-reviewed-open-close-and-stopped-producers', action='store_true')
    parser.add_argument('--consent')
    parser.add_argument('--expect-public-key', help='64 hex characters; the key the runtime is pinned to')
    parser.add_argument('--drain', action='store_true', help='discard data an abandoned attempt left buffered')
    parser.add_argument('--settle-seconds', type=float, default=4.0)
    parser.add_argument('--journal-root', default=str(JOURNAL_ROOT))
    parser.add_argument('--stamp', default=time.strftime('%Y%m%d-%H%M%S', time.gmtime()))
    args = parser.parse_args(argv)
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root required')
    require(not args.send or args.apply, 'send requires --apply')
    require(args.apply or args.consent is None, 'unexpected consent')
    if args.apply:
        require(args.operator_reviewed_open_close_and_stopped_producers, 'operator review required')
        require(args.consent == f'FLOOD-ADVERT-{args.serial}', 'apply consent required')
        require(0 <= args.settle_seconds <= 30, 'settle interval invalid')
    expected_key = None
    if args.expect_public_key is not None:
        require(re.fullmatch(r'[0-9a-f]{64}', args.expect_public_key) is not None, 'expected key malformed')
        expected_key = args.expect_public_key
    allowed = {QUERY, APP_START, *STATS.values(), ADVERT}

    def read(exchange):
        return dict(identity=REPEAT.snapshot(exchange, expected_key), counters=counters(exchange))

    if args.apply:
        with REPEAT.journal(args.stamp, args.serial, root=Path(args.journal_root)) as append:
            with REPEAT.connection(args.serial, allowed, drain_pending=args.drain) as exchange:
                before = read(exchange)
                append(dict(event='prepared', before=before, outcome='not_started',
                            schema='sage.lantern.radio-flood-advert.v1'))
                append(dict(event='advert_intent', opcode=ADVERT[0], outcome='unknown_until_verified'))
                require(exchange(ADVERT) == b'\x00', 'advert result unverified')
                time.sleep(args.settle_seconds)
                after = read(exchange)
                append(dict(event='advert_sent', observed=after))
                append(dict(event='drained', bytes=exchange.drained_bytes))
            append(dict(event='complete', observed=after, outcome='one_flood_advert_sent'))
        result = dict(transmitted=True, before=before, after=after)
    else:
        with REPEAT.connection(args.serial, allowed, drain_pending=args.drain) as exchange:
            result = read(exchange)
    print(json.dumps(dict(schema='sage.lantern.radio-flood-review.v1', serial=args.serial,
                          mode='send' if args.apply else 'stats', result=result,
                          rf_delivery_verified=False), sort_keys=True))


def entrypoint():
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(success=False, reason=REPEAT.INVENTORY.refusal_reason(error),
                              instruction='preserve_journal_no_automatic_retry')), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(entrypoint())
