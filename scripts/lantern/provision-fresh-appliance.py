#!/usr/bin/env python3
"""Reviewed fresh provisioning phases; no implicit identity generation or readiness.

base: OS bootstrap check (apt only by separate opt-in), existing guarded offline
base installer (native build opt-in required), then unit metadata preparation.
app: existing canonical fresh app installer, with explicit per-unit arguments.
identity: explicitly consented native SAGE fresh initialization between base/app.
Reviewed initial SAGE YAML and protected runtime config remain operator inputs.
This runner never invokes legacy verify/PROVISIONED,
activates SAGE, manufactures LOCAL_VOICE_READY, or handles private keys/PINs.
Personal SAGE has independent initialized-file/lock prerequisites; voice retains
LOCAL_VOICE_READY. File installation cannot prove either service ready.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import stat
import pwd
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[2]
SELF = 'scripts/lantern/provision-fresh-appliance.py'
APP = 'scripts/lantern/install-fleet-appliance-local.py'
POSTCOMMISSION = 'scripts/lantern/provision-postcommission-startup.py'
FRESH_HANDOFF = 'scripts/lantern/handoff-fresh-audio-lifetime.py'
PROVISIONING_PROFILE = 'software/config/sage-provisioning-profile.example.yaml'
PROVISIONING_PROFILE_PATH = '/etc/sage/provisioning-profile.yaml'
POST_PHASES = {'postcommission-handoff': 'handoff', 'postcommission-startup': 'install-startup'}
POST_OPTIONS = ('startup_dependencies', 'startup_dependencies_sha256', 'startup_expected',
                'startup_expected_sha256', 'startup_attempt_id', 'apply_postcommission')
VALUE_OPTIONS = {'--account', '--radio-inventory', '--peer-radio-inventory', '--radio-usb-serial',
                 '--radio-vendor-id', '--radio-product-id', '--radio-stable-path', '--radio-public-fingerprint',
                 '--audio-usb-serial', '--audio-stable-path', '--audio-vendor-id', '--audio-product-id',
                 '--alsa-device', '--gdm-base-sha256', '--audio-platform-id-path',
                 '--commissioned-manifest-sha256', '--predecessor-release-root'}
FLAG_OPTIONS = {'--allow-owner-vault-setup', '--enable-kiosk-autologin', '--radio-absent'}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def helper(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_release(args):
    require(type(args.manifest_sha256) is str and re.fullmatch(r'[0-9a-f]{64}', args.manifest_sha256), 'reviewed manifest hash required')
    output = Path(args.output).absolute()
    payload, manifest_path = output / 'payload', output / 'manifest.json'
    verifier = helper('phase_manifest', 'scripts/appliance-release-manifest.py')
    raw = verifier.metadata_bytes(manifest_path)[0]
    require(raw and hashlib.sha256(raw).hexdigest() == args.manifest_sha256, 'reviewed manifest hash mismatch')
    manifest = verifier.verify(payload, manifest_path)
    stage = helper('phase_stage', 'scripts/stage-appliance-release.py')
    app = helper('phase_app', APP)
    require(stage.read_json(output / 'selection.json') == manifest['selection'], 'selection mismatch')
    app.canonical_profile(payload, manifest, stage)
    entries = {entry['path']: entry for entry in manifest['files']}
    sources = {row['path'] for row in stage.required_sources()}
    sources.update(stage.module_names(ROOT, 'software/runtime', 'sage_lantern', ('pyproject.toml',)))
    require({name for name, entry in entries.items() if entry['role'] == 'runtime_source'}
            == {name for name in sources if name.startswith('software/runtime/')}, 'runtime closure mismatch')
    require(SELF in entries and entries[SELF]['role'] == 'installer', 'runner missing from canonical release')
    for name in sources | {SELF}:
        require(name in entries and verifier.file_hash(ROOT / name)[0]['sha256'] == entries[name]['sha256'], 'reviewed source closure mismatch')
    require(hashlib.sha256(verifier.metadata_bytes(manifest_path)[0]).hexdigest() == args.manifest_sha256, 'manifest changed')
    return payload


def app_options(values):
    values = list(values)
    if values[:1] == ['--']:
        values.pop(0)
    require(len(values) <= 80, 'too many app options')
    output, seen = [], set()
    while values:
        option = values.pop(0)
        require(option in VALUE_OPTIONS | FLAG_OPTIONS, 'unsupported or runner-owned app option')
        require(option not in seen or option == '--peer-radio-inventory', 'duplicate app option')
        seen.add(option)
        output.append(option)
        if option in VALUE_OPTIONS:
            require(values and not values[0].startswith('--') and len(values[0]) <= 4096 and '\x00' not in values[0], 'missing app option value')
            output.append(values.pop(0))
    return output


def commands(args, payload):
    require(args.unit_id in {'unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'}, 'explicit unit required')
    profile = getattr(args, 'platform_profile', 'debian12')
    require(profile in {'debian12', 'orangepi5plus-ubuntu26.04'}, 'unsupported platform profile')
    require(profile == 'debian12' or (args.phase == 'base' and args.unit_id in
            {'unit-c', 'unit-d', 'unit-e', 'unit-f'}), 'C–F platform profile is base-only on C–F')
    boot_options = ('apply_boot_theme', 'boot_theme_manifest', 'boot_theme_plan')
    if args.phase == 'boot-theme':
        require(not args.app_options and not args.allow_native_build and not args.install_os_packages
            and not any(getattr(args, name, None) for name in POST_OPTIONS +
                ('owner_approved_companion_bootstrap', 'identity_config_sha256', 'commission_plan',
                 'commission_plan_sha256')), 'mixed boot theme options')
        applying = bool(getattr(args, 'apply_boot_theme', False))
        require(not applying or args.execute, 'boot theme apply requires execute')
        require(all(bool(getattr(args, name, None)) == applying for name in boot_options[1:]),
                'exact boot theme apply pins required')
        command = ['/usr/bin/python3', '-I', '-B', str(payload / 'scripts/lantern/install-boot-theme.py')]
        if applying:
            require(all(re.fullmatch(r'[0-9a-f]{64}', getattr(args, name)) for name in boot_options[1:]),
                    'invalid boot theme digest')
            command += ['--apply', '--expected-manifest', args.boot_theme_manifest,
                        '--expected-plan', args.boot_theme_plan]
        return [command]
    require(not any(getattr(args, name, None) for name in boot_options), 'boot-only options in other phase')
    if args.phase == 'startup-helpers':
        require(not args.app_options and not args.allow_native_build and not args.install_os_packages
            and not any(getattr(args, name, None) for name in
                ('startup_dependencies', 'startup_dependencies_sha256', 'owner_approved_companion_bootstrap',
                 'identity_config_sha256', 'commission_plan', 'commission_plan_sha256')), 'mixed static phase options')
        applying = bool(args.apply_postcommission)
        require(all(bool(getattr(args, name, None)) == applying for name in
            ('startup_expected', 'startup_expected_sha256', 'startup_attempt_id')), 'exact static apply inputs required')
        require(not applying or args.execute, 'static apply requires execute')
        command = ['/opt/sage-lantern/venv/bin/python', '-I', '-B',
            str(payload / 'scripts/lantern/install-fresh-startup-helpers.py'),
            '--output', str(payload.parent), '--manifest-sha256', args.manifest_sha256,
            '--unit-id', args.unit_id]
        if applying:
            command += ['--apply', '--expected', args.startup_expected,
                '--expected-sha256', args.startup_expected_sha256, '--attempt-id', args.startup_attempt_id]
        return [command]
    if args.phase in POST_PHASES:
        return postcommission_commands(args, payload)
    require(not any(getattr(args, name, None) for name in POST_OPTIONS), 'startup-only options in other phase')
    if args.phase != 'identity-config':
        require(not getattr(args, 'commission_plan', None) and not getattr(args, 'commission_plan_sha256', None),
                'protected configuration plan is identity-config only')
    if args.phase in {'identity', 'identity-config'}:
        require(not args.app_options and not args.allow_native_build and not args.install_os_packages,
                'identity phase forbids other phase options')
        require(getattr(args, 'owner_approved_companion_bootstrap', False), 'explicit owner Companion bootstrap consent required')
        require(re.fullmatch(r'[0-9a-f]{64}', getattr(args, 'identity_config_sha256', '') or ''), 'reviewed identity config hash required')
        if args.phase == 'identity-config':
            require(getattr(args, 'commission_plan', None) and re.fullmatch(r'[0-9a-f]{64}', getattr(args, 'commission_plan_sha256', '') or ''),
                    'reviewed protected configuration plan required')
        return [['/usr/bin/setpriv', '--reuid', 'sage-lantern', '--regid', 'sage-lantern',
                 '--init-groups', '--inh-caps', '-all,+dac_read_search',
                 '--ambient-caps', '-all,+dac_read_search', '--bounding-set', '-all,+dac_read_search',
                 '/usr/bin/env', '-i',
                 'PATH=/usr/bin:/bin', 'HOME=/var/lib/sage-lantern', 'SAGE_HOME=/var/lib/sage-lantern/sage-personal',
                 'REST_ADDR=127.0.0.1:8080',
                 'SAGE_TLS_ADDR=127.0.0.1:8443', 'SAGE_CMT_RPC_ADDR=tcp://127.0.0.1:26657',
                 'SAGE_CMT_P2P_ADDR=tcp://127.0.0.1:26656', '/opt/sage-lantern/bin/sage-gui',
                 'init-fresh-personal-sage', '--profile', PROVISIONING_PROFILE_PATH,
                 '--unit', args.unit_id, '--owner-approved-companion-bootstrap']]
    require(not getattr(args, 'owner_approved_companion_bootstrap', False) and not getattr(args, 'identity_config_sha256', None),
            'identity-only options in other phase')
    if args.phase == 'base':
        require(not args.app_options, 'app options forbidden in base phase')
        require(args.allow_native_build, 'base phase includes native compilation; explicit approval required')
        bootstrap = '--install' if args.install_os_packages else '--check'
        return [
            ['/bin/bash', str(payload / 'scripts/bootstrap-lantern-os.sh'),
             *(['--profile', profile] if profile != 'debian12' else []), bootstrap],
            ['/bin/bash', str(payload / 'scripts/install-lantern-unit.sh')],
            ['/bin/bash', str(payload / 'scripts/provision-lantern-unit.sh'), 'prepare', '--unit-id', args.unit_id],
        ]
    require(args.phase == 'app' and not args.install_os_packages and not args.allow_native_build, 'base-only option in app phase')
    options = app_options(args.app_options)
    command = ['/usr/bin/python3', str(payload / APP), '--unit-id', args.unit_id,
               '--prepared-metadata', '/etc/sage-lantern/unit.env', '--release-root', str(payload),
               '--manifest', str(payload.parent / 'manifest.json'), '--operator-approved-release']
    return [command + options, command + options + ['--install']]


def postcommission_commands(args, payload):
    require(not args.app_options and not args.allow_native_build and not args.install_os_packages
        and not any(getattr(args, name, None) for name in ('owner_approved_companion_bootstrap',
            'identity_config_sha256', 'commission_plan', 'commission_plan_sha256')), 'mixed startup phase options')
    phase = helper('postcommission_contract', POSTCOMMISSION)
    raw = phase.trusted(args.startup_dependencies)
    require(hashlib.sha256(raw).hexdigest() == args.startup_dependencies_sha256, 'startup dependencies hash mismatch')
    handoff = helper('fresh_handoff_contract', FRESH_HANDOFF)
    deps = handoff.decode(raw)
    require(type(deps) is dict and set(deps) == {'schema', 'unit_id', 'handoff', 'tools', 'installer', 'service'}
        and deps['schema'] == 'sage.lantern.postcommission-dependencies.v1'
        and deps['unit_id'] == args.unit_id, 'startup unit/schema mismatch')
    manifest = handoff.decode((payload.parent / 'manifest.json').read_bytes())
    entries = {entry['path']: entry for entry in manifest['files']}
    required = {'handoff': FRESH_HANDOFF, 'installer': 'scripts/lantern/install-startup-coordinator.py',
        'service': 'software/systemd/sage-lantern-startup.service'}
    for name, source in required.items():
        require(deps[name] == {'path': str(payload / source), 'sha256': entries[source]['sha256']},
            'startup dependency differs from release')
    require(deps['installer']['sha256'] == phase.INSTALLER_SHA, 'startup installer not reviewed')
    tools = handoff.decode(handoff.pinned(deps['tools']))
    require(tools['unit_id'] == args.unit_id, 'startup tools unit mismatch')
    for name, source in {'coordinator': 'startup-coordinator.py', 'adapter': 'startup-audio-adapter.py',
            'helper': 'recover-cross-boot-audio.py', 'collector': 'collect-cross-boot-audio-pins.py'}.items():
        require(tools[name]['sha256'] == entries['scripts/lantern/' + source]['sha256'], 'startup source differs from release')
    require(tools['setup']['observer']['sha256'] == entries['scripts/lantern/activate-fresh-display.py']['sha256'],
        'startup setup observer differs from release')
    wheels = [entry for entry in manifest['files'] if entry['role'] == 'runtime_wheel']
    selected = manifest['selection']
    runtime_path = ('software/bundle/python-wheels/sage_lantern_runtime-'
        + selected['components']['runtime'] + '-py3-none-any.whl')
    selected_wheels = [entry for entry in selected['files'] if entry['role'] == 'runtime_wheel']
    require(len(wheels) == 1 and selected_wheels == [{'path': runtime_path, 'role': 'runtime_wheel'}]
        and wheels[0]['path'] == runtime_path and tools['runtime_wheel']['sha256'] == wheels[0]['sha256'],
        'startup runtime differs from release')
    command = ['/opt/sage-lantern/venv/bin/python', '-I', '-B', str(payload / POSTCOMMISSION),
        '--handoff', deps['handoff']['path'], '--handoff-sha256', deps['handoff']['sha256'],
        '--dependencies', str(args.startup_dependencies), '--dependencies-sha256', args.startup_dependencies_sha256,
        '--phase', POST_PHASES[args.phase]]
    applying = bool(getattr(args, 'apply_postcommission', False))
    require(all(bool(getattr(args, name, None)) == applying for name in
        ('startup_expected', 'startup_expected_sha256', 'startup_attempt_id')), 'exact startup apply inputs required')
    if applying:
        require(args.execute, 'startup apply requires explicit execution')
        expected = handoff.decode(handoff.pinned({'path': args.startup_expected, 'sha256': args.startup_expected_sha256}))
        require(expected['snapshot']['unit_id'] == args.unit_id and expected['phase'] == POST_PHASES[args.phase]
            and expected['dependencies_sha256'] == args.startup_dependencies_sha256, 'startup expected binding mismatch')
        require(str(uuid.UUID(args.startup_attempt_id)) == args.startup_attempt_id
            and uuid.UUID(args.startup_attempt_id).int > 0, 'fresh startup attempt UUID required')
        command += ['--apply', '--expected', args.startup_expected, '--expected-sha256', args.startup_expected_sha256,
            '--attempt-id', args.startup_attempt_id]
    return [command]


def node_preflight():
    helper('base_node_prerequisite', APP).node_preflight()


def execute(args, *, run=subprocess.run):
    payload = verify_release(args)
    steps = commands(args, payload)
    if args.execute:
        require(os.geteuid() == 0 and sys.platform == 'linux' and platform.machine() == 'aarch64', 'execute requires root Linux ARM64 target')
        if args.phase in {'identity', 'identity-config'}:
            identity_preflight(args, payload)
        commissioning = commission_preflight(args, payload) if args.phase == 'identity-config' else None
        if args.phase == 'base':
            require(not any(os.path.lexists(name) for name in ('/opt/sage-lantern', '/var/lib/sage-lantern',
                    '/etc/sage-lantern', '/etc/sage-lantern-device-lock.enabled', '/etc/sage-lantern-vault-setup.enabled')),
                    'existing base installation; no automatic resume')
            node_preflight()
        environment = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', HOME='/root', LANG='C.UTF-8',
                           PYTHONDONTWRITEBYTECODE='1', PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
        if getattr(args, 'platform_profile', 'debian12') == 'orangepi5plus-ubuntu26.04':
            environment['PATH'] = '/opt/lantern-python/3.11.16/bin:' + environment['PATH']
        for step in steps:
            require(verify_release(args) == payload, 'release changed between stages')
            if args.phase in POST_PHASES:
                require(commands(args, payload) == steps, 'startup inputs changed before execution')
            if commissioning is None:
                run(step, cwd=payload, env=environment, check=True)
            else:
                commission_begin(args, commissioning)
                result = run(step, cwd=payload, env=environment, check=True, capture_output=True, timeout=120)
                commission_install(args, result.stdout, commissioning)
    return dict(schema='sage.lantern.provision-phase.v1', unit_id=args.unit_id, phase=args.phase,
                executed=args.execute, commands=steps, ready=False, provisioned=False,
                configuration_applied=args.execute and args.phase == 'identity-config',
                boundary=('Boot theme child checks only; preserve recovery, next-boot configuration and rendering remain unverified. No reboot.' if args.phase == 'boot-theme' else
                          'Explicit postcommission phase only; no same-boot start or reboot acceptance. Preserve child receipts.' if args.phase in POST_PHASES else
                          'Run identity-config with explicit owner/policy approval, then app installation.' if args.phase == 'base'
                          else ('Protected config installed; ' if args.execute else 'Protected config installation planned; ') + 'next app installation, then owner name/PIN/recovery. Public enrollment and native acceptance remain.'
                          if args.phase == 'identity-config' else 'Owner PIN/recovery, root mesh/public prep and native startup acceptance remain; no service-start claim.'))


def identity_preflight(args, payload, *, root=Path('/')):
    verifier = helper('identity_manifest', 'scripts/appliance-release-manifest.py')
    config = root / 'var/lib/sage-lantern/sage-personal/config.yaml'
    metadata = root / 'etc/sage-lantern/unit.env'
    profile = root / PROVISIONING_PROFILE_PATH.lstrip('/')
    profile_template = payload / PROVISIONING_PROFILE
    binary = root / 'opt/sage-lantern/bin/sage-gui'
    for path in (config, metadata, profile, profile_template, binary):
        for component in (path, *path.parents):
            require(not component.is_symlink(), 'identity input symlink')
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not info.st_mode & 0o022, 'unsafe identity input')
    profile_info = profile.stat()
    require(profile_info.st_uid == 0 and stat.S_IMODE(profile_info.st_mode) == 0o600
            and identity_config_bytes(profile) == identity_config_bytes(profile_template),
            'reviewed root-owned provisioning profile required')
    require(metadata.stat().st_uid == 0 and binary.stat().st_uid == 0, 'operator-owned binding and executable required')
    account = pwd.getpwnam('sage-lantern')
    require(account.pw_uid != 0 and config.stat().st_uid == account.pw_uid and stat.S_IMODE(config.stat().st_mode) == 0o600,
            'initial config must be service-owned 0600')
    expected = f'UNIT_ID={args.unit_id}\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n'
    raw = identity_config_bytes(metadata).decode('utf-8')
    require(sorted(raw.splitlines()) == sorted(expected.splitlines()), 'prepared unit binding mismatch')
    require(hashlib.sha256(identity_config_bytes(config)).hexdigest() == args.identity_config_sha256, 'identity config differs from owner-reviewed candidate')
    require(verifier.file_hash(binary)[0]['sha256'] == verifier.file_hash(payload / 'software/bundle/linux-arm64/bin/sage-gui')[0]['sha256'],
            'installed SAGE differs from matched release')


def identity_config_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 1024 * 1024
                and not info.st_mode & 0o022, 'unsafe identity config')
        raw = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
        require(len(raw) == info.st_size and (info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'identity config changed or oversized')
        return raw


def commission_preflight(args, payload):
    assembler = helper('commission_assembler', 'scripts/lantern/assemble-protected-config.py')
    raw = assembler.read(args.commission_plan)
    require(hashlib.sha256(raw).hexdigest() == args.commission_plan_sha256, 'commission plan hash mismatch')
    plan = json.loads(raw, object_pairs_hook=assembler.unique)
    require(type(plan) is dict and set(plan) == {'schema', 'unit_id', 'runtime_template', 'runtime_sha256', 'quotas'}
            and plan['schema'] == 'sage.lantern.commission-plan.v1' and plan['unit_id'] == args.unit_id, 'commission plan unit/schema mismatch')
    runtime = assembler.read(plan['runtime_template'])
    require(hashlib.sha256(runtime).hexdigest() == plan['runtime_sha256'], 'runtime template hash mismatch')
    quotas = plan['quotas']
    require(type(quotas) is dict and set(quotas) == set(assembler.QUOTAS)
            and all(type(value) is int and 0 < value <= 2**53-1 for value in quotas.values()), 'explicit bounded quotas required')
    require(quotas['node_bytes'] >= quotas['actor_bytes'] and quotas['node_objects'] >= quotas['actor_objects'], 'reversed quotas')
    initial = identity_config_bytes(Path('/var/lib/sage-lantern/sage-personal/config.yaml'))
    require(hashlib.sha256(initial).hexdigest() == args.identity_config_sha256, 'initial config changed')
    assembler.merge_yaml(initial, quotas, enabled=True)
    template = assembler.tomllib.loads(runtime.decode())
    assembler.reject_embedded_secrets(template)
    sage = template.get('sage', {})
    bootstrap = assembler.yaml_document(initial)[2].get('vendored_agent_bootstrap', {})
    require(sage.get('identity_path') == '/var/lib/sage-lantern/identity/lantern-agent.key'
            and sage.get('base_url') == 'http://127.0.0.1:8080' and sage.get('domain') == bootstrap.get('home_domain')
            and 'chain_id' not in sage and 'protected_storage' not in sage, 'runtime template must be fresh private binding')
    manifest = json.loads((payload.parent / 'manifest.json').read_bytes(), object_pairs_hook=assembler.unique)
    paths = [entry['path'] for entry in manifest['files'] if entry['role'] == 'sage_manifest']
    require(len(paths) == 1, 'SAGE provenance missing')
    provenance = json.loads(assembler.read(payload / paths[0]), object_pairs_hook=assembler.unique)
    required = {'cmd/sage-gui/fresh_personal_init.go', 'cmd/sage-gui/private_listeners.go',
                'cmd/sage-gui/provisioning_profile.go',
                'cmd/sage-gui/private_media_config.go', 'api/rest/private_media_handler.go',
                'api/rest/workflow_journal_handler.go', 'internal/store/workflow_journal.go'}
    require(provenance.get('schema') == 'sage.lantern.binary-staging.v1'
            and required <= provenance.get('source_sha256', {}).keys(), 'required protected initialization sources missing')
    commission_inputs(initial, runtime)
    disk = os.statvfs('/var/lib/sage-lantern/sage-personal')
    require(disk.f_bavail * disk.f_frsize >= quotas['min_free_bytes'] + 16 * 1024 * 1024, 'insufficient private filesystem reserve')
    return assembler, plan, initial, runtime


COMMISSION_FILES = {'sage.yaml': 'var/lib/sage-lantern/sage-personal/config.yaml',
                    'lantern.toml': 'etc/sage-lantern/lantern.toml',
                    'bindings.json': 'etc/sage-lantern/commissioned-bindings.json',
                    'initialized-public.json': 'etc/sage-lantern/initialized-public.json'}
# The personal SAGE config is written by the running appliance after commissioning
# (vault unlock and federation bookkeeping). A successor install may accept that
# one output as evolved when the operator asks explicitly; every other output must
# still match its recorded digest before anything is replaced.
EVOLVABLE_COMMISSION_FILES = frozenset({'sage.yaml', 'lantern.toml'})
PENDING = 'etc/sage-lantern/commissioning.pending.json'
COMPLETE = 'etc/sage-lantern/commissioning-complete.json'


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def commission_binding(args):
    require(args.unit_id in {'unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'}, 'invalid commission unit')
    values = dict(unit_id=args.unit_id, manifest_sha256=args.manifest_sha256,
                  plan_sha256=args.commission_plan_sha256, initial_config_sha256=args.identity_config_sha256)
    require(all(type(values[name]) is str and re.fullmatch(r'[0-9a-f]{64}', values[name])
                for name in ('manifest_sha256', 'plan_sha256', 'initial_config_sha256')), 'invalid reviewed hash')
    return values


def commission_inputs(initial, runtime, *, root=Path('/'), pending=False):
    require(identity_config_bytes(root / COMMISSION_FILES['sage.yaml']) == initial, 'initial config changed; reconcile')
    require(identity_config_bytes(root / COMMISSION_FILES['lantern.toml']) == runtime, 'installed runtime differs from reviewed template')
    names = [COMPLETE, COMMISSION_FILES['bindings.json'], COMMISSION_FILES['initialized-public.json']]
    names += [COMMISSION_FILES[name] + '.before-commissioning' for name in ('sage.yaml', 'lantern.toml')]
    if not pending:
        names.append(PENDING)
    require(not any(os.path.lexists(root / name) for name in names), 'existing or incomplete commissioning; reconcile')


def commission_json(path, *, owner_uid=0):
    for component in (path, *path.parents):
        require(not component.is_symlink(), 'commission receipt symlink')
    info = path.stat()
    require(info.st_uid == owner_uid and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe commission receipt owner/mode')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate commission field')
            result[key] = value
        return result
    return json.loads(identity_config_bytes(path), object_pairs_hook=unique)


def durable_new(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def commission_begin(args, prepared, *, root=Path('/')):
    _, _, initial, runtime = prepared
    commission_inputs(initial, runtime, root=root)
    require(hashlib.sha256(initial).hexdigest() == args.identity_config_sha256, 'initial config differs from reviewed hash')
    pending = dict(schema='sage.lantern.commission-pending.v1', transaction_id=str(uuid.uuid4()), **commission_binding(args))
    durable_new(root / PENDING, (json.dumps(pending, sort_keys=True) + '\n').encode())
    return pending


def commission_receipt_binding(unit_id, manifest_sha256, *, root=Path('/'), owner_uid=0):
    """The completion receipt with its static binding verified, not its outputs."""
    require(not os.path.lexists(root / PENDING), 'commissioning pending; app installation forbidden')
    receipt = commission_json(root / COMPLETE, owner_uid=owner_uid)
    fields = {'schema', 'transaction_id', 'unit_id', 'manifest_sha256', 'plan_sha256', 'initial_config_sha256', 'output_sha256'}
    require(type(receipt) is dict and set(receipt) == fields and receipt['schema'] == 'sage.lantern.commission-complete.v1'
            and receipt['unit_id'] == unit_id and receipt['manifest_sha256'] == manifest_sha256, 'commission completion binding mismatch')
    require(type(receipt['transaction_id']) is str and str(uuid.UUID(receipt['transaction_id'])) == receipt['transaction_id'], 'invalid commission transaction')
    require(all(type(receipt[name]) is str and re.fullmatch(r'[0-9a-f]{64}', receipt[name])
                for name in ('manifest_sha256', 'plan_sha256', 'initial_config_sha256')), 'invalid completion hash')
    outputs = receipt['output_sha256']
    require(type(outputs) is dict and set(outputs) == set(COMMISSION_FILES), 'incomplete output binding')
    return receipt


def commission_output_drift(receipt, *, root=Path('/')):
    """Recorded commissioning outputs whose bytes have changed since commissioning."""
    drift = {}
    for name, path in COMMISSION_FILES.items():
        recorded = receipt['output_sha256'][name]
        require(type(recorded) is str and re.fullmatch(r'[0-9a-f]{64}', recorded), 'invalid completion hash: ' + name)
        observed = hashlib.sha256(identity_config_bytes(root / path)).hexdigest()
        if observed != recorded:
            drift[name] = observed
    return drift


def commission_completion_check(unit_id, manifest_sha256, *, root=Path('/'), owner_uid=0, accept_evolved=frozenset()):
    require(set(accept_evolved) <= EVOLVABLE_COMMISSION_FILES, 'unreviewed evolvable commissioning output')
    receipt = commission_receipt_binding(unit_id, manifest_sha256, root=root, owner_uid=owner_uid)
    for name in sorted(commission_output_drift(receipt, root=root)):
        require(name in accept_evolved, 'commission output changed: ' + name)
    require(not os.path.lexists(root / PENDING), 'commissioning became pending')
    return receipt


def commission_install(args, stdout, prepared, *, root=Path('/')):
    assembler, plan, initial, runtime = prepared
    pending = commission_json(root / PENDING, owner_uid=os.geteuid())
    require(type(pending) is dict and set(pending) == {'schema', 'transaction_id', *commission_binding(args)}
            and pending['schema'] == 'sage.lantern.commission-pending.v1'
            and all(pending[key] == value for key, value in commission_binding(args).items()), 'pending commission binding mismatch')
    require(type(pending['transaction_id']) is str and str(uuid.UUID(pending['transaction_id'])) == pending['transaction_id'], 'invalid pending transaction')
    commission_inputs(initial, runtime, root=root, pending=True)
    payloads = assembler.generated_config(stdout, args.unit_id, initial, runtime, plan['quotas'])
    config = root / 'var/lib/sage-lantern/sage-personal/config.yaml'
    target_runtime = root / 'etc/sage-lantern/lantern.toml'
    bindings = root / 'etc/sage-lantern/commissioned-bindings.json'
    require(identity_config_bytes(config) == initial, 'initial config changed after initialization; reconcile')
    require(not os.path.lexists(bindings), 'existing commissioning bindings; reconcile')
    previous_runtime = identity_config_bytes(target_runtime)
    require(previous_runtime == runtime, 'installed runtime differs from reviewed template; reconcile before replacing')
    receipt = target_runtime.parent / 'initialized-public.json'
    descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(stdout)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(receipt.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    backups = ((config, initial), (target_runtime, previous_runtime))
    for path, prior in backups:
        backup = path.with_name(path.name + '.before-commissioning')
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(prior)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for path, data in ((config, payloads['sage.yaml']), (target_runtime, payloads['lantern.toml'])):
        info = path.stat()
        descriptor, temporary = tempfile.mkstemp(prefix='.commission-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    for directory in (config.parent, target_runtime.parent):
        sync_directory(directory)
    descriptor = os.open(bindings, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(payloads['bindings.json'])
        stream.flush()
        os.fsync(stream.fileno())
    for directory in {config.parent, target_runtime.parent}:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    outputs = {**payloads, 'initialized-public.json': stdout}
    for name, data in outputs.items():
        require(identity_config_bytes(root / COMMISSION_FILES[name]) == data, 'commissioned output readback mismatch')
    completion = {**pending, 'schema': 'sage.lantern.commission-complete.v1',
                  'output_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()}}
    path = root / COMPLETE
    temporary = path.with_name('.commission-complete-' + pending['transaction_id'])
    require(not os.path.lexists(path), 'existing commission completion')
    durable_new(temporary, (json.dumps(completion, sort_keys=True) + '\n').encode())
    os.replace(temporary, path)
    sync_directory(path.parent)
    require(commission_json(root / PENDING, owner_uid=os.geteuid()) == pending, 'pending fence changed')
    (root / PENDING).unlink()
    try:
        sync_directory(path.parent)
    except OSError:
        durable_new(root / PENDING, (json.dumps(pending, sort_keys=True) + '\n').encode())
        raise


def main(argv=None):
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='Canonical OUTPUT containing payload and manifest.json')
    parser.add_argument('--manifest-sha256', required=True, help='Independently reviewed exact manifest hash')
    parser.add_argument('--unit-id', choices=('unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'), required=True)
    parser.add_argument('--phase', choices=('base', 'identity', 'identity-config', 'app', 'startup-helpers', 'boot-theme', *POST_PHASES), required=True)
    parser.add_argument('--apply-boot-theme', action='store_true')
    parser.add_argument('--boot-theme-manifest')
    parser.add_argument('--boot-theme-plan')
    parser.add_argument('--startup-dependencies')
    parser.add_argument('--startup-dependencies-sha256')
    parser.add_argument('--startup-expected')
    parser.add_argument('--startup-expected-sha256')
    parser.add_argument('--startup-attempt-id')
    parser.add_argument('--apply-postcommission', action='store_true', help='Explicit once-only reviewed phase apply; requires --execute and expected pins')
    parser.add_argument('--owner-approved-companion-bootstrap', action='store_true')
    parser.add_argument('--identity-config-sha256', help='Reviewed per-unit initial SAGE config hash; identity phase only')
    parser.add_argument('--commission-plan', help='Explicit protected configuration plan; identity-config phase only')
    parser.add_argument('--commission-plan-sha256', help='Independently reviewed plan SHA256')
    parser.add_argument('--execute', action='store_true', help='Explicitly execute target steps; default validates/plans only')
    parser.add_argument('--allow-native-build', action='store_true')
    parser.add_argument('--install-os-packages', action='store_true', help='Authorize bootstrap apt package/index changes; base phase only')
    parser.add_argument('--platform-profile', choices=('debian12', 'orangepi5plus-ubuntu26.04'), default='debian12',
                        help='Explicit base platform; C–F Ubuntu uses separately verified Python 3.11.16')
    parser.add_argument('app_options', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(execute(args), sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print('Provision phase stopped. Preserve partial state; no automatic retry, cleanup or readiness marker.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
