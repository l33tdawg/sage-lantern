"""Explicit one-shot fresh broker start; never an inactive-service recovery path.

The pinned inventory is an operator-reviewed input, not generated readiness.
This helper does not provision identities, open radios, start voice directly,
enable services, clear fences, retry starts or infer empty application queues.
Failed attempted starts cancel the broker before voice and verify empty cgroups.
Cancellation is process-owned, not a broker-enforced lease: SIGKILL/host failure
requires operator recovery. Retained intent alone never proves containment.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import signal
import time

from .deployment_drain import require, verify_root_broker_active
from .audio_readiness import ALSA_CONFIG, ALSA_PATH, GUARD, GUARD_PATH
from .mesh_voice_ipc import CONFIG, SOCKET
from .suspend_io import FENCE, publish, read_json, unique_object
from .suspend_system import DEPLOYMENT_FENCE, SuspendSystem


BROKER = "sage-lantern-mesh-runtime.service"
VOICE = "sage-lantern-voice-loop.service"
SYSTEMD = "/etc/systemd/system/"
START = Path("/run/sage-lantern-mesh-control/voice-start-authorized.json")
RECORD = Path("/run/sage-lantern-voice-bootstrap.json")
STATE = Path("/var/lib/sage-lantern-mesh-supervisor")
START_SECONDS = 30
CONTAIN_SECONDS = 120
AUDIO_INPUTS = {str(GUARD_PATH): GUARD.encode(), str(ALSA_PATH): ALSA_CONFIG.encode()}
REQUIRED = {str(CONFIG), SYSTEMD + BROKER, SYSTEMD + VOICE,
    SYSTEMD + BROKER + ".d/20-radio.conf",
    SYSTEMD + VOICE + ".d/30-unit-audio.conf",
    SYSTEMD + VOICE + ".d/40-mesh-broker.conf", *AUDIO_INPUTS}
OPTIONAL = {SYSTEMD + VOICE + ".d/" + name for name in (
    "90-sage-lantern-sleep.conf", "90-lantern-local-vision.conf", "95-lantern-local-vision.conf")}
RADIO_JOURNAL = SYSTEMD + BROKER + '.d/25-radio-commission-journal.conf'
RADIO_JOURNAL_SHA256 = 'c6e21f3ebf5af6620b799772ca99c0ee206e9b4c619c3cf370bf794d98eed045'
OPTIONAL.add(RADIO_JOURNAL)


def read_public(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(str(path)))
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
            and before.st_nlink == 1 and not before.st_mode & 0o022 and before.st_size <= 65536)
        raw = os.read(descriptor, 65537)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(len(raw) == before.st_size and all(
            getattr(before, field) == getattr(after, field)
            for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


class ColdBrokerBootstrap:
    def __init__(self, inventory):
        require(type(inventory) is dict and REQUIRED <= set(inventory) <= REQUIRED | OPTIONAL)
        require(all(type(value) is str and len(value) == 64
            and all(character in "0123456789abcdef" for character in value) for value in inventory.values()))
        require(all(inventory[path] == hashlib.sha256(raw).hexdigest()
            for path, raw in AUDIO_INPUTS.items()))
        require(RADIO_JOURNAL not in inventory or inventory[RADIO_JOURNAL] == RADIO_JOURNAL_SHA256)
        self.inventory = dict(inventory)
        self.system = SuspendSystem()

    def inputs(self, deadline=None):
        from .mesh_daemon import MeshDaemon, device_policy, load_config
        from .device_access import MARKER, _marker_state
        require("verify-deployment-active" in MeshDaemon.handle.__code__.co_consts)
        require((RADIO_JOURNAL in self.inventory) == os.path.lexists(RADIO_JOURNAL))
        require(all(hashlib.sha256(read_public(path)).hexdigest() == expected
            for path, expected in self.inventory.items()))
        config = load_config()
        require(read_public(SYSTEMD + BROKER + ".d/20-radio.conf") == device_policy(config).encode())
        require(_marker_state(MARKER) is True)
        require(not os.path.lexists(FENCE) and not os.path.lexists(DEPLOYMENT_FENCE))
        require(self.system.verify_sleep_idle().confirmed is True)
        for service in (BROKER, VOICE):
            limit = self.system.clock() + 5 if deadline is None else deadline
            require(self.system.clock() < limit)
            value = self.system._show(service, limit,
                ("FragmentPath", "DropInPaths", "NeedDaemonReload", "Restart", "LoadState"))
            require(value["FragmentPath"] == SYSTEMD + service and value["LoadState"] == "loaded"
                and value["NeedDaemonReload"] == "no" and value["Restart"] == "no")
            expected = {path for path in self.inventory if path.startswith(SYSTEMD + service + ".d/")}
            require(set(value["DropInPaths"].split()) == expected)
        return True

    def inactive(self, service, deadline=None):
        deadline = self.system.clock() + 5 if deadline is None else deadline
        require(self.system.clock() < deadline)
        value = self.system._show(service, deadline,
            ("ActiveState", "MainPID", "Job", "ControlGroup", "Slice", "KillMode"))
        require(value["ActiveState"] == "inactive" and value["MainPID"] == "0"
            and self.system._no_job(value["Job"]) and value["Slice"] == "system.slice"
            and value["KillMode"] == "control-group")
        group = self.system._cgroup("/system.slice/" + service)
        self.system._empty_tree(group, deadline)
        if value["ControlGroup"]:
            require(self.system._cgroup(value["ControlGroup"]) == group)
        require(value == self.system._show(service, deadline, tuple(value)))
        require(self.system.clock() < deadline)

    def contain(self):
        deadline = self.system.clock() + CONTAIN_SECONDS
        broker_stopped = False
        for service in (BROKER, VOICE):
            try:
                self.system._command(["stop", "--no-block", service],
                    min(deadline, self.system.clock() + 5))
            except Exception:
                pass
            service_deadline = min(deadline, self.system.clock() + (85 if service == BROKER else 30))
            while self.system.clock() < service_deadline:
                try:
                    self.inactive(service, deadline=service_deadline)
                    if service == BROKER:
                        broker_stopped = True
                    break
                except Exception:
                    time.sleep(min(0.2, max(0, service_deadline - self.system.clock())))
            else:
                if service == VOICE:
                    return False
        if not broker_stopped:
            return False
        try:
            for service in (BROKER, VOICE):
                self.inactive(service, deadline=deadline)
            return not os.path.lexists(START)
        except Exception:
            return False

    def fresh(self):
        self.inputs()
        for path in (SOCKET, START, Path("/run/sage-lantern-voice")):
            require(not os.path.lexists(path))
        if os.path.lexists(STATE):
            info = STATE.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o700 and not list(STATE.iterdir()))
        for service in (BROKER, VOICE):
            self.inactive(service)
        require(self.system.inactive_verified(VOICE).confirmed is True)
        return True

    def active(self, deadline=None):
        if deadline is not None:
            require(self.system.clock() < deadline)
        evidence = verify_root_broker_active()
        for service, key in ((BROKER, "broker_pid"), (VOICE, "voice_pid")):
            limit = self.system.clock() + 5 if deadline is None else deadline
            require(self.system.clock() < limit)
            value = self.system._show(service, limit,
                ("ActiveState", "MainPID", "Job"))
            require(value["ActiveState"] == "active" and value["MainPID"] == str(evidence[key])
                and self.system._no_job(value["Job"]))
        require(not os.path.lexists(START))
        return evidence

    def start(self):
        require(os.geteuid() == 0)
        self.fresh()
        binding = dict(schema="sage.lantern.voice-bootstrap.v1", phase="intent",
            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            pid=os.getpid(), inventory=self.inventory)
        descriptor = os.open(RECORD, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(binding, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        parent = os.open(RECORD.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        attempted = False
        handlers = {}
        def interrupted(signum, frame):
            raise InterruptedError("bootstrap cancelled")
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.signal(signum, interrupted)
            self.fresh()
            deadline = self.system.clock() + START_SECONDS
            attempted = True
            self.system._command(["start", "--no-block", BROKER], deadline)
            while self.system.clock() < deadline:
                try:
                    evidence = self.active(deadline=deadline)
                    break
                except InterruptedError:
                    raise
                except Exception:
                    time.sleep(min(0.2, max(0, deadline - self.system.clock())))
            else:
                require(False)
            require(self.system.clock() < deadline)
            self.inputs(deadline=deadline)
            require(self.system.clock() < deadline)
            require(read_json(RECORD, owner_uid=0, limit=16384) == binding)
            publish(RECORD, {**binding, "phase": "complete", "evidence": evidence})
            return dict(broker_active=True, voice_active=True, storage_verified=True,
                capture_verified=False, inference_verified=False, physical_voice_verified=False)
        except BaseException:
            for signum in handlers:
                signal.signal(signum, signal.SIG_IGN)
            failure = {**binding, "phase": "cancelling", "start_attempted": attempted,
                "containment_verified": False, "recovery_required": True}
            try:
                publish(RECORD, failure)
            except Exception:
                pass
            contained = False
            if attempted:
                try:
                    contained = self.contain() is True
                except Exception:
                    pass
            try:
                publish(RECORD, {**failure, "phase": "failed", "containment_verified": contained})
            except Exception:
                pass
            raise
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    raw = read_public(args.inventory)
    require(hashlib.sha256(raw).hexdigest() == args.inventory_sha256)
    owner = ColdBrokerBootstrap(json.loads(raw, object_pairs_hook=unique_object))
    if args.execute:
        print(json.dumps(owner.start(), sort_keys=True))
    else:
        owner.fresh()
        print(json.dumps(dict(preflight=True, activation_authorized=False, storage_verified=False)))


if __name__ == "__main__":
    main()
