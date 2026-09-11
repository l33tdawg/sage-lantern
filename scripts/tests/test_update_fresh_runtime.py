import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest


SPEC = importlib.util.spec_from_file_location('cold_runtime',
    Path(__file__).resolve().parents[1] / 'lantern/update-fresh-runtime.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def wheel(extra=None):
    files = {'sage_lantern/__init__.py': b'__version__ = "0.2.0"\n',
        'sage_lantern/release-manifest.json': b'{}',
        MODULE.DIST + 'METADATA': b'Name: sage-lantern-runtime\nVersion: 0.2.0\n',
        MODULE.DIST + 'WHEEL': b'Wheel-Version: 1.0\n', MODULE.DIST + 'RECORD': b'record\n',
        MODULE.DIST + 'entry_points.txt': b'[console_scripts]\n'}
    files.update(extra or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


@pytest.mark.parametrize('fault', [None, 'inhibit', 'stop', 'install', 'verify', 'current'])
def test_transition_orders_containment_before_install_and_never_recovers_automatically(fault):
    events = []
    def action(name):
        def run():
            events.append(name)
            if fault == name:
                raise ValueError
        return run
    backend = SimpleNamespace(**{name: action(name) for name in ('current', 'inhibit', 'stop', 'install', 'verify')})
    if fault:
        with pytest.raises(ValueError):
            MODULE.transition(backend, events.append)
        assert 'verify_runtime_done' not in events
    else:
        result = MODULE.transition(backend, events.append)
        assert events.index('inhibit') < events.index('stop') < events.index('install') < events.index('verify')
        assert result == dict(runtime_updated=True, services_inhibited=True, activation_authorized=False,
                              native_changed=False, helpers_installed=False, recovery='manual-only')
    assert not any(name in events for name in ('start', 'restart', 'rollback'))


@pytest.mark.parametrize('path', ['../outside.py', '/outside.py', 'other_package/module.py',
                                'sage_lantern/nested/module.py', 'sage_lantern_runtime-0.2.0.data/scripts/hook'])
def test_wheel_rejects_non_runtime_or_install_hook_paths(path):
    with pytest.raises(ValueError):
        MODULE.wheel_files(wheel({path: b'forbidden'}))


@pytest.mark.parametrize('fault', [None, 'changed', 'extra', 'missing', 'link'])
def test_installed_module_bytes_and_exact_package_tree(tmp_path, fault):
    raw = wheel()
    files = MODULE.wheel_files(raw)
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    path = tmp_path / 'sage_lantern/__init__.py'
    if fault == 'changed':
        path.write_bytes(b'changed')
    if fault == 'extra':
        (path.parent / 'overlay.py').write_bytes(b'extra')
    if fault == 'missing':
        path.unlink()
    if fault == 'link':
        path.unlink()
        path.symlink_to(tmp_path / (MODULE.DIST + 'METADATA'))
    def reader(path):
        if path.is_symlink():
            raise ValueError
        return path.read_bytes()
    if fault:
        with pytest.raises((ValueError, FileNotFoundError)):
            MODULE.verify_installed(raw, site=tmp_path, reader=reader)
    else:
        assert MODULE.verify_installed(raw, site=tmp_path, reader=reader) == 2


@pytest.mark.parametrize('fault', [None, 'not_stopped', 'not_guarded', 'wheel_changed', 'backup_changed', 'pip_failure', 'timeout'])
def test_install_is_single_offline_no_deps_with_sanitized_environment(monkeypatch, fault):
    backend = MODULE.Update.__new__(MODULE.Update)
    backend.guarded = fault != 'not_guarded'
    backend.stopped = set() if fault == 'not_stopped' else set(MODULE.CONSUMERS)
    backend.old, backend.new = b'old', b'new'
    backend.expected = {'new_wheel': {'path': '/reviewed/sage_lantern_runtime-0.2.0-py3-none-any.whl'}}
    backend.observer = SimpleNamespace(remaining=lambda: 10)
    monkeypatch.setattr(MODULE, 'pinned', lambda item: b'changed' if fault == 'wheel_changed' else b'new')
    monkeypatch.setattr(MODULE, 'read', lambda path: b'changed' if fault == 'backup_changed' else b'old')
    calls = []
    def run(argv, **options):
        calls.append(argv)
        assert argv[:6] == ['/opt/sage-lantern/venv/bin/python', '-I', '-B', '-m', 'pip', 'install']
        assert argv[6:-1] == ['--no-index', '--no-deps', '--no-cache-dir', '--no-compile', '--force-reinstall']
        assert options['env']['PIP_CONFIG_FILE'] == '/dev/null'
        assert set(options['env']) == {'PATH', 'HOME', 'LANG', 'PIP_CONFIG_FILE', 'PIP_DISABLE_PIP_VERSION_CHECK', 'PYTHONDONTWRITEBYTECODE'}
        assert options['timeout'] == 10
        if fault == 'timeout':
            raise TimeoutError
        return SimpleNamespace(returncode=1 if fault == 'pip_failure' else 0)
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    if fault:
        with pytest.raises((ValueError, TimeoutError)):
            backend.install()
    else:
        backend.install()
    assert len(calls) == (1 if fault in (None, 'pip_failure', 'timeout') else 0)


def test_status_is_a_required_consumer_and_no_sage_stop(monkeypatch):
    backend = MODULE.Update.__new__(MODULE.Update)
    backend.guarded = True
    backend.stopped = set()
    calls = []
    backend.observer = SimpleNamespace(system=SimpleNamespace(_command=lambda argv, deadline: calls.append(argv)), deadline=10)
    backend.current = lambda: calls.append('verified')
    backend.stop()
    assert calls == [['stop', 'sage-lantern-camera.service'], 'verified',
                     ['stop', 'sage-lantern-display-power.service'], 'verified',
                     ['stop', 'sage-lantern-status.service'], 'verified']


def test_backup_and_records_never_overwrite(tmp_path):
    path = tmp_path / MODULE.BACKUP
    MODULE.durable(path, b'old')
    with pytest.raises(FileExistsError):
        MODULE.durable(path, b'new')
    assert path.read_bytes() == b'old'


def test_r6_real_wheel_layout_is_accepted():
    path = Path('/private/tmp/lantern-fresh-artifacts-20260906-r6/software/bundle/python-wheels') / MODULE.BACKUP
    if not path.exists():
        pytest.skip('local reviewed R6 artifact unavailable')
    raw = path.read_bytes()
    assert MODULE.sha(raw) == MODULE.WHEEL_SHA
    assert 'sage_lantern/deployment_bringup.py' in MODULE.wheel_files(raw)


@pytest.mark.parametrize('fault', [None, 'active', 'job', 'cgroup', 'occupied', 'override', 'changed', 'native'])
def test_consumers_fail_closed_before_mutation(monkeypatch, fault):
    backend = MODULE.Update.__new__(MODULE.Update)
    backend.guarded = False
    backend.stopped = set()
    backend.expected = {'consumers': dict.fromkeys(MODULE.CONSUMERS),
                        'files': {'/units/' + name: MODULE.sha(b'unit') for name in MODULE.CONSUMERS}}
    monkeypatch.setattr(MODULE, 'read', lambda path: b'unit')
    calls = []
    def native():
        calls.append('native')
        if fault == 'native':
            raise ValueError
    backend.native = native
    def show(name, deadline, fields):
        calls.append('show')
        value = dict(LoadState='loaded', ActiveState='inactive', MainPID='0', Job='',
            ControlGroup='', Slice='system.slice', KillMode='control-group', FragmentPath='/units/' + name,
            DropInPaths='', Restart='on-failure', NeedDaemonReload='no')
        if fault == 'active':
            value['ActiveState'] = 'active'
        if fault == 'job':
            value['Job'] = '1'
        if fault == 'cgroup':
            value['ControlGroup'] = '/elsewhere'
        if fault == 'override':
            value['DropInPaths'] = '/unreviewed.conf'
        if fault == 'changed' and calls.count('show') == 2:
            value['MainPID'] = '42'
        return value
    def empty(path, deadline):
        assert path.startswith('/system.slice/')
        calls.append('empty')
        if fault == 'occupied':
            raise ValueError
    backend.preflight = SimpleNamespace(FIELDS=())
    backend.observer = SimpleNamespace(deadline=10, remaining=lambda: 5,
        system=SimpleNamespace(_show=show, _empty_tree=empty, _cgroup=lambda path: path))
    if fault:
        with pytest.raises(ValueError):
            backend.current()
    else:
        backend.current()
        assert calls.count('empty') == 3 and calls.count('show') == 6
    assert calls[0] == 'native'
