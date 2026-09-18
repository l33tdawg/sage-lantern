#!/usr/bin/env python3
"""Read-only release snapshot: the live state a Lantern release has to describe.

Runs on a unit as root and prints one canonical JSON document. Nothing is
written, no service is started or stopped, and no historical record is touched:
the tool only reads the pinned documents, the live supervisor fence, the
commissioning receipt, the model files and the durable pointer, and it asks the
given helper and collector to describe the deployed runtime.

The baseline it mints is the six static fields the boot lifecycle compares
against (``inventory``, ``runtime_sources``, ``files``, ``commission_sha256``,
``actor``, ``model_argv``). It is minted with the same reviewed helpers the
lifecycle uses, but deliberately without the lifecycle's "producers are
inactive" preconditions: a release is built and installed on a unit that is
still serving, and nothing here needs a quiet producer tree to be true. The
producers-inactive rule stays where it belongs, in the adapter at boot.

Usage:
    release-snapshot.py --helper PATH --helper-sha256 SHA \
        --collector PATH --collector-sha256 SHA \
        --wheel PATH --wheel-sha256 SHA [--retained-baseline PATH]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import types


SCHEMA = 'sage.lantern.release-snapshot.v1'
CONFIG = Path('/etc/sage-lantern/startup.json')
ATTEMPTS = Path('/var/lib/sage-lantern-cross-boot-audio')
JOURNALS = Path('/var/lib/sage-lantern-startup')
FENCE = Path('/var/lib/sage-lantern-mesh-supervisor/fence.json')


def require(value):
    if not value:
        raise ValueError('release_snapshot_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def unique(items):
    result = {}
    for name, value in items:
        require(name not in result)
        result[name] = value
    return result


def trusted(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    raw = Path(path).read_bytes()
    require(0 < len(raw) <= 2 * 1024 * 1024)
    return raw


def load(path, digest):
    raw = trusted(path)
    require(sha(raw) == digest)
    module = types.ModuleType('release_snapshot_dependency')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def pin(path, digest):
    require(re.fullmatch('[0-9a-f]{64}', digest) is not None)
    return {'path': str(path), 'sha256': digest}


def pin_of(path):
    raw = trusted(path)
    return {'path': str(path), 'sha256': sha(raw)}, raw


def mint(helper, collector, wheel, site, retained_model_files):
    """The six static baseline fields, from live state, without starting anything."""
    from sage_lantern import audio_readiness
    from sage_lantern.deployment_bringup import ColdBrokerBootstrap
    from sage_lantern.mesh_daemon import load_config
    from sage_lantern.suspend_system import SuspendSystem
    sources = collector.runtime_sources(helper, wheel, site)
    system = SuspendSystem()
    inventory = collector.bootstrap_inventory(helper, system)
    ColdBrokerBootstrap(inventory).inputs()
    argv = collector.model_argv(system, helper)
    model = system._show(helper.MODEL, system.clock() + 5,
                         ('DropInPaths', 'FragmentPath', 'NeedDaemonReload'))
    require(model['FragmentPath'] == str(helper.SYSTEMD / helper.MODEL)
            and model['NeedDaemonReload'] == 'no')
    files = collector.model_files(helper, argv, model, retained_model_files)
    config = load_config()
    baseline = dict(inventory=inventory, runtime_sources=sources, files=files,
                    commission_sha256=helper.sha(helper.canonical(audio_readiness.commissioned_receipt())),
                    actor=dict(chain_id=config['chain_id'], agent_id=config['agent_id']),
                    model_argv=argv)
    # The wheel and the deployed tree must still describe each other after the
    # model scan, so a runtime edit during the snapshot cannot be pinned.
    require(collector.runtime_sources(helper, wheel, site) == sources)
    return baseline


def live_state():
    config_pin, config_raw = pin_of(CONFIG)
    config = json.loads(config_raw, object_pairs_hook=unique)
    policy_pin, policy_raw = pin_of(config['policy']['path'])
    require(policy_pin['sha256'] == config['policy']['sha256'])
    policy = json.loads(policy_raw, object_pairs_hook=unique)
    coordinator_pin, coordinator_raw = pin_of(config['coordinator']['path'])
    require(coordinator_pin['sha256'] == config['coordinator']['sha256'])
    pointer = None
    if (JOURNALS / 'latest.json').exists():
        # `latest.json` is itself a pin onto the prior lifetime document.
        pointer_pin, pointer_raw = pin_of(JOURNALS / 'latest.json')
        document = json.loads(pointer_raw, object_pairs_hook=unique)
        prior_pin = {'path': document['path'], 'sha256': document['sha256']}
        prior_raw = trusted(prior_pin['path'])
        require(sha(prior_raw) == prior_pin['sha256'])
        pointer = {'pin': pointer_pin, 'document': prior_pin, 'prior_raw': prior_raw}
    fence_pin, fence_raw = pin_of(FENCE)
    return dict(config=config, config_pin=config_pin, policy=policy, policy_pin=policy_pin,
                coordinator_pin=coordinator_pin, pointer=pointer, fence_pin=fence_pin,
                fence_raw=fence_raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('helper', 'collector', 'wheel'):
        parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--' + name + '-sha256', required=True)
    parser.add_argument('--retained-baseline', type=Path)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux'
            and Path(sys.prefix) == Path('/opt/sage-lantern/venv'))
    helper = load(args.helper, args.helper_sha256)
    collector = load(args.collector, args.collector_sha256)
    wheel_raw = trusted(args.wheel)
    require(sha(wheel_raw) == args.wheel_sha256)
    site = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    state = live_state()
    retained_path = args.retained_baseline or Path(state['policy']['baseline']['path'])
    retained = json.loads(trusted(retained_path), object_pairs_hook=unique)
    baseline = mint(helper, collector, args.wheel, site, retained['files'])
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    attempt = dict(state_dir=str(ATTEMPTS / boot), state_dir_exists=os.path.lexists(ATTEMPTS / boot),
                   journal_exists=os.path.lexists(JOURNALS / (boot + '.jsonl')),
                   boot_journal=str(JOURNALS / (boot + '.jsonl')))
    document = dict(schema=SCHEMA, boot_id=boot, helper=pin(args.helper, args.helper_sha256),
                    collector=pin(args.collector, args.collector_sha256),
                    wheel=pin(args.wheel, args.wheel_sha256), baseline=baseline,
                    config=state['config_pin'], policy=state['policy_pin'],
                    coordinator=state['coordinator_pin'],
                    fence=dict(state['fence_pin'],
                               document=json.loads(state['fence_raw'], object_pairs_hook=unique)),
                    attempt=attempt, historical_outcomes='unknown')
    if state['pointer'] is not None:
        document['pointer'] = state['pointer']['pin']
        document['prior_pin'] = state['pointer']['document']
        document['prior'] = json.loads(state['pointer']['prior_raw'], object_pairs_hook=unique)
        document['initial_lifetime'] = state['config']['initial_lifetime']
    print(canonical(document).decode())


if __name__ == '__main__':
    main()
