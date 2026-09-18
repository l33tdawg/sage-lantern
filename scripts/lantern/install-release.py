#!/usr/bin/env python3
"""Install one reviewed Lantern release as a transaction over code and pins.

Runs on the unit as root, with the release directory on local disk. A release is
not a file copy: it carries the runtime wheel that describes the deployed bytes,
the startup artifacts that pin it, the baseline of the live state it was built
from and the allowance (if any) that excuses a reviewed prior deviation. This
installer verifies every digest, stages the whole transaction under a root-0700
directory, mints the baseline again from the live state and refuses if the unit
moved since the release was built, requires a held owner session, and only then
publishes the coordinator and `/etc/sage-lantern/startup.json`.

Nothing historical is rewritten: the previous config, coordinator and pinned
artifacts are preserved byte-for-byte as `*.before`, the boot's own journal is
never touched, and `latest.json` only moves after a verified completion.

    verify    --release DIR
    stage     --release DIR
    preflight --release DIR
    apply     --release DIR --mode next-boot|recover-spent
    rollback  --release DIR
    accept    --release DIR
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import types
import uuid


CONFIG = Path('/etc/sage-lantern/startup.json')
COORDINATOR = Path('/opt/sage-lantern/libexec/startup-coordinator.py')
JOURNALS = Path('/var/lib/sage-lantern-startup')
ATTEMPTS = Path('/var/lib/sage-lantern-cross-boot-audio')
FENCE = Path('/var/lib/sage-lantern-mesh-supervisor/fence.json')
PYTHON = '/opt/sage-lantern/venv/bin/python'
BASELINE_FIELDS = ('inventory', 'runtime_sources', 'files', 'commission_sha256', 'actor', 'model_argv')
SESSION_WINDOW = 15
SESSION_INTERVAL = 1.0
SERVICES = ('sage-lantern-llm', 'sage-lantern-mesh-runtime', 'sage-lantern-voice-loop')
PLACED = {'runtime/sage_lantern_runtime-0.2.0-py3-none-any.whl': 'sage_lantern_runtime-0.2.0-py3-none-any.whl',
          'tools/startup-audio-adapter.py': 'startup-audio-adapter.py',
          'tools/startup-coordinator.py': 'startup-coordinator.py',
          'tools/collect-cross-boot-audio-pins.py': 'collect-cross-boot-audio-pins.py',
          'tools/recover-cross-boot-audio.py': 'recover-cross-boot-audio.py',
          'tools/release-snapshot.py': 'release-snapshot.py',
          'state/baseline.json': 'baseline.json',
          'state/policy.json': 'policy.json',
          'state/startup.json': 'startup.json'}


def require(value, detail=None):
    if not value:
        raise ValueError('release_install_refused' + (f': {detail}' if detail else ''))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def read(path):
    return Path(path).read_bytes()


def unique(items):
    result = {}
    for name, value in items:
        require(name not in result)
        result[name] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique)


def pin(path):
    raw = read(path)
    return {'path': str(path), 'sha256': sha(raw)}, raw


def write(path, raw):
    with Path(path).open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(Path(path).parent)


def write_or_verify(path, raw):
    path = Path(path)
    if path.exists():
        require(path.read_bytes() == raw)
        return
    write(path, raw)


def replace(path, raw):
    path = Path(path)
    temporary = path.with_name(path.name + '.release-new')
    write(temporary, raw)
    os.replace(temporary, path)
    sync(path.parent)


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def root_directory(raw):
    path = Path(raw)
    require(path.is_absolute() and '..' not in path.parts and path.parent == Path('/var/lib'))
    require(re.fullmatch(r'/var/lib/sage-lantern-release-[a-z0-9-]+-[0-9]{8}-[0-9]{6}',
                         str(path)) is not None)
    return path


def load_module(path, name):
    raw = read(path)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def consumer_boot(current, prior_boot):
    """The boot that will spend the release's tolerance.

    The adapter binds a prior to a boot other than the prior's own. When the
    durable pointer already names the boot being installed on, the release is
    spent by the next, not-yet-existing boot, so a synthetic id stands in for it.
    """
    require(str(uuid.UUID(current)) == current and str(uuid.UUID(prior_boot)) == prior_boot)
    if prior_boot != current:
        return current
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'sage-lantern-next-boot:' + current))


def verify_release(release):
    """Step 1: every digest in the manifest, and the paths it names."""
    release = Path(release)
    manifest = decode(read(release / 'manifest.json'))
    require(manifest.get('schema') == 'sage.lantern.release.v1'
            and manifest.get('config_path') == str(CONFIG)
            and manifest.get('coordinator_path') == str(COORDINATOR))
    root = root_directory(manifest['root'])
    listed = manifest['files']
    require(type(listed) is dict and listed)
    actual = {}
    for base, directories, names in os.walk(release):
        directories.sort()
        for name in sorted(names):
            path = Path(base) / name
            relative = str(path.relative_to(release))
            if relative != 'manifest.json':
                actual[relative] = sha(read(path))
    require(set(actual) == set(listed))
    for relative, digest in listed.items():
        require(actual[relative] == digest, relative)
    require(set(listed) == set(PLACED) | ({'state/allowance.json'} if 'state/allowance.json' in listed else set()))
    return manifest, root


def require_install_boot(manifest):
    """An install may only run on the boot the release was built for. The boot
    that later *spends* the release (and accepts it) is a different one."""
    require(manifest['boot_id'] == boot_id(), 'release built for another boot')


def observe():
    """The live production pins this transaction has to preserve and replace."""
    config_raw = read(CONFIG)
    config = decode(config_raw)
    coordinator, coordinator_raw = pin(COORDINATOR)
    require(coordinator['sha256'] == config['coordinator']['sha256']
            and config['coordinator']['path'] == str(COORDINATOR))
    policy, policy_raw = pin(config['policy']['path'])
    require(policy['sha256'] == config['policy']['sha256'])
    policy_document = decode(policy_raw)
    require(set(policy_document) == {'schema', 'helper', 'collector', 'runtime_wheel', 'baseline'}
            and policy_document['schema'] == 'sage.lantern.startup-audio-policy.v1')
    artifacts = {}
    for name in ('helper', 'collector', 'runtime_wheel', 'baseline'):
        pin_value, raw = pin(policy_document[name]['path'])
        require(pin_value['sha256'] == policy_document[name]['sha256'])
        artifacts[name] = (pin_value, raw)
    require((JOURNALS / 'latest.json').exists(), 'no durable pointer to preserve')
    pointer, pointer_raw = pin(JOURNALS / 'latest.json')
    document = decode(pointer_raw)
    prior, prior_raw = pin(document['path'])
    require(prior['sha256'] == document['sha256'])
    fence, fence_raw = pin(FENCE)
    boot = boot_id()
    require(str(uuid.UUID(boot)) == boot)
    return dict(boot=boot, config=config, config_raw=config_raw, config_pin={'path': str(CONFIG),
                'sha256': sha(config_raw)}, coordinator=coordinator, coordinator_raw=coordinator_raw,
                policy=policy, policy_raw=policy_raw, policy_document=policy_document,
                artifacts=artifacts, pointer=pointer, pointer_raw=pointer_raw, prior=prior,
                prior_raw=prior_raw, fence=fence, fence_raw=fence_raw,
                journal=JOURNALS / (boot + '.jsonl'), state_dir=ATTEMPTS / boot)


def require_held_session(window=SESSION_WINDOW):
    """The lifecycle asserts one access epoch across its run: require a held one."""
    from sage_lantern.device_access import access_epoch
    epoch, held = None, None
    deadline = time.monotonic() + window * 6 + 5
    while True:
        require(time.monotonic() < deadline, 'owner session not held')
        value = access_epoch()
        now = time.monotonic()
        if value is None or value != epoch:
            epoch, held = value, now
        elif now - held >= window:
            return epoch
        time.sleep(SESSION_INTERVAL)


def snapshot(root, manifest):
    """Run the pinned snapshot tool; the only thing that can mint a baseline."""
    helper = manifest['artifacts']['helper']
    collector = manifest['artifacts']['collector']
    wheel = manifest['artifacts']['runtime_wheel']
    for artifact in (helper, collector, wheel):
        require(artifact['path'] == str(root / Path(artifact['path']).name))
    command = [PYTHON, '-I', '-B', str(root / 'release-snapshot.py'),
               '--helper', str(root / 'recover-cross-boot-audio.py'), '--helper-sha256', helper['sha256'],
               '--collector', str(root / 'collect-cross-boot-audio-pins.py'), '--collector-sha256', collector['sha256'],
               '--wheel', str(root / Path(wheel['path']).name), '--wheel-sha256', wheel['sha256']]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    require(result.returncode == 0, result.stderr[-400:])
    document = decode(result.stdout.encode())
    require(document['schema'] == 'sage.lantern.release-snapshot.v1')
    return document


def stage(release, manifest, root, observed):
    require(os.geteuid() == 0 and sys.prefix == '/opt/sage-lantern/venv')
    require(not root.exists())
    epoch = require_held_session()
    root.mkdir(mode=0o700)
    sync(Path('/var/lib'))
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o700)
    for relative, name in sorted(PLACED.items()):
        write(root / name, read(Path(release) / relative))
    if 'state/allowance.json' in manifest['files']:
        write(root / 'allowance.json', read(Path(release) / 'state' / 'allowance.json'))

    # Preserve the originals byte-for-byte before anything else is interpreted.
    write_or_verify(root / 'startup.json.before', observed['config_raw'])
    write_or_verify(root / 'coordinator.py.before', observed['coordinator_raw'])
    write_or_verify(root / 'policy.json.before', observed['policy_raw'])
    write_or_verify(root / 'latest.json.before', observed['pointer_raw'])
    write_or_verify(root / 'fence.json.before', observed['fence_raw'])
    write_or_verify(root / 'prior-lifetime.json.before', observed['prior_raw'])
    for name, (pin_value, raw) in sorted(observed['artifacts'].items()):
        write_or_verify(root / (Path(pin_value['path']).name + '.before'), raw)
    for name in ('adapter', 'allowance'):
        if name in observed['config']:
            pin_value, raw = pin(observed['config'][name]['path'])
            require(pin_value['sha256'] == observed['config'][name]['sha256'])
            write_or_verify(root / (Path(pin_value['path']).name + '.before'), raw)

    # Step 4: mint the baseline from the verified live state, and refuse if the
    # unit has moved since the release was built to describe it.
    fresh = snapshot(root, manifest)
    baseline = {name: fresh['baseline'][name] for name in BASELINE_FIELDS}
    require(set(baseline) == set(BASELINE_FIELDS))
    require(canonical(baseline) == read(root / 'baseline.json'))
    require(fresh['boot_id'] == observed['boot'])
    policy = decode(read(root / 'policy.json'))
    for name in ('helper', 'collector', 'runtime_wheel', 'baseline'):
        require(policy[name]['path'] == str(root / Path(policy[name]['path']).name))
    require(policy['baseline'] == {'path': str(root / 'baseline.json'),
                                   'sha256': sha(read(root / 'baseline.json'))})
    config = decode(read(root / 'startup.json'))
    require(config['initial_lifetime'] == observed['config']['initial_lifetime'],
            'the release must carry this unit\'s own seed lifetime')
    require(config['adapter']['path'] == str(root / 'startup-audio-adapter.py'))
    require(config['coordinator']['path'] == str(COORDINATOR))
    coordinator = load_module(root / 'startup-coordinator.py', 'staged_coordinator')
    coordinator.validate_full_config(config)
    coordinator.verify_wheel(policy['runtime_wheel'])
    adapter = load_module(root / 'startup-audio-adapter.py', 'staged_adapter')
    require(adapter.HELPER_SHA == policy['helper']['sha256']
            and adapter.COLLECTOR_SHA == policy['collector']['sha256'])
    allowance = decode(read(root / 'allowance.json')) if 'allowance' in config else None
    adapter.validate_prior(fresh['prior'], fresh['fence']['document'], fresh['baseline'],
                           consumer_boot(observed['boot'], fresh['prior']['boot_id']), allowance,
                           fence_sha256=fresh['fence']['sha256'], prior_pin=fresh['prior_pin'])
    write(root / 'staged.json', canonical(dict(
        schema='sage.lantern.release-staged.v1', boot_id=observed['boot'],
        manifest=sha(read(Path(release) / 'manifest.json')), epoch=epoch,
        staged_at=int(time.time()), config_sha256=sha(read(root / 'startup.json')),
        policy_sha256=sha(read(root / 'policy.json')),
        baseline_sha256=sha(read(root / 'baseline.json')),
        coordinator_sha256=sha(read(root / 'startup-coordinator.py')),
        preserved={'config': sha(observed['config_raw']),
                   'coordinator': sha(observed['coordinator_raw']),
                   'pointer': observed['pointer'], 'fence': observed['fence']},
        allowance=allowance is not None, historical_outcomes='unknown')))
    print('Release staged under', root, '- production unchanged.', flush=True)


def preflight(release, manifest, root, observed, mode):
    require((root / 'staged.json').exists())
    staged = decode(read(root / 'staged.json'))
    require(staged['manifest'] == sha(read(Path(release) / 'manifest.json')))
    epoch = require_held_session()
    fresh = snapshot(root, manifest)
    baseline = {name: fresh['baseline'][name] for name in BASELINE_FIELDS}
    require(canonical(baseline) == read(root / 'baseline.json'))
    policy = decode(read(root / 'policy.json'))
    config = decode(read(root / 'startup.json'))
    allowance = decode(read(root / 'allowance.json')) if 'allowance' in config else None
    adapter = load_module(root / 'startup-audio-adapter.py', 'preflight_adapter')
    adapter.validate_prior(fresh['prior'], fresh['fence']['document'], fresh['baseline'],
                           consumer_boot(observed['boot'], fresh['prior']['boot_id']), allowance,
                           fence_sha256=fresh['fence']['sha256'], prior_pin=fresh['prior_pin'])
    coordinator = load_module(root / 'startup-coordinator.py', 'preflight_coordinator')
    coordinator.validate_full_config(config)
    coordinator.verify_wheel(policy['runtime_wheel'])
    require(adapter.HELPER_SHA == policy['helper']['sha256']
            and adapter.COLLECTOR_SHA == policy['collector']['sha256'])
    require(read(COORDINATOR) == observed['coordinator_raw'])
    require(read(CONFIG) == observed['config_raw'])

    attempt = fresh['attempt']
    unspent = not attempt['journal_exists'] and not attempt['state_dir_exists']
    prefer = unspent and fresh['prior']['boot_id'] != observed['boot']
    mode_used, detail = 'structural', 'the current boot already holds a lifecycle attempt'
    if prefer:
        command = [PYTHON, '-I', '-B', str(root / 'startup-audio-adapter.py'),
                   '--policy', str(root / 'policy.json'), '--policy-sha256', sha(read(root / 'policy.json')),
                   '--prior', observed['prior']['path'], '--prior-sha256', observed['prior']['sha256'],
                   '--boot-id', observed['boot'], '--access-epoch', epoch]
        if allowance is not None:
            command += ['--allowance', str(root / 'allowance.json'),
                        '--allowance-sha256', sha(read(root / 'allowance.json'))]
        result = subprocess.run(command, capture_output=True, text=True, timeout=540)
        require(result.returncode == 0, result.stdout + result.stderr[-400:])
        report = decode(result.stdout.encode())
        require(report.get('preflight_verified') is True and report.get('services_started') is False)
        mode_used, detail = 'adapter', 'adapter preflight verified with --apply omitted'
    require(mode in ('next-boot', 'recover-spent'))
    if mode == 'next-boot':
        require(attempt['journal_exists'],
                'the boot lifecycle has not been spent yet; stop sage-lantern-startup.service first')
    else:
        require(attempt['journal_exists'] and not attempt['state_dir_exists']
                and fresh['prior']['boot_id'] != observed['boot'],
                'recover-spent needs a spent attempt and an absent state directory')
    write(root / 'preflight.json', canonical(dict(
        schema='sage.lantern.release-preflight.v1', boot_id=observed['boot'], mode=mode,
        mode_used=mode_used, detail=detail, epoch=epoch, checked_at=int(time.time()),
        snapshot=dict(boot_id=fresh['boot_id'], pointer=sha(canonical(fresh['pointer'])),
                      fence=fresh['fence']['sha256'], baseline=sha(canonical(baseline))),
        services_started=False, production_unchanged=True)))
    print(f'Preflight verified ({mode_used}); nothing in production changed.', flush=True)


def apply(release, manifest, root, observed, mode):
    require((root / 'preflight.json').exists())
    require(not (root / 'apply-intent.json').exists() and not (root / 'acceptance.json').exists(),
            'this release has already been applied')
    staged = decode(read(root / 'staged.json'))
    require(staged['manifest'] == sha(read(Path(release) / 'manifest.json')))
    require(read(COORDINATOR) == observed['coordinator_raw']
            and read(CONFIG) == observed['config_raw'], 'production moved since stage')
    epoch = require_held_session()
    journal_before = sha(read(observed['journal'])) if observed['journal'].exists() else None
    write(root / 'apply-intent.json', canonical(dict(
        schema='sage.lantern.release-apply.v1', boot_id=observed['boot'], mode=mode,
        epoch=epoch, attempted_at=int(time.time()), prior=observed['pointer'],
        preserved={'config': sha(observed['config_raw']),
                   'coordinator': sha(observed['coordinator_raw'])},
        historical_outcomes='unknown')))
    replace(COORDINATOR, read(root / 'startup-coordinator.py'))
    replace(CONFIG, read(root / 'startup.json'))
    require(sha(read(COORDINATOR)) == staged['coordinator_sha256'])
    require(sha(read(CONFIG)) == staged['config_sha256'])
    record = dict(schema='sage.lantern.release-complete.v1', boot_id=observed['boot'],
                  mode=mode, published_at=int(time.time()),
                  coordinator={'path': str(COORDINATOR), 'sha256': staged['coordinator_sha256']},
                  config={'path': str(CONFIG), 'sha256': staged['config_sha256']},
                  pointer_advanced=False, historical_outcomes='unknown',
                  another_reboot_tested=False)
    if mode == 'next-boot':
        record['lifecycle_for_current_boot'] = 'spent or not due; the installed release runs at the next boot'
    else:
        completed = run_operator_lifecycle(root, observed, epoch)
        replace(JOURNALS / 'latest.json', canonical(completed))
        record['pointer_advanced'] = True
        record['lifetime'] = completed
        record['lifecycle_for_current_boot'] = 'completed in an operator journal'
    if journal_before is not None:
        require(sha(read(observed['journal'])) == journal_before,
                'the boot journal must never be rewritten')
    write(root / 'complete.json', canonical(record))
    print('Release published;', record['lifecycle_for_current_boot'], flush=True)


def run_operator_lifecycle(root, observed, epoch):
    """The reviewed recovery path for a boot whose own attempt is already spent."""
    # Loaded from the published path: the coordinator refuses to run as a copy.
    coordinator = load_module(COORDINATOR, 'operator_coordinator')
    config_raw = read(CONFIG)
    config = decode(config_raw)
    operator = root / 'operator-journal'
    require(not operator.exists())
    operator.mkdir(mode=0o700)
    sync(root)
    write(operator / 'latest.json', observed['pointer_raw'])
    coordinator.JOURNALS = operator
    backend = coordinator.FullLifecycle(config, config_raw)
    journal = coordinator.Journal(observed['boot'], sha(config_raw))
    try:
        fcntl.flock(journal.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = coordinator.run_full_attempt(backend, journal, epoch)
    finally:
        journal.close()
    require(read(JOURNALS / 'latest.json') == observed['pointer_raw'],
            'the durable pointer moved during the operator lifecycle')
    return completed


def rollback(release, manifest, root, observed):
    # Undo is available until the release is accepted: the boot is the evidence.
    require((root / 'apply-intent.json').exists() and not (root / 'acceptance.json').exists())
    require(read(CONFIG) == read(root / 'startup.json'))
    require(sha(read(COORDINATOR)) == sha(read(root / 'startup-coordinator.py')))
    require(read(JOURNALS / 'latest.json') == read(root / 'latest.json.before'))
    replace(COORDINATOR, read(root / 'coordinator.py.before'))
    replace(CONFIG, read(root / 'startup.json.before'))
    require(read(COORDINATOR) == observed['coordinator_raw'] and read(CONFIG) == observed['config_raw'])
    attempt = 1
    while (root / f'rollback-{attempt}.json').exists():
        attempt += 1
    os.replace(root / 'apply-intent.json', root / f'apply-intent-rolled-back-{attempt}.json')
    sync(root)
    write(root / f'rollback-{attempt}.json', canonical(dict(
        schema='sage.lantern.release-rollback.v1', boot_id=observed['boot'], attempt=attempt,
        restored={'config': sha(observed['config_raw']),
                  'coordinator': sha(observed['coordinator_raw'])},
        pointer_advanced=False, producers_started=False, historical_outcomes='unknown')))
    print('Partial apply rolled back to the preserved originals.', flush=True)


def accept(release, manifest, root, observed):
    """Post-boot acceptance: prove the installed release completed this boot."""
    require((root / 'complete.json').exists())
    require(read(CONFIG) == read(root / 'startup.json')
            and sha(read(COORDINATOR)) == sha(read(root / 'startup-coordinator.py')))
    record = decode(read(root / 'complete.json'))
    boot = boot_id()
    journal = JOURNALS / (boot + '.jsonl')
    require(journal.exists(), 'this boot has no lifecycle journal')
    rows = [decode(line) for line in read(journal).splitlines()]
    operational = [row for row in rows if row['phase'] == 'operational']
    require(operational, 'the lifecycle did not reach operational')
    pointer, pointer_raw = pin(JOURNALS / 'latest.json')
    document = decode(pointer_raw)
    lifetime_path = Path(document['path'])
    lifetime_raw = read(lifetime_path)
    require(lifetime_path == ATTEMPTS / boot / 'new-lifetime.json'
            and sha(lifetime_raw) == document['sha256'],
            'the durable pointer does not name this boot')
    lifetime = decode(lifetime_raw)
    require(lifetime['boot_id'] == boot and lifetime['phase'] == 'complete')
    failures = subprocess.run(['systemctl', '--failed', '--no-legend', '--plain'],
                              capture_output=True, text=True).stdout.strip()
    require(failures == '', f'failed units: {failures[:200]}')
    for service in SERVICES:
        state = subprocess.check_output(['systemctl', 'is-active', service], text=True).strip()
        require(state == 'active', f'{service}: {state}')
    acceptance = decode(read(root / 'acceptance.json')) if (root / 'acceptance.json').exists() else \
        dict(schema='sage.lantern.release-acceptance.v1', install_boot=record['boot_id'], boots=[])
    require(all(item['boot_id'] != boot for item in acceptance['boots']))
    acceptance['boots'].append(dict(
        boot_id=boot, accepted_at=int(time.time()), journal=sha(read(journal)),
        pointer=pointer, lifetime=document, phase=operational[-1]['details'],
        services=list(SERVICES)))
    acceptance['another_reboot_tested'] = len(acceptance['boots']) >= 2
    acceptance['boots_since_install'] = len(acceptance['boots'])
    write_or_verify_or_replace(root / 'acceptance.json', canonical(acceptance))
    print(json.dumps(dict(boot_id=boot, accepted=len(acceptance['boots']),
                          another_reboot_tested=acceptance['another_reboot_tested'],
                          pointer=pointer), indent=1, sort_keys=True), flush=True)


def write_or_verify_or_replace(path, raw):
    path = Path(path)
    if path.exists():
        previous = decode(path.read_bytes())
        require(previous.get('schema') == 'sage.lantern.release-acceptance.v1')
        replace(path, raw)
        return
    write(path, raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('phase', choices=['verify', 'stage', 'preflight', 'apply', 'rollback', 'accept'])
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--mode', choices=['next-boot', 'recover-spent'])
    args = parser.parse_args()
    manifest, root = verify_release(args.release)
    if args.phase == 'verify':
        print(json.dumps(dict(root=str(root), unit=manifest['unit'], boot_id=manifest['boot_id'],
                              files=len(manifest['files']), allowance='state/allowance.json' in manifest['files'],
                              authorized_by=manifest['authorized_by']), indent=1, sort_keys=True))
        return
    if args.phase == 'rollback':
        rollback(args.release, manifest, root, observe())
        return
    if args.phase == 'accept':
        accept(args.release, manifest, root, observe())
        return
    if args.phase == 'stage':
        require(os.geteuid() == 0 and sys.prefix == '/opt/sage-lantern/venv')
        require_install_boot(manifest)
        stage(args.release, manifest, root, observe())
        return
    require(args.mode is not None)
    require(os.geteuid() == 0 and sys.prefix == '/opt/sage-lantern/venv')
    require_install_boot(manifest)
    observed = observe()
    if args.phase == 'preflight':
        preflight(args.release, manifest, root, observed, args.mode)
    else:
        apply(args.release, manifest, root, observed, args.mode)


if __name__ == '__main__':
    main()
