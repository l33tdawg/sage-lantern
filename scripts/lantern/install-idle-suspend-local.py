#!/usr/bin/env python3
"""Build a reviewable Unit A bundle; NEVER modify installed files or services.

Default output is a plan. Freeze that JSON, then --apply --manifest PLAN --stage
NEW_DIRECTORY to stage it as root on Linux/aarch64. --check validates a staged
bundle against the independently frozen plan. Neither option deploys/activates.
The parent deployer must atomically back up installed code/drop-ins, establish
containment, compare effective ExecStart arguments, install/fsync files, create
the service-owned drain directory, and independently check before any activation.
"""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import tempfile


PACKAGE = "/opt/sage-lantern/venv/lib/python3.11/site-packages/sage_lantern"
MODULES = ("device_access", "device_lock", "power_settings", "vault_setup", "idle_suspend",
           "suspend_capability", "suspend_io", "suspend_journal", "suspend_runtime",
           "suspend_system", "suspend_kiosk", "suspend_transition", "inference_guard", "adapters",
           "community_service", "community_messaging", "community_public", "canonical_messaging", "message_storage",
           "message_contacts", "message_drafts", "message_dictation")
HELPERS = {
    "unit-a-device-auth.py": "sage-lantern-device-auth",
    "unit-a-voice-loop.py": "sage-lantern-voice-loop",
    "lantern-display-power.py": "sage-lantern-display-power",
    "lantern-idle-suspend.py": "sage-lantern-idle-suspend",
}
SERVICES = ("device-auth", "voice-loop", "camera", "display-power", "fleet-admin", "llm")
MAX_FILE = 2 * 1024 * 1024
MAX_BUNDLE = 16 * 1024 * 1024
FENCE = "[Unit]\nConditionPathExists=!/run/sage-lantern-sleep.fenced\n"


def read_safe(path, *, owner=None):
    path = Path(path).absolute()
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("linked_parent")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_size > MAX_FILE or metadata.st_mode & 0o022
                or (owner is not None and (metadata.st_uid != owner
                    or stat.S_IMODE(metadata.st_mode) != 0o600))):
            raise ValueError("unsafe_file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError("file_limit")
        return data
    finally:
        os.close(descriptor)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def build_plan(source):
    source = Path(source).absolute()
    payloads = {}
    origins = {}
    missing = []

    def add(relative, destination, mode=0o644):
        try:
            payloads[destination] = read_safe(source / relative)
            origins[destination] = (relative, mode)
        except FileNotFoundError:
            missing.append(relative)

    for module in MODULES:
        add(f"software/runtime/src/sage_lantern/{module}.py", f"{PACKAGE}/{module}.py")
    for filename, installed in HELPERS.items():
        add(f"scripts/lantern/{filename}", f"/usr/local/libexec/{installed}", 0o755)
    add("software/systemd/sage-lantern-idle-suspend.service",
        "/etc/systemd/system/sage-lantern-idle-suspend.service")
    generated = {
        "/etc/tmpfiles.d/sage-lantern-suspend.conf":
            "d /run/sage-lantern-model 0700 root root -\n",
        "/etc/systemd/system/sage-lantern-llm.service.d/90-sleep-baseline.conf":
            "[Service]\nReadWritePaths=/run/sage-lantern-model\n"
            "ExecStartPost=+/opt/sage-lantern/venv/bin/python "
            "/usr/local/libexec/sage-lantern-idle-suspend --model-started\n",
    }
    for destination, text in generated.items():
        payloads[destination] = text.encode()
        origins[destination] = ("generated", 0o644)
    auth_unit = read_safe(source / "software/systemd/sage-lantern-device-auth.service").decode()
    starts = [line.removeprefix("ExecStart=") for line in auth_unit.splitlines() if line.startswith("ExecStart=")]
    if len(starts) != 1 or not starts[0].startswith("/opt/sage-lantern/venv/bin/python /usr/local/libexec/sage-lantern-device-auth "):
        raise ValueError("auth_execstart_unexpected")
    if "--drain-report-dir" in starts[0] or "\\" in starts[0] or "%" in starts[0]:
        raise ValueError("auth_execstart_ambiguous")
    for service in SERVICES:
        text = FENCE
        if service == "device-auth":
            text += ("[Service]\nExecStart=\nExecStart=" + starts[0]
                     + " --drain-report-dir /var/lib/sage-lantern/device-lock/drain\n")
        if service == "voice-loop":
            text += ("[Service]\nExecStart=\nExecStart=/usr/local/libexec/sage-lantern-voice-loop --photo-ready"
                     " --drain-request /run/sage-lantern-voice-drain-request.json\n"
                     "RuntimeDirectoryPreserve=yes\nTimeoutStopSec=30\n"
                     "Environment=SAGE_LANTERN_INFERENCE_GUARD=/var/lib/sage-lantern/inference\n"
                     "ReadWritePaths=/var/lib/sage-lantern/inference\n")
        destination = f"/etc/systemd/system/sage-lantern-{service}.service.d/90-sage-lantern-sleep.conf"
        payloads[destination] = text.encode()
        origins[destination] = ("generated", 0o644)
    runner = payloads.get("/usr/local/libexec/sage-lantern-idle-suspend")
    if runner is not None:
        values = [ast.literal_eval(node.value) for node in ast.parse(runner).body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "SLEEP_GUARD_TEXT" for target in node.targets)]
        expected = ("[Service]\nExecCondition=/opt/sage-lantern/venv/bin/python "
                    "/usr/local/libexec/sage-lantern-idle-suspend --sleep-guard\n")
        if values != [expected]:
            raise ValueError("sleep_guard_unexpected")
        destination = "/etc/systemd/system/systemd-suspend.service.d/90-sage-lantern.conf"
        payloads[destination] = expected.encode()
        origins[destination] = ("generated_from_SLEEP_GUARD_TEXT", 0o644)
    if sum(map(len, payloads.values())) > MAX_BUNDLE:
        raise ValueError("bundle_limit")
    files = [dict(destination=destination, source=origins[destination][0],
                  mode=origins[destination][1], owner="root:root", size=len(data),
                  sha256=hashlib.sha256(data).hexdigest(), artifact=f"payload/{index:03d}")
             for index, (destination, data) in enumerate(sorted(payloads.items()))]
    plan = dict(version=1, scope="UnitA_stage_only", activation=False, missing=sorted(missing), files=files,
                drain_directory=dict(path="/var/lib/sage-lantern/device-lock/drain", owner="sage-lantern:sage-lantern", mode=0o700),
                inference_directory=dict(path="/var/lib/sage-lantern/inference", owner="sage-lantern:sage-lantern", mode=0o700),
                model_directory=dict(path="/run/sage-lantern-model", owner="root:root", mode=0o700),
                required_prechecks=["Independent review of frozen manifest; hashes are not signatures",
                    "Unit A root Linux aarch64 Python 3.11 installed environment",
                    "Compare effective auth ExecStart with frozen source and voice ExecStart with approved --photo-ready",
                    "Validate installed dependency closure including power_settings; compile and import offline",
                    "Atomic root-only backups of every overwritten code file and drop-in before changes",
                    "Containment and durable writes before guarded services are released",
                    "Create drain directory service-owned 0700 without exposing private keys",
                    "Create inference directory service-owned 0700; directory existence does not prove readiness",
                    "Parent separately stops ALL model consumers before one controlled LLM restart to establish root model-instance baseline",
                    "Inference guard configuration is opt-in only, not a readiness declaration",
                    "Create model directory root-owned 0700 during installation; install tmpfiles rule for subsequent boots",
                    "Validate root --model-started hook and ExecStartPost activation semantics before coordinated LLM restart; start voice only after fresh baseline proof",
                    "Never automatically purge pending markers; unknown inference stays pending and deep sleep must remain disabled before fencing",
                    "Do not enable/start idle unit or create idle-suspend enabled marker",
                    "Parent separate native guard/drain/health checks before any activation"])
    return plan, payloads


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def stage_bundle(stage, plan, payloads, frozen, *, owner=0):
    if plan != frozen or plan["missing"]:
        raise ValueError("unfrozen_or_incomplete_bundle")
    stage = Path(stage).absolute()
    parent = stage.parent
    for directory in (parent, *parent.parents):
        metadata = directory.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in (0, owner)
                or (metadata.st_mode & 0o022 and not metadata.st_mode & stat.S_ISVTX)):
            raise ValueError("unsafe_stage_parent")
    if stage.exists() or stage.is_symlink():
        raise ValueError("stage_exists")
    temporary = Path(tempfile.mkdtemp(prefix=".idle-stage-", dir=parent))
    try:
        (temporary / "payload").mkdir(mode=0o700)
        for item in plan["files"]:
            data = payloads[item["destination"]]
            if hashlib.sha256(data).hexdigest() != item["sha256"] or len(data) != item["size"]:
                raise ValueError("payload_changed")
            write_private(temporary / item["artifact"], data)
        write_private(temporary / "manifest.json", encoded(plan))
        sync_dir(temporary / "payload")
        sync_dir(temporary)
        os.rename(temporary, stage)
        sync_dir(parent)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def check_bundle(stage, frozen, *, owner=0):
    stage = Path(stage).absolute()
    for directory in (stage, stage / "payload"):
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValueError("unsafe_stage")
    plan = strict_json(read_safe(stage / "manifest.json", owner=owner))
    if plan != frozen or plan.get("missing") or plan.get("activation") is not False or plan.get("scope") != "UnitA_stage_only":
        raise ValueError("manifest_mismatch")
    expected = set()
    if type(plan.get("files")) is not list or not 1 <= len(plan["files"]) <= 64:
        raise ValueError("file_count")
    total = 0
    for index, item in enumerate(plan["files"]):
        if item["artifact"] != f"payload/{index:03d}":
            raise ValueError("unsafe_artifact")
        data = read_safe(stage / item["artifact"], owner=owner)
        total += len(data)
        if total > MAX_BUNDLE:
            raise ValueError("bundle_limit")
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("hash_mismatch")
        expected.add(f"{index:03d}")
    if {entry.name for entry in stage.iterdir()} != {"payload", "manifest.json"} or {entry.name for entry in (stage / "payload").iterdir()} != expected:
        raise ValueError("unexpected_files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--stage", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Stage only; never deploy")
    mode.add_argument("--check", action="store_true", help="Check only; never activate")
    args = parser.parse_args()
    try:
        if args.apply or args.check:
            if os.geteuid() != 0 or platform.system() != "Linux" or platform.machine() != "aarch64":
                raise ValueError("root_linux_aarch64_required")
            if args.manifest is None or args.stage is None:
                raise ValueError("manifest_and_stage_required")
            frozen = strict_json(read_safe(args.manifest))
            if args.apply:
                plan, payloads = build_plan(args.source)
                stage_bundle(args.stage, plan, payloads, frozen)
            check_bundle(args.stage, frozen)
            print(json.dumps({"checked": True, "deployed": False, "activated": False}))
        else:
            plan, _ = build_plan(args.source)
            print(encoded(plan).decode(), end="")
        return 0
    except Exception:
        print('{"error":"bundle_validation_failed","activated":false}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
