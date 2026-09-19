"""Bounded RAM projection of confirmed firmware dequeues, not RF discovery."""

import hashlib
import math
import re
import threading
import time

from .mesh_voice_ipc import require
from .meshcore_transport import FIRMWARE, SUPPORTED_FIRMWARE


WINDOW_MS = 60000
MAX_SENDERS = 32


class RadioEvents:
    def __init__(self, *, clock=time.monotonic, wall=time.time):
        self._clock, self._wall = clock, wall
        self._mutex = threading.Lock()
        self._epoch = None
        self._entries = {}

    def clear(self):
        with self._mutex:
            self._epoch = None
            self._entries.clear()

    def _current(self, epoch):
        require(type(epoch) is str and re.fullmatch('[0-9a-f]{32}', epoch) is not None)
        if self._epoch != epoch:
            self._epoch = epoch
            self._entries.clear()
        now, wall = self._clock(), self._wall()
        require(type(now) in (int, float) and math.isfinite(now) and now >= 0
            and type(wall) in (int, float) and math.isfinite(wall) and wall > 0)
        if any(now < item[0] for item in self._entries.values()):
            self._entries.clear()
        self._entries = {key: item for key, item in self._entries.items() if (now - item[0]) * 1000 < WINDOW_MS}
        return now, int(wall * 1000)

    def observe(self, event, epoch):
        with self._mutex:
            if epoch is None:
                self._epoch = None
                self._entries.clear()
                return
            now, wall = self._current(epoch)
            require(type(event) is dict and set(event) == {'kind', 'sender_prefix', 'snr_quarter_db'})
            require(event['kind'] in ('contact', 'channel', 'channel_binary'))
            snr = event['snr_quarter_db']
            require(snr is None or type(snr) is int and -128 <= snr <= 127)
            if event['kind'] != 'contact':
                require(event['sender_prefix'] is None)
                return
            prefix = event['sender_prefix']
            require(type(prefix) is bytes and len(prefix) == 6)
            identifier = hashlib.sha256(epoch.encode() + prefix).hexdigest()
            self._entries.pop(identifier, None)
            if len(self._entries) >= MAX_SENDERS:
                del self._entries[next(iter(self._entries))]
            self._entries[identifier] = (now, wall, snr)

    def snapshot(self, epoch, *, identity_verified, receiving, repeat=None):
        with self._mutex:
            now, wall = self._current(epoch)
            require(type(identity_verified) is bool and type(receiving) is bool)
            require(repeat is None or type(repeat) is bool)
            if identity_verified and repeat is None:
                repeat = False
            if not identity_verified:
                repeat = None
            if not identity_verified or not receiving:
                self._entries.clear()
            entries = [dict(id=key, dequeued_at_unix_ms=item[1], age_ms=int((now-item[0])*1000),
                snr_db=None if item[2] is None else item[2] / 4, rssi_dbm=None,
                rf_received_at_unix_ms=None, application_authenticated=False, direct_neighbor_verified=False)
                for key, item in reversed(self._entries.items())]
            return dict(schema='sage.lantern.radio-events.v1', observed_at_unix_ms=wall,
                valid_for_ms=2000, window_ms=WINDOW_MS, serial_identity_verified=identity_verified,
                firmware=FIRMWARE if identity_verified else None, receiving=receiving,
                repeater=None if repeat is None else dict(enabled=repeat, source='firmware_info'),
                profile=None, saved_contacts=dict(available=False, count=None),
                neighbors=dict(available=False, reason='no_rf_neighbor_discovery'),
                last_heard=dict(source='firmware_dequeued_contact_messages', entries=entries))


def validate_status(value):
    if type(value) is dict and 'health' in value:
        health = value['health']
        validate_status({key: item for key, item in value.items() if key != 'health'})
        validate_health(health)
        return value
    require(type(value) is dict and set(value) == {'schema', 'observed_at_unix_ms', 'valid_for_ms',
        'window_ms', 'serial_identity_verified', 'firmware', 'receiving', 'repeater', 'profile',
        'saved_contacts', 'neighbors', 'last_heard'})
    require(value['schema'] == 'sage.lantern.radio-events.v1' and value['valid_for_ms'] == 2000
        and value['window_ms'] == WINDOW_MS and type(value['observed_at_unix_ms']) is int and value['observed_at_unix_ms'] > 0)
    require(type(value['serial_identity_verified']) is bool and type(value['receiving']) is bool
        and (value['firmware'] in SUPPORTED_FIRMWARE if value['serial_identity_verified'] else value['firmware'] is None) and value['profile'] is None)
    if value['serial_identity_verified']:
        repeater = value['repeater']
        require(type(repeater) is dict and set(repeater) == {'enabled', 'source'}
                and type(repeater['enabled']) is bool and repeater['source'] == 'firmware_info')
    else:
        require(value['repeater'] is None)
    require(not value['receiving'] or value['serial_identity_verified'])
    require(value['saved_contacts'] == dict(available=False, count=None)
        and value['neighbors'] == dict(available=False, reason='no_rf_neighbor_discovery'))
    require(value['saved_contacts']['available'] is False and value['neighbors']['available'] is False)
    heard = value['last_heard']
    require(type(heard) is dict and set(heard) == {'source', 'entries'}
        and heard['source'] == 'firmware_dequeued_contact_messages' and type(heard['entries']) is list and len(heard['entries']) <= MAX_SENDERS)
    seen = set()
    for entry in heard['entries']:
        require(type(entry) is dict and set(entry) == {'id', 'dequeued_at_unix_ms', 'age_ms', 'snr_db',
            'rssi_dbm', 'rf_received_at_unix_ms', 'application_authenticated', 'direct_neighbor_verified'})
        require(type(entry['id']) is str and re.fullmatch('[0-9a-f]{64}', entry['id']) is not None and entry['id'] not in seen)
        seen.add(entry['id'])
        require(type(entry['dequeued_at_unix_ms']) is int and entry['dequeued_at_unix_ms'] > 0
            and type(entry['age_ms']) is int and 0 <= entry['age_ms'] < WINDOW_MS)
        require(entry['snr_db'] is None or type(entry['snr_db']) in (int, float)
            and math.isfinite(entry['snr_db']) and -32 <= entry['snr_db'] <= 31.75 and entry['snr_db'] * 4 % 1 == 0)
        require(entry['rssi_dbm'] is None and entry['rf_received_at_unix_ms'] is None
            and entry['application_authenticated'] is False and entry['direct_neighbor_verified'] is False)
    require(not heard['entries'] or value['receiving'] and value['serial_identity_verified'])
    return value


def validate_health(value):
    counters = {'battery_mv', 'uptime_seconds', 'error_flags', 'tx_queue', 'tx_airtime_seconds',
        'rx_airtime_seconds', 'packets_received', 'packets_sent', 'sent_flood', 'sent_direct',
        'received_flood', 'received_direct', 'receive_errors'}
    require(type(value) is dict and set(value) == counters | {
        'noise_floor_dbm', 'last_rssi_dbm', 'last_snr_db', 'discovered_nodes'})
    require(all(type(value[key]) is int and 0 <= value[key] <= 0xffffffff for key in counters))
    require(type(value['noise_floor_dbm']) is int and -32768 <= value['noise_floor_dbm'] <= 32767)
    require(type(value['last_rssi_dbm']) is int and -128 <= value['last_rssi_dbm'] <= 127)
    require(type(value['last_snr_db']) in (int, float) and -32 <= value['last_snr_db'] <= 31.75)
    require(type(value['discovered_nodes']) is list and len(value['discovered_nodes']) <= 256)
    seen = set()
    for node in value['discovered_nodes']:
        require(type(node) is dict and set(node) == {'public_key', 'name', 'age_seconds', 'source', 'direct_neighbor_verified'})
        require(type(node['public_key']) is str and re.fullmatch('[0-9a-f]{64}', node['public_key']) is not None
            and node['public_key'] not in seen)
        seen.add(node['public_key'])
        require(node['name'] is None or type(node['name']) is str and len(node['name']) <= 32
            and all(character.isprintable() for character in node['name']))
        require(type(node['age_seconds']) in (int, float) and 0 <= node['age_seconds'] < 300
            and node['source'] == 'firmware_advertisement' and node['direct_neighbor_verified'] is False)
    return value
