#!/usr/bin/env python3
"""Bounded SSH-only pack worker. Requests are JSON on stdin; never shell code."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import knowledge_pack as kp
import lamplighter as ll

CODE = Path(__file__).resolve().parent
CONFIG = Path('/etc/sage-lamplighter-lab/operator.json')
TRUST = CODE / 'pack-trust-root.json'
PACK = Path('/var/lib/sage-lamplighter-lab/world-facts-lite-v3')
STATE = Path('/var/lib/sage-lamplighter-dashboard')
TERMINAL = ('complete', 'failed', 'paused', 'interrupted')


def write(path, value):
    ll.atomic(path, json.dumps(value, sort_keys=True).encode() + b'\n')


def config():
    c = json.loads(CONFIG.read_bytes())
    ll.require(c['unit'] in ('unit-a', 'unit-b') and ll.HEX.fullmatch(c['manifest']), 'invalid operator binding')
    c['peer'] = ll.peer_url(c['peer'])
    c['endpoint'] = ll.peer_url(c['endpoint'])
    return c


def job_path(identifier):
    ll.require(str(uuid.UUID(identifier)) == identifier, 'invalid job ID')
    return STATE / 'jobs' / (identifier + '.json')


def lease():
    fd = os.open(STATE / 'worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ValueError('A transfer is already running on this unit')
    return fd


def current():
    path = STATE / 'active.json'
    if not path.exists():
        return None
    job = json.loads(job_path(json.loads(path.read_bytes())['id']).read_bytes())
    if job['state'] not in TERMINAL:
        try:
            fd = lease()
        except ValueError:
            return job
        try:
            job.update(state='interrupted', phase='interrupted', error='Worker stopped; resume to reuse verified chunks', updated=time.time())
            write(job_path(job['id']), job)
        finally:
            os.close(fd)
    return job


def hardware():
    def read_json(command):
        try:
            r = subprocess.run(command, capture_output=True, text=True, check=True, timeout=4)
            return json.loads(r.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            return []
    neighbours = [{'address': n.get('dst'), 'mac': n.get('lladdr'), 'interface': n.get('dev'),
                   'state': n.get('state', []), 'source': 'LAN neighbour cache'}
                  for n in read_json(['ip', '-j', '-4', 'neigh']) if n.get('dst')]
    usb = []
    for p in Path('/sys/bus/usb/devices').glob('*'):
        if not (p / 'idVendor').exists():
            continue
        def field(name):
            try:
                return (p / name).read_text().strip()
            except OSError:
                return None
        vendor, product = field('idVendor'), field('idProduct')
        if vendor == '1d6b':
            continue
        usb.append({'path': p.name, 'vendor': vendor, 'product_id': product,
                    'name': field('product') or 'USB device', 'manufacturer': field('manufacturer'),
                    'radio': vendor == '303a' and product == '1001'})
    temps = []
    for p in Path('/sys/class/thermal').glob('thermal_zone*/temp'):
        try:
            temps.append(float(p.read_text().strip()) / 1000)
        except (OSError, ValueError):
            pass
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    total, available = (int(memory[k].split()[0])*1024 for k in ('MemTotal', 'MemAvailable'))
    return {'neighbours': neighbours, 'usb': usb, 'temperature_c': max(temps) if temps else None,
            'load_1m': os.getloadavg()[0], 'memory_total': total, 'memory_available': available,
            'uptime_seconds': float(Path('/proc/uptime').read_text().split()[0]),
            'radio_neighbours': {'monitored': False, 'reason': 'LoRa neighbour polling is not active'}}


def snapshot():
    c = config()
    online = False
    try:
        cat = json.loads(ll.get(c['endpoint'], '/catalogue.json', 8192))
        online = c['manifest'] in cat.get('manifests', [])
    except (OSError, ValueError):
        pass
    raw = (PACK / 'manifest.json').read_bytes()
    m = ll.manifest(raw, (PACK / 'manifest.sig.json').read_bytes(), kp.load_trust_root(TRUST), c['manifest'])
    active = current()
    history = sorted((STATE / 'jobs').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    return {'unit': c['unit'], 'seed_online': online, 'endpoint': c['endpoint'],
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'free_bytes': shutil.disk_usage(STATE).free, 'screen_available': True, 'lora_chips': 1,
        'hardware': hardware(),
        'pack': {'manifest': c['manifest'], 'title': m['pack']['title'], 'pack_id': m['pack']['id'],
                 'version': m['pack']['version'], 'chunks': len(m['chunks']), 'bytes': m['totals']['chunk_bytes'],
                 'signature_verified': True, 'seed_available': online},
        'active': active, 'history': [json.loads(p.read_bytes()) for p in history]}


def start(request):
    c = config()
    ll.require(request.get('source') == ('unit-b' if c['unit'] == 'unit-a' else 'unit-a'), 'source must be the other configured unit')
    ll.require(request.get('manifest') == c['manifest'], 'pack is not in the approved catalogue')
    old = current()
    fd = lease()
    try:

        if request['action'] == 'resume':
            ll.require(old and old['state'] in ('paused', 'interrupted', 'failed'), 'no resumable transfer')
            job = old
            ll.require(request.get('id') == job['id'], 'job changed')
        else:
            ll.require(not old or old['state'] == 'complete', 'resume the unfinished transfer first')
            staging = STATE / 'staging'
            ll.require(not staging.is_symlink(), 'unsafe staging directory')
            if staging.exists():
                shutil.rmtree(staging)
            job = {'id': str(uuid.uuid4()), 'source': request['source'], 'target': c['unit'],
                   'manifest': c['manifest'], 'created': time.time(), 'verified': 0, 'total': 0,
                   'downloaded': 0, 'reused': 0, 'download_bytes': 0}
        pause = STATE / 'pause'
        if pause.exists():
            pause.unlink()
        job.update(state='running', phase='queued', updated=time.time(), error=None)
        write(job_path(job['id']), job)
        write(STATE / 'active.json', {'id': job['id']})
        with open(STATE / 'worker.log', 'w') as log:
            subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--worker', job['id'], str(fd)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, pass_fds=(fd,), start_new_session=True)
        return job
    finally:
        os.close(fd)


def worker(identifier, fd):
    # The inherited lock survives SSH exit and is released only when this worker exits.
    os.fstat(fd)
    job = json.loads(job_path(identifier).read_bytes())
    c = config()
    path = job_path(identifier)
    def update(event):
        job.update(event, updated=time.time())
        write(path, job)
    def paused():
        return (STATE / 'pause').exists()
    try:
        result = ll.fetch([c['peer']], c['manifest'], STATE / 'staging', kp.load_trust_root(TRUST),
                          progress=update, cancelled=paused)
        ll.require(result['complete'], 'incomplete pack')
        update({'phase': 'publishing'})
        # All bytes verified before publication. Existing seed remains available.
        staging = STATE / 'staging'
        for source in (staging / 'chunks').iterdir():
            ll.atomic(PACK / 'chunks' / source.name, source.read_bytes(), mode=0o644)
        for name in ('manifest.json', 'manifest.sig.json'):
            ll.atomic(PACK / name, (staging / name).read_bytes(), mode=0o644)
        ll.objects(PACK, kp.load_trust_root(TRUST))
        update({'state': 'complete', 'phase': 'complete', 'verified': result['chunks'],
                'total': result['total_chunks'], 'receipt': result, 'completed': time.time()})
        for old in sorted((STATE / 'jobs').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[50:]:
            old.unlink()
    except InterruptedError:
        update({'state': 'paused', 'phase': 'paused', 'error': None})
    except Exception as error:
        update({'state': 'failed', 'phase': 'failed', 'error': str(error)[:350]})
    finally:
        os.close(fd)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        worker(sys.argv[2], int(sys.argv[3]))
        return
    request = json.loads(sys.stdin.buffer.read(8193))
    ll.require(type(request) is dict, 'invalid request')
    action = request.get('action')
    if action == 'snapshot':
        result = snapshot()
    elif action in ('start', 'resume'):
        # Serialize starts/resumes/pause requests, separate from the worker lease.
        with open(STATE / 'control.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = start(request)
    elif action == 'pause':
        with open(STATE / 'control.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            job = current()
            ll.require(job and job['id'] == request.get('id') and job['state'] == 'running', 'no matching running transfer')
            ll.require(job['phase'] != 'publishing', 'Pack is being published; wait for verification to finish')
            (STATE / 'pause').touch()
            result = {'pause_requested': True, 'id': job['id']}
    else:
        raise ValueError('unknown operation')
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': str(error)[:350]}))
        raise SystemExit(1)
