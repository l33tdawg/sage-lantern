import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sage_lantern.deployment_bringup import AUDIO_INPUTS, REQUIRED


SPEC = importlib.util.spec_from_file_location('stale_audio',
    Path(__file__).parents[1] / 'lantern/inspect-stale-audio-epoch.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture():
    services = {service: dict(pid=100 + index, start_ticks='1234', uid=0 if service == MODULE.BROKER else 994,
        invocation='a' * 32, argv=['/reviewed/python', '-m', 'reviewed.module'], image_sha256='b' * 64,
        dropins=[]) for index, service in enumerate(MODULE.SERVICES)}
    files = {MODULE.CONFIG: 'c' * 64, MODULE.VOICE_HELPER: 'd' * 64,
             '/etc/sage-lantern/audio-capture-alsa.conf': 'e' * 64}
    files.update({MODULE.SYSTEMD + service: 'f' * 64 for service in MODULE.SERVICES})
    files.update({name: MODULE.sha(AUDIO_INPUTS[name]) if name in AUDIO_INPUTS else 'f' * 64
                  for name in REQUIRED})
    for service, pin in services.items():
        pin['dropins'] = sorted(name for name in REQUIRED if name.startswith(MODULE.SYSTEMD + service + '.d/'))
    expected = dict(schema='sage.lantern.stale-audio-inspection.v1', unit_id='unit-b',
        boot_id='11111111-1111-4111-8111-111111111111', chain_id='sage-personal-test', agent_id='1' * 64,
        bootstrap_path='/var/lib/reviewed/bootstrap.json', bootstrap_sha256='2' * 64, fence_sha256='3' * 64,
        files=files, runtime_sha256={'mesh_daemon.py': '4' * 64}, services=services)
    bootstrap = dict(schema='sage.lantern.voice-bootstrap.v1', phase='complete', boot_id=expected['boot_id'],
        pid=90, inventory={name: files[name] for name in REQUIRED},
        evidence=dict(broker_id='5' * 32, vault_epoch='6' * 64 + ':7',
                      broker_pid=services[MODULE.BROKER]['pid'], voice_pid=services[MODULE.VOICE]['pid']))
    fence = dict(schema='sage.lantern.storage-supervisor.v1', phase='active', broker_id='5' * 32)
    return expected, bootstrap, fence


class Fake:
    def __init__(self, bootstrap, fence):
        self.bootstrap, self.fence = bootstrap, fence
        self.epochs = ['6' * 64 + ':8'] * 2
        self.events = []

    def files(self, expected):
        self.events.append('files')

    def service(self, service, pin):
        self.events.append(service)

    def records(self, expected):
        return copy.deepcopy((self.bootstrap, self.fence))

    def snapshot(self, expected):
        self.events.append('signed_get')
        return self.epochs.pop(0)

    def remaining(self):
        return 1


def test_same_instance_forward_generation_is_diagnostic_only():
    expected, bootstrap, fence = fixture()
    native = Fake(bootstrap, fence)
    result = MODULE.inspect(expected, native)
    assert result['stale_epoch_verified'] is True
    assert result['historical_work_disposition'] == 'unknown'
    for field in ('mutations_performed', 'containment_verified', 'drain_verified',
                  'rebootstrap_authorized', 'native_update_authorized'):
        assert result[field] is False
    assert native.events.count('signed_get') == 2
    assert native.events.count(MODULE.BROKER) == 3


@pytest.mark.parametrize('name', sorted(AUDIO_INPUTS))
def test_actual_bootstrap_audio_inputs_cannot_be_omitted_or_forged(name):
    expected, bootstrap, fence = fixture()
    assert name in expected['files'] and name in bootstrap['inventory']
    del expected['files'][name]
    with pytest.raises(ValueError):
        MODULE.validate(expected)
    expected, bootstrap, fence = fixture()
    expected['files'][name] = bootstrap['inventory'][name] = '9' * 64
    with pytest.raises(Exception):
        MODULE.inspect(expected, Fake(bootstrap, fence))


@pytest.mark.parametrize('value', ['6' * 64 + ':7', '6' * 64 + ':6', '9' * 64 + ':8',
                                 '6' * 64 + ':0', '6' * 64 + ':18446744073709551616', 'invalid'])
def test_non_forward_same_instance_epoch_refused(value):
    expected, bootstrap, fence = fixture()
    native = Fake(bootstrap, fence)
    native.epochs = [value]
    with pytest.raises(ValueError):
        MODULE.inspect(expected, native)


@pytest.mark.parametrize('fault', ['bootstrap_phase', 'boot', 'broker_pid', 'voice_pid', 'fence', 'inventory'])
def test_historical_binding_mismatch_refused_before_probe(fault):
    expected, bootstrap, fence = fixture()
    if fault == 'bootstrap_phase':
        bootstrap['phase'] = 'failed'
    elif fault == 'boot':
        bootstrap['boot_id'] = '22222222-2222-4222-8222-222222222222'
    elif fault in ('broker_pid', 'voice_pid'):
        bootstrap['evidence'][fault] += 1
    elif fault == 'fence':
        fence['broker_id'] = '9' * 32
    else:
        bootstrap['inventory'][MODULE.CONFIG] = '9' * 64
    native = Fake(bootstrap, fence)
    with pytest.raises(ValueError):
        MODULE.inspect(expected, native)
    assert 'signed_get' not in native.events


@pytest.mark.parametrize('fault', ['epoch', 'process', 'record', 'deadline', 'storage_unavailable'])
def test_changed_or_unavailable_observation_refuses(monkeypatch, fault):
    expected, bootstrap, fence = fixture()
    native = Fake(bootstrap, fence)
    if fault == 'epoch':
        native.epochs[-1] = '6' * 64 + ':9'
    elif fault == 'record':
        original = native.snapshot
        def snapshot(expected):
            native.fence['phase'] = 'recovery_required'
            return original(expected)
        monkeypatch.setattr(native, 'snapshot', snapshot)
    else:
        def fail(*args):
            raise TimeoutError() if fault == 'deadline' else ValueError()
        monkeypatch.setattr(native, {'process': 'service', 'deadline': 'remaining',
                                     'storage_unavailable': 'snapshot'}[fault], fail)
    with pytest.raises((ValueError, TimeoutError)):
        MODULE.inspect(expected, native)


@pytest.mark.parametrize('fault', ['extra', 'missing_service', 'missing_file', 'image', 'argv', 'dropin'])
def test_expected_schema_is_closed(fault):
    expected, _, _ = fixture()
    if fault == 'extra':
        expected['apply'] = True
    elif fault == 'missing_service':
        del expected['services'][MODULE.VOICE]
    elif fault == 'missing_file':
        del expected['files'][MODULE.VOICE_HELPER]
    elif fault == 'image':
        expected['services'][MODULE.VOICE]['image_sha256'] = 'unreviewed'
    elif fault == 'argv':
        expected['services'][MODULE.VOICE]['argv'] = ['bad\x00argv']
    else:
        expected['services'][MODULE.VOICE]['dropins'] = ['/tmp/untrusted.conf']
    with pytest.raises(ValueError):
        MODULE.validate(expected)


def test_systemctl_uses_repeated_properties_and_never_mutates(monkeypatch):
    expected, _, _ = fixture()
    native = MODULE.Native(5)
    def run(command, **kwargs):
        assert command[:3] == ['/usr/bin/systemctl', 'show', '--no-pager']
        assert command[3:-1] == [part for field in MODULE.FIELDS for part in ('-p', field)]
        assert kwargs['timeout'] <= 5
        return SimpleNamespace(stdout=b'LoadState=not-found\n')
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    with pytest.raises(ValueError):
        native.service(MODULE.VOICE, expected['services'][MODULE.VOICE])


def test_duplicate_json_and_apply_flag_refused():
    with pytest.raises(ValueError):
        MODULE.decode(b'{"field":1,"field":2}')
    with pytest.raises(SystemExit) as raised:
        MODULE.main(['--expected', '/root/expected.json', '--expected-sha256', 'a' * 64, '--apply'])
    assert raised.value.code == 2


def test_public_file_symlink_refused(tmp_path):
    path = tmp_path / 'public.json'
    path.symlink_to(tmp_path / 'missing')
    with pytest.raises((ValueError, OSError)):
        MODULE.public_bytes(str(path))
