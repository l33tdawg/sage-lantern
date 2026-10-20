#!/usr/bin/env python3
"""Disabled candidate: root launches/enrolls one Shell; failure holds apps."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import resource
import select
import signal
import socket
import socketserver
import stat
import struct
import sys
import threading
import time

from sage_lantern.desktop_launch import (ROOT_CGROUP, CONTROL, NOTIFY, LaunchOnce,
    BrokerLaunchOnce, active_session, bridge_peer, existing_compositor,
    ready_datagram)
from sage_lantern.desktop_native import (BrokerClient, NativeError, PeerAnchor,
    decode, kernel_peer, pidfd_alive, trusted_file)
from sage_lantern.desktop_control import unique_object
from sage_lantern.desktop_seat import SeatAnchor
from sage_lantern.loader_broker import LoaderBroker

REQUIRED = {'/usr/local/libexec/sage-lantern-guarded-session-authority',
    '/usr/local/libexec/desktop-native-adapter.py',
    '/usr/local/libexec/sage-lantern-guarded-session-bridge',
    '/usr/local/libexec/capture-selection-cli.py',
    '/usr/local/libexec/early_boot_admission.py',
    '/usr/local/libexec/frozen_gnome_consumer.py',
    '/usr/local/libexec/launch_selection.py',
    '/usr/local/libexec/launch_settlement_recorder.py',
    '/usr/local/libexec/lifecycle_settlement.py',
    '/usr/local/libexec/linux_probe.py',
    '/usr/local/libexec/native_probe.py',
    '/usr/local/libexec/settlement_journal.py',
    '/usr/local/libexec/settlement_owner.py',
    '/usr/local/libexec/settlement_anchor.py',
    *('/opt/sage-lantern/venv/lib/python3.11/site-packages/sage_lantern/' + name + '.py'
      for name in ('__init__', 'desktop_launch', 'desktop_native',
                   'desktop_control', 'desktop_seat', 'loader_broker'))}


def adapter_module():
    path = Path(__file__).with_name('desktop-native-adapter.py')
    spec = importlib.util.spec_from_file_location('guarded_native_adapter', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_launch_policy(path, adapter):
    body = trusted_file(path)
    value = json.loads(body, object_pairs_hook=unique_object)
    fields = {'schema', 'uid', 'gid', 'account', 'home', 'native_policy',
              'native_policy_sha256', 'files', 'activation_file'}
    if (type(value) is not dict or set(value) != fields
            or value['schema'] != 'sage.lantern.guarded-launch-policy.v1'
            or value['uid'] != 1000 or type(value['gid']) is not int or value['gid'] <= 0
            or value['account'] != 'sage' or value['home'] != '/home/sage'
            or value['activation_file'] != '/etc/sage-lantern/desktop-pin-activation.json'
            or value['native_policy'] != '/etc/sage-lantern/desktop-native-policy.json'
            or type(value['files']) is not dict or not REQUIRED <= set(value['files'])
            or any(type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None
                   for digest in (value['native_policy_sha256'], *value['files'].values()))):
        raise NativeError()
    payloads = {str(path): hashlib.sha256(body).hexdigest(),
        value['native_policy']: value['native_policy_sha256'], **value['files']}
    paths = {Path(filename) for filename in payloads}
    for filename in tuple(paths):
        paths.update(filename.parents)
    def metadata():
        records = {}
        for filename in paths:
            info = filename.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise NativeError()
            identity = (info.st_dev, info.st_ino, info.st_uid, info.st_mode)
            if stat.S_ISREG(info.st_mode):
                identity += (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            elif not stat.S_ISDIR(info.st_mode):
                raise NativeError()
            records[str(filename)] = identity
        return records
    baseline = metadata()
    for filename, digest in payloads.items():
        trusted_file(filename, digest)
    native, native_check = adapter.load_policy(Path(value['native_policy']))
    if native['uid'] != value['uid'] or metadata() != baseline:
        raise NativeError()
    def check():
        return metadata() == baseline and native_check() is True
    return {**value, **native}, check, hashlib.sha256(body).hexdigest()


def load_settlement_runtime(source, journal_root, anchor_root, capture_policy,
                            *, expected_uid=1000, early_root="/",
                            early_receipt=None, counter_spec=None):
    """Load and compose the reviewed source-only settlement owner."""
    source = Path(source)
    if not source.is_absolute() or not source.is_dir():
        raise NativeError()
    names = {
        "lifecycle": ("sage_lantern_lifecycle_settlement",
                      "lifecycle_settlement.py"),
        "journal": ("sage_lantern_settlement_journal", "settlement_journal.py"),
        "probe": ("sage_lantern_linux_probe", "linux_probe.py"),
        "anchor": ("sage_lantern_settlement_anchor", "settlement_anchor.py"),
        "owner": ("sage_lantern_settlement_owner", "settlement_owner.py"),
        "recorder": ("sage_lantern_launch_settlement_recorder",
                     "launch_settlement_recorder.py"),
        "selection": ("sage_lantern_launch_selection", "launch_selection.py"),
        "early": ("sage_lantern_early_boot_admission",
                  "early_boot_admission.py"),
    }
    loaded = {}
    for name, (module_name, filename) in names.items():
        path = source / filename
        spec = importlib.util.spec_from_file_location(
            module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    lifecycle = loaded["lifecycle"]
    journal = loaded["journal"].SettlementJournal(
        loaded["journal"].FileJournalBackend(journal_root),
        lifecycle.SettlementAuthority())
    probe = loaded["probe"].LinuxProbe("/")
    counter = None
    if counter_spec is not None:
        counter = loaded["anchor"].NvmeSmartCounter(*counter_spec)
    anchor = loaded["anchor"].FileAnchorStore(anchor_root, counter=counter)
    owner = loaded["owner"].SettlementOwner(journal, probe, anchor)
    if owner.anchor_value is None and not owner.initialize_anchor():
        raise NativeError("settlement_anchor_not_initialized")
    recorder = loaded["recorder"].LaunchSettlementRecorder(owner)
    def identity():
        machine = trusted_file("/etc/machine-id").decode("ascii").strip()
        boot = trusted_file(
            "/proc/sys/kernel/random/boot_id").decode("ascii").strip()
        return machine, boot
    selection = loaded["selection"].provider_from_capture(
        str(capture_policy), read_file=trusted_file,
        platform_identity=identity)
    if early_receipt is None:
        admission = loaded["early"].observe(
            early_root, expected_uid=expected_uid, generation=1)
        admission_check = lambda: loaded["early"].receipt_current(
            admission, early_root)
    else:
        admission = loaded["early"].read_receipt(
            early_receipt, expected_uid=expected_uid, generation=1,
            root=early_root)
        admission_check = lambda: loaded["early"].receipt_current(
            loaded["early"].read_receipt(
                early_receipt, expected_uid=expected_uid, generation=1,
                root=early_root), early_root)
    return selection, recorder, owner.recover, admission_check


def activation_check(policy, digest):
    value = json.loads(trusted_file(policy['activation_file']))
    return value == dict(schema='sage.lantern.desktop-pin-activation.v1',
        qualified_power=True, fresh_session_authorized=True, launch_policy_sha256=digest)


def bound_socket(path, uid, kind):
    path = Path(path)
    for parent in (path.parent, *path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise NativeError()
    if os.path.lexists(path):
        raise NativeError()  # No auto-adoption or removal of a predecessor socket.
    channel = socket.socket(socket.AF_UNIX, kind)
    try:
        channel.bind(str(path))
        os.chown(path, 0, pwd.getpwuid(uid).pw_gid)
        os.chmod(path, 0o660)
        return channel, (path.lstat().st_dev, path.lstat().st_ino)
    except Exception:
        channel.close()
        raise


def remove_own_socket(path, identity):
    try:
        info = Path(path).lstat()
        if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == identity:
            Path(path).unlink()
    except FileNotFoundError:
        pass


class Authority:
    def __init__(self, policy, check, digest, adapter, ready,
                 selection_provider=None, broker_factory=None,
                 settlement_recorder=None, settlement_recovery=None,
                 admission_check=None):
        self.policy, self.check, self.digest = policy, check, digest
        self.adapter, self.ready = adapter, ready
        self.selection_provider = selection_provider or (lambda: None)
        self.selection = None
        self.settlement_recovery = settlement_recovery
        self.admission_check = admission_check
        self.recovery_confirmed = broker_factory is None
        if broker_factory is None:
            self.launch = LaunchOnce(self.prepare)
        elif (settlement_recorder is None or settlement_recovery is None
              or admission_check is None):
            raise NativeError("settlement_owner_required")
        else:
            self.launch = BrokerLaunchOnce(
                self.prepare, broker_factory, settlement_recorder,
                lambda: self.selection)
        self.anchor = self.fd = self.native = self.worker = None
        self.seat = None
        self.gate = threading.Lock()
        self.publication = threading.Lock()
        self.held = threading.Event()

    def prepare(self):
        if (self.held.is_set() or not self.check() or not activation_check(self.policy, self.digest)
                or existing_compositor(self.policy['uid'])
                or not self.recovery_confirmed
                or (self.admission_check is not None
                    and not self.admission_check())):
            raise NativeError()
        selection = self.selection_provider()
        if (selection is None
                or getattr(selection, "runtime_authorized", None) is not False
                or not getattr(selection, "machine_id", None)
                or not getattr(selection, "boot_id", None)
                or not getattr(selection, "capture_id", None)
                or type(getattr(selection, "generation", None)) is not int
                or getattr(selection, "generation") < 1
                or not getattr(selection, "policy_sha256", None)):
            raise NativeError("capture_selection_required")
        self.selection = selection
        return self.policy, active_session(self.policy['uid'])

    def recover(self):
        """Settle every durable pre-release attempt before accepting a launch."""
        if self.settlement_recovery is None:
            self.recovery_confirmed = True
            return True
        try:
            outcomes = self.settlement_recovery()
        except Exception:
            return False
        if outcomes is None:
            return False
        if any(getattr(item, "status", None) != "settled" for item in outcomes):
            return False
        self.recovery_confirmed = True
        return True

    def valid(self):
        try:
            return (not self.held.is_set() and self.check() and self.fd is not None
                and pidfd_alive(self.fd) and self.anchor.matches(self.anchor.pid, self.anchor.uid)
                and (self.seat is None or self.seat.matches()))
        except Exception:
            return False

    def publish_native(self, policy):
        with self.publication:
            if self.held.is_set() or self.seat is None or not self.valid():
                raise NativeError()
            broker = BrokerClient(policy['broker_socket'], policy['broker_uid'])
            self.native = self.adapter.NativeServer(policy['native_socket'], self.anchor,
                broker, self.fd, policy_check=self.valid)
            # Cancellation can arrive during bind/construction. Do not start
            # a server after hold has already fenced this launch attempt.
            if self.held.is_set() or not self.valid():
                self.native.server_close()
                raise NativeError()
            self.worker = threading.Thread(target=self.native.serve_forever,
                kwargs={'poll_interval': 0.1}, daemon=True)
            self.worker.start()
            if self.held.is_set() or not self.valid():
                self.native.server_close()
                raise NativeError()

    def start(self, connection):
        with self.gate:
            try:
                policy, session, child = self.launch.start()
            except BaseException:
                self.fd = getattr(self.launch, "fd", None)
                raise
            self.fd = self.launch.fd
            self.anchor = PeerAnchor.enroll(child.pid, policy['uid'], session['Id'],
                policy['overlay'], policy['executable_sha256'], parent=os.getpid(), notify=NOTIFY)
            if not self.valid():
                raise NativeError()
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline and self.valid():
                if select.select([connection], [], [], 0)[0]:
                    # EOF or unexpected additional frame cancels startup.
                    raise NativeError()
                if not select.select([self.ready], [], [], 0.1)[0]:
                    continue
                body, ancillary, flags, _ = self.ready.recvmsg(1025,
                    socket.CMSG_SPACE(struct.calcsize('3i')))
                if ready_datagram(body, ancillary, flags, child.pid, policy['uid']) and self.valid():
                    # READY is a process notification, not proof of seat access.
                    # Never publish native PIN authority before the exact child
                    # controls this logind session and holds its DRM/evdev fds.
                    self.seat = SeatAnchor.enroll(child.pid, policy['uid'],
                        session['Id'], self.anchor.leader)
                    if not self.valid():
                        raise NativeError()
                    self.publish_native(policy)
                    return
            raise NativeError()

    def hold(self):
        self.held.set()
        with self.publication:
            if self.native is not None:
                self.native.server_close()  # Revokes RAM authority; never kill/terminate child.

    def close(self):
        self.hold()
        if self.native is not None and self.worker is not None and self.worker.is_alive():
            self.native.shutdown()
        if self.worker is not None:
            self.worker.join(timeout=2)
        if self.fd is not None:
            os.close(self.fd)


class Handler(socketserver.StreamRequestHandler):
    rbufsize = 0

    def handle(self):
        authority = self.server.authority
        if not self.server.slot.acquire(blocking=False):
            return
        admitted = False
        try:
            pid, uid = kernel_peer(self.request)
            if not bridge_peer(pid, uid, authority.policy['uid']):
                return
            self.request.settimeout(3)
            if decode(self.rfile.readline(514)) != {'action': 'start'}:
                return
            admitted = True
            authority.start(self.request)
            self.wfile.write(b'{"ready":true}\n')
            self.wfile.flush()
            self.request.settimeout(None)
            while authority.valid() and bridge_peer(pid, uid, authority.policy['uid']):
                if select.select([self.request], [], [], 0.25)[0]:
                    break
        except Exception:
            pass  # No secrets/exception strings in journal output.
        finally:
            if admitted:
                authority.hold()
            self.server.slot.release()


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False
    def __init__(self, channel, authority):
        super().__init__(CONTROL, Handler, bind_and_activate=False)
        self.socket.close()
        self.socket = channel
        self.authority, self.slot = authority, threading.Lock()
        self.server_activate()
    def handle_error(self, *_):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument("--capture-policy", type=Path)
    parser.add_argument("--loader-broker")
    parser.add_argument("--settlement-root", type=Path)
    parser.add_argument("--settlement-anchor", type=Path)
    parser.add_argument("--early-boot-receipt", type=Path)
    parser.add_argument("--settlement-counter-device")
    parser.add_argument("--settlement-counter-serial")
    parser.add_argument("--settlement-counter-witness", type=Path)
    parser.add_argument("--settlement-source", type=Path,
        default=Path(__file__).resolve().parents[2] /
            "software/security/desktop_loader_candidate")
    args = parser.parse_args(argv)
    broker_values = (args.capture_policy, args.loader_broker,
                     args.settlement_root, args.settlement_anchor,
                     args.early_boot_receipt)
    if any(value is not None for value in broker_values) and not all(
            value is not None for value in broker_values):
        parser.error("capture-policy, loader-broker, settlement-root, "
                     "settlement-anchor and early-boot-receipt must be "
                     "supplied together")
    counter_values = (args.settlement_counter_device,
                      args.settlement_counter_serial,
                      args.settlement_counter_witness)
    if any(value is not None for value in counter_values) and not all(
            value is not None for value in counter_values):
        parser.error("settlement-counter-device, settlement-counter-serial "
                     "and settlement-counter-witness must be supplied together")
    if args.loader_broker is not None and not all(
            value is not None for value in counter_values):
        parser.error("broker-backed launch requires the NVMe settlement counter")
    status = Path('/proc/self/status').read_text()
    if (os.geteuid() != 0 or not hasattr(os, 'pidfd_open') or 'NoNewPrivs:\t1\n' not in status
            or resource.getrlimit(resource.RLIMIT_CORE) != (0, 0)
            or Path('/proc/self/cgroup').read_text().strip() != ROOT_CGROUP):
        raise NativeError()
    adapter = adapter_module()
    policy, check, digest = load_launch_policy(args.policy, adapter)
    selection_provider = broker_factory = settlement_recorder = recovery = admission = None
    if args.loader_broker is not None:
        selection_provider, settlement_recorder, recovery, admission = load_settlement_runtime(
            args.settlement_source, args.settlement_root,
            args.settlement_anchor, args.capture_policy,
            early_receipt=args.early_boot_receipt,
            counter_spec=(args.settlement_counter_device,
                          args.settlement_counter_serial,
                          args.settlement_counter_witness))
        broker_factory = lambda: LoaderBroker.connect(args.loader_broker)
    ready, ready_identity = bound_socket(NOTIFY, policy['uid'], socket.SOCK_DGRAM)
    authority = None
    try:
        ready.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        control, control_identity = bound_socket(CONTROL, policy['uid'], socket.SOCK_STREAM)
        authority = Authority(
            policy, check, digest, adapter, ready,
            selection_provider=selection_provider,
            broker_factory=broker_factory,
            settlement_recorder=settlement_recorder,
            settlement_recovery=recovery,
            admission_check=admission)
        if not authority.recover():
            authority.hold()
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        try:
            with Server(control, authority) as server:
                server.timeout = 0.25
                while not stop.is_set():
                    server.handle_request()
                    if authority.anchor is not None and not authority.valid():
                        authority.hold()
        finally:
            remove_own_socket(CONTROL, control_identity)
    finally:
        if authority is not None:
            authority.close()
        ready.close()
        remove_own_socket(NOTIFY, ready_identity)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit('guarded_session_held') from None
