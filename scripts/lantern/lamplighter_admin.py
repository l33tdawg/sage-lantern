#!/usr/bin/env python3
"""SSH-local operator commands for the A/B public-pack lab; no web admin API."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

import knowledge_pack as kp
import lamplighter as ll

CONFIG = Path('/etc/sage-lamplighter-lab/operator.json')
ROLE = Path('/etc/sage-lamplighter-lab')
PACK = Path('/var/lib/sage-lamplighter-lab/world-facts-lite-v3')
TRUST = Path('/opt/sage-lamplighter-lab/v1/pack-trust-root.json')
SEED = 'sage-lamplighter-seed.service'


def systemctl(*args):
    return subprocess.run(['/usr/bin/systemctl', *args], check=True,
                          capture_output=True, text=True, timeout=30).stdout.strip()


def config():
    c = json.loads(CONFIG.read_bytes())
    ll.require(c['unit'] in ('unit-a', 'unit-b'), 'unknown support unit')
    ll.require(ll.HEX.fullmatch(c['manifest']), 'invalid pinned manifest')
    for name in ('endpoint', 'peer'):
        c[name] = ll.peer_url(c[name])
    ll.require(c['endpoint'] != c['peer'], 'peer must be the other seed')
    return c


def status(c):
    state = dict(line.split('=', 1) for line in systemctl('show', SEED,
        '-p', 'ActiveState', '-p', 'UnitFileState', '-p', 'MainPID',
        '-p', 'NRestarts', '-p', 'MemoryCurrent').splitlines())
    legacy = {}
    for line in systemctl('list-unit-files', 'sage-lantern-*.service',
                          '--no-legend', '--no-pager').splitlines():
        unit = line.split()[0]
        if not unit.endswith('.service'):
            continue
        properties = dict(row.split('=', 1) for row in systemctl('show', unit,
            '-p', 'MainPID', '-p', 'ActiveState', '-p', 'DropInPaths').splitlines())
        legacy[unit] = (properties['MainPID'] == '0'
            and properties['ActiveState'] in ('inactive', 'failed')
            and '90-lamplighter-lab.conf' in properties['DropInPaths'])
    catalogue_ok = False
    try:
        catalogue = json.loads(ll.get(c['endpoint'], '/catalogue.json', 8192))
        catalogue_ok = catalogue == {'schema': 'sage.lamplighter.catalogue.lab.v1',
            'manifests': [c['manifest']], 'authority': 'untrusted_carrier'}
    except (OSError, ValueError):
        pass
    retired = bool(legacy) and all(legacy.values()) and (ROLE / 'enabled').is_file()
    try:
        absent = not Path('/var/lib/sage-lantern/sage-personal').exists()
    except PermissionError:
        absent = None  # Ordinary operators do not gain access to private storage.
    wipe = json.loads((ROLE / 'personal-sage-wipe.json').read_bytes())
    wipe_recorded = wipe.get('event') == 'personal_sage_deleted' and wipe.get('target_absent') is True
    ready = (state['ActiveState'] == 'active' and state['UnitFileState'] == 'enabled'
             and catalogue_ok and retired and absent is not False and wipe_recorded)
    return {'unit': c['unit'], 'ready': ready, 'service': state,
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'catalogue_matches_pin': catalogue_ok, 'endpoint': c['endpoint'],
        'personal_sage_absent_now': absent, 'personal_sage_wipe_recorded': wipe_recorded,
        'legacy_services_inhibited': legacy,
        'role': json.loads((ROLE / 'role.json').read_bytes()),
        'pack_cache_verified_by_this_command': False,
        'radio_delivery_verified_by_this_command': False}


def verify_cache(c):
    digest, objects = ll.objects(PACK, kp.load_trust_root(TRUST))
    ll.require(digest == c['manifest'], 'cache is not the configured pack')
    return {'unit': c['unit'], 'verified': True, 'manifest': digest,
            'chunks': len(objects) - 2, 'sage_imported': False}


def check_peer(c):
    base = '/packs/' + c['manifest'] + '/'
    root = kp.load_trust_root(TRUST)
    m = ll.manifest(ll.get(c['peer'], base + 'manifest.json', ll.MAX_MANIFEST),
        ll.get(c['peer'], base + 'manifest.sig.json', 4096), root, c['manifest'])
    entry = m['chunks'][0]
    ll.chunk(ll.get(c['peer'], base + 'chunks/' + entry['sha256'] + '.json',
                    ll.MAX_CHUNK), entry)
    return {'unit': c['unit'], 'peer': c['peer'], 'verified_manifest': c['manifest'],
            'verified_sample_chunks': 1, 'full_transfer': False, 'sage_imported': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status', 'verify-cache', 'check-peer', 'restart-seed'))
    args = parser.parse_args()
    c = config()
    if args.command == 'restart-seed':
        ll.require(os.geteuid() == 0, 'restart-seed requires sudo via the existing SSH login')
        ll.require((ROLE / 'enabled').is_file(), 'Lamplighter role is not enabled')
        verify_cache(c)  # Do not interrupt a seed for an invalid replacement cache.
        systemctl('restart', SEED)
        deadline = time.monotonic() + 20
        while True:
            result = status(c)
            if result['ready'] or time.monotonic() >= deadline:
                break
            time.sleep(.5)
    else:
        result = {'status': status, 'verify-cache': verify_cache,
                  'check-peer': check_peer}[args.command](c)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('ready', True) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        raise SystemExit(1)
