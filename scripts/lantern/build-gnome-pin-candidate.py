#!/usr/bin/env python3
"""Build an undeployed mandatory guard against C's frozen GNOME source.

No installer: root launch selection and physical failure qualification remain
required. Refuse another packaged source or a pre-existing output directory.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN_HASH = 'f8ccfb2496fd875ac3fbdfc5178f3b400b35bb11ab564667efd22741d6ba82c5'
INIT_HASH = '336f2bc9e18043dedc8e5673ae05301170ad8c1466840110bef241f9e2fbe899'
# Actual hashes are pinned below from the source-bound C preflight, not upstream HEAD.
LIB_HASH = 'd492a9c4d7c683149518c246fda3bd8cee2c73f4b4a54697ad8fd29604b98c21'


def digest(body):
    return hashlib.sha256(body).hexdigest()


def once(source, before, after):
    if source.count(before) != 1:
        raise ValueError('gnome_source_anchor_mismatch')
    return source.replace(before, after, 1)


def build(preflight, startup, output):
    first, second = json.loads(preflight.read_text()), json.loads(startup.read_text())
    if first['resource_library_sha256'] != LIB_HASH:
        raise ValueError('gnome_library_mismatch')
    resources = {**first['resources'], **second['resources']}
    main = resources['/org/gnome/shell/ui/main.js']['source']
    init = resources['/org/gnome/shell/ui/init.js']['source']
    for name, body in [('main.js', main), ('init.js', init)]:
        if digest(body.encode()) != resources[f'/org/gnome/shell/ui/{name}']['sha256']:
            raise ValueError('source_digest_mismatch')
    if digest(main.encode()) != MAIN_HASH or digest(init.encode()) != INIT_HASH:
        raise ValueError('gnome_main_mismatch')
    main = once(main, "import Gio from 'gi://Gio';", "import Gio from 'gi://Gio';\n" +
                "import * as LanternPinGuard from './lanternPinGuard.js';\n" +
                'export const LANTERN_MANDATORY_GUARD = true;\n' +
                'export function setActionMode(mode) {\n' +
                '    actionMode = LanternPinGuard.isLocked() ? Shell.ActionMode.NONE : mode;\n}')
    # GNOME normally restores NORMAL when startup or another modal completes.
    # Fence every native restore synchronously, not just on a watchdog tick.
    main, count = re.subn(r'(?m)^(\s*)actionMode = (Shell\.ActionMode\.NORMAL|record\.actionMode|newActionMode);',
                          r'\1setActionMode(\2);', main)
    if count != 5:
        raise ValueError('gnome_action_mode_anchors_mismatch')
    main = once(main, '    layoutManager.init();',
                '    LanternPinGuard.install();\n    layoutManager.init();')
    main = once(main, '    extensionManager.init();',
                '    // Mandatory appliance build: optional extensions are never initialized.')
    init = once(init, "import('./main.js').then(main => main.start()).catch(e => {",
                "import('./main.js').then(main => {\n" +
                "            if (main.LANTERN_MANDATORY_GUARD !== true)\n" +
                "                throw new Error('mandatory_guard_missing');\n" +
                '            return main.start();\n        }).catch(e => {')
    # Collect every byte before creating output, so validation leaves no partial tree.
    files = {'ui/main.js': main.encode(), 'ui/init.js': init.encode()}
    for name in ['lanternPinGuard.js', 'lanternGuardState.js']:
        files['ui/' + name] = (ROOT / 'software/gnome' / name).read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    for name, body in files.items():
        path = output / 'overlay' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    manifest = {'schema': 'sage.lantern.gnome-pin-candidate.v1',
        'gnome_library_sha256': LIB_HASH,
        'base_main_sha256': resources['/org/gnome/shell/ui/main.js']['sha256'],
        'base_init_sha256': resources['/org/gnome/shell/ui/init.js']['sha256'],
        'files': {name: digest(body) for name, body in files.items()},
        'installed_on_c': False, 'desktop_pin_accepted': False,
        'launch_supervision_qualified': False}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', type=Path, default=ROOT / 'work/c-desktop-pin-preflight-20261001/installed-source.json')
    parser.add_argument('--startup', type=Path, default=ROOT / 'work/c-desktop-pin-candidate-20261001/startup-source.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = build(args.preflight, args.startup, args.output)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
