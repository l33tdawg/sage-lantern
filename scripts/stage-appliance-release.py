#!/usr/bin/env python3
"""Stage the canonical Lantern appliance profile; never install or activate it."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re


SPEC = importlib.util.spec_from_file_location("appliance_manifest", Path(__file__).with_name("appliance-release-manifest.py"))
MANIFEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANIFEST)
require = MANIFEST.require
APP = "apps/lantern-fleet-admin/"
APP_DEST = "/opt/sage-lantern/fleet-admin/"
SDK_ROOT = "sources/sage-sdk/"
BINARY = "software/bundle/linux-arm64/bin/sage-gui"
SAGE_MANIFEST = "release/sage-manifest.json"
LOCK = "software/stack.lock.json"
WHEELS = "software/bundle/python-wheels/"
EPOCH = 1788652800
RADIO_JOURNAL = "software/systemd/sage-lantern-mesh-runtime.service.d/25-radio-commission-journal.conf"
RADIO_PINS = {
    RADIO_JOURNAL: "c6e21f3ebf5af6620b799772ca99c0ee206e9b4c619c3cf370bf794d98eed045",
    "scripts/lantern/request-broker-contact.py": "103344d9abbd4104a58ce87b18ca74a59498b73e4e310ca90ae28a3432fcee85",
}
MAINTENANCE_PINS = {
    "scripts/lantern/complete-runtime-maintenance-audio.py": "2d44a31392dc4fc0e624ea928c6da7de5e8bb99848ecc4a81ae11f379f51cae9",
    "scripts/lantern/build-runtime-maintenance-env.py": "05b496ff69d4017faef914816028ecd9b61a10b421f9cfc0638937c1e325454f",
    "scripts/lantern/collect-fresh-vision-inputs.py": "caf8a4883dd7761e9eae260f9971cc9ad67d3fb579255988db911dca79c3d2f8",
    "scripts/lantern/prepare-runtime-maintenance.py": "d7b07dec5ea29a789a8c312aa6b3bb8e79ad0444d31ea64b7f64d2e776aa44c8",
    "scripts/lantern/preflight-fresh-native-update.py": "31734f5538fe9e363e0f75cc97d7af2d5560e914c55356ef4538124619691c2d",
    "scripts/lantern/update-fresh-runtime.py": "202f71b7ea8f213f3eaf922fb51b6f23a3c280872b48f5605c4418f8a284160e",
}
INSTALLERS = (
    "scripts/lantern/stage-appliance-update.py",
    "scripts/lantern/provision-fresh-appliance.py",
    "scripts/bootstrap-lantern-os.sh", "scripts/install-lantern-unit.sh",
    "scripts/provision-lantern-unit.sh", "scripts/lantern/install-fleet-appliance-local.py",
    "scripts/lantern/install-boot-theme.py", "scripts/lantern/install-idle-suspend-local.py",
    "scripts/lantern/install-platform-audio-capture-local.sh",
    "scripts/lantern/install-camera-local.sh", "scripts/lantern/install-display-power-local.sh",
    "scripts/lantern/install-vision-local.sh", "scripts/lantern/install-unit-a-private-access.sh",
    "scripts/lantern/install-fleet-admin-local.sh",
    "scripts/lantern/install-startup-coordinator.py",
    "scripts/lantern/provision-postcommission-startup.py",
    "scripts/lantern/install-fresh-startup-helpers.py",
    "scripts/lantern/install-npu-payload.py",
    "scripts/lantern/activate-fresh-display.py",
)
SUPPORT = (
    "scripts/appliance-release-manifest.py", "scripts/stage-appliance-release.py",
    "scripts/verify-runtime-release.py", "scripts/verify-lantern-bundle.py",
    "scripts/build-runtime-release.py", "scripts/build-sdk-release.py", "scripts/build-sage-release.py",
    "scripts/lantern/lantern-unit-config.py", "scripts/lantern/fleet-install-preflight.py",
    "scripts/lantern/assemble-protected-config.py",
    "scripts/lantern/prepare-mesh-runtime.py", "scripts/lantern/prepare-fresh-mesh-runtime.py",
    "scripts/lantern/inspect-fresh-sage-policy.py", "scripts/lantern/plan-fresh-vision.py",
    "scripts/lantern/lantern-network-setup.py", "scripts/capture-lantern-public-identity.py",
    "scripts/capture-lantern-evidence.sh",
    "scripts/lantern/benchmarks/observe_voice.py",
    "scripts/lantern/handoff-first-audio-lifetime.py",
    "scripts/lantern/handoff-fresh-audio-lifetime.py",
    "scripts/lantern/prepare-postcommission-release.py",
    "scripts/lantern/startup-coordinator.py", "scripts/lantern/startup-audio-adapter.py",
    "scripts/lantern/recover-cross-boot-audio.py", "scripts/lantern/collect-cross-boot-audio-pins.py",
    "software/systemd/sage-lantern-startup.service",
    "software/config/sage-personal.example.yaml",
    "software/config/sage-provisioning-profile.example.yaml",
    # The console lane (D-019) travels as reviewed support payload: the local
    # bundle builder/deployer/checker and the typed-question rehearsal are the
    # per-unit lane, and the activation runbook is the reviewed procedure that
    # applies the kiosk session settings to a live operator session (the tool
    # refuses an account with no running session, so it cannot be an image
    # provisioning phase).
    "scripts/lantern/build-console-bundle-local.py",
    "scripts/lantern/deploy-staged-console-bundle-local.py",
    "scripts/lantern/check-console-lane-local.py",
    "scripts/lantern/rehearse-typed-question-local.py",
    "scripts/lantern/configure-kiosk-session-settings.py",
    "docs/lantern/CF_CONSOLE_LANE_ACTIVATION.md",
    # The C-F sleep bundle pair (the A/B installer stays in INSTALLERS): the
    # D/E/F runbook stages and deploys each unit's sleep bundle with these.
    "scripts/lantern/install-cf-idle-suspend-local.py",
    "scripts/lantern/deploy-staged-idle-bundle-local.py",
    # Per-unit OS crash protection; explicit apply does not restart Lantern.
    "scripts/lantern/configure-crash-privacy.py",
    "docs/lantern/CRASH_PRIVACY.md",
    "scripts/lantern/configure-kiosk-privileges.py",
    "docs/lantern/KIOSK_OPERATOR_BOUNDARY.md",
)
HELPERS = {
    "wait-auth-native.py": "libexec/sage-lantern-wait-auth-native",
    "request-broker-contact.py": "libexec/sage-lantern-request-broker-contact",
    "unit-a-device-auth.py": "libexec/sage-lantern-device-auth",
    "unit-a-voice-loop.py": "libexec/sage-lantern-voice-loop",
    "unit-a-camera-server.py": "libexec/sage-lantern-camera-server",
    "unit-a-status-server.py": "libexec/sage-lantern-status-server",
    "unit-a-ptt-monitor.py": "libexec/sage-lantern-ptt-monitor",
    "unit-a-observation-status.py": "libexec/sage-lantern-observation-status",
    "verify-local-voice.py": "libexec/sage-lantern-verify-local-voice",
    "approve-local-voice.py": "libexec/sage-lantern-approve-local-voice",
    "activate-unit-a-local-voice.sh": "sbin/activate-unit-a-local-voice",
    "configure-kiosk-login.py": "libexec/sage-lantern-configure-kiosk-login",
    "configure-unit-a-hostname.py": "libexec/sage-lantern-configure-unit-a-hostname",
    "lantern-display-power.py": "libexec/sage-lantern-display-power",
    "lantern-display-session.py": "libexec/sage-lantern-display-session",
    "lantern-idle-suspend.py": "libexec/sage-lantern-idle-suspend",
    "set-platform-audio-capture.py": "libexec/sage-lantern-set-platform-audio-capture",
    "cf-mode-supervisor.py": "libexec/sage-lantern-mode-supervisor",
    "cf-output-mode.py": "libexec/sage-lantern-output-mode",
    "cf-desktop-mode.sh": "bin/sage-lantern-desktop-mode",
    "sage-lantern-kiosk": "bin/sage-lantern-kiosk",
    "sage-lantern-session": "bin/sage-lantern-session",
}
UNITS = tuple("sage-lantern-" + name + ".service" for name in (
    "audio-capture", "camera", "device-auth", "display-power", "idle-suspend", "llm", "llm-cpu", "vlm", "embed", "embed-shim", "personal-sage",
    "ptt", "runtime", "status", "voice-loop", "mesh-runtime", "gateway-evidence"))
MODELS = ("ggml-base.bin", "qwen3.5-4b-q4_k_m.gguf", "mmproj-BF16.gguf", "en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json", "nomic-embed-text-v1.5.Q8_0.gguf")
ARCHIVES = ("llama.cpp-b9637.tar.gz", "whisper.cpp-v1.8.6.tar.gz")
BASE_WHEEL_PREFIXES = ("numpy-1.26.4-", "piper_tts-1.4.2-", "pynacl-1.6.2-", "cryptography-46.0.3-",
                       "PyYAML-6.0.2-cp311-cp311-manylinux_2_17_aarch64.")

NPU_ROOT = "software/bundle/linux-arm64/npu/"
NPU_ARTIFACTS = {
    NPU_ROOT + "lib/librkllmrt.so": "/opt/sage-lantern/npu/lib/librkllmrt.so",
    NPU_ROOT + "lib/librknnrt.so": "/opt/sage-lantern/npu/lib/librknnrt.so",
    NPU_ROOT + "include/rkllm.h": None,
    NPU_ROOT + "include/rknn_api.h": None,
    NPU_ROOT + "include/rknn_matmul_api.h": None,
    NPU_ROOT + "include/rknn_custom_op.h": None,
    NPU_ROOT + "models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm":
        "/opt/sage-lantern/npu/models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm",
    NPU_ROOT + "models/qwen3.5-0.8b_vision_rk3588.rknn":
        "/opt/sage-lantern/npu/models/qwen3.5-0.8b_vision_rk3588.rknn",
    NPU_ROOT + "models/qwen3.5-0.8b_w8a8_rk3588.rkllm":
        "/opt/sage-lantern/npu/models/qwen3.5-0.8b_w8a8_rk3588.rkllm",
}
NPU_ARTIFACT_PINS = {
    NPU_ROOT + "lib/librkllmrt.so": "6a9e4fc5324c68921c3a900340361e107af7599fe34dc8fa7759b2c5ae22a6e6",
    NPU_ROOT + "lib/librknnrt.so": "d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8",
    NPU_ROOT + "include/rkllm.h": "80596a578f7f8e70df6eda1c2cbead3bfced14623a190258f2bd009a3d1f72cf",
    NPU_ROOT + "include/rknn_api.h": "340f16c14bc86d41bc5a64251bd1e747dd50899a4438d08707b48d8de384165a",
    NPU_ROOT + "include/rknn_matmul_api.h": "597524096b298e4220608cf684ed3209b6a7ff515b1b469679e5d813f2f1a6da",
    NPU_ROOT + "include/rknn_custom_op.h": "af5983da0ca244ca31dc3162aa683322b0285531196c7a770f29cd2e3b8ccaa6",
    NPU_ROOT + "models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm":
        "d3721fc8104b39aba7fc39c272806015a947074dbba7da00091a1747e73c1c3a",
    # Runtime-matched Qwen3.5-0.8B VL pair (rkllm-toolkit 1.3.0). The toolkit
    # generation must match the installed rkllm runtime (1.3.0) — a 1.2.3-era
    # conversion is rejected at runtime by design. See D-016.
    NPU_ROOT + "models/qwen3.5-0.8b_vision_rk3588.rknn":
        "e96a3f016163e7cf7ae516ed9878ccad7c576c658d71a60333f5cb154680fd81",
    NPU_ROOT + "models/qwen3.5-0.8b_w8a8_rk3588.rkllm":
        "ba85edac97e8f4ed2cdf0d94a3744b5609385b4706c45677f55cdaeb465ce054",
}
NPU_SOURCE_PINS = {
    "software/npu/rkllm_engine.cpp": "408eb358080dca83e533fb8e4b819c2792242e443fb0e6227ee3bf5e6a8e3cfc",
    "software/npu/rkllm_server.py": "674ae3d23f5527518a641ded9b1475e21aeb76bb4de2123b26000e779c251db0",
    "software/npu/vlm_engine.cpp": "e4912a4cb6849d8247ec983361709e96d88497393caeedff2942f62b4322c4a5",
    "software/npu/vlm_server.py": "9fdc82afa2014b629e428b3e72cc5114a8de15263f6d6140f0860b6b39712f58",
}


def mapping(path, role, destination=None, operation="review_only"):
    mode = None
    if destination is not None and operation in {"copy", "reviewed_copy", "template", "installer_transform"}:
        mode = "preserve_manifest" if role == "ui" else (0o755 if role in {"root_helper", "sage_binary"} else 0o644)
    return dict(path=path, role=role, destination=destination, operation=operation, destination_mode=mode)


def required_sources():
    rows = [mapping(name, "installer") for name in INSTALLERS]
    rows += [mapping("software/npu/rkllm_engine.cpp", "support", "/opt/sage-lantern/npu/bin/rkllm_engine", "offline_build"),
             mapping("software/npu/rkllm_server.py", "support", "/opt/sage-lantern/npu/libexec/rkllm_server.py", "copy"),
             mapping("software/npu/vlm_engine.cpp", "support", "/opt/sage-lantern/npu/bin/vlm_engine", "offline_build"),
             mapping("software/npu/vlm_server.py", "support", "/opt/sage-lantern/npu/libexec/vlm_server.py", "copy"),
             mapping("scripts/lantern/nomic-shim.py", "support", "/opt/sage-lantern/nomic-shim/nomic-shim.py", "copy")]
    rows += [mapping(name, "support") for name in SUPPORT]
    rows += [mapping(name, "support") for name in MAINTENANCE_PINS]
    static_names = ('startup-coordinator.py', 'startup-audio-adapter.py', 'recover-cross-boot-audio.py',
                    'collect-cross-boot-audio-pins.py', 'activate-fresh-display.py')
    rows = [mapping(row['path'], row['role'], '/opt/sage-lantern/libexec/' + Path(row['path']).name,
                    'reviewed_copy') if Path(row['path']).name in static_names else row for row in rows]
    rows += [mapping("scripts/lantern/" + name, "root_helper", "/usr/local/" + destination, "reviewed_copy")
             for name, destination in HELPERS.items()]
    rows += [mapping("scripts/smoke-test-lantern-unit.sh", "root_helper", "/usr/local/sbin/smoke-test-lantern-unit", "copy")]
    rows += [mapping("software/systemd/" + name, "systemd", "/etc/systemd/system/" + name, "reviewed_copy") for name in UNITS]
    rows += [mapping("software/systemd/sage-lantern-voice-loop.service.d/40-mesh-broker.conf", "dropin",
                     "/etc/systemd/system/sage-lantern-voice-loop.service.d/40-mesh-broker.conf", "reviewed_copy")]
    # The local question lane (D-019) travels in the payload for review but is
    # never installed by the app installer: it is an activation for a unit whose
    # mesh runtime is absent, applied by the console lane deployer, which refuses
    # a mesh-managed unit by name.
    rows += [mapping("software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf", "dropin",
                     "/etc/systemd/system/sage-lantern-voice-loop.service.d/60-local-question-lane.conf", "reviewed_copy")]
    rows += [mapping(RADIO_JOURNAL, "dropin", "/etc/systemd/system/sage-lantern-mesh-runtime.service.d/25-radio-commission-journal.conf", "reviewed_copy")]
    rows += [mapping("software/config/lantern.example.toml", "config_template", "/etc/sage-lantern/lantern.toml", "template")]
    rows += [mapping(APP + "deploy/" + name, "support") for name in (
        "sage-lantern-fleet-admin.service", "sage-lantern-device-agent.service",
        "sage-lantern-observation-status.service", "sage-lantern-observation-status.timer",
        "sage-lantern-kiosk.desktop", "sage-lantern.desktop")]
    rows += [mapping("software/plymouth/sage-lantern/" + name, "support", "/usr/share/plymouth/themes/sage-lantern/" + name, "reviewed_copy")
             for name in ("sage-lantern.plymouth", "sage-lantern.script")]
    rows.append(mapping("software/plymouth/sage-lantern/README.md", "support"))
    for row in rows:
        if row["path"] == "software/systemd/sage-lantern-device-auth.service":
            row["operation"] = "installer_transform"
    return rows


def module_names(root, prefix, package, extras):
    directory = root / prefix / "src" / package
    MANIFEST.no_links(directory)
    names = sorted(path for path in directory.iterdir() if path.name != "__pycache__")
    # The 18 October policy-wheel rebuild brought the runtime past its original
    # 128-module bound. Keep the same bounded inventory as the runtime builder.
    require(1 <= len(names) <= 160 and all(path.suffix == ".py" and path.is_file() for path in names), "module tree must contain only direct Python source files")
    require(directory / "__init__.py" in names, "missing package initializer")
    return [prefix + "/" + name for name in extras] + [path.relative_to(root).as_posix() for path in names]


def read_json(path):
    return MANIFEST.document(MANIFEST.metadata_bytes(path)[0])


def applicable(package):
    for field, target in (("os", "linux"), ("cpu", "arm64"), ("libc", "glibc")):
        choices = package.get(field, [])
        require(type(choices) is list and all(type(value) is str for value in choices), "invalid npm platform selector")
        positive = [value for value in choices if not value.startswith("!")]
        if "!" + target in choices or positive and target not in positive:
            return False
    return True


def dependency_closure(packages, expected):
    for name in ("", *sorted(expected)):
        metadata = packages[name]
        dependencies = dict(metadata.get("dependencies", {}))
        if not name:
            dependencies.update(metadata.get("devDependencies", {}))
        optional = metadata.get("optionalDependencies", {})
        dependencies.update(optional)
        for dependency in dependencies:
            require(re.fullmatch(r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+", dependency) is not None, "unsafe npm dependency name")
            parts = name.split("/") if name else []
            candidates = []
            while parts:
                if parts[-1] != "node_modules":
                    candidates.append("/".join(parts) + "/node_modules/" + dependency)
                parts.pop()
            candidates.append("node_modules/" + dependency)
            resolved = next((candidate for candidate in candidates if candidate in packages), None)
            require(resolved is not None, "dependency absent from lock graph")
            require(resolved in expected or dependency in optional and not applicable(packages[resolved]), "unavailable required npm dependency")


def dependency_inventory(packages, files):
    expected = {}
    for name, metadata in packages.items():
        if not name:
            continue
        MANIFEST.safe_name(name)
        require(re.fullmatch(r"(?:node_modules/(?:@[^/]+/)?[^/]+)(?:/node_modules/(?:@[^/]+/)?[^/]+)*", name) is not None,
                "unsupported npm workspace/link package")
        require(type(metadata) is dict and not metadata.get("link"), "linked npm package forbidden")
        ancestors = []
        parent = name
        while "/node_modules/" in parent:
            parent = parent.rsplit("/node_modules/", 1)[0]
            require(parent in packages, "nested npm parent absent from lock")
            ancestors.append(packages[parent])
        if applicable(metadata) and all(applicable(ancestor) for ancestor in ancestors):
            expected[name] = metadata
    observed = set()
    for name in files:
        if name.startswith("node_modules/"):
            match = re.match(r"^(node_modules/(?:@[^/]+/)?[^/]+(?:/node_modules/(?:@[^/]+/)?[^/]+)*)/", name)
            if match:
                observed.add(match.group(1))
            else:
                require(name == "node_modules/.package-lock.json", "unexpected npm root entry")
    require(observed == set(expected), "incomplete/extra Linux ARM64 UI dependency inventory")
    dependency_closure(packages, expected)
    return expected


def ui_inventory(root, source):
    before = MANIFEST.tree(root)
    files = before[0]
    require(all(name in {"package.json", "package-lock.json"} or name.startswith(("dist/", "public/", "node_modules/")) for name in files), "unexpected UI artifact entry")
    for name in ("dist/server/index.js", "node_modules/vinext/dist/cli.js", "package.json", "package-lock.json"):
        require(name in files, "missing runnable UI artifact: " + name)
    for name in ("package.json", "package-lock.json"):
        require(MANIFEST.file_hash(root / name)[0] == MANIFEST.file_hash(source / APP / name)[0], "UI source/lock mismatch")
    public_files = MANIFEST.tree(source / APP / "public")[0]
    require({name.removeprefix("public/") for name in files if name.startswith("public/")} == set(public_files), "incomplete UI public assets")
    for name in public_files:
        require(MANIFEST.file_hash(root / "public" / name)[0] == MANIFEST.file_hash(source / APP / "public" / name)[0], "UI public asset mismatch")
    package, lock = read_json(root / "package.json"), read_json(root / "package-lock.json")
    require(lock.get("lockfileVersion") == 3 and type(lock.get("packages")) is dict, "npm lockfile v3 required")
    packages = lock["packages"]
    require("" in packages and package.get("name") == "sage-lantern-fleet-admin", "unexpected UI package")
    for key in ("name", "version", "dependencies", "devDependencies", "optionalDependencies"):
        require(package.get(key) == packages[""].get(key), "UI root package/lock disagreement")
    expected = dependency_inventory(packages, files)
    for name, metadata in expected.items():
        installed = read_json(root / name / "package.json")
        require(type(metadata.get("version")) is str and installed.get("version") == metadata["version"], "installed npm version mismatch")
    require(MANIFEST.tree(root) == before, "UI inventory changed during validation")
    return sorted(files), {name: metadata["version"] for name, metadata in sorted(expected.items())}


def inputs(source, artifacts):
    for name, expected in NPU_SOURCE_PINS.items():
        require(MANIFEST.file_hash(source / name)[0]["sha256"] == expected, "unreviewed NPU source")
    for name, expected in MAINTENANCE_PINS.items():
        details, _ = MANIFEST.file_hash(source / name)
        require(details['sha256'] == expected, 'unreviewed maintenance companion')
    for name, expected in RADIO_PINS.items():
        details, _ = MANIFEST.file_hash(source / name)
        require(details['sha256'] == expected, 'unreviewed radio commissioning artifact')
    rows = required_sources()
    rows += [mapping(name, "runtime_source") for name in module_names(source, "software/runtime", "sage_lantern", ("pyproject.toml",))]
    result = {row["path"]: (source / row["path"], row) for row in rows}
    artifact_tree = MANIFEST.tree(artifacts)
    lock = read_json(artifacts / LOCK)
    require(lock.get("schema") == "sage.lantern.stack-lock.v1" and lock.get("target") == dict(os="linux", architecture="arm64", python="3.11"), "unexpected stack lock target")
    components = {key: lock["components"][name]["version"] for key, name in (("sage", "sage"), ("sdk", "sage-agent-sdk"), ("runtime", "lantern-runtime"))}
    locked = {}
    for entry in lock["files"]:
        name = MANIFEST.safe_name(entry["path"])
        require(name not in locked, "duplicate locked artifact")
        allowed = name in NPU_ARTIFACTS or name == BINARY or name in {"software/cache/models/" + value for value in MODELS}
        allowed |= name in {"software/cache/sources/" + value for value in ARCHIVES}
        allowed |= name.startswith(WHEELS) and "/" not in name.removeprefix(WHEELS) and name.endswith(".whl")
        require(allowed, "unselected legacy/extra stack artifact: " + name)
        details, _ = MANIFEST.file_hash(artifacts / name)
        require(details["sha256"] == entry["sha256"] and details["bytes"] == entry["bytes"], "locked artifact mismatch")
        if name in NPU_ARTIFACT_PINS:
            require(details["sha256"] == NPU_ARTIFACT_PINS[name], "unreviewed NPU artifact")
        locked[name] = entry
    mandatory = {BINARY, *NPU_ARTIFACTS} | {"software/cache/models/" + value for value in MODELS} | {"software/cache/sources/" + value for value in ARCHIVES}
    require(mandatory <= set(locked), "missing required base installer artifacts")
    for prefix in BASE_WHEEL_PREFIXES:
        require(sum(name.startswith(WHEELS + prefix) for name in locked) == 1, "missing/ambiguous required base wheel: " + prefix)
    sdk_wheel = WHEELS + "sage_agent_sdk-" + components["sdk"] + "-py3-none-any.whl"
    runtime_wheel = WHEELS + "sage_lantern_runtime-" + components["runtime"] + "-py3-none-any.whl"
    require({sdk_wheel, runtime_wheel} <= set(locked), "missing matched wheels")
    require(all(not name.startswith(WHEELS + "sage_agent_sdk-") or name == sdk_wheel for name in locked)
            and all(not name.startswith(WHEELS + "sage_lantern_runtime-") or name == runtime_wheel for name in locked), "ambiguous local wheels")
    sdk_files = module_names(artifacts, SDK_ROOT.rstrip("/"), "sage_sdk", ("pyproject.toml", "README.md"))
    ui_files, dependencies = ui_inventory(artifacts / APP, source)
    expected = set(locked) | {LOCK, SAGE_MANIFEST} | set(sdk_files) | {APP + name for name in ui_files}
    require(set(artifact_tree[0]) == expected, "extra/missing artifact root entries")
    directories = {"."} | {str(parent) for name in expected for parent in Path(name).parents}
    require(set(artifact_tree[1]) == directories, "unselected empty artifact directory")
    for name in sorted(expected):
        destination, operation = None, "evidence_only"
        role = "dependency"
        if name == BINARY:
            role, destination, operation = "sage_binary", "/opt/sage-lantern/bin/sage-gui", "copy"
        elif name == SAGE_MANIFEST:
            role = "sage_manifest"
        elif name in (sdk_wheel, runtime_wheel):
            role = "sdk_wheel" if name == sdk_wheel else "runtime_wheel"
            destination, operation = "/opt/sage-lantern/venv", "wheel_install"
        elif name.startswith(WHEELS):
            destination, operation = "/opt/sage-lantern/venv", "wheel_install"
        elif name in NPU_ARTIFACTS:
            destination = NPU_ARTIFACTS[name]
            operation = "copy" if destination else "evidence_only"
        elif name.startswith("software/cache/models/"):
            destination, operation = "/opt/sage-lantern/models/" + Path(name).name, "copy"
        elif name.startswith("software/cache/sources/"):
            destination, operation = ("/opt/sage-lantern/bin/whisper-cli" if "whisper" in name else "/opt/sage-lantern/llama.cpp/llama-server"), "offline_build"
        elif name.startswith(SDK_ROOT):
            role = "sdk_source"
        elif name.startswith(APP):
            role, destination, operation = "ui", APP_DEST + name.removeprefix(APP), "copy"
        else:
            role = "support"
        require(name not in result, "overlapping source/artifact selection")
        result[name] = (artifacts / name, mapping(name, role, destination, operation))
    return result, components, dependencies


def snapshot(selected):
    result = {}
    total = 0
    for name, (path, _) in sorted(selected.items()):
        result[name] = MANIFEST.file_hash(path)
        total += result[name][0]["bytes"]
        require(total <= MANIFEST.MAX_TOTAL and len(result) <= MANIFEST.MAX_FILES, "stage exceeds bounds")
    return result


def write_new(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.no_links(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def copy_verified(source, target, expected):
    details, observed = expected
    target.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.no_links(source)
    MANIFEST.no_links(target)
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        require(MANIFEST.identity(os.fstat(stream.fileno())) == observed, "source changed before copy")
        output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, details["mode"])
        with os.fdopen(output, "wb") as destination:
            os.fchmod(destination.fileno(), details["mode"])
            digest, size = hashlib.sha256(), 0
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                require(size <= details["bytes"], "source grew during copy")
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        require(size == details["bytes"] and digest.hexdigest() == details["sha256"]
                and MANIFEST.identity(os.fstat(stream.fileno())) == observed == MANIFEST.identity(source.lstat()), "source changed during copy")


def build(source, artifacts, output, release_id):
    source, artifacts, output = (Path(value).absolute() for value in (source, artifacts, output))
    for path in (source, artifacts, output):
        MANIFEST.no_links(path)
    require(all(left != right and left not in right.parents and right not in left.parents
                for left, right in ((source, artifacts), (source, output), (artifacts, output))), "roots must be separate non-nested directories")
    require(not output.exists() and output.parent.is_dir(), "output must be new with existing parent")
    selected, components, dependencies = inputs(source, artifacts)
    before = snapshot(selected)
    artifact_before = MANIFEST.tree(artifacts)
    public_before = MANIFEST.tree(source / APP / "public")
    selection = dict(schema=MANIFEST.SELECTION_SCHEMA, release_id=release_id, components=components,
                     files=[dict(path=name, role=row["role"]) for name, (_, row) in sorted(selected.items())])
    dropin = "release/dropins/sage-lantern-fleet-admin.service.d/90-device-lock.conf"
    selection["files"] += [dict(path=dropin, role="dropin"), dict(path="release/destinations.json", role="support")]
    MANIFEST.selection(selection)
    output.mkdir(mode=0o755)
    stage = output / "payload"
    stage.mkdir(mode=0o755)
    for name, (path, _) in sorted(selected.items()):
        copy_verified(path, stage / name, before[name])
    write_new(stage / dropin, b"[Service]\nEnvironment=LANTERN_DEVICE_LOCK=1\n")
    destinations = dict(schema="sage.lantern.appliance-destinations.v1", release_id=release_id,
                        deployment_ready=False, signed_authenticity=False, activation_authorized=False,
                        account="sage-lantern", ui_dependencies=dependencies,
                        generated_by_fresh_installer=["/etc/systemd/system/sage-lantern-fleet-admin.service",
                            "/etc/sage-lantern/appliance-bindings.json", "/etc/udev/rules.d/71-sage-lantern-audio.rules",
                            "/usr/local/share/sage-lantern/meshcore-radio-public.json"],
                        files=[row for _, row in sorted(selected.values(), key=lambda entry: entry[1]["path"])] +
                            [mapping(dropin, "dropin", "/etc/systemd/system/sage-lantern-fleet-admin.service.d/90-device-lock.conf", "review_only")])
    write_new(stage / "release/destinations.json", MANIFEST.canonical(destinations))
    write_new(output / "selection.json", MANIFEST.canonical(selection))
    for directory, _, names in os.walk(output):
        Path(directory).chmod(0o755)
        for name in names:
            os.utime(Path(directory) / name, (EPOCH, EPOCH), follow_symlinks=False)
    manifest = MANIFEST.build(stage, output / "selection.json", output / "manifest.pending.json")
    try:
        after, after_components, after_dependencies = inputs(source, artifacts)
        require(after == selected and after_components == components and after_dependencies == dependencies
                and snapshot(after) == before and MANIFEST.tree(artifacts) == artifact_before
                and MANIFEST.tree(source / APP / "public") == public_before, "source inventory changed during stage build")
    except BaseException:
        (output / "manifest.pending.json").unlink()
        raise
    write_new(output / "manifest.json", MANIFEST.metadata_bytes(output / "manifest.pending.json")[0])
    (output / "manifest.pending.json").unlink()
    for directory, _, names in os.walk(output, topdown=False):
        for name in names:
            os.utime(Path(directory) / name, (EPOCH, EPOCH), follow_symlinks=False)
        os.utime(directory, (EPOCH, EPOCH), follow_symlinks=False)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    result = build(args.source_root, args.artifact_root, args.output, args.release_id)
    print(json.dumps(dict(release_id=result["release_id"], files=len(result["files"]), deployment_ready=False, signed_authenticity=False)))
