import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('fresh_reset', ROOT / 'scripts/lantern/plan-fresh-reset.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MOUNTS = '1 0 8:1 / / rw - ext4 /dev/root rw\n'


class FreshResetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def directory(self, path):
        destination = self.root / path.lstrip('/')
        destination.mkdir(parents=True, exist_ok=True)
        return destination

    def file(self, path, content=b'PRIVATE CONTENT MUST NOT BE READ'):
        destination = self.root / path.lstrip('/')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return destination

    def preview(self, **kwargs):
        return MODULE.preview(self.root, mount_reader=lambda: MOUNTS, **kwargs)

    def rows(self, result):
        return {item['path']: item for item in result['paths']}

    def reasons(self, result):
        return '\n'.join(item['reason'] for item in result['blockers'])

    def test_absence_is_not_authority_or_recovery_proof(self):
        result = self.preview()
        self.assertTrue(result['inventory_complete'])
        for field in ('ready', 'reset_authorized', 'execute_supported'):
            self.assertFalse(result[field])
        self.assertEqual(self.rows(result)[MODULE.RECOVERY]['kind'], 'missing')
        self.assertIn('radio recovery preservation', self.reasons(result))

    def test_regular_secret_files_are_stat_only(self):
        path = '/etc/sage-lantern/identity.key'
        secret = self.file(path)
        secret.chmod(0o600)
        original = os.open
        def directory_open(name, flags, *args, **kwargs):
            self.assertTrue(flags & os.O_DIRECTORY)
            self.assertTrue(flags & os.O_NOFOLLOW)
            return original(name, flags, *args, **kwargs)
        with patch.object(MODULE.os, 'open', side_effect=directory_open), \
             patch('builtins.open', side_effect=AssertionError('content read')), \
             patch.object(MODULE.os, 'read', side_effect=AssertionError('content read')):
            result = self.preview()
        row = self.rows(result)[path]
        self.assertEqual(row['bytes'], secret.stat().st_size)
        self.assertEqual(row['mode'], '0o600')
        self.assertNotIn('PRIVATE CONTENT', json.dumps(result))
        self.assertNotIn('sha256', json.dumps(result))

    def test_recovery_subtree_preserved_not_recursed(self):
        self.file(MODULE.RECOVERY)
        self.file('/var/lib/sage-lantern/recovery/radio/other-secret')
        result = self.preview()
        rows = self.rows(result)
        self.assertEqual(rows[MODULE.RECOVERY]['category'], 'preserve')
        self.assertNotIn('/var/lib/sage-lantern/recovery/radio/other-secret', rows)
        self.assertIn('hardware recovery subtree preserved', self.reasons(result))

    def test_recursive_symlink_rejected_without_following(self):
        directory = self.directory('/opt/sage-lantern')
        target = self.directory('/unrelated')
        self.file('/unrelated/secret')
        (directory / 'linked').symlink_to(target, target_is_directory=True)
        result = self.preview()
        self.assertFalse(result['inventory_complete'])
        self.assertIn('recursive link rejected', self.reasons(result))
        self.assertNotIn('/opt/sage-lantern/linked/secret', self.rows(result))

    def test_recursive_root_symlink_rejected(self):
        self.directory('/opt')
        (self.root / 'opt/sage-lantern').symlink_to('/etc')
        self.assertIn('recursive link rejected', self.reasons(self.preview()))

    def test_ancestor_symlink_refused(self):
        target = self.directory('/unrelated')
        (self.root / 'opt').symlink_to(target, target_is_directory=True)
        result = self.preview()
        self.assertFalse(result['inventory_complete'])
        self.assertNotIn('/opt/sage-lantern', self.rows(result))

    def test_same_device_bind_mount_blocks_before_descent(self):
        self.file('/opt/sage-lantern/mounted/secret')
        mounts = MOUNTS + '2 1 8:1 /other /opt/sage-lantern/mounted rw - ext4 /dev/root rw\n'
        result = MODULE.preview(self.root, mount_reader=lambda: mounts)
        self.assertFalse(result['inventory_complete'])
        self.assertIn('recursive mount boundary rejected', self.reasons(result))
        self.assertNotIn('/opt/sage-lantern/mounted/secret', self.rows(result))

    def test_mount_at_recursive_root_blocks(self):
        self.file('/etc/sage-lantern/secret')
        mounts = MOUNTS + '2 1 8:1 /other /etc/sage-lantern rw - ext4 /dev/root rw\n'
        result = MODULE.preview(self.root, mount_reader=lambda: mounts)
        self.assertNotIn('/etc/sage-lantern/secret', self.rows(result))

    def test_mount_metadata_missing_malformed_or_changed(self):
        for raw in ('', 'garbage', '2 1 8:1 / /other rw - ext4 /dev/root rw\n'):
            with self.subTest(raw=raw):
                result = MODULE.preview(self.root, mount_reader=lambda: raw)
                self.assertFalse(result['inventory_complete'])
                self.assertEqual(result['paths'], [])
        reader = iter((MOUNTS, MOUNTS + '2 1 8:1 /other /opt rw - ext4 /dev/root rw\n'))
        result = MODULE.preview(self.root, mount_reader=lambda: next(reader))
        self.assertIn('mount metadata changed', self.reasons(result))
        self.assertFalse(result['inventory_complete'])

    def test_hardlinks_and_special_files_are_blockers(self):
        source = self.file('/etc/sage-lantern/identity')
        os.link(source, self.root / 'identity-alias')
        os.mkfifo(source.parent / 'fifo')
        result = self.preview()
        self.assertIn('hard-linked file', self.reasons(result))
        self.assertIn('special file', self.reasons(result))
        self.assertFalse(result['inventory_complete'])

    def test_exact_units_dropins_and_startup_links_only(self):
        unit = '/etc/systemd/system/sage-lantern-voice-loop.service'
        self.file(unit)
        dropin = unit + '.d/90-sage-lantern-sleep.conf'
        self.file(dropin)
        wants = self.directory('/etc/systemd/system/multi-user.target.wants')
        (wants / 'sage-lantern-voice-loop.service').symlink_to('../sage-lantern-voice-loop.service')
        (wants / 'ssh.service').symlink_to('/usr/lib/systemd/system/ssh.service')
        self.file('/etc/systemd/system/sage-lantern-unknown.service')
        rows = self.rows(self.preview())
        self.assertEqual(rows[unit]['category'], 'candidate')
        self.assertEqual(rows[dropin]['category'], 'candidate')
        self.assertEqual(rows[str('/etc/systemd/system/multi-user.target.wants/sage-lantern-voice-loop.service')]['kind'], 'symlink')
        self.assertEqual(rows['/etc/systemd/system/multi-user.target.wants/sage-lantern-voice-loop.service']['link_target'], '../sage-lantern-voice-loop.service')
        self.assertNotIn('/etc/systemd/system/multi-user.target.wants/ssh.service', rows)
        self.assertEqual(rows['/etc/systemd/system/sage-lantern-unknown.service']['category'], 'preserve-review')

    def test_home_unknown_staging_and_shared_os_preserved(self):
        self.file('/home/radxa/lantern-old.tar.gz')
        self.file('/root/sage-lantern-deployment-old/key')
        self.file('/root/deployment-unnamed/key')
        self.file('/home/radxa/unrelated-project/secret')
        self.file('/home/radxa/.config/sage-lantern/unit-b/chromium/secret')
        self.file('/etc/NetworkManager/system-connections/wifi')
        self.file('/home/radxa/.config/sage-lantern/unit-a/chromium/Cookies')
        result = self.preview()
        rows = self.rows(result)
        self.assertEqual(rows['/home/radxa/lantern-old.tar.gz']['category'], 'preserve-review')
        self.assertNotIn('/root/sage-lantern-deployment-old/key', rows)
        self.assertEqual(rows['/root/deployment-unnamed']['category'], 'preserve-review')
        self.assertNotIn('/root/deployment-unnamed/key', rows)
        self.assertNotIn('/home/radxa/unrelated-project/secret', rows)
        self.assertNotIn('/home/radxa/.config/sage-lantern/unit-b/chromium/secret', rows)
        self.assertNotIn('/etc/NetworkManager/system-connections/wifi', rows)
        self.assertEqual(rows['/etc/NetworkManager']['category'], 'preserve')
        self.assertEqual(rows['/home/radxa/.config/sage-lantern/unit-a/chromium/Cookies']['category'], 'candidate')

    def test_limits_fail_closed(self):
        self.file('/opt/sage-lantern/deep/nested/secret')
        for limits in (dict(max_entries=2), dict(max_depth=1)):
            with self.subTest(limits=limits):
                result = self.preview(**limits)
                self.assertFalse(result['inventory_complete'])
                self.assertIn('limit exceeded', self.reasons(result))

    def test_directory_race_rejected(self):
        self.directory('/opt/sage-lantern')
        original = MODULE.Inventory.directory
        def changed(inventory, parent, name, expected):
            if name == 'sage-lantern':
                raise ValueError('directory changed before traversal')
            return original(inventory, parent, name, expected)
        with patch.object(MODULE.Inventory, 'directory', changed):
            result = self.preview()
        self.assertFalse(result['inventory_complete'])
        self.assertIn('directory changed', self.reasons(result))

    def test_permission_denial_is_not_absence(self):
        self.directory('/etc/sage-lantern')
        original = MODULE.Inventory.directory
        def denied(inventory, parent, name, expected):
            if name == 'sage-lantern':
                raise PermissionError(13, 'denied')
            return original(inventory, parent, name, expected)
        with patch.object(MODULE.Inventory, 'directory', denied):
            result = self.preview()
        self.assertFalse(result['inventory_complete'])
        self.assertIn('inventory refused: 13', self.reasons(result))

    def test_shared_config_exact_metadata_preserved(self):
        config = '/var/lib/AccountsService/users/radxa'
        self.file(config)
        self.file('/etc/gdm3/daemon.conf')
        rows = self.rows(self.preview())
        self.assertEqual(rows[config]['category'], 'preserve')
        self.assertEqual(rows['/etc/gdm3/daemon.conf']['category'], 'preserve')

    def test_cli_has_no_execution_or_target_override(self):
        for args in ([], ['--execute'], ['--preview', '--root', '/tmp'], ['--preview', '--force']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    MODULE.main(args)
        with patch.object(MODULE, 'preview', return_value=self.preview()), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(MODULE.main(['--preview']), 0)
        self.assertFalse(json.loads(output.getvalue())['ready'])


if __name__ == '__main__':
    unittest.main()
