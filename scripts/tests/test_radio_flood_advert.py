import importlib.util
import json
from pathlib import Path
import struct

import pytest


SPEC = importlib.util.spec_from_file_location('radio_flood_advert', Path(__file__).parents[1] / 'lantern/radio-flood-advert.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REPEAT = MODULE.REPEAT
KEY = bytes(range(32))


def stats(kind, **values):
    layout, names = MODULE.LAYOUTS[kind]
    packed = [values.get(name, 0) for name in names]
    return bytes((24, MODULE.SUBTYPE[kind])) + struct.pack(layout, *packed)


class Transport:
    def __init__(self, adverts_ok=True):
        self.allowed = set()
        self.poisoned = False
        self.calls = []
        self.packets = dict(packets_received=10, packets_sent=4, sent_flood=1, sent_direct=3,
                            received_flood=2, received_direct=5, receive_errors=0)
        self.adverts_ok = adverts_ok

    def __call__(self, command):
        assert command in self.allowed, command
        self.calls.append(command)
        if command == MODULE.QUERY:
            return info()
        if command == MODULE.APP_START:
            return self_info()
        if command == MODULE.ADVERT:
            if self.adverts_ok:
                self.packets['sent_flood'] += 1
                self.packets['packets_sent'] += 1
            return b'\x00'
        for kind, value in MODULE.STATS.items():
            if command == value:
                return stats(kind, **self.packets) if kind == 'packets' else stats(kind)
        raise AssertionError(command)


def info():
    response = bytearray(82)
    response[:2] = bytes((13, 13))
    response[60:60 + len('v1.17.1-d929643-r5')] = b'v1.17.1-d929643-r5'
    return bytes(response)


def self_info():
    response = bytearray(58)
    response[:4] = bytes((5, 1, 10, 22))
    response[4:36] = KEY
    response[48:58] = struct.pack('<IIBB', 917375, 62500, 8, 5)
    return bytes(response) + b'LANTERN-A'


def test_parse_stats_round_trip_and_rejections():
    frame = stats('packets', packets_received=1, packets_sent=2, sent_flood=3, sent_direct=4,
                  received_flood=5, received_direct=6, receive_errors=7)
    parsed = MODULE.parse_stats('packets', frame)
    assert parsed['sent_flood'] == 3 and parsed['receive_errors'] == 7
    for bad in (frame[:-1], frame + b'\x00', bytes((25, 2)) + frame[2:], bytes((24, 1)) + frame[2:]):
        with pytest.raises(REPEAT.INVENTORY.InventoryRefused):
            MODULE.parse_stats('packets', bad)


def test_send_reads_before_and_after_and_counts_the_advert():
    exchange = Transport()
    exchange.allowed = {REPEAT.QUERY, REPEAT.APP_START, *MODULE.STATS.values(), MODULE.ADVERT}
    before = dict(identity=REPEAT.snapshot(exchange, KEY.hex()), counters=MODULE.counters(exchange))
    assert before['counters']['packets']['sent_flood'] == 1
    assert exchange(MODULE.ADVERT) == b'\x00'
    after = MODULE.counters(exchange)
    assert after['packets']['sent_flood'] == 2 and after['packets']['packets_sent'] == 5
    assert exchange.calls[-1] == MODULE.STATS['packets']


def test_stats_mode_never_transmits():
    exchange = Transport()
    exchange.allowed = {REPEAT.QUERY, REPEAT.APP_START, *MODULE.STATS.values()}
    MODULE.counters(exchange)
    assert MODULE.ADVERT not in exchange.calls
