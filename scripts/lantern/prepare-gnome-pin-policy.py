#!/usr/bin/env python3
"""Write a local review policy for the source-bound native guard candidate.

Does not install files, create a socket, enroll a process, restart or unlock C.
Never use these observations as another unit's identity/qualification evidence.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


def prepare(candidate, live, install, output):
    manifest_path = candidate / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    observed = json.loads(live.read_text())
    session = dict(line.split('=', 1) for line in observed.get('session', '').splitlines())
    required = {'ui/main.js', 'ui/init.js', 'ui/lanternPinGuard.js', 'ui/lanternGuardState.js'}
    if (manifest.get('schema') != 'sage.lantern.gnome-pin-candidate.v1'
            or set(manifest.get('files', {})) != required or not install.is_absolute()
            or observed.get('library_sha256') != manifest.get('gnome_library_sha256')
            or type(observed.get('broker_uid')) is not int or observed['broker_uid'] <= 0
            or re.fullmatch(r'[0-9a-f]{64}', observed.get('shell_sha256', '')) is None
            or any(session.get(key) != value for key, value in
                   {'Id': observed.get('active_session'), 'User': '1000', 'Seat': 'seat0',
                    'Class': 'user', 'Type': 'wayland', 'Active': 'yes', 'Remote': 'no'}.items())):
        raise ValueError('candidate_policy_refused')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((candidate / 'overlay' / name).read_bytes()).hexdigest() != digest:
            raise ValueError('candidate_digest_mismatch')
    files = {str(install / 'overlay' / name): digest for name, digest in manifest['files'].items()}
    files[str(install / 'manifest.json')] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    files['/usr/lib/gnome-shell/libshell-18.so'] = manifest['gnome_library_sha256']
    policy = dict(schema='sage.lantern.desktop-native-policy.v1', uid=1000,
        overlay='/org/gnome/shell=' + str(install / 'overlay'),
        executable_sha256=observed['shell_sha256'], files=files,
        native_socket='/run/sage-lantern-desktop/native.sock',
        broker_socket='/run/sage-lantern-desktop-auth/control.sock',
        broker_uid=observed['broker_uid'])
    with output.open('x') as stream:
        stream.write(json.dumps(policy, indent=2) + '\n')
    return policy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--live-metadata', type=Path, required=True)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    prepare(args.candidate, args.live_metadata, args.install_dir, args.output)
    print('Local review policy prepared; not installed or accepted.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
