#!/usr/bin/env python3
"""Nonmutating two-unit configuration planner; not hardware verification/install.

Reads only explicitly supplied prepared metadata and public radio inventories.
Peer comparison establishes uniqueness only within the supplied comparison set.
Hardware arguments are operator-supplied bindings, not live device evidence.
Voice-activation output maps a freshly validated plan to the installer's config;
the installer must still validate all identity/recovery evidence and hardware.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


class PlanError(ValueError):
    pass


def match(value, pattern, error):
    if type(value) is not str or not re.fullmatch(pattern, value):
        raise PlanError(error)
    return value


def safe_path(value, *, local_input=False):
    match(value, r"/[A-Za-z0-9_./:-]+", "unsafe_path")
    path = Path(value)
    if str(path) != value or ".." in path.parts or path == Path("/"):
        raise PlanError("unsafe_path")
    if local_input:
        for component in (path, *path.parents):
            if component.is_symlink():
                raise PlanError("symlink_path")
    return path


def read_public(value, filename):
    path = safe_path(value, local_input=True)
    if path.name != filename:
        raise PlanError("unexpected_input_filename")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16384:
            raise PlanError("invalid_public_file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(16385)
        if len(data) > 16384:
            raise PlanError("invalid_public_file")
        return data.decode("utf-8")
    finally:
        os.close(descriptor)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlanError("duplicate_field")
        result[key] = value
    return result


def radio_inventory(filename):
    value = json.loads(read_public(filename, "meshcore-radio-public.json"), object_pairs_hook=unique_object)
    if type(value) is not dict or value.get("schema") != "sage.lantern.meshcore-radio-public.v1":
        raise PlanError("invalid_radio_schema")
    unit = match(value.get("unit_id"), r"unit-[a-z]", "invalid_radio_unit")
    usb = value.get("usb")
    identity = value.get("radio_identity")
    if type(usb) is not dict or type(identity) is not dict:
        raise PlanError("missing_hardware_binding")
    serial = match(usb.get("serial"), r"[A-Za-z0-9:_-]{1,128}", "invalid_usb_serial")
    vendor = match(usb.get("vendor_id"), r"[0-9a-f]{4}", "invalid_usb_vendor")
    product = match(usb.get("product_id"), r"[0-9a-f]{4}", "invalid_usb_product")
    stable = str(safe_path(usb.get("stable_path")))
    if not stable.startswith("/dev/serial/by-id/") or len(Path(stable).parts) != 5:
        raise PlanError("invalid_radio_path")
    public_key = match(identity.get("public_key"), r"[0-9a-f]{64}", "invalid_public_identity")
    fingerprint = "sha256:" + hashlib.sha256(bytes.fromhex(public_key)).hexdigest()
    if fingerprint != identity.get("public_key_sha256"):
        raise PlanError("public_fingerprint_mismatch")
    return {"unit_id": unit, "usb_serial": serial, "vendor_id": vendor,
            "product_id": product, "stable_path": stable, "public_key": public_key,
            "public_fingerprint": fingerprint}


def build_plan(args):
    unit = match(args.unit_id, r"unit-[a-z]", "invalid_unit_id")
    account = match(args.account, r"[a-z][a-z0-9_-]{0,31}", "invalid_account")
    if account in {"root", "sage-lantern", "nobody"}:
        raise PlanError("reserved_operator_account")
    prepared = {}
    for line in read_public(args.prepared_metadata, "unit.env").splitlines():
        if not line or "=" not in line:
            raise PlanError("invalid_prepared_metadata")
        key, value = line.split("=", 1)
        if key in prepared:
            raise PlanError("duplicate_field")
        prepared[key] = value
    if prepared != {"UNIT_ID": unit, "PROVISIONING_SCHEMA": "sage-lantern-unit-v1"}:
        raise PlanError("prepared_unit_mismatch")
    repo = safe_path(args.repo_root or f"/home/{account}/sage-commons")
    if repo.parent != Path(f"/home/{account}"):
        raise PlanError("repo_outside_operator_home")
    app = safe_path(args.app_root or str(repo / "apps/lantern-fleet-admin"))
    evidence = safe_path(args.evidence_root or str(repo / f"evidence/{unit}-provisioning"))
    if app != repo / "apps/lantern-fleet-admin" or evidence != repo / f"evidence/{unit}-provisioning":
        raise PlanError("inconsistent_unit_paths")
    radio = radio_inventory(args.radio_inventory)
    if radio["unit_id"] != unit:
        raise PlanError("radio_unit_mismatch")
    expected = {"usb_serial": args.radio_usb_serial, "vendor_id": args.radio_vendor_id,
                "product_id": args.radio_product_id, "stable_path": args.radio_stable_path,
                "public_fingerprint": args.radio_public_fingerprint}
    if any(radio[key] != value for key, value in expected.items()):
        raise PlanError("radio_hardware_binding_mismatch")
    if len(args.peer_radio_inventory) > 16:
        raise PlanError("peer_inventory_limit")
    peers = [radio_inventory(path) for path in args.peer_radio_inventory]
    records = [radio, *peers]
    for field in ("unit_id", "usb_serial", "public_fingerprint", "stable_path"):
        if len({record[field] for record in records}) != len(records):
            raise PlanError("duplicate_radio_binding")
    audio_serial = match(args.audio_usb_serial, r"[A-Za-z0-9:_-]{1,128}", "invalid_audio_serial")
    audio_path = str(safe_path(args.audio_stable_path))
    if not audio_path.startswith("/dev/snd/by-id/") or len(Path(audio_path).parts) != 5:
        raise PlanError("invalid_audio_path")
    if audio_serial not in Path(audio_path).name:
        raise PlanError("audio_binding_mismatch")
    if unit != "unit-a" and "SAGE-LANTERN-A-AUDIO" in audio_path:
        raise PlanError("unit_a_audio_on_other_unit")
    audio = {"usb_serial": audio_serial, "stable_path": audio_path}
    if args.audio_vendor_id is not None or args.audio_product_id is not None:
        audio["vendor_id"] = match(args.audio_vendor_id, r"[0-9a-f]{4}", "invalid_audio_vendor")
        audio["product_id"] = match(args.audio_product_id, r"[0-9a-f]{4}", "invalid_audio_product")
    return {"schema": "sage.lantern.unit-plan.v1", "mode": "plan-only", "unit_id": unit,
            "hostname": f"sage-lantern-{unit[-1]}", "operator_account": account,
            "service_account": "sage-lantern", "repo_root": str(repo), "app_root": str(app),
            "evidence_root": str(evidence), "install_root": "/opt/sage-lantern",
            "state_root": "/var/lib/sage-lantern", "config_root": "/etc/sage-lantern",
            "radio": radio, "audio": audio,
            "compared_peer_units": sorted(peer["unit_id"] for peer in peers),
            "fleet_comparison_performed": bool(peers), "ownership_verified": False,
            "hardware_verified": False, "provisioned": False,
            "limitations": ["Operator bindings are not live hardware verification.",
                            "Uniqueness covers supplied public inventories only.",
                            "SAGE identity remains the security principal; radio binding needs authenticated enrollment.",
                            "Target paths are lexical plans; installer must reject target symlinks at use time.",
                            "No installer, account existence, recovery or security acceptance performed."]}


def voice_activation_config(args, plan):
    if plan["unit_id"] not in {"unit-a", "unit-b"} or plan["operator_account"] != "radxa":
        raise PlanError("voice_installer_target_unsupported")
    if not args.identity_inventory:
        raise PlanError("identity_inventory_required")
    inventory = {}
    for line in read_public(args.identity_inventory, "unit-inventory.env").splitlines():
        name, separator, value = line.partition("=")
        if not separator or name in inventory:
            raise PlanError("invalid_identity_inventory")
        inventory[name] = value
    if (inventory.get("UNIT_ID") != plan["unit_id"]
            or inventory.get("INVENTORY_SCHEMA") != "sage-lantern-identity-v1"
            or inventory.get("RADIO_IDENTITY_FINGERPRINT") != plan["radio"]["public_fingerprint"]):
        raise PlanError("identity_radio_binding_mismatch")
    evidence = safe_path(plan["evidence_root"])
    if args.activation_staging_root:
        evidence = safe_path(args.activation_staging_root) / evidence.relative_to("/")
    audio = plan["audio"]["stable_path"]
    match(audio, r"/dev/snd/by-id/usb-[A-Za-z0-9_-]+", "unsupported_voice_audio_path")
    if plan["unit_id"] == "unit-b" and "SAGE-LANTERN-A-AUDIO" in audio:
        raise PlanError("unit_a_audio_on_unit_b")
    return {"schema": "sage-lantern-voice-activation-v1", "unit_id": plan["unit_id"],
            "prepared_metadata": args.prepared_metadata, "inventory": args.identity_inventory,
            "stable_usb_audio": audio,
            "identity_evidence": str(evidence / "personal-public-identity.json"),
            "recovery_dir": str(evidence / "recovery-test")}


def audio_udev_rules(plan):
    audio = plan["audio"]
    vendor = match(audio.get("vendor_id"), r"[0-9a-f]{4}", "audio_vendor_required")
    product = match(audio.get("product_id"), r"[0-9a-f]{4}", "audio_product_required")
    serial = match(audio.get("usb_serial"), r"[A-Za-z0-9:_-]{1,128}", "invalid_audio_serial")
    return (f'SUBSYSTEM=="sound", KERNEL=="controlC*", ENV{{ID_VENDOR_ID}}=="{vendor}", '
            f'ENV{{ID_MODEL_ID}}=="{product}", ENV{{ID_SERIAL_SHORT}}=="{serial}", '
            'GROUP="sage-lantern", MODE="0660", SYMLINK+="sage-lantern-audio-control"')


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--unit-id", required=True)
    result.add_argument("--account", default="radxa")
    result.add_argument("--format", choices=("plan", "voice-activation", "audio-udev"), default="plan")
    result.add_argument("--audio-vendor-id", help="Observed normal-firmware USB vendor ID, four lowercase hex digits")
    result.add_argument("--audio-product-id", help="Observed normal-firmware USB product ID, four lowercase hex digits")
    result.add_argument("--identity-inventory", help="Public unit-inventory.env required for voice-activation output")
    result.add_argument("--activation-staging-root", help="Map evidence paths into a dry-run staging tree only")
    result.add_argument("--prepared-metadata", required=True)
    for name in ("repo-root", "app-root", "evidence-root"):
        result.add_argument("--" + name)
    result.add_argument("--radio-inventory", required=True)
    result.add_argument("--peer-radio-inventory", action="append", default=[])
    for name in ("radio-usb-serial", "radio-vendor-id", "radio-product-id",
                 "radio-stable-path", "radio-public-fingerprint", "audio-usb-serial", "audio-stable-path"):
        result.add_argument("--" + name, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        plan = build_plan(args)
        if args.format == "voice-activation":
            plan = voice_activation_config(args, plan)
        elif args.identity_inventory or args.activation_staging_root:
            raise PlanError("activation_options_require_voice_format")
        elif args.format == "audio-udev":
            plan = audio_udev_rules(plan)
    except (ValueError, OSError, TypeError, RecursionError):
        print("Unit plan rejected: invalid or inconsistent public inputs.", file=sys.stderr)
        return 2
    print(plan if args.format == "audio-udev" else json.dumps(plan, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
