import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = load('console_lane_check', 'scripts/lantern/check-console-lane-local.py')


class Runner:
    """A systemctl/curl stand-in that records what was asked."""

    def __init__(self, *, voice=('enabled', 'active', 'success'), app=('enabled', 'active', 'success'),
                 health=None, sleep_exit=0):
        self.calls = []
        self.voice = voice
        self.app = app
        self.health = health if health is not None else {'ok': True, 'typed_ready': True, 'review_ready': True}
        self.sleep_exit = sleep_exit

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[0] == CHECK.SYSTEMCTL:
            unit = argv[2]
            state = self.voice if unit == CHECK.VOICE_UNIT else self.app
            if argv[1] == 'is-enabled':
                return 0, state[0].encode()
            if argv[1] == 'is-active':
                return 0 if state[1] == 'active' else 3, state[1].encode()
            if argv[1] == 'show':
                return 0, f'Result={state[2]}'.encode()
            return 0, b''
        if argv[0] == '/usr/bin/curl':
            return 0, json.dumps(self.health).encode()
        if argv[0] == CHECK.SLEEP_CHECK[0]:
            return self.sleep_exit, b''
        raise AssertionError(argv)


@pytest.fixture
def unit(tmp_path, monkeypatch):
    """A synthetic installed unit with the gate and the app build id."""
    root = tmp_path / 'unit'
    gate = root / CHECK.GATE.lstrip('/')
    gate.parent.mkdir(parents=True)
    gate.write_bytes((ROOT / 'software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf').read_bytes())
    build = root / CHECK.APP_ROOT.lstrip('/') / 'dist/server'
    build.mkdir(parents=True)
    (build / 'BUILD_ID').write_text('4d6d0f82-4083-49b9-9989-81cb0795f1af\n')
    monkeypatch.setattr(CHECK, 'GATE', str(gate))
    monkeypatch.setattr(CHECK, 'APP_ROOT', str(root / CHECK.APP_ROOT.lstrip('/')))
    monkeypatch.setattr(CHECK, 'run', Runner())
    return root, gate


def manifest_for(gate, build_id='4d6d0f82-4083-49b9-9989-81cb0795f1af'):
    return {'schema': 'sage.lantern.console-bundle.v1', 'bundle_id': 'test', 'mode': 'stage-only',
            'app_build_id': build_id,
            'files': [{'destination': str(gate), 'mode': 0o644,
                       'sha256': hashlib.sha256(gate.read_bytes()).hexdigest()}]}


def test_check_passes_for_an_activated_lane(unit, tmp_path, capsys):
    _, gate = unit
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest_for(gate)))
    assert CHECK.main(['--manifest', str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['ok'] is True and report['failures'] == []
    assert report['checks']['typed_lane_advertised']['ok'] is True
    assert report['checks']['bundle_files_verified']['ok'] is True
    assert report['checks']['console_build_matches_bundle']['ok'] is True
    # Read-only by default: nothing was started or reset.
    assert not any(call[1] in {'start', 'restart', 'reset-failed'} for call in CHECK.run.calls if len(call) > 1)


def test_check_fails_when_the_gate_is_gone_or_the_lane_is_down(unit, tmp_path, capsys, monkeypatch):
    root, gate = unit
    gate.unlink()
    monkeypatch.setattr(CHECK, 'run', Runner(voice=('disabled', 'failed', 'exit-code')))
    assert CHECK.main([]) == 1
    report = json.loads(capsys.readouterr().out)
    assert set(report['failures']) >= {'question_gate_installed', 'question_service_active',
                                       'question_service_enabled'}
    assert report['checks']['bundle_files_verified']['ok'] is None


def test_check_reports_missing_typed_capability_and_mismatched_files(unit, tmp_path, capsys, monkeypatch):
    _, gate = unit
    monkeypatch.setattr(CHECK, 'run', Runner(health={'ok': True, 'typed_ready': False, 'review_ready': True}))
    manifest = manifest_for(gate)
    manifest['files'][0]['sha256'] = '0' * 64
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    assert CHECK.main(['--manifest', str(path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['checks']['typed_lane_advertised']['ok'] is False
    assert report['checks']['bundle_files_verified']['ok'] is False
    assert report['checks']['bundle_files_verified']['detail']['mismatched'] == [str(gate)]


def test_activate_starts_a_stopped_service_then_requires_it_active(unit, monkeypatch, capsys):
    _, gate = unit

    class Restarting(Runner):
        def __call__(self, argv):
            if argv[0] == CHECK.SYSTEMCTL and argv[1] in {'reset-failed', 'start'}:
                self.calls.append(argv)
                self.voice = ('enabled', 'active', 'success')
                return 0, b''
            return super().__call__(argv)

    runner = Restarting(voice=('enabled', 'failed', 'exit-code'))
    monkeypatch.setattr(CHECK, 'run', runner)
    assert CHECK.main(['--activate', '--sleep-check']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['ok'] is True
    assert [CHECK.SYSTEMCTL, 'start', CHECK.VOICE_UNIT] in runner.calls
    assert report['checks']['sleep_check_green']['ok'] is True


def test_sleep_check_failure_is_reported(unit, monkeypatch, capsys):
    monkeypatch.setattr(CHECK, 'run', Runner(sleep_exit=1))
    assert CHECK.main(['--sleep-check']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['checks']['sleep_check_green'] == {'ok': False, 'detail': {'exit': 1}}


def test_invalid_manifest_refuses(tmp_path):
    with pytest.raises(SystemExit) as refusal:
        CHECK.manifest_files(Path('/definitely/absent/manifest.json'))
    assert str(refusal.value) == 'manifest_unavailable'
    broken = tmp_path / 'manifest.json'
    broken.write_text('{"schema": "not-this-bundle"}\n')
    with pytest.raises(SystemExit) as refusal:
        CHECK.manifest_files(broken)
    assert str(refusal.value) == 'manifest_invalid'
