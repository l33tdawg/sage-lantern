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


BUILD = load('console_build', 'scripts/lantern/build-console-bundle-local.py')
DEPLOY = load('console_deploy', 'scripts/lantern/deploy-staged-console-bundle-local.py')


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A synthetic app checkout with a built dist/ tree."""
    root = tmp_path / 'src'
    (root / 'apps/lantern-fleet-admin/app/device').mkdir(parents=True)
    (root / 'apps/lantern-fleet-admin/lib').mkdir(parents=True)
    (root / 'apps/lantern-fleet-admin/dist/server').mkdir(parents=True)
    (root / 'apps/lantern-fleet-admin/dist/client/_next/static').mkdir(parents=True)
    (root / 'apps/lantern-fleet-admin/public').mkdir(parents=True)
    (root / 'scripts/lantern').mkdir(parents=True)
    (root / 'apps/lantern-fleet-admin/app/device/screen.tsx').write_text('export default 1;\n')
    (root / 'apps/lantern-fleet-admin/lib/device-state.ts').write_text('export const x = 1;\n')
    (root / 'apps/lantern-fleet-admin/dist/server/index.js').write_text('// server bundle\n')
    (root / 'apps/lantern-fleet-admin/dist/server/BUILD_ID').write_text('synthetic-build\n')
    (root / 'apps/lantern-fleet-admin/dist/client/_next/static/app.js').write_text('// client\n')
    (root / 'apps/lantern-fleet-admin/public/favicon.svg').write_text('<svg/>\n')
    (root / 'apps/lantern-fleet-admin/package.json').write_text('{"name":"app"}\n')
    (root / 'apps/lantern-fleet-admin/package-lock.json').write_text('{"lockfileVersion":3}\n')
    (root / 'apps/lantern-fleet-admin/next.config.ts').write_text('export default {};\n')
    (root / 'apps/lantern-fleet-admin/vite.config.ts').write_text('export default {};\n')
    (root / 'scripts/lantern/unit-a-voice-loop.py').write_text('#!/usr/bin/env python3\n')
    gate = root / 'software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf'
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_bytes((ROOT / 'software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf').read_bytes())
    monkeypatch.setattr(BUILD, 'ROOT', root)
    monkeypatch.setattr(BUILD, 'APP', root / 'apps/lantern-fleet-admin')
    monkeypatch.setattr(BUILD, 'VOICE_SOURCE', root / 'scripts/lantern/unit-a-voice-loop.py')
    monkeypatch.setattr(BUILD, 'QUESTION_GATE_SOURCE', gate)
    return root


def stage_bundle(app, tmp_path, name='bundle', *, gate=True):
    stage = tmp_path / name
    argv = ['--stage', str(stage), '--no-build']
    if not gate:
        argv.append('--no-question-gate')
    assert BUILD.main(argv) == 0
    return stage


def predecessor_for(app, tmp_path, name='predecessor'):
    """A released payload tree that matches what the unit already runs."""
    root = tmp_path / name
    target = root / 'apps/lantern-fleet-admin'
    target.mkdir(parents=True)
    for relative in ('dist/server/index.js', 'dist/server/BUILD_ID', 'dist/client/_next/static/app.js',
                     'public/favicon.svg', 'package.json', 'package-lock.json'):
        source = app / 'apps/lantern-fleet-admin' / relative
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        (target / relative).write_bytes(source.read_bytes())
    (root / 'scripts/lantern').mkdir(parents=True)
    (root / 'scripts/lantern/unit-a-voice-loop.py').write_text('#!/usr/bin/env python3\n')
    return root


def installed_root(app, tmp_path, predecessor, name='unit'):
    """A synthetic installed unit: the app files plus the question loop."""
    root = tmp_path / name
    for relative in ('dist/server/index.js', 'dist/server/BUILD_ID', 'dist/client/_next/static/app.js',
                     'public/favicon.svg', 'package.json', 'package-lock.json'):
        target = root / 'opt/sage-lantern/fleet-admin' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((predecessor / 'apps/lantern-fleet-admin' / relative).read_bytes())
    loop = root / 'usr/local/libexec'
    loop.mkdir(parents=True)
    (loop / 'sage-lantern-voice-loop').write_text('#!/usr/bin/env python3\n')
    return root


def deploy_bundle(stage, backup, root, **overrides):
    options = dict(root=root, predecessor=None, accepted=(), owner=os.geteuid(),
                   restart_app=False, run=lambda argv: subprocess.CompletedProcess(argv, 0, b''))
    options.update(overrides)
    return DEPLOY.deploy(stage, stage / 'manifest.json', backup, **options)


def test_stage_records_every_ui_file_and_the_question_gate(app, tmp_path):
    stage = stage_bundle(app, tmp_path)
    plan = json.loads((stage / 'manifest.json').read_text())
    assert plan['schema'] == 'sage.lantern.console-bundle.v1'
    assert plan['mode'] == 'stage-only' and plan['activation'] == {'local_question_lane': True}
    assert plan['app_build_id'] == 'synthetic-build'
    destinations = {item['destination'] for item in plan['files']}
    assert '/opt/sage-lantern/fleet-admin/dist/server/index.js' in destinations
    assert '/opt/sage-lantern/fleet-admin/package-lock.json' in destinations
    assert '/usr/local/libexec/sage-lantern-voice-loop' in destinations
    assert DEPLOY.QUESTION_GATE in destinations
    assert not any('node_modules' in item['destination'] for item in plan['files'])
    for item in plan['files']:
        data = (stage / item['artifact']).read_bytes()
        assert len(data) == item['size']
        assert hashlib.sha256(data).hexdigest() == item['sha256']
    # The gate resets the mesh start condition and the audio start condition.
    gate = (stage / plan['files'][-1]['artifact']).read_text()
    assert 'ConditionPathExists=\n' in gate and 'ExecCondition=\n' in gate
    assert 'Restart=on-failure' in gate and 'LOCAL_VOICE_READY' in gate


def test_stage_requires_an_empty_destination_and_refuses_missing_dist(app, tmp_path):
    stage = tmp_path / 'twice'
    assert BUILD.main(['--stage', str(stage), '--no-build']) == 0
    with pytest.raises(SystemExit) as second:
        BUILD.main(['--stage', str(stage), '--no-build'])
    assert str(second.value) == 'stage_exists'
    (app / 'apps/lantern-fleet-admin/dist').rename(app / 'apps/lantern-fleet-admin/dist-away')
    with pytest.raises(SystemExit) as missing:
        BUILD.main(['--stage', str(tmp_path / 'fresh'), '--no-build'])
    assert str(missing.value) == 'app_build_missing: dist'


def test_staged_gate_is_the_reviewed_release_file(app, tmp_path, monkeypatch):
    # The builder no longer carries its own copy of the gate: the staged bytes
    # are the reviewed release-tree file, byte for byte.
    reviewed = (ROOT / 'software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf').read_bytes()
    assert BUILD.QUESTION_GATE_SOURCE.is_file()
    assert BUILD.QUESTION_GATE_SOURCE.read_bytes() == reviewed
    stage = stage_bundle(app, tmp_path)
    plan = json.loads((stage / 'manifest.json').read_text())
    staged = [item for item in plan['files'] if item['destination'] == DEPLOY.QUESTION_GATE]
    assert len(staged) == 1
    assert (stage / staged[0]['artifact']).read_bytes() == reviewed
    # A release tree without the reviewed gate cannot stage one silently.
    monkeypatch.setattr(BUILD, 'QUESTION_GATE_SOURCE', app / 'software/absent.conf')
    with pytest.raises(SystemExit) as missing:
        BUILD.main(['--stage', str(tmp_path / 'no-gate'), '--no-build'])
    assert str(missing.value) == 'question_gate_source_missing'


def test_deploy_installs_only_verified_destinations_and_records_backups(app, tmp_path):
    stage = stage_bundle(app, tmp_path)
    predecessor = predecessor_for(app, tmp_path)
    root = installed_root(app, tmp_path, predecessor)
    backup = tmp_path / 'backups/console-1'
    calls = []

    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b'')

    receipt = deploy_bundle(stage, backup, root, predecessor=predecessor, run=run)
    assert receipt['activated'] is False and receipt['app_restarted'] is False
    assert receipt['replaced_unverifiable_targets'] == []
    assert receipt['mesh_voice_state'] == 'unknown'
    assert len(receipt['files']) == len(json.loads((stage / 'manifest.json').read_text())['files'])
    assert (backup / 'receipt.json').is_file()
    # Every destination that already existed is backed up; the question gate is new.
    assert len(receipt['backups']) == len(receipt['files']) - 1
    for entry in receipt['backups']:
        assert Path(entry['path']).is_file()
        assert entry['path'].startswith(str(backup))
    assert calls == [[DEPLOY.SYSTEMCTL, 'daemon-reload']]
    # The installed question loop is executable and the app is replaced in place.
    assert (root / 'usr/local/libexec/sage-lantern-voice-loop').read_text().startswith('#!')
    assert (root / DEPLOY.QUESTION_GATE.lstrip('/')).is_file()


def test_deploy_refuses_unanchored_replacements_until_accepted(app, tmp_path):
    stage = stage_bundle(app, tmp_path)
    predecessor = predecessor_for(app, tmp_path)
    root = installed_root(app, tmp_path, predecessor)
    drifted = root / 'opt/sage-lantern/fleet-admin/dist/server/index.js'
    drifted.write_text('// hand-edited on the unit\n')
    with pytest.raises(DEPLOY.Refused) as refusal:
        deploy_bundle(stage, tmp_path / 'backups/console-2', root, predecessor=predecessor)
    assert refusal.value.gate == 'destination_differs_from_predecessor'
    receipt = deploy_bundle(stage, tmp_path / 'backups/console-3', root, predecessor=predecessor,
                            accepted=[str(drifted)])
    recorded = receipt['replaced_unverifiable_targets'][0]
    assert recorded['destination'] == '/opt/sage-lantern/fleet-admin/dist/server/index.js'
    assert recorded['observed_sha256'] == hashlib.sha256(b'// hand-edited on the unit\n').hexdigest()


def test_anchored_replacement_is_not_recorded_as_unverifiable(app, tmp_path):
    stage = stage_bundle(app, tmp_path)
    # The release payload keeps the previous build; the bundle carries the new one.
    predecessor = predecessor_for(app, tmp_path)
    (app / 'apps/lantern-fleet-admin/dist/server/index.js').write_text('// rebuilt\n')
    rebuilt = stage_bundle(app, tmp_path, name='bundle-2')
    root = installed_root(app, tmp_path, predecessor)
    receipt = deploy_bundle(rebuilt, tmp_path / 'backups/console-9', root, predecessor=predecessor)
    assert receipt['replaced_unverifiable_targets'] == []
    changed = [entry for entry in receipt['files']
               if entry['destination'].endswith('dist/server/index.js')]
    assert len(changed) == 1
    assert changed[0]['replaced_sha256'] == hashlib.sha256(b'// server bundle\n').hexdigest()
    assert (root / 'opt/sage-lantern/fleet-admin/dist/server/index.js').read_text() == '// rebuilt\n'


def test_release_path_mapping_is_required_to_be_relative(app, tmp_path):
    predecessor = predecessor_for(app, tmp_path)
    with pytest.raises(DEPLOY.Refused) as refusal:
        DEPLOY.anchor_path(predecessor, {'release_path': '../../etc/passwd'})
    assert refusal.value.gate == 'unsafe_release_path'
    assert DEPLOY.anchor_path(predecessor, {'release_path': None}) is None
    assert DEPLOY.anchor_path(predecessor, {'release_path': 'scripts/lantern/unit-a-voice-loop.py'}) is not None


def test_second_bundle_is_anchored_on_the_previous_stage(app, tmp_path):
    predecessor = predecessor_for(app, tmp_path)
    root = installed_root(app, tmp_path, predecessor)
    # The first local console update is anchored on the release payload.
    (app / 'apps/lantern-fleet-admin/dist/server/index.js').write_text('// first build\n')
    first = stage_bundle(app, tmp_path, name='first')
    deploy_bundle(first, tmp_path / 'backups/console-10', root, predecessor=predecessor)
    # The unit now runs the first bundle, so the release payload no longer matches.
    (app / 'apps/lantern-fleet-admin/dist/server/index.js').write_text('// second build\n')
    second = stage_bundle(app, tmp_path, name='second')
    with pytest.raises(DEPLOY.Refused) as refusal:
        deploy_bundle(second, tmp_path / 'backups/console-11', root, predecessor=predecessor)
    assert refusal.value.gate == 'destination_differs_from_predecessor'
    receipt = deploy_bundle(second, tmp_path / 'backups/console-12', root,
                            predecessor_stage=first, predecessor=predecessor)
    assert receipt['replaced_unverifiable_targets'] == []
    assert (root / 'opt/sage-lantern/fleet-admin/dist/server/index.js').read_text() == '// second build\n'
    assert DEPLOY.stage_anchors(first)['/usr/local/libexec/sage-lantern-voice-loop'].is_file()
    with pytest.raises(DEPLOY.Refused, match='predecessor_stage_unavailable'):
        DEPLOY.stage_anchors(tmp_path / 'missing')


def test_builder_freezes_the_release_anchor_for_one_tarball(app, tmp_path):
    predecessor = predecessor_for(app, tmp_path)
    stage = tmp_path / 'anchored'
    assert BUILD.main(['--stage', str(stage), '--no-build', '--anchor-from', str(predecessor)]) == 0
    plan = json.loads((stage / 'manifest.json').read_text())
    assert plan['anchor']['files'] == 7          # six app destinations plus the question loop
    assert plan['anchor']['absent'] == 0
    assert plan['anchor']['sha256'] and plan['anchor']['payload'] == str(predecessor)
    assert (stage / 'anchor/apps/lantern-fleet-admin/dist/server/index.js').is_file()
    assert (stage / 'anchor/scripts/lantern/unit-a-voice-loop.py').is_file()
    # The gate has no release path, so it is deliberately absent from the anchor.
    assert not (stage / 'anchor/etc').exists()
    # A newly built asset the old payload does not carry is recorded as absent:
    # the deployer still refuses it if the unit happens to have it.
    (app / 'apps/lantern-fleet-admin/dist/client/_next/static/new-asset.js').write_text('// new\n')
    rebuilt = tmp_path / 'rebuilt'
    assert BUILD.main(['--stage', str(rebuilt), '--no-build', '--anchor-from', str(predecessor)]) == 0
    rebuilt_plan = json.loads((rebuilt / 'manifest.json').read_text())
    assert rebuilt_plan['anchor']['absent'] == 1
    assert rebuilt_plan['anchor']['absent_paths'] == ['apps/lantern-fleet-admin/dist/client/_next/static/new-asset.js']
    # A payload that is not this release refuses instead of minting an empty anchor.
    (predecessor / 'apps/lantern-fleet-admin/dist/server/index.js').unlink()
    with pytest.raises(SystemExit) as missing:
        BUILD.main(['--stage', str(tmp_path / 'broken'), '--no-build', '--anchor-from', str(predecessor)])
    assert str(missing.value) == 'anchor_source_missing: apps/lantern-fleet-admin/dist/server/index.js'


def test_deploy_requires_an_explicit_anchor(app, tmp_path):
    stage = stage_bundle(app, tmp_path)
    predecessor = predecessor_for(app, tmp_path)
    root = installed_root(app, tmp_path, predecessor)
    with pytest.raises(DEPLOY.Refused) as refusal:
        deploy_bundle(stage, tmp_path / 'backups/console-13', root)
    assert refusal.value.gate == 'anchor_required'


def test_deploy_refuses_a_dependency_change_before_writing_anything(app, tmp_path):
    stage = stage_bundle(app, tmp_path)
    predecessor = predecessor_for(app, tmp_path)
    root = installed_root(app, tmp_path, predecessor)
    plan_path = stage / 'manifest.json'
    plan = json.loads(plan_path.read_text())
    for item in plan['files']:
        if item['destination'].endswith('package-lock.json'):
            data = (stage / item['artifact']).read_bytes() + b'\n'
            (stage / item['artifact']).write_bytes(data)
            item['size'] = len(data)
            item['sha256'] = hashlib.sha256(data).hexdigest()
    plan_path.write_text(json.dumps(plan, sort_keys=True, indent=2) + '\n')
    with pytest.raises(DEPLOY.Refused) as refusal:
        DEPLOY.deploy(stage, plan_path, tmp_path / 'backups/console-4', root=root, predecessor=predecessor,
                      owner=os.geteuid(), run=lambda argv: subprocess.CompletedProcess(argv, 0, b''))
    assert refusal.value.gate == 'dependency_change_requires_release'
    assert not (root / DEPLOY.QUESTION_GATE.lstrip('/')).exists()
    assert (root / 'opt/sage-lantern/fleet-admin/dist/server/index.js').read_text() == '// server bundle\n'


def test_deploy_refuses_the_mesh_owned_voice_start_and_tampered_payloads(app, tmp_path):
    stage = stage_bundle(app, tmp_path)
    predecessor = predecessor_for(app, tmp_path)
    root = installed_root(app, tmp_path, predecessor)
    (root / DEPLOY.MESH_CONFIG.lstrip('/')).parent.mkdir(parents=True, exist_ok=True)
    (root / DEPLOY.MESH_CONFIG.lstrip('/')).write_text('{}\n')
    with pytest.raises(DEPLOY.Refused) as refusal:
        deploy_bundle(stage, tmp_path / 'backups/console-5', root, predecessor=predecessor)
    assert refusal.value.gate == 'mesh_question_lane_owns_voice_start'
    (root / DEPLOY.MESH_CONFIG.lstrip('/')).unlink()
    plan = json.loads((stage / 'manifest.json').read_text())
    (stage / plan['files'][0]['artifact']).write_bytes(b'tampered\n')
    with pytest.raises(DEPLOY.Refused) as refusal:
        deploy_bundle(stage, tmp_path / 'backups/console-6', root, predecessor=predecessor)
    assert refusal.value.gate == 'payload_does_not_match_manifest'


def test_deploy_restart_verifies_the_console_is_active(app, tmp_path):
    stage = stage_bundle(app, tmp_path)
    predecessor = predecessor_for(app, tmp_path)
    root = installed_root(app, tmp_path, predecessor)
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[-1] == DEPLOY.APP_UNIT and argv[1] == 'is-active':
            return subprocess.CompletedProcess(argv, 0, b'active\n')
        return subprocess.CompletedProcess(argv, 0, b'')

    receipt = deploy_bundle(stage, tmp_path / 'backups/console-7', root, predecessor=predecessor,
                            restart_app=True, run=run)
    assert receipt['app_restarted'] is True
    assert [DEPLOY.SYSTEMCTL, 'restart', DEPLOY.APP_UNIT] in calls
    assert [DEPLOY.SYSTEMCTL, 'is-active', DEPLOY.APP_UNIT] in calls

    def failed(argv):
        return subprocess.CompletedProcess(argv, 0, b'inactive\n')

    with pytest.raises(DEPLOY.Refused) as refusal:
        deploy_bundle(stage, tmp_path / 'backups/console-8', root, predecessor=predecessor,
                      restart_app=True, run=failed)
    assert refusal.value.gate == 'app_not_active_after_restart'
