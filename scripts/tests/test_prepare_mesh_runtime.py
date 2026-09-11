import builtins
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / "lantern/prepare-mesh-runtime.py"
SPEC = importlib.util.spec_from_file_location("prepare_mesh_runtime", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    root.chmod(0o700)
    original_directory = MODULE.directory

    def fixture_directory(path, owner):
        path = Path(path)
        if not path.is_relative_to(root):
            return original_directory(path, owner)
        for current in (path, *path.parents):
            info = current.lstat()
            MODULE.require(stat.S_ISDIR(info.st_mode) and info.st_uid == owner and not info.st_mode & 0o022)
            if current == root:
                break
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    monkeypatch.setattr(MODULE, "directory", fixture_directory)
    config = dict(schema="sage.lantern.mesh-runtime.v1", unit_id="unit-b", voice_uid=1001,
                  endpoint="http://127.0.0.1:8080", chain_id="unit-b-chain", agent_id="12" * 32,
                  identity_file="/var/lib/sage-lantern/identity/ordinary.key",
                  receiver_slot="12345678-1234-4234-8234-123456789abc",
                  radio=dict(stable_path="/dev/serial/by-id/VerifiedRadio", serial="verified",
                             vendor_id="1234", product_id="abcd", public_key="34" * 32),
                  peers=[], approve_initial_open=True, acknowledge_reset=True,
                  messaging_enabled=False, receive_while_locked=False,
                  dispatch_enabled=False, dispatch_while_locked=False)
    inventory = dict(INVENTORY_SCHEMA="sage-lantern-identity-v1", UNIT_ID="unit-b", PERSONAL_CHAIN_ID="unit-b-chain")
    for key in ("PERSONAL_GENESIS", "COMET_NODE", "PERSONAL_VALIDATOR", "PUBLIC_PROPOSER", "TLS_FEDERATION", "RECOVERY_EVIDENCE"):
        inventory[key + "_FINGERPRINT"] = "sha256:" + "ab" * 32
    inventory["VOICE_IDENTITY_FINGERPRINT"] = MODULE.fingerprint(config["agent_id"])
    inventory["RADIO_IDENTITY_FINGERPRINT"] = MODULE.fingerprint(config["radio"]["public_key"])
    for key in ("IDENTITY_UNIQUENESS", "RECOVERY_TEST", "NO_CLONED_SECRETS", "NO_PUBLIC_VALIDATOR_KEY"):
        inventory[key + "_ATTESTED"] = "PASS"
    raw = "".join(f"{key}={value}\n" for key, value in inventory.items()).encode()
    radio = dict(schema="sage.lantern.meshcore-radio-public.v1", unit_id="unit-b",
                 usb={key: value for key, value in config["radio"].items() if key != "public_key"},
                 radio_identity=dict(public_key=config["radio"]["public_key"],
                                     public_key_sha256=inventory["RADIO_IDENTITY_FINGERPRINT"]))
    contents = dict(manifest=MODULE.encoded(config), inventory=raw,
                    provisioned=f"UNIT_ID=unit-b\nINVENTORY_SHA256={hashlib.sha256(raw).hexdigest()}\n".encode(),
                    unit_metadata=b"UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n",
                    radio_inventory=MODULE.encoded(radio))
    args = dict(output_dir=root / "bundle", owner=os.geteuid())
    for name, content in contents.items():
        path = root / name
        path.write_bytes(content)
        path.chmod(0o600)
        args[name] = path
    return args, config


def test_success_modes_content_hashes_and_no_activation(prepared):
    args, config = prepared
    MODULE.prepare(**args)
    output = args["output_dir"]
    assert sorted(path.name for path in output.iterdir()) == ["20-radio.conf", "PREPARED.json", "mesh-runtime.json"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert json.loads((output / "mesh-runtime.json").read_bytes()) == config
    assert (output / "20-radio.conf").read_text() == (
        "[Service]\nDevicePolicy=closed\nDeviceAllow=\nDeviceAllow=" + config["radio"]["stable_path"] + " rw\n")
    receipt = json.loads((output / "PREPARED.json").read_bytes())
    for name, digest in receipt["artifacts"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
        assert stat.S_IMODE((output / name).stat().st_mode) == (0o644 if name.endswith("conf") else 0o600)


@pytest.mark.parametrize("field,value", [("voice_uid", True), ("receiver_slot", "00000000-0000-0000-0000-000000000000"),
    ("agent_id", "56" * 32), ("chain_id", "wrong"), ("unit_id", "unit-a"),
    ("receive_while_locked", True), ("endpoint", "http://example.com:8080"), ("unexpected", "secret")])
def test_manifest_binding_refused_before_output(prepared, field, value):
    args, config = prepared
    config[field] = value
    args["manifest"].write_bytes(MODULE.encoded(config))
    with pytest.raises((RuntimeError, ValueError)):
        MODULE.prepare(**args)
    assert not args["output_dir"].exists()


@pytest.mark.parametrize("name", ["manifest", "radio_inventory", "provisioned", "unit_metadata", "inventory"])
def test_duplicate_inputs_rejected(prepared, name):
    args, config = prepared
    path = args[name]
    raw = path.read_bytes()
    if name in ("manifest", "radio_inventory"):
        raw = raw.rstrip()[:-1] + b',"schema":"duplicate"}'
    else:
        raw += raw.splitlines()[0] + b"\n"
    path.write_bytes(raw)
    with pytest.raises((RuntimeError, ValueError)):
        MODULE.prepare(**args)
    assert not args["output_dir"].exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "mode", "oversize", "fifo"])
def test_unsafe_input_refused(prepared, kind):
    args, config = prepared
    path = args["manifest"]
    if kind == "symlink":
        saved = path.with_suffix(".saved")
        path.rename(saved)
        path.symlink_to(saved)
    elif kind == "hardlink":
        os.link(path, path.with_suffix(".link"))
    elif kind == "mode":
        path.chmod(0o666)
    elif kind == "oversize":
        path.write_bytes(b" " * 16385)
    else:
        path.unlink()
        os.mkfifo(path, 0o600)
    with pytest.raises((RuntimeError, OSError)):
        MODULE.prepare(**args)
    assert not args["output_dir"].exists()


@pytest.mark.parametrize("kind", ["empty", "uncertain", "dangling"])
def test_existing_target_never_changed(prepared, kind):
    args, config = prepared
    output = args["output_dir"]
    if kind == "dangling":
        output.symlink_to(output.parent / "absent")
    else:
        output.mkdir(mode=0o700)
        if kind == "uncertain":
            (output / "fence.json").write_bytes(b"unknown capture")
    with pytest.raises(FileExistsError):
        MODULE.prepare(**args)
    if kind == "uncertain":
        assert (output / "fence.json").read_bytes() == b"unknown capture"
    assert not (output / "PREPARED.json").exists()


def test_partial_failure_is_retained_and_cannot_retry(prepared, monkeypatch):
    args, config = prepared
    real_write = MODULE.write

    def fail_after_manifest(directory_fd, name, content, mode):
        if name == "20-radio.conf":
            raise OSError("synthetic failure")
        real_write(directory_fd, name, content, mode)

    monkeypatch.setattr(MODULE, "write", fail_after_manifest)
    with pytest.raises(OSError):
        MODULE.prepare(**args)
    assert (args["output_dir"] / "mesh-runtime.json").exists()
    assert not (args["output_dir"] / "PREPARED.json").exists()
    with pytest.raises(FileExistsError):
        MODULE.prepare(**args)


def test_output_claim_excludes_second_preparer(prepared):
    args, config = prepared
    MODULE.prepare(**args)
    before = {path.name: path.read_bytes() for path in args["output_dir"].iterdir()}
    with pytest.raises(FileExistsError):
        MODULE.prepare(**args)
    assert before == {path.name: path.read_bytes() for path in args["output_dir"].iterdir()}


@pytest.mark.parametrize("python_flags", [[], ["-I", "-S"]])
def test_cli_help_and_unknown_have_no_output(tmp_path, python_flags):
    for arguments, code in [(["--help"], 0), (["--dry-run"], 2), (["--serve"], 2)]:
        result = subprocess.run([sys.executable, *python_flags, str(SCRIPT), *arguments], cwd=tmp_path,
                                capture_output=True, timeout=10)
        assert result.returncode == code
    assert list(tmp_path.iterdir()) == []


def test_missing_runtime_dependency_denies_preparation(prepared, monkeypatch):
    args, config = prepared
    original_import = builtins.__import__

    def unavailable(name, *arguments, **kwargs):
        if name.startswith("sage_lantern"):
            raise ModuleNotFoundError("synthetic unavailable runtime")
        return original_import(name, *arguments, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(ModuleNotFoundError):
        MODULE.prepare(**args)
    assert not args["output_dir"].exists()


@pytest.mark.parametrize("change", ["inventory_hash", "attestation", "radio_key", "radio_usb", "radio_unit"])
def test_verified_inventory_mismatch(prepared, change):
    args, config = prepared
    if change == "inventory_hash":
        args["inventory"].write_bytes(args["inventory"].read_bytes().replace(b"ab", b"cd"))
    elif change == "attestation":
        raw = args["inventory"].read_bytes().replace(b"PASS", b"FAIL")
        args["inventory"].write_bytes(raw)
        args["provisioned"].write_bytes(f"UNIT_ID=unit-b\nINVENTORY_SHA256={hashlib.sha256(raw).hexdigest()}\n".encode())
    else:
        radio = json.loads(args["radio_inventory"].read_bytes())
        if change == "radio_key":
            radio["radio_identity"]["public_key"] = "99" * 32
        elif change == "radio_usb":
            radio["usb"]["serial"] = "different"
        else:
            radio["unit_id"] = "unit-a"
        args["radio_inventory"].write_bytes(MODULE.encoded(radio))
    with pytest.raises(RuntimeError):
        MODULE.prepare(**args)
    assert not args["output_dir"].exists()


def test_real_directory_rejects_symlink_and_writable_ancestors(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    for path in (link, tmp_path):
        with pytest.raises((OSError, RuntimeError)):
            MODULE.directory(path, 0)


def test_wrong_owner_refused_without_output(prepared):
    args, config = prepared
    args["owner"] = os.geteuid() + 1
    with pytest.raises(RuntimeError):
        MODULE.prepare(**args)
    assert not args["output_dir"].exists()


@pytest.mark.parametrize("matches", [False, True])
def test_public_actor_requires_inventory_binding(prepared, matches):
    args, config = prepared
    config["public_mesh"] = dict(identity_file="/var/lib/sage-lantern/identity/public.key", author="56" * 32,
        chain_id="commons", domain="public-domain", gateway_key="78" * 32, radio_key="9a" * 32,
        storage_actor=config["agent_id"], storage_chain=config["chain_id"],
        timing=dict(frame_seconds=0.5, gateway_seconds=30, safety_seconds=10))
    args["manifest"].write_bytes(MODULE.encoded(config))
    if matches:
        inventory = MODULE.environment(args["inventory"].read_bytes())
        inventory["PUBLIC_PROPOSER_FINGERPRINT"] = MODULE.fingerprint(config["public_mesh"]["author"])
        raw = "".join(f"{key}={value}\n" for key, value in inventory.items()).encode()
        args["inventory"].write_bytes(raw)
        args["provisioned"].write_bytes(f"UNIT_ID=unit-b\nINVENTORY_SHA256={hashlib.sha256(raw).hexdigest()}\n".encode())
        MODULE.prepare(**args)
        assert json.loads((args["output_dir"] / "mesh-runtime.json").read_bytes())["public_mesh"] == config["public_mesh"]
    else:
        with pytest.raises(RuntimeError):
            MODULE.prepare(**args)
        assert not args["output_dir"].exists()
