import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('auth_supersession',
    Path(__file__).resolve().parents[1] / 'lantern/auth-capability-supersession.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture():
    def report(instance, confirmed):
        return json.dumps(dict(version=1, instance_id=instance, closing=confirmed, drained=confirmed,
            local_revoked=confirmed, upstream_cleanup='confirmed' if confirmed else 'unknown')).encode()
    old, current = 'a' * 32, 'b' * 32
    receipts = {old: report(old, False), current: report(current, True)}
    image = dict(binary_sha256='c' * 64, manifest_sha256='d' * 64, inventory_sha256='e' * 64, version='old-v27')
    process = dict(pid=334079, start_ticks=12345, invocation='f' * 32, uid=994,
                   application_boot_id='12345678-1234-4234-8234-123456789abc')
    binding = dict(unit_id='unit-a', boot_id='22345678-1234-4234-8234-123456789abc', root_id='1' * 64,
        chain_id='sage-personal-a', initialized_sha256='2' * 64, native_before=image,
        native_after=dict(image, binary_sha256='3' * 64, manifest_sha256='4' * 64,
                          inventory_sha256='5' * 64, version='new-v27'),
        sage_before=process, current_auth=dict(pid=334080, start_ticks=12346, instance_id=current, uid=994),
        unknown_receipts={old: hashlib.sha256(receipts[old]).hexdigest()})
    evidence = copy.deepcopy(binding)
    evidence.update(schema=MODULE.SCHEMA,
        sage_after=dict(process, application_boot_id='32345678-1234-4234-8234-123456789abc'),
        current_auth_report_sha256=hashlib.sha256(receipts[current]).hexdigest(),
        capability_invalidated=True, historical_request_completion='unknown')
    live = dict(boot_id=binding['boot_id'], sage_after=copy.deepcopy(evidence['sage_after']),
        native_after=copy.deepcopy(binding['native_after']), auth_inactive=True, auth_cgroup_empty=True,
        starts_inhibited=True, encrypted=True, vault_locked=True)
    return evidence, binding, receipts, live


def validate(values):
    evidence, binding, receipts, live = values
    return MODULE.validate(evidence, binding=binding, receipts=receipts, live=live)


def test_same_pid_exec_capability_only_and_receipts_unchanged():
    values = fixture()
    original = copy.deepcopy(values)
    assert validate(values) == dict(capability_invalidated=True, historical_request_completion='unknown',
        mutation_authorized=False, auth_drain_receipts_rewritten=False)
    assert values == original


@pytest.mark.parametrize('field', sorted(MODULE.BINDING_FIELDS))
def test_every_transition_binding_must_match(field):
    values = fixture()
    values[0][field] = None
    with pytest.raises(ValueError):
        validate(values)


@pytest.mark.parametrize('field', ['auth_inactive', 'auth_cgroup_empty', 'starts_inhibited', 'encrypted', 'vault_locked'])
@pytest.mark.parametrize('value', [False, 1, None])
def test_live_containment_is_literal_positive_evidence(field, value):
    values = fixture()
    values[3][field] = value
    with pytest.raises(ValueError):
        validate(values)


@pytest.mark.parametrize('fault', ['unchanged_boot', 'wrong_live_boot', 'wrong_binary', 'wrong_source',
    'unknown_mutated', 'current_unknown', 'current_missing', 'extra_unknown', 'claim_completion',
    'numeric_claim', 'credential_field', 'wrong_pid', 'wrong_uid', 'wrong_current_hash', 'empty_unknown'])
def test_reconciliation_rejects_unproven_or_widened_claims(fault):
    evidence, binding, receipts, live = values = fixture()
    if fault == 'unchanged_boot':
        evidence['sage_after']['application_boot_id'] = binding['sage_before']['application_boot_id']
    elif fault == 'wrong_live_boot':
        live['sage_after']['application_boot_id'] = binding['sage_before']['application_boot_id']
    elif fault in ('wrong_binary', 'wrong_source'):
        live['native_after']['binary_sha256' if fault == 'wrong_binary' else 'inventory_sha256'] = '6' * 64
    elif fault == 'unknown_mutated':
        receipts['a' * 32] += b'\n'
    elif fault == 'current_unknown':
        receipts['b' * 32] = receipts['b' * 32].replace(b'true', b'false').replace(b'confirmed', b'unknown')
        evidence['current_auth_report_sha256'] = hashlib.sha256(receipts['b' * 32]).hexdigest()
    elif fault == 'current_missing':
        del receipts['b' * 32]
    elif fault == 'extra_unknown':
        receipts['7' * 32] = receipts['a' * 32].replace(b'a' * 32, b'7' * 32)
    elif fault == 'claim_completion':
        evidence['historical_request_completion'] = 'confirmed'
    elif fault == 'numeric_claim':
        evidence['capability_invalidated'] = 1
    elif fault == 'credential_field':
        evidence['cookie'] = 'not permitted'
    elif fault in ('wrong_pid', 'wrong_uid'):
        evidence['sage_after']['pid' if fault == 'wrong_pid' else 'uid'] += 1
    elif fault == 'wrong_current_hash':
        evidence['current_auth_report_sha256'] = '8' * 64
    elif fault == 'empty_unknown':
        binding['unknown_receipts'] = {}
    with pytest.raises(ValueError):
        validate(values)


def test_same_binary_restart_can_invalidate_sessions_but_still_requires_new_application_boot():
    evidence, binding, receipts, live = fixture()
    binding['native_after'] = copy.deepcopy(binding['native_before'])
    evidence['native_after'] = copy.deepcopy(binding['native_after'])
    live['native_after'] = copy.deepcopy(binding['native_after'])
    MODULE.validate(evidence, binding=binding, receipts=receipts, live=live)


@pytest.mark.parametrize('fault', [None, 'occupied', 'unguarded', 'auth_active', 'changed_health',
                                 'timeout', 'helper_hash', 'evidence_hash', 'current_unknown'])
def test_reader_revalidates_live_containment_and_actual_receipts(monkeypatch, tmp_path, fault):
    spec = importlib.util.spec_from_file_location('reader_test',
        Path(__file__).resolve().parents[1] / 'lantern/preflight-fresh-native-update.py')
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    evidence, binding, receipts, live = fixture()
    helper = Path(MODULE.__file__).read_bytes() + b'\nlistener_owner = lambda *args: "321"\n'
    binary = b'new reviewed binary'
    manifest = dict(binary_sha256=reader.sha(binary), inventory_sha256='5' * 64, version='new-v27')
    manifest_raw = json.dumps(manifest).encode()
    native = dict(manifest, manifest_sha256=reader.sha(manifest_raw))
    binding['native_after'] = native
    evidence['native_after'] = native
    initialized = json.dumps(dict(root_id=binding['root_id'], chain_id=binding['chain_id'])).encode()
    binding['initialized_sha256'] = evidence['initialized_sha256'] = reader.sha(initialized)
    reports = tmp_path / 'reports'
    reports.mkdir()
    for instance, raw in receipts.items():
        (reports / (instance + '.json')).write_bytes(raw)
    evidence_raw = json.dumps(evidence).encode()
    if fault == 'current_unknown':
        receipts['b' * 32] = receipts['b' * 32].replace(b'true', b'false').replace(b'confirmed', b'unknown')
    monkeypatch.setattr(reader, 'REPORTS', reports)
    def trusted(path, *args, **kwargs):
        path = Path(path)
        if path.name == 'auth-capability-supersession.py':
            return helper
        if path.parent == reports:
            return receipts[path.stem]
        return {'/evidence': evidence_raw, '/manifest': manifest_raw, reader.BINARY: binary,
                reader.CONFIG + 'initialized-public.json': initialized}[str(path)]
    monkeypatch.setattr(reader, 'trusted', trusted)
    original_lstat = Path.lstat
    def lstat(path):
        if str(path) == '/var/lib/sage-lantern-cold-native-update':
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
        if path == reports or path.parent == reports:
            return SimpleNamespace(st_mode=(stat.S_IFDIR | 0o700) if path == reports else (stat.S_IFREG | 0o600), st_uid=994)
        return original_lstat(path)
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(Path, 'stat', lambda path: SimpleNamespace(st_dev=1, st_ino=2))
    monkeypatch.setattr(Path, 'read_text', lambda path: binding['boot_id'])
    monkeypatch.setattr(reader, 'load_kiosk_binding', lambda: SimpleNamespace(unit_id='unit-a'))
    pinned = binding['sage_before']
    monkeypatch.setattr(reader, 'LinuxProcesses', lambda: SimpleNamespace(read=lambda pid:
        SimpleNamespace(uid=994, start_ticks=pinned['start_ticks'], executable=reader.BINARY,
                        argv=(reader.BINARY, 'serve'), state='S')))
    observer = reader.Observation.__new__(reader.Observation)
    observer.uid, observer.deadline = 994, 45
    def show(name, deadline, fields):
        active = name == reader.SAGE or fault == 'auth_active' and name == reader.AUTH
        value = dict(LoadState='loaded', ActiveState='active' if active else 'inactive',
            MainPID=str(pinned['pid']) if active else '0', InvocationID=pinned['invocation'], Job='',
            ControlGroup='/system.slice/' + name, Slice='system.slice', KillMode='control-group',
            User='sage-lantern', NeedDaemonReload='no', Restart='no', FragmentPath='/unit', DropInPaths='')
        return {name: value[name] for name in fields}
    def empty(*args):
        if fault == 'occupied':
            raise ValueError
    def guard(*args):
        if fault == 'unguarded':
            raise ValueError
    observer.system = SimpleNamespace(_show=show, _guard=guard, _empty_tree=empty, _cgroup=lambda path: path)
    calls = []
    def health(*args):
        calls.append('health')
        boot = evidence['sage_after']['application_boot_id']
        if fault == 'changed_health' and len(calls) == 2:
            boot = binding['sage_before']['application_boot_id']
        return dict(sage='running', version='new-v27', boot_id=boot, encrypted=True, vault_locked=True)
    observer.get = health
    def remaining():
        if fault == 'timeout':
            raise TimeoutError
        return 5
    observer.remaining = remaining
    def consume():
        return observer.read_supersession('/evidence', 'f' * 64 if fault == 'evidence_hash' else reader.sha(evidence_raw),
            helper_sha256='f' * 64 if fault == 'helper_hash' else reader.sha(helper),
            binding=binding, manifest_path='/manifest')
    if fault:
        with pytest.raises((ValueError, TimeoutError)):
            consume()
    else:
        assert consume()['capability_invalidated'] is True
        assert calls == ['health', 'health']


@pytest.mark.parametrize('fault', [None, 'wrong_owner', 'wildcard', 'duplicate', 'wrong_start'])
def test_listener_is_unique_loopback_and_owned_by_pinned_process(tmp_path, fault):
    process = tmp_path / '42'
    (process / 'fd').mkdir(parents=True)
    (tmp_path / 'net').mkdir()
    (tmp_path / 'sys/kernel/random').mkdir(parents=True)
    boot = '12345678-1234-4234-8234-123456789abc'
    (tmp_path / 'sys/kernel/random/boot_id').write_text(boot)
    (process / 'stat').write_text('42 (sage) ' + ' '.join(['S'] + ['0'] * 18 + ['100']))
    address = '00000000' if fault == 'wildcard' else '0100007F'
    row = '0: ' + address + ':1F90 00000000:0000 0A 0 0 0 0 0 321\n'
    (tmp_path / 'net/tcp').write_text('header\n' + row * (2 if fault == 'duplicate' else 1))
    os.symlink('socket:[999]' if fault == 'wrong_owner' else 'socket:[321]', process / 'fd/3')
    if fault:
        with pytest.raises(ValueError):
            MODULE.listener_owner(42, 101 if fault == 'wrong_start' else 100, boot, proc=tmp_path)
    else:
        assert MODULE.listener_owner(42, 100, boot, proc=tmp_path) == '321'
