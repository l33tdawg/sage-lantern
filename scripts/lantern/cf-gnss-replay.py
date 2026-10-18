#!/usr/bin/env python3
"""Offline L76K NMEA validator. Reads JSONL stdin, never a serial device or service."""
import argparse
import datetime as dt
import json
import math
import re
import sys

MAX_LINE = 4096
MAX_SENTENCE = 160
TALKERS = {'GP', 'GN', 'GL', 'BD'}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def utc(value):
    require(type(value) is str and value.endswith('Z'), 'utc_required')
    parsed = dt.datetime.fromisoformat(value[:-1] + '+00:00')
    require(parsed.tzinfo == dt.timezone.utc, 'utc_required')
    return parsed


def coordinate(raw, direction, latitude):
    digits, limit, positive, negative = (2, 90, 'N', 'S') if latitude else (3, 180, 'E', 'W')
    require(re.fullmatch(r'\d{' + str(digits + 2) + r'}(?:\.\d{1,7})?', raw), 'coordinate_format')
    require(direction in (positive, negative), 'coordinate_direction')
    degrees, minutes = int(raw[:digits]), float(raw[digits:])
    require(0 <= degrees <= limit and 0 <= minutes < 60 and (degrees < limit or minutes == 0), 'coordinate_range')
    return (degrees + minutes / 60) * (1 if direction == positive else -1)


def timestamp(time, date):
    require(re.fullmatch(r'\d{6}(?:\.\d{1,6})?', time) and re.fullmatch(r'\d{6}', date), 'timestamp_format')
    whole, _, fraction = time.partition('.')
    # This candidate deliberately supports 2000–2099, not an inferred century.
    return dt.datetime(2000 + int(date[4:]), int(date[2:4]), int(date[:2]),
        int(whole[:2]), int(whole[2:4]), int(whole[4:]), int(fraction.ljust(6, '0') or '0'), tzinfo=dt.timezone.utc)


def parse(sentence):
    require(type(sentence) is str and len(sentence) <= MAX_SENTENCE, 'sentence_length')
    raw = sentence.removesuffix('\r\n').removesuffix('\n')
    require(re.fullmatch(r'\$[\x20-\x7e]+\*[0-9A-Fa-f]{2}', raw), 'sentence_framing')
    body, check = raw[1:].rsplit('*', 1)
    require('*' not in body and '$' not in body, 'sentence_framing')
    checksum = 0
    for byte in body.encode('ascii'):
        checksum ^= byte
    require(checksum == int(check, 16), 'checksum')
    fields = body.split(',')
    require(len(fields[0]) == 5 and fields[0][:2] in TALKERS, 'unsupported_talker')
    kind = fields[0][2:]
    if kind == 'GGA':
        require(len(fields) == 15 and fields[6] in ('0', '1', '2', '6'), 'gga_shape_or_quality')
        return {'kind': kind, 'valid': fields[6] in ('1', '2')}
    if kind != 'RMC':
        return {'kind': 'ignored'}
    require(len(fields) in (13, 14), 'rmc_shape')
    require(fields[2] in ('A', 'D', 'V') and fields[12] in ('A', 'D', 'E', 'N'), 'rmc_status_or_mode')
    nav = fields[13] if len(fields) == 14 else ''
    require(nav in ('', 'S', 'C', 'U', 'V'), 'rmc_navigation_status')
    if fields[2] == 'V' or fields[12] in ('E', 'N'):
        return {'kind': kind, 'valid': False}
    return {'kind': kind, 'valid': True, 'timestamp': timestamp(fields[1], fields[9]),
            'latitude': coordinate(fields[3], fields[4], True),
            'longitude': coordinate(fields[5], fields[6], False), 'mode': fields[12],
            'navigation_status': nav or 'unavailable'}


class Tracker:
    """One receiver session. Validity uses receipt age AND a separately trusted UTC."""
    def __init__(self, max_age=3.0, max_utc_skew=3.0):
        require(type(max_age) in (int, float) and math.isfinite(max_age) and 0 < max_age <= 30, 'age_bound')
        require(type(max_utc_skew) in (int, float) and math.isfinite(max_utc_skew) and 0 < max_utc_skew <= 30, 'skew_bound')
        self.max_age, self.max_utc_skew = max_age, max_utc_skew
        self.fix = None
        self.last_receipt = None
        self.last_fix_time = None
        self.reason = 'no_data'

    def feed(self, sentence, received_monotonic, received_utc=None, clock_trusted=False):
        require(type(received_monotonic) in (int, float) and math.isfinite(received_monotonic)
                and received_monotonic >= 0 and type(clock_trusted) is bool, 'receipt_invalid')
        if not self.advance(received_monotonic):
            return self.snapshot(received_monotonic)
        try:
            value = parse(sentence)
            if value['kind'] == 'ignored':
                return self.snapshot(received_monotonic)
            if not value['valid']:
                self.fix, self.reason = None, 'no_fix'
                return self.snapshot(received_monotonic)
            if value['kind'] == 'GGA':
                # GGA has no date; it cannot create or refresh a current position.
                return self.snapshot(received_monotonic)
            require(self.last_fix_time is None or value['timestamp'] > self.last_fix_time, 'fix_time_not_advancing')
            require(clock_trusted and received_utc is not None, 'utc_unverified')
            observed_utc = utc(received_utc)
            require(abs((observed_utc - value['timestamp']).total_seconds()) <= self.max_utc_skew, 'utc_skew')
            self.last_fix_time = value['timestamp']
            self.fix = {**value, 'received_monotonic': received_monotonic}
            self.reason = 'valid_sample'
        except (ValueError, TypeError, OverflowError):
            # Do not emit receiver bytes or retain a previous coordinate on bad data.
            self.fix, self.reason = None, 'invalid_or_untrusted_sample'
        return self.snapshot(received_monotonic)

    def advance(self, now):
        if self.last_receipt is not None and now < self.last_receipt:
            self.fix, self.reason = None, 'receipt_clock_reversed'
            return False
        self.last_receipt = now
        return True

    def snapshot(self, now):
        require(type(now) in (int, float) and math.isfinite(now) and now >= 0, 'clock_invalid')
        self.advance(now)
        status, position = self.reason, None
        if self.fix is not None:
            age = now - self.fix['received_monotonic']
            if age < 0:
                status = 'receipt_clock_reversed'
            elif age > self.max_age:
                status = 'stale'
                self.fix, self.reason = None, status
            else:
                status = 'valid_sample'
                position = {key: self.fix[key] for key in ('latitude', 'longitude', 'mode', 'navigation_status')}
                position.update(receiver_utc=self.fix['timestamp'].isoformat().replace('+00:00', 'Z'), age_seconds=age)
        return dict(schema='sage.lantern.gnss-replay.v1', source='offline-replay', status=status,
                    position=position, current_position_verified=False, hardware_accepted=False,
                    navigation_safety_verified=False)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_field')
        result[key] = value
    return result


def replay(stream, output):
    tracker = Tracker()
    for index in range(10001):
        raw = stream.readline(MAX_LINE + 1)
        if not raw:
            return
        require(index < 10000 and len(raw.encode('utf-8')) <= MAX_LINE, 'input_bound')
        row = json.loads(raw, object_pairs_hook=unique)
        require(type(row) is dict and set(row) <= {'sentence', 'received_monotonic', 'received_utc', 'clock_trusted', 'now'}, 'event_fields')
        if set(row) == {'now'}:
            result = tracker.snapshot(row['now'])
        else:
            require({'sentence', 'received_monotonic'} <= set(row) and 'now' not in row, 'event_shape')
            result = tracker.feed(**row)
        output.write(json.dumps(result, sort_keys=True, allow_nan=False) + '\n')


def main():
    argparse.ArgumentParser(description=__doc__, allow_abbrev=False).parse_args()
    try:
        replay(sys.stdin, sys.stdout)
        return 0
    except (ValueError, TypeError, OverflowError):
        print('GNSS replay refused malformed input; no input values disclosed.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
