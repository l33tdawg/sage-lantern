# SPDX-License-Identifier: Apache-2.0
"""PRIVATE cross-process historical change detector; never native authority.

Capture only after independent actual lineage/expiry verification. Pin returned
SHA256 into separately protected own records; a digest is integrity, not a
signature or approval. Callers must derive current own paths AND directory
identities from actual protected ledger/provision relations, never a request DTO.
No SQL interpretation, writes, key/payload access, enrollment or ARM occurs here.
"""
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from .history_metadata_fence import HistoricalMetadataFence, FenceError, _need, _stamp, _relative, _FLAGS

MAX_SEAL = 2 * 1024 * 1024
# H48 retains 2,059 files on C; reserve one finite 256-file capacity block.
MAX_FILES, MAX_DIRS, MAX_ENTRIES = 2304, 512, 4096
MAX_TOTAL_BYTES = 256 * 1024 * 1024
SCHEMA = 'meshticulum.private-history-identity-seal.v2'


class _Ancestry:
    """Held openat chain; no symlink or writable/foreign ancestor exceptions."""
    def __init__(self, path, uid):
        self.fds, self.records = [], []
        try:
            _need(type(uid) is int and uid == os.geteuid(), 'wrong_owner_uid')
            path = Path(os.path.abspath(path)); self.uid = uid
            names = ('/', *path.parts[1:])
            _need(len(names) <= 64 and len(str(path)) <= 4096, 'ancestry_too_long')
            for name in names:
                fd = os.open(name, _FLAGS | os.O_DIRECTORY,
                             dir_fd=self.fds[-1] if self.fds else None)
                self.fds.append(fd); s = os.fstat(fd)
                _need(s.st_uid in (0, uid) and not stat.S_IMODE(s.st_mode) & 0o022,
                      'writable_or_foreign_ancestor')
                self.records.append([name, list(_stamp(s)[:4])])
            s = os.fstat(self.fds[-1])
            _need(s.st_uid == uid and stat.S_IMODE(s.st_mode) == 0o700, 'unsafe_private_leaf')
            self.check()
        except BaseException:
            self.close(); raise

    def check(self):
        for index, (name, identity) in enumerate(self.records):
            s = os.fstat(self.fds[index])
            named = os.stat(name, dir_fd=self.fds[index-1] if index else None, follow_symlinks=False)
            _need(list(_stamp(s)[:4]) == identity == list(_stamp(named)[:4]), 'ancestry_changed')

    def close(self):
        for fd in reversed(self.fds):
            try: os.close(fd)
            except OSError: pass
        self.fds.clear()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def _bounded(files, directories):
    _need(type(files) is dict and type(directories) is dict, 'invalid_expectations')
    _need(len(files) <= MAX_FILES and len(directories) <= MAX_DIRS, 'too_many_paths')
    _need(sum(len(v) for v in directories.values()) <= MAX_ENTRIES, 'too_many_entries')
    _need(all(type(p) is str and len(p) <= 512 for p in (*files, *directories)), 'path_too_long')


def _json(raw):
    _need(type(raw) is bytes and len(raw) <= MAX_SEAL, 'invalid_seal_bytes')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            _need(key not in out, 'duplicate_json_key')
            out[key] = value
        return out
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise FenceError('invalid_seal_json') from error


def capture(root, *, owner_uid, expected_files, expected_directories):
    """Return (public-metadata-only seal bytes, SHA256), with no filesystem write.

    Expected hashes and exact namespaces must be independently verified BEFORE
    capture. No current own additions are permitted while capturing the baseline.
    """
    _bounded(expected_files, expected_directories)
    with _Ancestry(root, owner_uid) as chain, HistoricalMetadataFence(root, owner_uid=owner_uid,
            expected_files=expected_files, expected_directories=expected_directories) as fence:
        _need(_stamp(os.fstat(fence._fds['']))[:4] == tuple(chain.records[-1][1]), 'root_chain_mismatch')
        _need(sum(s[5] for s in fence._files.values()) <= MAX_TOTAL_BYTES, 'metadata_too_large')
        data = dict(schema=SCHEMA, uid=owner_uid, ancestry=chain.records,
            files={p: dict(stamp=list(fence._files[p]), sha256=expected_files[p])
                   for p in sorted(expected_files)},
            directories={p: dict(stamp=list(fence._dirs[p]), entries=sorted(fence._entries[p]))
                         for p in sorted(expected_directories)})
        fence.check()
        chain.check()
        raw = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
        _need(len(raw) <= MAX_SEAL, 'seal_too_large')
        return raw, hashlib.sha256(raw).hexdigest()


def read_seal(path, *, owner_uid, expected_sha256):
    """Read an already protected manifest; this module never installs it."""
    path = Path(os.path.abspath(path))
    _need(path.name == 'history-identity-seal.json', 'unsafe_seal_path')
    _need(type(owner_uid) is int and owner_uid == os.geteuid(), 'wrong_owner_uid')
    chain = _Ancestry(path.parent, owner_uid); parent = chain.fds[-1]
    fd = None
    try:
        chain.check()
        fd = os.open(path.name, _FLAGS, dir_fd=parent)
        s = os.fstat(fd)
        _need(stat.S_ISREG(s.st_mode) and s.st_uid == owner_uid and s.st_nlink == 1
              and stat.S_IMODE(s.st_mode) == 0o600 and s.st_size <= MAX_SEAL, 'unsafe_seal_file')
        raw = os.pread(fd, MAX_SEAL + 1, 0)
        _need(len(raw) == s.st_size and _stamp(os.fstat(fd)) == _stamp(s)
              == _stamp(os.stat(path.name, dir_fd=parent, follow_symlinks=False)), 'seal_changed_during_read')
        chain.check()
        _need(hashlib.sha256(raw).hexdigest() == expected_sha256, 'seal_digest_mismatch')
        return raw
    finally:
        if fd is not None:
            os.close(fd)
        chain.close()


class SealedHistory:
    """Reopen immutable old expectations across RPCs, then hold FDs until close.

    own_directories maps PRESENT parents/<id>/runs/<id> paths to fixed
    (dev, ino, mode, uid) identities derived from actual own protected records.
    The exact True relation callback checks the sorted path tuple before/after
    every scan. It must verify current actual relations; this class cannot prove
    a caller supplied an actual-ledger-derived identity. No additions/removals
    during this instance are allowed. Reopen with independently observed current
    own paths next RPC, retaining the same immutable seal and pinned digest.
    """
    def __init__(self, root, *, owner_uid, seal_bytes, expected_sha256,
                 own_directories=None, validate_actual_relation=None):
        self._fence, self._own_fds, self._chain = None, {}, None
        self._failed, self._closed = False, False
        try:
            _need(type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}', expected_sha256),
                  'invalid_seal_digest')
            _need(type(seal_bytes) is bytes and len(seal_bytes) <= MAX_SEAL
                  and hashlib.sha256(seal_bytes).hexdigest() == expected_sha256, 'seal_digest_mismatch')
            data = _json(seal_bytes)
            _need(type(data) is dict and set(data) == {'schema', 'uid', 'files', 'directories', 'ancestry'}
                  and data['schema'] == SCHEMA and type(data['uid']) is int
                  and data['uid'] == owner_uid == os.geteuid(), 'invalid_seal_header')
            _bounded(data['files'], data['directories'])
            for group, keys in ((data['files'], {'stamp', 'sha256'}),
                                (data['directories'], {'stamp', 'entries'})):
                for record in group.values():
                    _need(type(record) is dict and set(record) == keys, 'invalid_seal_record')
                    s = record['stamp']
                    _need(type(s) is list and len(s) == 8
                          and all(type(n) is int and n >= 0 for n in s), 'invalid_seal_stamp')
            _need(sum(v['stamp'][5] for v in data['files'].values()) <= MAX_TOTAL_BYTES, 'metadata_too_large')
            self._data, self._uid = data, owner_uid
            self._own = dict(own_directories or {})
            _need(len(self._own) <= MAX_ENTRIES, 'too_many_own_paths')
            self._relation = validate_actual_relation
            _need(not self._own or callable(self._relation), 'missing_actual_relation_check')
            directories = {}
            for path, record in data['directories'].items():
                _need(type(record['entries']) is list, 'invalid_seal_entries')
                directories[path] = list(record['entries'])
            for path, identity in self._own.items():
                _relative(path)
                parent, _, name = path.rpartition('/')
                _need(parent in ('parents', 'runs') and parent in directories
                      and name not in directories[parent], 'invalid_own_path')
                _need(type(identity) in (tuple, list) and len(identity) == 4
                      and all(type(n) is int and n >= 0 for n in identity), 'invalid_own_identity')
                self._own[path] = tuple(identity)
                directories[parent].append(name)
            _bounded(data['files'], directories)
            self._paths = tuple(sorted(self._own))
            self._chain = _Ancestry(root, owner_uid)
            _need(self._chain.records == data['ancestry'], 'sealed_ancestry_changed')
            self._call_relation()
            self._fence = HistoricalMetadataFence(root, owner_uid=owner_uid,
                expected_files={p: v['sha256'] for p, v in data['files'].items()},
                expected_directories=directories)
            _need(_stamp(os.fstat(self._fence._fds['']))[:4] == tuple(self._chain.records[-1][1]),
                  'root_chain_mismatch')
            for path in self._paths:
                parent, _, name = path.rpartition('/')
                fd = os.open(name, _FLAGS | os.O_DIRECTORY, dir_fd=self._fence._fds[parent])
                self._own_fds[path] = fd
                self._fence._mark(fd)
            self.check()
        except BaseException as error:
            self.close()
            if isinstance(error, Exception) and not isinstance(error, FenceError):
                raise FenceError('seal_setup_failed') from error
            raise

    def _call_relation(self):
        if self._own:
            _need(self._relation(self._paths) is True, 'actual_relation_refused')

    def _scan(self):
        self._chain.check()
        self._fence.check()
        for path, record in self._data['files'].items():
            _need(self._fence._files[path] == tuple(record['stamp']), 'sealed_file_identity_changed')
        for path, record in self._data['directories'].items():
            actual = _stamp(self._fence._protected(self._fence._fds[path], directory=True))
            sealed = tuple(record['stamp'])
            growth = any(p.rpartition('/')[0] == path for p in self._paths)
            _need(actual[:4] == sealed[:4] if growth else actual == sealed,
                  'sealed_directory_identity_changed')
        identities = {}
        for path, fd in self._own_fds.items():
            self._fence._offset(fd)
            actual = _stamp(self._fence._protected(fd, directory=True))
            _need(actual == _stamp(self._fence._named(path))
                  and actual[:4] == self._own[path], 'own_directory_rebound')
            identities[path] = actual
        self._chain.check()
        return identities

    def check(self):
        """Return None; never refresh the original seal after detected drift."""
        try:
            _need(not self._failed and not self._closed, 'seal_unavailable')
            self._call_relation()
            before = self._scan()
            self._call_relation()
            _need(self._scan() == before, 'own_namespace_changed_during_callback')
        except BaseException as error:
            self._failed = True
            if isinstance(error, Exception) and not isinstance(error, FenceError):
                raise FenceError('seal_check_failed') from error
            raise

    def around(self, callback, *args, **kwargs):
        self.check()
        try:
            return callback(*args, **kwargs)
        finally:
            self.check()

    def close(self):
        self._closed = True
        for fd in self._own_fds.values():
            try:
                identity, offset = self._fence._tokens[fd]
                if _stamp(os.fstat(fd))[:2] != identity or os.lseek(fd, 0, os.SEEK_CUR) != offset:
                    continue
                os.close(fd)
            except OSError:
                pass
        self._own_fds.clear()
        if self._fence is not None:
            self._fence.close()
        if self._chain is not None:
            self._chain.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
