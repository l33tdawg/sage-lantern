# SPDX-License-Identifier: Apache-2.0
"""Owned local SSH control sockets; these are not native device authority."""
import os
from pathlib import Path
import stat
import tempfile
import time

from meshticulum.bench.wire import require


class Connections:
    def __init__(self, *, clock, deadline, job_factory):
        self.clock, self.deadline, self.job_factory = clock, deadline, job_factory
        # OpenSSH's Unix socket path is short even when the evidence directory
        # is long. Creation is private and explicit, after the window check.
        self.directory = Path(tempfile.mkdtemp(prefix='mtssh-', dir=Path('/tmp').resolve()))
        self.fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        self.identity = self._private_directory(os.fstat(self.fd))
        self.jobs, self.sockets = {}, {}
        self.closed = False

    @staticmethod
    def _private_directory(info):
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_ssh_control_directory')
        return info.st_dev, info.st_ino

    def _directory(self):
        require(not self.closed and self._private_directory(os.fstat(self.fd)) == self.identity
                and self._private_directory(self.directory.lstat()) == self.identity,
                'ssh_control_directory_replaced')

    def open(self, unit, argv):
        self._directory()
        require(unit in ('unit-a','unit-b','unit-c') and unit not in self.jobs and self.clock() < self.deadline,
                'ssh_control_duplicate_or_expired')
        path = self.directory / unit
        require(len(os.fsencode(path)) < 100 and not path.exists(), 'invalid_ssh_control_path')
        command = list(argv)
        master = command.index('ControlMaster=no')
        control_path = command.index('ControlPath=none')
        require(command[master-1] == command[control_path-1] == '-o', 'invalid_ssh_control_options')
        command[master] = 'ControlMaster=yes'
        command[control_path] = 'ControlPath='+str(path)
        command[-1:-1] = ['-N', '-o', 'ControlPersist=no']
        job = self.job_factory(command, deadline=self.deadline, clock=self.clock)
        self.jobs[unit] = job
        setup_end = min(self.deadline, self.clock()+5)
        while True:
            require(job.poll() is None and self.clock() < setup_end, 'ssh_control_setup_failed_or_expired')
            self._directory()
            try: info = path.lstat()
            except FileNotFoundError:
                time.sleep(.01)
                continue
            require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid()
                    and info.st_nlink == 1 and not info.st_mode & 0o077, 'unsafe_ssh_control_socket')
            self.sockets[unit] = (path, info.st_dev, info.st_ino)
            return

    def path(self, unit):
        self._directory()
        require(unit in self.jobs and unit in self.sockets, 'ssh_control_not_ready')
        job = self.jobs[unit]
        # Cleanup may need an owned connection after the dispatch cutoff. The
        # operator checks dispatch deadlines; this is only a liveness check.
        job.pump()
        require(job.process.poll() is None, 'ssh_control_process_not_live')
        path, dev, ino = self.sockets[unit]
        info = path.lstat()
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid()
                and info.st_nlink == 1 and not info.st_mode & 0o077
                and (info.st_dev, info.st_ino) == (dev, ino), 'ssh_control_socket_replaced')
        return path

    def close(self):
        if self.closed: return
        errors = []
        for unit, job in self.jobs.items():
            try: job.close()
            except BaseException as error: errors.append((unit, type(error).__name__))
        # Keep handles and the held directory available for another explicit
        # cleanup attempt if a process could not be reaped. Never settle an
        # owned master merely by removing its socket pathname.
        require(all(job.process.poll() is not None for job in self.jobs.values()),
                'ssh_control_process_cleanup_unconfirmed:'+repr(errors))
        for unit, (path, dev, ino) in self.sockets.items():
            try:
                self._directory()
                info = path.lstat()
                require((info.st_dev, info.st_ino) == (dev, ino), 'ssh_control_socket_replaced')
                path.unlink()
            except FileNotFoundError: pass  # OpenSSH removes its own socket.
            except BaseException as error: errors.append((unit, type(error).__name__))
        try:
            self._directory()
            self.directory.rmdir()
        except BaseException as error: errors.append(('directory', type(error).__name__))
        finally:
            os.close(self.fd)
            self.closed = True
        require(not errors, 'ssh_control_cleanup_unconfirmed:'+repr(errors))
