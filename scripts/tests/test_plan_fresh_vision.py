import importlib.util
import itertools
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('fresh_vision', ROOT / 'scripts/lantern/plan-fresh-vision.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    original_lstat = Path.lstat
    original_fstat = os.fstat

    def root_owned(info):
        names = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        return SimpleNamespace(**{name: 0 if name == 'st_uid' else getattr(info, name) for name in names})

    monkeypatch.setattr(Path, 'lstat', lambda path: root_owned(original_lstat(path)))
    monkeypatch.setattr(os, 'fstat', lambda descriptor: root_owned(original_fstat(descriptor)))
    files = MODULE.InstalledFiles(tmp_path)

    def write(name, data):
        path = tmp_path / name.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        path.chmod(0o644)
        return path

    def configure(unit='unit-a', alsa='hw:CARD=UnitA,DEV=0'):
        write(MODULE.BINDINGS, json.dumps(dict(schema='sage.lantern.unit-plan.v1', unit_id=unit,
                                             operator_account='radxa', audio=dict(alsa_device=alsa))))
        write(MODULE.RECEIPT, json.dumps(dict(schema='sage.lantern.fresh-app-install.v1', unit_id=unit,
                                            installed=True, lock_required=True)))
        write(MODULE.METADATA, f'UNIT_ID={unit}\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n')
        for service in MODULE.BASE_HASHES:
            write(MODULE.SYSTEMD + service, (ROOT / 'software/systemd' / service).read_bytes())
        write(MODULE.SYSTEMD + MODULE.VOICE + '.d/30-unit-audio.conf', MODULE.voice_base(alsa))
        write(MODULE.SYSTEMD + MODULE.VOICE + '.d/40-mesh-broker.conf', MODULE.MESH)

    configure()
    return files, write, configure


@pytest.mark.parametrize('unit,alsa', [('unit-' + letter.lower(), 'hw:CARD=Unit' + letter + ',DEV=0') for letter in 'ABCDEF'])
def test_exact_fresh_candidates_preserve_binding_and_broker(installed, unit, alsa):
    files, write, configure = installed
    configure(unit, alsa)
    result = MODULE.plan(unit, files)
    (voice,) = result['candidates']
    assert voice['content'] == MODULE.voice_base(alsa).rstrip('\n') + ' --photo-ready\n'
    assert voice['content'].count('--photo-ready') == 1
    assert '--config /etc/sage-lantern/lantern.toml' in voice['content']
    assert '--drain-request /run/sage-lantern-mesh-control/voice-drain-request.json' in voice['content']
    assert result['enable'] == [MODULE.VLM]
    assert result['keep_disabled'] == [MODULE.CPU_LLM]
    assert all(MODULE.CPU_LLM + '.d/' not in item['destination'] for item in result['candidates'])
    assert all(MODULE.LLM + '.d/' not in item['destination'] for item in result['candidates'])
    assert result['serving_profile'] == 'npu-text-8081-npu-vision-8082'
    assert result['preserve'] == sorted(MODULE.expected_dropin_paths())
    assert result['mode'] == 'plan-only'
    assert result['readiness_verified'] is result['activation_authorized'] is result['installed_files_written'] is False
    assert len(result['input_sha256']) == 9
    assert len(result['evidence_requirements']['required']) >= 6
    assert set(result['evidence_requirements']) == {
        'npu_model', 'vision_encoder', 'vision_llm', 'engine', 'required'}
    assert result['evidence_requirements']['vision_encoder']['sha256'] == (
        'e96a3f016163e7cf7ae516ed9878ccad7c576c658d71a60333f5cb154680fd81')
    assert result['evidence_requirements']['vision_llm']['sha256'] == (
        'ba85edac97e8f4ed2cdf0d94a3744b5609385b4706c45677f55cdaeb465ce054')
    assert result == MODULE.plan(unit, files)


def test_reviewed_local_question_gate_is_verified_and_preserved(installed):
    files, write, configure = installed
    write(MODULE.QUESTION_GATE, MODULE.QUESTION_GATE_CONTENT)
    result = MODULE.plan('unit-a', files)
    assert MODULE.QUESTION_GATE in result['preserve']
    assert result['input_sha256'][MODULE.QUESTION_GATE] == MODULE.digest(MODULE.QUESTION_GATE_CONTENT.encode())
    assert len(result['input_sha256']) == 10
    # The gate is an activation, not a candidate: the plan still proposes the
    # same one voice drop-in and never rewrites the local question lane.
    assert all(item['destination'] != MODULE.QUESTION_GATE for item in result['candidates'])
    assert result['local_question_lane'] is True
    assert result['voice_sleep_profile'] == 'standard'


def test_question_gate_constant_matches_the_reviewed_release_file():
    # The planner runs on a unit, so its expectation is a constant; the reviewed
    # bytes live once in the release tree. Drift between them is a defect.
    reviewed = (ROOT / 'software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf').read_text()
    assert reviewed == MODULE.QUESTION_GATE_CONTENT
    assert MODULE.QUESTION_GATE.endswith('/sage-lantern-voice-loop.service.d/60-local-question-lane.conf')


def test_cf_non_mesh_voice_sleep_profile_is_reviewed_and_recorded(installed, sleep_payloads):
    files, write, configure = installed
    alsa = 'hw:CARD=UnitC,DEV=0'
    configure('unit-c', alsa)
    payloads = sleep_payloads(alsa)
    for name, content in payloads.items():
        write(name, content)
    assert payloads[MODULE.SLEEP_VOICE].decode() == MODULE.voice_sleep(alsa)
    write(MODULE.SLEEP_VOICE, MODULE.cf_voice_sleep(alsa))
    result = MODULE.plan('unit-c', files)
    assert result['voice_sleep_profile'] == 'non-mesh-broker'
    assert result['local_question_lane'] is False
    # A third variant of the same drop-in is still refused by name.
    write(MODULE.SLEEP_VOICE,
          MODULE.cf_voice_sleep(alsa).replace('SAGE_LANTERN_MESH_BROKER=0', 'SAGE_LANTERN_MESH_BROKER=1'))
    with pytest.raises(ValueError, match='sleep override mismatch'):
        MODULE.plan('unit-c', files)


def test_planner_sleep_bytes_are_the_installers_bytes(tmp_path):
    """The planner's expectation and both installers are one artifact.

    The planner decides whether an installed sleep drop-in is reviewed; the
    installers generate it. If they drift, a unit that was installed by the
    reviewed installer is refused by the planner (or the reverse).
    """
    alsa = 'hw:CARD=UnitC,DEV=0'
    bindings = tmp_path / 'appliance-bindings.json'
    bindings.write_text(json.dumps({'audio': {'alsa_device': alsa}}) + '\n')
    for filename, expected in (('install-idle-suspend-local.py', MODULE.voice_sleep),
                               ('install-cf-idle-suspend-local.py', MODULE.cf_voice_sleep)):
        spec = importlib.util.spec_from_file_location('installer_' + filename.replace('-', '_'),
                                                      ROOT / 'scripts/lantern' / filename)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        _, payloads = installer.build_plan(ROOT, bindings=bindings)
        assert payloads[MODULE.SLEEP_VOICE].decode() == expected(alsa)


def test_changed_or_foreign_question_lane_override_is_refused(installed):
    files, write, configure = installed
    changed = MODULE.QUESTION_GATE_CONTENT.replace('Restart=on-failure', 'Restart=no')
    write(MODULE.QUESTION_GATE, changed)
    with pytest.raises(ValueError, match='local question lane override mismatch'):
        MODULE.plan('unit-a', files)
    write(MODULE.QUESTION_GATE, MODULE.QUESTION_GATE_CONTENT)
    assert MODULE.plan('unit-a', files)['preserve'].count(MODULE.QUESTION_GATE) == 1
    write(MODULE.SYSTEMD + MODULE.VOICE + '.d/61-unreviewed.conf', '[Service]\n')
    with pytest.raises(ValueError, match='unknown systemd override'):
        MODULE.plan('unit-a', files)


@pytest.mark.parametrize('alsa', ['hw:CARD=Audio,DEV=0 --photo-ready', 'default', 'hw:0,0',
                                'hw:CARD=Audio,DEV=0\nExecStart=/evil', 'hw:CARD=%i,DEV=0', None])
def test_invalid_audio_cannot_inject_command(installed, alsa):
    files, write, configure = installed
    write(MODULE.BINDINGS, json.dumps(dict(schema='sage.lantern.unit-plan.v1', unit_id='unit-a',
                                         operator_account='radxa', audio=dict(alsa_device=alsa))))
    with pytest.raises(ValueError, match='ALSA'):
        MODULE.plan('unit-a', files)


@pytest.mark.parametrize('unit,alsa', [('unit-' + letter.lower(), 'hw:CARD=Unit' + letter + ',DEV=0') for letter in 'ABCDEF'])
@pytest.mark.parametrize('sleep', [False, True])
def test_guarded_audio_closure_preserves_exact_fences(installed, unit, alsa, sleep):
    files, write, configure = installed
    configure(unit, alsa)
    for name, content in MODULE.AUDIO_INPUTS.items():
        write(name, content)
    if sleep:
        for name, content in MODULE.sleep_dropins(alsa).items():
            write(name, content)
    result = MODULE.plan(unit, files, fresh_audio=True)
    expected = MODULE.expected_dropin_paths() | MODULE.AUDIO_INPUTS.keys()
    if sleep:
        expected |= set(MODULE.SLEEP_DROPIN_PATHS)
    assert result['preserve'] == sorted(expected)
    assert result['audio_profile'] == 'commissioned-guard'
    for name in expected:
        assert result['input_sha256'][name] == MODULE.digest(files.read(name))
    assert result['activation_authorized'] is result['readiness_verified'] is False
    assert result['candidates'][0]['content'] == MODULE.voice_base(alsa).rstrip('\n') + ' --photo-ready\n'
    assert result == MODULE.plan(unit, files)


def test_new_fresh_mode_requires_audio_pair_but_old_plan_is_explicit(installed):
    files, write, configure = installed
    assert MODULE.plan('unit-a', files)['audio_profile'] == 'pre-commissioning'
    with pytest.raises(ValueError, match='fresh audio inputs required'):
        MODULE.plan('unit-a', files, fresh_audio=True)


@pytest.mark.parametrize('fresh_audio', [False, True])
@pytest.mark.parametrize('name', list(MODULE.AUDIO_INPUTS))
@pytest.mark.parametrize('fault', ['missing', 'changed', 'link'])
def test_audio_partial_or_mutated_pair_never_accepted(installed, fresh_audio, name, fault):
    files, write, configure = installed
    for path, content in MODULE.AUDIO_INPUTS.items():
        write(path, content)
    target = files.root / name.lstrip('/')
    if fault == 'missing':
        target.unlink()
    elif fault == 'changed':
        write(name, MODULE.AUDIO_INPUTS[name] + '\n')
    else:
        target.unlink()
        target.symlink_to(files.root / MODULE.BINDINGS.lstrip('/'))
    with pytest.raises(ValueError):
        MODULE.plan('unit-a', files, fresh_audio=fresh_audio)


def test_audio_constants_match_frozen_runtime_contract():
    import ast
    source = ast.parse((ROOT / 'software/runtime/src/sage_lantern/audio_readiness.py').read_text())
    names = {'VOICE', 'BROKER', 'PYTHON', 'GUARD_PATH', 'ALSA_PATH', 'ALSA_CONFIG', 'GUARD'}
    constants = ast.Module(body=[node for node in source.body if isinstance(node, ast.Assign)
                                and any(isinstance(target, ast.Name) and target.id in names
                                        for target in node.targets)], type_ignores=[])
    namespace = {'Path': Path}
    exec(compile(constants, '<audio constants>', 'exec'), namespace)
    assert MODULE.AUDIO_INPUTS == {
        str(namespace['GUARD_PATH']): namespace['GUARD'],
        str(namespace['ALSA_PATH']): namespace['ALSA_CONFIG'],
    }


def test_alsa_appearance_during_old_profile_planning_rejected(installed, monkeypatch):
    files, write, configure = installed
    original = files.inventory
    calls = []
    def inventory():
        calls.append(True)
        if len(calls) == 2:
            write(MODULE.ALSA_PATH, MODULE.AUDIO_INPUTS[MODULE.ALSA_PATH])
        return original()
    monkeypatch.setattr(files, 'inventory', inventory)
    with pytest.raises(ValueError, match='changed during planning'):
        MODULE.plan('unit-a', files)


@pytest.mark.parametrize('name', [MODULE.BINDINGS, MODULE.RECEIPT, MODULE.METADATA])
def test_mismatched_or_missing_unit_records_reject(installed, name):
    files, write, configure = installed
    path = files.root / name.lstrip('/')
    write(name, path.read_bytes().replace(b'unit-a', b'unit-b'))
    with pytest.raises(ValueError, match='mismatch'):
        MODULE.plan('unit-a', files)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        MODULE.plan('unit-a', files)


def test_duplicate_json_and_false_installation_reject(installed):
    files, write, configure = installed
    write(MODULE.BINDINGS, '{"unit_id":"unit-a","unit_id":"unit-b"}')
    with pytest.raises(ValueError, match='duplicate'):
        MODULE.plan('unit-a', files)
    configure()
    write(MODULE.RECEIPT, files.read(MODULE.RECEIPT).replace(b'true', b'false'))
    with pytest.raises(ValueError, match='mismatch'):
        MODULE.plan('unit-a', files)


@pytest.mark.parametrize('filename', ['30-unit-audio.conf', '40-mesh-broker.conf'])
@pytest.mark.parametrize('mutation', ['append', 'remove', 'change'])
def test_exact_audio_and_mesh_bytes_required(installed, filename, mutation):
    files, write, configure = installed
    name = MODULE.SYSTEMD + MODULE.VOICE + '.d/' + filename
    if mutation == 'remove':
        (files.root / name.lstrip('/')).unlink()
    else:
        data = files.read(name)
        write(name, data + b'\n' if mutation == 'append' else data.replace(b'sage-lantern', b'other-lantern'))
    with pytest.raises((ValueError, FileNotFoundError)):
        MODULE.plan('unit-a', files)


@pytest.mark.parametrize('directory', MODULE.UNIT_ROOTS)
@pytest.mark.parametrize('dropin_dir', MODULE.DROPIN_DIRS)
def test_unknown_overrides_in_all_reviewed_search_roots_reject(installed, directory, dropin_dir):
    files, write, configure = installed
    write(directory + '/' + dropin_dir + '/99-extra.conf', '[Service]\nEnvironment=OTHER=1\n')
    with pytest.raises(ValueError, match='unknown systemd override'):
        MODULE.plan('unit-a', files)


@pytest.mark.parametrize('service', [MODULE.VOICE, MODULE.LLM, MODULE.CPU_LLM, MODULE.VLM])
def test_changed_base_and_shadow_services_reject(installed, service):
    files, write, configure = installed
    name = MODULE.SYSTEMD + service
    write(name, files.read(name) + b'\n')
    with pytest.raises(ValueError, match='base service'):
        MODULE.plan('unit-a', files)
    configure()
    write('/run/systemd/system/' + service, '[Service]\nExecStart=/other\n')
    with pytest.raises(ValueError, match='service shadow'):
        MODULE.plan('unit-a', files)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'writable', 'oversized', 'parent-writable', 'parent-symlink'])
def test_unsafe_files_and_parents_reject(installed, kind):
    files, write, configure = installed
    path = files.root / MODULE.BINDINGS.lstrip('/')
    if kind == 'symlink':
        path.unlink()
        path.symlink_to('appliance-installed.json')
    elif kind == 'hardlink':
        os.link(path, path.with_name('alias.json'))
    elif kind == 'writable':
        path.chmod(0o666)
    elif kind == 'oversized':
        path.write_bytes(b'x' * (MODULE.MAX_FILE + 1))
    elif kind == 'parent-writable':
        path.parent.chmod(0o777)
    else:
        parent = path.parent
        parent.rename(parent.with_name('renamed'))
        parent.symlink_to('renamed')
    with pytest.raises(ValueError):
        MODULE.plan('unit-a', files)


def test_nonroot_opened_file_rejected(installed, monkeypatch):
    files, write, configure = installed
    original = os.fstat
    def untrusted(descriptor):
        info = original(descriptor)
        info.st_uid = 1000
        return info
    monkeypatch.setattr(os, 'fstat', untrusted)
    with pytest.raises(ValueError, match='unsafe installed file'):
        files.read(MODULE.BINDINGS)


def test_changed_inputs_or_new_override_during_plan_reject(installed, monkeypatch):
    files, write, configure = installed
    original = files.inventory
    calls = 0
    def inventory():
        nonlocal calls
        calls += 1
        if calls == 2:
            write(MODULE.SYSTEMD + MODULE.VOICE + '.d/99-race.conf', '[Service]\nRestart=always\n')
        return original()
    monkeypatch.setattr(files, 'inventory', inventory)
    with pytest.raises(ValueError, match='unknown systemd override'):
        MODULE.plan('unit-a', files)


def test_changed_public_binding_on_final_read_rejects(installed, monkeypatch):
    files, write, configure = installed
    original = files.read
    calls = 0
    def read(name):
        nonlocal calls
        if name == MODULE.BINDINGS:
            calls += 1
            if calls == 2:
                write(name, original(name) + b'\n')
        return original(name)
    monkeypatch.setattr(files, 'read', read)
    with pytest.raises(ValueError, match='changed during planning'):
        MODULE.plan('unit-a', files)


def test_usrmerge_alias_checked_without_ignoring_vendor_overrides(installed):
    files, write, configure = installed
    (files.root / 'usr/lib/systemd/system').mkdir(parents=True)
    (files.root / 'lib').symlink_to('usr/lib')
    assert MODULE.plan('unit-a', files)['mode'] == 'plan-only'
    write('/usr/lib/systemd/system/service.d/99-extra.conf', '[Service]\nRestart=always\n')
    with pytest.raises(ValueError, match='unknown systemd override'):
        MODULE.plan('unit-a', files)


def test_unreviewed_lib_alias_rejected(installed):
    files, write, configure = installed
    (files.root / 'lib').symlink_to('other')
    with pytest.raises(ValueError, match='unreviewed lib alias'):
        MODULE.plan('unit-a', files)


def test_cli_only_prints_no_writes_execution_or_readiness(installed, monkeypatch, capsys):
    files, write, configure = installed
    monkeypatch.setattr(MODULE, 'InstalledFiles', lambda: files)
    original = os.open
    def read_only(path, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', read_only)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: pytest.fail('must not execute commands'))
    assert MODULE.main(['--unit-id', 'unit-a']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['readiness_verified'] is False
    assert set(path.name for path in (files.root / 'etc/sage-lantern').iterdir()) == {
        'appliance-bindings.json', 'appliance-installed.json', 'unit.env'}


@pytest.mark.parametrize('argument', ['--apply', '--enable', '--output', '--root', '--unit'])
def test_cli_rejects_activation_output_and_alternate_root_options(argument):
    with pytest.raises(SystemExit) as error:
        MODULE.main(['--unit-id', 'unit-a', argument])
    assert error.value.code == 2


@pytest.fixture
def sleep_payloads():
    """Build the A/B installer's sleep payloads for one unit's capture binding."""
    spec = importlib.util.spec_from_file_location('sleep_generator', ROOT / 'scripts/lantern/install-idle-suspend-local.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

    def build(alsa):
        with tempfile.TemporaryDirectory() as temporary:
            bindings = Path(temporary) / 'appliance-bindings.json'
            bindings.write_text(json.dumps({'audio': {'alsa_device': alsa}}) + '\n')
            _, payloads = installer.build_plan(ROOT, bindings=bindings)
        return {name: payloads[name] for name in MODULE.SLEEP_DROPIN_PATHS}

    return build


@pytest.mark.parametrize('unit,alsa', [('unit-' + letter.lower(), 'hw:CARD=Unit' + letter + ',DEV=0') for letter in 'ABCDEF'])
def test_reviewed_generated_sleep_preserved_with_later_fresh_voice(installed, sleep_payloads, unit, alsa):
    files, write, configure = installed
    configure(unit, alsa)
    payloads = sleep_payloads(alsa)
    for name, content in payloads.items():
        assert content == MODULE.sleep_dropins(alsa)[name].encode()
        write(name, content)
    result = MODULE.plan(unit, files)
    assert result['preserve'] == sorted(MODULE.expected_dropin_paths() | payloads.keys())
    assert len(result['input_sha256']) == 14
    for name, content in payloads.items():
        assert result['input_sha256'][name] == MODULE.digest(content)
        assert files.read(name) == content
    (voice,) = result['candidates']
    assert voice['content'] == MODULE.voice_base(alsa).rstrip('\n') + ' --photo-ready\n'
    assert voice['destination'].endswith('/95-lantern-local-vision.conf')
    assert Path(voice['destination']).name > '90-sleep-baseline.conf'
    assert 'ConditionPathExists=' not in voice['content']
    assert 'Environment=' not in voice['content']
    assert 'ExecStartPost=' not in voice['content']
    assert 'ReadWritePaths=' not in voice['content']
    assert 'Restart=' not in voice['content']
    assert result['activation_authorized'] is result['readiness_verified'] is False
    assert result == MODULE.plan(unit, files)


@pytest.mark.parametrize('selected', [subset for count in (1, 2)
                                    for subset in itertools.combinations(MODULE.SLEEP_DROPIN_PATHS, count)])
def test_partial_sleep_configuration_rejected(installed, sleep_payloads, selected):
    files, write, configure = installed
    payloads = sleep_payloads('hw:CARD=UnitA,DEV=0')
    for name in selected:
        write(name, payloads[name])
    with pytest.raises(ValueError, match='incomplete reviewed sleep'):
        MODULE.plan('unit-a', files)


@pytest.mark.parametrize('name', MODULE.SLEEP_DROPIN_PATHS)
@pytest.mark.parametrize('mutation', ['append', 'clear-fence', 'change-path'])
def test_sleep_bytes_not_merely_names_checked(installed, sleep_payloads, name, mutation):
    files, write, configure = installed
    payloads = sleep_payloads('hw:CARD=UnitA,DEV=0')
    for path, content in payloads.items():
        write(path, content)
    content = payloads[name]
    if mutation == 'append':
        content += b'\n'
    elif mutation == 'clear-fence':
        content += b'[Unit]\nConditionPathExists=\n'
    else:
        content = content.replace(b'sage-lantern', b'other-lantern')
    write(name, content)
    with pytest.raises(ValueError, match='sleep override mismatch'):
        MODULE.plan('unit-a', files)


def test_sleep_does_not_allow_unknown_later_vision_or_fence_variants(installed, sleep_payloads):
    files, write, configure = installed
    for name, content in sleep_payloads('hw:CARD=UnitA,DEV=0').items():
        write(name, content)
    write(MODULE.SYSTEMD + MODULE.VOICE + '.d/99-sage-lantern-sleep.conf', MODULE.SLEEP_FENCE)
    with pytest.raises(ValueError, match='unknown systemd override'):
        MODULE.plan('unit-a', files)


def test_sleep_inputs_revalidated_before_return(installed, sleep_payloads, monkeypatch):
    files, write, configure = installed
    payloads = sleep_payloads('hw:CARD=UnitA,DEV=0')
    for name, content in payloads.items():
        write(name, content)
    original = files.inventory
    calls = 0
    def inventory():
        nonlocal calls
        calls += 1
        if calls == 2:
            name = next(iter(payloads))
            write(name, payloads[name] + b'\n')
        return original()
    monkeypatch.setattr(files, 'inventory', inventory)
    with pytest.raises(ValueError, match='changed during planning'):
        MODULE.plan('unit-a', files)
