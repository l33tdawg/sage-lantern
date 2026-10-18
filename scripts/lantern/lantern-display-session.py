#!/usr/bin/env python3
"""Root supervisor for one verified active dedicated GDM X11 kiosk.

--check only verifies public session/process metadata; it does not connect to X.
--check-x11 performs only the installed helper's non-blanking idle query.
--serve requires an explicit root-owned activation policy written AFTER owner
PIN/recovery setup: /etc/sage-lantern-display-power.enabled, containing exactly
"fresh unit-a\n" through "fresh unit-f\n", or explicitly "legacy unit-a\n".
This policy is operator authorization, not measured encryption/readiness evidence.
Xauthority is copied only into a sealed anonymous descriptor, never logged or
persisted; no private application state or arbitrary inherited environment is used.
Session loss kills the child group without stale-display finally/wake actions.
Revalidation is bounded observation, not an atomic logind/X-server transaction.
Normal operation uses 500 ms pinned checks and five-second full reconciliation.
The child starts parked and is paused during full reconciliation. A separate
eight-second watchdog kills its unreaped process group on lease expiry even if
the main loop is delayed. Verification shares a two-second deadline; shutdown
does not perform logind discovery and allows at most one second of wake cleanup.
"""

import argparse
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import select
import signal
import stat
import subprocess
import sys
import threading
import time

from sage_lantern.suspend_kiosk import (
    KioskBinding, LinuxProcesses, _read_binding_file, _trusted_file,
    discover_launcher, load_kiosk_binding,
)


HELPER = '/usr/local/libexec/sage-lantern-display-power'
POLICY = Path('/etc/sage-lantern-display-power.enabled')
ENV = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')
FIELDS = ('Id', 'User', 'Name', 'Seat', 'Active', 'Remote', 'Type', 'Class', 'Service', 'State', 'Scope', 'Display')
CHECK_SECONDS = 0.5
RECONCILE_SECONDS = 5.0
LEASE_SECONDS = 8.0
VERIFY_SECONDS = 2.0
BACKOFF_MAX = 30.0
CHILD = ('import os,signal; '
         'os.environ["XAUTHORITY"] = "/proc/%s/fd/%s" % (os.getpid(), os.environ.pop("LANTERN_AUTH_FD")); '
         'os.kill(os.getpid(), signal.SIGSTOP); '
         'os.execv("/usr/bin/python3", ["/usr/bin/python3", "/usr/local/libexec/sage-lantern-display-power"])')
QUERY_CHILD = ('import os,signal; '
               'os.environ["XAUTHORITY"] = "/proc/%s/fd/%s" % (os.getpid(), os.environ.pop("LANTERN_AUTH_FD")); '
               'os.kill(os.getpid(), signal.SIGSTOP); '
               'os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", "-B", '
               '"/usr/local/libexec/sage-lantern-display-power", "--query-idle"])')


def remaining(deadline, stopped=None):
    require(stopped is None or not stopped.is_set(), 'display supervisor stopping')
    budget = deadline - time.monotonic()
    require(budget > 0, 'display verification deadline expired')
    return budget


def require(value, reason):
    if not value:
        raise ValueError(reason)


def properties(raw):
    result = {}
    for line in raw.splitlines():
        name, separator, value = line.partition('=')
        require(separator and name not in result, 'invalid logind properties')
        result[name] = value
    require(set(result) == set(FIELDS), 'incomplete logind properties')
    return result


def session_fields(raw, binding, session):
    values = properties(raw)
    expected = dict(Id=session, User=str(binding.uid), Name=binding.account, Seat='seat0',
                    Active='yes', Remote='no', Type='x11', Class='user', State='active',
                    Scope=f'session-{session}.scope')
    require(all(values[name] == value for name, value in expected.items())
            and values['Service'] in ('gdm-autologin', 'gdm-password'), 'not active local kiosk session')
    return values


def selected_environment(raw, binding, session, logind_display):
    require(len(raw) <= 65536 and raw.endswith(b'\0'), 'invalid launcher environment')
    names = {b'DISPLAY', b'XAUTHORITY', b'XDG_SESSION_ID', b'XDG_SESSION_TYPE', b'XDG_SEAT'}
    values = {}
    for item in raw.split(b'\0'):
        name, separator, value = item.partition(b'=')
        if name in names:
            require(separator and name not in values, 'duplicate launcher binding')
            values[name] = value.decode('ascii')
    require(set(values) == names, 'missing launcher binding')
    display = values[b'DISPLAY']
    require(re.fullmatch(r':[0-9]{1,4}(?:\.[0-9]{1,2})?', display), 'nonlocal display')
    authority = f'/run/user/{binding.uid}/gdm/Xauthority'
    require(values[b'XAUTHORITY'] == authority and values[b'XDG_SESSION_ID'] == session
            and values[b'XDG_SESSION_TYPE'] == 'x11' and values[b'XDG_SEAT'] == 'seat0'
            and logind_display in ('', display), 'launcher/logind binding mismatch')
    return display, authority


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def process_identity(process):
    return (process.pid, process.parent, process.start_ticks, process.uid, process.executable, process.argv)


def sealed_authority(session):
    require(authority_identity(session.authority, session.binding.uid) == session.authority_pin,
            'authority changed before snapshot')
    descriptor = os.open(session.authority, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    snapshot = None
    try:
        require(file_identity(os.fstat(descriptor)) == session.authority_pin, 'authority changed while opening')
        data = os.read(descriptor, 65537)
        require(len(data) == session.authority_pin[5] and len(data) <= 65536
                and file_identity(os.fstat(descriptor)) == session.authority_pin
                and authority_identity(session.authority, session.binding.uid) == session.authority_pin,
                'authority changed during snapshot')
        snapshot = os.memfd_create('lantern-display-authority', os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        require(os.write(snapshot, data) == len(data), 'short authority snapshot')
        os.fchown(snapshot, session.binding.uid, -1)
        os.fchmod(snapshot, 0o400)
        fcntl.fcntl(snapshot, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        return snapshot
    except Exception:
        if snapshot is not None:
            os.close(snapshot)
        raise
    finally:
        os.close(descriptor)


def authority_identity(path, uid):
    for parent in reversed(Path(path).parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, uid)
                and not info.st_mode & 0o022, 'unsafe authority directory')
    info = Path(path).lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_nlink == 1
            and not info.st_mode & 0o077 and 0 < info.st_size <= 65536, 'unsafe authority file')
    return file_identity(info)


def socket_identity(display, uid):
    directory = Path('/tmp/.X11-unix')
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o1777, 'unsafe X socket directory')
    info = (directory / ('X' + display[1:].split('.')[0])).lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid in (0, uid), 'unsafe X socket')
    return file_identity(info)


@dataclass(frozen=True)
class Session:
    binding: KioskBinding
    session_id: str
    launcher: object
    display: str
    authority: str
    authority_pin: tuple
    socket_pin: tuple


class Child:
    def __init__(self, process):
        self.process = process
        self.pid = process.pid
        self.descriptor = os.pidfd_open(process.pid)
        self.lock = threading.Lock()
        self.deadline = None
        self.timer = None
        self.expired = False
        self.closed = False

    def poll(self):
        watcher = select.poll()
        watcher.register(self.descriptor, select.POLLIN)
        return 0 if watcher.poll(0) else None

    def _signal(self, action):
        try:
            os.killpg(self.pid, action)
        except ProcessLookupError:
            pass

    def _expire(self, deadline):
        with self.lock:
            if not self.closed and self.deadline == deadline:
                self.expired = True
                self._signal(signal.SIGKILL)

    def renew(self, deadline):
        with self.lock:
            now = time.monotonic()
            require(not self.closed and not self.expired and self.poll() is None
                    and deadline > now and (self.deadline is None or now < self.deadline),
                    'display child lease expired')
            if self.timer is not None:
                self.timer.cancel()
            self.deadline = deadline
            self.timer = threading.Timer(deadline - now, self._expire, args=(deadline,))
            self.timer.daemon = True
            self.timer.start()

    def pause(self):
        with self.lock:
            require(not self.closed and not self.expired, 'display child unavailable')
            self._signal(signal.SIGSTOP)

    def resume(self):
        with self.lock:
            require(not self.closed and not self.expired and self.deadline is not None
                    and time.monotonic() < self.deadline and self.poll() is None, 'display child lease expired')
            self._signal(signal.SIGCONT)

    def finish(self, graceful):
        with self.lock:
            valid = (not self.closed and not self.expired and self.deadline is not None
                     and time.monotonic() < self.deadline)
            grace = min(1.0, self.deadline - time.monotonic()) if graceful and valid else 0
            if grace > 0 and self.poll() is None:
                self._signal(signal.SIGTERM)
        if grace > 0:
            watcher = select.poll()
            watcher.register(self.descriptor, select.POLLIN)
            watcher.poll(max(0, int(grace * 1000)))
        with self.lock:
            if self.closed:
                return
            self._signal(signal.SIGKILL)
            self.closed = True
            if self.timer is not None:
                self.timer.cancel()
            try:
                self.process.wait(timeout=1)
            finally:
                os.close(self.descriptor)


class Native:
    def __init__(self):
        self.processes = LinuxProcesses()

    def command(self, command, *, deadline, stopped=None):
        remaining(deadline, stopped)
        _trusted_file(Path(command[0]))
        result = subprocess.run(command, env=ENV, stdin=subprocess.DEVNULL,
                                capture_output=True, check=True, timeout=remaining(deadline, stopped))
        remaining(deadline, stopped)
        require(len(result.stdout) <= 16384, 'oversize session properties')
        return result.stdout.decode('utf-8').strip()

    def snapshot(self, deadline, stopped):
        result = {}
        count = 0
        for entry in Path('/proc').iterdir():
            remaining(deadline, stopped)
            if not entry.name.isdecimal():
                continue
            count += 1
            require(count <= self.processes.max_processes, 'process scan bound exceeded')
            try:
                result[int(entry.name)] = self.processes.read(int(entry.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
        return result

    def in_session(self, process, session, binding):
        group = f'/user.slice/user-{binding.uid}.slice/session-{session}.scope'
        with (Path('/proc') / str(process.pid) / 'cgroup').open('rb') as stream:
            groups = stream.read(16385)
        require(len(groups) <= 16384, 'oversize cgroup')
        lines = [line.split(':', 2) for line in groups.decode().splitlines()]
        return any(len(line) == 3 and line[1] in ('', 'name=systemd')
                   and (line[2] == group or line[2].startswith(group + '/')) for line in lines)

    def cheap(self, session, deadline, stopped=None):
        remaining(deadline, stopped)
        require(self.processes.fence_state() == 'absent'
                and not os.path.lexists('/run/sage-lantern-deployment.pending'), 'sleep/deployment containment active')
        process = self.processes.read(session.launcher.pid)
        require(process_identity(process) == process_identity(session.launcher) and process.state != 'Z'
                and self.in_session(process, session.session_id, session.binding), 'pinned launcher changed')
        require(authority_identity(session.authority, session.binding.uid) == session.authority_pin
                and socket_identity(session.display, session.binding.uid) == session.socket_pin, 'X session changed')
        remaining(deadline, stopped)

    def inspect(self, binding, deadline=None, stopped=None):
        deadline = time.monotonic() + VERIFY_SECONDS if deadline is None else deadline
        remaining(deadline, stopped)
        def command(arguments):
            return self.command(arguments, deadline=deadline, stopped=stopped)
        self.processes.validate_host()
        require(self.processes.fence_state() == 'absent'
                and not os.path.lexists('/run/sage-lantern-deployment.pending'), 'sleep/deployment containment active')
        session = command(['/usr/bin/loginctl', 'show-seat', 'seat0', '-p', 'ActiveSession', '--value'])
        require(re.fullmatch(r'[A-Za-z0-9]{1,32}', session), 'invalid active session')
        values = session_fields(command(['/usr/bin/loginctl', 'show-session', session, '--no-pager', '--all',
                                         *(argument for field in FIELDS for argument in ('-p', field))]),
                                binding, session)
        group = f'/user.slice/user-{binding.uid}.slice/session-{session}.scope'
        actual = command(['/usr/bin/systemctl', 'show', values['Scope'], '-p', 'ControlGroup', '--value'])
        require(actual == group, 'unexpected session cgroup')
        candidates = []
        for process in self.snapshot(deadline, stopped).values():
            remaining(deadline, stopped)
            if (process.uid != binding.uid or process.executable != '/usr/bin/bash'
                    or process.argv not in binding.launcher_argv):
                continue
            if not self.in_session(process, session, binding):
                continue
            candidates.append(process)
        require(len(candidates) == 1, 'dedicated kiosk ambiguous or absent')
        process = candidates[0]
        verified = discover_launcher(launcher_pid=process.pid, binding=binding, backend=self.processes)
        require(process_identity(verified) == process_identity(process), 'launcher changed')
        with (Path('/proc') / str(process.pid) / 'environ').open('rb') as stream:
            raw = stream.read(65537)
        display, authority = selected_environment(raw, binding, session, values['Display'])
        require(process_identity(self.processes.read(process.pid)) == process_identity(process),
                'launcher changed during environment read')
        result = Session(binding, session, process, display, authority,
                         authority_identity(authority, binding.uid), socket_identity(display, binding.uid))
        require(command(['/usr/bin/loginctl', 'show-seat', 'seat0', '-p', 'ActiveSession', '--value']) == session,
                'active session changed during inspection')
        remaining(deadline, stopped)
        return result

    def await_parked(self, child, deadline, stopped):
        while child.poll() is None:
            remaining(deadline, stopped)
            raw = (Path('/proc') / str(child.pid) / 'stat').read_text()
            if raw.rsplit(')', 1)[1].split()[0] in ('T', 't'):
                return
            time.sleep(0.01)
        raise ValueError('display child exited before parking')

    def launch(self, session, deadline=None, stopped=None):
        deadline = time.monotonic() + VERIFY_SECONDS if deadline is None else deadline
        remaining(deadline, stopped)
        _trusted_file(Path('/usr/bin/python3').resolve())
        _trusted_file(Path(HELPER))
        account = pwd.getpwnam(session.binding.account)
        require(account.pw_uid == session.binding.uid and account.pw_dir == f'/home/{session.binding.account}',
                'account changed')
        descriptor = sealed_authority(session)
        try:
            self.cheap(session, deadline, stopped)
            environment = {**ENV, 'HOME': account.pw_dir, 'USER': account.pw_name,
                           'DISPLAY': session.display, 'LANTERN_AUTH_FD': str(descriptor),
                           'PYTHONDONTWRITEBYTECODE': '1'}
            process = subprocess.Popen(['/usr/bin/python3', '-c', CHILD], env=environment,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       user=account.pw_uid, group=account.pw_gid, extra_groups=(),
                                       pass_fds=(descriptor,), start_new_session=True)
            try:
                child = Child(process)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
                raise
            try:
                self.await_parked(child, deadline, stopped)
                return child
            except Exception:
                child.finish(False)
                raise
        finally:
            os.close(descriptor)

    def stop(self, child, graceful):
        child.finish(graceful)

    def check_x11(self, binding):
        deadline = time.monotonic() + VERIFY_SECONDS
        first = self.inspect(binding, deadline=deadline)
        _trusted_file(Path('/usr/bin/python3').resolve())
        _trusted_file(Path(HELPER))
        account = pwd.getpwnam(binding.account)
        require(account.pw_uid == binding.uid and account.pw_dir == f'/home/{binding.account}',
                'account changed')
        descriptor = sealed_authority(first)
        process = None
        child = None
        try:
            self.cheap(first, deadline)
            remaining(deadline)
            process = subprocess.Popen(['/usr/bin/python3', '-I', '-B', '-c', QUERY_CHILD],
                                       env={**ENV, 'HOME': account.pw_dir, 'USER': account.pw_name,
                                            'DISPLAY': first.display, 'LANTERN_AUTH_FD': str(descriptor)},
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       user=account.pw_uid, group=account.pw_gid, extra_groups=(),
                                       pass_fds=(descriptor,), start_new_session=True)
            child = Child(process)
            self.await_parked(child, deadline, None)
            child.renew(deadline)
            child.resume()
            output = b''
            while True:
                ready, _, _ = select.select([process.stdout], [], [], remaining(deadline))
                require(ready, 'idle query timed out')
                chunk = os.read(process.stdout.fileno(), 22 - len(output))
                if not chunk:
                    break
                output += chunk
                require(len(output) <= 21, 'oversize idle result')
            ready, _, _ = select.select([child.descriptor], [], [], remaining(deadline))
            require(ready, 'idle query did not exit')
            child.finish(False)
            require(process.returncode == 0 and re.fullmatch(rb'[0-9]{1,20}\n?', output)
                    and int(output) <= 2**64 - 1, 'invalid idle result')
        finally:
            try:
                if child is not None:
                    child.finish(False)
                elif process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=1)
            finally:
                if process is not None:
                    process.stdout.close()
                os.close(descriptor)
        require(same_session(first, self.inspect(binding, deadline=deadline)), 'session changed')
        remaining(deadline)
        return first


def binding_for(mode, unit):
    if mode == 'legacy':
        require(unit == 'unit-a', 'legacy is Unit A only')
        account = pwd.getpwnam('radxa')
        require(account.pw_uid == 1000 and account.pw_dir == '/home/radxa', 'legacy account mismatch')
        return KioskBinding.legacy()
    binding = load_kiosk_binding()
    require(binding.unit_id == unit, 'installed unit mismatch')
    return binding


def approved_binding():
    raw = _read_binding_file(POLICY)
    require(raw in tuple(f'fresh unit-{letter}\n' for letter in 'abcdef') + ('legacy unit-a\n',), 'display activation not approved')
    mode, unit = raw.strip().split(' ')
    return binding_for(mode, unit)


def same_session(first, second):
    return (first.binding == second.binding and first.session_id == second.session_id
            and first.display == second.display and first.authority == second.authority
            and first.authority_pin == second.authority_pin and first.socket_pin == second.socket_pin
            and process_identity(first.launcher) == process_identity(second.launcher))


def supervise(native, stopped, binding_loader=approved_binding, clock=time.monotonic):
    child = None
    pinned = None
    reconcile_at = 0.0
    expires = 0.0
    retry_at = 0.0
    backoff = 1.0
    started_at = 0.0
    paused = False
    try:
        while not stopped.is_set():
            now = clock()
            if child is None and now < retry_at:
                stopped.wait(min(CHECK_SECONDS, retry_at - now))
                continue
            try:
                binding = binding_loader()
                require(not stopped.is_set(), 'supervisor stopping')
                if child is None:
                    deadline = clock() + VERIFY_SECONDS
                    current = native.inspect(binding, deadline=deadline, stopped=stopped)
                    require(clock() < deadline and not stopped.is_set(), 'acquisition expired')
                    child = native.launch(current, deadline=deadline, stopped=stopped)
                    paused = True
                    require(clock() < deadline and not stopped.is_set(), 'launch expired')
                    pinned = current
                    started_at = clock()
                    expires = started_at + LEASE_SECONDS
                    child.renew(expires)
                    child.resume()
                    paused = False
                    reconcile_at = clock() + RECONCILE_SECONDS
                else:
                    require(child.poll() is None and now < expires and binding == pinned.binding,
                            'child exited or lease invalid')
                    if now >= reconcile_at:
                        child.pause()
                        paused = True
                        deadline = min(expires, clock() + VERIFY_SECONDS)
                        current = native.inspect(binding, deadline=deadline, stopped=stopped)
                        require(same_session(pinned, current) and clock() < deadline and not stopped.is_set(),
                                'reconciliation changed or expired')
                        native.cheap(pinned, deadline, stopped)
                        require(clock() < deadline and not stopped.is_set(), 'reconciliation expired')
                        expires = clock() + LEASE_SECONDS
                        child.renew(expires)
                        child.resume()
                        paused = False
                        reconcile_at = clock() + RECONCILE_SECONDS
                    else:
                        deadline = min(expires, clock() + CHECK_SECONDS)
                        native.cheap(pinned, deadline, stopped)
                        require(clock() < deadline and not stopped.is_set(), 'cheap verification expired')
                    if clock() - started_at >= 30:
                        backoff = 1.0
            except Exception:
                if child is not None:
                    native.stop(child, False)
                    child = None
                paused = False
                retry_at = clock() + backoff
                backoff = min(BACKOFF_MAX, backoff * 2)
                stopped.wait(CHECK_SECONDS)
                continue
            stopped.wait(min(CHECK_SECONDS, max(0, expires - clock())))
    finally:
        if child is not None:
            try:
                deadline = min(expires, clock() + 0.25)
                require(not paused and clock() < deadline and binding_loader() == pinned.binding,
                        'shutdown lease unavailable')
                native.cheap(pinned, deadline)
                graceful = clock() < deadline
            except Exception:
                graceful = False
            native.stop(child, graceful)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--check-x11', action='store_true')
    mode.add_argument('--serve', action='store_true')
    parser.add_argument('--unit-id', choices=('unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'))
    parser.add_argument('--legacy', action='store_true')
    args = parser.parse_args(argv)
    try:
        require(os.geteuid() == 0, 'root session verification required')
        native = Native()
        if args.check_x11:
            require(args.unit_id is not None, 'explicit unit required')
            binding = binding_for('legacy' if args.legacy else 'fresh', args.unit_id)
            current = native.check_x11(binding)
            print(json.dumps(dict(verified=True, activated=False, unit_id=args.unit_id,
                                  display=current.display, idle_query=True)))
        elif args.check:
            require(args.unit_id is not None, 'explicit unit required')
            binding = binding_for('legacy' if args.legacy else 'fresh', args.unit_id)
            deadline = time.monotonic() + VERIFY_SECONDS
            first = native.inspect(binding, deadline=deadline)
            require(same_session(first, native.inspect(binding, deadline=deadline)), 'session changed')
            remaining(deadline)
            print(json.dumps(dict(verified=True, activated=False, unit_id=args.unit_id,
                                  session_id=first.session_id, display=first.display, launcher_pid=first.launcher.pid)))
        else:
            require(args.unit_id is None and not args.legacy, 'serve binding comes only from approved policy')
            stopped = threading.Event()
            for signum in (signal.SIGINT, signal.SIGTERM):
                signal.signal(signum, lambda received, frame: stopped.set())
            supervise(native, stopped)
        return 0
    except Exception:
        print('Display session verification failed; no fallback display selected.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
