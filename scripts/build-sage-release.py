#!/usr/bin/env python3
"""Stage an unpromoted offline linux/arm64 sage-gui; never install or run it."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
INPUT_FIELDS = ("GoFiles", "SFiles", "HFiles", "SysoFiles", "EmbedFiles")
DENIED = {".git", ".sage", ".env", "__pycache__", "node_modules"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def source_bytes(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("symlink source input")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 256 * 1024 * 1024:
        raise ValueError("unsafe source input")
    return path.read_bytes()


def relative_input(name):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or any(part in DENIED for part in path.parts):
        raise ValueError("unsafe build input path")
    if path.suffix.lower() in {".key", ".pem", ".p12", ".sqlite", ".db"}:
        raise ValueError("private-state-like build input")
    return path


def environment():
    result = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT") if key in os.environ}
    result.update(GOENV="off", GOWORK="off", GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off",
                  GOTELEMETRY="off", CGO_ENABLED="0", GOOS="linux", GOARCH="arm64", GOARM64="v8.0",
                  GOFLAGS="", GOEXPERIMENT="", GONOPROXY="none", GONOSUMDB="none", GOVCS="*:off")
    return result


def run(command, cwd, env, timeout=120):
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if completed.returncode:
        raise RuntimeError("offline Go command failed: " + completed.stderr[-12000:])
    return completed.stdout


def json_stream(text):
    decoder = json.JSONDecoder()
    while text.strip():
        value, end = decoder.raw_decode(text.lstrip())
        yield value
        text = text.lstrip()[end:]


def snapshot(source, go, env):
    packages = list(json_stream(run([go, "list", "-mod=readonly", "-deps", "-json", "./cmd/sage-gui"], source, env)))
    if not packages or len(packages) > 10000:
        raise ValueError("invalid dependency graph")
    local = {}
    dependencies = {}
    modules = {}

    def capture(path, external_name):
        data = source_bytes(path)
        if path.is_relative_to(source):
            name = str(relative_input(str(path.relative_to(source))))
            local[name] = data
        else:
            dependencies[external_name] = digest(data)

    for name in ("go.mod", "go.sum"):
        capture(source / name, name)
    for package in packages:
        if package.get("Error") or package.get("DepsErrors") or package.get("CgoFiles"):
            raise ValueError("incomplete or CGO-dependent graph")
        if package["ImportPath"] == "unsafe":
            continue
        directory = Path(package["Dir"])
        for field in INPUT_FIELDS:
            for name in package.get(field, []):
                path = directory / relative_input(name)
                capture(path, package["ImportPath"] + "/" + name)
        module = package.get("Module")
        if module:
            replacement = module.get("Replace", module)
            modules[module["Path"]] = {key: replacement[key] for key in ("Path", "Version", "Sum", "GoModSum") if key in replacement}
            if replacement.get("GoMod"):
                modfile = Path(replacement["GoMod"])
                capture(modfile, "module:" + module["Path"] + "/go.mod")
                if modfile.is_relative_to(source) and modfile.with_name("go.sum").exists():
                    capture(modfile.with_name("go.sum"), "unused")
            if replacement.get("Dir") and not replacement.get("Version") and not Path(replacement["Dir"]).is_relative_to(source):
                raise ValueError("local replacement outside source root")
    if "cmd/sage-gui/main.go" not in local:
        raise ValueError("missing target source")
    return local, dependencies, modules


def source_manifest(snapshot_value):
    local, dependencies, modules = snapshot_value
    return {"source_sha256": {name: digest(data) for name, data in sorted(local.items())},
            "dependency_input_sha256": dependencies, "modules": modules}


def verify_elf(path):
    with path.open("rb") as stream:
        header = stream.read(64)
    if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01" or int.from_bytes(header[18:20], "little") != 183:
        raise ValueError("artifact is not little-endian ELF64 AArch64")


def build(source, output, build_id, epoch):
    with tempfile.TemporaryDirectory(prefix="lantern-go-telemetry-") as directory:
        Path(directory, "mode").write_text("off\n")
        env = environment()
        env["TEST_TELEMETRY_DIR"] = directory
        return build_offline(source, output, build_id, epoch, env)


def build_offline(source, output, build_id, epoch, env):
    source, output = Path(source).absolute(), Path(output).absolute()
    if output.exists() or source == output or source in output.parents:
        raise ValueError("use a new output directory outside source")
    for path in (source, output, *source.parents, *output.parents):
        if path.is_symlink():
            raise ValueError("linked source/output directory")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", build_id):
        raise ValueError("invalid local build ID")
    date = datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    go_path = shutil.which("go")
    if not go_path:
        raise ValueError("installed Go toolchain required")
    go = str(Path(go_path).resolve())
    toolchain = json.loads(run([go, "env", "-json", "GOVERSION", "GOROOT", "GOTOOLDIR", "GOMODCACHE", "GOTELEMETRY"], source, env))
    if toolchain["GOTELEMETRY"] != "off":
        raise ValueError("isolated Go telemetry must be off")
    tool_hashes = {"go": digest(source_bytes(Path(go)))}
    for name in ("compile", "asm", "link"):
        tool_hashes[name] = digest(source_bytes(Path(toolchain["GOTOOLDIR"]) / name))
    for header in sorted((Path(toolchain["GOROOT"]) / "pkg/include").glob("*.h")):
        tool_hashes["include/" + header.name] = digest(source_bytes(header))
    original = snapshot(source, go, env)
    inventory = source_manifest(original)
    inventory_hash = digest(encoded(inventory))
    version = "lantern-local." + build_id
    flags = f"-buildid= -s -w -X main.version={version} -X main.commit=source-{inventory_hash[:16]} -X main.date={date}"
    command = [go, "build", "-mod=readonly", "-buildvcs=false", "-trimpath", "-p=2", "-ldflags", flags,
               "-o", "../artifact/sage-gui", "./cmd/sage-gui"]
    with tempfile.TemporaryDirectory(prefix="lantern-sage-build-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        stage, artifact = temporary / "source", temporary / "artifact"
        artifact.mkdir()
        for name, data in original[0].items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
        if source_manifest(snapshot(stage, go, env)) != inventory:
            raise ValueError("staged dependency graph differs")
        run(command, stage, env, timeout=1800)
        binary = artifact / "sage-gui"
        verify_elf(binary)
        proof = run([go, "version", "-m", str(binary)], stage, env)
        for expected in ("GOOS=linux", "GOARCH=arm64", "CGO_ENABLED=0", "./cmd/sage-gui"):
            if expected not in proof:
                if expected == "./cmd/sage-gui" and "github.com/l33tdawg/sage/cmd/sage-gui" in proof:
                    continue
                raise ValueError("missing build metadata: " + expected)
        binary_bytes = binary.read_bytes()
        for value in (version, "source-" + inventory_hash[:16], date):
            if value.encode() not in binary_bytes:
                raise ValueError("missing linked version string")
        if source_manifest(snapshot(source, go, env)) != inventory or source_manifest(snapshot(stage, go, env)) != inventory:
            raise ValueError("source/dependency inputs changed during build")
        manifest = dict(inventory, schema="sage.lantern.binary-staging.v1", build_id=build_id,
                        version=version, source_date_epoch=epoch, inventory_sha256=inventory_hash,
                        go_version=toolchain["GOVERSION"], toolchain_sha256=tool_hashes,
                        build_command=["go", *command[1:]], build_environment={key: value for key, value in env.items() if key not in {"HOME", "PATH", "TMPDIR", "SYSTEMROOT", "TEST_TELEMETRY_DIR"}},
                        telemetry="isolated mode=off directory via TEST_TELEMETRY_DIR",
                        binary_sha256=digest(binary_bytes), architecture="ELF64 little-endian AArch64",
                        version_proof="linker flags recorded; version/source/date bytes present; target version command not executed",
                        promoted=False, target_executed=False)
        (artifact / "manifest.json").write_bytes(encoded(manifest))
        (artifact / "build-info.txt").write_text(proof.replace(str(binary), "sage-gui"))
        artifact.rename(output)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT.parent / "sage")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12])
    parser.add_argument("--source-date-epoch", type=int, default=int(datetime.now(timezone.utc).timestamp()))
    arguments = parser.parse_args()
    result = build(arguments.source, arguments.output, arguments.build_id, arguments.source_date_epoch)
    print(json.dumps({key: result[key] for key in ("version", "binary_sha256", "inventory_sha256")}, sort_keys=True))
