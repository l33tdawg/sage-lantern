import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('static_helpers', ROOT / 'lantern/install-fresh-startup-helpers.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    payload = tmp_path / 'payload'
    payload.mkdir()
    etc = tmp_path / 'etc'
    etc.mkdir()
    monkeypatch.setattr(helper, 'ETC', etc)
    monkeypatch.setattr(helper, 'DEST', tmp_path / 'libexec')
    monkeypatch.setattr(helper, 'STATE', tmp_path / 'journal')
    monkeypatch.setattr(helper, 'trusted', lambda path: Path(path).read_bytes())
    monkeypatch.setattr(helper, 'manifest_bytes', lambda path: Path(path).read_bytes())
    monkeypatch.setattr(helper, 'safe_directory', lambda path: None)
    calls = []
    monkeypatch.setattr(helper, 'inactive', lambda: calls.append('inactive'))
    real_load = helper.load
    monkeypatch.setattr(helper, 'load', lambda name: SimpleNamespace(verify_release=lambda args: payload)
        if name == 'provision-fresh-appliance' else real_load(name))
    def put(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(helper.encode(value))
    rows, entries = [], []
    for name in helper.NAMES:
        source = 'scripts/lantern/' + name
        put(payload / source, {'source': name})
        entries.append(dict(path=source, sha256=helper.digest((payload / source).read_bytes())))
        rows.append(dict(path=source, destination=str(helper.DEST / name),
            operation='reviewed_copy', destination_mode=0o644))
    put(payload / 'release/destinations.json', dict(files=rows))
    entries.append(dict(path='release/destinations.json',
        sha256=helper.digest((payload / 'release/destinations.json').read_bytes())))
    put(payload.parent / 'manifest.json', dict(files=entries, release_id='reviewed'))
    put(payload.parent / 'selection.json', {'release_id': 'reviewed'})
    (etc / 'unit.env').write_text('UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n')
    put(etc / 'appliance-installed.json', dict(schema='sage.lantern.fresh-app-install.v1',
        unit_id='unit-b', release_id='reviewed', installed=True, lock_required=True))
    put(etc / 'appliance-bindings.json', dict(schema='sage.lantern.unit-plan.v1', unit_id='unit-b'))
    args = SimpleNamespace(unit_id='unit-b', manifest_sha256=helper.digest((payload.parent / 'manifest.json').read_bytes()),
        attempt_id='11111111-1111-4111-8111-111111111111')
    return args, payload, calls, put


def test_readonly_then_apply(fixture):
    args, payload, calls, put = fixture
    plan = helper.inspect(args)[0]
    assert not helper.DEST.exists() and not helper.STATE.exists()
    result = helper.apply(args, plan)
    assert result['installed'] and not result['services_started'] and not result['services_enabled']
    assert len(list(helper.DEST.iterdir())) == 5
    assert len(calls) >= 16
    with pytest.raises(ValueError):
        helper.apply(args, plan)


@pytest.mark.parametrize('field,value', [('installed', False), ('unit_id', 'unit-a'), ('release_id', 'other')])
def test_receipt_refused(fixture, field, value):
    args, payload, calls, put = fixture
    path = helper.ETC / 'appliance-installed.json'
    receipt = json.loads(path.read_bytes())
    receipt[field] = value
    put(path, receipt)
    with pytest.raises(ValueError):
        helper.inspect(args)
    assert not helper.STATE.exists()


def test_app_absent(fixture):
    args, *_ = fixture
    (helper.ETC / 'appliance-installed.json').unlink()
    with pytest.raises(FileNotFoundError):
        helper.inspect(args)


def test_actual_sized_release_metadata_keeps_small_input_limit(fixture, monkeypatch):
    args, payload, calls, put = fixture
    destinations = payload / 'release/destinations.json'
    selection = payload.parent / 'selection.json'
    for path, size in ((destinations, 10004530), (selection, 4150137)):
        raw = path.read_bytes()
        path.write_bytes(raw + b' ' * (size - len(raw)))
    manifest_path = payload.parent / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'][-1]['sha256'] = helper.digest(destinations.read_bytes())
    put(manifest_path, manifest)
    args.manifest_sha256 = helper.digest(manifest_path.read_bytes())
    def small(path):
        raw = Path(path).read_bytes()
        assert len(raw) <= 2097152
        return raw
    spec = importlib.util.spec_from_file_location('sized_verifier', ROOT / 'appliance-release-manifest.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    reads = []
    def metadata(path):
        reads.append(path)
        return verifier.metadata_bytes(path)[0]
    monkeypatch.setattr(helper, 'trusted', small)
    monkeypatch.setattr(helper, 'manifest_bytes', metadata)
    plan, _ = helper.inspect(args)
    assert destinations in reads and selection in reads
    assert plan['selection_sha256'] == helper.digest(selection.read_bytes())
    oversized = payload.parent / 'oversized.json'
    with oversized.open('wb') as stream:
        stream.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(ValueError):
        metadata(oversized)


def test_foreign_binding(fixture):
    args, payload, calls, put = fixture
    put(helper.ETC / 'appliance-bindings.json', dict(schema='sage.lantern.unit-plan.v1', unit_id='unit-a'))
    with pytest.raises(ValueError):
        helper.inspect(args)


@pytest.mark.parametrize('field,value', [('destination', '/usr/bin/arbitrary'), ('destination_mode', 0o755),
                                       ('operation', 'copy')])
def test_manifest_destination_contract(fixture, field, value):
    args, payload, calls, put = fixture
    path = payload / 'release/destinations.json'
    rows = json.loads(path.read_bytes())
    rows['files'][0][field] = value
    put(path, rows)
    manifest = json.loads((payload.parent / 'manifest.json').read_bytes())
    manifest['files'][-1]['sha256'] = helper.digest(path.read_bytes())
    put(payload.parent / 'manifest.json', manifest)
    args.manifest_sha256 = helper.digest((payload.parent / 'manifest.json').read_bytes())
    with pytest.raises(ValueError):
        helper.inspect(args)


def test_partial_failure_retained(fixture, monkeypatch):
    args, *_ = fixture
    plan = helper.inspect(args)[0]
    original = helper.exclusive
    def write(path, raw, mode=0o600):
        if path.name == helper.NAMES[1]:
            raise OSError('injected')
        original(path, raw, mode)
    monkeypatch.setattr(helper, 'exclusive', write)
    with pytest.raises(OSError):
        helper.apply(args, plan)
    assert (helper.DEST / helper.NAMES[0]).exists()
    assert (helper.STATE / 'intent.json').exists()
    assert json.loads((helper.STATE / 'failed.json').read_bytes())['retry_authorized'] is False
    assert not (helper.STATE / 'complete.json').exists()


def test_service_changes_during_apply(fixture, monkeypatch):
    args, *_ = fixture
    plan = helper.inspect(args)[0]
    def check():
        if helper.DEST.exists() and list(helper.DEST.iterdir()):
            raise ValueError('changed job')
    monkeypatch.setattr(helper, 'inactive', check)
    with pytest.raises(ValueError):
        helper.apply(args, plan)
    assert (helper.STATE / 'failed.json').exists()
    assert not (helper.STATE / 'complete.json').exists()


def test_exclusive_nofollow(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, 'safe_directory', lambda path: None)
    victim = tmp_path / 'victim'
    victim.write_bytes(b'unchanged')
    link = tmp_path / 'link'
    link.symlink_to(victim)
    for target in (victim, link):
        with pytest.raises(FileExistsError):
            helper.exclusive(target, b'overwrite')
    assert victim.read_bytes() == b'unchanged'


def test_real_reader_rejects_symlink(tmp_path):
    with pytest.raises((OSError, ValueError)):
        path = tmp_path / 'link'
        path.symlink_to('/etc/passwd')
        helper.contract.trusted(path)


@pytest.mark.parametrize('change', ['job', 'group', 'second'])
def test_actual_service_fence(change):
    fields = dict(LoadState='not-found', ActiveState='inactive', MainPID='0', Job='',
        ControlGroup='', Slice='', NeedDaemonReload='no', FragmentPath='', DropInPaths='', Transient='no')
    calls = []
    def show(*args):
        calls.append(1)
        return dict(fields, Job='42') if change == 'job' or (change == 'second' and len(calls) == 2) else dict(fields)
    def empty(*args):
        if change == 'group':
            raise ValueError('populated')
    system = SimpleNamespace(_show=show, _no_job=lambda job: job == '', _cgroup=lambda path: path, _empty_tree=empty)
    with pytest.raises(ValueError):
        helper.contract.startup_inactive(system, installed=False)


def test_explicit_runner_and_mapping():
    runner = helper.load('provision-fresh-appliance')
    args = SimpleNamespace(unit_id='unit-a', phase='startup-helpers', app_options=[],
        allow_native_build=False, install_os_packages=False, apply_postcommission=False,
        manifest_sha256='b'*64)
    command = runner.commands(args, Path('/release/payload'))[0]
    assert '--apply' not in command and command[-1] == 'unit-a'
    args.apply_postcommission = True
    with pytest.raises(ValueError):
        runner.commands(args, Path('/release/payload'))
    stage = runner.helper('static_stage_test', 'scripts/stage-appliance-release.py')
    rows = {row['path']: row for row in stage.required_sources()}
    for name in helper.NAMES:
        row = rows['scripts/lantern/' + name]
        assert row['destination'] == '/opt/sage-lantern/libexec/' + name
        assert row['destination_mode'] == 0o644
