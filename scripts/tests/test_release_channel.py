"""The release builder and installer: pins, allowlists and refusal boundaries."""
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software/runtime/src'))


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = load('release_build', 'scripts/lantern/build-release.py')
INSTALL = load('release_install', 'scripts/lantern/install-release.py')
BOOT = '11111111-1111-4111-8111-111111111111'
PRIOR_BOOT = '22222222-2222-4222-8222-222222222222'
PATH = '/etc/sage-lantern/mesh-runtime.json'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def deployed(tmp_path):
    package = tmp_path / 'deployed'
    package.mkdir()
    modules = {'__init__.py': b'# init\n', 'mesh_runtime.py': b'VALUE = 1\n',
               '._mesh_runtime.py': b'\x00\x05\x16\x07mac metadata\n'}
    for name, raw in modules.items():
        (package / name).write_bytes(raw)
    hashes = tmp_path / 'installed.txt'
    hashes.write_text(''.join(f'{BUILD.sha(raw)}  {name}\n' for name, raw in modules.items()))
    return package, hashes, modules


def template_wheel(tmp_path):
    path = tmp_path / 'template.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('sage_lantern_runtime-0.2.0.dist-info/METADATA', 'Name: sage-lantern-runtime\n')
        archive.writestr('sage_lantern_runtime-0.2.0.dist-info/RECORD', '')
        archive.writestr('sage_sdk/__init__.py', '# sdk\n')
        archive.writestr('sage_lantern/__init__.py', '# stale\n')
    return path


def snapshot(*, delta=True, broker_mismatch=True):
    prior_inventory = {PATH: 'a' * 64}
    live_inventory = {PATH: 'b' * 64} if delta else {PATH: 'a' * 64}
    fence = dict(schema='sage.lantern.storage-supervisor.v1', phase='active',
                 broker_id='c' * 32 if broker_mismatch else 'd' * 32)
    return dict(
        schema='sage.lantern.release-snapshot.v1', boot_id=BOOT,
        baseline=dict(inventory=live_inventory, runtime_sources={'sage_lantern/x.py': '1' * 64},
                      files={'/model.gguf': '2' * 64}, commission_sha256='3' * 64,
                      actor={'chain_id': 'chain', 'agent_id': 'owner'}, model_argv=['/opt/llama-server']),
        config={'path': '/etc/sage-lantern/startup.json', 'sha256': '4' * 64},
        policy={'path': '/var/lib/old/policy.json', 'sha256': '5' * 64},
        coordinator={'path': '/opt/sage-lantern/libexec/startup-coordinator.py', 'sha256': '6' * 64},
        fence={'path': '/var/lib/sage-lantern-mesh-supervisor/fence.json', 'sha256': '7' * 64,
               'document': fence},
        pointer={'path': '/var/lib/sage-lantern-startup/latest.json', 'sha256': '8' * 64},
        prior_pin={'path': f'/var/lib/sage-lantern-cross-boot-audio/{PRIOR_BOOT}/new-lifetime.json',
                   'sha256': 'b' * 64},
        prior=dict(schema='sage.lantern.voice-bootstrap.v1', phase='complete', boot_id=PRIOR_BOOT,
                   pid=100, inventory=prior_inventory,
                   evidence=dict(broker_id='d' * 32, vault_epoch='9' * 64 + ':2',
                                 broker_pid=101, voice_pid=102)),
        initial_lifetime={'path': '/var/lib/sage-lantern-maintained-audio/old/new-lifetime.json',
                          'sha256': 'a' * 64},
        attempt=dict(state_dir='/var/lib/sage-lantern-cross-boot-audio/' + BOOT,
                     state_dir_exists=True, journal_exists=True,
                     boot_journal='/var/lib/sage-lantern-startup/' + BOOT + '.jsonl'),
        historical_outcomes='unknown')


def build(tmp_path, snapshot_document, name='release'):
    package, hashes, _ = deployed(tmp_path)
    snapshot_path = tmp_path / 'snapshot.json'
    snapshot_path.write_bytes(canonical(snapshot_document))
    args = type('Args', (), dict(
        snapshot=snapshot_path, deployed=package, template_wheel=template_wheel(tmp_path),
        out=tmp_path / name, unit='lantern-b', stamp='20260918-150000',
        built_at='2026-09-18T07:00:00Z', authorized_by='owner-approved test',
        allowance_authorized_by='owner-approved allowance', installed_hashes=hashes,
        tools=ROOT / 'scripts/lantern', runtime_src=ROOT / 'software/runtime/src'))()
    BUILD.build(args)
    return args.out


def test_build_generates_a_self_describing_release(tmp_path):
    release = build(tmp_path, snapshot())
    manifest = json.loads((release / 'manifest.json').read_bytes())
    assert manifest['schema'] == 'sage.lantern.release.v1'
    assert manifest['boot_id'] == BOOT
    assert set(manifest['files']) == set(INSTALL.PLACED) | {'state/allowance.json'}
    wheel = BUILD.sha((release / 'runtime' / BUILD.WHEEL_NAME).read_bytes())
    assert manifest['artifacts']['runtime_wheel']['sha256'] == wheel
    assert wheel in (release / 'tools/collect-cross-boot-audio-pins.py').read_text()
    assert wheel in (release / 'tools/startup-coordinator.py').read_text()
    collector = manifest['artifacts']['collector']['sha256']
    assert f"COLLECTOR_SHA = '{collector}'" in (release / 'tools/startup-audio-adapter.py').read_text()


def test_release_wheel_carries_the_deployed_bytes_not_the_template(tmp_path):
    release = build(tmp_path, snapshot())
    with zipfile.ZipFile(release / 'runtime' / BUILD.WHEEL_NAME) as archive:
        names = {name for name in archive.namelist() if name.startswith('sage_lantern/')}
        assert names == {'sage_lantern/__init__.py', 'sage_lantern/mesh_runtime.py',
                         'sage_lantern/._mesh_runtime.py'}
        assert archive.read('sage_lantern/mesh_runtime.py') == b'VALUE = 1\n'
        assert archive.read('sage_lantern/._mesh_runtime.py').startswith(b'\x00\x05')
        assert archive.read('sage_sdk/__init__.py') == b'# sdk\n'


def test_wheel_is_reproducible_from_the_same_deployed_bytes(tmp_path):
    package, _, _ = deployed(tmp_path)
    first = BUILD.build_wheel(template_wheel(tmp_path), package, tmp_path / 'one.whl')
    second = BUILD.build_wheel(template_wheel(tmp_path), package, tmp_path / 'two.whl')
    assert first == second
    assert (tmp_path / 'one.whl').read_bytes() == (tmp_path / 'two.whl').read_bytes()


def test_build_refuses_deployed_bytes_that_do_not_match_the_unit(tmp_path):
    package, hashes, modules = deployed(tmp_path)
    hashes.write_text(''.join(f'{BUILD.sha(raw)}  {name}\n' for name, raw in modules.items())
                      + f"{'f' * 64}  ghost.py\n")
    snapshot_path = tmp_path / 'snapshot.json'
    snapshot_path.write_bytes(canonical(snapshot()))
    args = type('Args', (), dict(
        snapshot=snapshot_path, deployed=package, template_wheel=template_wheel(tmp_path),
        out=tmp_path / 'release', unit='lantern-b', stamp='20260918-150000',
        built_at='2026-09-18T07:00:00Z', authorized_by='owner-approved test',
        allowance_authorized_by='owner-approved allowance', installed_hashes=hashes,
        tools=ROOT / 'scripts/lantern', runtime_src=ROOT / 'software/runtime/src'))()
    with pytest.raises(ValueError):
        BUILD.build(args)


def test_allowance_is_prior_scoped_and_pins_exactly_the_delta(tmp_path):
    release = build(tmp_path, snapshot())
    allowance = json.loads((release / 'state/allowance.json').read_bytes())
    assert allowance['boot_id'] == PRIOR_BOOT
    assert allowance['inventory_delta'] == {PATH: {'from': 'a' * 64, 'to': 'b' * 64}}
    assert allowance['fence']['broker_id'] == 'c' * 32
    assert allowance['historical_outcomes'] == 'unknown'


def test_no_allowance_when_the_prior_binds_strictly(tmp_path):
    release = build(tmp_path, snapshot(delta=False, broker_mismatch=False))
    assert not (release / 'state/allowance.json').exists()
    config = json.loads((release / 'state/startup.json').read_bytes())
    assert 'allowance' not in config


def test_injected_allowlist_accepts_only_its_own_wheel(tmp_path):
    release = build(tmp_path, snapshot())
    collector = BUILD.load_module(release / 'tools/collect-cross-boot-audio-pins.py', 'c')
    coordinator = BUILD.load_module(release / 'tools/startup-coordinator.py', 'k')
    wheel = json.loads((release / 'manifest.json').read_bytes())['artifacts']['runtime_wheel']
    assert wheel['sha256'] == collector.RELEASE_RUNTIME == coordinator.RELEASE_RUNTIME
    calls = []
    coordinator.pinned = lambda pin: calls.append(pin) or b''
    coordinator.verify_wheel(wheel)
    assert calls == [wheel]
    with pytest.raises(Exception):
        coordinator.verify_wheel(dict(wheel, sha256='f' * 64))
    assert calls == [wheel]


def test_verify_release_accepts_and_then_refuses_a_tampered_file(tmp_path, monkeypatch):
    release = build(tmp_path, snapshot())
    monkeypatch.setattr(INSTALL, 'boot_id', lambda: BOOT)
    manifest, root = INSTALL.verify_release(release)
    assert str(root) == manifest['root']
    INSTALL.require_install_boot(manifest)
    target = release / 'tools/startup-audio-adapter.py'
    target.write_bytes(target.read_bytes() + b'# tampered\n')
    with pytest.raises(ValueError):
        INSTALL.verify_release(release)


def test_verify_release_refuses_a_foreign_boot(tmp_path, monkeypatch):
    release = build(tmp_path, snapshot())
    manifest = json.loads((release / 'manifest.json').read_bytes())
    monkeypatch.setattr(INSTALL, 'boot_id', lambda: PRIOR_BOOT)
    with pytest.raises(ValueError):
        INSTALL.require_install_boot(manifest)
    # A later boot may still verify and accept the release it was installed with.
    INSTALL.verify_release(release)


def test_verify_release_refuses_an_unexpected_extra_file(tmp_path, monkeypatch):
    release = build(tmp_path, snapshot())
    monkeypatch.setattr(INSTALL, 'boot_id', lambda: BOOT)
    (release / 'tools/extra.py').write_bytes(b'# extra\n')
    with pytest.raises(ValueError):
        INSTALL.verify_release(release)


def test_root_is_derived_from_the_manifest_and_confined():
    assert INSTALL.root_directory('/var/lib/sage-lantern-release-lantern-b-20260918-150000')
    for bad in ('/var/lib/sage-lantern-release/', '/etc/sage-lantern', '/var/lib/sage-lantern-release-x',
                '/var/lib/sage-lantern-release-lantern-b-2026-09-18', '/tmp/release'):
        with pytest.raises(ValueError):
            INSTALL.root_directory(bad)


def test_consumer_boot_is_a_real_boot_that_is_not_the_prior(tmp_path):
    next_boot = INSTALL.consumer_boot(BOOT, BOOT)
    assert str(__import__('uuid').UUID(next_boot)) == next_boot
    assert next_boot != BOOT
    assert INSTALL.consumer_boot(BOOT, PRIOR_BOOT) == BOOT
    with pytest.raises(ValueError):
        INSTALL.consumer_boot(BOOT, 'not-a-boot')
