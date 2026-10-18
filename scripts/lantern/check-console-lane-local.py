#!/usr/bin/env python3
"""Read-only check that an activated console lane is the lane that was deployed.

Run on the unit after `deploy-staged-console-bundle-local.py` and
`systemctl enable --now sage-lantern-voice-loop.service`. It answers one
question — "is this unit running the reviewed console lane, and can the question
service answer?" — without writing anything, without starting anything (unless
`--activate` is given) and without needing the owner PIN: the control server's
`/health` works while the device is locked.

A pass is configuration evidence, not acceptance: the on-glass typed question
still needs the owner's unlock (D-019).
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


APP_UNIT = 'sage-lantern-fleet-admin.service'
VOICE_UNIT = 'sage-lantern-voice-loop.service'
HEALTH = 'http://127.0.0.1:3197/health'
SLEEP_CHECK = ('/opt/sage-lantern/venv/bin/python',
               '/usr/local/libexec/sage-lantern-idle-suspend', '--check')
GATE = '/etc/systemd/system/sage-lantern-voice-loop.service.d/60-local-question-lane.conf'
APP_ROOT = '/opt/sage-lantern/fleet-admin'
SYSTEMCTL = '/usr/bin/systemctl'
MAX_FILE = 16 * 1024 * 1024


def run(argv):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=False)
    return result.returncode, result.stdout


def readable(path, *, limit=MAX_FILE):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            return None
        return os.read(descriptor, limit)
    finally:
        os.close(descriptor)


def service(run_command, unit):
    _, enabled = run_command([SYSTEMCTL, 'is-enabled', unit])
    _, active = run_command([SYSTEMCTL, 'is-active', unit])
    _, result = run_command([SYSTEMCTL, 'show', unit, '--property=Result'])
    return {'enabled': enabled.decode().strip(),
            'active': active.decode().strip(),
            'result': result.decode().strip().removeprefix('Result=')}


def health(run_command):
    code, body = run_command(['/usr/bin/curl', '-s', '--max-time', '3', HEALTH])
    if code != 0:
        return None
    try:
        value = json.loads(body.decode('utf-8'))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def manifest_files(manifest):
    try:
        plan = json.loads(Path(manifest).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise SystemExit('manifest_unavailable') from None
    if type(plan) is not dict or plan.get('schema') != 'sage.lantern.console-bundle.v1':
        raise SystemExit('manifest_invalid')
    return plan


def installed(manifest):
    """Compare every recorded destination with what the unit actually holds."""
    results = {'checked': 0, 'mismatched': [], 'missing': []}
    for item in manifest.get('files', []):
        destination = item.get('destination')
        if type(destination) is not str or not destination.startswith('/'):
            raise SystemExit('manifest_invalid')
        results['checked'] += 1
        data = readable(destination)
        if data is None:
            results['missing'].append(destination)
            continue
        if hashlib.sha256(data).hexdigest() != item.get('sha256'):
            results['mismatched'].append(destination)
            continue
        mode = stat.S_IMODE(os.stat(destination).st_mode)
        if mode != item.get('mode'):
            results['mismatched'].append(destination)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path,
                        help='the deployed bundle manifest (default: none — file checks are reported as unverified)')
    parser.add_argument('--activate', action='store_true',
                        help='start the question service if it is not running, then require it active')
    parser.add_argument('--sleep-check', action='store_true',
                        help='also require the guarded sleep check to pass (root only)')
    parser.add_argument('--json', action='store_true', help='print one JSON object only')
    args = parser.parse_args(argv)
    report = {'schema': 'sage.lantern.console-lane-check.v1', 'ok': False, 'checks': {}}
    failures = []

    def record(name, ok, detail):
        report['checks'][name] = {'ok': bool(ok), 'detail': detail}
        if not ok:
            failures.append(name)

    gate = readable(GATE)
    record('question_gate_installed', gate is not None,
           GATE if gate is not None else 'absent')

    voice = service(run, VOICE_UNIT)
    if args.activate and voice['active'] != 'active':
        run([SYSTEMCTL, 'reset-failed', VOICE_UNIT])
        run([SYSTEMCTL, 'start', VOICE_UNIT])
        voice = service(run, VOICE_UNIT)
    record('question_service_active', voice['active'] == 'active' and voice['result'] == 'success', voice)
    record('question_service_enabled', voice['enabled'] == 'enabled',
           {'enabled': voice['enabled'], 'note': 'the reviewed local activation enables the lane at boot'})

    health_value = health(run)
    record('typed_lane_advertised',
           bool(health_value) and health_value.get('typed_ready') is True,
           health_value or 'health_unavailable')
    record('review_lane_advertised',
           bool(health_value) and health_value.get('review_ready') is True,
           {'review_ready': bool(health_value) and health_value.get('review_ready')})

    app = service(run, APP_UNIT)
    record('console_active', app['active'] == 'active', app)
    build_id = readable(Path(APP_ROOT) / 'dist/server/BUILD_ID')
    if args.manifest is not None:
        manifest = manifest_files(args.manifest)
        files = installed(manifest)
        record('bundle_files_verified', not files['missing'] and not files['mismatched'], files)
        expected = manifest.get('app_build_id')
        record('console_build_matches_bundle',
               build_id is not None and expected is not None and build_id.decode().strip() == expected,
               {'installed': build_id.decode().strip() if build_id else None, 'bundle': expected})
    else:
        report['checks']['bundle_files_verified'] = {'ok': None, 'detail': 'no manifest supplied'}
        report['checks']['console_build_matches_bundle'] = {
            'ok': None, 'detail': build_id.decode().strip() if build_id else None}

    if args.sleep_check:
        code, _ = run(list(SLEEP_CHECK))
        record('sleep_check_green', code == 0, {'exit': code})

    report['ok'] = not failures
    report['failures'] = failures
    print(json.dumps(report, sort_keys=True, indent=None if args.json else 1))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
