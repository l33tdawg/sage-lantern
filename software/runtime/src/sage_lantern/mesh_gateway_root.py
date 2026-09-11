"""Operator-pinned local gateway role; no discovery, enrollment or trust defaults."""

import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
import threading

from .public_gateway_bridge import LocalPublicREST, PublicGatewayBridge
from .public_gateway_composition import RootPublicGateway, validate_gateway_config
from .public_mesh_protocol import canonical, public_key, require
from .public_mesh_provider import PublicMeshTiming


def shutdown_supported():
    from .suspend_system import _run, BUSCTL
    try:
        result = _run([BUSCTL, "--json=short", "get-property", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1/unit/sage_2dlantern_2dmesh_2druntime_2eservice",
            "org.freedesktop.systemd1.Service", "TimeoutStopUSec"], 3)
        value = json.loads(result.stdout)
        return (result.returncode == 0 and type(value) is dict and set(value) == {"type", "data"}
            and value["type"] == "t" and type(value["data"]) is list and len(value["data"]) == 1
            and type(value["data"][0]) is int and 65_000_000 <= value["data"][0] <= 120_000_000)
    except Exception:
        return False


def validate(value, private):
    require(type(value) is dict and set(value) == {"identity_file", "role", "backend_pin", "calibration"})
    require(type(value["identity_file"]) is str and re.fullmatch(
        r"/var/lib/sage-lantern/identity/[A-Za-z0-9_.-]+", value["identity_file"]) is not None
        and value["identity_file"] != private["identity_file"])
    role = value["role"]
    require(type(role) is dict and set(role) == {"schema", "role", "enabled", "endpoint", "chain_id", "domain",
        "gateway_key", "storage_actor", "storage_chain", "pins", "timing", "operation_seconds", "lease_seconds", "poll_interval"})
    require(role["schema"] == "sage.lantern.public-gateway-role.v1" and role["role"] == "public_gateway"
        and role["enabled"] is True and role["storage_actor"] == private["agent_id"]
        and role["storage_chain"] == private["chain_id"] and role["gateway_key"] != private["agent_id"]
        and role["endpoint"] != private["endpoint"] and role["chain_id"] != private["chain_id"])
    public_key(role["gateway_key"])
    require(type(role["endpoint"]) is str and re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", role["endpoint"]) is not None
        and int(role["endpoint"].rsplit(":", 1)[1]) <= 65535)
    for name in ("chain_id", "domain"):
        require(type(role[name]) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", role[name]) is not None)
    require(type(role["pins"]) is list and 1 <= len(role["pins"]) <= 32)
    for pin in role["pins"]:
        require(type(pin) is dict and set(pin) == {"radio_key", "author"})
        public_key(pin["radio_key"])
        public_key(pin["author"])
        require(pin["author"] != private["agent_id"])
    require(len({pin["radio_key"][:12] for pin in role["pins"]}) == len(role["pins"])
            and len({pin["author"] for pin in role["pins"]}) == len(role["pins"]))
    require(type(role["timing"]) is dict and set(role["timing"]) == {"frame_seconds", "gateway_seconds", "safety_seconds"})
    timing = PublicMeshTiming(**role["timing"])
    timing.validate()
    for name in ("operation_seconds", "lease_seconds", "poll_interval"):
        require(type(role[name]) in (int, float))
    require(timing.gateway_seconds + timing.frame_seconds + timing.safety_seconds < role["operation_seconds"] <= 50
        and role["operation_seconds"] + 5 <= role["lease_seconds"] <= 55
        and 0.01 <= role["poll_interval"] <= 0.2 and 2 * role["poll_interval"] <= timing.frame_seconds)
    pin = value["backend_pin"]
    require(type(pin) is dict and set(pin) == {"pid", "start_ticks", "boot_id", "listener_inode"})
    require(type(pin["pid"]) is int and 1 <= pin["pid"] <= 2**31 - 1)
    for field in ("start_ticks", "listener_inode"):
        require(type(pin[field]) is str and re.fullmatch(r"[1-9][0-9]{0,19}", pin[field]) is not None)
    require(type(pin["boot_id"]) is str and re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", pin["boot_id"]) is not None)
    calibration = value["calibration"]
    require(type(calibration) is dict and set(calibration) == {"schema", "radio_key", "role_digest", "issued_at", "expires_at"})
    require(calibration["schema"] == "sage.lantern.gateway-calibration.v1"
        and calibration["radio_key"] == private["radio"]["public_key"]
        and calibration["role_digest"] == hashlib.sha256(canonical(role)).hexdigest()
        and type(calibration["issued_at"]) is int and type(calibration["expires_at"]) is int
        and 0 < calibration["issued_at"] < calibration["expires_at"] <= 2**53 - 1)
    require(private["messaging_enabled"] is True and private["dispatch_enabled"] is True
        and private["receive_while_locked"] is False and private["dispatch_while_locked"] is False)


def backend_process_current(value, *, proc=Path("/proc")):
    """Match an operator-attested chain/endpoint to its exact live listener owner."""
    try:
        pin = value["backend_pin"]
        process = proc / str(pin["pid"])
        before = (process / "stat").read_text().rsplit(")", 1)[1].split()[19]
        if before != pin["start_ticks"] or (proc / "sys/kernel/random/boot_id").read_text().strip() != pin["boot_id"]:
            return False
        port = int(value["role"]["endpoint"].rsplit(":", 1)[1])
        def listener_matches():
            with (proc / "net/tcp").open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            require(len(data) <= 1024 * 1024)
            rows = [line.split() for line in data.decode("ascii").splitlines()[1:]]
            matches = [row for row in rows if len(row) >= 10 and row[1] == f"0100007F:{port:04X}" and row[3] == "0A"]
            return len(matches) == 1 and matches[0][9] == pin["listener_inode"]
        require(listener_matches())
        owned = None
        for index, entry in enumerate((process / "fd").iterdir()):
            require(index < 4096)
            if os.readlink(entry) == "socket:[" + pin["listener_inode"] + "]":
                owned = entry
        return (owned is not None and os.readlink(owned) == "socket:[" + pin["listener_inode"] + "]"
            and listener_matches() and (process / "stat").read_text().rsplit(")", 1)[1].split()[19] == before)
    except Exception:
        return False


def backend_current(value):
    from .gateway_evidence import current
    return current(value)


def verify_before_open(store, identity, settings, *, access_check):
    rows = store.scan(limit=50, max_records=4096, access_check=access_check)
    return verify_gateway_inventory(rows, store, identity, settings, access_check=access_check)


def verify_gateway_inventory(rows, store, identity, settings, *, access_check):
    from .community_public import PublicProposal
    from .public_gateway_bridge import SCHEMA as BRIDGE_SCHEMA, NAMESPACE as BRIDGE_NAMESPACE, receipt_data
    from .public_gateway_runtime import PublicGatewayRuntime, PublicGatewayPin, SCHEMA, SIGN_DOMAIN, NAMESPACE, SLOTS
    from .public_mesh_protocol import proposal_data
    role = settings["role"]
    binding = dict(gateway=role["gateway_key"], chain_id=role["chain_id"], domain=role["domain"],
        endpoint=role["endpoint"], storage_actor=role["storage_actor"], storage_chain=role["storage_chain"])
    pins = tuple(PublicGatewayPin(bytes.fromhex(pin["radio_key"]), pin["author"]) for pin in role["pins"])
    expected = dict(bridge=binding, pins=[dict(radio=pin.radio_key.hex(), author=pin.author)
        for pin in sorted(pins, key=lambda pin: pin.radio_key)])
    slots = {str(uuid.uuid5(NAMESPACE, canonical([binding, index]).decode())) for index in range(SLOTS)}
    class DataValidator:
        _wire = staticmethod(PublicGatewayRuntime._wire)
        _frames = staticmethod(PublicGatewayRuntime._frames)
        def __init__(self):
            self.pins = pins
    validator = DataValidator()
    require(identity.agent_id == role["gateway_key"] and store.storage.actor.agent_id == role["storage_actor"]
        and store.storage.actor.chain_id == role["storage_chain"])
    require(type(rows) is list and len(rows) <= 4096 and access_check() is True)
    for row in rows:
        if row["kind"] != "public_proposal":
            continue
        payload = row["payload"]
        body = payload.get("body", {})
        if str(body.get("schema", "")).startswith("sage.lantern.public-gateway-runtime.") or row["record_id"] in slots:
            require(set(payload) == {"body", "signature"} and type(payload["signature"]) is str
                and re.fullmatch(r"[0-9a-f]{128}", payload["signature"]) is not None)
            identity._signing_key.verify_key.verify(SIGN_DOMAIN + canonical(body), bytes.fromhex(payload["signature"]))
            require(type(body) is dict and set(body) == {"schema", "binding", "slot", "revision", "data"}
                and body["schema"] == SCHEMA and body["binding"] == expected and row["record_id"] in slots
                and body["slot"] == row["record_id"] and type(body["revision"]) is int and body["revision"] == row["revision"])
            PublicGatewayRuntime._validate(validator, body["data"])
            require(body["data"]["state"] not in {"forwarding", "dispatching"})
        elif payload.get("schema") == BRIDGE_SCHEMA:
            require(set(payload) == {"schema", "binding", "proposal", "state", "receipt", "dispatch_digest"}
                and payload["binding"] == binding and payload["state"] in {"prepared", "receipt"})
            proposal = PublicProposal(**payload["proposal"])
            require(proposal_data(proposal) == payload["proposal"] and proposal.chain_id == role["chain_id"]
                and proposal.domain == role["domain"] and proposal.author in {pin.author for pin in pins})
            identifier = str(uuid.uuid5(BRIDGE_NAMESPACE, canonical([role["storage_chain"], role["storage_actor"],
                role["chain_id"], proposal.author, proposal.operation_id]).decode()))
            require(identifier == row["record_id"])
            if payload["state"] == "prepared":
                require(payload["receipt"] is None and payload["dispatch_digest"] is None)
            else:
                public_key(payload["dispatch_digest"])
                receipt_data(payload["receipt"], proposal)
    require(access_check() is True)
    return True


class GatewayRoot:
    def __init__(self, resource, identity, *, backend_check=backend_current, clock=time.time, rest_factory=LocalPublicREST):
        self.resource, self.settings = resource, resource.config["public_gateway"]
        validate(self.settings, resource.config)
        self.role_settings = self.settings["role"]
        self._local = threading.local()
        require(identity.agent_id == self.role_settings["gateway_key"])
        self._backend_check, self._clock = backend_check, clock
        self.rest = rest_factory(self.role_settings["endpoint"])
        self.role = None
        try:
            require(self.evidence())
            runtime = resource._provider._runtime
            bridge = PublicGatewayBridge(runtime.store, self.rest, identity._signing_key,
                chain_id=self.role_settings["chain_id"], domain=self.role_settings["domain"],
                authors=frozenset(pin["author"] for pin in self.role_settings["pins"]), clock=lambda: int(self._clock()),
                access_check=lambda: resource.active() and self.unlocked() and self.evidence(),
                storage_check=resource.supervisor.resource_lifecycle_check,
                backend_check=lambda endpoint, chain: endpoint == self.role_settings["endpoint"]
                    and chain == self.role_settings["chain_id"] and self.evidence())
            validate_gateway_config(self.role_settings, bridge)
            self.role = RootPublicGateway(resource._provider, bridge, self.role_settings,
                config_check=lambda settings: settings == self.role_settings and self.evidence(),
                contact_check=lambda pin: self.evidence() and dict(radio_key=pin.radio_key.hex(), author=pin.author) in self.role_settings["pins"],
                timing_check=lambda settings: settings == self.role_settings and self.evidence(),
                lease_budget_check=lambda seconds: type(seconds) in (int, float) and 0 < seconds <= 55 and self.evidence(),
                lease_factory=self.acquire, unlocked_check=self.unlocked)
        except BaseException:
            self.rest.close()
            raise

    def evidence(self):
        calibration = self.settings["calibration"]
        return (self.resource.configured() and self.resource.config["public_gateway"] == self.settings
            and calibration["issued_at"] <= self._clock() < calibration["expires_at"]
            and self._backend_check(self.settings) is True)

    def start(self, guard):
        return self.role.start(access_check=guard)

    def unlocked(self):
        epoch = self.resource._epoch()
        return epoch is not None and epoch == getattr(self._local, "epoch", epoch)

    def acquire(self, consent):
        epoch = self.resource._epoch()
        require(epoch is not None and not hasattr(self._local, "epoch"))
        self._local.epoch = epoch
        def finished():
            self._local.__dict__.clear()
        try:
            lease = self.resource.gateway_lease(consent, self.role_settings["lease_seconds"], on_finish=finished)
            if not self.unlocked():
                lease.close()
                raise ValueError("gateway_epoch_changed")
            return lease
        except BaseException:
            finished()
            raise
