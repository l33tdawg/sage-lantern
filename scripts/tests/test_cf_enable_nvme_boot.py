import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cf_spi', Path(__file__).parents[1] / 'lantern/cf-enable-nvme-boot.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FstabTests(unittest.TestCase):
    def test_remove_only_expected_sd_dependencies(self):
        source = 'UUID=nvme / ext4 defaults 0 1\nUUID=sd /media/boot-media ext4 defaults,nofail 0 2\n/media/boot-media/boot /boot none bind,nofail 0 0\ntmpfs /tmp tmpfs defaults 0 0\n'
        self.assertEqual(mod.local_fstab(source,'nvme'), 'UUID=nvme / ext4 defaults 0 1\ntmpfs /tmp tmpfs defaults 0 0\n')

    def test_refuse_unexpected_root_or_boot(self):
        for text in ('UUID=wrong / ext4 defaults 0 1\n',
                     'UUID=nvme / ext4 defaults 0 1\nUUID=other /boot ext4 defaults 0 2\n',
                     'tmpfs /tmp tmpfs defaults 0 0\n'):
            with self.assertRaises(ValueError):
                mod.local_fstab(text,'nvme')

if __name__ == '__main__':
    unittest.main()
