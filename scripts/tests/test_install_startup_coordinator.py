import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software/runtime/src'))
SPEC = importlib.util.spec_from_file_location('install_startup', ROOT / 'scripts/lantern/install-startup-coordinator.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
COORDINATOR_SPEC = importlib.util.spec_from_file_location('standalone_startup', ROOT / 'scripts/lantern/startup-coordinator.py')
coordinator = importlib.util.module_from_spec(COORDINATOR_SPEC)
COORDINATOR_SPEC.loader.exec_module(coordinator)


@pytest.fixture(autouse=True)
def coordinator_loader(monkeypatch):
    monkeypatch.setattr(installer, 'startup', coordinator)
    monkeypatch.setattr(installer, 'load_coordinator', lambda digest: coordinator)


def test_default_is_read_only(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['installer', '--config', '/config', '--config-sha256', 'a' * 64,
        '--service', '/service', '--service-sha256', 'b' * 64, '--coordinator-sha256', 'c' * 64])
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.startup, 'pinned', lambda pin: pin['path'].encode())
    monkeypatch.setattr(installer, 'preflight', lambda *args: calls.append('preflight'))
    monkeypatch.setattr(installer, 'exclusive', lambda *args: pytest.fail('unexpected write'))
    monkeypatch.setattr(installer.subprocess, 'run', lambda *args, **kwargs: pytest.fail('unexpected service action'))
    installer.main()
    assert calls == ['preflight']


def test_apply_enables_only_coordinator_without_start(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['installer', '--config', '/config', '--config-sha256', 'a' * 64,
        '--service', '/service', '--service-sha256', 'b' * 64, '--coordinator-sha256', 'c' * 64, '--apply'])
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.startup, 'pinned', lambda pin: pin['path'].encode())
    monkeypatch.setattr(installer, 'preflight', lambda *args: calls.append('preflight'))
    monkeypatch.setattr(installer, 'exclusive', lambda path, raw: calls.append(path))
    monkeypatch.setattr(installer.subprocess, 'run', lambda command, **kwargs: calls.append(command))
    installer.main()
    assert calls == ['preflight', installer.startup.CONFIG, installer.SERVICE,
        ['/usr/bin/systemctl', 'daemon-reload'], ['/usr/bin/systemctl', 'enable', 'sage-lantern-startup.service']]


def test_failed_validation_never_installs(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['installer', '--config', '/config', '--config-sha256', 'a' * 64,
        '--service', '/service', '--service-sha256', 'b' * 64, '--coordinator-sha256', 'c' * 64, '--apply'])
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.startup, 'pinned', lambda pin: b'{}')
    monkeypatch.setattr(installer, 'exclusive', lambda *args: pytest.fail('unexpected write'))
    with pytest.raises(RuntimeError):
        installer.main()


def test_exclusive_never_overwrites(tmp_path):
    path = tmp_path / 'config'
    installer.exclusive(path, b'original')
    with pytest.raises(FileExistsError):
        installer.exclusive(path, b'replacement')
    assert path.read_bytes() == b'original'
