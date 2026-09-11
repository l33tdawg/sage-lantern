"""Strict public evidence schema; never rewrites or upgrades auth drain receipts.

validate() requires independently observed live state from a trusted caller.
It is not a mutation authority, historical cleanup proof, or standalone gate.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import uuid


SCHEMA = 'sage.lantern.auth-capability-supersession.v1'
BINDING_FIELDS = {'unit_id', 'boot_id', 'root_id', 'chain_id', 'initialized_sha256',
                  'native_before', 'native_after', 'sage_before', 'current_auth', 'unknown_receipts'}
IMAGE_FIELDS = {'binary_sha256', 'manifest_sha256', 'inventory_sha256', 'version'}
PROCESS_FIELDS = {'pid', 'start_ticks', 'invocation', 'uid', 'application_boot_id'}
AUTH_FIELDS = {'pid', 'start_ticks', 'instance_id', 'uid'}


def listener_owner(pid, start_ticks, boot_id, *, proc=Path('/proc')):
    process = proc / str(pid)
    def identity():
        return ((process / 'stat').read_text().rsplit(')', 1)[1].split()[19],
                (proc / 'sys/kernel/random/boot_id').read_text().strip())
    require(identity() == (str(start_ticks), boot_id))
    def listening():
        with (proc / 'net/tcp').open('rb') as stream:
            raw = stream.read(1048577)
        require(len(raw) <= 1048576)
        rows = [row.split() for row in raw.decode('ascii').splitlines()[1:]]
        matches = [row for row in rows if len(row) >= 10 and row[3] == '0A' and row[1].endswith(':1F90')]
        require(len(matches) == 1 and matches[0][1] == '0100007F:1F90'
                and re.fullmatch('[1-9][0-9]*', matches[0][9]))
        return matches[0][9]
    inode = listening()
    owned = None
    for index, entry in enumerate((process / 'fd').iterdir()):
        require(index < 4096)
        try:
            if os.readlink(entry) == 'socket:[' + inode + ']':
                owned = entry
        except FileNotFoundError:
            continue
    require(owned is not None and os.readlink(owned) == 'socket:[' + inode + ']'
            and listening() == inode and identity() == (str(start_ticks), boot_id))
    return inode


def require(value):
    if not value:
        raise ValueError('auth_supersession_refused')


def exact(value, fields):
    require(type(value) is dict and set(value) == fields)


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) and value != '0' * 64)


def invocation(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{32}', value) and value != '0' * 32)


def boot(value):
    require(type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0)


def identity(value, auth=False):
    exact(value, AUTH_FIELDS if auth else PROCESS_FIELDS)
    for name in ('pid', 'start_ticks', 'uid'):
        require(type(value[name]) is int and value[name] > 0)
    invocation(value['instance_id' if auth else 'invocation'])
    if not auth:
        boot(value['application_boot_id'])


def image(value):
    exact(value, IMAGE_FIELDS)
    for name in IMAGE_FIELDS - {'version'}:
        digest(value[name])
    require(type(value['version']) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9.+_-]{0,159}', value['version']))


def receipt(raw, instance, confirmed):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value)
            value[key] = item
        return value
    require(type(raw) is bytes and 0 < len(raw) <= 512)
    value = json.loads(raw, object_pairs_hook=pairs)
    exact(value, {'version', 'instance_id', 'closing', 'drained', 'local_revoked', 'upstream_cleanup'})
    require(type(value['version']) is int and value['version'] == 1 and value['instance_id'] == instance)
    require(all(value[name] is confirmed for name in ('closing', 'drained', 'local_revoked')))
    require(value['upstream_cleanup'] == ('confirmed' if confirmed else 'unknown'))


def validate(evidence, *, binding, receipts, live):
    exact(binding, BINDING_FIELDS)
    require(binding['unit_id'] == 'unit-a')
    boot(binding['boot_id'])
    digest(binding['root_id'])
    digest(binding['initialized_sha256'])
    require(type(binding['chain_id']) is str and re.fullmatch('sage-personal-[A-Za-z0-9._-]{1,110}', binding['chain_id']))
    for name in ('native_before', 'native_after'):
        image(binding[name])
    identity(binding['sage_before'])
    identity(binding['current_auth'], auth=True)
    require(binding['sage_before']['uid'] == binding['current_auth']['uid'])
    unknown = binding['unknown_receipts']
    require(type(unknown) is dict and 1 <= len(unknown) <= 1023)
    current = binding['current_auth']['instance_id']
    require(current not in unknown)
    for instance, expected in unknown.items():
        invocation(instance)
        digest(expected)
    exact(evidence, BINDING_FIELDS | {'schema', 'sage_after', 'current_auth_report_sha256',
          'capability_invalidated', 'historical_request_completion'})
    require(evidence['schema'] == SCHEMA)
    require(all(evidence[name] == binding[name] for name in BINDING_FIELDS))
    identity(evidence['sage_after'])
    require(evidence['sage_after']['application_boot_id'] != binding['sage_before']['application_boot_id'])
    require(all(evidence['sage_after'][name] == binding['sage_before'][name]
                for name in PROCESS_FIELDS - {'application_boot_id'}))
    require(evidence['capability_invalidated'] is True and evidence['historical_request_completion'] == 'unknown')
    digest(evidence['current_auth_report_sha256'])
    require(type(receipts) is dict and unknown.keys() <= receipts.keys() and current in receipts and len(receipts) <= 1024)
    for instance, raw in receipts.items():
        invocation(instance)
        receipt(raw, instance, confirmed=instance not in unknown)
        if instance in unknown:
            require(hashlib.sha256(raw).hexdigest() == unknown[instance])
        if instance == current:
            require(hashlib.sha256(raw).hexdigest() == evidence['current_auth_report_sha256'])
    exact(live, {'boot_id', 'sage_after', 'native_after', 'auth_inactive', 'auth_cgroup_empty',
                 'starts_inhibited', 'encrypted', 'vault_locked'})
    require(live['boot_id'] == binding['boot_id'] and live['sage_after'] == evidence['sage_after']
            and live['native_after'] == binding['native_after'])
    require(all(live[name] is True for name in ('auth_inactive', 'auth_cgroup_empty',
                'starts_inhibited', 'encrypted', 'vault_locked')))
    return {'capability_invalidated': True, 'historical_request_completion': 'unknown',
            'mutation_authorized': False, 'auth_drain_receipts_rewritten': False}
