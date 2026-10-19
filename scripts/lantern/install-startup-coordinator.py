"""Install reviewed post-unlock startup wiring; never run it during provisioning.

Requires the matched runtime and recovery adapter already installed. Config is
operator-supplied, not synthesized from whatever happens to be on the device.
Existing destinations are refused. Partial installs are retained for inspection.
The coordinator owns model startup: installation disables the model's independent
boot enablement without stopping it. Unit definitions, commissioning inputs,
keys and fences are unchanged.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import hashlib
import types

from sage_lantern.deployment_bringup import read_public
from sage_lantern.suspend_io import unique_object


SERVICE = Path('/etc/systemd/system/sage-lantern-startup.service')
COORDINATOR = Path('/opt/sage-lantern/libexec/startup-coordinator.py')
startup = None


def load_coordinator(digest):
    raw = read_public(COORDINATOR)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RuntimeError('coordinator_hash_mismatch')
    module = types.ModuleType('reviewed_startup_coordinator')
    module.__file__ = str(COORDINATOR)
    exec(compile(raw, str(COORDINATOR), 'exec'), module.__dict__)
    return module


def preflight(config_raw, service_raw):
    config = json.loads(config_raw, object_pairs_hook=unique_object)
    startup.validate_full_config(config)
    for name in ('adapter', 'policy', 'initial_lifetime', 'coordinator'):
        startup.pinned(config[name])
    policy = startup.decode(startup.pinned(config['policy']))
    startup.require(set(policy) == {'schema', 'helper', 'collector', 'runtime_wheel', 'baseline'}
        and policy['schema'] == 'sage.lantern.startup-audio-policy.v1')
    for name in ('helper', 'collector', 'baseline'):
        startup.pinned(policy[name])
    startup.verify_wheel(policy['runtime_wheel'])
    startup.require(b'ExecStart=/opt/sage-lantern/venv/bin/python -I -B /opt/sage-lantern/libexec/startup-coordinator.py\n' in service_raw)
    startup.require(not os.path.lexists(startup.CONFIG) and not os.path.lexists(SERVICE))
    for target in (startup.CONFIG, SERVICE):
        for parent in target.parents:
            info = parent.lstat()
            startup.require(parent.is_dir() and not parent.is_symlink() and info.st_uid == 0 and not info.st_mode & 0o022)
    return config


def exclusive(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    global startup
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--service', required=True)
    parser.add_argument('--service-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--coordinator-sha256', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('root_required')
    startup = load_coordinator(args.coordinator_sha256)
    startup.require(os.geteuid() == 0)
    config_raw = startup.pinned({'path': args.config, 'sha256': args.config_sha256})
    service_raw = startup.pinned({'path': args.service, 'sha256': args.service_sha256})
    preflight(config_raw, service_raw)
    if args.apply:
        exclusive(startup.CONFIG, config_raw)
        exclusive(SERVICE, service_raw)
        subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True, timeout=30, env=startup.ENV)
        # ColdBrokerBootstrap requires the model to be inactive when the
        # coordinator collects its inputs, then starts it in the proper order.
        subprocess.run(['/usr/bin/systemctl', 'disable', 'sage-lantern-llm.service'],
                       check=True, timeout=30, env=startup.ENV)
        subprocess.run(['/usr/bin/systemctl', 'enable', SERVICE.name], check=True, timeout=30, env=startup.ENV)
    print(json.dumps({'installed': args.apply, 'started': False, 'crossboot_acceptance': False}))


if __name__ == '__main__':
    main()
