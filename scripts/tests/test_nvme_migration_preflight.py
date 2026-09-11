import copy
import hashlib
import importlib.util
import lzma
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('nvme_preflight', Path(__file__).resolve().parents[1] / 'lantern/nvme-migration-preflight.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def expected():
    return dict(schema='sage.lantern.nvme-preflight.v1', board_compatible='radxa,rock-5b-plus',
        sd_root='/dev/mmcblk1p3', sd_serial='0x128338a1', target='/dev/nvme0n1',
        target_model='Netac NVMe SSD 512GB', target_serial='AA20260312512G069617',
        target_bytes=512110190592, image_bytes=512, image_sha512='a' * 128, image_sha256='b' * 64)


def snapshot():
    return dict(compatible=['radxa,rock-5b-plus'], root='/dev/mmcblk1p3', sd_serial='0x128338a1',
        lsblk={'blockdevices': [dict(path='/dev/nvme0n1', type='disk', size=512110190592,
            model='Netac NVMe SSD 512GB', serial='AA20260312512G069617', ro=False, fstype=None,
            pttype=None, mountpoints=[None], **{'maj:min': '259:0'})]},
        holders=[], signatures=[], swap_devices=[], device_number='259:0')


def test_b_identity_passes_without_a_serial():
    module.validate_snapshot(expected(), snapshot())


@pytest.mark.parametrize('key,value', [('serial', 'AA20260312512G069623'), ('size', 1),
    ('model', 'different'), ('type', 'part'), ('path', '/dev/nvme1n1'), ('ro', True),
    ('ro', 0), ('children', [{}]), ('mountpoints', ['/mnt']), ('fstype', 'ext4'), ('pttype', 'gpt')])
def test_target_mismatch_or_used_refused(key, value):
    value_snapshot = snapshot()
    value_snapshot['lsblk']['blockdevices'][0][key] = value
    with pytest.raises(ValueError):
        module.validate_snapshot(expected(), value_snapshot)


@pytest.mark.parametrize('key,value', [('root', '/dev/nvme0n1p3'), ('sd_serial', 'other'),
    ('compatible', ['radxa,rock-5b']), ('holders', ['dm-0']), ('signatures', [{'type': 'gpt'}]),
    ('swap_devices', ['/dev/nvme0n1']), ('device_number', '259:1')])
def test_source_board_and_usage_refused(key, value):
    value_snapshot = snapshot()
    value_snapshot[key] = value
    with pytest.raises(ValueError):
        module.validate_snapshot(expected(), value_snapshot)


def test_image_exact_size_hash_and_read_only(tmp_path):
    payload = b'x' * 512
    compressed = lzma.compress(payload)
    path = tmp_path / 'os.img.xz'
    path.write_bytes(compressed)
    pins = expected()
    pins.update(image_sha512=hashlib.sha512(compressed).hexdigest(), image_sha256=hashlib.sha256(payload).hexdigest())
    assert module.verify_image(path, pins)['bytes'] == 512
    assert path.read_bytes() == compressed
    pins['image_bytes'] = 256
    with pytest.raises(ValueError, match='too_large'):
        module.verify_image(path, pins)


def test_duplicate_pins_rejected():
    with pytest.raises(ValueError, match='duplicate'):
        module.decode('{"target":"one","target":"two"}')


def test_no_apply_or_firmware_path():
    source = Path(module.__file__).read_text()
    assert "add_argument('--apply'" not in source
    assert 'rsetup' not in source and "'dd'" not in source
    assert "'--no-act'" in source and 'os.O_WRONLY' not in source
