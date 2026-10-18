#!/usr/bin/env python3
"""Check every root-owned mandatory resource before exec; no stock fallback.

Candidate launcher for the future guarded session. Do not invoke in C's live
session. A root launch authority still has to qualify session/unit selection.
"""
import argparse
import json
import os
from pathlib import Path

from sage_lantern.desktop_native import NativeError, trusted_file

REQUIRED = {'ui/main.js', 'ui/init.js', 'ui/lanternPinGuard.js', 'ui/lanternGuardState.js'}
UNSAFE = {'G_RESOURCE_OVERLAYS', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'GJS_PATH',
          'GI_TYPELIB_PATH', 'SHELL_DEBUG', 'GNOME_SHELL_SESSION_MODE',
          'GIO_MODULE_DIR', 'GIO_EXTRA_MODULES'}


def launch_environment(candidate, shell_digest, environment):
    manifest = json.loads(trusted_file(candidate / 'manifest.json'))
    if (manifest.get('schema') != 'sage.lantern.gnome-pin-candidate.v1'
            or set(manifest.get('files', {})) != REQUIRED
            or any(environment.get(key) for key in UNSAFE)
            or environment.get('XDG_SESSION_TYPE') != 'wayland'):
        raise NativeError()
    trusted_file('/usr/bin/gnome-shell', shell_digest)
    # Library drift refuses; rebuilding against an upgraded image is deliberate.
    trusted_file('/usr/lib/gnome-shell/libshell-18.so', manifest['gnome_library_sha256'])
    for name, digest in manifest['files'].items():
        trusted_file(candidate / 'overlay' / name, digest)
    result = dict(environment)
    result['G_RESOURCE_OVERLAYS'] = '/org/gnome/shell=' + str(candidate / 'overlay')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--shell-sha256', required=True)
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        raise NativeError()  # The compositor runs as the unprivileged graphical account.
    environment = launch_environment(args.candidate, args.shell_sha256, os.environ)
    os.execve('/usr/bin/gnome-shell', ['/usr/bin/gnome-shell', '--mode=ubuntu'], environment)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('mandatory_gnome_guard_unavailable') from None
