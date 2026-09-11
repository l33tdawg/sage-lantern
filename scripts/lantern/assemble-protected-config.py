#!/usr/bin/env python3
"""Assemble disabled protected-storage candidates in an explicit private staging root."""

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[2]
QUOTAS = ("actor_bytes", "node_bytes", "actor_objects", "node_objects", "min_free_bytes")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def read(path):
    path = Path(path).absolute()
    require(not any(part in {".sage", ".ssh", "sage-personal"} for part in path.parts), "private state input forbidden; export base configuration to staging")
    require(path.suffix not in {".key", ".pem", ".db", ".sqlite"}, "private key/state input forbidden")
    for component in (path, *path.parents):
        require(not component.is_symlink(), "symlink input forbidden")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 2 * 1024 * 1024, "unsafe/oversize input")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(2 * 1024 * 1024 + 1)
    finally:
        os.close(descriptor)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def reject_embedded_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            require(normalized not in {"private_key", "secret_key", "seed", "seed_hex", "password", "passphrase", "api_key", "access_token", "token", "cookie", "cookies"}, "embedded secret field forbidden")
            reject_embedded_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_embedded_secrets(child)


def metadata(raw):
    pairs = []
    for line in raw.decode().splitlines():
        key, separator, value = line.partition("=")
        require(separator and key, "invalid public inventory")
        pairs.append((key, value))
    return unique(pairs)


def yaml_document(raw):
    text = raw.decode("utf-8")
    require(not any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(text)), "YAML aliases/anchors unsupported")
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    require(isinstance(node, yaml.MappingNode), "SAGE YAML requires a mapping")

    def check(current):
        if isinstance(current, yaml.MappingNode):
            seen = set()
            for key, value in current.value:
                require(isinstance(key, yaml.ScalarNode) and key.tag == "tag:yaml.org,2002:str", "YAML keys must be strings")
                require(key.value not in seen, "duplicate YAML field")
                seen.add(key.value)
                check(value)
        elif isinstance(current, yaml.SequenceNode):
            for child in current.value:
                check(child)
    check(node)
    return text, node, yaml.safe_load(text)


def merge_yaml(raw, quotas, *, enabled=False):
    text, node, original = yaml_document(raw)
    reject_embedded_secrets(original)
    block = {"enabled": enabled, **quotas}
    prior = original.get("private_media")
    if "private_media" in original:
        require(type(prior) is dict and set(prior) <= set(block), "conflicting media section")
        for key, value in prior.items():
            require(type(value) is type(block[key]) and (value == block[key] or (key == "enabled" and value is False)), "conflicting media setting")
    fragment = yaml.safe_dump({"private_media": block}, sort_keys=False)
    spans = [(key, value) for key, value in node.value if key.value == "private_media"]
    if spans:
        key, value = spans[0]
        require(key.start_mark.column == 0 and value.flow_style is False, "media section requires block YAML")
        result = text[:key.start_mark.index] + fragment + text[value.end_mark.index:]
    else:
        result = text.rstrip("\n") + "\n" + fragment
    expected = copy.deepcopy(original)
    expected["private_media"] = block
    require(yaml_document(result.encode())[2] == expected, "YAML merge changed unrelated settings")
    return result


def merge_toml(raw, chain_id, *, enabled=False):
    text = raw.decode("utf-8")
    original = tomllib.loads(text)
    reject_embedded_secrets(original)
    sage = original.get("sage")
    require(type(sage) is dict, "missing sage table")
    require(re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", sage.get("base_url", "")) is not None, "explicit local endpoint required")
    require(0 < int(sage["base_url"].rsplit(":", 1)[1]) <= 65535, "invalid endpoint port")
    require(type(sage.get("identity_path")) is str and Path(sage["identity_path"]).is_absolute(), "explicit identity path required")
    require(type(sage.get("domain")) is str and bool(sage["domain"].strip()), "explicit domain required")
    require(chain_id != sage["domain"], "chain ID must not be a domain")
    additions = {"protected_storage": enabled, "chain_id": chain_id}
    if enabled:
        additions["conversation_dir"] = "/var/lib/sage-lantern/history/conversations-v1"
    for key, value in additions.items():
        require(key not in sage or (type(sage[key]) is type(value) and sage[key] == value), "conflicting protected sage setting")
    headers = list(re.finditer(r"(?m)^\[sage\][ \t]*(?:#[^\n]*)?(?:\n|$)", text))
    require(len(headers) == 1, "requires one unambiguous literal [sage] header")
    insert = "".join(key + " = " + json.dumps(value) + "\n" for key, value in additions.items() if key not in sage)
    offset = headers[0].end()
    result = text[:offset] + ("\n" if offset and text[offset-1] != "\n" else "") + insert + text[offset:]
    expected = copy.deepcopy(original)
    expected["sage"].update(additions)
    require(tomllib.loads(result) == expected, "TOML merge changed unrelated settings")
    return result, sage


def generated_config(raw, unit_id, sage_yaml, runtime_toml, quotas):
    require(type(raw) is bytes and len(raw) <= 8192, "initializer output oversized")
    identity = json.loads(raw, object_pairs_hook=unique)
    fields = {"schema", "unit_id", "chain_id", "root_id", "private_agent_id", "public_agent_id",
              "local_enrollment_committed", "public_enrolled", "vault_initialized", "ready", "provisioned"}
    require(type(identity) is dict and set(identity) == fields and identity["schema"] == "sage.lantern.initialized.v1"
            and identity["unit_id"] == unit_id and unit_id in {"unit-a", "unit-b"}, "initializer binding mismatch")
    require(all(identity[name] is False for name in ("local_enrollment_committed", "public_enrolled", "vault_initialized", "ready", "provisioned")),
            "initializer made unsupported readiness claim")
    identities = [identity[name] for name in ("root_id", "private_agent_id", "public_agent_id")]
    require(all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) for value in identities)
            and len(set(identities)) == 3, "invalid or reused generated identity")
    chain = identity["chain_id"]
    require(type(chain) is str and re.fullmatch(r"sage-personal-[A-Za-z0-9._-]{1,110}", chain), "invalid generated private chain")
    require(type(quotas) is dict and set(quotas) == set(QUOTAS)
            and all(type(value) is int and 0 < value <= 2**53-1 for value in quotas.values()), "explicit bounded quotas required")
    require(quotas["node_bytes"] >= quotas["actor_bytes"] and quotas["node_objects"] >= quotas["actor_objects"], "reversed quotas")
    config = yaml_document(sage_yaml)[2]
    require(config.get("federation", {}).get("enabled") is False and config.get("quorum", {}).get("enabled") is False,
            "private listener policy required")
    private_path = "/var/lib/sage-lantern/identity/lantern-agent.key"
    bootstrap = config.get("vendored_agent_bootstrap", {})
    require(bootstrap.get("agent_key_file") == private_path, "private bootstrap path mismatch")
    runtime, original = merge_toml(runtime_toml, chain, enabled=True)
    require(original["identity_path"] == private_path and original["base_url"] == "http://127.0.0.1:8080"
            and original["domain"] == bootstrap.get("home_domain"), "runtime private binding mismatch")
    public = {"schema": "sage.lantern.commissioned-bindings.v1", "unit_id": unit_id, "private_chain_id": chain,
              "private_agent_id": identity["private_agent_id"], "private_identity_path": private_path,
              "public_agent_id": identity["public_agent_id"], "public_identity_path": "/var/lib/sage-lantern/identity/lantern-public.key",
              "public_enrolled": False, "ready": False, "provisioned": False}
    return {"sage.yaml": merge_yaml(sage_yaml, quotas, enabled=True).encode(), "lantern.toml": runtime.encode(),
            "bindings.json": (json.dumps(public, sort_keys=True) + "\n").encode()}


def helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capabilities(args):
    result = {"sage_binary_inputs_checked": False, "runtime_wheel_checked": False, "sdk_wheel_checked": False}
    require(bool(args.sage_manifest) == bool(args.sage_binary), "SAGE manifest and binary must be supplied together")
    if args.sage_manifest:
        manifest = json.loads(read(args.sage_manifest), object_pairs_hook=unique)
        require(type(manifest) is dict and manifest.get("schema") == "sage.lantern.binary-staging.v1", "unsupported SAGE capability manifest")
        needed = {"cmd/sage-gui/private_media_config.go", "api/rest/private_media_handler.go", "api/rest/workflow_journal_handler.go", "internal/store/workflow_journal.go"}
        sources = manifest.get("source_sha256")
        require(type(sources) is dict and needed <= sources.keys(), "SAGE manifest lacks required source inputs")
        require(all(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) for digest in sources.values()), "invalid source digest")
        binary = Path(args.sage_binary).absolute()
        for component in (binary, *binary.parents):
            require(not component.is_symlink(), "linked binary forbidden")
        require(binary.is_file() and binary.stat().st_nlink == 1 and binary.stat().st_size <= 256 * 1024 * 1024, "unsafe binary")
        helper("sage_builder", "build-sage-release.py").verify_elf(binary)
        require(hashlib.sha256(binary.read_bytes()).hexdigest() == manifest.get("binary_sha256"), "binary hash mismatch")
        result["sage_binary_inputs_checked"] = True
    if args.runtime_wheel:
        read(args.runtime_wheel)
        helper("runtime_verifier", "verify-runtime-release.py").verify_wheel(Path(args.runtime_wheel))
        result["runtime_wheel_checked"] = True
    if args.sdk_wheel:
        read(args.sdk_wheel)
        builder = helper("sdk_builder", "build-sdk-release.py")
        with zipfile.ZipFile(args.sdk_wheel) as archive:
            manifest = archive.read("sage_sdk/release-manifest.json")
        original = builder.snapshot(ROOT.parent / "sage/sdk/python")
        payloads = builder.staged_payloads(original)
        document = json.loads(manifest, object_pairs_hook=unique)
        require(type(document) is dict and document.get("schema") == "sage.lantern.sdk-release.v1", "unsupported SDK manifest")
        require(document.get("source_sha256") == builder.hashes(original) and document.get("staged_sha256") == builder.hashes(payloads) and document.get("version") == builder.VERSION, "SDK manifest mismatch")
        builder.verify_payload(Path(args.sdk_wheel), payloads, manifest)
        result["sdk_wheel_checked"] = True
    return result


def assemble(args):
    require(args.unit_id in {"unit-a", "unit-b"}, "unsupported unit")
    require(args.operator_approved, "explicit operator approval required")
    chain = args.chain_id
    require(type(chain) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", chain) is not None, "invalid chain binding")
    require(not any(word in chain.lower() for word in ("placeholder", "example", "replace", "your-", "tbd")) and chain != args.unit_id, "placeholder/derived chain binding forbidden")
    inputs = {name: read(getattr(args, name)) for name in ("sage_yaml", "runtime_toml", "inventory", "public_identity")}
    inventory = metadata(inputs["inventory"])
    identity = json.loads(inputs["public_identity"], object_pairs_hook=unique)
    require(inventory.get("INVENTORY_SCHEMA") == "sage-lantern-identity-v1" and inventory.get("UNIT_ID") == args.unit_id, "inventory unit mismatch")
    require(type(identity) is dict and identity.get("schema") == "sage.lantern.personal-public-identity.v1", "unsupported public identity schema")
    reject_embedded_secrets(identity)
    voice = identity.get("voice_identity")
    require(type(voice) is dict and set(voice) == {"algorithm", "public_key_hex"} and voice["algorithm"] == "ed25519" and type(voice["public_key_hex"]) is str and re.fullmatch(r"[0-9a-f]{64}", voice["public_key_hex"]), "invalid public voice identity")
    fingerprint = "sha256:" + hashlib.sha256(json.dumps(voice, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(identity.get("voice_identity_fingerprint") == fingerprint, "invalid public voice fingerprint")
    require(inventory.get("PERSONAL_CHAIN_ID") == identity.get("personal_chain_id") == chain, "private chain binding mismatch")
    for inventory_field, identity_field in (("PERSONAL_GENESIS_FINGERPRINT", "personal_genesis_fingerprint"), ("VOICE_IDENTITY_FINGERPRINT", "voice_identity_fingerprint")):
        value = inventory.get(inventory_field)
        require(type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None and value == identity.get(identity_field), "public binding fingerprint mismatch")
    quotas = {name: getattr(args, name) for name in QUOTAS}
    require(all(type(value) is int and 0 < value <= 2**63-1 for value in quotas.values()), "positive explicit quotas required")
    require(quotas["node_bytes"] >= quotas["actor_bytes"] and quotas["node_objects"] >= quotas["actor_objects"], "reversed quotas")
    require(quotas["min_free_bytes"] <= 2**63-1-4*(2*1024*1024+1024)-65536, "reserve overflow")
    sage = merge_yaml(inputs["sage_yaml"], quotas)
    runtime, original_sage = merge_toml(inputs["runtime_toml"], chain)
    checked = capabilities(args)
    staging = Path(args.staging_root).absolute()
    for component in (staging, *staging.parents):
        require(not component.is_symlink(), "linked staging directory")
    require(not any(part in {".sage", ".ssh", "sage-personal", "sage-lantern"} for part in staging.parts), "live/private staging path forbidden")
    require(not any(staging == Path(prefix) or Path(prefix) in staging.parents for prefix in ("/etc", "/opt", "/usr", "/run")), "live staging root forbidden")
    info = staging.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, "staging root must be existing owned mode 0700")
    output = staging / args.unit_id
    plan = {"schema": "sage.lantern.protected-config-candidate.v1", "unit_id": args.unit_id,
            "mode": "disabled-candidate", "operator_approved": True, "binding_inputs_consistent": True,
            "chain_binding_verified": False, "chain_id": chain, "sage_endpoint": original_sage["base_url"],
            "applied": False, "backend_verified": False, "target_disk_verified": False, "provisioned": False,
            "activation_required": True, "artifact_checks": checked,
            "input_sha256": {key: hashlib.sha256(value).hexdigest() for key, value in inputs.items()},
            "blockers": ["Independently verify the operator-supplied local chain/agent/endpoint binding on the target.",
                         "Verify matched installed SAGE/SDK/runtime capabilities, active vault and media backend.",
                         "Probe actual SQLite filesystem capacity; no staging-host space claim substitutes.",
                         "Separate authorized activation must enable both candidate settings; no PROVISIONED write."]}
    payloads = {"sage.candidate.yaml": sage.encode(), "lantern.candidate.toml": runtime.encode()}
    plan["output_sha256"] = {key: hashlib.sha256(value).hexdigest() for key, value in payloads.items()}
    payloads["plan.json"] = (json.dumps(plan, sort_keys=True, indent=2) + "\n").encode()
    require(all(read(getattr(args, name)) == data for name, data in inputs.items()), "base input changed during assembly")
    output.mkdir(mode=0o700)
    for name, payload in payloads.items():
        descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    return plan


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--unit-id", choices=("unit-a", "unit-b"), required=True)
    result.add_argument("--operator-approved", action="store_true")
    for name in ("chain-id", "sage-yaml", "runtime-toml", "inventory", "public-identity", "staging-root"):
        result.add_argument("--" + name, required=True)
    for name in QUOTAS:
        result.add_argument("--" + name.replace("_", "-"), type=int, required=True)
    for name in ("sage-manifest", "sage-binary", "runtime-wheel", "sdk-wheel"):
        result.add_argument("--" + name)
    return result


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    try:
        print(json.dumps(assemble(parser().parse_args()), sort_keys=True))
    except (ValueError, OSError, yaml.YAMLError, KeyError, TypeError, zipfile.BadZipFile):
        print("Candidate assembly rejected; inspect inputs locally. No activation performed.", file=sys.stderr)
        raise SystemExit(2)
