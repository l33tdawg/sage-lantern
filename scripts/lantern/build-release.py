#!/usr/bin/env python3
"""Build one Lantern release from a unit's deployed bytes.

A release is a transaction over code *and* the pins that describe it, so this
tool never copies from the repository working tree: the runtime wheel is built
from the package directory that was fetched off the unit, and the artifacts that
carry an allowlist (collector, coordinator) or a digest constant (adapter) are
regenerated with that wheel's digest before their own digests are taken. The
result is a self-describing directory:

    manifest.json                 digests, target boot, install paths
    runtime/<wheel>               built from the deployed bytes
    tools/startup-audio-adapter.py
    tools/startup-coordinator.py
    tools/collect-cross-boot-audio-pins.py
    tools/recover-cross-boot-audio.py
    tools/release-snapshot.py
    state/baseline.json           the live state the release describes
    state/policy.json             pins helper, collector, wheel, baseline
    state/startup.json            pins adapter, policy, coordinator (+allowance)
    state/allowance.json          only when the prior binding needs one

The build refuses if the assembled documents do not satisfy the artifacts: the
generated coordinator must accept the wheel and the config, and the generated
adapter must accept the prior/fence/inventory binding exactly as the boot will.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import types
import uuid
import zipfile


SCHEMA = 'sage.lantern.release.v1'
BASELINE_FIELDS = ('inventory', 'runtime_sources', 'files', 'commission_sha256', 'actor', 'model_argv')
COORDINATOR_PATH = '/opt/sage-lantern/libexec/startup-coordinator.py'
CONFIG_PATH = '/etc/sage-lantern/startup.json'
ROOT_PREFIX = '/var/lib/sage-lantern-release-'
ALLOWLIST_ANCHOR = 'RELEASE_RUNTIME = None'
COLLECTOR_ANCHOR = 'COLLECTOR_SHA = '
WHEEL_NAME = 'sage_lantern_runtime-0.2.0-py3-none-any.whl'
# A release has to be reproducible: every entry carries the same fixed timestamp
# so building the same deployed bytes twice yields the same wheel digest.
WHEEL_TIME = (2026, 1, 1, 0, 0, 0)


def require(value):
    if not value:
        raise ValueError('release_build_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def read(path):
    return Path(path).read_bytes()


def write(path, raw):
    path = Path(path)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def recorded(raw):
    digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()
    return f'sha256={digest}', str(len(raw))


def installed_hashes(path):
    table = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            digest, name = line.split()
            table[name.split('/', 1)[1] if name.startswith('sage_lantern/') else name] = digest
    return table


def build_wheel(old_whl, package, out, expected=None):
    """A wheel whose sage_lantern/*.py are the deployed bytes, not the working tree."""
    modules = {path.name: read(path) for path in sorted(Path(package).iterdir())
               if path.is_file() and path.name.endswith('.py')}
    require(modules)
    if expected is not None:
        require(set(modules) == set(expected))
        for name, raw in modules.items():
            require(sha(raw) == expected[name])
    with zipfile.ZipFile(old_whl) as archive:
        old = [(info, archive.read(info.filename)) for info in archive.infolist()]
    replacements = {f'sage_lantern/{name}': raw for name, raw in modules.items()}

    def entry(name, raw, source=None):
        info = zipfile.ZipInfo(name, date_time=WHEEL_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16 if source is None else source.external_attr
        return info, raw

    written = {}
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for info, raw in old:
            if info.filename.startswith('sage_lantern/') and info.filename.endswith('.py'):
                continue
            if info.filename.endswith('dist-info/RECORD'):
                continue
            archive.writestr(*entry(info.filename, raw, info))
            written[info.filename] = raw
        for name, raw in sorted(replacements.items()):
            archive.writestr(*entry(name, raw))
            written[name] = raw
        record = 'sage_lantern_runtime-0.2.0.dist-info/RECORD'
        rows = [(name, *recorded(raw)) for name, raw in written.items()]
        rows.append((record, '', ''))
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator='\n').writerows(rows)
        archive.writestr(*entry(record, buffer.getvalue().encode()))
    return sha(read(out))


def inject(text, anchor, replacement, where):
    """Replace one exact anchor line; refuse if it is absent or ambiguous."""
    lines = text.splitlines(keepends=True)
    hits = [index for index, line in enumerate(lines) if line.startswith(anchor)]
    require(len(hits) == 1)
    lines[hits[0]] = replacement + '\n'
    return ''.join(lines)


def generate_tools(tools, wheel_digest, out):
    collector = inject(read(tools / 'collect-cross-boot-audio-pins.py').decode(), ALLOWLIST_ANCHOR,
                       f"RELEASE_RUNTIME = {wheel_digest!r}", 'collector')
    coordinator = inject(read(tools / 'startup-coordinator.py').decode(), ALLOWLIST_ANCHOR,
                         f"RELEASE_RUNTIME = {wheel_digest!r}", 'coordinator')
    collector_digest = sha(collector.encode())
    adapter = read(tools / 'startup-audio-adapter.py').decode()
    matches = [line for line in adapter.splitlines() if line.startswith(COLLECTOR_ANCHOR)]
    require(len(matches) == 1
            and re.fullmatch(r"COLLECTOR_SHA = '[0-9a-f]{64}'", matches[0]) is not None)
    adapter = adapter.replace(matches[0], f"COLLECTOR_SHA = '{collector_digest}'", 1)
    files = {'startup-audio-adapter.py': adapter.encode(),
             'startup-coordinator.py': coordinator.encode(),
             'collect-cross-boot-audio-pins.py': collector.encode(),
             'recover-cross-boot-audio.py': read(tools / 'recover-cross-boot-audio.py'),
             'release-snapshot.py': read(tools / 'release-snapshot.py')}
    for name, raw in files.items():
        write(out / name, raw)
    return files


def load_module(path, name):
    raw = read(path)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def local_pins(out):
    """Resolve a release's root-owned pins against the release tree itself, so the
    generated coordinator can verify the wheel before the unit has it."""
    table = {WHEEL_NAME: out / 'runtime' / WHEEL_NAME, 'baseline.json': out / 'state' / 'baseline.json',
             'policy.json': out / 'state' / 'policy.json'}
    for name in ('startup-audio-adapter.py', 'startup-coordinator.py',
                 'collect-cross-boot-audio-pins.py', 'recover-cross-boot-audio.py'):
        table[name] = out / 'tools' / name
    def resolve(pin):
        require(set(pin) == {'path', 'sha256'})
        local = table.get(Path(pin['path']).name)
        require(local is not None and local.exists())
        raw = read(local)
        require(sha(raw) == pin['sha256'])
        return raw
    return resolve


def inventory_delta(prior, live):
    delta = {}
    for name in set(prior) | set(live):
        before, after = prior.get(name), live.get(name)
        if before == after:
            continue
        require(before is not None and after is not None)
        require(re.fullmatch('[0-9a-f]{64}', before) is not None
                and re.fullmatch('[0-9a-f]{64}', after) is not None)
        delta[name] = {'from': before, 'to': after}
    return delta


def consumer_boot(current, prior_boot):
    """The boot that will spend the tolerance.

    Normally that is the boot being installed on. When the durable pointer
    already names the current boot (its lifecycle is complete), the release is
    spent by the *next* boot, which does not exist yet: a synthetic id is used so
    the binding is still checked for a boot that is not the prior's own.
    """
    require(str(uuid.UUID(current)) == current and str(uuid.UUID(prior_boot)) == prior_boot)
    if prior_boot != current:
        return current
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'sage-lantern-next-boot:' + current))


def build(args):
    # The generated coordinator imports the runtime package; describe it from the
    # source tree so the builder only needs this repository.
    if str(args.runtime_src) not in sys.path:
        sys.path.insert(0, str(args.runtime_src))
    snapshot = json.loads(read(args.snapshot))
    require(snapshot.get('schema') == 'sage.lantern.release-snapshot.v1'
            and {'boot_id', 'baseline', 'fence', 'pointer', 'prior_pin', 'prior', 'initial_lifetime'}
            <= set(snapshot))
    boot = snapshot['boot_id']
    require(str(uuid.UUID(boot)) == boot)
    root = f'{ROOT_PREFIX}{args.unit}-{args.stamp}'
    require(re.fullmatch(r'/var/lib/sage-lantern-release-[a-z0-9-]+-[0-9]{8}-[0-9]{6}',
                         root) is not None)
    out = Path(args.out)
    require(not out.exists())
    for directory in ('runtime', 'tools', 'state'):
        (out / directory).mkdir(parents=True, mode=0o755)
    expected = installed_hashes(args.installed_hashes) if args.installed_hashes else None
    wheel_digest = build_wheel(args.template_wheel, args.deployed, out / 'runtime' / WHEEL_NAME, expected)
    tools = generate_tools(Path(args.tools), wheel_digest, out / 'tools')
    digests = {name: sha(raw) for name, raw in tools.items()}

    baseline = {name: snapshot['baseline'][name] for name in BASELINE_FIELDS}
    require(set(baseline) == set(BASELINE_FIELDS))
    write(out / 'state' / 'baseline.json', canonical(baseline))
    policy = {
        'schema': 'sage.lantern.startup-audio-policy.v1',
        'helper': {'path': f'{root}/recover-cross-boot-audio.py',
                   'sha256': digests['recover-cross-boot-audio.py']},
        'collector': {'path': f'{root}/collect-cross-boot-audio-pins.py',
                      'sha256': digests['collect-cross-boot-audio-pins.py']},
        'runtime_wheel': {'path': f'{root}/{WHEEL_NAME}', 'sha256': wheel_digest},
        'baseline': {'path': f'{root}/baseline.json',
                     'sha256': sha(read(out / 'state' / 'baseline.json'))},
    }
    write(out / 'state' / 'policy.json', canonical(policy))

    prior, fence = snapshot['prior'], snapshot['fence']
    delta = inventory_delta(prior['inventory'], baseline['inventory'])
    strict = prior['evidence']['broker_id'] == fence['document']['broker_id'] and not delta
    allowance = None
    if not strict:
        require(args.allowance_authorized_by)
        allowance = dict(schema='sage.lantern.startup-allowance.v1',
                         boot_id=prior['boot_id'], prior=snapshot['prior_pin'],
                         fence=dict(sha256=fence['sha256'], phase=fence['document']['phase'],
                                    broker_id=fence['document']['broker_id']),
                         inventory_delta=delta, historical_outcomes='unknown',
                         authorized_by=args.allowance_authorized_by)
        write(out / 'state' / 'allowance.json', canonical(allowance))
    config = dict(schema='sage.lantern.startup-coordinator.v1',
                  adapter={'path': f'{root}/startup-audio-adapter.py',
                           'sha256': digests['startup-audio-adapter.py']},
                  policy={'path': f'{root}/policy.json',
                          'sha256': sha(read(out / 'state' / 'policy.json'))},
                  initial_lifetime=snapshot['initial_lifetime'],
                  coordinator={'path': COORDINATOR_PATH,
                               'sha256': digests['startup-coordinator.py']})
    if allowance is not None:
        config['allowance'] = {'path': f'{root}/allowance.json',
                               'sha256': sha(read(out / 'state' / 'allowance.json'))}
    write(out / 'state' / 'startup.json', canonical(config))

    # The assembled documents must satisfy the generated artifacts before a
    # release exists at all: this is the binding the boot will re-check.
    coordinator = load_module(out / 'tools' / 'startup-coordinator.py', 'built_coordinator')
    coordinator.validate_full_config(config)
    coordinator.pinned = local_pins(out)
    coordinator.verify_wheel(policy['runtime_wheel'])
    adapter = load_module(out / 'tools' / 'startup-audio-adapter.py', 'built_adapter')
    adapter.validate_prior(prior, fence['document'], baseline,
                           consumer_boot(boot, prior['boot_id']), allowance,
                           fence_sha256=fence['sha256'], prior_pin=snapshot['prior_pin'])
    require(adapter.HELPER_SHA == policy['helper']['sha256'])
    require(adapter.COLLECTOR_SHA == policy['collector']['sha256'])

    files = {}
    for base, _, names in os.walk(out):
        for name in names:
            path = Path(base) / name
            relative = str(path.relative_to(out))
            if relative != 'manifest.json':
                files[relative] = sha(read(path))
    manifest = dict(schema=SCHEMA, unit=args.unit, boot_id=boot, built_at=args.built_at,
                    authorized_by=args.authorized_by, root=root, coordinator_path=COORDINATOR_PATH,
                    config_path=CONFIG_PATH, prior=snapshot['prior_pin'], fence=fence['sha256'],
                    artifacts={'runtime_wheel': policy['runtime_wheel'],
                               'helper': policy['helper'], 'collector': policy['collector'],
                               'adapter': config['adapter'], 'coordinator': config['coordinator']},
                    files=files)
    write(out / 'manifest.json', canonical(manifest))
    print(json.dumps(dict(wheel=wheel_digest, coordinator=digests['startup-coordinator.py'],
                          collector=digests['collect-cross-boot-audio-pins.py'],
                          adapter=digests['startup-audio-adapter.py'],
                          helper=digests['recover-cross-boot-audio.py'],
                          baseline=policy['baseline']['sha256'], root=root,
                          allowance=allowance is not None), indent=1, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--deployed', type=Path, required=True)
    parser.add_argument('--template-wheel', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--stamp', required=True)
    parser.add_argument('--built-at', required=True)
    parser.add_argument('--authorized-by', required=True)
    parser.add_argument('--allowance-authorized-by')
    parser.add_argument('--installed-hashes', type=Path)
    parser.add_argument('--tools', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--runtime-src', type=Path,
                        default=Path(__file__).resolve().parents[2] / 'software/runtime/src')
    build(parser.parse_args())


if __name__ == '__main__':
    main()
