#!/usr/bin/env python3
"""Prepare private fresh mesh config from reviewed commissioning, never legacy attestations.

Native execution reads the existing Companion identity locally for signed GET
storage observations; no key bytes leave the process or enter output artifacts.
USB inspection is metadata-only, not radio handshake or capture acceptance.
The output is a new staging directory, never installed configuration or grants.
Public mesh/gateway commissioning remains a separate reviewed operation.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import sys
import stat
import subprocess
import uuid
from types import ModuleType

import yaml


def helper(name, filename):
    path = Path(__file__).with_name(filename)
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


LEGACY = helper("fresh_mesh_io", "prepare-mesh-runtime.py")
COMMISSION = helper("fresh_mesh_commission", "provision-fresh-appliance.py")
require = LEGACY.require
document = LEGACY.document
encoded = LEGACY.encoded
CONFIG = Path("/etc/sage-lantern")
PRIVATE_KEY = "/var/lib/sage-lantern/identity/lantern-agent.key"
SAGE_YAML = Path('/var/lib/sage-lantern/sage-personal/config.yaml')
GENESIS = SAGE_YAML.parent / 'data/cometbft/config/genesis.json'
SAGE_BINARY = Path('/opt/sage-lantern/bin/sage-gui')
PROVISIONING_PROFILE = Path('/etc/sage/provisioning-profile.yaml')
POLICY_FIELDS = {'chain_id', 'data_dir', 'agent_key_file', 'rest_addr', 'encryption',
    'private_media', 'embedding', 'federation', 'quorum', 'voter', 'vendored_agent_bootstrap'}


def service_document(path, uid, *, private=False):
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, uid) and not info.st_mode & 0o022)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == uid
        and stat.S_IMODE(info.st_mode) in ((0o600,) if private else (0o600, 0o640, 0o644)))
    return COMMISSION.identity_config_bytes(path)


def current_yaml(uid):
    return service_document(SAGE_YAML, uid, private=True)


def native_config_check(expected):
    from sage_lantern.suspend_kiosk import _trusted_file
    require(type(expected) is str and re.fullmatch(r'[0-9a-f]{64}', expected))
    def binary_digest():
        _trusted_file(SAGE_BINARY)
        with SAGE_BINARY.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    require(binary_digest() == expected)
    _trusted_file(Path('/usr/bin/setpriv'))
    _trusted_file(PROVISIONING_PROFILE)
    environment = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C',
        HOME='/var/lib/sage-lantern', SAGE_HOME=str(SAGE_YAML.parent), REST_ADDR='127.0.0.1:8080',
        SAGE_CMT_RPC_ADDR='tcp://127.0.0.1:26657', SAGE_CMT_P2P_ADDR='tcp://127.0.0.1:26656',
        SAGE_TLS_ADDR='127.0.0.1:8443')
    result = subprocess.run(['/usr/bin/setpriv', '--reuid', 'sage-lantern', '--regid', 'sage-lantern',
        '--init-groups', '--inh-caps', '-all,+dac_read_search', '--ambient-caps', '-all,+dac_read_search',
        '--bounding-set', '-all,+dac_read_search', str(SAGE_BINARY), 'check-private-config'],
        env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    require(result.returncode == 0 and binary_digest() == expected)


def current_policy(raw, policy, config):
    require(type(policy) is dict and set(policy) == POLICY_FIELDS)
    require(not any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(raw)))
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node):
        pairs = []
        for key, value in node.value:
            require(key.tag == 'tag:yaml.org,2002:str')
            pairs.append((loader.construct_object(key), loader.construct_object(value)))
        return LEGACY.pairs(pairs)
    UniqueLoader.add_constructor('tag:yaml.org,2002:map', mapping)
    value = yaml.load(raw, Loader=UniqueLoader)
    require(type(value) is dict and POLICY_FIELDS <= set(value))
    require(encoded({name: value[name] for name in POLICY_FIELDS}) == encoded(policy))
    require(policy['chain_id'] == config['chain_id']
        and policy['data_dir'] == '/var/lib/sage-lantern/sage-personal/data'
        and policy['agent_key_file'] == '/var/lib/sage-lantern/sage-personal/agent.key'
        and policy['rest_addr'] == '127.0.0.1:8080'
        and policy['encryption'] == {'enabled': True} and type(policy['encryption']['enabled']) is bool)
    for name in ('federation', 'quorum'):
        require(type(policy[name]) is dict and policy[name].get('enabled') is False)
    require(policy['federation'].get('p2p_enabled', False) is False)
    require(policy['quorum'].get('peers', []) == []
        and policy['quorum'].get('p2p_addr', 'tcp://127.0.0.1:26656') == 'tcp://127.0.0.1:26656'
        and policy['quorum'].get('tls_addr', '127.0.0.1:8443') == '127.0.0.1:8443')
    bootstrap = policy['vendored_agent_bootstrap']
    require(type(bootstrap) is dict and set(bootstrap) == {'agent_key_file', 'home_domain', 'clearance'}
        and bootstrap['agent_key_file'] == PRIVATE_KEY and type(bootstrap['home_domain']) is str
        and bool(bootstrap['home_domain']) and type(bootstrap['clearance']) is int and 0 <= bootstrap['clearance'] <= 255)
    voter = policy['voter']
    require(type(voter) is dict and set(voter) == {'enabled', 'required', 'poll_interval'}
        and voter['enabled'] is True and voter['required'] is True and type(voter['poll_interval']) is str)
    interval = re.fullmatch(r'([1-9][0-9]{0,5})(ms|s|m)', voter['poll_interval'])
    require(interval is not None)
    milliseconds = int(interval[1]) * {'ms': 1, 's': 1000, 'm': 60000}[interval[2]]
    require(100 <= milliseconds <= 60000)
    embedding = policy['embedding']
    require(type(embedding) is dict and set(embedding) <= {'provider', 'dimension', 'model', 'base_url', 'api_key'}
        and embedding.get('provider') == 'hash' and type(embedding.get('dimension')) is int
        and embedding['dimension'] == 768 and all(embedding.get(name) in (None, '') for name in ('model', 'base_url', 'api_key')))
    media = policy['private_media']
    quotas = {'actor_bytes', 'node_bytes', 'actor_objects', 'node_objects', 'min_free_bytes'}
    require(type(media) is dict and set(media) == quotas | {'enabled'} and media['enabled'] is True
        and all(type(media[name]) is int and 0 < media[name] <= 2**53 - 1 for name in quotas)
        and media['node_bytes'] >= media['actor_bytes'] and media['node_objects'] >= media['actor_objects'])


def commission_provenance(receipt, unit, raw):
    require(not os.path.lexists('/' + COMMISSION.PENDING))
    require(type(receipt) is dict and set(receipt) == {'schema', 'transaction_id', 'unit_id',
        'manifest_sha256', 'plan_sha256', 'initial_config_sha256', 'output_sha256'}
        and receipt['schema'] == 'sage.lantern.commission-complete.v1' and receipt['unit_id'] == unit)
    require(type(receipt['transaction_id']) is str and str(uuid.UUID(receipt['transaction_id'])) == receipt['transaction_id'])
    require(all(type(receipt[name]) is str and re.fullmatch(r'[0-9a-f]{64}', receipt[name])
        for name in ('manifest_sha256', 'plan_sha256', 'initial_config_sha256')))
    outputs = receipt['output_sha256']
    require(type(outputs) is dict and set(outputs) == set(COMMISSION.COMMISSION_FILES)
        and all(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) for value in outputs.values()))
    for filename, name in (('lantern.toml', 'runtime'), ('bindings.json', 'commissioned'), ('initialized-public.json', 'initialized')):
        require(sha(raw[name]) == outputs[filename])
    require(not os.path.lexists('/' + COMMISSION.PENDING))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def validate(config, initialized, commissioned, app, bindings, radio, *, radio_fingerprint, service_uid):
    from sage_lantern.mesh_daemon import validate_config
    from sage_lantern.mesh_serial_owner import RadioBinding
    validate_config(config)
    require("public_mesh" not in config and "public_gateway" not in config)
    require(config["unit_id"] in {"unit-a", "unit-b", "unit-c", "unit-d", "unit-e", "unit-f"} and config["voice_uid"] == service_uid
        and type(service_uid) is int and service_uid > 0)
    unit = config["unit_id"]
    fields = {"schema", "unit_id", "chain_id", "root_id", "private_agent_id", "public_agent_id",
        "local_enrollment_committed", "public_enrolled", "vault_initialized", "ready", "provisioned"}
    require(type(initialized) is dict and set(initialized) == fields
        and initialized["schema"] == "sage.lantern.initialized.v1" and initialized["unit_id"] == unit)
    require(all(initialized[name] is False for name in
        ("local_enrollment_committed", "public_enrolled", "vault_initialized", "ready", "provisioned")))
    identities = [initialized[name] for name in ("root_id", "private_agent_id", "public_agent_id")]
    require(all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
        and value != "00" * 32 for value in identities) and len(set(identities)) == 3)
    require(type(initialized["chain_id"]) is str
        and re.fullmatch(r"sage-personal-[A-Za-z0-9._-]{1,110}", initialized["chain_id"]))
    expected = dict(schema="sage.lantern.commissioned-bindings.v1", unit_id=unit,
        private_chain_id=initialized["chain_id"], private_agent_id=initialized["private_agent_id"],
        private_identity_path=PRIVATE_KEY, public_agent_id=initialized["public_agent_id"],
        public_identity_path="/var/lib/sage-lantern/identity/lantern-public.key",
        public_enrolled=False, ready=False, provisioned=False)
    require(type(commissioned) is dict and encoded(commissioned) == encoded(expected))
    require(config["chain_id"] == initialized["chain_id"] and config["agent_id"] == initialized["private_agent_id"]
        and config["identity_file"] == PRIVATE_KEY and config["endpoint"] == "http://127.0.0.1:8080")
    require(type(app) is dict and app.get("schema") == "sage.lantern.fresh-app-install.v1"
        and app.get("unit_id") == unit and app.get("installed") is True and app.get("lock_required") is True
        and app.get("ready") is False and app.get("provisioned") is False)
    require(type(bindings) is dict and bindings.get("schema") == "sage.lantern.unit-plan.v1"
        and bindings.get("unit_id") == unit and bindings.get("service_account") == "sage-lantern")
    binding = RadioBinding.from_inventory(radio, unit_id=unit, expected_fingerprint=radio_fingerprint)
    observed = asdict(binding)
    observed.pop("unit_id")
    observed["public_key"] = binding.public_key.hex()
    require(observed == config["radio"] and binding.public_key != bytes(32))
    plan_radio = bindings["radio"]
    require(all(plan_radio.get(key) == value for key, value in dict(unit_id=unit,
        usb_serial=binding.serial, vendor_id=binding.vendor_id, product_id=binding.product_id,
        stable_path=binding.stable_path, public_key=binding.public_key.hex(), public_fingerprint=radio_fingerprint).items()))
    require(not config["receive_while_locked"] or config["messaging_enabled"])
    require(not config["dispatch_while_locked"] or config["dispatch_enabled"])
    for peer in config["peers"]:
        require(peer["agent_id"] != "00" * 32 and peer["radio_public_key"] != "00" * 32
            and (peer["chain_id"], peer["agent_id"]) != (config["chain_id"], config["agent_id"]))
    return binding


def observe(config, binding):
    from sage_sdk import SageClient
    from sage_lantern.mesh_daemon import load_identity
    from sage_lantern.mesh_usb_opener import _inspect
    from sage_lantern.message_contacts import RecipientID
    from sage_lantern.message_storage import MessageStorageProbe
    before = _inspect(binding)
    identity, descriptor = load_identity(config)
    try:
        with SageClient(config["endpoint"], identity, timeout=5, trust_env=False) as client:
            probe = MessageStorageProbe(client, actor=RecipientID(config["chain_id"], config["agent_id"]),
                endpoint=config["endpoint"])
            snapshot = probe.snapshot()
            require(_inspect(binding) == before and probe.snapshot() == snapshot)
        return dict(boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            private_agent_id=identity.agent_id, storage_epoch=snapshot.epoch,
            usb_node=dict(path=str(before.path), device=before.device, inode=before.inode, rdev=before.rdev))
    finally:
        os.close(descriptor)


def inputs(args):
    paths = dict(manifest=args.manifest, radio_inventory=args.radio_inventory,
        commission=CONFIG / "commissioning-complete.json", app=CONFIG / "appliance-installed.json",
        bindings=CONFIG / "appliance-bindings.json", initialized=CONFIG / "initialized-public.json",
        commissioned=CONFIG / "commissioned-bindings.json", unit_metadata=CONFIG / "unit.env",
        runtime=CONFIG / 'lantern.toml', current_sage_policy=args.current_sage_policy)
    raw = {name: LEGACY.read(path, 0) for name, path in paths.items()}
    uid = pwd.getpwnam('sage-lantern').pw_uid
    raw['current_sage'] = current_yaml(uid)
    raw['genesis'] = service_document(GENESIS, uid)
    for name in ("manifest", "radio_inventory", "commission", "app", "bindings", 'current_sage', 'current_sage_policy'):
        expected = getattr(args, name + "_sha256")
        require(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected) and sha(raw[name]) == expected)
    config = document(raw["manifest"])
    receipt = document(raw["commission"])
    commission_provenance(receipt, config['unit_id'], raw)
    current_policy(raw['current_sage'], document(raw['current_sage_policy']), config)
    require(document(raw['genesis']).get('chain_id') == config['chain_id'])
    native_config_check(args.sage_binary_sha256)
    require(current_yaml(uid) == raw['current_sage'] and service_document(GENESIS, uid) == raw['genesis'])
    require(sha(raw["initialized"]) == receipt["output_sha256"]["initialized-public.json"]
        and sha(raw["commissioned"]) == receipt["output_sha256"]["bindings.json"])
    require(raw["unit_metadata"] == ("UNIT_ID=" + config["unit_id"] + "\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n").encode())
    binding = validate(config, *(document(raw[name]) for name in
        ("initialized", "commissioned", "app", "bindings", "radio_inventory")),
        radio_fingerprint=args.radio_fingerprint, service_uid=uid)
    return raw, config, binding


def prepare(args):
    require(os.geteuid() == 0 and sys.platform == "linux")
    from sage_lantern.mesh_daemon import device_policy
    raw, config, binding = inputs(args)
    observation = observe(config, binding)
    require(inputs(args) == (raw, config, binding) and observe(config, binding) == observation)
    artifacts = {"mesh-runtime.json": encoded(config), "20-radio.conf": device_policy(config).encode()}
    require(len(artifacts["mesh-runtime.json"]) <= 16384)
    receipt = dict(schema="sage.lantern.fresh-mesh-preparation.v1", unit_id=config["unit_id"],
        inputs={name: sha(data) for name, data in raw.items()},
        artifacts={name: sha(data) for name, data in artifacts.items()}, observation=observation,
        private_identity_verified=True, storage_verified=True, usb_binding_verified=True,
        sage_binary_sha256=args.sage_binary_sha256, current_config_validated=True,
        commissioning_diff_explained=False,
        radio_handshake_verified=False, capture_verified=False, installed=False, activated=False, ready=False)
    output = args.output_dir
    parent = LEGACY.directory(output.parent, 0)
    target = None
    try:
        os.mkdir(output.name, 0o700, dir_fd=parent)
        os.fsync(parent)
        target = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        os.fchmod(target, 0o700)
        for name, data in artifacts.items():
            LEGACY.write(target, name, data, 0o600 if name.endswith("json") else 0o644)
        require(inputs(args) == (raw, config, binding) and observe(config, binding) == observation)
        LEGACY.write(target, "PREPARED.json", encoded(receipt), 0o600)
    finally:
        if target is not None:
            os.close(target)
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("manifest", "radio-inventory", "output-dir", 'current-sage-policy'):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("manifest", "radio-inventory", "commission", "app", "bindings", 'current-sage', 'current-sage-policy'):
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument('--sage-binary-sha256', required=True)
    parser.add_argument("--radio-fingerprint", required=True)
    try:
        prepare(parser.parse_args())
    except Exception:
        print("Fresh mesh preparation refused; preserve partial output. No activation performed.", file=sys.stderr)
        return 1
    print("Prepared fresh mesh inputs only; no installation, grants, capture acceptance or activation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
