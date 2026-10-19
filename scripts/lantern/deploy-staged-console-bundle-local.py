#!/usr/bin/env python3
"""Install a staged console bundle on one C-F unit; never enable or start it.

This is the parent deployer `build-console-bundle-local.py` describes. It
verifies the staged payload against the frozen manifest, backs up every file it
overwrites, refuses to replace a destination that neither matches the
predecessor release payload nor is explicitly accepted by name, installs
atomically with fsync, reloads systemd when unit files changed and writes a
receipt. It does not enable or start any unit and does not create any marker.

D-017 rules apply: either the destination is byte-identical to the predecessor
payload (`--predecessor`, the release tree the unit is anchored on) or the
operator accepts it by name, in which case the observed digest is recorded under
`replaced_unverifiable_targets`. A refusal names its gate.

Two console-specific gates: a bundle that changes `package.json` or
`package-lock.json` is refused (the staged bundle carries no `node_modules`, so
a dependency change belongs to a release cut), and the local question-lane
drop-in is refused on a unit whose voice lane is owned by the mesh broker,
because that broker authorizes the voice start itself.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess


MAX_FILE = 16 * 1024 * 1024
MAX_FILES = 4096
QUESTION_GATE = '/etc/systemd/system/sage-lantern-voice-loop.service.d/60-local-question-lane.conf'
DEPENDENCY_FILES = ('/opt/sage-lantern/fleet-admin/package.json',
                    '/opt/sage-lantern/fleet-admin/package-lock.json')
MESH_CONFIG = '/etc/sage-lantern/mesh-runtime.json'
MESH_ENVIRONMENT = 'SAGE_LANTERN_MESH_BROKER=1'
VOICE_UNIT = 'sage-lantern-voice-loop.service'
APP_UNIT = 'sage-lantern-fleet-admin.service'
SYSTEMCTL = '/usr/bin/systemctl'


class Refused(Exception):
    def __init__(self, gate):
        super().__init__(gate)
        self.gate = gate


def read_regular(path, *, limit=MAX_FILE):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise Refused('destination_not_a_regular_file') from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise Refused('destination_not_a_regular_file')
        data = bytearray()
        while True:
            chunk = os.read(descriptor, limit + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise Refused('file_limit')
        return bytes(data), metadata
    finally:
        os.close(descriptor)


def trusted_directory(root, destination, *, owner):
    for component in reversed(destination.parents):
        relative = str(component).lstrip('/')
        path = (root / relative) if str(root) != '/' else Path(component)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return False
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner
                or metadata.st_mode & 0o022):
            raise Refused('untrusted_destination_directory')
    return True


def prepare_parent(root, destination, *, owner):
    if trusted_directory(root, destination, owner=owner):
        return
    parent = destination.parent
    (root / str(parent).lstrip('/') if str(root) != '/' else parent).mkdir(mode=0o755, parents=True, exist_ok=True)
    if not trusted_directory(root, destination, owner=owner):
        raise Refused('untrusted_destination_directory')


def install_file(root, destination, data, mode, *, owner):
    target = (root / destination.lstrip('/')) if str(root) != '/' else Path(destination)
    temporary = target.parent / ('.' + target.name + '.console-new')
    if temporary.exists() or temporary.is_symlink():
        raise Refused('stale_replacement_file')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if os.geteuid() == 0 and str(root) == '/':
        os.chown(temporary, owner, owner)
    os.replace(temporary, target)
    sync_dir(target.parent)


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def backup_file(backup_root, index, destination, data, mode):
    relative = str(Path(destination).relative_to('/'))
    path = backup_root / 'files' / f'{index:03d}'
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)
    return {'path': str(path), 'destination': destination, 'relative': relative, 'mode': mode}


def prepare_backup_root(backup_root, *, owner):
    parent = backup_root.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if str(backup_root) != '/' and backup_root.exists():
        raise Refused('backup_root_exists')
    backup_root.mkdir(mode=0o700)
    if os.geteuid() == 0 and str(backup_root).startswith('/'):
        os.chown(backup_root, owner, owner)
    return backup_root


def mesh_managed(root, run):
    """The mesh broker owns the voice start only when its runtime is installed."""
    if (root / MESH_CONFIG.lstrip('/')).exists() or (root / MESH_CONFIG.lstrip('/')).is_symlink():
        return True, 'configured'
    if str(root) != '/':
        return False, 'unknown'
    result = run([SYSTEMCTL, 'show', VOICE_UNIT, '--property=Environment'])
    environment = result.stdout.decode('utf-8', errors='strict')
    return False, 'dangling_mesh_environment' if MESH_ENVIRONMENT in environment else 'absent'


def anchor_path(predecessor_root, item):
    """Where the anchored release payload keeps this destination.

    The release payload keeps the app under `apps/lantern-fleet-admin/` and the
    question loop under `scripts/lantern/`, so each bundle record names its own
    release path; a record without one (a new file) has no anchor.
    """
    release_path = item.get('release_path')
    if release_path is None:
        return None
    if (type(release_path) is not str or not release_path or release_path.startswith('/')
            or '..' in Path(release_path).parts):
        raise Refused('unsafe_release_path')
    candidate = predecessor_root / release_path
    return candidate if candidate.is_file() and not candidate.is_symlink() else None


def stage_anchors(predecessor_stage):
    """Destination -> artifact for a previously deployed console bundle.

    The second local deploy of a lane can no longer be anchored on the release
    payload, because the unit now runs the first bundle. Pointing at that staged
    bundle keeps every replacement anchored on the exact bytes that were
    installed, and the receipt still names what changed.
    """
    stage = Path(predecessor_stage)
    manifest_path = stage / 'manifest.json'
    if not manifest_path.is_file():
        raise Refused('predecessor_stage_unavailable')
    plan = json.loads(read_regular(manifest_path)[0].decode('utf-8'))
    if type(plan) is not dict or plan.get('schema') != 'sage.lantern.console-bundle.v1' or type(plan.get('files')) is not list:
        raise Refused('predecessor_stage_unavailable')
    anchors = {}
    for item in plan['files']:
        artifact = item.get('artifact')
        destination = item.get('destination')
        if (type(artifact) is not str or type(destination) is not str
                or not artifact.startswith('payload/') or '..' in Path(artifact).parts):
            raise Refused('predecessor_stage_unavailable')
        path = stage / artifact
        if path.is_file() and not path.is_symlink():
            anchors[destination] = path
    return anchors


def deploy(stage, manifest_path, backup_root, *, root=Path('/'), predecessor=None,
           predecessor_stage=None, accepted=(), owner=0, restart_app=False, run=None):
    if run is None:
        def run(argv):
            return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, check=False)
    stage = Path(stage)
    root = Path(root)
    plan = json.loads(read_regular(manifest_path)[0].decode('utf-8'))
    if (type(plan) is not dict or plan.get('schema') != 'sage.lantern.console-bundle.v1'
            or plan.get('mode') != 'stage-only' or type(plan.get('files')) is not list
            or not 1 <= len(plan['files']) <= MAX_FILES):
        raise Refused('manifest_invalid')
    if not (stage / 'payload').is_dir() or not (stage / 'manifest.json').is_file():
        raise Refused('stage_incomplete')
    if read_regular(stage / 'manifest.json')[0] != read_regular(manifest_path)[0]:
        raise Refused('stage_manifest_mismatch')
    destinations = [item.get('destination') for item in plan['files']]
    if len(set(destinations)) != len(destinations):
        raise Refused('duplicate_destination')
    accepted = {str(Path(item)) for item in accepted}
    if QUESTION_GATE in destinations:
        mesh, mesh_state = mesh_managed(root, run)
        if mesh:
            raise Refused('mesh_question_lane_owns_voice_start')
    else:
        mesh_state = 'untouched'
    predecessor_root = Path(predecessor) if predecessor is not None else None
    if predecessor_root is not None and not predecessor_root.is_dir():
        raise Refused('predecessor_unavailable')
    anchors = stage_anchors(predecessor_stage) if predecessor_stage is not None else {}
    if predecessor_root is None and predecessor_stage is None:
        raise Refused('anchor_required')
    # A staged console bundle never carries node_modules, so a dependency change
    # must be refused before anything is written: it belongs to a release cut.
    for item in plan['files']:
        if item['destination'] in DEPENDENCY_FILES:
            previous = anchors.get(item['destination'])
            resolved = previous if previous is not None else (
                anchor_path(predecessor_root, item) if predecessor_root is not None else None)
            if resolved is not None and read_regular(resolved)[0] != read_regular(stage / item['artifact'])[0]:
                raise Refused('dependency_change_requires_release')
    prepare_backup_root(Path(backup_root), owner=owner)
    receipt = {'version': 1, 'scope': plan.get('bundle_id'), 'activated': False,
               'app_restarted': False, 'mesh_voice_state': mesh_state,
               'manifest_sha256': hashlib.sha256(read_regular(manifest_path)[0]).hexdigest(),
               'files': [], 'replaced_unverifiable_targets': [], 'backups': []}
    for index, item in enumerate(plan['files']):
        destination = item['destination']
        if type(destination) is not str or not destination.startswith('/') or '..' in Path(destination).parts:
            raise Refused('unsafe_destination')
        if item.get('artifact') != f'payload/{index:03d}':
            raise Refused('unsafe_artifact')
        data = read_regular(stage / item['artifact'])[0]
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise Refused('payload_does_not_match_manifest')
        prepare_parent(root, Path(destination), owner=owner)
        target = (root / destination.lstrip('/')) if str(root) != '/' else Path(destination)
        before = None
        if target.exists() or target.is_symlink():
            current, metadata = read_regular(target)
            if metadata.st_uid != owner or stat.S_IMODE(metadata.st_mode) not in (0o644, 0o755, 0o600):
                raise Refused('untrusted_destination')
            before = hashlib.sha256(current).hexdigest()
            if current != data:
                anchor = None
                previous = anchors.get(destination)
                if previous is not None:
                    anchor = hashlib.sha256(read_regular(previous)[0]).hexdigest()
                elif predecessor_root is not None:
                    candidate = anchor_path(predecessor_root, item)
                    if candidate is not None:
                        anchor = hashlib.sha256(read_regular(candidate)[0]).hexdigest()
                if anchor != before and destination not in accepted and str(target) not in accepted:
                    raise Refused('destination_differs_from_predecessor')
                if anchor != before:
                    receipt['replaced_unverifiable_targets'].append(
                        {'destination': destination, 'observed_sha256': before})
            receipt['backups'].append(backup_file(Path(backup_root), index, destination, current,
                                                  stat.S_IMODE(metadata.st_mode)))
            mode = stat.S_IMODE(metadata.st_mode)
        else:
            mode = item['mode']
        install_file(root, destination, data, mode, owner=owner)
        receipt['files'].append({'destination': destination, 'mode': mode,
                                 'sha256': hashlib.sha256(data).hexdigest(),
                                 'replaced_sha256': before})
    if any(item['destination'].startswith('/etc/systemd/system/') for item in plan['files']):
        result = run([SYSTEMCTL, 'daemon-reload'])
        receipt['daemon_reload'] = result.returncode == 0
        if result.returncode != 0:
            raise Refused('daemon_reload_failed')
    if restart_app:
        result = run([SYSTEMCTL, 'restart', APP_UNIT])
        if result.returncode != 0:
            raise Refused('app_restart_failed')
        active = run([SYSTEMCTL, 'is-active', APP_UNIT])
        if active.stdout.decode('utf-8', errors='strict').strip() != 'active':
            raise Refused('app_not_active_after_restart')
        receipt['app_restarted'] = True
    backup_root = Path(backup_root)
    (backup_root / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    sync_dir(backup_root)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--manifest', type=Path,
                        help='frozen manifest (default: <stage>/manifest.json)')
    parser.add_argument('--backup-root', type=Path, required=True)
    parser.add_argument('--predecessor', type=Path,
                        help='release payload tree the unit is anchored on (D-017)')
    parser.add_argument('--predecessor-stage', type=Path,
                        help='previously deployed console bundle this unit now runs (D-017)')
    parser.add_argument('--accept-replaced', action='append', default=[],
                        help='destination explicitly accepted for replacement (recorded)')
    parser.add_argument('--restart-app', action='store_true',
                        help='restart the console unit and verify it is active afterwards')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    manifest = args.manifest or (args.stage / 'manifest.json')
    try:
        receipt = deploy(args.stage, manifest, args.backup_root, root=args.root,
                         predecessor=args.predecessor, predecessor_stage=args.predecessor_stage,
                         accepted=args.accept_replaced,
                         restart_app=args.restart_app)
    except Refused as error:
        print(json.dumps({'deployed': False, 'refused': error.gate}, sort_keys=True))
        return 2
    print(json.dumps({'deployed': True, 'activated': False,
                      'receipt': str(args.backup_root / 'receipt.json'),
                      'files': len(receipt['files']),
                      'app_restarted': receipt['app_restarted'],
                      'replaced_unverifiable_targets': len(receipt['replaced_unverifiable_targets'])},
                     sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
