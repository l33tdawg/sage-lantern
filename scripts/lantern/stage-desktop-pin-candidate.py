#!/usr/bin/env python3
"""Freeze a matched, disabled review bundle. Never install/activate a session."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SITE = '/opt/sage-lantern/venv/lib/python3.11/site-packages/sage_lantern'
GNOME = Path('/usr/local/lib/sage-lantern/gnome-pin')
NATIVE_POLICY = '/etc/sage-lantern/desktop-native-policy.json'
LAUNCH_POLICY = '/etc/sage-lantern/desktop-launch-policy.json'
HELPERS = {
    'guarded-session-authority.py': 'sage-lantern-guarded-session-authority',
    'guarded-session-bridge.py': 'sage-lantern-guarded-session-bridge',
    'desktop-native-adapter.py': 'desktop-native-adapter.py',
    'unit-a-device-auth.py': 'sage-lantern-device-auth',
    'cf-mode-supervisor.py': 'sage-lantern-mode-supervisor',
    'lantern-idle-suspend.py': 'sage-lantern-idle-suspend',
}
UNITS = {
    'sage-lantern-guarded-session.service': '/etc/systemd/system/sage-lantern-guarded-session.service',
    'gnome-shell-pin-candidate.conf': '/etc/systemd/user/org.gnome.Shell@ubuntu.service.d/90-lantern-pin.conf',
    'desktop-auth-pin-candidate.conf': '/etc/systemd/system/sage-lantern-device-auth.service.d/90-desktop-pin.conf',
    'sage-lantern-device-auth.service': '/etc/systemd/system/sage-lantern-device-auth.service',
}


def script(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'),
                                                 ROOT / 'scripts/lantern' / filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def sha(body):
    return hashlib.sha256(body).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def stage(preflight, startup, live, output):
    if output.exists():
        raise FileExistsError(output)
    observed = json.loads(live.read_text())
    if (observed.get('kiosk_uid') != 1000 or type(observed.get('kiosk_gid')) is not int
            or observed['kiosk_gid'] <= 0):
        raise ValueError('kiosk_nss_observation_required')
    payloads = {}
    def add(target, body, source, mode=0o644):
        if target in payloads or not Path(target).is_absolute():
            raise ValueError('duplicate_target')
        payloads[target] = dict(body=body, source=source, mode=mode)
    with tempfile.TemporaryDirectory(prefix='desktop-pin-stage-') as temporary:
        candidate = Path(temporary) / 'gnome'
        script('build-gnome-pin-candidate.py').build(preflight, startup, candidate)
        native_path = Path(temporary) / 'native-policy.json'
        native = script('prepare-gnome-pin-policy.py').prepare(candidate, live, GNOME, native_path)
        for path in sorted(candidate.rglob('*')):
            if path.is_file():
                add(str(GNOME / path.relative_to(candidate)), path.read_bytes(), 'source-bound GNOME overlay')
        add(NATIVE_POLICY, native_path.read_bytes(), 'generated native policy')
    for source, filename in HELPERS.items():
        add('/usr/local/libexec/' + filename, (ROOT / 'scripts/lantern' / source).read_bytes(),
            'scripts/lantern/' + source, 0o755)
    # A full source snapshot makes the candidate reviewable as a matched set.
    # It does not prove unrelated local runtime changes match installed r17.
    for path in sorted((ROOT / 'software/runtime/src/sage_lantern').glob('*.py')):
        add(SITE + '/' + path.name, path.read_bytes(), str(path.relative_to(ROOT)))
    for source, target in UNITS.items():
        add(target, (ROOT / 'software/systemd' / source).read_bytes(), 'software/systemd/' + source)
    add('/etc/xdg/autostart/sage-lantern-console.desktop',
        (ROOT / 'software/gnome/sage-lantern-console-pin-candidate.desktop').read_bytes(),
        'software/gnome/sage-lantern-console-pin-candidate.desktop')
    add('/etc/sage-lantern/session-profile', b'wayland-mutter-native-pin\n',
        'explicit candidate sleep profile')
    launch = dict(schema='sage.lantern.guarded-launch-policy.v1', uid=1000,
        gid=observed['kiosk_gid'], account='sage', home='/home/sage', native_policy=NATIVE_POLICY,
        native_policy_sha256=sha(payloads[NATIVE_POLICY]['body']),
        files={target: sha(item['body']) for target, item in payloads.items()},
        activation_file='/etc/sage-lantern/desktop-pin-activation.json')
    add(LAUNCH_POLICY, encoded(launch), 'generated root launch policy')
    # No activation file, session selector, enabled links or executable installer.
    records = [dict(target=target, bundle_path='payload/rootfs' + target,
        sha256=sha(item['body']), source=item['source'], mode=oct(item['mode']))
        for target, item in sorted(payloads.items())]
    manifest = dict(schema='sage.lantern.desktop-pin-review-bundle.v1',
        base_release='lantern-cf-20260930-r17',
        base_release_manifest='8f4be6fa6e903f1d4c9af249dd74826b2c1428b4cbd70ca827807a3d95226a00',
        installed_on_c=False, deployable=False, desktop_pin_accepted=False,
        files=records, input_sha256={str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path):
            sha(path.read_bytes()) for path in (preflight, startup, live)},
        unresolved=['qualified power and fresh-session window',
            'root-controlled GDM/session selection and user override closure',
            'logind/DRM seat access from root-created Shell',
            'first-frame concealment and real kernel input coverage',
            'Wayland compositor death/application preservation limits',
            'portrait native/private-API duplicate-login UX',
            'physical sleep/wake and display power',
            'matched release integration of all snapshotted runtime changes'])
    output.mkdir(parents=True)
    for record in records:
        destination = output / record['bundle_path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payloads[record['target']]['body'])
        destination.chmod(int(record['mode'], 8))
    (output / 'manifest.json').write_bytes(encoded(manifest))
    return manifest


def verify(directory):
    value = json.loads((directory / 'manifest.json').read_text())
    if (value.get('schema') != 'sage.lantern.desktop-pin-review-bundle.v1'
            or any(value.get(flag) is not False for flag in
                   ('installed_on_c', 'deployable', 'desktop_pin_accepted'))):
        raise ValueError('review_flags_invalid')
    paths = set()
    for item in value['files']:
        relative = Path(item['bundle_path'])
        if relative.is_absolute() or '..' in relative.parts or str(relative) in paths:
            raise ValueError('bundle_path_invalid')
        paths.add(str(relative))
        source = directory / relative
        if source.is_symlink() or sha(source.read_bytes()) != item['sha256']:
            raise ValueError('bundle_digest_mismatch')
    actual = {str(path.relative_to(directory)) for path in (directory / 'payload').rglob('*') if path.is_file()}
    if actual != paths:
        raise ValueError('bundle_payload_mismatch')
    return len(paths)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', type=Path, default=ROOT / 'work/c-desktop-pin-preflight-20261001/installed-source.json')
    parser.add_argument('--startup', type=Path, default=ROOT / 'work/c-desktop-pin-candidate-20261001/startup-source.json')
    parser.add_argument('--live-metadata', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        print(f'Verified {verify(args.verify)} review payloads; candidate remains disabled.')
    else:
        if args.live_metadata is None or args.output is None:
            parser.error('--live-metadata and --output are required')
        stage(args.preflight, args.startup, args.live_metadata, args.output)
        print(f'Prepared {verify(args.output)} review payloads; candidate remains disabled.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
