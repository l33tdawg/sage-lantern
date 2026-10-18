import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cf_nvme', Path(__file__).parents[1] / 'lantern/cf-nvme-setup.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class StorageGuards(unittest.TestCase):
    def setUp(self):
        self.info = dict(compatible=['rockchip,rk3588-orangepi-5-plus'], root='/dev/mmcblk1p1',
                        disk=dict(path='/dev/nvme0n1', type='disk', size=256060514304, serial='test-device',
                                  ro=False, fstype=None, pttype=None, mountpoints=[]),
                        signatures=[], holders=[], swaps=[], installer_matches=True)

    def test_blank_matching_device(self):
        mod.validate(self.info, 'test-device', 256060514304)

    def test_refuses_wrong_identity(self):
        with self.assertRaises(ValueError):
            mod.validate(self.info, 'another-unit', 256060514304)

    def test_refuses_reformat_and_in_use_devices(self):
        changes = [dict(children=[{}]), dict(fstype='ext4'), dict(pttype='gpt'),
                   dict(mountpoints=['/']), dict(ro=True)]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                data = copy.deepcopy(self.info)
                data['disk'].update(change)
                mod.validate(data)

    def test_refuses_other_boards_current_nvme_and_unreviewed_installer(self):
        for change in [dict(compatible=['radxa,rock-5b-plus']), dict(root='/dev/nvme0n1p1'),
                       dict(installer_matches=False), dict(signatures=[{}]), dict(holders=['dm-0']),
                       dict(swaps=['/dev/nvme0n1'])]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                mod.validate(dict(self.info, **change))

if __name__ == '__main__':
    unittest.main()
