import importlib.util
import ast
import os
from pathlib import Path
import tarfile
from types import SimpleNamespace
import types

import pytest


SPEC = importlib.util.spec_from_file_location('b_guard_handoff',
    Path(__file__).resolve().parents[1] / 'lantern/handoff-b-policy-guards.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def coordinator():
    path = Path(__file__).resolve().parents[1] / 'lantern/deploy-b-private-policy.py'
    require_sha = '33a8963dbd7d1fdf9d0bfc11593f66d86034f2e8cf4b2fb854a2320b0280bb9e'
    archive = os.environ.get('LANTERN_B_POLICY_REVIEW_ARCHIVE')
    if archive:
        with tarfile.open(archive) as source:
            matches = [member for member in source.getmembers() if member.isfile()
                and Path(member.name).name == path.name]
            assert len(matches) == 1
            raw = source.extractfile(matches[0]).read()
    else:
        raw = path.read_bytes()
    assert module.POLICY_SHA == require_sha == module.sha(raw)
    result = types.ModuleType('b_v5_coordinator')
    result.__file__ = str(path)
    result.reviewed_raw = raw
    exec(compile(raw, str(path), 'exec'), result.__dict__)
    return result


def test_v5_backend_selects_scoped_system_without_runtime_patch():
    current = coordinator()
    tree = ast.parse(current.reviewed_raw)
    backend = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Backend')
    assignments = [node for node in ast.walk(backend) if isinstance(node, ast.Assign)
        and any(ast.unparse(target) == 'self.system' for target in node.targets)]
    assert len(assignments) == 1
    assert ast.unparse(assignments[0].value) == 'b_suspend_system(self.preflight.SuspendSystem)'


@pytest.mark.parametrize('fault', [None, 'job', 'masked_service'])
def test_handoff_containment_calls_actual_v5_mask_gate(tmp_path, monkeypatch, fault):
    current = coordinator()
    masks = []
    monkeypatch.setattr(current, 'runtime_mask_snapshot', lambda unit, paths: masks.append(unit) or ('stable',))
    class Base:
        def _command(self, argv, deadline):
            assert argv == ['show', '--no-pager', '--property=UnitPath']
            return 'UnitPath=/etc/systemd/system /run/systemd/system /run/systemd/transient\n'

        def _show(self, unit, deadline, fields):
            masked = unit in current.MASKED_SLEEP_TARGETS or fault == 'masked_service'
            return dict(LoadState='masked' if masked else 'loaded', ActiveState='inactive',
                Job='1' if fault == 'job' else '', NeedDaemonReload='no', Transient='no',
                UnitFileState='masked-runtime' if masked else 'static', DropInPaths='',
                FragmentPath='/run/systemd/system/' + unit)

        def _no_job(self, job):
            return job in ('', '0')

    system = current.b_suspend_system(Base)
    backend = object.__new__(module.Backend)
    backend.phase = 'mesh'
    backend.native = lambda: None
    backend.mesh = SimpleNamespace(B_GUARD=tmp_path, SERVICES=(), B_SERVICES=(), directory=lambda *args: None)
    backend.policy = SimpleNamespace(system=system)
    if fault:
        with pytest.raises(RuntimeError):
            backend.containment()
    else:
        backend.containment()
        assert set(masks) == set(current.MASKED_SLEEP_TARGETS)
        assert len(masks) == 3 * len(current.MASKED_SLEEP_TARGETS)
    with pytest.raises(RuntimeError, match='b_provisioning_suspend_forbidden'):
        system.suspend_verified()


def test_projection_preserves_every_other_pin_and_original_document():
    old = {'unit_id': 'unit-b', 'containment': 'fresh-b-v1', 'boot_id': 'boot',
        'service_files': {'/original': 'a' * 64, '/guard': module.sha(b'fence')},
        'preparation_args': {'immutable': 'reviewed'}}
    before = module.encoded(old)
    result = module.project_mesh(old, {Path('/guard'): b'fence'})
    assert result == {**old, 'service_files': {'/original': 'a' * 64}}
    assert module.encoded(old) == before


@pytest.mark.parametrize('fault', ['unit', 'mode', 'hash', 'missing'])
def test_projection_rejects_other_device_or_unpinned_removal(fault):
    expected = {'unit_id': 'unit-b', 'containment': 'fresh-b-v1',
        'service_files': {'/guard': module.sha(b'fence')}}
    if fault == 'unit':
        expected['unit_id'] = 'unit-a'
    elif fault == 'mode':
        expected['containment'] = None
    elif fault == 'hash':
        expected['service_files']['/guard'] = '0' * 64
    else:
        expected['service_files'].clear()
    with pytest.raises(RuntimeError):
        module.project_mesh(expected, {Path('/guard'): b'fence'})


class Fake:
    def __init__(self, root, fault=None):
        self.phase = 'mesh'
        self.root, self.fault = root, fault
        self.events = []
        self.claimed = False
        self.guards = {root / ('policy-' + name): b'old' for name in ('model', 'broker', 'voice')}
        for path, raw in self.guards.items():
            path.write_bytes(raw)
        self.original = root / 'original.conf'
        self.original.write_bytes(b'preserve-exactly')
        self.successor = root / 'mesh-guard'
        self.successor.write_bytes(b'physical-fence')
        self.checks = 0
        self.removes = 0

    def check(self):
        self.checks += 1
        self.events.append('check')
        assert self.successor.read_bytes() == b'physical-fence'
        assert self.original.read_bytes() == b'preserve-exactly'
        if self.fault == 'preflight' or self.fault == 'post_remove' and self.removes:
            raise RuntimeError('unknown')

    def claim(self):
        self.events.append('claim')
        (self.root / 'attempt').mkdir()
        self.claimed = True

    def record(self, phase, path):
        assert self.claimed
        self.events.append(phase)
        if self.fault == 'journal' or self.fault == 'complete' and phase == 'complete':
            raise OSError('journal failed')

    def archive(self, path, raw):
        assert self.events[-1] == 'remove_intent'
        self.events.append('archive')
        (self.root / (path.name + '.archive')).write_bytes(raw)
        if self.fault == 'archive':
            raise OSError('archive unknown')

    def remove(self, path, raw):
        assert self.events[-1] == 'archive'
        assert (self.root / (path.name + '.archive')).read_bytes() == raw
        self.events.append('remove')
        path.unlink()
        self.removes += 1
        if self.fault == 'remove':
            raise OSError('remove unknown')

    def reload(self):
        self.events.append('reload')
        if self.fault == 'reload':
            raise TimeoutError('reload unknown')

    def failure(self):
        self.events.append('containment')
        assert self.successor.read_bytes() == b'physical-fence'
        return self.fault != 'containment'

    def publish(self):
        self.events.append('publish')
        if self.fault in ('publish', 'containment'):
            raise OSError('metadata unknown')


@pytest.mark.parametrize('fault', [None, 'preflight', 'journal', 'archive', 'remove',
    'reload', 'post_remove', 'publish', 'complete', 'containment'])
def test_transfer_order_and_unknown_results_never_retry(tmp_path, fault):
    backend = Fake(tmp_path, fault)
    if fault:
        with pytest.raises((OSError, RuntimeError)):
            module.transfer(backend)
        assert backend.events.count('containment') == (0 if fault == 'preflight' else 1)
    else:
        result = module.transfer(backend)
        assert result['transferred'] is True and result['services_started'] is False
        assert result['commissioning_performed'] is False
        assert backend.removes == 3 and backend.events[-1] == 'complete'
    assert backend.original.read_bytes() == b'preserve-exactly'
    assert backend.successor.read_bytes() == b'physical-fence'
    if fault in ('remove', 'reload', 'post_remove'):
        assert backend.removes == 1
    if backend.claimed:
        with pytest.raises(FileExistsError):
            backend.claim()


@pytest.mark.parametrize('fault', [None, 'write', 'reload', 'verify'])
def test_native_failure_restores_restart_guard_independently(tmp_path, monkeypatch, fault):
    backend = object.__new__(module.Backend)
    backend.phase = 'native'
    path = tmp_path / 'native.guard'
    backend.guards = {path: b'[Service]\nRestart=no\n'}
    backend.removed = {path}
    events = []
    def write(target, raw, mode):
        events.append('write')
        assert mode == 0o644
        if fault == 'write':
            raise OSError('unknown')
        target.write_bytes(raw)
    def reload():
        events.append('reload')
        if fault == 'reload':
            raise TimeoutError('unknown')
    def containment():
        events.append('verify')
        if fault == 'verify':
            raise RuntimeError('unknown')
    monkeypatch.setattr(module, 'write', write)
    backend.reload, backend.containment = reload, containment
    assert backend.failure() is (fault is None)
    assert events == ['write', 'reload', 'verify']
    if fault != 'write':
        assert path.read_bytes() == backend.guards[path]
        assert backend.removed == set()


@pytest.mark.parametrize('fault', [None, 'snapshot', 'plan', 'boot', 'completion', 'document',
    'installed_config', 'unit_path', 'unit_hash', 'loaded', 'transient', 'dropin', 'reload',
    'restart', 'active', 'pid', 'job', 'cgroup', 'enable_link'])
def test_native_release_requires_actual_audio_and_installed_owner(tmp_path, monkeypatch, fault):
    backend = object.__new__(module.Backend)
    plan = {'boot_id': 'boot'}
    documents = {tmp_path / 'boot/startup.json': b'{}', tmp_path / 'boot/baseline.json': b'baseline'}
    backend.audio = SimpleNamespace(HANDOFF=tmp_path, canonical=module.encoded,
        documents=lambda *args: documents)
    def snapshot():
        if fault == 'snapshot':
            raise RuntimeError('not commissioned or not actually active')
        return plan, b'actual-lifetime'
    backend.audio_owner = SimpleNamespace(snapshot=snapshot, tools={},
        coordinator=SimpleNamespace(validate_full_config=lambda config: None))
    backend.expected = {'audio_plan_sha256': module.sha(module.encoded(plan)),
        'startup_service': {'path': module.STARTUP_SERVICE, 'sha256': 'a' * 64}}
    unit = dict(LoadState='loaded', Transient='no', FragmentPath=module.STARTUP_SERVICE,
        DropInPaths='', NeedDaemonReload='no', Restart='no', ActiveState='inactive', MainPID='0', Job='')
    def empty(*args):
        if fault == 'cgroup':
            raise RuntimeError('nonempty')
    backend.policy = SimpleNamespace(expected={'boot_id': 'boot'}, properties=lambda service: unit,
        system=SimpleNamespace(_empty_tree=empty, _cgroup=lambda group: group))
    complete = dict(schema='sage.lantern.first-audio-handoff-result.v1', unit_id='unit-b',
        boot_id='boot', files={str(path): module.sha(raw) for path, raw in documents.items()},
        services_started=False, services_enabled=False, reboot_accepted=False)
    values = {**documents, tmp_path / 'boot/complete.json': module.encoded(complete), module.STARTUP: b'{}'}
    if fault == 'plan':
        backend.expected['audio_plan_sha256'] = '0' * 64
    elif fault == 'boot':
        backend.policy.expected['boot_id'] = 'other'
    elif fault == 'completion':
        values[tmp_path / 'boot/complete.json'] = b'{}'
    elif fault == 'document':
        values[tmp_path / 'boot/baseline.json'] = b'changed'
    elif fault == 'installed_config':
        values[module.STARTUP] = b'not installed exact candidate'
    elif fault == 'unit_path':
        backend.expected['startup_service']['path'] = '/other'
    replacements = {'loaded': ('LoadState', 'not-found'), 'transient': ('Transient', 'yes'),
        'dropin': ('DropInPaths', '/unknown'), 'reload': ('NeedDaemonReload', 'yes'),
        'restart': ('Restart', 'always'), 'active': ('ActiveState', 'active'),
        'pid': ('MainPID', '123'), 'job': ('Job', '1')}
    if fault in replacements:
        name, value = replacements[fault]
        unit[name] = value
    def pinned(pin):
        if fault == 'unit_hash':
            raise RuntimeError('hash changed')
        return b'pinned-unit'
    monkeypatch.setattr(module, 'read', lambda path: values[path])
    monkeypatch.setattr(module, 'pinned', pinned)
    monkeypatch.setattr(module, 'parents', lambda path: None)
    monkeypatch.setattr(Path, 'is_symlink', lambda path: fault != 'enable_link')
    monkeypatch.setattr(Path, 'resolve', lambda path: Path(module.STARTUP_SERVICE))
    if fault:
        with pytest.raises(RuntimeError):
            backend.audio_check()
    else:
        backend.audio_check()


def test_duplicate_json_refused():
    with pytest.raises(RuntimeError):
        module.decode(b'{"unit_id":"unit-a","unit_id":"unit-b"}')


@pytest.mark.parametrize('fault', [None, 'epoch', 'boot', 'sha', 'schema', 'extra'])
def test_mesh_receipt_binds_current_post_unlock_epoch(fault):
    expected = {'boot_id': 'actual-boot'}
    receipt = dict(schema='sage.lantern.b-first-mesh-containment.v1', boot_id='actual-boot',
        storage_epoch='actual-post-policy-unlock-epoch', expected_sha256=module.sha(module.encoded(expected)))
    if fault:
        key = {'epoch': 'storage_epoch', 'boot': 'boot_id', 'sha': 'expected_sha256',
            'schema': 'schema', 'extra': 'extra'}[fault]
        receipt[key] = ''
        with pytest.raises(RuntimeError):
            module.mesh_receipt(module.encoded(receipt), expected)
    else:
        assert module.mesh_receipt(module.encoded(receipt), expected) == receipt


@pytest.mark.parametrize('fault', [None, 'second_replace'])
def test_mesh_metadata_publication_archives_originals_and_keeps_guards(tmp_path, monkeypatch, fault):
    backend = object.__new__(module.Backend)
    backend.phase = 'mesh'
    backend.directory = tmp_path / 'journal'
    backend.directory.mkdir()
    guard_root = tmp_path / 'mesh'
    guard_root.mkdir()
    backend.mesh = SimpleNamespace(B_GUARD=guard_root)
    backend.metadata = {guard_root / 'expected.json': b'original-expected',
        guard_root / 'complete.json': b'original-receipt'}
    originals = dict(backend.metadata)
    for path, raw in backend.metadata.items():
        path.write_bytes(raw)
    successor = guard_root / 'physical-guard'
    successor.write_bytes(b'fence')
    backend.next_mesh, backend.next_receipt = {'updated': 'expected'}, {'updated': 'receipt'}
    backend.check = lambda: [module.require(path.read_bytes() == raw) for path, raw in backend.metadata.items()]
    backend.containment = lambda: module.require(successor.read_bytes() == b'fence')
    events = []
    backend.record = lambda phase, path: events.append((phase, path))
    monkeypatch.setattr(module, 'parents', lambda path: None)
    monkeypatch.setattr(module, 'read', lambda path: Path(path).read_bytes())
    replace = module.os.replace
    def replacement(source, target):
        assert events[-1] == ('metadata_replace_intent', str(target))
        if fault and target.name == 'complete.json':
            raise OSError('unknown')
        replace(source, target)
    monkeypatch.setattr(module.os, 'replace', replacement)
    if fault:
        with pytest.raises(OSError):
            backend.publish()
        assert not (backend.directory / 'mesh-expected.json').exists()
    else:
        backend.publish()
        assert (backend.directory / 'mesh-expected.json').read_bytes() == module.encoded(backend.next_mesh)
    for path, raw in originals.items():
        assert (backend.directory / ('before-' + path.name)).read_bytes() == raw
    assert successor.read_bytes() == b'fence'


@pytest.mark.parametrize('fault', [None, 'absent_fence', 'guard_bytes', 'condition', 'active',
    'pid', 'job', 'restart', 'cgroup', 'sleep'])
def test_successor_requires_physical_not_merely_recorded_containment(tmp_path, monkeypatch, fault):
    backend = object.__new__(module.Backend)
    backend.phase = 'mesh'
    backend.native = lambda: None
    service = 'sage-lantern-mesh-runtime.service'
    value = dict(ActiveState='inactive', MainPID='0', Job='', Restart='no')
    replacements = {'active': ('ActiveState', 'active'), 'pid': ('MainPID', '42'),
        'job': ('Job', '1'), 'restart': ('Restart', 'on-failure')}
    if fault in replacements:
        key, replacement = replacements[fault]
        value[key] = replacement
    def gate(name):
        def check(*args):
            if fault == name:
                raise RuntimeError('physical evidence refused')
        return check
    backend.mesh = SimpleNamespace(B_GUARD=tmp_path, SERVICES=(service,), B_SERVICES=(),
        directory=gate('absent_fence'), b_guard_path=lambda name: tmp_path / name,
        b_guard_bytes=lambda: b'exact-guard')
    backend.policy = SimpleNamespace(properties=lambda name: value, system=SimpleNamespace(
        _guard=gate('condition'), _empty_tree=gate('cgroup'), _sleep_idle=gate('sleep'), _cgroup=lambda path: path))
    monkeypatch.setattr(module, 'read', lambda path: b'wrong' if fault == 'guard_bytes' else b'exact-guard')
    if fault:
        with pytest.raises(RuntimeError):
            backend.containment()
    else:
        backend.containment()
