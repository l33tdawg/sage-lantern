#!/usr/bin/env python3
"""Read-only wheel/source/SDK gates; no clients, identities or services created."""

import argparse
import ast
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"
SDK_VERSION = "11.19.10+lantern.20260906"
REQUIRED = {"network_setup", "device_lock", "device_access", "vault_setup", "power_settings",
            "conversation_sage", "conversation_store", "conversation_service", "history_media",
            "community_service", "community_messaging", "community_public", "canonical_messaging",
            "message_dictation", "message_storage", "idle_suspend", "suspend_kiosk", "inference_guard", "deployment_drain",
            "workflow_store", "private_media_store", "conversation_records", "conversation_workflow_store",
            "protected_conversation_owner", "public_workflow_store", "mesh_contact_registry", "mesh_contacts",
            "mesh_envelope", "mesh_fragments", "mesh_inbox", "mesh_mailbox", "mesh_outbox", "mesh_receipt_sender",
            "mesh_receiver", "mesh_runtime", "mesh_serial_owner", "mesh_usb_opener", "meshcore_contacts", "meshcore_transport",
            "mesh_daemon", "mesh_voice_ipc", "mesh_read_receipts", "mesh_read_root", "mesh_public_root", "mesh_gateway_root",
            "storage_supervisor", "storage_supervisor_socket", "gateway_contact", "gateway_evidence",
            "public_mesh_protocol", "public_mesh_provider", "public_gateway_bridge", "public_gateway_runtime", "public_gateway_composition",
            "public_gateway_observation", "public_queue"}
SDK_APIS = {
    "__init__": {"trust_env"},
    "get_profile": set(), "owned_domains": set(), "domain_access_sample": set(),
    "get_memory": {"memory_id"}, "list_memories": {"provider", "agent", "status"},
    "propose": {"content", "memory_type", "domain_tag", "classification", "embedding", "linked_memories"},
    "embed": {"text"}, "hybrid": {"query", "embedding", "provider", "status_filter"}, "forget": {"memory_id"},
    "pipe_inbox_history": {"limit"}, "pipe_outbox": {"limit"}, "lookup_agents": {"name", "limit"},
    "pipe_resolve": {"to"}, "message_send": {"to_agent", "payload", "idempotency_key", "intent", "ttl_minutes", "require_encrypted_storage"},
    "message_storage_status": set(),
    "pipe_send": {"payload", "to_agent", "idempotency_key"},
    "workflow_get": {"record_id"}, "workflow_list": {"after", "limit"},
    "workflow_put": {"record_id", "kind", "expected_revision", "payload", "guard"},
    "private_media_get": {"object_id"}, "private_media_put": {"object_id", "jpeg"},
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _regular_bytes(path, maximum):
    for component in (path, *path.parents):
        require(not component.is_symlink(), "linked release input")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= maximum, "unsafe release input")
    data = path.read_bytes()
    require(len(data) == info.st_size, "release input changed")
    return data


def verify_target_binary(root=ROOT):
    """Execute only the hash-reviewed target binary's version command before install.

    The operator-trusted lock is integrity metadata, not a publisher signature or
    proof of server capabilities. This mode belongs on the target, not the host.
    """
    root = Path(root).absolute()
    lock_path = root / "software/stack.lock.json"
    lock_bytes = _regular_bytes(lock_path, 2 * 1024 * 1024)
    lock = json.loads(lock_bytes)
    expected = lock["components"]["sage"]["version"]
    require(type(expected) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", expected), "invalid reviewed SAGE version")
    relative = "software/bundle/linux-arm64/bin/sage-gui"
    entries = [entry for entry in lock["files"] if entry["path"] == relative]
    require(len(entries) == 1, "SAGE binary not uniquely locked")
    binary = root / relative
    data = _regular_bytes(binary, 256 * 1024 * 1024)
    require(len(data) == entries[0]["bytes"] and hashlib.sha256(data).hexdigest() == entries[0]["sha256"], "SAGE binary hash mismatch")
    require(len(data) >= 64 and data[:7] == b"\x7fELF\x02\x01\x01" and int.from_bytes(data[18:20], "little") == 183,
            "SAGE binary is not ELF64 AArch64")
    result = subprocess.run([str(binary), "version"], cwd="/", capture_output=True, text=True, timeout=5,
                            env={"PATH": os.defpath, "HOME": "/nonexistent", "SAGE_HOME": "/nonexistent", "LANG": "C"})
    require(result.returncode == 0 and len(result.stdout) <= 1024 and result.stderr == "", "SAGE version command failed")
    match = re.fullmatch(r"sage-gui ([A-Za-z0-9][A-Za-z0-9._+-]{0,127}) \(commit [^,\r\n]+, built [^,\r\n)]+(?:, max-app-v[0-9]+(?:, auto-vote-v[0-9]+)?)?\)\n?", result.stdout)
    require(match is not None and match[1] == expected, "SAGE binary reviewed version mismatch")
    require(_regular_bytes(binary, 256 * 1024 * 1024) == data and _regular_bytes(lock_path, 2 * 1024 * 1024) == lock_bytes,
            "release changed during binary preflight")
    print("Verified exact locked target SAGE hash/version; server capabilities remain unverified.")
    return expected


def verify_sdk_methods(methods):
    for name, parameters in SDK_APIS.items():
        require(name in methods and parameters <= methods[name], "missing pinned SDK API: " + name)


def canonical_messaging_capability(methods):
    missing_methods = [] if "message_storage_status" in methods else ["message_storage_status"]
    missing_parameters = [] if "require_encrypted_storage" in methods.get("message_send", set()) else ["message_send.require_encrypted_storage"]
    return {"available": False, "verified": False, "missing_methods": missing_methods,
            "missing_parameters": missing_parameters,
            "reason": "sdk_storage_api_unavailable" if missing_methods or missing_parameters
            else "runtime_storage_and_auth_verification_required"}


def verify_wheel(path, root=ROOT):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and len(names) <= 160, "invalid wheel inventory")
        require(all(info.file_size <= 2 * 1024 * 1024 for info in archive.infolist()), "oversize wheel member")
        manifest = json.loads(archive.read("sage_lantern/release-manifest.json"))
        require(manifest["version"] == VERSION and manifest["schema"] == "sage.lantern.runtime-release.v1", "wrong release")
        expected = {"src/" + name for name in names if name.startswith("sage_lantern/") and name.endswith(".py")}
        actual = {str(path.relative_to(root / "software/runtime"))
                  for path in (root / "software/runtime/src/sage_lantern").glob("*.py")}
        require(expected == actual, "runtime source inventory differs from wheel")
        dist_info = f"sage_lantern_runtime-{VERSION}.dist-info/"
        allowed = {name.removeprefix("src/") for name in expected} | {"sage_lantern/release-manifest.json"}
        allowed |= {dist_info + name for name in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD")}
        require(set(names) == allowed, "unexpected release payload")
        require(REQUIRED <= {Path(name).stem for name in expected}, "required module missing")
        require(set(manifest["source_sha256"]) == expected | {"pyproject.toml"}, "incomplete source manifest")
        for name, checksum in manifest["source_sha256"].items():
            source = (root / "software/runtime" / name).read_bytes()
            require(hashlib.sha256(source).hexdigest() == checksum, "stale runtime source: " + name)
            if name.startswith("src/"):
                require(archive.read(name.removeprefix("src/")) == source, "wheel source mismatch")
        metadata = archive.read(f"sage_lantern_runtime-{VERSION}.dist-info/METADATA").decode()
        for dependency in ("PyNaCl==1.6.2", "cryptography==46.0.3", f"sage-agent-sdk=={SDK_VERSION}", "PyYAML==6.0.2"):
            require("requires-dist: " + dependency.lower() in metadata.lower(), "missing dependency pin")
        require(not any(name.startswith("sage_sdk/") for name in names), "SDK must remain a separate artifact")
        return manifest


YAML_WHEEL = 'PyYAML-6.0.2-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl'
YAML_SHA256 = '5d225db5a45f21e78dd9358e58a98702a0302f2659a3c6cd320564b75b86f47c'


def verify_yaml_wheel(wheelhouse):
    candidates = [path for path in wheelhouse.glob('*.whl') if path.name.lower().startswith('pyyaml-')]
    require(len(candidates) == 1 and candidates[0].name == YAML_WHEEL, 'missing/ambiguous pinned ARM64 PyYAML wheel')
    require(hashlib.sha256(_regular_bytes(candidates[0], 2 * 1024 * 1024)).hexdigest() == YAML_SHA256,
            'PyYAML wheel hash mismatch')
    return candidates[0]


def verify_yaml_installed():
    require(importlib.metadata.version('PyYAML') == '6.0.2', 'wrong installed dependency: PyYAML')
    package = importlib.import_module('yaml')
    distribution = importlib.metadata.distribution('PyYAML')
    require(package.__version__ == '6.0.2'
            and Path(package.__file__).resolve() == Path(distribution.locate_file('yaml/__init__.py')).resolve()
            and Path(package.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),
            'PyYAML shadowed or wrong version')
    require(package.safe_load('enabled: true\n') == {'enabled': True}, 'PyYAML parser unavailable')


def verify_bundle(root=ROOT):
    wheelhouse = root / "software/bundle/python-wheels"
    lock = json.loads((root / "software/stack.lock.json").read_text())
    require(lock["components"]["lantern-runtime"]["version"] == VERSION, "wrong locked runtime version")
    paths = [wheelhouse / f"sage_lantern_runtime-{VERSION}-py3-none-any.whl",
             wheelhouse / f"sage_agent_sdk-{SDK_VERSION}-py3-none-any.whl"]
    crypto = list(wheelhouse.glob("cryptography-46.0.3-*_aarch64.whl"))
    require(len(crypto) == 1, "missing/ambiguous pinned ARM64 cryptography wheel")
    paths += crypto
    paths.append(verify_yaml_wheel(wheelhouse))
    for path in paths:
        entries = [entry for entry in lock["files"] if entry["path"] == str(path.relative_to(root))]
        require(len(entries) == 1, "artifact not uniquely locked")
        require(path.stat().st_size == entries[0]["bytes"] and hashlib.sha256(path.read_bytes()).hexdigest() == entries[0]["sha256"], "artifact hash mismatch")
    verify_wheel(paths[0], root)
    with zipfile.ZipFile(paths[1]) as archive:
        tree = ast.parse(archive.read("sage_sdk/client.py"))
        client = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SageClient")
        methods = {node.name: {arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
                   for node in client.body if isinstance(node, ast.FunctionDef)}
        verify_sdk_methods(methods)
    print(f"Verified frozen runtime and separate SDK {SDK_VERSION} API surface; not server/native acceptance.")
    print(json.dumps({"canonical_messaging": canonical_messaging_capability(methods)}, sort_keys=True))


def verify_installed():
    package = importlib.import_module("sage_lantern")
    require(package.__version__ == VERSION and importlib.metadata.version("sage-lantern-runtime") == VERSION, "wrong installed runtime")
    for name, version in (("sage-agent-sdk", SDK_VERSION), ("cryptography", "46.0.3"), ("PyNaCl", "1.6.2")):
        require(importlib.metadata.version(name) == version, "wrong installed dependency: " + name)
    verify_yaml_installed()
    directory = Path(package.__file__).parent
    distribution = importlib.metadata.distribution("sage-lantern-runtime")
    require(directory.resolve() == Path(distribution.locate_file("sage_lantern")).resolve(), "runtime shadowed by source path")
    manifest = json.loads((directory / "release-manifest.json").read_text())
    modules = set()
    for name, checksum in manifest["source_sha256"].items():
        if not name.startswith("src/sage_lantern/"):
            continue
        path = directory / Path(name).name
        require(hashlib.sha256(path.read_bytes()).hexdigest() == checksum, "installed source hash mismatch")
        modules.add(path.stem)
    require(REQUIRED <= modules, "required installed module missing")
    require({path.stem for path in directory.glob("*.py")} == modules, "unexpected installed source overlay")
    for module in sorted(modules - {"__main__"}):
        importlib.import_module("sage_lantern." + module)
    from sage_sdk import SageClient
    methods = {name: set(inspect.signature(getattr(SageClient, name)).parameters)
               for name in (*SDK_APIS, "message_storage_status") if callable(getattr(SageClient, name, None))}
    verify_sdk_methods(methods)
    print(f"Imported {len(modules) - 1} installed modules; hashed {len(modules)} source files; SDK signatures verified without client construction.")
    print(json.dumps({"canonical_messaging": canonical_messaging_capability(methods)}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bundle", action="store_true")
    mode.add_argument("--installed", action="store_true")
    mode.add_argument("--binary-target", action="store_true", help="Run hash-reviewed target binary version check; no install actions")
    arguments = parser.parse_args()
    if arguments.bundle:
        verify_bundle()
    elif arguments.binary_target:
        verify_target_binary()
    else:
        verify_installed()
