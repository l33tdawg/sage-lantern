#!/usr/bin/env python3
"""Closed-tree appliance integrity manifest, not signed authenticity/readiness.

Consumes a separately prepared sterile tree and explicit operator selection.
Does not build, copy, install, execute selected files or discover device state.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


SCHEMA = "sage.lantern.appliance-manifest.v1"
SELECTION_SCHEMA = "sage.lantern.appliance-selection.v1"
MAX_FILES = 100000
MAX_FILE = 4 * 1024 ** 3
MAX_TOTAL = 16 * 1024 ** 3
ROLES = {"installer", "root_helper", "systemd", "dropin", "config_template", "ui", "runtime_source", "sdk_source",
         "sage_binary", "sage_manifest", "sdk_wheel", "runtime_wheel", "support", "dependency"}
SINGLE = {"sage_binary", "sage_manifest", "sdk_wheel", "runtime_wheel"}
REQUIRED = ROLES - {"support", "dependency"}
DENIED_PARTS = {".git", ".ssh", ".sage", "__pycache__", ".pytest_cache", ".cache", "sage-personal", "device-lock",
                "photos", "journals", "queues", "sessions", "logs", "secrets", "identity"}
DENIED_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".pyc", ".pth", ".log"}
SECRET_PATTERN = re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----(?:\r?\n|\\n|\\r\\n)[A-Za-z0-9+/=]{32}")
PUBLIC_EXAMPLE_FILES = {
    'node_modules/miniflare/dist/src/index.js': '9584409d464f6c21d720b20da3c5dc6e5bdab0393f9cb19b36cb960beaf0958b',
    'node_modules/wrangler/wrangler-dist/cli.js': 'fc1aa72afc91906759a3555e76e6dae1b13504f71dec6d3bbfa0fe24c351ebd7',
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode("ascii")


def document(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def safe_name(name):
    require(type(name) is str and 1 <= len(name) <= 1024 and "\\" not in name, "invalid relative path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and str(path) == name and all(part not in {".", ".."} for part in path.parts), "unsafe relative path")
    dependency_name = name.removeprefix("apps/lantern-fleet-admin/")
    packaged_queue_code = dependency_name in {
        "node_modules/miniflare/dist/src/workers/queues",
        "node_modules/miniflare/dist/src/workers/queues/broker.worker.js",
        "node_modules/miniflare/dist/src/workers/queues/broker.worker.js.map",
    }
    require(all((part.lower() not in DENIED_PARTS or (part == "queues" and packaged_queue_code))
                and not part.lower().startswith(".env") for part in path.parts), "state/secret path forbidden")
    require(path.suffix.lower() not in DENIED_SUFFIXES, "state/key/cache payload forbidden")
    require(path.name.lower() not in {"credentials.json", "cookies.json", "history.json", "auth.json", "unit.env", "unit-inventory.env", "provisioned"}, "device state forbidden")
    return name


def no_links(path):
    for component in (path, *path.parents):
        require(not component.is_symlink(), "linked path forbidden")


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode), info.st_nlink)


def _read_file(path, *, maximum=MAX_FILE, scan_secrets=True, capture=False):
    no_links(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= maximum, "unsafe file")
        require(stat.S_IMODE(before.st_mode) in {0o644, 0o755}, "unexpected file permissions")
        digest = hashlib.sha256()
        total = 0
        tail = b""
        chunks = []
        public_example_hash = next((value for name, value in PUBLIC_EXAMPLE_FILES.items()
                                    if path.parts[-len(PurePosixPath(name).parts):] == PurePosixPath(name).parts), None)
        secret_found = False
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            require(total <= maximum, "file grew beyond bound")
            if scan_secrets:
                if SECRET_PATTERN.search(tail + chunk) is not None:
                    require(public_example_hash is not None, "embedded private-key material")
                    secret_found = True
                tail = chunk[-128:]
            digest.update(chunk)
            if capture:
                chunks.append(chunk)
        after = os.fstat(descriptor)
        require(identity(before) == identity(after) == identity(path.lstat()) and total == before.st_size, "file changed while hashing")
        require(not secret_found or digest.hexdigest() == public_example_hash, "embedded private-key material in unreviewed dependency bytes")
        return dict(bytes=total, sha256=digest.hexdigest(), mode=stat.S_IMODE(before.st_mode)), identity(before), b"".join(chunks)
    finally:
        os.close(descriptor)


def file_hash(path, *, maximum=MAX_FILE, scan_secrets=True):
    return _read_file(path, maximum=maximum, scan_secrets=scan_secrets)[:2]


def metadata_bytes(path):
    _, observed, content = _read_file(path, maximum=32 * 1024 * 1024, capture=True)
    return content, observed


def tree(root):
    no_links(root)
    require(root.is_dir(), "staging root missing")
    files, directories = {}, {}
    for directory, children, names in os.walk(root, followlinks=False):
        directory = Path(directory)
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and not directory.is_symlink(), "linked/non-directory stage")
        require(stat.S_IMODE(info.st_mode) == 0o755, "unexpected directory permissions")
        directories[str(directory.relative_to(root))] = identity(info)
        require(len(files) + len(directories) <= MAX_FILES * 2, "tree exceeds bound")
        for name in children + names:
            path = directory / name
            relative = safe_name(path.relative_to(root).as_posix())
            info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode), "linked payload forbidden")
            if name in names:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "nonregular/hardlinked payload")
                files[relative] = identity(info)
        require(len(files) <= MAX_FILES, "file inventory exceeds bound")
    return files, directories


def selection(value):
    require(type(value) is dict and set(value) == {"schema", "release_id", "components", "files"}
            and value["schema"] == SELECTION_SCHEMA, "invalid selection schema")
    require(type(value["release_id"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value["release_id"]), "invalid release ID")
    require(type(value["components"]) is dict and set(value["components"]) == {"sage", "sdk", "runtime"}, "missing exact component versions")
    require(all(type(version) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", version)
                for version in value["components"].values()), "invalid component version")
    require(type(value["files"]) is list and 1 <= len(value["files"]) <= MAX_FILES, "invalid selection inventory")
    paths, roles = set(), []
    for entry in value["files"]:
        require(type(entry) is dict and set(entry) == {"path", "role"} and entry["role"] in ROLES, "invalid selected entry")
        name = safe_name(entry["path"])
        require(name not in paths, "duplicate selected path")
        paths.add(name)
        roles.append(entry["role"])
    require(REQUIRED <= set(roles) and all(roles.count(role) == 1 for role in SINGLE), "missing/ambiguous appliance role")
    return value


def helper(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def matched(root, selected):
    def one(role):
        return root / next(entry["path"] for entry in selected["files"] if entry["role"] == role)
    runtime = helper("verify-runtime-release")
    require(selected["components"]["runtime"] == runtime.VERSION and selected["components"]["sdk"] == runtime.SDK_VERSION, "verifier component version mismatch")
    runtime.verify_wheel(one("runtime_wheel"), root)
    sdk = helper("build-sdk-release")
    original = sdk.snapshot(root / "sources/sage-sdk")
    payloads = sdk.staged_payloads(original)
    with zipfile.ZipFile(one("sdk_wheel")) as archive:
        require(len(archive.infolist()) <= 160 and all(info.file_size <= 2 * 1024 * 1024 for info in archive.infolist()), "oversize SDK archive")
        raw = archive.read("sage_sdk/release-manifest.json")
    manifest = document(raw)
    require(manifest.get("schema") == "sage.lantern.sdk-release.v1" and manifest.get("version") == runtime.SDK_VERSION
            and manifest.get("source_sha256") == sdk.hashes(original) and manifest.get("staged_sha256") == sdk.hashes(payloads), "SDK source manifest mismatch")
    sdk.verify_payload(one("sdk_wheel"), payloads, raw)
    import ast
    parsed = ast.parse(payloads["src/sage_sdk/client.py"])
    client = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == "SageClient")
    methods = {node.name: {arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
               for node in client.body if isinstance(node, ast.FunctionDef)}
    runtime.verify_sdk_methods(methods)
    require(one("sage_manifest").stat().st_size <= 32 * 1024 * 1024, "oversize SAGE manifest")
    binary_manifest = document(one("sage_manifest").read_bytes())
    require(binary_manifest.get("schema") == "sage.lantern.binary-staging.v1"
            and binary_manifest.get("version") == selected["components"]["sage"], "SAGE manifest version mismatch")
    helper("build-sage-release").verify_elf(one("sage_binary"))
    require(file_hash(one("sage_binary"))[0]["sha256"] == binary_manifest.get("binary_sha256"), "SAGE binary manifest mismatch")
    for name in ("source_sha256", "dependency_input_sha256", "toolchain_sha256"):
        require(type(binary_manifest.get(name)) is dict and bool(binary_manifest[name]), "incomplete SAGE provenance")
        require(all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) for value in binary_manifest[name].values()), "invalid SAGE source digest")
    require(type(binary_manifest.get("modules")) is dict and bool(binary_manifest["modules"]), "missing SAGE module inventory")
    source_inventory = {name: binary_manifest[name] for name in ("source_sha256", "dependency_input_sha256", "modules")}
    require(hashlib.sha256(canonical(source_inventory)).hexdigest() == binary_manifest.get("inventory_sha256"), "SAGE source inventory mismatch")
    ui = {entry["path"] for entry in selected["files"] if entry["role"] == "ui"}
    require("apps/lantern-fleet-admin/dist/server/index.js" in ui
            and "apps/lantern-fleet-admin/package.json" in ui and "apps/lantern-fleet-admin/package-lock.json" in ui,
            "missing built UI entry/lock/package")
    require(any(name.startswith("apps/lantern-fleet-admin/node_modules/") for name in ui), "missing staged UI dependencies")


def inventory(root, selected):
    before = tree(root)
    expected = {entry["path"] for entry in selected["files"]}
    require(set(before[0]) == expected, "extra/missing selected payload")
    allowed_directories = {"."} | {str(parent) for name in expected for parent in PurePosixPath(name).parents}
    require(set(before[1]) == allowed_directories, "unselected empty directory")
    entries = []
    total = 0
    for entry in sorted(selected["files"], key=lambda value: value["path"]):
        details, observed = file_hash(root / entry["path"])
        require(observed == before[0][entry["path"]], "source changed before hashing")
        total += details["bytes"]
        require(total <= MAX_TOTAL, "release exceeds byte bound")
        entries.append({**entry, **details})
    matched(root, selected)
    for entry in entries:
        details, observed = file_hash(root / entry["path"])
        require(details == {key: entry[key] for key in ("bytes", "sha256", "mode")}
                and observed == before[0][entry["path"]], "source changed during manifest build")
    require(tree(root) == before, "source inventory changed during manifest build")
    return entries


def build(root, selection_path, output):
    root, selection_path, output = (Path(path).absolute() for path in (root, selection_path, output))
    no_links(selection_path)
    no_links(output)
    require(output != root and root not in output.parents and root not in selection_path.parents, "metadata must be outside closed stage")
    require(not output.exists(), "manifest output must be new")
    raw, observed = metadata_bytes(selection_path)
    selected = selection(document(raw))
    files = inventory(root, selected)
    require(metadata_bytes(selection_path) == (raw, observed), "selection changed during build")
    manifest = dict(schema=SCHEMA, release_id=selected["release_id"], selection=selected,
                    selection_sha256=hashlib.sha256(canonical(selected)).hexdigest(), files=files,
                    deployment_ready=False, runtime_acceptance_verified=False, signed_authenticity=False)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), 0o644)
        stream.write(canonical(manifest))
        stream.flush()
        os.fsync(stream.fileno())
    return manifest


def verify(root, manifest_path):
    root, path = Path(root).absolute(), Path(manifest_path).absolute()
    raw, observed = metadata_bytes(path)
    value = document(raw)
    require(type(value) is dict and set(value) == {"schema", "release_id", "selection", "selection_sha256", "files",
        "deployment_ready", "runtime_acceptance_verified", "signed_authenticity"} and value["schema"] == SCHEMA, "invalid manifest")
    require(all(value[key] is False for key in ("deployment_ready", "runtime_acceptance_verified", "signed_authenticity")), "unsupported readiness/authenticity claim")
    selected = selection(value["selection"])
    require(value["release_id"] == selected["release_id"] and value["selection_sha256"] == hashlib.sha256(canonical(selected)).hexdigest(), "selection digest mismatch")
    require(inventory(root, selected) == value["files"], "manifest payload mismatch")
    require(metadata_bytes(path) == (raw, observed), "manifest changed during verification")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    args = parser.parse_args()
    if args.mode == "build":
        require(args.selection is not None, "build requires explicit selection")
        result = build(args.root, args.selection, args.manifest)
    else:
        require(args.selection is None, "verify uses manifest-bound selection")
        result = verify(args.root, args.manifest)
    print(json.dumps({"release_id": result["release_id"], "files": len(result["files"]), "deployment_ready": False,
                      "runtime_acceptance_verified": False, "signed_authenticity": False}, sort_keys=True))
