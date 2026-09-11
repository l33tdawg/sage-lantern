import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'lantern/install-vision-local.sh'


@pytest.fixture
def installer(tmp_path, monkeypatch):
    module = types.ModuleType('vision_installer')
    source = SCRIPT.read_text().split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    exec(compile(source, str(SCRIPT), 'exec'), module.__dict__)
    for name in ('STATE', 'SYSTEMD'):
        setattr(module, name, tmp_path / name.lower())
        getattr(module, name).mkdir()
    for name in ('MODEL', 'PROJECTOR', 'BINARY', 'VOICE'):
        setattr(module, name, tmp_path / name.lower())
        getattr(module, name).write_text(name + ' --photo-ready')
    module.MODEL_SHA = hashlib.sha256(module.MODEL.read_bytes()).hexdigest()
    module.PROJECTOR_SHA = hashlib.sha256(module.PROJECTOR.read_bytes()).hexdigest()
    for service in (module.LLM_SERVICE, module.VOICE_SERVICE):
        (module.SYSTEMD / service).write_text('[Service]\nExecStart=/existing\n')
    monkeypatch.setattr(module.platform, 'machine', lambda: 'aarch64')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    return module


def valid_marker(installer, monkeypatch):
    hashes = dict(model_sha256=installer.MODEL_SHA, projector_sha256=installer.PROJECTOR_SHA, llama_server_sha256=installer.digest(installer.BINARY))
    marker = installer.STATE / 'LOCAL_VISION_VALIDATED'
    marker.write_text(json.dumps(dict(validated=True, **hashes)))
    marker.chmod(0o644)
    (installer.STATE / 'LOCAL_VOICE_READY').touch()
    original = Path.lstat

    def root_lstat(path):
        result = original(path)
        if path == marker:
            return types.SimpleNamespace(st_mode=result.st_mode, st_uid=0, st_size=result.st_size)
        return result

    monkeypatch.setattr(Path, 'lstat', root_lstat)
    return hashes


def test_check_only_reads_without_service_actions(installer, monkeypatch, capsys):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert command == [str(installer.BINARY), '--help']
        assert kwargs['timeout'] == 30
        return subprocess.CompletedProcess(command, 0, '--mmproj --no-mmproj-offload --image-max-tokens --parallel --n-gpu-layers', '')

    monkeypatch.setattr(installer.subprocess, 'run', fake_run)
    monkeypatch.setattr(sys, 'argv', ['installer', '--check-only'])
    installer.main()
    assert len(calls) == 1
    assert not list(installer.STATE.iterdir())
    assert 'NOT established' in capsys.readouterr().out


def test_hash_mismatch_prevents_binary_execution(installer, monkeypatch):
    installer.MODEL.write_text('wrong')
    monkeypatch.setattr(installer.subprocess, 'run', lambda *args, **kwargs: pytest.fail('must not execute'))
    with pytest.raises(ValueError, match='mismatch'):
        installer.check_artifacts()


def test_missing_flag_rejected(installer, monkeypatch):
    monkeypatch.setattr(installer.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess([], 0, '--mmproj-url', ''))
    with pytest.raises(ValueError, match='--mmproj'):
        installer.check_artifacts()


@pytest.mark.parametrize('field', ['validated', 'model_sha256', 'projector_sha256', 'llama_server_sha256'])
def test_evidence_requires_explicit_matching_validation(installer, monkeypatch, field):
    hashes = valid_marker(installer, monkeypatch)
    marker = installer.STATE / 'LOCAL_VISION_VALIDATED'
    evidence = json.loads(marker.read_text())
    evidence[field] = False if field == 'validated' else 'wrong'
    marker.write_text(json.dumps(evidence))
    with pytest.raises(ValueError):
        installer.check_evidence(hashes)


def test_valid_marker_and_permissions(installer, monkeypatch):
    hashes = valid_marker(installer, monkeypatch)
    installer.check_evidence(hashes)
    (installer.STATE / 'LOCAL_VISION_VALIDATED').chmod(0o666)
    with pytest.raises(ValueError, match='root-owned'):
        installer.check_evidence(hashes)


def test_voice_marker_required(installer, monkeypatch):
    hashes = valid_marker(installer, monkeypatch)
    (installer.STATE / 'LOCAL_VOICE_READY').unlink()
    with pytest.raises(ValueError, match='LOCAL_VOICE_READY'):
        installer.check_evidence(hashes)


def test_enable_and_disable_preserve_unrelated_dropins(installer, monkeypatch):
    calls = []
    monkeypatch.setattr(installer, 'run', lambda *args: calls.append(args))
    directory = installer.SYSTEMD / f'{installer.LLM_SERVICE}.d'
    directory.mkdir()
    unrelated = directory / '10-local.conf'
    unrelated.write_text('[Service]\nNice=5\n')
    installer.configure(True)
    model_override = directory / installer.DROPIN
    content = model_override.read_text()
    for option in ('--mmproj', '--no-mmproj-offload', '--parallel 1', '--threads 6', '--image-max-tokens 256', 'MemoryMax=5G', 'MemorySwapMax=0'):
        assert option in content
    assert '--n-predict' not in content and '--predict' not in content
    assert calls[0] == ('systemctl', 'stop', installer.VOICE_SERVICE)
    assert ('systemctl', 'restart', installer.LLM_SERVICE) in calls
    assert '--photo-ready' in (installer.SYSTEMD / f'{installer.VOICE_SERVICE}.d' / installer.DROPIN).read_text()
    assert list(installer.STATE.glob('vision-dropins-backup-*'))
    installer.configure(False)
    assert not model_override.exists()
    assert unrelated.read_text() == '[Service]\nNice=5\n'


def test_enable_refuses_conflicting_execstart(installer, monkeypatch):
    directory = installer.SYSTEMD / f'{installer.LLM_SERVICE}.d'
    directory.mkdir()
    (directory / '99-other.conf').write_text('[Service]\nExecStart=/custom\n')
    monkeypatch.setattr(installer, 'run', lambda *args: pytest.fail('no service actions allowed'))
    with pytest.raises(ValueError, match='operator review'):
        installer.configure(True)


def test_partial_install_failure_restores_original_files(installer, monkeypatch):
    monkeypatch.setattr(installer, 'run', lambda *args: None)
    directory = installer.SYSTEMD / f'{installer.LLM_SERVICE}.d'
    directory.mkdir()
    existing = directory / installer.DROPIN
    existing.write_text('old override')
    write = installer.atomic_write
    count = 0

    def fail_once(path, content):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('disk full')
        write(path, content)

    monkeypatch.setattr(installer, 'atomic_write', fail_once)
    with pytest.raises(OSError, match='disk full'):
        installer.configure(True)
    assert existing.read_text() == 'old override'
    assert not (installer.SYSTEMD / f'{installer.VOICE_SERVICE}.d' / installer.DROPIN).exists()
    backup = next(installer.STATE.glob('vision-dropins-backup-*'))
    assert (backup / f'{installer.LLM_SERVICE}.d' / installer.DROPIN).read_text() == 'old override'


def test_restart_failure_rolls_back(installer, monkeypatch):
    calls = []

    def run(*args):
        calls.append(args)
        if len(calls) == 3:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(installer, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        installer.configure(True)
    assert not list(installer.SYSTEMD.glob('*.d/90-lantern-local-vision.conf'))
    assert calls.count(('systemctl', 'restart', installer.LLM_SERVICE)) == 2


def test_disable_does_not_require_model_or_evidence(installer, monkeypatch):
    installer.MODEL.unlink()
    called = []
    monkeypatch.setattr(installer, 'configure', lambda enabled: called.append(enabled))
    monkeypatch.setattr(sys, 'argv', ['installer', '--disable'])
    installer.main()
    assert called == [False]
