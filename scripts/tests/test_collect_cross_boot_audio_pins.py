import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest


SPEC = importlib.util.spec_from_file_location('pin_collector', Path(__file__).resolve().parents[1] /
                                            'lantern/collect-cross-boot-audio-pins.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HELPER = SimpleNamespace(sha=lambda raw: hashlib.sha256(raw).hexdigest(), decode=json.loads,
                         MODEL='sage-lantern-llm.service')
ARGV = ['/opt/sage-lantern/llama.cpp/llama-server', '--model',
        '/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf', '--host', '127.0.0.1', '--port', '8081']


@pytest.mark.parametrize('fault', [None, 'wrong_type', 'multiple', 'ignore_errors', 'secret_flag', 'foreign_binary'])
def test_model_argv_is_typed_and_public_only(fault):
    value = dict(type='a(sasbttttuii)', data=[[ARGV[0], list(ARGV), False, 0, 0, 0, 0, 0, 0, 0]])
    if fault == 'wrong_type':
        value['type'] = 's'
    elif fault == 'multiple':
        value['data'] *= 2
    elif fault == 'ignore_errors':
        value['data'][0][2] = True
    elif fault == 'secret_flag':
        value['data'][0][1] += ['--api-key', 'not-to-be-exported']
    elif fault == 'foreign_binary':
        value['data'][0][0] = '/unreviewed'
    def execute(command, deadline):
        assert command[:4] == ['/usr/bin/busctl', '--system', '--json=short', 'get-property']
        assert command[-1] == 'ExecStart'
        return json.dumps(value)
    system = SimpleNamespace(clock=lambda: 0, _execute=execute)
    if fault:
        with pytest.raises(ValueError):
            MODULE.model_argv(system, HELPER)
    else:
        assert MODULE.model_argv(system, HELPER) == ARGV


@pytest.mark.parametrize('extra,accepted', [
    (['--threads', '4', '--threads-batch', '4', '--parallel', '1', '--cache-ram', '0'], True),
    (['--cache-ram', '-1'], False),
    (['--threads-batch', 'four'], False),
    (['--cache-ram'], False),
    (['--threads-batch', '4', '--threads-batch', '6'], False),
    (['--cache-ram', '0', '--api-key', 'not-public'], False),
])
def test_cpu_profile_options_remain_numeric_unique_and_public(extra, accepted):
    argv = [*ARGV, *extra]
    value = dict(type='a(sasbttttuii)', data=[[ARGV[0], argv, False, 0, 0, 0, 0, 0, 0, 0]])
    system = SimpleNamespace(clock=lambda: 0, _execute=lambda *args: json.dumps(value))
    if accepted:
        assert MODULE.model_argv(system, HELPER) == argv
    else:
        with pytest.raises(ValueError):
            MODULE.model_argv(system, HELPER)


NPU_ARGV = ['/usr/bin/python3', '/opt/sage-lantern/npu/libexec/rkllm_server.py',
            '--engine', '/opt/sage-lantern/npu/bin/rkllm_engine',
            '--model', '/opt/sage-lantern/npu/models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm',
            '--host', '127.0.0.1', '--port', '8081', '--model-name', 'lantern-native5-2b',
            '--context', '2048', '--cpus', '4', '--max-new-tokens', '256',
            '--fallback-url', 'http://127.0.0.1:8082']


def npu_value(argv):
    return dict(type='a(sasbttttuii)', data=[[argv[0], list(argv), False, 0, 0, 0, 0, 0, 0, 0]])


def test_npu_server_argv_is_accepted_when_pinned():
    system = SimpleNamespace(clock=lambda: 0, _execute=lambda *args: json.dumps(npu_value(NPU_ARGV)))
    assert MODULE.model_argv(system, HELPER) == NPU_ARGV


@pytest.mark.parametrize('mutate', [
    lambda argv: [*argv, '--api-key', 'not-public'],
    lambda argv: [*argv, '--cpus', '8'],
    lambda argv: ['/usr/bin/python3.11', *argv[1:]],
    lambda argv: [('/tmp/engine' if item == '/opt/sage-lantern/npu/bin/rkllm_engine' else item) for item in argv],
    lambda argv: [('http://0.0.0.0:8082/' if item == 'http://127.0.0.1:8082' else item) for item in argv],
    lambda argv: [('12' if item == '4' else item) for item in argv],
    lambda argv: [item for item in argv if item != '--model-name' and item != 'lantern-native5-2b'],
    lambda argv: argv[:-1],
    lambda argv: [('8192' if item == '2048' else item) for item in argv],
], ids=['secret_flag', 'duplicate_cpus', 'foreign_interpreter', 'foreign_engine', 'foreign_fallback',
        'bad_cpus', 'missing_model_name', 'truncated', 'unbounded_context'])
def test_npu_server_argv_refuses_unpinned_or_malformed(mutate):
    argv = mutate(NPU_ARGV)
    system = SimpleNamespace(clock=lambda: 0, _execute=lambda *args: json.dumps(npu_value(argv)))
    with pytest.raises(ValueError):
        MODULE.model_argv(system, HELPER)


def test_npu_swap_supersedes_the_llama_cpp_baseline_once():
    model = {'FragmentPath': '/etc/systemd/system/sage-lantern-llm.service', 'DropInPaths': ''}
    retained = {'/etc/systemd/system/sage-lantern-llm.service': 'a' * 64,
                '/opt/sage-lantern/llama.cpp/llama-server': 'b' * 64,
                '/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf': 'c' * 64}
    helper = SimpleNamespace(file_hash=lambda name: 'd' * 64)
    files = MODULE.model_files(helper, NPU_ARGV, model, retained)
    assert set(files) == {model['FragmentPath'], NPU_ARGV[0],
                          NPU_ARGV[NPU_ARGV.index('--model') + 1]}
    with pytest.raises(ValueError):
        MODULE.model_files(helper, NPU_ARGV, model, {**retained, '/tmp/evil': 'e' * 64})
    with pytest.raises(ValueError):
        MODULE.model_files(helper, NPU_ARGV, model, {})


@pytest.mark.parametrize('fault', [None, 'wheel_hash', 'installed_changed', 'extra_source', 'missing_source'])
def test_runtime_must_match_exact_wheel_not_host(tmp_path, monkeypatch, fault):
    package = tmp_path / 'sage_lantern'
    package.mkdir()
    (tmp_path / 'sage_sdk').mkdir()
    (tmp_path / 'sage_sdk/__init__.py').write_bytes(b'sdk source')
    raw = b'reviewed runtime source'
    (package / '__init__.py').write_bytes(raw)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('sage_lantern/__init__.py', raw)
    wheel = tmp_path / 'runtime.whl'
    wheel.write_bytes(output.getvalue())
    monkeypatch.setattr(MODULE, 'R7', HELPER.sha(output.getvalue()))
    monkeypatch.setattr(MODULE, 'trusted', lambda path: Path(path).read_bytes())
    if fault == 'wheel_hash':
        monkeypatch.setattr(MODULE, 'R7', '0' * 64)
    elif fault == 'installed_changed':
        (package / '__init__.py').write_bytes(b'different runtime')
    elif fault == 'extra_source':
        (package / 'unreviewed.py').write_bytes(b'extra')
    elif fault == 'missing_source':
        (package / '__init__.py').unlink()
    if fault:
        with pytest.raises(ValueError):
            MODULE.runtime_sources(HELPER, wheel, tmp_path)
    else:
        result = MODULE.runtime_sources(HELPER, wheel, tmp_path)
        assert result[str(package / '__init__.py')] == HELPER.sha(raw)
        assert set(result) == {str(package / '__init__.py'), str(tmp_path / 'sage_sdk/__init__.py')}


def test_hash_mismatch_never_executes_helper(monkeypatch):
    monkeypatch.setattr(MODULE, 'trusted', lambda path: b'raise AssertionError("executed")')
    with pytest.raises(ValueError):
        MODULE.load_helper('/public/helper.py', '0' * 64)


@pytest.mark.parametrize('candidate', ['RETRIEVAL_V1', 'COMPACT_PROMPT_V1', 'OWNER_LEDGERS_V2'])
def test_reviewed_retrieval_wheel_is_not_a_general_wheel_bypass(tmp_path, monkeypatch, candidate):
    package = tmp_path / 'sage_lantern'
    package.mkdir()
    sdk = tmp_path / 'sage_sdk'
    sdk.mkdir()
    (package / '__init__.py').write_bytes(b'reviewed retrieval fixture')
    (sdk / '__init__.py').write_bytes(b'sdk fixture')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('sage_lantern/__init__.py', b'reviewed retrieval fixture')
    wheel = tmp_path / 'retrieval.whl'
    wheel.write_bytes(output.getvalue())
    monkeypatch.setattr(MODULE, 'trusted', lambda path: Path(path).read_bytes())
    monkeypatch.setattr(MODULE, candidate, HELPER.sha(output.getvalue()))
    assert MODULE.runtime_sources(HELPER, wheel, tmp_path)
    (package / '__init__.py').write_bytes(b'drift')
    with pytest.raises(ValueError):
        MODULE.runtime_sources(HELPER, wheel, tmp_path)


def test_no_apply_collector_interface():
    with pytest.raises(SystemExit):
        MODULE.main(['--apply'])


def test_main_revalidates_sources_and_outputs_only_expected(monkeypatch, capsys):
    expected = {'schema': 'fixture', 'historical_outcomes': 'unknown'}
    helper = SimpleNamespace(canonical=lambda value: json.dumps(value).encode())
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE.sys, 'prefix', '/opt/sage-lantern/venv')
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    monkeypatch.setattr(MODULE, 'trusted', lambda path: b'helper')
    monkeypatch.setattr(MODULE, 'load_helper', lambda *args: helper)
    calls = []
    def sources(*args):
        calls.append('sources')
        return {'installed-source': 'digest'}
    monkeypatch.setattr(MODULE, 'runtime_sources', sources)
    monkeypatch.setattr(MODULE, 'collect', lambda *args: expected)
    MODULE.main(['--helper', '/public/helper.py', '--helper-sha256', HELPER.sha(b'helper'),
                 '--runtime-wheel', '/public/runtime.whl'])
    assert calls == ['sources', 'sources']
    assert json.loads(capsys.readouterr().out) == expected


@pytest.fixture
def model_fixture(tmp_path, monkeypatch):
    names = ['unit', 'dropin', 'server', 'model.gguf', 'mmproj-BF16.gguf']
    paths = {name: tmp_path / name for name in names}
    for name, path in paths.items():
        path.write_bytes(('public-' + name).encode())
    projector = str(paths['mmproj-BF16.gguf'])
    monkeypatch.setattr(MODULE, 'PROJECTOR', projector)
    argv = [str(paths['server']), '--model', str(paths['model.gguf']), '--threads', '6']
    model = dict(FragmentPath=str(paths['unit']), DropInPaths=str(paths['dropin']))
    reads = []
    def file_hash(name):
        reads.append(name)
        return HELPER.sha(Path(name).read_bytes())
    helper = SimpleNamespace(file_hash=file_hash)
    baseline = {str(path): HELPER.sha(path.read_bytes()) for path in paths.values()}
    return helper, argv, model, baseline, projector, reads


def test_present_pinned_projector_without_mmproj_is_retained(model_fixture):
    helper, argv, model, baseline, projector, reads = model_fixture
    before = list(argv)
    assert '--mmproj' not in argv
    assert MODULE.model_files(helper, argv, model, baseline) == baseline
    assert projector in reads and argv == before


def test_unused_projector_is_not_invented_without_retained_pin(model_fixture):
    helper, argv, model, baseline, projector, reads = model_fixture
    expected = {name: digest for name, digest in baseline.items() if name != projector}
    assert MODULE.model_files(helper, argv, model) == expected
    assert MODULE.model_files(helper, argv, model, expected) == expected
    assert projector not in reads


@pytest.mark.parametrize('fault', ['missing', 'changed', 'wrong_pin'])
def test_retained_projector_missing_or_changed_refuses(model_fixture, fault):
    helper, argv, model, baseline, projector, _ = model_fixture
    if fault == 'missing':
        Path(projector).unlink()
    elif fault == 'changed':
        Path(projector).write_bytes(b'changed-public-projector')
    else:
        baseline[projector] = '0' * 64
    with pytest.raises((ValueError, FileNotFoundError)):
        MODULE.model_files(helper, argv, model, baseline)


@pytest.mark.parametrize('fault', ['foreign_extra', 'missing_required', 'short_hash', 'bool_hash'])
def test_retained_selection_is_not_an_arbitrary_file_allowlist(model_fixture, fault):
    helper, argv, model, baseline, projector, reads = model_fixture
    if fault == 'foreign_extra':
        baseline['/unapproved/artifact'] = 'a' * 64
    elif fault == 'missing_required':
        del baseline[argv[0]]
    elif fault == 'short_hash':
        baseline[projector] = '302b92'
    else:
        baseline[projector] = True
    with pytest.raises(ValueError):
        MODULE.model_files(helper, argv, model, baseline)
    assert reads == []


def test_enabled_projector_remains_required_by_argv(model_fixture):
    helper, argv, model, baseline, projector, _ = model_fixture
    argv += ['--mmproj', projector]
    assert MODULE.model_files(helper, argv, model) == baseline
    del baseline[projector]
    with pytest.raises(ValueError):
        MODULE.model_files(helper, argv, model, baseline)


def test_actual_baseline_validation_repro_with_unused_projector(model_fixture):
    helper, argv, model, baseline_files, projector, _ = model_fixture
    adapter_spec = importlib.util.spec_from_file_location('actual_adapter', Path(__file__).resolve().parents[1] /
                                                        'lantern/startup-audio-adapter.py')
    adapter = importlib.util.module_from_spec(adapter_spec)
    adapter_spec.loader.exec_module(adapter)
    baseline = {name: {} for name in adapter.STATIC}
    baseline.update(files=baseline_files, model_argv=list(argv))
    expected = dict(baseline, boot_id='test-boot', historical_outcomes='unknown', old_boot_attribution='unverified',
                    files=MODULE.model_files(helper, argv, model))
    with pytest.raises(ValueError):
        adapter.validate_current(expected, baseline, 'test-boot')
    expected['files'] = MODULE.model_files(helper, argv, model, baseline_files)
    adapter.validate_current(expected, baseline, 'test-boot')
    assert projector in expected['files'] and expected['model_argv'] == argv and '--mmproj' not in argv


@pytest.mark.parametrize('mode', ['legacy', 'journal', 'mutated', 'unknown', 'duplicate', 'wrong_service'])
def test_collected_bootstrap_exact_optional_journal(monkeypatch, mode):
    from sage_lantern import deployment_bringup as bootstrap
    raw = {name: b'synthetic' for name in bootstrap.REQUIRED}
    raw.update(bootstrap.AUDIO_INPUTS)
    journal = (Path(__file__).parents[2] / 'software/systemd/sage-lantern-mesh-runtime.service.d/25-radio-commission-journal.conf').read_bytes()
    raw[bootstrap.RADIO_JOURNAL] = journal + (b'\nReadWritePaths=/\n' if mode == 'mutated' else b'')
    monkeypatch.setattr(MODULE, 'trusted', lambda path: raw[str(path)])
    helper = SimpleNamespace(sha=HELPER.sha, BROKER=bootstrap.BROKER, VOICE=bootstrap.VOICE,
                             SYSTEMD=Path(bootstrap.SYSTEMD))
    def show(service, deadline, fields):
        paths = [name for name in bootstrap.REQUIRED if name.startswith(bootstrap.SYSTEMD + service + '.d/')]
        if mode != 'legacy' and service == (bootstrap.VOICE if mode == 'wrong_service' else bootstrap.BROKER):
            paths += [bootstrap.RADIO_JOURNAL]
        if service == bootstrap.BROKER:
            if mode == 'unknown':
                paths += [bootstrap.SYSTEMD + service + '.d/26-arbitrary.conf']
            if mode == 'duplicate':
                paths += [bootstrap.RADIO_JOURNAL]
        return dict(FragmentPath=bootstrap.SYSTEMD + service, NeedDaemonReload='no', DropInPaths=' '.join(paths))
    system = SimpleNamespace(clock=lambda: 0, _show=show)
    if mode in ('legacy', 'journal'):
        result = MODULE.bootstrap_inventory(helper, system)
        assert (bootstrap.RADIO_JOURNAL in result) == (mode == 'journal')
    else:
        with pytest.raises(Exception):
            MODULE.bootstrap_inventory(helper, system)
