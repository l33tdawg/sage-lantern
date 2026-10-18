import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('prepare_cf_sd', Path(__file__).parents[1] / 'lantern/prepare-cf-sd.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FreshImageTests(unittest.TestCase):
    def test_boot_and_fstab_use_the_new_unique_filesystem_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'boot').mkdir(); (root / 'etc').mkdir()
            (root / 'boot/armbianEnv.txt').write_text('verbosity=1\nrootdev=UUID=' + mod.SOURCE_FS_UUID + '\n')
            (root / 'etc/fstab').write_text('UUID=' + mod.SOURCE_FS_UUID + ' / ext4 defaults 0 1\n')
            new = 'b4c41522-765f-42a2-b82d-fab6b8a60ec9'
            mod.set_boot_uuid(root, new)
            self.assertIn('rootdev=UUID=' + new, (root / 'boot/armbianEnv.txt').read_text())
            self.assertIn('UUID=' + new + ' /', (root / 'etc/fstab').read_text())
            self.assertNotIn(mod.SOURCE_FS_UUID, (root / 'etc/fstab').read_text())

    def test_static_configuration_requires_exact_nic_and_valid_subnet(self):
        mod.validate_options('d', 'sage', None, None, None, [])
        mod.validate_options('d', 'sage', '192.0.2.20/24', '192.0.2.1', '02:00:00:00:00:01', ['1.1.1.1'])
        for args in [('d', 'root', None, None, None, []),
                     ('d', 'sage', '192.0.2.20/24', '192.0.3.1', '02:00:00:00:00:01', []),
                     ('d', 'sage', '192.0.2.20/24', '192.0.2.1', None, [])]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                mod.validate_options(*args)

    def test_fresh_image_has_unique_identity_generation_and_ssh_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ('etc/default', 'etc/ssh', 'etc/systemd/system', 'var/lib/dbus', 'etc/netplan'):
                (root / folder).mkdir(parents=True)
            (root / 'etc/armbian-release').write_text('BOARD=orangepi5-plus\n')
            (root / 'etc/hosts').write_text('127.0.0.1 localhost\n127.0.1.1 orangepi5-plus\n')
            (root / 'etc/machine-id').write_text('never-clone-this')
            (root / 'etc/ssh/ssh_host_ed25519_key').write_text('never-clone-this')
            (root / 'etc/netplan/10-dhcp.yaml').write_text('original dhcp config')
            self.assertEqual(mod.customize(root, 'd', 'sage', '$6$test-salted-hash'), 'lantern-d')
            cfg = root / 'etc/lantern-firstboot.json'
            self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(cfg.read_text())['hostname'], 'lantern-d')
            self.assertEqual((root / 'etc/machine-id').read_text(), '')
            self.assertFalse((root / 'etc/ssh/ssh_host_ed25519_key').exists())
            self.assertIn('Requires=lantern-firstboot.service', (root / 'etc/systemd/system/ssh.service.d/lantern-firstboot.conf').read_text())
            self.assertTrue((root / 'etc/systemd/system/multi-user.target.wants/lantern-firstboot.service').is_symlink())
            self.assertEqual((root / 'etc/netplan/10-dhcp.yaml').read_text(), 'original dhcp config')

    def test_rejects_commissioned_or_wrong_board_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'etc').mkdir()
            (root / 'etc/armbian-release').write_text('BOARD=rock-5b-plus\n')
            with self.assertRaises(ValueError):
                mod.customize(root, 'd', 'sage', 'hash')
            (root / 'etc/armbian-release').write_text('BOARD=orangepi5-plus\n')
            (root / 'var/lib/sage-lantern').mkdir(parents=True)
            with self.assertRaises(ValueError):
                mod.customize(root, 'd', 'sage', 'hash')

if __name__ == '__main__':
    unittest.main()
