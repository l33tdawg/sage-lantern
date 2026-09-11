import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('startup_adapter', Path(__file__).resolve().parents[1] /
                                            'lantern/startup-audio-adapter.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
BOOT = 'fa412850-b01f-4a1f-973b-a4c508f65417'
OLD = '31b687ff-dad6-483f-9369-c9e24ae09ca8'
EPOCH = 'a' * 64 + ':2'


def completed_prior():
    return dict(schema='sage.lantern.voice-bootstrap.v1', phase='complete', boot_id=OLD,
                pid=100, inventory={}, evidence=dict(broker_id='a' * 32, vault_epoch=EPOCH,
                                                     broker_pid=101, voice_pid=102))


@pytest.mark.parametrize('field', MODULE.STATIC)
def test_static_inputs_cannot_be_observed_into_authority(field):
    baseline = {name: {} for name in MODULE.STATIC}
    expected = dict(baseline, boot_id=BOOT, historical_outcomes='unknown', old_boot_attribution='unverified')
    expected[field] = 'changed'
    with pytest.raises(ValueError):
        MODULE.validate_current(expected, baseline, BOOT)


@pytest.mark.parametrize('fault', [None, 'same_boot', 'broker', 'inventory', 'phase'])
def test_prior_is_exact_broker_binding_not_historical_drain(fault):
    prior = completed_prior()
    fence = dict(schema='sage.lantern.storage-supervisor.v1', phase='active', broker_id='a' * 32)
    if fault == 'same_boot':
        prior['boot_id'] = BOOT
    elif fault == 'broker':
        fence['broker_id'] = 'b' * 32
    elif fault == 'inventory':
        prior['inventory'] = {'foreign': 'value'}
    elif fault == 'phase':
        fence['phase'] = 'recovery_required'
    if fault:
        with pytest.raises(ValueError):
            MODULE.validate_prior(prior, fence, {'inventory': {}}, BOOT)
    else:
        MODULE.validate_prior(prior, fence, {'inventory': {}}, BOOT)


@pytest.mark.parametrize('apply,fault', [(False, None), (True, None), (True, 'receipt'),
    (True, 'revoked'), (True, 'collect_changed'), (True, 'transition'), (True, 'epoch_changed')])
def test_full_owner_path_and_failure_boundary(tmp_path, monkeypatch, apply, fault):
    events = []
    baseline = {name: {} for name in MODULE.STATIC}
    expected = dict(baseline, boot_id=BOOT, historical_outcomes='unknown',
                    old_boot_attribution='unverified', storage_epoch=EPOCH)
    prior = completed_prior()
    policy = {name: dict(path=name, sha256='unused') for name in ('baseline', 'helper', 'collector', 'runtime_wheel')}
    policy['runtime_wheel']['sha256'] = hashlib.sha256(b'wheel').hexdigest()
    canonical = lambda value: json.dumps(value, sort_keys=True).encode()
    monkeypatch.setattr(MODULE, 'pinned', lambda pin: canonical(baseline if pin['path'] == 'baseline' else prior))
    monkeypatch.setattr(MODULE, 'trusted', lambda path: b'wheel')
    attempts = tmp_path / 'attempts'
    attempts.mkdir(mode=0o700)
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    original_lstat = Path.lstat
    def lstat(path):
        info = original_lstat(path)
        if path == attempts:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0)
        return info
    monkeypatch.setattr(Path, 'lstat', lstat)
    live = ['access']
    evidence = dict(broker_id='b' * 32, vault_epoch=EPOCH if fault != 'epoch_changed' else 'b' * 64 + ':3',
                    broker_pid=201, voice_pid=202)
    class Recovery:
        def __init__(self, value):
            self.state = attempts / BOOT
            self.owner = SimpleNamespace(active=lambda: evidence)
        def lock(self):
            events.append('lock')
        def reconcile(self):
            events.append('reconcile')
        def completed(self):
            events.append('completed')
            if fault == 'revoked':
                live[0] = None
            return dict(lifetime_sha256='digest', broker=evidence)
        def close(self):
            events.append('close')
    def write(path, raw):
        if fault == 'receipt' and path.name == 'startup-complete.json':
            raise OSError('disk')
        path.write_bytes(raw)
    def transition(backend, record):
        events.append('single_full_start')
        try:
            if fault == 'transition':
                raise RuntimeError('failed')
            backend.completed()
            record('bootstrap_done')
        except BaseException:
            events.append('contain')
            raise
    helper = SimpleNamespace(Recovery=Recovery, decode=json.loads, canonical=canonical,
        sha=lambda raw: hashlib.sha256(raw).hexdigest(), ATTEMPTS=attempts, SUPERVISOR=tmp_path,
        public=lambda path: canonical(dict(schema='sage.lantern.storage-supervisor.v1',
            phase='active', broker_id='a' * 32)), parents=lambda path: None, sync=lambda path: None,
        write=write, transition=transition)
    def collect(*args, retained_model_files):
        assert retained_model_files == baseline['files']
        value = copy.deepcopy(expected)
        if fault == 'collect_changed':
            value['files'] = {'unknown': 'digest'}
        return value
    collector = SimpleNamespace(runtime_sources=lambda *args: {}, collect=collect)
    if fault:
        with pytest.raises((ValueError, OSError, RuntimeError)):
            MODULE.run(helper, collector, policy, dict(path='prior', sha256='pin'), BOOT,
                       'access', apply, lambda: live[0])
        assert ('contain' in events) == (fault != 'collect_changed')
        assert not (attempts / BOOT / 'startup-complete.json').exists()
    else:
        result = MODULE.run(helper, collector, policy, dict(path='prior', sha256='pin'), BOOT,
                            'access', apply, lambda: live[0])
        assert result['services_started'] is apply
        assert events.count('single_full_start') == int(apply)
        if apply:
            assert result['old_drain_verified'] is False
            assert (attempts / BOOT / 'prior-lifetime.json').read_bytes() == canonical(prior)
        else:
            assert list(attempts.iterdir()) == []


def test_dependencies_remain_frozen():
    root = Path(__file__).resolve().parents[1] / 'lantern'
    for name, digest in [('recover-cross-boot-audio.py', MODULE.HELPER_SHA),
                         ('collect-cross-boot-audio-pins.py', MODULE.COLLECTOR_SHA)]:
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest


def test_hash_checked_before_exec(monkeypatch):
    monkeypatch.setattr(MODULE, 'trusted', lambda path: b'raise AssertionError("executed")')
    with pytest.raises(ValueError):
        MODULE.load(dict(path='/helper', sha256='0' * 64))


@pytest.mark.parametrize('field', ['schema', 'phase', 'boot_id', 'pid', 'inventory', 'evidence'])
def test_prior_rejects_each_missing_field(field):
    prior = completed_prior()
    del prior[field]
    with pytest.raises(ValueError):
        MODULE.validate_prior(prior, {}, {}, BOOT)


@pytest.mark.parametrize('field,value', [('schema', 'wrong'), ('pid', True), ('pid', 0), ('pid', '100'),
    ('extra', None), ('inventory', [])])
def test_prior_rejects_invalid_top_level(field, value):
    prior = completed_prior()
    prior[field] = value
    with pytest.raises(ValueError):
        MODULE.validate_prior(prior, {}, {}, BOOT)


@pytest.mark.parametrize('field,value', [('broker_id', 'short'), ('vault_epoch', True),
    ('vault_epoch', 'epoch'), ('vault_epoch', 'a' * 64 + ':0'),
    ('vault_epoch', 'a' * 64 + ':' + str(2**64)), ('broker_pid', True), ('voice_pid', '102'),
    ('broker_pid', 0), ('voice_pid', -1), ('extra', None)])
def test_prior_rejects_invalid_evidence(field, value):
    prior = completed_prior()
    prior['evidence'][field] = value
    with pytest.raises(ValueError):
        MODULE.validate_prior(prior, {}, {}, BOOT)


@pytest.mark.parametrize('field', ['broker_id', 'vault_epoch', 'broker_pid', 'voice_pid'])
def test_prior_rejects_truncated_evidence(field):
    prior = completed_prior()
    del prior['evidence'][field]
    with pytest.raises(ValueError):
        MODULE.validate_prior(prior, {}, {}, BOOT)


@pytest.mark.parametrize('raw', [b'{"schema":"a","schema":"b"}',
    b'{"helper":{"path":"a","path":"b"}}'])
def test_policy_duplicate_keys_refused_before_loading(monkeypatch, raw):
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE.sys, 'prefix', '/opt/sage-lantern/venv')
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    monkeypatch.setattr(MODULE, 'pinned', lambda pin: raw)
    def load(pin):
        pytest.fail('must not load dependency with ambiguous policy')
    monkeypatch.setattr(MODULE, 'load', load)
    with pytest.raises(ValueError):
        MODULE.main(['--policy', '/policy', '--policy-sha256', '0' * 64,
                     '--prior', '/prior', '--prior-sha256', '0' * 64,
                     '--boot-id', BOOT, '--access-epoch', 'access'])
