import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts/lantern'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load('provision-fresh-appliance')
phase = load('provision-postcommission-startup')
handoff = load('handoff-fresh-audio-lifetime')
RUNTIME = json.loads((Path(__file__).parent/'fixtures/closure2-runtime-artifact.json').read_bytes())


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    payload = tmp_path/'payload'
    payload.mkdir()
    def read(path):
        return Path(path).read_bytes()
    monkeypatch.setattr(phase, 'trusted', read)
    monkeypatch.setattr(handoff, 'trusted', read)
    original_helper = module.helper
    monkeypatch.setattr(module, 'helper', lambda name, path: phase if path == module.POSTCOMMISSION
        else handoff if path == module.FRESH_HANDOFF else original_helper(name, path))
    sources = [module.FRESH_HANDOFF, 'scripts/lantern/install-startup-coordinator.py',
        'software/systemd/sage-lantern-startup.service', 'scripts/lantern/activate-fresh-display.py',
        'scripts/lantern/startup-coordinator.py', 'scripts/lantern/startup-audio-adapter.py',
        'scripts/lantern/recover-cross-boot-audio.py', 'scripts/lantern/collect-cross-boot-audio-pins.py',
        RUNTIME['file']['path']]
    entries = {source: dict(path=source, role='support', sha256=phase.INSTALLER_SHA if 'install-startup' in source else 'a'*64)
        for source in sources}
    entries[RUNTIME['file']['path']] = RUNTIME['file']
    (tmp_path/'manifest.json').write_text(json.dumps(dict(files=list(entries.values()),
        selection=dict(components=dict(runtime=RUNTIME['runtime_version']), files=[RUNTIME['selected']]))))
    tools = dict(unit_id='unit-b', **{name: dict(path='/opt/'+name, sha256='a'*64)
        for name in ('coordinator', 'adapter', 'helper', 'collector', 'runtime_wheel')},
        setup=dict(observer=dict(path='/opt/observer', sha256='a'*64)))
    tools['runtime_wheel']['sha256'] = RUNTIME['file']['sha256']
    tool_path = tmp_path/'tools.json'
    tool_path.write_bytes(handoff.canonical(tools))
    deps = dict(schema='sage.lantern.postcommission-dependencies.v1', unit_id='unit-b',
        tools=dict(path=str(tool_path), sha256=handoff.sha(tool_path.read_bytes())))
    for name, source in [('handoff', sources[0]), ('installer', sources[1]), ('service', sources[2])]:
        deps[name] = dict(path=str(payload/source), sha256=entries[source]['sha256'])
    path = tmp_path/'deps.json'
    path.write_bytes(handoff.canonical(deps))
    args = SimpleNamespace(unit_id='unit-b', phase='postcommission-handoff', app_options=[],
        allow_native_build=False, install_os_packages=False, execute=False,
        startup_dependencies=str(path), startup_dependencies_sha256=handoff.sha(path.read_bytes()),
        apply_postcommission=False)
    return args, payload, deps, tools


def write_dependencies(args, deps):
    raw = handoff.canonical(deps)
    Path(args.startup_dependencies).write_bytes(raw)
    args.startup_dependencies_sha256 = handoff.sha(raw)


@pytest.mark.parametrize('unit', ['unit-a', 'unit-b'])
@pytest.mark.parametrize('selection', ['postcommission-handoff', 'postcommission-startup'])
def test_explicit_independent_phase_readonly_default(inputs, monkeypatch, unit, selection):
    args, payload, deps, tools = inputs
    args.unit_id = deps['unit_id'] = tools['unit_id'] = unit
    args.phase = selection
    raw = handoff.canonical(tools)
    Path(deps['tools']['path']).write_bytes(raw)
    deps['tools']['sha256'] = handoff.sha(raw)
    write_dependencies(args, deps)
    monkeypatch.setattr(module, 'verify_release', lambda args: payload)
    result = module.execute(args, run=lambda *args, **kwargs: pytest.fail('default must not execute'))
    command = result['commands'][0]
    assert command[:3] == ['/opt/sage-lantern/venv/bin/python', '-I', '-B']
    assert command[-1] == module.POST_PHASES[selection]
    assert '--apply' not in command and result['ready'] is False


@pytest.mark.parametrize('field', ['unit_id', 'handoff', 'installer', 'service'])
def test_foreign_release_or_identity_refused(inputs, field):
    args, payload, deps, tools = inputs
    if field == 'unit_id':
        deps[field] = 'unit-a'
    else:
        deps[field]['sha256'] = 'b'*64
    write_dependencies(args, deps)
    with pytest.raises(ValueError):
        module.commands(args, payload)


@pytest.mark.parametrize('field', ['unit_id', 'coordinator', 'adapter', 'helper', 'collector', 'runtime_wheel', 'setup'])
def test_mixed_tool_closure_refused(inputs, field):
    args, payload, deps, tools = inputs
    if field == 'unit_id':
        tools[field] = 'unit-a'
    elif field == 'setup':
        tools[field]['observer']['sha256'] = 'b'*64
    else:
        tools[field]['sha256'] = 'b'*64
    raw = handoff.canonical(tools)
    Path(deps['tools']['path']).write_bytes(raw)
    deps['tools']['sha256'] = handoff.sha(raw)
    write_dependencies(args, deps)
    with pytest.raises(ValueError):
        module.commands(args, payload)


def test_explicit_apply_once_no_automatic_next_phase(inputs, monkeypatch, tmp_path):
    args, payload, deps, tools = inputs
    args.execute = args.apply_postcommission = True
    args.startup_attempt_id = '11111111-1111-4111-8111-111111111111'
    expected = dict(snapshot=dict(unit_id='unit-b'), phase='handoff', dependencies_sha256=args.startup_dependencies_sha256)
    path = tmp_path/'expected.json'
    path.write_bytes(handoff.canonical(expected))
    args.startup_expected = str(path)
    args.startup_expected_sha256 = handoff.sha(path.read_bytes())
    monkeypatch.setattr(module, 'verify_release', lambda args: payload)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.sys, 'platform', 'linux')
    monkeypatch.setattr(module.platform, 'machine', lambda: 'aarch64')
    calls = []
    result = module.execute(args, run=lambda command, **kwargs: calls.append(command))
    assert len(calls) == 1 and '--apply' in calls[0] and 'install-startup' not in calls[0]
    assert result['ready'] is False and result['provisioned'] is False
    expected['snapshot']['unit_id'] = 'unit-a'
    path.write_bytes(handoff.canonical(expected))
    args.startup_expected_sha256 = handoff.sha(path.read_bytes())
    with pytest.raises(ValueError):
        module.commands(args, payload)


@pytest.mark.parametrize('field', ['allow_native_build', 'install_os_packages', 'owner_approved_companion_bootstrap'])
def test_no_crossphase_options(inputs, field):
    args, payload, deps, tools = inputs
    setattr(args, field, True)
    with pytest.raises(ValueError):
        module.commands(args, payload)


def test_child_failure_not_retried(inputs, monkeypatch):
    args, payload, deps, tools = inputs
    args.execute = True
    monkeypatch.setattr(module, 'verify_release', lambda args: payload)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.sys, 'platform', 'linux')
    monkeypatch.setattr(module.platform, 'machine', lambda: 'aarch64')
    calls = []
    def failed(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)
    with pytest.raises(subprocess.CalledProcessError):
        module.execute(args, run=failed)
    assert len(calls) == 1


def test_app_cannot_inherit_startup_apply(inputs):
    args, payload, deps, tools = inputs
    args.phase = 'app'
    with pytest.raises(ValueError):
        module.commands(args, payload)


@pytest.mark.parametrize('change', ['role', 'version', 'path', 'duplicate', 'selection', 'hash'])
def test_real_runtime_contract_rejects_mismatch(inputs, change):
    args, payload, deps, tools = inputs
    path = payload.parent/'manifest.json'
    manifest = json.loads(path.read_bytes())
    wheel = next(entry for entry in manifest['files'] if entry['role'] == 'runtime_wheel')
    if change == 'role':
        wheel['role'] = 'dependency'
    elif change == 'version':
        manifest['selection']['components']['runtime'] = '9.9.9'
    elif change == 'path':
        wheel['path'] = 'software/bundle/python-wheels/sage_lantern-0.2.0-py3-none-any.whl'
    elif change == 'duplicate':
        manifest['files'].append(dict(wheel, path='software/bundle/python-wheels/foreign.whl'))
    elif change == 'selection':
        manifest['selection']['files'] = []
    else:
        wheel['sha256'] = 'b'*64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        module.commands(args, payload)


def test_real_runtime_designation_not_filename_guess(inputs):
    args, payload, deps, tools = inputs
    path = payload.parent/'manifest.json'
    manifest = json.loads(path.read_bytes())
    manifest['files'].append(dict(path='software/bundle/python-wheels/sage_lantern-1-py3-none-any.whl',
        role='dependency', sha256='b'*64))
    path.write_text(json.dumps(manifest))
    assert len(module.commands(args, payload)) == 1
