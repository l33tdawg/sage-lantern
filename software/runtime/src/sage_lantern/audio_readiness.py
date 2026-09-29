"""Explicit pre-start microphone test, with no retained or transmitted audio.

Generate/review/install --print-guard and --print-alsa before capture. --inspect prints public
input hashes for operator review; --capture requires their exact digest and
explicit microphone consent. --check is a short-lived capture-status consumer.
--admit instead checks durable commissioning plus current device/config binding;
normal boot, wake and process restart do not require another microphone test.
Historical capture is not current capture, PTT, ASR or intelligibility evidence.
"""

import argparse
import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time
import uuid

from .suspend_io import publish, read_json, unique_object
from .suspend_system import SuspendSystem


VOICE = "sage-lantern-voice-loop.service"
BROKER = "sage-lantern-mesh-runtime.service"
PYTHON = "/opt/sage-lantern/venv/bin/python"
GUARD_PATH = Path("/etc/systemd/system/" + VOICE + ".d/50-fresh-audio-validation.conf")
ALSA_PATH = Path("/etc/sage-lantern/audio-capture-alsa.conf")
ALSA_CONFIG = "pcm.hw {\n @args [ CARD DEV ]\n @args.CARD { type string }\n @args.DEV { type integer }\n type hw\n card $CARD\n device $DEV\n}\n"
GUARD = ("[Service]\nEnvironment=ALSA_CONFIG_PATH=" + str(ALSA_PATH)
    + "\nExecCondition=+" + PYTHON + " -m sage_lantern.audio_readiness --admit\n")
RECEIPT = Path("/etc/sage-lantern/audio-capture.json")
COMMISSION = Path("/etc/sage-lantern/audio-commissioned.json")
MARKER = Path("/var/lib/sage-lantern/LOCAL_VOICE_READY")
PENDING = Path("/run/sage-lantern-audio-validation.pending")
BINDINGS = Path("/etc/sage-lantern/appliance-bindings.json")
TTL = 600
BYTE_COUNT = 16000 * 2 * 2 * 2


def require(value):
    if value is not True:
        raise RuntimeError("fresh_audio_unverified")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def trusted_bytes(path):
    path = Path(path)
    requested = path
    recorder = path == Path("/usr/bin/arecord")
    if recorder and path.is_symlink():
        require(path.lstat().st_uid == 0 and os.readlink(path) in ("aplay", "/usr/bin/aplay"))
        path = Path("/usr/bin/aplay")
    require(path.resolve() == path)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
            and before.st_nlink in ((1, 2) if recorder else (1,))
            and not before.st_mode & 0o022 and before.st_size <= 2 * 1024 * 1024)
        raw = os.read(descriptor, before.st_size + 1)
        require(len(raw) == before.st_size)
        for after in (os.fstat(descriptor), path.lstat()):
            require(all(getattr(before, field) == getattr(after, field) for field in
                ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")))
        require(requested.resolve() == path)
        return raw
    finally:
        os.close(descriptor)


def card_nodes(audio):
    """Resolve a reviewed capture binding to its control and capture nodes."""
    match = re.fullmatch(r"hw:CARD=([A-Za-z0-9_]{1,32}),DEV=([0-9]{1,2})", audio["alsa_device"])
    require(match is not None)
    stable = Path(audio["stable_path"])
    require(stable.is_symlink())
    target = stable.resolve(strict=True)
    card = re.fullmatch(r"controlC([0-9]{1,3})", target.name)
    require(target.parent == Path("/dev/snd") and card is not None)
    number = card[1]
    require(Path("/proc/asound/card" + number + "/id").read_text().strip() == match[1])
    capture = Path("/dev/snd/pcmC" + number + "D" + match[2] + "c")
    return match, target, capture


def node_identity(node):
    info = node.lstat()
    require(stat.S_ISCHR(info.st_mode))
    device = Path(f"/sys/dev/char/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}/device").resolve(strict=True)
    require(Path("/sys/devices") in device.parents)
    return info, device


def hardware(audio):
    require(type(audio) is dict)
    if audio.get("source") == "platform":
        return platform_hardware(audio)
    return usb_hardware(audio)


def usb_hardware(audio):
    require(all(type(audio.get(key)) is str for key in
        ("stable_path", "usb_serial", "vendor_id", "product_id", "alsa_device")))
    require(re.fullmatch(r"/dev/snd/by-id/[A-Za-z0-9_.:+-]+", audio["stable_path"]) is not None)
    match, target, capture = card_nodes(audio)
    nodes = {}
    usb_parent = None
    for node in (target, capture):
        info, device = node_identity(node)
        usb = next((parent for parent in (device, *device.parents) if (parent / "idVendor").is_file()), None)
        require(usb is not None)
        require(tuple((usb / name).read_text().strip() for name in ("idVendor", "idProduct", "serial"))
            == (audio["vendor_id"], audio["product_id"], audio["usb_serial"]))
        require(usb_parent is None or usb_parent == str(usb))
        usb_parent = str(usb)
        nodes[str(node)] = dict(device=info.st_dev, inode=info.st_ino, rdev=info.st_rdev)
    return dict(card_id=match[1], usb_path=usb_parent, nodes=nodes)


def platform_hardware(audio):
    """Platform-codec capture (C–F): the card must sit on the reviewed platform device."""
    require(all(type(audio.get(key)) is str for key in
        ("stable_path", "id_path", "platform_device", "alsa_device")))
    require(audio["id_path"] == "platform-" + audio["platform_device"])
    require(audio["stable_path"] == "/dev/snd/by-path/" + audio["id_path"])
    match, target, capture = card_nodes(audio)
    platform = (Path("/sys/devices/platform") / audio["platform_device"]).resolve(strict=True)
    require(platform.parent == Path("/sys/devices/platform"))
    nodes = {}
    for node in (target, capture):
        info, device = node_identity(node)
        require(platform == device or platform in device.parents)
        nodes[str(node)] = dict(device=info.st_dev, inode=info.st_ino, rdev=info.st_rdev)
    return dict(card_id=match[1], platform_path=str(platform), nodes=nodes)


def property_value(system, name):
    unit = "".join(character if character.isalnum() else f"_{ord(character):02x}" for character in VOICE)
    raw = system._execute(["/usr/bin/busctl", "--system", "--json=short", "get-property",
        "org.freedesktop.systemd1", "/org/freedesktop/systemd1/unit/" + unit,
        "org.freedesktop.systemd1.Service", name], system.clock() + 5)
    value = json.loads(raw, object_pairs_hook=unique_object)
    require(type(value) is dict and set(value) == {"type", "data"}
        and value["type"] == "a(sasbttttuii)" and type(value["data"]) is list)
    return value["data"]


def snapshot():
    binding = json.loads(trusted_bytes(BINDINGS), object_pairs_hook=unique_object)
    require(binding.get("schema") == "sage.lantern.unit-plan.v1" and binding.get("unit_id") in {"unit-a", "unit-b", "unit-c", "unit-d", "unit-e", "unit-f"})
    audio = binding["audio"]
    observed = hardware(audio)
    system = SuspendSystem()
    value = system._show(VOICE, system.clock() + 5, ("FragmentPath", "DropInPaths", "NeedDaemonReload", "User", "Group", "Restart", "Environment"))
    require(value["FragmentPath"] == "/etc/systemd/system/" + VOICE and value["NeedDaemonReload"] == "no"
        and value["User"] == value["Group"] == "sage-lantern" and value["Restart"] == "no")
    dropins = value["DropInPaths"].split()
    require(str(GUARD_PATH) in dropins and len(dropins) == len(set(dropins)) and len(dropins) <= 16)
    require(trusted_bytes(GUARD_PATH) == GUARD.encode())
    require(trusted_bytes(ALSA_PATH) == ALSA_CONFIG.encode())
    environment = [entry for entry in shlex.split(value["Environment"]) if entry.startswith("ALSA_CONFIG_PATH=")]
    require(environment == ["ALSA_CONFIG_PATH=" + str(ALSA_PATH)])
    conditions = property_value(system, "ExecCondition")
    require(type(conditions) is list and len(conditions) == 1 and len(conditions[0]) == 10
        and conditions[0][:3] == [PYTHON, [PYTHON, "-m", "sage_lantern.audio_readiness", "--admit"], False])
    starts = property_value(system, "ExecStart")
    argv = ["/usr/local/libexec/sage-lantern-voice-loop", "--config", "/etc/sage-lantern/lantern.toml",
        "--alsa-device", audio["alsa_device"], "--drain-request", "/run/sage-lantern-mesh-control/voice-drain-request.json"]
    require(type(starts) is list and len(starts) == 1 and len(starts[0]) == 10 and starts[0][0] == argv[0]
        and starts[0][1] in (argv, argv + ["--photo-ready"]) and starts[0][2] is False)
    files = {str(BINDINGS), str(ALSA_PATH), "/etc/sage-lantern/lantern.toml", "/usr/bin/arecord", argv[0], value["FragmentPath"], *dropins}
    package = Path(__file__).resolve().parent
    sources = list(package.glob("*.py"))
    require(1 <= len(sources) <= 512)
    files.update(str(path) for path in sources)
    hashes = {path: hashlib.sha256(trusted_bytes(path)).hexdigest() for path in sorted(files)}
    services = {}
    for service in ("sage-lantern-llm.service", "sage-lantern-personal-sage.service"):
        services[service] = commissioning_service(system, service)
    return dict(unit_id=binding["unit_id"], audio=audio, hardware=observed, inputs=hashes, services=services,
        boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())


def commissioning_service(system, service):
    fields = ("LoadState", "ActiveState", "MainPID", "InvocationID", "Job")
    identity = system._show(service, system.clock() + 5, fields)
    require(identity["LoadState"] == "loaded" and system._no_job(identity["Job"]))
    if service == "sage-lantern-llm.service" and identity["ActiveState"] == "inactive":
        require(identity["MainPID"] == "0")
        inactive(system, service)
    else:
        require(identity["ActiveState"] == "active"
            and re.fullmatch(r"[1-9][0-9]*", identity["MainPID"]) is not None
            and re.fullmatch(r"[0-9a-f]{32}", identity["InvocationID"]) is not None)
    require(system._show(service, system.clock() + 5, fields) == identity)
    return identity


def samples(raw):
    require(type(raw) is bytes and len(raw) == BYTE_COUNT)
    values = array.array("h", raw)
    if sys.byteorder != "little":
        values.byteswap()
    rms = math.isqrt(sum(value * value for value in values) // len(values))
    clipped = sum(abs(value) >= 32760 for value in values)
    require(rms >= 20 and clipped * 100 < len(values) and min(values) != max(values))
    return dict(frames=32000, channels=2, rate=16000, sample_format="S16_LE", rms=rms, clipped=clipped)


def boottime():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def commissioned_binding(observed):
    return {name: observed[name] for name in ("unit_id", "audio", "inputs")}


def capture_document(value):
    require(type(value) is dict and set(value) == {"schema", "snapshot", "captured_at", "expires_at", "capture"}
        and value["schema"] == "sage.lantern.audio-capture.v1")
    require(type(value["captured_at"]) in (int, float) and type(value["expires_at"]) in (int, float)
        and math.isfinite(value["captured_at"]) and value["captured_at"] >= 0
        and value["expires_at"] == value["captured_at"] + TTL)
    capture = value["capture"]
    require(type(capture) is dict and set(capture) == {"frames", "channels", "rate", "sample_format", "rms", "clipped"}
        and all(type(capture[key]) is int for key in ("frames", "channels", "rate", "rms", "clipped"))
        and capture["frames"] == 32000 and capture["channels"] == 2 and capture["rate"] == 16000
        and capture["sample_format"] == "S16_LE" and 20 <= capture["rms"] <= 32768
        and 0 <= capture["clipped"] * 100 < 64000)


def commissioned_receipt():
    require(not os.path.lexists(PENDING))
    value = read_json(COMMISSION, owner_uid=0, mode=0o644, limit=131072)
    require(type(value) is dict and set(value) == {"schema", "commissioning_id", "binding", "capture_evidence_sha256"}
        and value["schema"] == "sage.lantern.audio-commissioned.v1"
        and type(value["commissioning_id"]) is str and str(uuid.UUID(value["commissioning_id"])) == value["commissioning_id"])
    historical = read_json(RECEIPT, owner_uid=0, mode=0o644, limit=131072)
    capture_document(historical)
    require(digest(historical) == value["capture_evidence_sha256"]
        and commissioned_binding(historical["snapshot"]) == value["binding"])
    require(read_json(MARKER, owner_uid=0, mode=0o644) ==
        dict(schema="sage.lantern.fresh-voice-gate.v2", commission_sha256=digest(value)))
    return value


def admission_verified(*, commissioning=False):
    """Current audio admission, not a claim of recent capture or broker/storage readiness."""
    try:
        value = commissioned_receipt()
        observed = snapshot()
        require(commissioned_binding(observed) == value["binding"] and not os.path.lexists(PENDING))
        require(commissioning is True or observed["services"]["sage-lantern-llm.service"]["ActiveState"] == "active")
        return True
    except Exception:
        return False


def _readiness_diagnostic(started, stage, attempts, listener_ack, *, outcome, busy=None):
    try:
        value = dict(event="audio_operational_probe", stage=stage, outcome=outcome,
                     attempts=attempts, elapsed_ms=max(0, min(86400000, int((time.monotonic() - started) * 1000))),
                     busy=busy, **listener_ack.diagnostic())
        print(json.dumps(value, sort_keys=True, separators=(",", ":")), file=sys.stderr, flush=True)
    except Exception:
        pass


def operational_broker_request(supervisor, request, *, peer_uid, listener_ack, busy_retries=0):
    """Root-dispatched read-only probe; diagnostics never grant capture access.

    Trusted caller may opt into at most two contention-only retries. Default is
    diagnostic-only, with no retries. No request field selects this behavior.
    All IO stays outside supervisor locks; every retry repeats live checks under
    the original four-second deadline and retains an already observed lifetime.
    """
    from .storage_supervisor import RootStorageSupervisor
    from .voice_listener_ack import ListenerAcknowledgements, ListenerCheckBusy
    require(type(listener_ack) is ListenerAcknowledgements)
    require(type(supervisor) is RootStorageSupervisor and type(peer_uid) is int
        and peer_uid in (0, supervisor._backend.uid))
    require(type(request) is dict and set(request) == {"op", "nonce"}
        and request["op"] == "verify-audio-operational" and type(request["nonce"]) is str
        and str(uuid.UUID(request["nonce"])) == request["nonce"])
    require(type(busy_retries) is int and 0 <= busy_retries <= 2)
    started = time.monotonic()
    deadline = started + 4
    anchor = None

    def retained():
        require(time.monotonic() < deadline)
        if not supervisor._control.acquire(timeout=0.05):
            raise ListenerCheckBusy("control")
        try:
            if not supervisor._mutex.acquire(timeout=0.05):
                raise ListenerCheckBusy("state")
            try:
                supervisor._held()
                require(supervisor.phase == "active" and not supervisor._shutdown_requested
                    and supervisor._proof is None and supervisor._resource is not None
                    and not supervisor._resource_joined)
                return (supervisor.broker_id, supervisor._baseline, supervisor._vault,
                        supervisor._producer, id(supervisor._resource))
            finally:
                supervisor._mutex.release()
        finally:
            supervisor._control.release()

    for attempt in range(busy_retries + 1):
        stage = "admission_before"
        try:
            require(time.monotonic() < deadline and admission_verified())
            stage = "retained_before"
            before = retained()
            if anchor is None:
                anchor = before
            require(before == anchor)
            for suffix in ("before", "after"):
                if suffix == "after":
                    stage = "admission_after"
                    require(time.monotonic() < deadline and admission_verified())
                stage = "storage_epoch_" + suffix
                require(time.monotonic() < deadline and supervisor._epoch() == before[1])
                stage = "vault_identity_" + suffix
                require(time.monotonic() < deadline and supervisor._backend.vault_identity() == before[2])
                stage = "producer_" + suffix
                require(time.monotonic() < deadline and supervisor._backend.producer() == before[3])
                stage = "listener_" + suffix
                require(time.monotonic() < deadline and listener_ack.verified(supervisor,
                    pid=before[3].pid, uid=before[3].uid, propagate_busy=True) is True)
            stage = "retained_after"
            require(retained() == before and time.monotonic() < deadline)
            if attempt:
                _readiness_diagnostic(started, "complete", attempt + 1, listener_ack, outcome="verified_after_retry")
            return {"result": {"nonce": request["nonce"], "ready": True}}
        except ListenerCheckBusy as error:
            if attempt < busy_retries and deadline - time.monotonic() > 0.01:
                time.sleep(0.01)
                continue
            _readiness_diagnostic(started, stage, attempt + 1, listener_ack, outcome="unavailable", busy=error.lock_name)
            raise
        except Exception:
            _readiness_diagnostic(started, stage, attempt + 1, listener_ack, outcome="unavailable")
            raise


def operational_ready():
    """Unprivileged status API: unavailable/legacy broker means false, not inference."""
    from .mesh_voice_ipc import connect, receive, send
    connection = None
    try:
        nonce = str(uuid.uuid4())
        connection = connect()
        connection.settimeout(5)
        send(connection, {"op": "verify-audio-operational", "nonce": nonce})
        response = receive(connection, 256)
        require(type(response) is dict and set(response) == {"result"})
        value = response["result"]
        require(type(value) is dict and set(value) == {"nonce", "ready"}
            and value["nonce"] == nonce and value["ready"] is True)
        return True
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def capture_verified():
    try:
        commissioned_receipt()
        value = read_json(RECEIPT, owner_uid=0, mode=0o644, limit=131072)
        capture_document(value)
        require(type(value["captured_at"]) in (int, float) and type(value["expires_at"]) in (int, float)
            and 0 <= value["captured_at"] <= boottime() < value["expires_at"] == value["captured_at"] + TTL)
        require(snapshot() == value["snapshot"])
        require(not os.path.lexists(PENDING) and boottime() < value["expires_at"])
        return True
    except Exception:
        return False


def inactive(system, service):
    deadline = system.clock() + 5
    value = system._show(service, deadline, ("ActiveState", "MainPID", "Job", "ControlGroup", "Slice"))
    require(value["ActiveState"] == "inactive" and value["MainPID"] == "0"
        and system._no_job(value["Job"]) and value["Slice"] == "system.slice")
    group = system._cgroup("/system.slice/" + service)
    require(not value["ControlGroup"] or system._cgroup(value["ControlGroup"]) == group)
    system._empty_tree(group, deadline)


def record_audio(observed):
    system = SuspendSystem()
    unit = "sage-lantern-audio-check-" + uuid.uuid4().hex + ".service"
    properties = ["User=sage-lantern", "Group=sage-lantern", "SupplementaryGroups=audio",
        "Type=exec", "RuntimeMaxSec=6", "TimeoutStopSec=2", "KillMode=control-group", "SendSIGKILL=yes",
        "PrivateNetwork=yes", "RestrictAddressFamilies=AF_UNIX", "NoNewPrivileges=yes",
        "ProtectSystem=strict", "ProtectHome=yes", "DevicePolicy=closed", "MemoryMax=64M"]
    properties += ["DeviceAllow=" + path + " rw" for path in observed["hardware"]["nodes"]]
    command = ["/usr/bin/systemd-run", "--quiet", "--wait", "--pipe", "--collect", "--unit=" + unit,
        "--setenv=ALSA_CONFIG_PATH=" + str(ALSA_PATH)]
    command += ["--property=" + value for value in properties]
    command += ["--", "/usr/bin/arecord", "-q", "-D", observed["audio"]["alsa_device"],
        "-f", "S16_LE", "-r", "16000", "-c", "2", "-t", "raw", "-d", "2", "-"]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin", "LANG": "C"}, start_new_session=True)
    raw = bytearray()
    deadline = time.monotonic() + 10
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                require(remaining > 0 and bool(selector.select(remaining)))
                chunk = os.read(process.stdout.fileno(), min(8192, BYTE_COUNT + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                require(len(raw) <= BYTE_COUNT)
        require(process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0)
        return samples(bytes(raw))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        process.stdout.close()
        state = system._show(unit, system.clock() + 5, ("LoadState", "ActiveState", "MainPID", "Job"))
        if state["LoadState"] != "not-found":
            system._command(["stop", unit], system.clock() + 5)
            state = system._show(unit, system.clock() + 5, tuple(state))
        require(state["ActiveState"] == "inactive" and state["MainPID"] == "0" and system._no_job(state["Job"]))
        system._empty_tree(system._cgroup("/system.slice/" + unit), system.clock() + 5)
        raw.clear()


def claim_pending(expected):
    descriptor = os.open(PENDING, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(expected)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(PENDING.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_capture(expected):
    require(os.geteuid() == 0 and sys.platform == "linux")
    observed = snapshot()
    require(digest(observed) == expected)
    system = SuspendSystem()
    for service in (VOICE, BROKER):
        inactive(system, service)
    require(system.verify_sleep_idle().confirmed is True)
    require(not any(os.path.lexists(path) for path in (MARKER, RECEIPT, COMMISSION)))
    claim_pending(expected)
    require(not any(os.path.lexists(path) for path in (MARKER, RECEIPT, COMMISSION)) and snapshot() == observed)
    print("Two-second local microphone test; speak now. Audio will not be retained.", file=sys.stderr)
    started = boottime()
    capture = record_audio(observed)
    require(0 < boottime() - started <= 10 and snapshot() == observed)
    for service in (VOICE, BROKER):
        inactive(system, service)
    require(system.verify_sleep_idle().confirmed is True)
    now = boottime()
    receipt = dict(schema="sage.lantern.audio-capture.v1", snapshot=observed,
        captured_at=now, expires_at=now + TTL, capture=capture)
    publish(RECEIPT, receipt, mode=0o644)
    commissioning = dict(schema="sage.lantern.audio-commissioned.v1", commissioning_id=str(uuid.uuid4()),
        binding=commissioned_binding(observed), capture_evidence_sha256=digest(receipt))
    publish(COMMISSION, commissioning, mode=0o644)
    marker = dict(schema="sage.lantern.fresh-voice-gate.v2", commission_sha256=digest(commissioning))
    require(MARKER.parent.resolve() == MARKER.parent)
    parent = os.open(MARKER.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(MARKER.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(descriptor, "w") as stream:
        os.fchmod(stream.fileno(), 0o644)
        json.dump(marker, stream)
        stream.flush()
        os.fsync(stream.fileno())
    for path in (MARKER.parent, PENDING.parent):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    PENDING.unlink()
    descriptor = os.open(PENDING.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if not capture_verified() or not admission_verified(commissioning=True):
        claim_pending(expected)
        require(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ("print-guard", "print-alsa", "inspect", "check", "admit", "capture"):
        modes.add_argument("--" + name, action="store_true")
    parser.add_argument("--expected-inputs-sha256")
    parser.add_argument("--approve-microphone-capture", action="store_true")
    args = parser.parse_args()
    try:
        if args.print_guard:
            print(GUARD, end="")
        elif args.print_alsa:
            print(ALSA_CONFIG, end="")
        elif args.check:
            return 0 if capture_verified() else 1
        elif args.admit:
            return 0 if admission_verified() else 1
        elif args.inspect:
            observed = snapshot()
            print(json.dumps(dict(input_sha256=digest(observed), snapshot=observed, capture_verified=False)))
        else:
            require(args.approve_microphone_capture is True)
            validate_capture(args.expected_inputs_sha256)
            print(json.dumps(dict(audio_commissioned=True, capture_verified=True, retained_audio=False,
                capture_evidence_expires_seconds=TTL,
                physical_ptt_verified=False, inference_verified=False)))
    except Exception:
        print("Fresh audio validation refused; preserve pending evidence for review.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
