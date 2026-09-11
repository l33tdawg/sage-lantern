#!/usr/bin/env python3
"""Stage public B initialization inputs only; never generate keys or install.

Quotas are explicit arguments, not inferred from A or invented as defaults.
The target operator must review policy and install the initial YAML privately
before the canonical identity-config phase can initialize independent B keys.
"""

import argparse
import hashlib
import json
from pathlib import Path
import tomllib


QUOTAS = ('actor_bytes', 'node_bytes', 'actor_objects', 'node_objects', 'min_free_bytes')
YAML = b'''data_dir: /var/lib/sage-lantern/sage-personal/data
agent_key_file: /var/lib/sage-lantern/sage-personal/agent.key
rest_addr: 127.0.0.1:8080
embedding:
  provider: hash
  dimension: 768
federation:
  enabled: false
  p2p_enabled: false
  p2p_relay_addrs: []
  p2p_force_private: false
quorum:
  enabled: false
voter:
  enabled: true
  required: true
vendored_agent_bootstrap:
  agent_key_file: /var/lib/sage-lantern/identity/lantern-agent.key
  home_domain: local-field-knowledge
  clearance: 1
'''


def require(value, reason):
    if value is not True:
        raise ValueError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare(runtime, runtime_hash, target_directory, quotas):
    require(digest(runtime) == runtime_hash, 'runtime_hash_mismatch')
    sage = tomllib.loads(runtime.decode())['sage']
    require(sage == {'base_url': 'http://127.0.0.1:8080',
        'identity_path': '/var/lib/sage-lantern/identity/lantern-agent.key', 'domain': 'local-field-knowledge'}, 'fresh_private_template_required')
    require(type(quotas) is dict and set(quotas) == set(QUOTAS)
        and all(type(value) is int and 0 < value <= 2**53 - 1 for value in quotas.values()), 'explicit_bounded_quotas_required')
    require(quotas['node_bytes'] >= quotas['actor_bytes'] and quotas['node_objects'] >= quotas['actor_objects'], 'reversed_quotas')
    target = Path(target_directory)
    require(target.is_absolute() and '..' not in target.parts and str(target).startswith('/opt/'), 'root_staging_path_required')
    plan = dict(schema='sage.lantern.commission-plan.v1', unit_id='unit-b',
        runtime_template=str(target / 'runtime-template.toml'), runtime_sha256=runtime_hash, quotas=quotas)
    return {'initial-sage.yaml': YAML, 'runtime-template.toml': runtime,
        'commission-plan.json': (json.dumps(plan, sort_keys=True, indent=2) + '\n').encode()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-template', type=Path, required=True)
    parser.add_argument('--runtime-sha256', required=True)
    parser.add_argument('--target-directory', required=True)
    parser.add_argument('--output', type=Path, required=True)
    for name in QUOTAS:
        parser.add_argument('--' + name.replace('_', '-'), type=int, required=True)
    args = parser.parse_args()
    files = prepare(args.runtime_template.read_bytes(), args.runtime_sha256, args.target_directory,
        {name: getattr(args, name) for name in QUOTAS})
    args.output.mkdir(mode=0o700)
    for name, raw in files.items():
        with (args.output / name).open('xb') as stream:
            stream.write(raw)
        (args.output / name).chmod(0o600)
    print(json.dumps(dict(unit_id='unit-b', output=str(args.output), files={name: digest(raw) for name, raw in files.items()},
        identity_generated=False, installed=False, policy_review_required=True), sort_keys=True))


if __name__ == '__main__':
    main()
