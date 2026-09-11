import hashlib
import importlib.util
import lzma
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('nvme_writer', Path(__file__).resolve().parents[1] / 'lantern/nvme-image-write.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('serial,boot,power', [('A-serial', True, True), ('B-serial', False, True), ('B-serial', True, False), (None, True, True)])
def test_explicit_authorization_required(serial, boot, power):
    with pytest.raises(ValueError):
        module.authorize(SimpleNamespace(confirm_erase=serial, boot_reviewed=boot, power_and_suspend_ready=power), {'target_serial': 'B-serial'})


def test_exact_authorization():
    module.authorize(SimpleNamespace(confirm_erase='B-serial', boot_reviewed=True, power_and_suspend_ready=True), {'target_serial': 'B-serial'})


def test_stream_write_short_writes_and_exact_readback(tmp_path, monkeypatch):
    payload = b'os-image' * 65536
    image = tmp_path / 'image.xz'
    image.write_bytes(lzma.compress(payload))
    target = tmp_path / 'regular-test-file'
    target.write_bytes(b'?' * (len(payload) + 512))
    expected = dict(image_bytes=len(payload), image_sha256=hashlib.sha256(payload).hexdigest())
    image_fd = os.open(image, os.O_RDONLY)
    target_fd = os.open(target, os.O_RDWR)
    original_write = os.write
    monkeypatch.setattr(module.os, 'write', lambda descriptor, raw: original_write(descriptor, raw[:8192]))
    try:
        module.write_image(image_fd, target_fd, expected)
        assert module.readback(target_fd, expected) == expected['image_sha256']
        assert target.read_bytes() == payload + b'?' * 512
        os.pwrite(target_fd, b'bad', 0)
        with pytest.raises(ValueError, match='readback_mismatch'):
            module.readback(target_fd, expected)
    finally:
        os.close(image_fd)
        os.close(target_fd)


def test_oversized_image_refused_before_chunk_write(tmp_path):
    image = tmp_path / 'image.xz'
    image.write_bytes(lzma.compress(b'x' * 1024))
    target = tmp_path / 'regular-test-file'
    target.write_bytes(b'original')
    with image.open('rb') as source, target.open('r+b') as destination:
        with pytest.raises(ValueError, match='image_grew'):
            module.write_image(source.fileno(), destination.fileno(), {'image_bytes': 512})
    assert target.read_bytes() == b'original'


def test_no_firmware_or_service_calls():
    source = Path(module.__file__).read_text()
    assert 'rsetup' not in source and 'systemctl' not in source and '/dev/mtd' not in source
    assert 'os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW' in source


def test_failed_image_check_and_failed_journal_preserve_original(tmp_path):
    image = tmp_path / 'image.xz'
    image.write_bytes(b'invalid')
    original = ValueError('image verification failed')
    def verify(*args):
        raise original
    def append(*args):
        raise OSError('disk full')
    helper = SimpleNamespace(verify_image=verify)
    with pytest.raises(ValueError) as caught:
        module.apply(helper, image, {}, {}, SimpleNamespace(append=append))
    assert caught.value is original


def test_default_cli_does_not_create_write_journal(tmp_path, monkeypatch):
    import sys
    raw = b'{}'
    helper = SimpleNamespace(decode=lambda raw: {}, validate_expected=lambda value: None,
        snapshot=lambda expected: {}, validate_snapshot=lambda *args: None,
        verify_image=lambda *args: {'verified': True})
    monkeypatch.setattr(sys, 'argv', ['writer', '--preflight-helper', '/helper', '--preflight-sha256', 'helperhash',
        '--expected', '/expected', '--expected-sha256', hashlib.sha256(raw).hexdigest(), '--image', str(tmp_path / 'image')])
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module, 'load_helper', lambda *args: helper)
    monkeypatch.setattr(module, 'trusted', lambda path: raw)
    monkeypatch.setattr(module, 'Journal', lambda *args: pytest.fail('unexpected write intent'))
    monkeypatch.setattr(module, 'apply', lambda *args: pytest.fail('unexpected device write'))
    module.main()
