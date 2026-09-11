import importlib.util
import os
from pathlib import Path
import stat
import types

import pytest


SPEC = importlib.util.spec_from_file_location('maintenance_env', Path(__file__).resolve().parents[1] /
    'lantern/build-runtime-maintenance-env.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def expected():
    return dict(schema='sage.lantern.maintenance-environment.v1',
        attempt='2701621b-86ee-4302-8a90-98f70cff9046',
        python=dict(path='/usr/bin/python3.11', sha256='a' * 64),
        wheels=[dict(path='/staged/' + name, sha256='b' * 64) for name in
            ('sage_lantern_runtime-0.2.0-py3-none-any.whl', 'sage_agent_sdk-11.19.10-py3-none-any.whl')])


@pytest.mark.parametrize('fault', [None, 'duplicate_wheel', 'missing_sdk', 'foreign_python', 'existing_attempt', 'extra'])
def test_explicit_offline_inputs_no_retry(tmp_path, monkeypatch, fault):
    value = expected()
    monkeypatch.setattr(MODULE, 'ROOT', tmp_path)
    calls = []
    monkeypatch.setattr(MODULE, 'pinned', lambda pin: calls.append(pin))
    if fault == 'duplicate_wheel':
        value['wheels'].append(value['wheels'][0])
    elif fault == 'missing_sdk':
        value['wheels'][1]['path'] = '/staged/requests-1.0-py3-none-any.whl'
    elif fault == 'foreign_python':
        value['python']['path'] = '/tmp/python'
    elif fault == 'existing_attempt':
        (tmp_path / value['attempt']).mkdir()
    elif fault == 'extra':
        value['index_url'] = 'https://must-not-contact.invalid'
    if fault:
        with pytest.raises(ValueError):
            MODULE.inspect(value)
    else:
        assert MODULE.inspect(value) == tmp_path / value['attempt']
        assert calls == [value['python'], *value['wheels']]
        assert not (tmp_path / value['attempt']).exists()


@pytest.fixture
def root_fixture(tmp_path, monkeypatch):
    real_lstat = Path.lstat
    def lstat(path):
        info = real_lstat(path)
        return types.SimpleNamespace(**{field: getattr(info, field) for field in
            ('st_dev', 'st_ino', 'st_mode', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}, st_uid=0)
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(MODULE, 'trusted', lambda path: Path(path).read_bytes())
    root = tmp_path / 'venv'
    root.mkdir()
    (root / 'lib').mkdir()
    (root / 'lib64').symlink_to('lib')
    (root / 'lib/public.py').write_bytes(b'pass\n')
    (root / 'bin').mkdir()
    python = root / 'bin/python'
    python.write_bytes(b'fixture executable')
    python.chmod(0o755)
    yield root
    for path in root.rglob('*'):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o755)
    root.chmod(0o755)


def test_sealed_inventory_detects_changed_bytes(root_fixture):
    root = root_fixture
    manifest = MODULE.inventory(root, seal=True)
    assert MODULE.inventory(root) == manifest
    assert manifest['lib64'] == dict(kind='link', target='lib')
    assert manifest['bin/python']['mode'] == 0o555
    assert manifest['lib/public.py']['mode'] == 0o444
    path = root / 'lib/public.py'
    path.chmod(0o644)
    path.write_bytes(b'changed\n')
    path.chmod(0o444)
    assert MODULE.inventory(root) != manifest


@pytest.mark.parametrize('fault', ['outside_link', 'hardlink', 'writable_file'])
def test_unsealed_or_escaping_environment_refused(root_fixture, fault):
    root = root_fixture
    if fault == 'outside_link':
        (root / 'escape').symlink_to('/etc')
    elif fault == 'hardlink':
        os.link(root / 'lib/public.py', root / 'second.py')
    if fault == 'writable_file':
        MODULE.inventory(root, seal=True)
        (root / 'lib/public.py').chmod(0o644)
    with pytest.raises(ValueError):
        MODULE.inventory(root, seal=fault != 'writable_file')


def test_builder_has_no_dependency_resolution_or_installed_copy():
    source = Path(MODULE.__file__).read_text()
    assert "'--no-index'" in source and "'--no-deps'" in source
    assert 'copytree' not in source and 'systemctl' not in source
    assert '/opt/sage-lantern/venv' not in source
