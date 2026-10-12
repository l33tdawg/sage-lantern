# SPDX-License-Identifier: Apache-2.0
"""PRIVATE local historical metadata change detector; never native authority.

Construct only AFTER independent actual row/relation/expiry verification. Supply
the verified raw file SHA256s and complete historical directory entry sets.
No SQL, grant/approval interpretation, creation, write, key or payload access.
Shared nonblocking historical owner locks refuse active exclusive owners. Own
current NativeCaps/consume/ARM checks remain mandatory and separate.
Continuity covers this retained-FD lifetime, never fresh RPC processes.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import threading

MAX_FILE = 16 * 1024 * 1024
_DBS = ('attempts.sqlite', 'bytes.sqlite', 'wire.sqlite', 'native.sqlite', 'caps.sqlite')
_FILES = frozenset(('grant.json', 'active', 'provision.json', 'job.json',
    'result.json', 'policy.json', 'owner.json', 'prior.json', 'started') + _DBS +
    tuple(db + suffix for db in _DBS for suffix in ('.owner', '.approval', '.pending')) +
    ('capture.json', 'history-binding.json', 'history-identity-seal.json', 'legacy-grant-seal.json'))
_CAPTURE_DIRECTORY = 'parents/d0f37a4030fbe55d2829e8c0cebcee5b'
_UNADMITTED_DIRECTORY = 'parents/63830cfa1d045680a9e4961d0d6b69c8'
_MB1_UNADMITTED_DIRECTORY = 'parents/a658ae21c653c6f5cadfaa1b28b57498'
_CAPTURE_IDENTITY_DIRECTORIES = (_CAPTURE_DIRECTORY+'/prepared-identities',
                                 _UNADMITTED_DIRECTORY+'/prepared-identities',
                                 _MB1_UNADMITTED_DIRECTORY+'/prepared-identities')
_PAID_PREPARED_DIRECTORY = 'parents/d60d8308326314a7753e651fa43a99e3'
_PAID_IDENTITIES = _PAID_PREPARED_DIRECTORY+'/prepared-identities'
_COMPLETE_PAID_DIRECTORY = 'parents/4559277fa43913ce3935b078d6744a55'
_COMPLETE_PAID_IDENTITIES = _COMPLETE_PAID_DIRECTORY+'/prepared-identities'
_BULK_PAID_DIRECTORY = 'parents/4034f8979b985660df21859606c4f8a7'
_BULK_PAID_IDENTITIES = _BULK_PAID_DIRECTORY+'/prepared-identities'
_MIXED_PAID_DIRECTORY = 'parents/ba4572e95e598ad6c17fc954c3792db7'
_MIXED_PAID_IDENTITIES = _MIXED_PAID_DIRECTORY+'/prepared-identities'
_SUCCESS_PAID_DIRECTORY = 'parents/1df30b47fa36b8d47328fb886528a7a9'
_SUCCESS_PAID_IDENTITIES = _SUCCESS_PAID_DIRECTORY+'/prepared-identities'
_PARTIAL_PAID_DIRECTORY = 'parents/f153a7a87a2803db85078df81a6a1aaf'
_PARTIAL_PAID_IDENTITIES = _PARTIAL_PAID_DIRECTORY+'/prepared-identities'
_ACCEPTED_BULK_DIRECTORY = 'parents/25a990a2d565c50b237ee45812a8c75f'
_ACCEPTED_BULK_IDENTITIES = _ACCEPTED_BULK_DIRECTORY+'/prepared-identities'
_REVERSE_BULK_DIRECTORY = 'parents/cb5869e6aa8de545a267007b085d4762'
_REVERSE_BULK_IDENTITIES = _REVERSE_BULK_DIRECTORY+'/prepared-identities'
_SMALL_BULK_DIRECTORY = 'parents/f6e147e70ec7ce07f673ea5e9cd2cdc2'
_SMALL_BULK_IDENTITIES = _SMALL_BULK_DIRECTORY+'/prepared-identities'
_ZERO_RETRY_BULK_DIRECTORY = 'parents/b8f9b5d9c5479c8d363c7b7e8014bc3b'
_ZERO_RETRY_BULK_IDENTITIES = _ZERO_RETRY_BULK_DIRECTORY+'/prepared-identities'
_IMMEDIATE_RX_BULK_DIRECTORY = 'parents/806fdca055e79dfc0b86ff8f304bcdf9'
_IMMEDIATE_RX_BULK_IDENTITIES = _IMMEDIATE_RX_BULK_DIRECTORY+'/prepared-identities'
_SF7_REPLICATE_DIRECTORY = 'parents/4ea23c6b8fa83366904ac62ffa98e2bf'
_SF7_REPLICATE_IDENTITIES = _SF7_REPLICATE_DIRECTORY+'/prepared-identities'
_PUBLIC32_DIRECTORY = 'parents/c26cd03a8fd427daf6b8d894e4b1d04c'
_PUBLIC32_IDENTITIES = _PUBLIC32_DIRECTORY+'/prepared-identities'
_PUBLIC32_RETRY_DIRECTORY = 'parents/8dfcfdfe8f8be2c0b326520d70eab7f5'
_PUBLIC32_RETRY_IDENTITIES = _PUBLIC32_RETRY_DIRECTORY+'/prepared-identities'
_PUBLIC32_SENDER_READY_DIRECTORY = 'parents/6f89e2a21919991e15224e393446928c'
_PUBLIC32_SENDER_READY_IDENTITIES = _PUBLIC32_SENDER_READY_DIRECTORY+'/prepared-identities'
_PUBLIC32_COMPLETE_DIRECTORY = 'parents/62bcef4f1c74532fd7ab3ada1b8b94e2'
_PUBLIC32_COMPLETE_IDENTITIES = _PUBLIC32_COMPLETE_DIRECTORY+'/prepared-identities'
_ENROLLED_ZERO_DIRECTORY = 'parents/141c892d83b6064a800c11aab6444f28'
_ENROLLED_ZERO_IDENTITIES = _ENROLLED_ZERO_DIRECTORY+'/prepared-identities'
_PARTIAL_BUNDLE_DIRECTORY = 'parents/05c36e09fd4056c94d341d2dd32243f4'
_PARTIAL_BUNDLE_IDENTITIES = _PARTIAL_BUNDLE_DIRECTORY+'/prepared-identities'
_V2_PARTIAL_BUNDLE_DIRECTORY = 'parents/bccb99afe6589968190b131eeaf372b3'
_V2_PARTIAL_BUNDLE_IDENTITIES = _V2_PARTIAL_BUNDLE_DIRECTORY+'/prepared-identities'
_V2_FAILED_BUNDLE_DIRECTORY = 'parents/487ae574816ff5444de03e3daa4401e4'
_V2_FAILED_BUNDLE_IDENTITIES = _V2_FAILED_BUNDLE_DIRECTORY+'/prepared-identities'
_V3_PARTIAL_BUNDLE_DIRECTORY = 'parents/e01508ddcae7389728add074de4876b7'
_V3_PARTIAL_BUNDLE_IDENTITIES = _V3_PARTIAL_BUNDLE_DIRECTORY+'/prepared-identities'
_V3_CHECKPOINT_BUNDLE_DIRECTORY = 'parents/2429969fcbb0f57b3deaeda7187036c2'
_V3_CHECKPOINT_BUNDLE_IDENTITIES = _V3_CHECKPOINT_BUNDLE_DIRECTORY+'/prepared-identities'
_V3_VERIFIED_BUNDLE_DIRECTORY = 'parents/7746ddba2544ce79c01a195f1e8746ce'
_V3_VERIFIED_BUNDLE_IDENTITIES = _V3_VERIFIED_BUNDLE_DIRECTORY+'/prepared-identities'
_SF5_STARTUP_DIRECTORY = 'parents/57339cca60ddf62b41eafac60147a252'
_SF5_STARTUP_IDENTITIES = _SF5_STARTUP_DIRECTORY+'/prepared-identities'
_SF5_COMPLETED_DIRECTORY = 'parents/4aa3ad2bc1346cae82bc90d8abd6dcd4'
_SF5_COMPLETED_IDENTITIES = _SF5_COMPLETED_DIRECTORY+'/prepared-identities'
_PARTIAL_BATCH_DIRECTORY = 'parents/a3ded77ef251c9d7e085ab790b9ac5b3'
_PARTIAL_BATCH_IDENTITIES = _PARTIAL_BATCH_DIRECTORY+'/prepared-identities'
_COMPLETED_BATCH_DIRECTORY = 'parents/76fe38883690e058771940d51d0d57d6'
_COMPLETED_BATCH_IDENTITIES = _COMPLETED_BATCH_DIRECTORY+'/prepared-identities'
_RX_PRIORITY_BATCH_DIRECTORY = 'parents/7630420c0e72181d6e7239c105c165c6'
_RX_PRIORITY_BATCH_IDENTITIES = _RX_PRIORITY_BATCH_DIRECTORY+'/prepared-identities'
_EARLY_RX_PARTIAL_DIRECTORY = 'parents/4ca8d86644aa24e6fefd06872a9cd131'
_EARLY_RX_PARTIAL_IDENTITIES = _EARLY_RX_PARTIAL_DIRECTORY+'/prepared-identities'
_GFSK_COMPLETE_DIRECTORY = 'parents/6be212b13351cb9852ed07ba7998be96'
_GFSK_COMPLETE_IDENTITIES = _GFSK_COMPLETE_DIRECTORY+'/prepared-identities'
_GFSK_BUNDLE_DIRECTORY = 'parents/e8aac38a787f33daa91c80e39cfc8710'
_GFSK_BUNDLE_IDENTITIES = _GFSK_BUNDLE_DIRECTORY+'/prepared-identities'
_GFSK_PARTIAL_BUNDLE_DIRECTORY = 'parents/73c1cda8c02939409d0b1e7e2a39be67'
_GFSK_PARTIAL_BUNDLE_IDENTITIES = _GFSK_PARTIAL_BUNDLE_DIRECTORY+'/prepared-identities'
_IDENTITY_COUNTS = {_PAID_IDENTITIES: 9, _COMPLETE_PAID_IDENTITIES: 10,
                    _BULK_PAID_IDENTITIES: 4, _MIXED_PAID_IDENTITIES: 4, _SUCCESS_PAID_IDENTITIES: (2,4), _PARTIAL_PAID_IDENTITIES: (0,1), _ACCEPTED_BULK_IDENTITIES: (0,2), _REVERSE_BULK_IDENTITIES: (0,2), _SMALL_BULK_IDENTITIES: (0,2), _ZERO_RETRY_BULK_IDENTITIES: (0,2), _IMMEDIATE_RX_BULK_IDENTITIES: (0,2), _SF7_REPLICATE_IDENTITIES: (0,2), _PUBLIC32_IDENTITIES: (0,2), _PUBLIC32_RETRY_IDENTITIES: (0,2), _PUBLIC32_SENDER_READY_IDENTITIES: (0,2), _PUBLIC32_COMPLETE_IDENTITIES: (0,2), _ENROLLED_ZERO_IDENTITIES: (0,), _PARTIAL_BUNDLE_IDENTITIES: (0,2), _V2_PARTIAL_BUNDLE_IDENTITIES: (0,2),_V2_FAILED_BUNDLE_IDENTITIES: (0,2),_V3_PARTIAL_BUNDLE_IDENTITIES: (0,2),_V3_CHECKPOINT_BUNDLE_IDENTITIES: (0,2),_V3_VERIFIED_BUNDLE_IDENTITIES: (0,2),_SF5_STARTUP_IDENTITIES: (0,1),_SF5_COMPLETED_IDENTITIES: (0,2),_PARTIAL_BATCH_IDENTITIES: (0,4),_COMPLETED_BATCH_IDENTITIES: (0,6),_RX_PRIORITY_BATCH_IDENTITIES: (0,6),_EARLY_RX_PARTIAL_IDENTITIES: (0,4),_GFSK_COMPLETE_IDENTITIES: (0,6),_GFSK_BUNDLE_IDENTITIES:(0,10),_GFSK_PARTIAL_BUNDLE_IDENTITIES:(0,2)}
_CAPTURE_FILES = frozenset(('capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json'))
_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


class FenceError(RuntimeError):
    """Protection or historical change check failed; no admission implication."""


def _need(value, reason):
    if not value:
        raise FenceError(reason)


def _relative(value, *, root=False):
    _need(type(value) is str, 'invalid_relative_path')
    if root and value == '':
        return value
    parts = value.split('/')
    _need(all(p not in ('', '.', '..') and re.fullmatch(r'[A-Za-z0-9_.-]+', p)
              for p in parts), 'invalid_relative_path')
    return value


def _stamp(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


class HistoricalMetadataFence:
    """Retained FD fence with explicit caller expectations, no snapshot import.

    expected_directories maps relative directory paths (root is '') to EXACT
    entry names. Opaque key/payload names may be listed but are never opened.
    expected_files maps allowlisted relative metadata files to verified SHA256s.
    new_own_namespaces is an explicit tuple of parents/<id> or runs/<id> additions;
    validate_actual_relation(tuple_of_present_paths) must independently inspect
    actual relations, return exactly True, and run on EVERY check with additions.
    This callback's result only permits namespace growth, never native admission.
    A previously observed addition cannot disappear. No current files are locked.
    """
    def __init__(self, root, *, owner_uid, expected_files, expected_directories,
                 new_own_namespaces=(), validate_actual_relation=None):
        self._fds, self._dirs, self._files, self._own_fds, self._tokens = {}, {}, {}, {}, {}
        self._closed, self._failed = False, False
        self._owner = (os.getpid(), threading.get_ident())
        try:
            _need(type(owner_uid) is int and owner_uid == os.geteuid(), 'wrong_owner_uid')
            self._uid = owner_uid
            self._root = Path(os.path.abspath(root))
            _need(self._root.resolve(strict=True) == self._root, 'symlink_root_ancestry')
            self._entries = {}
            for path, entries in expected_directories.items():
                path = _relative(path, root=True)
                _need(path in ('', 'parents', 'runs', *_CAPTURE_IDENTITY_DIRECTORIES, *_IDENTITY_COUNTS)
                      or re.fullmatch(r'(parents|runs)/[^/]+', path),
                      'outside_historical_metadata_namespace')
                names = tuple(entries)
                if path in _CAPTURE_IDENTITY_DIRECTORIES:
                    _need(not names, 'retained_capture_prepared_namespace_not_empty')
                if path in _IDENTITY_COUNTS:
                    counts=_IDENTITY_COUNTS[path]
                    allowed=counts if type(counts) is tuple else ((counts,)
                        if path in (_BULK_PAID_IDENTITIES,_MIXED_PAID_IDENTITIES) else (0,counts))
                    _need(len(names) in allowed and all(re.fullmatch(r'[0-9a-f]{32}\.json', n)
                          and int(n[:-5],16) != 0 for n in names),
                          'invalid_historical_prepared_identity_name')
                _need(len(names) == len(set(names)) and all(
                    _relative(n) == n and '/' not in n for n in names), 'invalid_entry_set')
                _need(not any(n.endswith(('-wal', '-shm', '-journal')) for n in names), 'sqlite_sidecar')
                self._entries[path] = frozenset(names)
            _need('' in self._entries, 'missing_root_entries')
            self._hashes = dict(expected_files)
            _need(not self._hashes.keys() & self._entries.keys(), 'file_directory_collision')
            for parent, names in self._entries.items():
                _need(all((parent+'/' if parent else '')+n in self._hashes
                          for n in names if n.endswith('.owner')), 'unverified_owner_lock')
            for path, digest in self._hashes.items():
                _relative(path)
                parent, _, name = path.rpartition('/')
                identity = parent in _IDENTITY_COUNTS and re.fullmatch(r'[0-9a-f]{32}\.json', name)
                _need(name in _FILES or identity and int(name[:-5],16) != 0, 'prohibited_metadata_file')
                if path.split('/')[-1] in _CAPTURE_FILES:
                    _need(parent in (_CAPTURE_DIRECTORY, _UNADMITTED_DIRECTORY, _MB1_UNADMITTED_DIRECTORY, _PAID_PREPARED_DIRECTORY, _COMPLETE_PAID_DIRECTORY,
                                     _BULK_PAID_DIRECTORY, _MIXED_PAID_DIRECTORY, _SUCCESS_PAID_DIRECTORY, _PARTIAL_PAID_DIRECTORY, _ACCEPTED_BULK_DIRECTORY, _REVERSE_BULK_DIRECTORY, _SMALL_BULK_DIRECTORY, _ZERO_RETRY_BULK_DIRECTORY, _IMMEDIATE_RX_BULK_DIRECTORY, _SF7_REPLICATE_DIRECTORY, _PUBLIC32_DIRECTORY, _PUBLIC32_RETRY_DIRECTORY, _PUBLIC32_SENDER_READY_DIRECTORY, _PUBLIC32_COMPLETE_DIRECTORY, _ENROLLED_ZERO_DIRECTORY, _PARTIAL_BUNDLE_DIRECTORY, _V2_PARTIAL_BUNDLE_DIRECTORY, _V2_FAILED_BUNDLE_DIRECTORY, _V3_PARTIAL_BUNDLE_DIRECTORY, _V3_CHECKPOINT_BUNDLE_DIRECTORY, _V3_VERIFIED_BUNDLE_DIRECTORY, _SF5_STARTUP_DIRECTORY, _SF5_COMPLETED_DIRECTORY, _PARTIAL_BATCH_DIRECTORY, _COMPLETED_BATCH_DIRECTORY, _RX_PRIORITY_BATCH_DIRECTORY, _EARLY_RX_PARTIAL_DIRECTORY, _GFSK_COMPLETE_DIRECTORY,_GFSK_BUNDLE_DIRECTORY,_GFSK_PARTIAL_BUNDLE_DIRECTORY),
                          'outside_retained_capture_metadata')
                _need(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest), 'invalid_sha256')
                _need(parent in self._entries and name in self._entries[parent], 'unscoped_file')
            for identity_directory in _IDENTITY_COUNTS:
                if identity_directory in self._entries:
                    _need(all(identity_directory+'/'+name in self._hashes for name in self._entries[identity_directory]),
                          'unverified_historical_prepared_identity')
            self._own = tuple(new_own_namespaces)
            _need(len(self._own) == len(set(self._own)), 'duplicate_own_namespace')
            for path in self._own:
                _relative(path)
                parent, _, name = path.rpartition('/')
                _need(parent in ('parents', 'runs') and parent in self._entries
                      and name not in self._entries[parent] and '/' not in name,
                      'invalid_own_namespace')
            _need(not self._own or callable(validate_actual_relation), 'missing_actual_relation_check')
            self._relation = validate_actual_relation
            self._seen = frozenset()
            for path in sorted(self._entries, key=lambda p: (p.count('/'), len(p))):
                parent, _, name = path.rpartition('/')
                if path:
                    _need(parent in self._dirs and name in self._entries[parent], 'unscoped_directory')
                    fd = os.open(name, _FLAGS | os.O_DIRECTORY, dir_fd=self._fds[parent])
                else:
                    fd = os.open(self._root, _FLAGS | os.O_DIRECTORY)
                self._fds[path] = fd
                self._dirs[path] = _stamp(self._protected(fd, directory=True))
                self._mark(fd)
            for path in sorted(self._hashes):
                parent, _, name = path.rpartition('/')
                fd = os.open(name, _FLAGS, dir_fd=self._fds[parent])
                self._fds[path] = fd
                stamp = _stamp(self._protected(fd))
                if name.endswith('.owner'):
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                self._mark(fd)
                self._files[path] = stamp
            self._refs = tuple(self._fds.items())
            self.check()
        except Exception as error:
            self.close()
            if not isinstance(error, FenceError):
                raise FenceError('filesystem_setup_failed') from error
            raise
        except BaseException:
            self.close()
            raise

    def _protected(self, fd, *, directory=False):
        s = os.fstat(fd)
        _need(s.st_uid == self._uid and stat.S_IMODE(s.st_mode) == (0o700 if directory else 0o600)
              and (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)), 'unsafe_metadata')
        if not directory:
            _need(s.st_nlink == 1 and 0 <= s.st_size <= MAX_FILE, 'unsafe_file_links_or_size')
        _need(fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC, 'fd_not_cloexec')
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        _need(flags & os.O_NONBLOCK and flags & os.O_ACCMODE == os.O_RDONLY, 'unsafe_fd_flags')
        return s

    def _mark(self, fd):
        token = MAX_FILE + fd + 1
        self._tokens[fd] = (_stamp(os.fstat(fd))[:2], token)
        os.lseek(fd, token, os.SEEK_SET)

    def _offset(self, fd):
        _need(os.lseek(fd, 0, os.SEEK_CUR) == self._tokens[fd][1], 'fd_reused_or_offset_changed')

    def _named(self, path):
        if not path:
            _need(self._root.resolve(strict=True) == self._root, 'root_ancestry_changed')
            return os.stat(self._root, follow_symlinks=False)
        parent, _, name = path.rpartition('/')
        return os.stat(name, dir_fd=self._fds[parent], follow_symlinks=False)

    def _scan(self):
        additions, updates, own_stamps = set(), {}, {}
        for path, stamp in self._dirs.items():
            actual = _stamp(self._protected(self._fds[path], directory=True))
            _need(actual == _stamp(self._named(path)), 'directory_replaced')
            possible = {p.rpartition('/')[2] for p in self._own if p.rpartition('/')[0] == path}
            self._offset(self._fds[path])
            os.lseek(self._fds[path], 0, os.SEEK_SET)
            names = frozenset(os.listdir(self._fds[path]))
            os.lseek(self._fds[path], self._tokens[self._fds[path]][1], os.SEEK_SET)
            extras = names - self._entries[path]
            _need(self._entries[path] <= names and extras <= possible, 'historical_namespace_changed')
            if possible:
                _need(actual[:4] == stamp[:4], 'directory_protection_changed')
                growth = {path + '/' + name for name in extras} - self._seen
                _need(bool(growth) or actual == stamp, 'directory_metadata_changed')
                if growth:
                    updates[path] = actual
            else:
                _need(actual == stamp, 'directory_metadata_changed')
            additions.update(path + '/' + name for name in extras)
        _need(self._seen <= additions, 'own_namespace_removed')
        for path in additions:
            parent, _, name = path.rpartition('/')
            if path not in self._own_fds:
                fd = os.open(name, _FLAGS | os.O_DIRECTORY, dir_fd=self._fds[parent])
                self._own_fds[path] = fd; self._mark(fd)
            fd = self._own_fds[path]; self._offset(fd)
            own_stamps[path] = _stamp(self._protected(fd, directory=True))
            _need(own_stamps[path] == _stamp(self._named(path)), 'own_directory_replaced')
        for path, stamp in self._files.items():
            fd = self._fds[path]
            _need(_stamp(self._protected(fd)) == stamp == _stamp(self._named(path)), 'historical_file_changed')
            self._offset(fd)
            if path.endswith('.owner'):
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            raw = os.pread(fd, MAX_FILE + 1, 0)
            _need(len(raw) == stamp[5] and hashlib.sha256(raw).hexdigest() == self._hashes[path],
                  'historical_bytes_changed')
            _need(_stamp(self._protected(fd)) == stamp == _stamp(self._named(path)), 'file_changed_during_hash')
        return frozenset(additions), updates, own_stamps

    def check(self):
        """Success returns None; failures poison the detector until closed."""
        try:
            _need(not self._closed and not self._failed and self._uid == os.geteuid()
                  and self._owner == (os.getpid(), threading.get_ident()), 'fence_unavailable')
            _need(tuple(self._fds.items()) == self._refs, 'fd_registry_changed')
            present, updates, own_stamps = self._scan()
            if present:
                _need(self._relation(tuple(sorted(present))) is True, 'actual_relation_refused')
            _need(tuple(self._fds.items()) == self._refs, 'fd_registry_changed')
            _need(self._scan() == (present, updates, own_stamps), 'namespace_changed_during_callback')
            self._dirs.update(updates)
            self._seen = present
        except BaseException as error:
            self._failed = True
            if isinstance(error, FenceError) or not isinstance(error, Exception):
                raise
            raise FenceError('filesystem_or_relation_check_failed') from error

    def around(self, callback, *args, **kwargs):
        """Check before/after a caller operation, including operations that raise."""
        self.check()
        try:
            return callback(*args, **kwargs)
        finally:
            self.check()

    def close(self):
        self._closed = True
        for fd in set(self._fds.values()) | set(self._own_fds.values()):
            try:
                if fd in self._tokens:
                    identity, token = self._tokens[fd]
                    if _stamp(os.fstat(fd))[:2] != identity or os.lseek(fd, 0, os.SEEK_CUR) != token:
                        continue  # Never close an unrelated recycled caller descriptor.
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
