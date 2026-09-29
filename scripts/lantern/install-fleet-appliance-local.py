#!/usr/bin/env python3
"""Offline fresh A/B app installation; no builds, service starts or readiness claim.

Requires an independently operator-approved matched sterile release, Node 22.13+,
existing sage-lantern account and separately provisioned runtime/private SAGE.
Install writes lock policy before application files; explicit kiosk opt-in alone
enables app/auth/status for next boot without starting them or unlocking SAGE.
Owner PIN setup permission is explicit; no PIN, identity, key or prior state is
read/copied. Partial failure remains locked; this is never an update installer.
Fresh history preferences enable saving and photos under the reviewed onboarding
consent; existing preferences/state are refused, never adopted or overwritten.

Remaining activation gates: matched installed runtime/SDK/SAGE, own private-chain
and Root binding, protected media/config, owner PIN/recovery, mesh startup owner,
kiosk login, camera/display/suspend and hardware tests.
Status reports configured path presence, never capture or RF verification.
Radio/audio plans are operator input consistency,
not evidence of successful physical IO. Nothing writes PROVISIONED.
An explicitly absent radio (no public inventory written) and an operator-named
platform capture codec are accepted; both remain recorded operator declarations.
A unit commissioned under an earlier reviewed release may take this app from a
successor release only with --commissioned-manifest-sha256 plus the on-unit
--predecessor-release-root: the predecessor manifest must hash to the pin and
its pinned native binary must be the one actually installed. The commissioning
receipt and every recorded output are still re-verified byte for byte.
"""

import hashlib
import grp
import configparser
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[2]
APP = 'apps/lantern-fleet-admin/'
DESTINATION = '/opt/sage-lantern/fleet-admin'
INSTALLER = 'scripts/lantern/install-fleet-appliance-local.py'
AUTH = 'scripts/lantern/unit-a-device-auth.py'
AUTH_UNIT = 'software/systemd/sage-lantern-device-auth.service'
AUTH_DRAIN = '/var/lib/sage-lantern/device-lock/drain'
APPLIANCE_FILES = {
    'scripts/lantern/wait-auth-native.py': '/usr/local/libexec/sage-lantern-wait-auth-native',
    'scripts/lantern/request-broker-contact.py': '/usr/local/libexec/sage-lantern-request-broker-contact',
    'software/systemd/sage-lantern-mesh-runtime.service.d/25-radio-commission-journal.conf': '/etc/systemd/system/sage-lantern-mesh-runtime.service.d/25-radio-commission-journal.conf',
    'scripts/lantern/unit-a-status-server.py': '/usr/local/libexec/sage-lantern-status-server',
    'software/systemd/sage-lantern-status.service': '/etc/systemd/system/sage-lantern-status.service',
    'scripts/lantern/unit-a-voice-loop.py': '/usr/local/libexec/sage-lantern-voice-loop',
    'software/systemd/sage-lantern-voice-loop.service': '/etc/systemd/system/sage-lantern-voice-loop.service',
    'software/systemd/sage-lantern-mesh-runtime.service': '/etc/systemd/system/sage-lantern-mesh-runtime.service',
    'software/systemd/sage-lantern-gateway-evidence.service': '/etc/systemd/system/sage-lantern-gateway-evidence.service',
    'software/systemd/sage-lantern-voice-loop.service.d/40-mesh-broker.conf': '/etc/systemd/system/sage-lantern-voice-loop.service.d/40-mesh-broker.conf',
    'scripts/lantern/sage-lantern-kiosk': '/usr/local/bin/sage-lantern-kiosk',
    'scripts/lantern/cf-mode-supervisor.py': '/usr/local/libexec/sage-lantern-mode-supervisor',
    'scripts/lantern/cf-output-mode.py': '/usr/local/libexec/sage-lantern-output-mode',
    'scripts/lantern/cf-desktop-mode.sh': '/usr/local/bin/sage-lantern-desktop-mode',
    'scripts/lantern/sage-lantern-session': '/usr/local/bin/sage-lantern-session',
    'apps/lantern-fleet-admin/deploy/sage-lantern.desktop': '/usr/share/xsessions/sage-lantern.desktop',
}
RADIO_PINS = {
    'software/systemd/sage-lantern-mesh-runtime.service.d/25-radio-commission-journal.conf':
        'c6e21f3ebf5af6620b799772ca99c0ee206e9b4c619c3cf370bf794d98eed045',
    'scripts/lantern/request-broker-contact.py':
        '103344d9abbd4104a58ce87b18ca74a59498b73e4e310ca90ae28a3432fcee85',
}
HELPERS = ('scripts/lantern/lantern-unit-config.py', 'scripts/appliance-release-manifest.py',
           'scripts/stage-appliance-release.py',
           'scripts/verify-runtime-release.py', 'scripts/build-runtime-release.py',
           'scripts/build-sdk-release.py', 'scripts/build-sage-release.py')
INSTALLED_RECEIPT = '/etc/sage-lantern/appliance-installed.json'
DEVICE_LOCK_MARKER = '/etc/sage-lantern-device-lock.enabled'
VAULT_SETUP_MARKER = '/etc/sage-lantern-vault-setup.enabled'
SUCCESSOR_STATE_DIRS = ('/var/lib/sage-lantern/device-lock', '/var/lib/sage-lantern/device-lock/drain',
                        '/var/lib/sage-lantern/history', '/var/lib/sage-lantern/history/conversations-v1')
# The session launcher is rewritten by this installer (per-unit profile
# injection), so an installed copy cannot be checked against the payload digest.
# The auth unit is likewise transformed; its staged row carries the
# installer_transform operation and is excluded by operation below.
TRANSFORMED_TARGETS = frozenset({APPLIANCE_FILES['scripts/lantern/sage-lantern-session']})


def helper(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(value, reason):
    if not value:
        raise ValueError(reason)


def no_links(path):
    require(path.is_absolute(), 'absolute path required')
    for parent in (path, *path.parents):
        require(not parent.is_symlink(), 'linked target forbidden')


def app_unit(unit_id):
    return f'''[Unit]
Description=SAGE Lantern {unit_id} private touchscreen app
After=sage-lantern-device-auth.service
Requires=sage-lantern-device-auth.service
ConditionPathExists=/etc/sage-lantern-device-lock.enabled
ConditionPathExists=/etc/sage-lantern/appliance-installed.json

[Service]
Type=simple
User=sage-lantern
Group=sage-lantern
WorkingDirectory={DESTINATION}
Environment=NODE_ENV=production
Environment=LANTERN_EDGE_LOCAL=1
Environment=LANTERN_DEVICE_LOCK=1
Environment="LANTERN_EDGE_OPERATOR={unit_id} operator"
ExecStart=/usr/bin/node {DESTINATION}/node_modules/vinext/dist/cli.js start --hostname 127.0.0.1 --port 3100
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
UMask=0077

[Install]
WantedBy=multi-user.target
'''


def canonical_profile(release, manifest, stage):
    """Reconstruct the frozen canonical profile; destinations are not instructions."""
    entries = {entry['path']: entry for entry in manifest['files']}
    destinations = stage.read_json(release / 'release/destinations.json')
    require(type(destinations) is dict and set(destinations) == {'schema', 'release_id', 'deployment_ready',
            'signed_authenticity', 'activation_authorized', 'account', 'ui_dependencies',
            'generated_by_fresh_installer', 'files'}, 'invalid canonical destinations schema')
    require(destinations['schema'] == 'sage.lantern.appliance-destinations.v1'
            and destinations['release_id'] == manifest['release_id'] and destinations['account'] == 'sage-lantern'
            and all(destinations[key] is False for key in ('deployment_ready', 'signed_authenticity', 'activation_authorized')),
            'unsupported canonical profile')
    require(destinations['generated_by_fresh_installer'] == [
        '/etc/systemd/system/sage-lantern-fleet-admin.service', '/etc/sage-lantern/appliance-bindings.json',
        '/etc/udev/rules.d/71-sage-lantern-audio.rules', '/usr/local/share/sage-lantern/meshcore-radio-public.json'],
        'unexpected generated destinations')
    rows = stage.required_sources()
    rows += [stage.mapping(name, 'runtime_source') for name in stage.module_names(
        release, 'software/runtime', 'sage_lantern', ('pyproject.toml',))]
    rows += [stage.mapping(name, 'sdk_source', operation='evidence_only') for name in stage.module_names(
        release, stage.SDK_ROOT.rstrip('/'), 'sage_sdk', ('pyproject.toml', 'README.md'))]
    lock = stage.read_json(release / stage.LOCK)
    require(lock.get('schema') == 'sage.lantern.stack-lock.v1'
            and lock.get('target') == dict(os='linux', architecture='arm64', python='3.11'), 'unsupported stack target')
    components = {key: lock['components'][name]['version'] for key, name in
                  (('sage', 'sage'), ('sdk', 'sage-agent-sdk'), ('runtime', 'lantern-runtime'))}
    require(components == manifest['selection']['components'], 'stack/component mismatch')
    sdk_wheel = stage.WHEELS + 'sage_agent_sdk-' + components['sdk'] + '-py3-none-any.whl'
    runtime_wheel = stage.WHEELS + 'sage_lantern_runtime-' + components['runtime'] + '-py3-none-any.whl'
    models = {'software/cache/models/' + name for name in stage.MODELS}
    archives = {'software/cache/sources/' + name for name in stage.ARCHIVES}
    locked = set()
    for entry in lock['files']:
        name = entry['path']
        require(name not in locked and name in entries, 'missing/duplicate locked artifact')
        locked.add(name)
        require(all(entry[key] == entries[name][key] for key in ('sha256', 'bytes')), 'stack artifact mismatch')
        if name == stage.BINARY:
            row = stage.mapping(name, 'sage_binary', '/opt/sage-lantern/bin/sage-gui', 'copy')
        elif name in stage.NPU_ARTIFACTS:
            require(entry["sha256"] == stage.NPU_ARTIFACT_PINS[name], "unreviewed NPU artifact")
            destination = stage.NPU_ARTIFACTS[name]
            row = stage.mapping(name, "dependency", destination, "copy" if destination else "evidence_only")
        elif name in models:
            row = stage.mapping(name, 'dependency', '/opt/sage-lantern/models/' + Path(name).name, 'copy')
        elif name in archives:
            destination = '/opt/sage-lantern/bin/whisper-cli' if 'whisper' in name else '/opt/sage-lantern/llama.cpp/llama-server'
            row = stage.mapping(name, 'dependency', destination, 'offline_build')
        else:
            require(name.startswith(stage.WHEELS) and '/' not in name.removeprefix(stage.WHEELS)
                    and name.endswith('.whl'), 'noncanonical artifact')
            require(not name.startswith(stage.WHEELS + 'sage_agent_sdk-') or name == sdk_wheel, 'extra SDK wheel')
            require(not name.startswith(stage.WHEELS + 'sage_lantern_runtime-') or name == runtime_wheel, 'extra runtime wheel')
            role = 'sdk_wheel' if name == sdk_wheel else 'runtime_wheel' if name == runtime_wheel else 'dependency'
            row = stage.mapping(name, role, '/opt/sage-lantern/venv', 'wheel_install')
        rows.append(row)
    require({stage.BINARY, sdk_wheel, runtime_wheel} | models | archives | stage.NPU_ARTIFACTS.keys() <= locked, 'missing base artifacts')
    for prefix in stage.BASE_WHEEL_PREFIXES:
        require(sum(name.startswith(stage.WHEELS + prefix) for name in locked) == 1, 'missing/ambiguous base wheel')
    rows += [stage.mapping(stage.LOCK, 'support', operation='evidence_only'),
             stage.mapping(stage.SAGE_MANIFEST, 'sage_manifest', operation='evidence_only')]
    ui = {name.removeprefix(APP): entry for name, entry in entries.items() if entry['role'] == 'ui'}
    require(all(name in {'package.json', 'package-lock.json'} or name.startswith(('dist/', 'public/', 'node_modules/')) for name in ui), 'unexpected UI path')
    require({'package.json', 'package-lock.json', 'dist/server/index.js', 'node_modules/vinext/dist/cli.js'} <= ui.keys(), 'missing runnable UI')
    package = stage.read_json(release / APP / 'package.json')
    npm = stage.read_json(release / APP / 'package-lock.json')
    require(npm.get('lockfileVersion') == 3 and type(npm.get('packages')) is dict, 'npm lock v3 required')
    packages = npm['packages']
    require(package.get('name') == 'sage-lantern-fleet-admin' and '' in packages, 'wrong UI package')
    for key in ('name', 'version', 'dependencies', 'devDependencies', 'optionalDependencies'):
        require(package.get(key) == packages[''].get(key), 'UI lock disagreement')
    expected = stage.dependency_inventory(packages, ui)
    for name, metadata in expected.items():
        require(type(metadata.get('version')) is str and stage.read_json(release / APP / name / 'package.json').get('version') == metadata['version'], 'installed UI version mismatch')
    require(destinations['ui_dependencies'] == {name: metadata['version'] for name, metadata in sorted(expected.items())}, 'dependency declaration mismatch')
    rows += [stage.mapping(APP + name, 'ui', stage.APP_DEST + name, 'copy') for name in ui]
    dropin = 'release/dropins/sage-lantern-fleet-admin.service.d/90-device-lock.conf'
    rows.append(stage.mapping(dropin, 'dropin', '/etc/systemd/system/sage-lantern-fleet-admin.service.d/90-device-lock.conf', 'review_only'))
    require(read_payload(release / dropin, entries[dropin]) == b'[Service]\nEnvironment=LANTERN_DEVICE_LOCK=1\n', 'lock dropin changed')
    expected_rows = {row['path']: row for row in rows}
    require(len(expected_rows) == len(rows) and type(destinations['files']) is list
            and len(destinations['files']) == len(rows), 'duplicate/incomplete canonical rows')
    actual_rows = {row['path']: row for row in destinations['files']}
    require(actual_rows == expected_rows, 'noncanonical destination/operation/mode')
    require(set(entries) == set(expected_rows) | {'release/destinations.json'}, 'noncanonical source closure')
    for name, row in expected_rows.items():
        require(entries[name]['role'] == row['role'], 'canonical role mismatch')
    require(entries['release/destinations.json']['role'] == 'support', 'invalid destinations role')
    return expected_rows


def prepare(args):
    config = helper('unit_config', HELPERS[0])
    require(args.unit_id in {'unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'} and args.format == 'plan'
            and not args.identity_inventory and not args.activation_staging_root, 'unsupported plan options')
    plan = config.build_plan(args)
    require(type(args.alsa_device) is str and re.fullmatch(r'hw:CARD=[A-Za-z0-9_]{1,32},DEV=[0-9]{1,2}', args.alsa_device), 'explicit ALSA capture binding required')
    plan['audio']['alsa_device'] = args.alsa_device
    rules = config.audio_udev_rules(plan)
    require(args.operator_approved_release is True, 'operator-approved release required')
    verifier = helper('appliance_manifest', 'scripts/appliance-release-manifest.py')
    release = Path(args.release_root).absolute()
    require(release.name == 'payload' and Path(args.manifest).absolute() == release.parent / 'manifest.json', 'canonical OUTPUT/payload and OUTPUT/manifest.json required')
    manifest = verifier.verify(release, args.manifest)
    stage = helper('canonical_stage', 'scripts/stage-appliance-release.py')
    require(stage.read_json(release.parent / 'selection.json') == manifest['selection'], 'external selection mismatch')
    profile = canonical_profile(release, manifest, stage)
    entries = {entry['path']: entry for entry in manifest['files']}
    sources = {row['path'] for row in stage.required_sources()}
    sources.update(stage.module_names(ROOT, 'software/runtime', 'sage_lantern', ('pyproject.toml',)))
    require({name for name, row in profile.items() if row['role'] == 'runtime_source'}
            == {name for name in sources if name.startswith('software/runtime/')}, 'runtime source closure mismatch')
    for name in sources:
        require(name in entries, 'missing installer/dependency selection')
        require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == entries[name]['sha256'], 'installer/helper source mismatch')
    require(entries[INSTALLER]['role'] == 'installer', 'installer role required')
    ui = {name: entry for name, entry in entries.items() if entry['role'] == 'ui'}
    require(all(name.startswith(APP) for name in ui), 'unexpected UI root')
    require(all(APP + name in ui for name in ('dist/server/index.js', 'package.json', 'package-lock.json', 'node_modules/vinext/dist/cli.js')), 'incomplete runnable app')
    return plan, rules, release, manifest, ui


def read_payload(source, entry):
    no_links(source)
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == entry['bytes']
                and info.st_size <= 256 * 1024 * 1024, 'source changed/oversized app file')
        data = stream.read(entry['bytes'] + 1)
    require(len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256'], 'source hash changed')
    return data


def payloads(plan, rules, release, manifest):
    entries = {entry['path']: entry for entry in manifest['files']}
    auth = read_payload(release / AUTH, entries[AUTH])
    auth_service = read_payload(release / AUTH_UNIT, entries[AUTH_UNIT]).decode()
    commands = re.findall(r'(?m)^ExecStart=([^\n]+)$', auth_service)
    require(len(commands) == 1 and '--state-dir /var/lib/sage-lantern/device-lock' in commands[0]
            and '--drain-report-dir' not in auth_service and not commands[0].endswith('\\'), 'unreviewed auth drain command')
    auth_service = auth_service.replace('ExecStart=' + commands[0], 'ExecStart=' + commands[0] + ' --drain-report-dir ' + AUTH_DRAIN, 1)
    auth_service = auth_service.replace('[Unit]\n', '[Unit]\nConditionPathExists=/etc/sage-lantern/appliance-installed.json\n', 1)
    radio = plan['radio']
    public_radio = None
    if radio.get('present', True):
        public_radio = dict(schema='sage.lantern.meshcore-radio-public.v1', unit_id=plan['unit_id'],
                            usb=dict(serial=radio['usb_serial'], vendor_id=radio['vendor_id'], product_id=radio['product_id'], stable_path=radio['stable_path']),
                            radio_identity=dict(public_key=radio['public_key'], public_key_sha256=radio['public_fingerprint']))
    account = plan['operator_account']
    require(re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', account) and account not in {'root', 'sage-lantern', 'nobody'}, 'invalid kiosk account')
    alsa = plan['audio']['alsa_device']
    require(re.fullmatch(r'hw:CARD=[A-Za-z0-9_]{1,32},DEV=[0-9]{1,2}', alsa), 'invalid ALSA binding')
    files = {destination: read_payload(release / source, entries[source]) for source, destination in APPLIANCE_FILES.items()}
    for source, expected in RADIO_PINS.items():
        require(hashlib.sha256(files[APPLIANCE_FILES[source]]).hexdigest() == expected,
                'unreviewed radio commissioning artifact')
    session = files['/usr/local/bin/sage-lantern-session'].decode()
    launcher = 'exec /usr/local/bin/sage-lantern-kiosk'
    require(session.count(launcher) == 1, 'unreviewed kiosk session launcher')
    profile = f'/home/{account}/.config/sage-lantern/{plan["unit_id"]}/chromium'
    session = session.replace(launcher, f'exec /usr/local/bin/sage-lantern-kiosk --profile-dir {profile}')
    require(session.startswith('#!/usr/bin/env bash\n'), 'unreviewed kiosk session format')
    session = session.replace('#!/usr/bin/env bash\n', f'#!/usr/bin/env bash\n[[ $(id -un) == {account} ]] || exit 2\n', 1)
    files['/usr/local/bin/sage-lantern-session'] = session.encode()
    files.update({
        '/etc/systemd/system/sage-lantern-fleet-admin.service': app_unit(plan['unit_id']).encode(),
        '/etc/systemd/system/sage-lantern-device-auth.service': auth_service.encode(),
        '/usr/local/libexec/sage-lantern-device-auth': auth,
        '/etc/udev/rules.d/71-sage-lantern-audio.rules': (rules + '\n').encode(),
        '/etc/sage-lantern/appliance-bindings.json': (json.dumps(plan, sort_keys=True, indent=2) + '\n').encode(),
        '/etc/systemd/system/sage-lantern-voice-loop.service.d/30-unit-audio.conf': (
            '[Service]\nExecStart=\nExecStart=/usr/local/libexec/sage-lantern-voice-loop '
            '--config /etc/sage-lantern/lantern.toml --alsa-device ' + alsa +
            ' --drain-request /run/sage-lantern-mesh-control/voice-drain-request.json\n').encode(),
    })
    if public_radio is not None:
        files['/usr/local/share/sage-lantern/meshcore-radio-public.json'] = (
            json.dumps(public_radio, sort_keys=True) + '\n').encode()
    return files


def fresh(root, files):
    require(not os.path.lexists(root / 'etc/sage-lantern/commissioning.pending.json'), 'commissioning pending; installation forbidden')
    targets = {*files, DESTINATION, '/etc/sage-lantern-device-lock.enabled',
               '/etc/sage-lantern-vault-setup.enabled', '/etc/sage-lantern/appliance-installed.json',
               '/etc/systemd/system/sage-lantern-fleet-admin.service.d',
               '/etc/systemd/system/sage-lantern-device-auth.service.d',
               '/etc/systemd/system/sage-lantern-voice-loop.service.d',
               '/etc/systemd/system/sage-lantern-mesh-runtime.service.d',
               '/etc/sage-lantern/mesh-runtime.json', '/var/lib/sage-lantern-mesh-supervisor',
               '/var/lib/sage-lantern-radio-commission', '/var/lib/sage-lantern-radio-operator',
               '/run/sage-lantern-mesh-control',
               '/var/lib/sage-lantern/device-lock', '/var/lib/sage-lantern/history',
               '/var/lib/sage-lantern/conversations-v1', '/var/lib/sage-lantern/LOCAL_VOICE_READY',
               '/etc/sage-lantern/PROVISIONED', '/var/lib/sage-lantern/PROVISIONED', '/var/lib/sage-lantern/PTT_VERIFIED'}
    for name in targets:
        path = root / name.lstrip('/')
        no_links(path)
        require(not path.exists(), 'fresh target required; existing state is not overwritten')
    for directory in ('/etc/systemd/system/multi-user.target.wants', '/etc/systemd/system/sage-lantern-personal-sage.service.wants'):
        for service in ('sage-lantern-fleet-admin.service', 'sage-lantern-device-auth.service', 'sage-lantern-voice-loop.service', 'sage-lantern-mesh-runtime.service', 'sage-lantern-gateway-evidence.service', 'sage-lantern-status.service'):
            path = root / directory.lstrip('/') / service
            require(not path.exists() and not path.is_symlink(), 'previous service enablement forbidden')


def write_new(path, data, mode=0o644):
    no_links(path)
    for parent in reversed(path.parents):
        if not parent.exists():
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def payload_targets(files, ui):
    """Installed path -> sha256 for every file this install is about to write."""
    targets = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    targets.update({DESTINATION + '/' + name.removeprefix(APP): entry['sha256'] for name, entry in ui.items()})
    return targets


def predecessor_targets(release, manifest):
    """Installed path -> sha256 recorded by the pinned predecessor release."""
    entries = {entry['path']: entry for entry in manifest['files']}
    plan = json.loads((Path(release) / 'payload/release/destinations.json').read_bytes())
    targets = {}
    for row in plan['files']:
        destination = row.get('destination')
        if not destination or row.get('operation') not in ('copy', 'reviewed_copy'):
            continue
        if destination in TRANSFORMED_TARGETS:
            continue
        entry = entries.get(row['path'])
        require(type(entry) is dict and type(entry.get('sha256')) is str
                and re.fullmatch(r'[0-9a-f]{64}', entry['sha256']) is not None,
                'predecessor release row lacks a digest: ' + row['path'])
        targets[destination] = entry['sha256']
    return targets


def installed_digest(root, name):
    """sha256 of an installed file, or None when it is absent."""
    path = root / name.lstrip('/')
    no_links(path)
    if not os.path.lexists(path):
        return None
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe installed target: ' + name)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def installed_release(root, *, root_prefix=Path('/')):
    """The release the receipt says is installed, read from its on-unit tree.

    The commissioning pin in the receipt binds the unit's provenance; this tree is
    then bound to the installed bytes by successor_state, and (for receipts written
    from this version on) by the release's own manifest digest.
    """
    receipt_path = root_prefix / INSTALLED_RECEIPT.lstrip('/')
    no_links(receipt_path)
    try:
        receipt = json.loads(receipt_path.read_bytes())
    except ValueError:
        raise ValueError('invalid installed appliance receipt')
    require(type(receipt) is dict and receipt.get('installed') is True,
            'installed appliance receipt does not record an installation')
    release_id = receipt.get('release_id')
    require(type(release_id) is str and release_id, 'installed appliance receipt lacks a release id')
    manifest_path = Path(root) / 'manifest.json'
    no_links(manifest_path)
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    require(type(manifest) is dict and manifest.get('release_id') == release_id,
            'installed release root does not name the installed release')
    digest = hashlib.sha256(raw).hexdigest()
    recorded = receipt.get('manifest_sha256')
    require(recorded is None or recorded == digest, 'installed release tree differs from the recorded manifest')
    return dict(root=str(root), release_id=release_id, manifest=manifest, manifest_sha256=digest, receipt=receipt)


def successor_state(root, unit_id, installed, files, ui, *, service_uid, service_gid, accept_replaced=False):
    """Verify an installed appliance is exactly the release it claims before replacing it.

    Nothing is written here. Every file this install would overwrite must either
    match the predecessor release's recorded digest, already match this release's
    bytes, or (for targets the predecessor release cannot account for, such as
    operator stopgaps at paths this release now owns) be explicitly accepted with
    --accept-replaced. Unit state outside the release write set is verified and
    left untouched.
    """
    receipt = installed['receipt']
    require(receipt.get('unit_id') == unit_id, 'installed receipt is for another unit')
    require(receipt.get('release_id') == installed['release_id'],
            'installed release is not the pinned predecessor')
    expected = predecessor_targets(installed['root'], installed['manifest'])
    replaced = {}
    for name, digest in sorted(payload_targets(files, ui).items()):
        current = installed_digest(root, name)
        if name in expected:
            require(current is not None, 'predecessor payload file missing: ' + name)
            require(current in (expected[name], digest),
                    'installed file differs from the predecessor release: ' + name)
        elif current is not None and current != digest:
            require(accept_replaced, 'unverifiable installed target would be replaced: ' + name)
            replaced[name] = current
    for name in SUCCESSOR_STATE_DIRS:
        path = root / name.lstrip('/')
        no_links(path)
        require(path.is_dir(), 'successor install requires existing state directory: ' + name)
        info = path.stat()
        require(info.st_uid == service_uid and info.st_gid == service_gid and not info.st_mode & 0o077,
                'successor state directory owner/mode changed: ' + name)
    require(installed_digest(root, DEVICE_LOCK_MARKER) == hashlib.sha256(b'1\n').hexdigest(),
            'successor install requires the existing device-lock marker')
    return replaced


def publish(path, data, mode, *, replace):
    """Write a new file, or atomically replace an already verified one."""
    if not replace or not os.path.lexists(path):
        write_new(path, data, mode)
        return
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe replacement target: ' + str(path))
    temporary = path.parent / ('.' + path.name + '.sage-new')
    no_links(temporary)
    require(not os.path.lexists(temporary), 'stale replacement file present: ' + str(temporary))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def install_release_runtime(release, manifest, *, root=Path('/'), run=None):
    """Install the release's pinned runtime wheels into the appliance venv.

    Successor installs only. Offline and pinned exactly like the base installer's
    line (numpy / sage-agent-sdk / sage-lantern-runtime / piper-tts from the
    release wheel directory); --no-deps and --force-reinstall because the versions
    do not change between releases while the bytes do. Every wheel in the
    directory must be listed in the release manifest with a matching digest.
    """
    run = run or subprocess.run
    entries = {entry['path']: entry for entry in manifest['files']}
    directory = release / 'software/bundle/python-wheels'
    for path in sorted(directory.glob('*.whl')):
        entry = entries.get('software/bundle/python-wheels/' + path.name)
        require(entry is not None, 'unlisted release wheel: ' + path.name)
        read_payload(path, entry)
    python = root / 'opt/sage-lantern/venv/bin/python'
    require(python.is_file() and not python.is_symlink(), 'missing appliance interpreter')
    environment = dict(PATH='/usr/bin:/bin', HOME='/nonexistent', PIP_NO_INDEX='1',
                       PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONDONTWRITEBYTECODE='1')
    run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', '--force-reinstall',
         '--find-links', str(directory),
         'numpy==1.26.4', 'sage-agent-sdk==11.19.10+lantern.20260906',
         'sage-lantern-runtime==0.2.0', 'piper-tts==1.4.2'],
        check=True, timeout=900, env=environment)
    run([str(python), str(release / 'scripts/verify-runtime-release.py'), '--installed'],
        check=True, timeout=120, env=environment)


def predecessor_release(root, pin):
    """The pinned predecessor release, already hash-checked by commissioning_binding."""
    manifest_path = Path(root) / 'manifest.json'
    no_links(manifest_path)
    raw = manifest_path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == pin, 'predecessor manifest differs from commissioned pin')
    manifest = json.loads(raw)
    require(type(manifest) is dict and type(manifest.get('release_id')) is str, 'invalid predecessor manifest')
    return dict(root=str(root), pin=pin, release_id=manifest['release_id'], manifest=manifest)


def provision_auth_drain(root, uid, gid):
    """Create fresh service-private state and mandatory drain directories only."""
    require(type(uid) is int and uid > 0 and type(gid) is int and gid > 0, 'non-root auth directory owner required')
    parent = root / 'var/lib/sage-lantern'
    no_links(parent)
    parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.mkdir('device-lock', mode=0o700, dir_fd=parent_fd)
        state_fd = os.open('device-lock', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            os.mkdir('drain', mode=0o700, dir_fd=state_fd)
            drain_fd = os.open('drain', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=state_fd)
            try:
                os.fchown(drain_fd, uid, gid)
                os.fchmod(drain_fd, 0o700)
                os.fsync(drain_fd)
            finally:
                os.close(drain_fd)
            os.fchown(state_fd, uid, gid)
            os.fchmod(state_fd, 0o700)
            os.fsync(state_fd)
        finally:
            os.close(state_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def provision_conversation_owner(root, uid, gid):
    require(type(uid) is int and uid > 0 and type(gid) is int and gid > 0,
            'non-root conversation owner required')
    parent = root / 'var/lib/sage-lantern'
    no_links(parent)
    for path in (parent, *parent.parents):
        info = path.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, uid)
                and (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX),
                'unsafe conversation ancestor')
        require(not set(os.listxattr(path)) & {'system.posix_acl_access', 'system.posix_acl_default'},
                'conversation ACL requires review')
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.mkdir('history', mode=0o700, dir_fd=parent_fd)
        history_fd = os.open('history', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            os.fchown(history_fd, uid, gid)
            os.fchmod(history_fd, 0o700)
            os.mkdir('conversations-v1', mode=0o700, dir_fd=history_fd)
            descriptor = os.open('conversations-v1', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=history_fd)
            try:
                os.fchown(descriptor, uid, gid)
                os.fchmod(descriptor, 0o700)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            preferences = json.dumps(dict(version=2, enabled=True, photos_enabled=True,
                                          generation=str(uuid.uuid4()), items=[]),
                                     separators=(',', ':')).encode('utf-8')
            descriptor = os.open('history.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=history_fd)
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchown(stream.fileno(), uid, gid)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(preferences)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(history_fd)
        finally:
            os.close(history_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def install_files(plan, rules, release, manifest, ui, *, allow_owner_setup, commissioning_manifest_sha256=None,
                  predecessor=None, accept_replaced=False, release_manifest_sha256=None, root=Path('/'), run=None):
    """Root prefix exists for synthetic filesystem tests, not a CLI bypass.

    Without a predecessor this is the fresh install: every target must be absent.
    With one, the unit already runs the pinned predecessor release and every file
    this install overwrites is verified against it first (see successor_state);
    unit state is checked and left in place, nothing is deleted, and no service is
    started or enabled.
    """
    files = payloads(plan, rules, release, manifest)
    service_uid = pwd.getpwnam('sage-lantern').pw_uid
    service_gid = grp.getgrnam('sage-lantern').gr_gid
    require(service_uid > 0 and service_gid > 0, 'non-root auth account/group required')
    replace = predecessor is not None
    if replace:
        require(not os.path.lexists(root / 'etc/sage-lantern/commissioning.pending.json'),
                'commissioning pending; installation forbidden')
        replaced = successor_state(root, plan['unit_id'], predecessor, files, ui,
                                   service_uid=service_uid, service_gid=service_gid,
                                   accept_replaced=accept_replaced)
    else:
        fresh(root, files)
        replaced = {}
    if not replace:
        write_new(root / DEVICE_LOCK_MARKER.lstrip('/'), b'1\n')
        provision_auth_drain(root, service_uid, service_gid)
        provision_conversation_owner(root, service_uid, service_gid)
    if allow_owner_setup and not os.path.lexists(root / VAULT_SETUP_MARKER.lstrip('/')):
        write_new(root / VAULT_SETUP_MARKER.lstrip('/'), b'allow-vault-initialize\n')
    for name, entry in sorted(ui.items()):
        data = read_payload(release / name, entry)
        publish(root / DESTINATION.lstrip('/') / name.removeprefix(APP), data, entry['mode'], replace=replace)
    for name, data in files.items():
        publish(root / name.lstrip('/'), data,
                0o755 if name.startswith(('/usr/local/libexec/', '/usr/local/bin/')) else 0o644, replace=replace)
    if replace:
        # The release's runtime wheel carries the sources the app services import;
        # it is installed before the receipt commits the new release.
        install_release_runtime(release, manifest, root=root, run=run)
    result = dict(schema='sage.lantern.fresh-app-install.v1', unit_id=plan['unit_id'], release_id=manifest['release_id'],
                  installed=True, services_started=False, services_enabled=False, lock_required=True,
                  owner_setup_permitted=allow_owner_setup, owner_setup_complete=False,
                  hardware_verified=False, backend_verified=False, provisioned=False, ready=False,
                  activation_required=True, commissioning_manifest_sha256=commissioning_manifest_sha256)
    if replace:
        result.update(predecessor_release_id=predecessor['release_id'],
                      predecessor_manifest_sha256=predecessor.get('manifest_sha256'),
                      replaced_unverifiable_targets=sorted(replaced),
                      restart_required=True, manifest_sha256=release_manifest_sha256)
    publish(root / INSTALLED_RECEIPT.lstrip('/'), (json.dumps(result, sort_keys=True) + '\n').encode(), 0o644,
            replace=replace)
    return result


def installed_source_closure(release, manifest, purelib):
    """Read installed Python source only; never instantiate clients or identities."""
    purelib = Path(purelib)
    no_links(purelib)
    runtime = {Path(entry['path']).name: entry['sha256'] for entry in manifest['files']
               if entry['role'] == 'runtime_source' and entry['path'].startswith('software/runtime/src/sage_lantern/')}
    builder = helper('installed_sdk_builder', 'scripts/build-sdk-release.py')
    staged = builder.staged_payloads(builder.snapshot(release / 'sources/sage-sdk'))
    sdk = {Path(name).name: hashlib.sha256(data).hexdigest() for name, data in staged.items()
           if name.startswith('src/sage_sdk/') and name.endswith('.py')}
    for package, expected in (('sage_lantern', runtime), ('sage_sdk', sdk)):
        directory = purelib / package
        no_links(directory)
        require(expected and {path.name for path in directory.glob('*.py')} == set(expected), 'installed module closure mismatch')
        for name, digest in expected.items():
            path = directory / name
            no_links(path)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 2 * 1024 * 1024, 'unsafe installed source')
                require(hashlib.sha256(stream.read(2 * 1024 * 1024 + 1)).hexdigest() == digest, 'installed source differs from release')


def onboarding_service_check(release, manifest, *, root=Path('/')):
    name = 'software/systemd/sage-lantern-personal-sage.service'
    entry = next(entry for entry in manifest['files'] if entry['path'] == name)
    expected = read_payload(release / name, entry)
    require(b'LOCAL_VOICE_READY' not in expected and b'ConditionPathExists=/etc/sage-lantern-device-lock.enabled\n' in expected,
            'personal SAGE onboarding gate missing')
    require(b'ExecStartPre=/opt/sage-lantern/bin/sage-gui check-lantern-private-config\n' in expected,
            'personal SAGE config capability gate missing')
    for setting in (b'REST_ADDR=127.0.0.1:8080', b'SAGE_CMT_RPC_ADDR=tcp://127.0.0.1:26657',
                    b'SAGE_CMT_P2P_ADDR=tcp://127.0.0.1:26656', b'SAGE_TLS_ADDR=127.0.0.1:8443',
                    b'SAGE_LANTERN_PRIVATE_LISTENERS=1'):
        require(b'Environment=' + setting + b'\n' in expected, 'personal SAGE loopback override missing')
    installed = root / 'etc/systemd/system/sage-lantern-personal-sage.service'
    require(read_payload(installed, entry) == expected, 'base personal SAGE unit needs reviewed onboarding update')
    for prefix in ('etc/systemd/system', 'run/systemd/system'):
        dropins = root / prefix / 'sage-lantern-personal-sage.service.d'
        no_links(dropins)
        require(not dropins.exists(), 'personal SAGE overrides require explicit review')


def kiosk_gdm_candidate(raw, account):
    require(re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', account) and account not in {'root', 'sage-lantern', 'nobody'}, 'invalid kiosk user')
    text = raw.decode('utf-8')
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(text)
    require(not parser.defaults() and 'daemon' in parser, 'explicit GDM daemon section required')
    controlled = {'AutomaticLoginEnable', 'AutomaticLogin', 'TimedLoginEnable', 'TimedLogin', 'DefaultSession', 'WaylandEnable'}
    for section in parser:
        for key, value in parser[section].items():
            if key.lower() in {name.lower() for name in controlled}:
                require(section == 'daemon' and key in controlled, 'ambiguous GDM setting')
                require(key in {'AutomaticLoginEnable', 'TimedLoginEnable', 'WaylandEnable'} and value == 'false', 'existing GDM login/session policy conflicts')
    headers = list(re.finditer(r'(?m)^\[daemon\][ \t]*(?:\n|$)', text))
    require(len(headers) == 1, 'unambiguous daemon header required')
    lines = text.splitlines(keepends=True)
    output, daemon = [], False
    for line in lines:
        if line.strip().startswith('['):
            daemon = line.strip() == '[daemon]'
        if daemon and re.match(r'^[ \t]*(AutomaticLoginEnable|TimedLoginEnable|WaylandEnable)[ \t]*=', line):
            continue
        output.append(line)
        if line.strip() == '[daemon]':
            if not line.endswith('\n'):
                output.append('\n')
            output.append(f'AutomaticLoginEnable=true\nAutomaticLogin={account}\nTimedLoginEnable=false\nWaylandEnable=false\n')
    return ''.join(output).encode()


def kiosk_plan(account, expected_digest, *, root=Path('/')):
    require(type(expected_digest) is str and re.fullmatch(r'[0-9a-f]{64}', expected_digest), 'reviewed GDM base hash required')
    gdm = root / 'etc/gdm3/daemon.conf'
    no_links(gdm)
    if root == Path('/'):
        for parent in gdm.parents:
            information = parent.stat()
            require(stat.S_ISDIR(information.st_mode) and information.st_uid == 0 and not information.st_mode & 0o022, 'untrusted GDM ancestor')
    descriptor = os.open(gdm, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 65536 and not info.st_mode & 0o022, 'unsafe GDM base')
        if root == Path('/'):
            require(info.st_uid == 0, 'GDM must be root-owned')
        original = stream.read(65537)
    require(hashlib.sha256(original).hexdigest() == expected_digest, 'reviewed GDM hash mismatch')
    candidate = kiosk_gdm_candidate(original, account)
    absent = ('etc/gdm3/custom.conf', 'etc/gdm/custom.conf', 'etc/sddm.conf', 'etc/sddm.conf.d',
              'etc/lightdm', f'var/lib/AccountsService/users/{account}', f'home/{account}/.dmrc',
              'var/lib/sage-lantern/recovery/kiosk', 'etc/sage-lantern/kiosk-boot.json')
    for name in absent:
        path = root / name
        no_links(path)
        require(not path.exists(), 'existing display/session configuration requires separate review')
    for prefix in ('etc/systemd/system', 'run/systemd/system'):
        units = root / prefix
        for pattern in ('getty@*.service*', 'serial-getty@*.service*', 'getty.target', 'getty.target.d', 'display-manager.service.d', 'gdm*.service.d'):
            require(not list(units.glob(pattern)), 'existing getty/display override')
    for tree in ('etc/systemd/system/getty.target.wants', 'etc/systemd/system/multi-user.target.wants'):
        for pattern in ('getty*', 'serial-getty*'):
            for path in (root / tree).glob(pattern):
                require(path.is_symlink() and str(path.resolve()).startswith(('/usr/lib/systemd/system/', '/lib/systemd/system/')), 'custom getty enablement')
    return dict(account=account, original=original, candidate=candidate, mode=stat.S_IMODE(info.st_mode),
                base_sha256=expected_digest)


def kiosk_native_gate():
    for name in ('/usr/sbin/gdm3', '/usr/bin/chromium', '/usr/bin/kwin_x11', '/usr/bin/xprop', '/usr/bin/xset', '/usr/bin/xrandr', '/usr/bin/curl'):
        require(Path(name).is_file() and os.access(name, os.X_OK), 'missing GDM/X11 kiosk dependency')
    manager = Path('/etc/X11/default-display-manager')
    no_links(manager)
    require(manager.read_text().strip() == '/usr/sbin/gdm3', 'GDM3 baseline required')
    for command, allowed in ((['systemctl', 'get-default'], {'graphical.target'}),
                             (['systemctl', 'is-enabled', 'gdm3.service'], {'enabled', 'alias'}),
                             (['systemctl', 'show', 'gdm3.service', '-p', 'ActiveState', '--value'], {'inactive'})):
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=5)
        require(result.stdout.strip() in allowed, 'GDM baseline not quiescent/boot-enabled')


def configure_kiosk_boot(plan, *, root=Path('/'), run=subprocess.run):
    """Explicit opt-in only; enable next-boot services, never restart GDM/unlock."""
    fresh_plan = kiosk_plan(plan['account'], plan['base_sha256'], root=root)
    require(fresh_plan == plan, 'GDM baseline changed')
    marker = root / 'etc/sage-lantern-device-lock.enabled'
    no_links(marker)
    require(marker.read_bytes() == b'1\n', 'lock policy required before OS autologin')
    receipt = json.loads((root / 'etc/sage-lantern/appliance-installed.json').read_text())
    require(receipt.get('lock_required') is True and receipt.get('ready') is False, 'locked app install required')
    backup = root / 'var/lib/sage-lantern/recovery/kiosk/gdm3-daemon.conf.before-kiosk'
    write_new(backup, plan['original'], 0o600)
    backup.parent.chmod(0o700)
    account = root / f'var/lib/AccountsService/users/{plan["account"]}'
    write_new(account, b'[User]\nSession=sage-lantern\nXSession=sage-lantern\nSystemAccount=false\n', 0o600)
    services = ['sage-lantern-device-auth.service', 'sage-lantern-status.service', 'sage-lantern-fleet-admin.service']
    run(['systemctl', 'daemon-reload'], check=True, capture_output=True, timeout=10)
    run(['systemctl', 'enable', *services], check=True, capture_output=True, timeout=15)
    gdm = root / 'etc/gdm3/daemon.conf'
    require(gdm.read_bytes() == plan['original'], 'GDM changed before commit')
    descriptor, temporary = tempfile.mkstemp(prefix='.lantern-gdm-', dir=gdm.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), plan['mode'])
            stream.write(plan['candidate'])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, gdm)
        parent_fd = os.open(gdm.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    result = dict(schema='sage.lantern.kiosk-boot.v1', os_autologin_configured=True,
                  account=plan['account'], session='sage-lantern', services_enabled=services,
                  services_started=False, private_sage_unlocked=False, ready=False, provisioned=False,
                  reboot_acceptance_required=True, previous_gdm_sha256=plan['base_sha256'])
    write_new(root / 'etc/sage-lantern/kiosk-boot.json', (json.dumps(result, sort_keys=True) + '\n').encode())
    return result


def node_preflight():
    executable = '/usr/bin/node'
    require(Path(executable).is_file() and os.access(executable, os.X_OK),
            'preinstall Node 22.13+ at /usr/bin/node; operator PATH is not used')
    version = subprocess.run([executable, '-p', 'JSON.stringify(process.versions.node.split(".").map(Number))'],
                             check=True, capture_output=True, text=True, timeout=5,
                             env=dict(PATH='/usr/bin:/bin', HOME='/nonexistent'))
    numbers = json.loads(version.stdout)
    require(type(numbers) is list and len(numbers) == 3
            and all(type(number) is int and number >= 0 for number in numbers), 'invalid Node version response')
    require(tuple(numbers) >= (22, 13, 0), 'Node 22.13+ required')


def native_preflight(args):
    require(os.geteuid() == 0 and sys.platform == 'linux' and platform.machine() == 'aarch64', 'root Linux ARM64 required')
    require(args.prepared_metadata == '/etc/sage-lantern/unit.env', 'live unit metadata required')
    require(pwd.getpwnam('sage-lantern').pw_uid != 0, 'non-root service account required')
    operator = pwd.getpwnam(args.account)
    require(operator.pw_uid > 0 and operator.pw_dir == '/home/' + args.account, 'explicit kiosk account/home required')
    for name in ('/etc/systemd/system', '/etc/sage-lantern', '/etc/udev/rules.d', '/opt/sage-lantern', '/usr/local/libexec', '/usr/local/bin', '/usr/share/xsessions', '/usr/local/share/sage-lantern'):
        path = Path(name)
        no_links(path)
        for parent in (path, *path.parents):
            if parent.exists():
                info = parent.stat()
                require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'unsafe installation parent')
    for name in ('/usr/bin/node', '/opt/sage-lantern/venv/bin/python'):
        require(Path(name).is_file() and os.access(name, os.X_OK), 'missing preinstalled interpreter')
    node_preflight()
    for service in ('sage-lantern-fleet-admin', 'sage-lantern-device-auth', 'sage-lantern-voice-loop', 'sage-lantern-mesh-runtime', 'sage-lantern-status'):
        result = subprocess.run(['systemctl', 'show', service, '-p', 'ActiveState', '--value'], capture_output=True, text=True, timeout=5, check=True)
        require(result.stdout.strip() in {'inactive', 'failed'}, 'appliance service not quiescent')
    python = '/opt/sage-lantern/venv/bin/python'
    environment = dict(PATH='/usr/bin:/bin', HOME='/nonexistent', PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONDONTWRITEBYTECODE='1')
    code = ('import importlib.metadata as metadata,json,sys,sysconfig; '
            'print(json.dumps(dict(python=list(sys.version_info[:2]),purelib=sysconfig.get_path("purelib"),'
            'versions={name:metadata.version(name) for name in '
            '["sage-lantern-runtime","sage-agent-sdk","PyNaCl","cryptography"]})))')
    result = subprocess.run([python, '-I', '-B', '-c', code], env=environment, capture_output=True, text=True, timeout=10, check=True)
    installed = json.loads(result.stdout)
    require(installed['python'] == [3, 11] and installed['purelib'] == '/opt/sage-lantern/venv/lib/python3.11/site-packages', 'installed interpreter target mismatch')
    subprocess.run([python, '-I', '-B', '-m', 'pip', 'check'], env=environment, capture_output=True, text=True, timeout=30, check=True)
    return installed


def commissioning_binding(args, manifest_digest, *, root=Path('/')):
    """Select the manifest the commissioning receipt must match; never silently widens it."""
    pin = getattr(args, 'commissioned_manifest_sha256', None)
    predecessor_root = getattr(args, 'predecessor_release_root', None)
    if pin is None:
        require(predecessor_root is None, 'predecessor release root without commissioned manifest pin')
        return manifest_digest
    require(type(pin) is str and re.fullmatch(r'[0-9a-f]{64}', pin), 'invalid commissioned manifest pin')
    require(type(predecessor_root) is str and Path(predecessor_root).is_absolute()
            and predecessor_root == str(Path(predecessor_root)), 'absolute predecessor release root required')
    stage = helper('commissioning_stage', 'scripts/stage-appliance-release.py')
    manifest_path = Path(predecessor_root) / 'manifest.json'
    no_links(manifest_path)
    descriptor = os.open(manifest_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 64 * 1024 * 1024,
                'unsafe predecessor manifest')
        raw = stream.read(64 * 1024 * 1024 + 1)
    require(hashlib.sha256(raw).hexdigest() == pin, 'predecessor manifest differs from commissioned pin')
    try:
        predecessor = json.loads(raw)
    except ValueError:
        raise ValueError('invalid predecessor manifest')
    rows = predecessor.get('files') if type(predecessor) is dict else None
    require(type(rows) is list, 'invalid predecessor manifest')
    entry = next((row for row in rows if type(row) is dict and row.get('path') == stage.BINARY), None)
    require(type(entry) is dict and type(entry.get('sha256')) is str
            and re.fullmatch(r'[0-9a-f]{64}', entry['sha256']), 'predecessor release lacks native binary binding')
    installed = root / 'opt/sage-lantern/bin/sage-gui'
    no_links(installed)
    descriptor = os.open(installed, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        installed_info = os.fstat(stream.fileno())
        require(stat.S_ISREG(installed_info.st_mode) and installed_info.st_nlink == 1,
                'unsafe installed native binary')
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    require(digest.hexdigest() == entry['sha256'], 'installed native binary differs from commissioning release')
    return pin


def main(argv=None):
    sys.dont_write_bytecode = True
    parser = helper('unit_config_parser', HELPERS[0]).parser()
    parser.description = __doc__
    for name in ('release-root', 'manifest'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--operator-approved-release', action='store_true')
    parser.add_argument('--allow-owner-vault-setup', action='store_true')
    parser.add_argument('--enable-kiosk-autologin', action='store_true', help='Explicit fresh GDM3/X11 OS login and app next-boot enablement; never unlocks SAGE')
    parser.add_argument('--gdm-base-sha256', help='Reviewed exact existing GDM daemon.conf SHA256; required with kiosk opt-in')
    parser.add_argument('--alsa-device', required=True, help='Operator-verified hw:CARD=<stable ALSA ID>,DEV=<capture device>; never inferred from A')
    parser.add_argument('--commissioned-manifest-sha256', help='Exact manifest that commissioned this unit when it differs from this release')
    parser.add_argument('--predecessor-release-root', help='On-unit root of the commissioned release (manifest.json bind); required with the pin')
    parser.add_argument('--installed-release-root',
                        help='On-unit root of the release currently installed; required to succeed an existing installation')
    parser.add_argument('--install', action='store_true', help='Install fresh native files; kiosk opt-in may enable next boot, never starts services')
    parser.add_argument('--accept-replaced', action='store_true',
                        help='Successor install only: allow replacing existing targets the pinned predecessor release cannot account for '
                             '(for example operator stopgaps at paths this release now owns); refused by default')
    args = parser.parse_args(argv)
    try:
        require(bool(args.gdm_base_sha256) == args.enable_kiosk_autologin, 'GDM hash and kiosk opt-in must be supplied together')
        prepared = prepare(args)
        manifest_digest = hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest()
        commissioning_digest = commissioning_binding(args, manifest_digest)
        if args.install:
            commissioning = helper('app_commissioning', 'scripts/lantern/provision-fresh-appliance.py')
            commissioning.commission_completion_check(args.unit_id, commissioning_digest)
            installed = native_preflight(args)
            components = prepared[3]['selection']['components']
            require(installed['versions'] == {'sage-lantern-runtime': components['runtime'], 'sage-agent-sdk': components['sdk'],
                    'PyNaCl': '1.6.2', 'cryptography': '46.0.3'}, 'installed component version mismatch')
            predecessor = None
            if os.path.lexists(Path('/') / INSTALLED_RECEIPT.lstrip('/')):
                require(args.installed_release_root is not None,
                        'installed appliance requires --installed-release-root to succeed')
                predecessor = installed_release(args.installed_release_root)
                # The installed interpreter must be exactly the release being
                # succeeded before this install replaces it; the new closure is
                # checked after the wheels are installed.
                installed_source_closure(Path(predecessor['root']) / 'payload', predecessor['manifest'],
                                         installed['purelib'])
            else:
                require(not args.accept_replaced, 'accept-replaced requires an installed predecessor release')
                installed_source_closure(prepared[2], prepared[3], installed['purelib'])
            onboarding_service_check(prepared[2], prepared[3])
            kiosk = None
            if args.enable_kiosk_autologin:
                kiosk = kiosk_plan(args.account, args.gdm_base_sha256)
                kiosk_native_gate()
            commissioning.commission_completion_check(args.unit_id, commissioning_digest)
            result = install_files(*prepared, allow_owner_setup=args.allow_owner_vault_setup,
                                   commissioning_manifest_sha256=commissioning_digest,
                                   predecessor=predecessor, accept_replaced=args.accept_replaced,
                                   release_manifest_sha256=manifest_digest)
            if predecessor is not None:
                installed_source_closure(prepared[2], prepared[3], installed['purelib'])
            if kiosk is not None:
                result['kiosk_boot'] = configure_kiosk_boot(kiosk)
                result['services_enabled'] = True
        else:
            result = dict(schema='sage.lantern.fresh-app-install.v1', unit_id=prepared[0]['unit_id'],
                          mode='check-only', release_id=prepared[3]['release_id'], installed=False,
                          commissioning_manifest_sha256=commissioning_digest,
                          kiosk_autologin_requested=args.enable_kiosk_autologin, target_display_verified=False,
                          ready=False, provisioned=False, hardware_verified=False, activation_required=True)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, configparser.Error, subprocess.SubprocessError) as refusal:
        reason = ' '.join(str(refusal).split()) or type(refusal).__name__
        print(f'Fresh app installation rejected or incomplete ({reason}); retain lock containment. No services started; boot enablement may be partial.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
