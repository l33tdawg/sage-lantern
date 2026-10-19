import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('install_npu_test', ROOT / 'scripts/lantern/install-npu-payload.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def payload(tmp_path, monkeypatch):
    source = tmp_path / 'payload'
    pins = {}
    records = []
    for name in (*MODULE.PROFILE.NPU_SOURCE_PINS, *MODULE.PROFILE.NPU_ARTIFACT_PINS):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ('synthetic ' + name).encode()
        path.write_bytes(raw)
        path.chmod(0o644)
        pins[name] = hashlib.sha256(raw).hexdigest()
        if name in MODULE.PROFILE.NPU_ARTIFACT_PINS:
            records.append(dict(path=name, bytes=len(raw), sha256=pins[name]))
    lock = source / MODULE.PROFILE.LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps(dict(schema='sage.lantern.stack-lock.v1',
        target=dict(os='linux', architecture='arm64', python='3.11'), files=records)))
    monkeypatch.setattr(MODULE.PROFILE, 'NPU_SOURCE_PINS',
        {name: pins[name] for name in MODULE.PROFILE.NPU_SOURCE_PINS})
    monkeypatch.setattr(MODULE.PROFILE, 'NPU_ARTIFACT_PINS',
        {name: pins[name] for name in MODULE.PROFILE.NPU_ARTIFACT_PINS})
    monkeypatch.setattr(MODULE.os, 'chown', lambda *args: None)
    monkeypatch.setattr(MODULE.os, 'fchown', lambda *args: None)
    return source


def compiler(calls, machine=183):
    def run(command, **options):
        calls.append((command, options))
        assert options['env'] == dict(PATH='/usr/bin:/bin', HOME='/nonexistent', LANG='C', LC_ALL='C')
        assert options['check'] is True
        if command == ['/usr/bin/c++', '--version']:
            return SimpleNamespace(stdout='synthetic compiler fixture\n')
        if command == ['/usr/bin/pkg-config', '--cflags', 'opencv4']:
            return SimpleNamespace(stdout='-I/usr/include/opencv4\n')
        if command == ['/usr/bin/pkg-config', '--libs', 'opencv4']:
            return SimpleNamespace(stdout='-lopencv_core -lopencv_imgproc -lopencv_imgcodecs\n')
        assert command[:3] == ['/usr/bin/c++', '-O2', '-std=c++17']
        assert '-lrkllmrt' in command
        assert '-Wl,-rpath,/opt/sage-lantern/npu/lib' in command
        if any(part.endswith('vlm_engine.cpp') for part in command):
            assert '-lrknnrt' in command and '-lopencv_core' in command
        raw = bytearray(64)
        raw[:6] = b'\x7fELF\x02\x01'
        raw[18:20] = machine.to_bytes(2, 'little')
        Path(command[command.index('-o') + 1]).write_bytes(raw)
        return SimpleNamespace(returncode=0)
    return run


def test_offline_build_installs_exact_payload_and_records_inputs_without_inference(payload, tmp_path):
    calls = []
    destination = tmp_path / 'installed'
    receipt = MODULE.build_and_install(payload, destination, run=compiler(calls))
    assert len(calls) == 5
    assert receipt['inference_validated'] is False
    assert receipt['input_sha256'] == {**MODULE.PROFILE.NPU_SOURCE_PINS, **MODULE.PROFILE.NPU_ARTIFACT_PINS}
    assert len(receipt['engine_sha256']) == 64 and len(receipt['vision_engine_sha256']) == 64
    assert json.loads((destination / 'build-provenance.json').read_text()) == receipt
    assert (destination / 'bin/rkllm_engine').stat().st_mode & 0o777 == 0o755
    assert (destination / 'bin/vlm_engine').stat().st_mode & 0o777 == 0o755
    for relative in ('lib/librkllmrt.so', 'lib/librknnrt.so',
                     'models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm',
                     'models/qwen3.5-0.8b_vision_rk3588.rknn',
                     'models/qwen3.5-0.8b_w8a8_rk3588.rkllm'):
        assert (destination / relative).read_bytes() == (payload / MODULE.PROFILE.NPU_ROOT / relative).read_bytes()
        assert (destination / relative).stat().st_mode & 0o777 == 0o644
    for exec_relative, source_relative in (('libexec/rkllm_server.py', 'software/npu/rkllm_server.py'),
                                           ('libexec/vlm_server.py', 'software/npu/vlm_server.py')):
        assert (destination / exec_relative).read_bytes() == (payload / source_relative).read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        MODULE.build_and_install(payload, destination, run=lambda *args, **kwargs: pytest.fail('must not compile twice'))


@pytest.mark.parametrize('fault', ['source', 'library', 'model', 'lock', 'symlink'])
def test_unreviewed_payload_refuses_before_compiler_or_destination(payload, tmp_path, fault):
    target = payload / ('software/npu/rkllm_engine.cpp' if fault == 'source'
        else MODULE.PROFILE.NPU_ROOT + ('models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm'
            if fault == 'model' else 'lib/librkllmrt.so'))
    if fault == 'lock':
        path = payload / MODULE.PROFILE.LOCK
        lock = json.loads(path.read_text())
        lock['files'][0]['sha256'] = '0' * 64
        path.write_text(json.dumps(lock))
    elif fault == 'symlink':
        original = tmp_path / 'outside'
        target.rename(original)
        target.symlink_to(original)
    else:
        target.write_bytes(target.read_bytes() + b'changed')
    destination = tmp_path / 'installed'
    with pytest.raises(ValueError):
        MODULE.build_and_install(payload, destination, run=lambda *args, **kwargs: pytest.fail('unreviewed compile'))
    assert not destination.exists()


def test_wrong_architecture_build_never_published(payload, tmp_path):
    destination = tmp_path / 'installed'
    with pytest.raises(ValueError, match='ELF64 AArch64'):
        MODULE.build_and_install(payload, destination, run=compiler([], machine=62))
    assert not destination.exists()


def test_changed_header_during_compile_never_published(payload, tmp_path):
    execute = compiler([])
    def changing(command, **options):
        result = execute(command, **options)
        if '-o' in command:
            path = payload / MODULE.PROFILE.NPU_ROOT / 'include/rkllm.h'
            path.write_bytes(path.read_bytes() + b'changed')
        return result
    destination = tmp_path / 'installed'
    with pytest.raises(ValueError, match='unreviewed NPU input'):
        MODULE.build_and_install(payload, destination, run=changing)
    assert not destination.exists()


def next_payload(payload, monkeypatch, changed):
    """Change payload sources/artifacts and re-pin them, as a new release would."""
    for name, raw in changed.items():
        path = payload / name
        path.write_bytes(raw)
    pins = {}
    records = []
    for name in (*MODULE.PROFILE.NPU_SOURCE_PINS, *MODULE.PROFILE.NPU_ARTIFACT_PINS):
        raw = (payload / name).read_bytes()
        pins[name] = hashlib.sha256(raw).hexdigest()
        if name in MODULE.PROFILE.NPU_ARTIFACT_PINS:
            records.append(dict(path=name, bytes=len(raw), sha256=pins[name]))
    (payload / MODULE.PROFILE.LOCK).write_text(json.dumps(dict(
        schema='sage.lantern.stack-lock.v1',
        target=dict(os='linux', architecture='arm64', python='3.11'), files=records)))
    monkeypatch.setattr(MODULE.PROFILE, 'NPU_SOURCE_PINS',
        {name: pins[name] for name in MODULE.PROFILE.NPU_SOURCE_PINS})
    monkeypatch.setattr(MODULE.PROFILE, 'NPU_ARTIFACT_PINS',
        {name: pins[name] for name in MODULE.PROFILE.NPU_ARTIFACT_PINS})


def predecessor_tree(payload, destination, tmp_path, *, release_id='fixture-predecessor'):
    """A release tree that accounts for exactly what is installed now."""
    rows = [dict(path=relative, destination=str(destination / relative),
                 sha256=hashlib.sha256((destination / relative).read_bytes()).hexdigest(), operation='copy')
            for relative in MODULE.payload_targets(payload)]
    root = tmp_path / 'release'
    (root / 'payload/release').mkdir(parents=True)
    (root / 'manifest.json').write_text(json.dumps(dict(release_id=release_id)))
    (root / 'payload/release/destinations.json').write_text(json.dumps(dict(files=rows)))
    return root


def test_successor_replaces_accounted_files_and_records_predecessor(payload, tmp_path, monkeypatch):
    calls = []
    destination = tmp_path / 'installed'
    MODULE.build_and_install(payload, destination, run=compiler(calls))
    release = predecessor_tree(payload, destination, tmp_path)
    next_payload(payload, monkeypatch, {
        MODULE.PROFILE.NPU_ROOT + 'models/qwen3.5-0.8b_vision_rk3588.rknn': b'synthetic new vision model\n',
        'software/npu/vlm_server.py': b'synthetic new vlm server\n'})
    calls.clear()
    receipt = MODULE.successor_install(payload, destination, release, run=compiler(calls))
    assert receipt['predecessor_release_id'] == 'fixture-predecessor'
    assert receipt['replaced_unverifiable_npu_targets'] == []
    assert (destination / 'models/qwen3.5-0.8b_vision_rk3588.rknn').read_bytes() == b'synthetic new vision model\n'
    assert (destination / 'libexec/vlm_server.py').read_bytes() == b'synthetic new vlm server\n'
    assert (destination / 'build-provenance.predecessor-fixture-predecessor.json').exists()
    assert json.loads((destination / 'build-provenance.json').read_text()) == receipt
    assert len(calls) == 5


def test_successor_refuses_an_edited_file_and_records_acceptance(payload, tmp_path, monkeypatch):
    calls = []
    destination = tmp_path / 'installed'
    MODULE.build_and_install(payload, destination, run=compiler(calls))
    release = predecessor_tree(payload, destination, tmp_path)
    edited = destination / 'models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm'
    edited.write_bytes(b'operator edit outside the release\n')
    next_payload(payload, monkeypatch, {
        'software/npu/vlm_server.py': b'synthetic new vlm server\n'})
    with pytest.raises(ValueError, match='unverifiable installed NPU file would be replaced: models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm'):
        MODULE.successor_install(payload, destination, release, run=compiler([]))
    assert edited.read_bytes() == b'operator edit outside the release\n'
    receipt = MODULE.successor_install(payload, destination, release, accept_replaced=True, run=compiler([]))
    assert receipt['replaced_unverifiable_npu_targets'] == [str(edited)]
    assert edited.read_bytes() == (payload / MODULE.PROFILE.NPU_ROOT / 'models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm').read_bytes()


def test_successor_adds_an_engine_the_older_provenance_does_not_bind(payload, tmp_path, monkeypatch):
    """A pre-vision destination (engine bound, no vision engine) still succeeds."""
    destination = tmp_path / 'installed'
    MODULE.build_and_install(payload, destination, run=compiler([]))
    release = predecessor_tree(payload, destination, tmp_path)
    provenance = json.loads((destination / 'build-provenance.json').read_text())
    del provenance['vision_engine_sha256']
    (destination / 'build-provenance.json').write_text(json.dumps(provenance, sort_keys=True) + '\n')
    (destination / 'bin/vlm_engine').unlink()
    receipt = MODULE.successor_install(payload, destination, release, run=compiler([]))
    assert receipt['replaced_unverifiable_npu_targets'] == []
    assert (destination / 'bin/vlm_engine').is_file()
    assert receipt['predecessor_release_id'] == 'fixture-predecessor'
