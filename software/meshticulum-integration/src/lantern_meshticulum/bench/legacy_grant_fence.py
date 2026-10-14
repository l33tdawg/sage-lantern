# SPDX-License-Identifier: Apache-2.0
"""PRIVATE expired legacy metadata fence, never admission or database evidence.

All expected rows must already be independently verified. Full original caps
remain charged; actual cost and SQLite consistency remain UNKNOWN. Only grant
and active bytes are read. Existing *.owner locks are opened/locked, never read.
All other regular entries, including SQLite/sidecars/keys/payloads, are lstat-only.
The required outer namespace gate must check strict current histories and actual
prepared relations. Its True result is not approval; no new-run DTO is accepted.
"""
import fcntl
import hashlib
import json
import os
import re
import stat
import threading
import time
from pathlib import Path

from .history_metadata_fence import FenceError, _need, _stamp, _FLAGS
from .pilot_ip_schema import IP_SCHEMAS, IP_V2, ip_metadata_grant

SCHEMA = 'meshticulum.private-legacy-grant-fence.v1'
MAX_RUNS, MAX_ENTRIES, MAX_GRANT, MAX_SEAL = 128, 4096, 16384, 2 * 1024 * 1024
CAPS = {'radio_attempts', 'radio_estimated_airtime_us', 'ip_frames',
        'tx_stream_bytes', 'rx_stream_bytes'}
ROW_KEYS = {'run_id', 'schema', 'grant_sha256', 'host_boot_id', 'caps',
            'original_issued_epoch_us', 'original_expires_epoch_us', 'active_marker'}
OWNERS = {name + '.owner' for name in
          ('attempts.sqlite', 'bytes.sqlite', 'wire.sqlite', 'native.sqlite', 'caps.sqlite')}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _json(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            _need(key not in out, 'duplicate_json_key'); out[key] = value
        return out
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise FenceError('invalid_json') from error


def _rows(rows):
    _need(type(rows) in (tuple, list) and 0 < len(rows) <= MAX_RUNS, 'invalid_rows')
    result = {}
    for source in rows:
        _need(type(source) is dict and ROW_KEYS <= source.keys(), 'missing_row_metadata')
        row = {key: source[key] for key in ROW_KEYS}
        sid = row['run_id']
        _need(type(sid) is str and re.fullmatch('[0-9a-f]{32}', sid) and sid not in result,
              'invalid_or_duplicate_run')
        _need(type(row['grant_sha256']) is str and
              re.fullmatch('[0-9a-f]{64}', row['grant_sha256']), 'invalid_grant_hash')
        _need(type(row['host_boot_id']) is str and len(row['host_boot_id']) <= 128
              and bool(row['host_boot_id']), 'invalid_boot')
        _need(row['active_marker'] in ('active', 'retired', 'revoked'), 'invalid_marker')
        _need(type(row['caps']) is dict and set(row['caps']) == CAPS and all(
              type(n) is int and 0 <= n < 2**63 for n in row['caps'].values()), 'invalid_caps')
        _need(all(type(row[key]) is int and 0 <= row[key] < 2**63 for key in
              ('original_issued_epoch_us', 'original_expires_epoch_us')) and
              row['original_issued_epoch_us'] < row['original_expires_epoch_us'], 'invalid_epoch')
        result[sid] = _json(_canonical(row))
    return result


class _Chain:
    def __init__(self, root, uid):
        self.fds, self.records, self.tokens = [], [], []
        path = Path(os.path.abspath(root))
        _need(len(path.parts) <= 64 and len(str(path)) <= 4096, 'ancestry_too_long')
        try:
            for name in ('/', *path.parts[1:]):
                fd = os.open(name, _FLAGS | os.O_DIRECTORY,
                             dir_fd=self.fds[-1] if self.fds else None)
                self.fds.append(fd); s = os.fstat(fd)
                self.records.append([name, list(_stamp(s)[:4])])
                token = MAX_SEAL + fd + 1; self.tokens.append(token)
                os.lseek(fd, token, os.SEEK_SET)
                _need(stat.S_ISDIR(s.st_mode) and s.st_uid in (0, uid)
                      and not stat.S_IMODE(s.st_mode) & 0o022, 'unsafe_ancestor')
            s = os.fstat(self.fds[-1])
            _need(s.st_uid == uid and stat.S_IMODE(s.st_mode) == 0o700, 'unsafe_runs_root')
            self.check()
        except BaseException:
            self.close(); raise

    def check(self):
        for i, (name, identity) in enumerate(self.records):
            named = os.stat(name, dir_fd=self.fds[i-1] if i else None, follow_symlinks=False)
            _need(list(_stamp(os.fstat(self.fds[i]))[:4]) == identity == list(_stamp(named)[:4]),
                  'ancestry_changed')
            _need(os.lseek(self.fds[i], 0, os.SEEK_CUR) == self.tokens[i], 'ancestry_fd_changed')

    def close(self):
        for i in reversed(range(len(self.fds))):
            try:
                fd = self.fds[i]
                if (i < len(self.records) and list(_stamp(os.fstat(fd))[:4]) == self.records[i][1]
                        and os.lseek(fd, 0, os.SEEK_CUR) == self.tokens[i]): os.close(fd)
            except OSError: pass
        self.fds.clear()


class LegacyGrantFence:
    """Retained descriptors plus optional immutable cross-process baseline.

    Seal SHA must be pinned separately in protected current records. Neither a
    hash nor independently_verified=True proves provenance: caller must establish
    it using the actual independent inventory before capture. Success returns
    None. No refunds, expiry changes, usage totals or eligibility are produced.
    Root additions are solely the mandatory outer gate's responsibility; exact
    original leaf namespaces never permit additions. A clock scope is <=60s.
    """
    def __init__(self, root, *, owner_uid, expected_rows, independently_verified,
                 exact_namespace_gate, seal_bytes=None, expected_sha256=None,
                 max_seconds=30):
        self._chain, self._fds, self._tokens = None, {}, {}
        self._closed, self._failed = False, False
        self._owner = (os.getpid(), threading.get_ident())
        try:
            _need(type(owner_uid) is int and owner_uid == os.geteuid(), 'wrong_owner')
            _need(independently_verified is True, 'rows_not_independently_verified')
            _need(callable(exact_namespace_gate), 'missing_outer_namespace_gate')
            _need(type(max_seconds) in (int, float) and 0 < max_seconds <= 60, 'invalid_clock_scope')
            self._uid, self._gate = owner_uid, exact_namespace_gate
            self._start_epoch = time.time_ns() // 1000
            self._start_mono = time.monotonic_ns()
            self._last_epoch, self._last_mono = self._start_epoch, self._start_mono
            self._end_mono = self._start_mono + int(max_seconds * 1e9)
            self._rows = _rows(expected_rows)
            self._chain = _Chain(root, owner_uid)
            self._baseline = None
            if seal_bytes is not None:
                _need(type(seal_bytes) is bytes and len(seal_bytes) <= MAX_SEAL
                      and type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}', expected_sha256)
                      and hashlib.sha256(seal_bytes).hexdigest() == expected_sha256, 'seal_digest_mismatch')
                data = _json(seal_bytes)
                _need(type(data) is dict and set(data) == {'schema', 'uid', 'ancestry', 'rows', 'leaves'}
                      and data['schema'] == SCHEMA and data['uid'] == owner_uid
                      and data['rows'] == self._rows and data['ancestry'] == self._chain.records,
                      'seal_binding_mismatch')
                self._baseline = data
            self._outer()
            for sid in sorted(self._rows):
                self._open(sid, directory=True)
                names = self._names(sid)
                _need({'grant.json', 'active'} <= set(names), 'missing_grant_or_marker')
                for name in sorted({'grant.json', 'active'} | (set(names) & OWNERS)):
                    self._open(sid+'/'+name)
            self._refs = tuple(self._fds.items())
            observed = self._scan()
            if self._baseline is None:
                self._baseline = dict(schema=SCHEMA, uid=owner_uid, ancestry=self._chain.records,
                                      rows=self._rows, leaves=observed)
            else:
                _need(observed == self._baseline['leaves'], 'sealed_legacy_identity_changed')
            self.check()
        except BaseException as error:
            self.close()
            if isinstance(error, Exception) and not isinstance(error, FenceError):
                raise FenceError('legacy_setup_failed') from error
            raise

    def _clock(self):
        epoch, mono = time.time_ns() // 1000, time.monotonic_ns()
        _need(self._last_epoch <= epoch and self._last_mono <= mono < self._end_mono,
              'clock_scope_invalid')
        self._last_epoch, self._last_mono = epoch, mono
        return epoch

    def _outer(self):
        self._clock(); self._chain.check()
        _need(self._gate() is True, 'outer_namespace_refused')
        self._chain.check(); self._clock()

    def _protected(self, s, directory=False):
        _need(s.st_uid == self._uid and stat.S_IMODE(s.st_mode) == (0o700 if directory else 0o600)
              and (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)), 'unsafe_entry')
        _need(directory or s.st_nlink == 1, 'unsafe_file_links')
        return list(_stamp(s))

    def _named(self, path):
        if '/' in path:
            sid, name = path.split('/')
            return os.stat(name, dir_fd=self._fds[sid], follow_symlinks=False)
        return os.stat(path, dir_fd=self._chain.fds[-1], follow_symlinks=False)

    def _open(self, path, directory=False):
        s = self._named(path); self._protected(s, directory)
        parent, name = path.split('/') if '/' in path else (None, path)
        fd = os.open(name, _FLAGS | (os.O_DIRECTORY if directory else 0),
                     dir_fd=self._fds[parent] if parent else self._chain.fds[-1])
        self._fds[path] = fd
        _need(_stamp(s) == _stamp(os.fstat(fd)), 'entry_rebound_during_open')
        if not directory and (name == 'active' or name in OWNERS):
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        token = MAX_SEAL + fd + 1
        os.lseek(fd, token, os.SEEK_SET)
        self._tokens[path] = (_stamp(s)[:2], token)

    def _names(self, sid):
        fd = self._fds[sid]; token = self._tokens[sid][1]
        _need(os.lseek(fd, 0, os.SEEK_CUR) == token, 'held_fd_changed')
        os.lseek(fd, 0, os.SEEK_SET)
        names = []
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    _need(len(names) < MAX_ENTRIES, 'too_many_leaf_entries')
                    _need(type(entry.name) is str and re.fullmatch('[A-Za-z0-9_.-]{1,128}', entry.name),
                          'invalid_leaf_namespace')
                    names.append(entry.name)
        finally: os.lseek(fd, token, os.SEEK_SET)
        return sorted(names)

    def _read(self, sid, name, limit):
        fd = self._fds[sid+'/'+name]; before = os.fstat(fd)
        _need(0 <= before.st_size <= limit, 'metadata_too_large')
        raw = os.pread(fd, limit+1, 0)
        _need(len(raw) == before.st_size and _stamp(before) == _stamp(os.fstat(fd))
              == _stamp(self._named(sid+'/'+name)), 'metadata_changed_during_read')
        return raw

    def _grant(self, sid):
        row = self._rows[sid]; raw = self._read(sid, 'grant.json', MAX_GRANT); g = _json(raw)
        _need(hashlib.sha256(raw).hexdigest() == row['grant_sha256'] and
              type(g) is dict and g['schema'] == row['schema'] and
              g['host_boot_id'] == row['host_boot_id'] and
              g['issued_epoch_us'] == row['original_issued_epoch_us'] and
              g['expires_epoch_us'] == row['original_expires_epoch_us'] < self._clock(),
              'grant_binding_or_expiry_changed')
        if g['schema'] in IP_SCHEMAS:
            if g['schema']==IP_V2:ip_metadata_grant(g)
            _need(g['session']['session_id'] == sid, 'grant_run_rebound')
            limits = g['limits']
            caps = dict(radio_attempts=0, radio_estimated_airtime_us=0,
                        ip_frames=limits['max_frames'], tx_stream_bytes=limits['max_tx_stream_bytes'],
                        rx_stream_bytes=limits['max_rx_stream_bytes'])
        else:
            _need(g['schema'] in ('meshticulum.bench-run.v1', 'meshticulum.service-run.v1')
                  and g['session_id'] == sid, 'grant_run_rebound')
            profile = g['profile']
            caps = dict(radio_attempts=profile['max_boot_attempts'],
                        radio_estimated_airtime_us=profile['max_boot_estimated_airtime_us'],
                        ip_frames=0, tx_stream_bytes=0, rx_stream_bytes=0)
        _need(all(type(n) is int for n in caps.values()) and caps == row['caps'], 'grant_full_caps_changed')
        _need(self._read(sid, 'active', 128) == (row['active_marker']+'\n').encode(), 'marker_changed')

    def _scan(self):
        self._clock(); self._chain.check(); observed = {}; count = 0
        for sid in sorted(self._rows):
            fd = self._fds[sid]
            leaf = self._protected(os.fstat(fd), True)
            _need(leaf == self._protected(self._named(sid), True), 'legacy_leaf_replaced')
            entries = {}
            for name in self._names(sid):
                entries[name] = self._protected(self._named(sid+'/'+name)); count += 1
                _need(count <= MAX_ENTRIES, 'too_many_entries')
            for path, held in self._fds.items():
                if path == sid or path.startswith(sid+'/'):
                    identity, token = self._tokens[path]
                    _need(_stamp(os.fstat(held))[:2] == identity
                          and os.lseek(held, 0, os.SEEK_CUR) == token, 'held_fd_changed')
                    flags = fcntl.fcntl(held, fcntl.F_GETFL)
                    _need(flags & os.O_ACCMODE == os.O_RDONLY and flags & os.O_NONBLOCK
                          and fcntl.fcntl(held, fcntl.F_GETFD) & fcntl.FD_CLOEXEC, 'unsafe_fd_flags')
                    _need(_stamp(os.fstat(held)) == _stamp(self._named(path)), 'held_entry_replaced')
                    if path.endswith('/active') or path.rpartition('/')[2] in OWNERS:
                        fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self._grant(sid)
            _need(leaf == self._protected(os.fstat(fd), True) and
                  entries == {n: self._protected(self._named(sid+'/'+n)) for n in self._names(sid)},
                  'leaf_changed_during_scan')
            observed[sid] = dict(stamp=leaf, entries=entries)
        self._chain.check(); self._clock()
        return observed

    def check(self):
        try:
            _need(not self._closed and not self._failed and self._uid == os.geteuid()
                  and self._owner == (os.getpid(), threading.get_ident()), 'legacy_fence_unavailable')
            _need(tuple(self._fds.items()) == self._refs, 'fd_registry_changed')
            self._outer(); before = self._scan(); self._outer()
            _need(before == self._scan() == self._baseline['leaves'], 'sealed_legacy_identity_changed')
        except BaseException as error:
            self._failed = True
            if isinstance(error, Exception) and not isinstance(error, FenceError):
                raise FenceError('legacy_check_failed') from error
            raise

    def seal(self):
        self.check(); raw = _canonical(self._baseline)
        _need(len(raw) <= MAX_SEAL, 'seal_too_large')
        return raw, hashlib.sha256(raw).hexdigest()

    def around(self, callback, *args, **kwargs):
        self.check()
        try: return callback(*args, **kwargs)
        finally: self.check()

    def close(self):
        self._closed = True
        for path, fd in reversed(tuple(self._fds.items())):
            try:
                identity, token = self._tokens.get(path, (_stamp(os.fstat(fd))[:2], None))
                if _stamp(os.fstat(fd))[:2] == identity and (token is None or
                        os.lseek(fd, 0, os.SEEK_CUR) == token): os.close(fd)
            except OSError: pass
        self._fds.clear()
        if self._chain is not None: self._chain.close()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def capture(root, **kwargs):
    """Return metadata seal bytes/digest without writing any filesystem content."""
    with LegacyGrantFence(root, **kwargs) as fence:
        return fence.seal()
