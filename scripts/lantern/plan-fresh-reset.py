#!/usr/bin/env python3
"""Unit A metadata-only reset preview; never a deletion manifest or authority.

Usage: python3 scripts/lantern/plan-fresh-reset.py --preview
Exit 0 means the bounded inventory completed, not that reset is safe. Exit 2
means inventory was incomplete or a traversal boundary was rejected. No file
contents are opened except Linux mount metadata at /proc/self/mountinfo.
"""

import argparse
import json
import os
from pathlib import PurePosixPath
import re
import stat
import sys


RECOVERY = '/var/lib/sage-lantern/recovery/radio/factory-v4-full-20260902.bin'
UNIT_NAMES = tuple('sage-lantern-' + name + '.service' for name in (
    'mesh-runtime', 'gateway-evidence', 'llm', 'ptt', 'voice-loop',
    'idle-suspend', 'camera', 'display-power', 'status', 'personal-sage',
    'runtime', 'device-auth', 'fleet-admin', 'device-agent', 'observation-status',
)) + ('sage-lantern-observation-status.timer',)
UNIT_DIRS = ('/etc/systemd/system', '/run/systemd/system',
             '/usr/lib/systemd/system', '/lib/systemd/system',
             '/run/systemd/generator', '/run/systemd/generator.early',
             '/run/systemd/generator.late', '/etc/systemd/user',
             '/home/radxa/.config/systemd/user')
RECURSIVE_ROOTS = (
    '/opt/sage-lantern', '/etc/sage-lantern', '/var/lib/sage-lantern',
    '/var/lib/sage-lantern-device', '/var/lib/sage-lantern-mesh-supervisor',
    '/home/radxa/.config/sage-lantern-chromium',
    '/home/radxa/.config/sage-lantern/unit-a/chromium',
) + tuple('/run/sage-lantern-' + name for name in (
    'model', 'voice', 'ptt', 'gateway-evidence', 'mesh-control', 'suspend'))
LEAF_PATHS = (
    '/etc/systemd/system/systemd-suspend.service.d/90-sage-lantern.conf',
    '/etc/tmpfiles.d/sage-lantern-suspend.conf',
    '/etc/udev/rules.d/71-sage-lantern-audio.rules',
    '/usr/local/bin/sage-lantern-session', '/usr/local/bin/sage-lantern-kiosk',
    '/usr/local/sbin/activate-unit-a-local-voice',
    '/usr/local/sbin/smoke-test-lantern-unit',
    '/usr/share/xsessions/sage-lantern.desktop',
    '/home/radxa/.config/autostart/sage-lantern-kiosk.desktop',
) + tuple('/etc/sage-lantern-' + name + '.enabled' for name in (
    'device-lock', 'vault-setup', 'idle-suspend',
)) + tuple('/usr/local/libexec/sage-lantern-' + name for name in (
    'device-auth', 'voice-loop', 'camera-server', 'status-server', 'ptt-monitor',
    'observation-status', 'verify-local-voice', 'approve-local-voice',
    'configure-kiosk-login', 'configure-unit-a-hostname', 'display-power',
    'idle-suspend',
)) + tuple('/run/sage-lantern-' + name for name in (
    'sleep.fenced', 'deployment.pending', 'voice-drain-request.json',
    'sleep-status.json', 'storage-broker.sock',
))
PRESERVE_PATHS = (
    RECOVERY, '/usr/local/share/sage-lantern/meshcore-radio-public.json',
    '/usr/bin/node', '/etc/ssh', '/home/radxa/.ssh', '/etc/NetworkManager',
    '/var/lib/NetworkManager', '/etc/network', '/etc/wpa_supplicant',
    '/etc/passwd', '/etc/group', '/etc/shadow', '/etc/gdm3',
    '/etc/gdm3/daemon.conf', '/etc/gdm3/custom.conf', '/etc/gdm/custom.conf',
    '/var/lib/AccountsService/users/radxa',
    '/etc/systemd/system/display-manager.service',
    '/var/lib/AccountsService', '/etc/systemd/logind.conf',
    '/etc/systemd/logind.conf.d', '/boot', '/etc/default/grub',
    '/usr/share/plymouth', '/lib/firmware',
)
DISCOVERY_DIRS = (
    '/opt', '/etc', '/var/lib', '/run', '/root', '/home/radxa',
    '/home/radxa/.config', '/home/radxa/.config/autostart',
    '/home/radxa/.config/sage-lantern',
    '/home/radxa/.config/sage-lantern/unit-a',
    '/usr/local/libexec', '/usr/local/bin', '/usr/local/sbin',
    '/usr/local/share', '/usr/share/xsessions', '/etc/udev/rules.d',
    '/etc/tmpfiles.d', '/etc/systemd/system/systemd-suspend.service.d',
)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def parse_mountinfo(raw):
    if not raw or len(raw) > 4 * 1024 * 1024:
        raise ValueError('missing or oversized mount metadata')
    mounts = set()
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < 10 or '-' not in fields[6:]:
            raise ValueError('malformed mount metadata')
        if not fields[0].isdigit() or not fields[1].isdigit():
            raise ValueError('malformed mount identifiers')
        path = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4])
        if not path.startswith('/') or str(PurePosixPath(path)) != path or '..' in PurePosixPath(path).parts:
            raise ValueError('noncanonical mount path')
        mounts.add(path)
    if '/' not in mounts:
        raise ValueError('root mount missing')
    return mounts


def read_mountinfo():
    with open('/proc/self/mountinfo', 'r', encoding='utf-8') as source:
        return source.read(4 * 1024 * 1024 + 1)


def identity(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


class Inventory:
    def __init__(self, root_fd, mounts, max_entries, max_depth):
        self.root_fd = root_fd
        self.mounts = mounts
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.visited = 0
        self.rows = {}
        self.blockers = []
        self.complete = True

    def block(self, path, reason, incomplete=False):
        item = {'path': path, 'reason': reason}
        if item not in self.blockers:
            self.blockers.append(item)
        if incomplete:
            self.complete = False

    def budget(self):
        self.visited += 1
        if self.visited > self.max_entries:
            raise ValueError('entry limit exceeded')

    def record(self, path, metadata, category):
        kind = ('directory' if stat.S_ISDIR(metadata.st_mode) else
                'symlink' if stat.S_ISLNK(metadata.st_mode) else
                'file' if stat.S_ISREG(metadata.st_mode) else 'special')
        self.rows[path] = dict(path=path, category=category, kind=kind,
            uid=metadata.st_uid, gid=metadata.st_gid, mode=oct(stat.S_IMODE(metadata.st_mode)),
            device=metadata.st_dev, inode=metadata.st_ino, links=metadata.st_nlink,
            bytes=metadata.st_size, mtime_ns=metadata.st_mtime_ns,
            ctime_ns=metadata.st_ctime_ns)

    def directory(self, parent, name, expected):
        descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
        if identity(os.fstat(descriptor)) != identity(expected):
            os.close(descriptor)
            raise ValueError('directory changed before traversal')
        return descriptor

    def locate(self, path):
        parts = PurePosixPath(path).parts[1:]
        descriptor = os.dup(self.root_fd)
        try:
            for name in parts[:-1]:
                metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                child = self.directory(descriptor, name, metadata)
                os.close(descriptor)
                descriptor = child
            return descriptor, parts[-1]
        except BaseException:
            os.close(descriptor)
            raise

    def inspect(self, path, category='candidate', recursive=False, discover=None):
        try:
            descriptor, name = self.locate(path)
            try:
                os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                try:
                    self.walk(descriptor, name, path, category, recursive, discover, 0, None)
                except FileNotFoundError:
                    raise ValueError('entry disappeared during inventory') from None
            finally:
                os.close(descriptor)
        except FileNotFoundError:
            self.rows.setdefault(path, dict(path=path, category=category, kind='missing'))
        except (OSError, ValueError) as error:
            self.block(path, 'inventory refused: ' + (str(error.errno) if isinstance(error, OSError) else str(error)), True)

    def walk(self, parent, name, path, category, recursive, discover, depth, device):
        self.budget()
        metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
        self.record(path, metadata, category)
        if recursive and (path in self.mounts or (device is not None and metadata.st_dev != device)):
            self.block(path, 'recursive mount boundary rejected', True)
            return
        if stat.S_ISLNK(metadata.st_mode):
            self.rows[path]['link_target'] = os.readlink(name, dir_fd=parent)
            self.block(path, 'recursive link rejected' if recursive else 'leaf link: separate exact-entry review; target not followed', recursive)
            return
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            self.block(path, 'hard-linked file requires preservation review', True)
            return
        if not stat.S_ISDIR(metadata.st_mode):
            if recursive and depth == 0:
                self.block(path, 'expected recursive directory root', True)
            if not stat.S_ISREG(metadata.st_mode):
                self.block(path, 'special file requires stopped-state review', recursive)
            return
        if not recursive and discover is None:
            if category == 'candidate':
                self.block(path, 'unexpected directory at exact leaf', True)
            return
        if depth >= self.max_depth:
            raise ValueError('depth limit exceeded')
        descriptor = self.directory(parent, name, metadata)
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    self.budget()
                    child_path = path + '/' + entry.name
                    if discover is not None:
                        discover(child_path, entry.name)
                    elif child_path == '/var/lib/sage-lantern/recovery':
                        self.inspect(child_path, 'preserve')
                        self.block(child_path, 'hardware recovery subtree preserved; no recursive inventory')
                    else:
                        self.walk(descriptor, entry.name, child_path, category, True, None,
                                  depth + 1, metadata.st_dev)
            if identity(os.fstat(descriptor)) != identity(metadata) or identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identity(metadata):
                raise ValueError('directory changed during inventory')
        finally:
            os.close(descriptor)

    def unknown(self, path, name):
        known = set(RECURSIVE_ROOTS + LEAF_PATHS + PRESERVE_PATHS)
        if path in known:
            return
        if str(PurePosixPath(path).parent) in ('/root', '/home/radxa'):
            self.inspect(path, 'preserve-review')
            self.block(path, 'home/root entry outside exact allowlist; preserve, staging classification requires review')
        elif any(token in name.lower() for token in ('lantern', 'meshcore', 'radio')):
            self.inspect(path, 'preserve-review')
            self.block(path, 'unclassified artifact; preserve pending exact-path review')

    def startup(self, path, name):
        if name in UNIT_NAMES:
            self.inspect(path)
        elif name.endswith('.d') and name[:-2] in UNIT_NAMES:
            self.inspect(path, recursive=True)
        elif name.endswith(('.wants', '.requires')):
            self.inspect(path, 'shared-startup-directory', discover=self.startup_link)
        else:
            self.unknown(path, name)

    def startup_link(self, path, name):
        if name in UNIT_NAMES:
            self.inspect(path)
        else:
            self.unknown(path, name)


def preview(root='/', *, mount_reader=read_mountinfo, max_entries=200000, max_depth=64):
    """root/mount_reader are fixture seams, not CLI target or proof overrides."""
    result = dict(schema=1, mode='preview-only', scope='unit-a', metadata_only=True,
                  reset_authorized=False, ready=False, execute_supported=False,
                  inventory_complete=False, observed_root=os.fspath(root), paths=[], blockers=[],
                  preservation_policy=['Preserve OS, network, SSH, service accounts, unrelated homes/services and firmware.',
                      'No process, unit, release, target identity or recovery-copy verification performed.',
                      'Missing paths do not prove prior contents were safely preserved.',
                      'Metadata is a bounded observation, not an atomic deletion plan.'])
    inventory = None
    try:
        if max_entries < 1 or not 1 <= max_depth <= 128:
            raise ValueError('invalid inventory limits')
        mount_before = mount_reader()
        mounts = parse_mountinfo(mount_before)
        descriptor = os.open(root, DIRECTORY_FLAGS)
        try:
            inventory = Inventory(descriptor, mounts, max_entries, max_depth)
            inventory.block('/var/lib/sage-lantern', 'radio recovery preservation outside disposable root requires separate verified reconciliation')
            inventory.block('/run', 'stop/drain/fence reconciliation and no remaining processes not verified')
            for path in RECURSIVE_ROOTS:
                inventory.inspect(path, recursive=True)
            for path in LEAF_PATHS:
                inventory.inspect(path)
            for path in PRESERVE_PATHS:
                inventory.inspect(path, 'preserve')
            for path in DISCOVERY_DIRS:
                inventory.inspect(path, 'shared-directory', discover=inventory.unknown)
            for path in UNIT_DIRS:
                inventory.inspect(path, 'shared-startup-directory', discover=inventory.startup)
            if mount_reader() != mount_before:
                inventory.block('/', 'mount metadata changed during inventory', True)
            result['inventory_complete'] = inventory.complete
        finally:
            os.close(descriptor)
    except (OSError, ValueError) as error:
        result['blockers'].append(dict(path='/', reason='inventory unavailable: ' +
            (str(error.errno) if isinstance(error, OSError) else str(error))))
    if inventory is not None:
        result['paths'] = [inventory.rows[path] for path in sorted(inventory.rows)]
        result['blockers'].extend(sorted(inventory.blockers, key=lambda item: (item['path'], item['reason'])))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', required=True, action='store_true')
    parser.parse_args(argv)
    result = preview()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['inventory_complete'] else 2


if __name__ == '__main__':
    sys.exit(main())
