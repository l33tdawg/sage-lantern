# SPDX-License-Identifier: Apache-2.0
"""Read-only public-parent evidence; no enrollment, reset or debt adjustment.

Only fixed ledger basenames in an explicitly scoped protected directory are
read. Existing owner locks remain held through the final observation. SQLite
receives a bounded, query-only memory image, never a native database pathname.
Historical policies remain readable after expiry without granting authority.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import threading
import weakref

from meshticulum.bench.serial_protocol import RadioProfile
from .profiles import GFSK_NAME,GFSK_KIND,GFSK_BUNDLE_KIND,GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND,GFSK250_FAST_BUNDLE_KIND,profile_name_for_kind,profile_from_plan,profile_for,profile_record
from meshticulum.ip.wire import IpLimits
from .pilot_parent_caps import BUNDLE_KINDS, PUBLIC32_KINDS, Cost, ParentPolicy, DOMAIN as PARENT_DOMAIN
from . import pilot_debt_snapshot as debt
from .pilot_debt_snapshot import (SnapshotError, need, canonical, sha, integer,
    identifier, hex_value, boot, public_json, _identity, MAX_DB)

NATIVE_DOMAIN = b'meshticulum.native-parent-caps.v1\0'
MAX_ROWS = 4096
UNITS = ('unit-a', 'unit-b', 'unit-c')
_RESULTS = weakref.WeakKeyDictionary()


def _native_hash(value):
    return sha(NATIVE_DOMAIN + canonical(value))


@dataclass(frozen=True, slots=True)
class Scope:
    schema: str
    role: str
    owner_uid: int
    parent_id: str
    policy_digest: str
    database_sha256: str
    unit: str | None
    current_host_boot_id: str
    observation_expires_epoch_us: int

    def __post_init__(self):
        need(self.schema == 'meshticulum.public-parent-snapshot-scope.v1'
             and self.role in ('controller', 'native'), 'invalid_scope')
        integer(self.owner_uid, 0, 2**32-1)
        identifier(self.parent_id)
        hex_value(self.policy_digest, 64)
        hex_value(self.database_sha256, 64)
        boot(self.current_host_boot_id)
        integer(self.observation_expires_epoch_us, 1)
        need((self.role == 'native' and self.owner_uid == 0 and self.unit in UNITS)
             or (self.role == 'controller' and self.unit is None), 'invalid_scope')

    @classmethod
    def from_dict(cls, value):
        try:
            need(type(value) is dict and set(value) == set(cls.__dataclass_fields__))
            return cls(**value)
        except Exception:
            raise SnapshotError('invalid_scope') from None


class Snapshot:
    """In-process observation result, not a grant or a constructible proof DTO."""
    def __init__(self):
        raise TypeError('use collect')

    def to_dict(self):
        need(type(self) is Snapshot and self in _RESULTS, 'unobserved_report')
        return public_json(_RESULTS[self][0])


def _issue(body, rows):
    result = object.__new__(Snapshot)
    _RESULTS[result] = (canonical(body), canonical(rows))
    return result


class _Observation:
    def __init__(self, scope, clock, boot_reader):
        need(type(scope) is Scope and callable(clock) and callable(boot_reader), 'invalid_scope')
        self.scope, self.clock, self.boot_reader = scope, clock, boot_reader
        self.refs = (scope, clock, boot_reader)
        self.binding = canonical(asdict(scope))
        self.owner = os.getpid(), threading.get_ident()
        self.first = self.last = self.deadline = None

    def _owned(self):
        need(os.geteuid() == self.scope.owner_uid
             and self.owner == (os.getpid(), threading.get_ident())
             and all(a is b for a, b in zip((self.scope, self.clock, self.boot_reader), self.refs))
             and canonical(asdict(self.scope)) == self.binding, 'observation_owner_changed')

    def __call__(self):
        self._owned()
        observed_boot = self.boot_reader()
        self._owned()
        need(observed_boot == self.scope.current_host_boot_id, 'current_boot_changed')
        now = self.clock()
        self._owned()
        need(type(now) is tuple and len(now) == 2, 'invalid_clock')
        for value in now:
            integer(value, 1)
        observed_boot = self.boot_reader()
        self._owned()
        need(observed_boot == self.scope.current_host_boot_id, 'current_boot_changed')
        if self.first is None:
            need(0 < self.scope.observation_expires_epoch_us-now[0] <= 60000000,
                 'observation_expired_or_unbounded')
            self.first = now
            self.deadline = now[1] + self.scope.observation_expires_epoch_us-now[0]
            integer(self.deadline, 1)
        need(now[0] < self.scope.observation_expires_epoch_us and now[1] < self.deadline,
             'observation_expired')
        if self.last is not None:
            need(all(a >= b for a, b in zip(now, self.last)), 'observation_clock_regressed')
        self.last = now
        return now


class _Reader:
    def __init__(self, state, scope, observe):
        self.scope, self.observe = scope, observe
        self.dirs, self.files, self.absences = [], {}, set()
        self.name = 'native.sqlite' if scope.role == 'native' else 'caps.sqlite'
        self.allowed = {self.name, self.name+'.owner', self.name+'.pending'}
        if scope.role == 'native':
            self.allowed.add(self.name+'.approval')
        paths = (state, state/'parents', state/'parents'/scope.parent_id) if scope.role == 'native' else (state,)
        try:
            for path in paths:
                observe()
                need(path.resolve() == path, 'symlinked_state')
                fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    info = os.fstat(fd)
                    self._private(info, True)
                except BaseException:
                    os.close(fd)
                    raise
                self.dirs.append((path, fd, _identity(info)))
            self.dirfd = self.dirs[-1][1]
            self.check()
        except BaseException:
            self.close()
            raise

    def _private(self, value, directory=False):
        need((stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
             and value.st_uid == self.scope.owner_uid
             and stat.S_IMODE(value.st_mode) == (0o700 if directory else 0o600)
             and (directory or value.st_nlink == 1), 'unprotected_metadata')

    def check(self):
        for path, fd, ident in self.dirs:
            held, named = os.fstat(fd), path.lstat()
            self._private(held, True)
            self._private(named, True)
            need(_identity(held)[:4] == ident[:4] == _identity(named)[:4], 'directory_changed')
        for name, (fd, ident, raw) in self.files.items():
            held, named = os.fstat(fd), os.stat(name, dir_fd=self.dirfd, follow_symlinks=False)
            self._private(held)
            self._private(named)
            need(_identity(held) == ident == _identity(named)
                 and os.pread(fd, len(raw)+1, 0) == raw, 'file_changed')
        for name in self.absences:
            try:
                os.stat(name, dir_fd=self.dirfd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise SnapshotError('absent_input_appeared')

    def read(self, name, limit, optional=False):
        need(name in self.allowed, 'file_outside_public_scope')
        self.observe()
        self.check()
        if name in self.absences:
            return None
        if name not in self.files:
            try:
                named = os.stat(name, dir_fd=self.dirfd, follow_symlinks=False)
            except FileNotFoundError:
                if not optional:
                    raise SnapshotError('required_input_missing') from None
                self.absences.add(name)
                self.observe()
                self.check()
                return None
            self._private(named)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=self.dirfd)
            try:
                info = os.fstat(fd)
                self._private(info)
                need(_identity(info) == _identity(named), 'file_changed_before_open')
                need(info.st_size <= limit, 'metadata_too_large')
                raw = os.pread(fd, limit+1, 0)
                need(len(raw) == info.st_size, 'short_metadata_read')
                self.files[name] = (fd, _identity(info), raw)
            except BaseException:
                os.close(fd)
                raise
        self.observe()
        self.check()
        return self.files[name][2]

    def sidecars(self):
        for suffix in ('-wal', '-shm', '-journal'):
            name = self.name+suffix
            try:
                os.stat(name, dir_fd=self.dirfd, follow_symlinks=False)
            except FileNotFoundError:
                self.absences.add(name)
                continue
            raise SnapshotError('sqlite_sidecar_present')

    @contextmanager
    def database(self):
        self.sidecars()
        need(self.read(self.name+'.owner', 4096) == b'', 'invalid_owner_lock')
        try:
            fcntl.flock(self.files[self.name+'.owner'][0], fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SnapshotError('owner_active') from None
        raw = self.read(self.name, MAX_DB)
        self.sidecars()
        need(sha(raw) == self.scope.database_sha256, 'database_hash_mismatch')
        need(len(raw) >= 100 and raw[:16] == b'SQLite format 3\0'
             and raw[18] in (1, 2) and raw[19] in (1, 2), 'invalid_sqlite_image')
        image = bytearray(raw)
        image[18] = image[19] = 1
        db = sqlite3.connect(':memory:', isolation_level=None)
        try:
            need(hasattr(db, 'deserialize'), 'sqlite_image_reader_unavailable')
            db.deserialize(bytes(image))
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            need(db.execute('PRAGMA query_only').fetchone() == (1,))
            yield db
            self.observe()
            self.sidecars()
            self.check()
        finally:
            db.close()

    def evidence(self):
        return {name: dict(device=ident[0], inode=ident[1], uid=ident[2],
                           mode=stat.S_IMODE(ident[3]), bytes=len(raw), sha256=sha(raw))
                for name, (_, ident, raw) in self.files.items()}

    def close(self):
        valid = True
        for fd, ident in [(fd, ident) for fd, ident, _ in self.files.values()] + [(fd, ident) for _, fd, ident in reversed(self.dirs)]:
            try:
                if _identity(os.fstat(fd))[:4] == ident[:4]:
                    os.close(fd)
                else:
                    valid = False
            except OSError:
                valid = False
        self.files.clear()
        self.dirs.clear()
        return valid


_CONTROLLER = {
    'policy': [('id', 'INTEGER', 0, 1), ('body', 'BLOB', 0, 0), ('digest', 'TEXT', 0, 0)],
    'clock': [('id', 'INTEGER', 0, 1), ('epoch', 'INTEGER', 0, 0), ('mono', 'INTEGER', 0, 0)],
    'allocations': [('id', 'TEXT', 0, 1), ('run_id', 'TEXT', 0, 0), ('body', 'BLOB', 1, 0)],
}
_NATIVE = {
    'enrollment': [('id', 'INTEGER', 0, 1), ('body', 'BLOB', 1, 0)],
    'clock': _CONTROLLER['clock'],
    'resources': [('id', 'TEXT', 0, 1), ('body', 'BLOB', 1, 0)],
    'claims': [('id', 'TEXT', 0, 1), ('body', 'BLOB', 1, 0), ('run_id', 'TEXT', 0, 0)],
    'prepared': [('id', 'TEXT', 0, 1), ('body', 'BLOB', 1, 0)],
    'starts': [('id', 'TEXT', 0, 1), ('body', 'BLOB', 1, 0)],
}


def _schema(db, role):
    expected = _NATIVE if role == 'native' else _CONTROLLER
    actual = db.execute("SELECT name,type,sql FROM sqlite_schema WHERE type IN ('table','view')").fetchall()
    need({(name, kind) for name, kind, _ in actual} == {(name, 'table') for name in expected}, 'invalid_ledger_schema')
    desired_sql = {}
    for name, columns in expected.items():
        parts = []
        for column, kind, notnull, pk in columns:
            part = column+' '+kind
            if pk:
                part += ' PRIMARY KEY' + (' CHECK(id=1)' if kind == 'INTEGER' else '')
            if column == 'run_id':
                part += ' UNIQUE'
            if notnull:
                part += ' NOT NULL'
            parts.append(part)
        desired_sql[name] = ''.join(('CREATE TABLE '+name+'('+','.join(parts)+')').split())
    need(all(type(sql) is str and ''.join(sql.split()) == desired_sql[name] for name, _, sql in actual),
         'invalid_ledger_schema')
    for name, columns in expected.items():
        info = db.execute('PRAGMA table_xinfo('+name+')').fetchall()
        need(len(info) == len(columns) and all(row[6] == 0 and row[4] is None for row in info)
             and [(r[1], r[2], r[3], r[5]) for r in info] == columns, 'invalid_ledger_schema')
    expected_indices = set()
    for table, columns in expected.items():
        number = int(any(kind == 'TEXT' and pk for _, kind, _, pk in columns)) + int(any(name == 'run_id' for name, *_ in columns))
        expected_indices.update('sqlite_autoindex_'+table+'_'+str(i) for i in range(1, number+1))
    indices = db.execute("SELECT name,sql FROM sqlite_schema WHERE type='index'").fetchall()
    need({name for name, _ in indices} == expected_indices and all(sql is None for _, sql in indices),
         'invalid_ledger_schema')
    unique_table = 'claims' if role == 'native' else 'allocations'
    unique = []
    for row in db.execute('PRAGMA index_list('+unique_table+')').fetchall():
        need(row[1] in ('sqlite_autoindex_'+unique_table+'_1', 'sqlite_autoindex_'+unique_table+'_2'),
             'invalid_ledger_schema')
        if row[2] == 1 and row[4] == 0:
            unique.append(tuple(r[2] for r in db.execute('PRAGMA index_info('+row[1]+')').fetchall()))
    need(('id',) in unique and ('run_id',) in unique, 'invalid_ledger_schema')
    triggers = db.execute("SELECT name,tbl_name,sql FROM sqlite_schema WHERE type='trigger'").fetchall()
    if role == 'controller':
        need(not triggers, 'invalid_ledger_schema')
    else:
        desired = {}
        for table in ('resources', 'claims', 'prepared', 'starts'):
            for action in ('UPDATE', 'DELETE'):
                name = 'immutable_'+table+'_'+action
                sql = 'CREATE TRIGGER '+name+' BEFORE '+action+' ON '+table+" BEGIN SELECT RAISE(ABORT,'append_only'); END"
                desired[name] = (table, sql)
        need(len(triggers) == len(desired) and all(name in desired and (table, sql) == desired[name]
             for name, table, sql in triggers), 'invalid_ledger_schema')


def _rows(db, table, columns):
    rows = db.execute('SELECT '+','.join(columns)+' FROM '+table+' ORDER BY '+columns[0]+' LIMIT '+str(MAX_ROWS+1)).fetchall()
    need(len(rows) <= MAX_ROWS, 'ledger_row_limit')
    return rows


def _body(raw):
    need(type(raw) is bytes and 0 < len(raw) <= 65536, 'invalid_canonical_row')
    value = public_json(raw)
    need(canonical(value) == raw, 'invalid_canonical_row')
    return value


def _cost(value):
    need(type(value) is dict and set(value) == set(asdict(Cost())), 'invalid_full_caps')
    return Cost(**value)


_META = {'parent_id', 'policy_digest', 'unit', 'host_boot_id', 'campaign_id', 'resource_sha256',
         'run_id', 'sends', 'sender', 'receiver', 'transport', 'format', 'object', 'source_run_id',
         'application_expires_epoch_us', 'grant_digest', 'profile', 'cipher_frame_limit',
         'secret_sha256', 'plan_sha256'}


def _child(value, policy):
    need(type(value) is dict and set(value) == {'metadata', 'cost', 'digest'}, 'invalid_child')
    m = value['metadata']
    public32 = policy.qualification_kind in PUBLIC32_KINDS
    fields = _META | ({'public_response_max_bytes'} if public32 else set())
    if policy.qualification_kind in BUNDLE_KINDS: fields.add('bundle_context_sha256')
    mb1=policy.qualification_kind in (GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND,GFSK250_FAST_BUNDLE_KIND)
    if mb1:fields.add('public_transport_descriptor_sha256')
    gfsk=policy.qualification_kind in (GFSK_KIND,GFSK_BUNDLE_KIND,GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND,GFSK250_FAST_BUNDLE_KIND)
    if gfsk:fields.add('radio_modem')
    from .mcu_artifact import META_SCHEMA, KIND as MCU_KIND, validate_metadata
    if type(m) is dict and m.get('schema')==META_SCHEMA:
        need(policy.qualification_kind==MCU_KIND,'mcu_child_policy_changed')
        validate_metadata(m);fields.update(('schema','mcu_artifact','mcu_artifact_sha256'))
    need(type(m) is dict and set(m) == fields and m['parent_id'] == policy.parent_id
         and m['policy_digest'] == policy.digest, 'invalid_child')
    if policy.qualification_kind in BUNDLE_KINDS:
        need(m['bundle_context_sha256'] == policy.bundle_context_sha256, 'bundle_child_context_changed')
    if mb1:hex_value(m['public_transport_descriptor_sha256'],64)
    unit = policy.unit(m['unit'])
    need(m['host_boot_id'] == unit.host_boot_id and type(m['sends']) is bool
         and m['sender'] in UNITS and m['receiver'] in UNITS and m['sender'] != m['receiver']
         and m['unit'] == (m['sender'] if m['sends'] else m['receiver']), 'invalid_child')
    policy.unit(m['sender']); policy.unit(m['receiver'])
    identifier(m['campaign_id']); identifier(m['run_id'])
    for key in ('resource_sha256', 'grant_digest', 'secret_sha256', 'plan_sha256'):
        hex_value(m[key], 64)
    obj = m['object']
    need(type(obj) is dict and set(obj) == {'bytes', 'sha256'}, 'invalid_child')
    if public32:
        need(type(m['public_response_max_bytes']) is int and m['public_response_max_bytes']==32768
             and m['transport']=='lora' and m['format'] in ('MR1.request','MR1.response')
             and (m['sender'],m['receiver'])==(('unit-a','unit-c') if m['format']=='MR1.request' else ('unit-c','unit-a')),
             'invalid_public32_child')
    integer(obj['bytes'], 1, 32768 if public32 and m['format']=='MR1.response' else 16384)
    need(not public32 or m['format']!='MR1.response' or mb1 or obj['bytes']==32768, 'invalid_public32_child')
    hex_value(obj['sha256'], 64)
    expiry = integer(m['application_expires_epoch_us'], 1)
    need(policy.issued_epoch_us < expiry <= policy.expires_epoch_us
         and m['format'] in ('MR1.request', 'MR1.response'), 'invalid_child')
    source = m['source_run_id']
    if type(source) is dict:
        need(set(source) == {'kind', 'request_run_id'} and source['kind'] == 'served_public_chunk'
             and m['format'] == 'MR1.response' and m['sends'], 'invalid_child')
        identifier(source['request_run_id']); need(source['request_run_id'] != m['run_id'], 'invalid_child')
    elif source is not None:
        identifier(source); need(source != m['run_id'] and m['sends'], 'invalid_child')
    else:
        need(not m['sends'] or m['format'] == 'MR1.request', 'invalid_child')
    need(type(m['profile']) is dict, 'invalid_full_caps')
    if m['transport'] == 'lora':
        if gfsk:need(m['radio_modem']==profile_name_for_kind(policy.qualification_kind) and m['unit'] in ('unit-a','unit-c'),'invalid_gfsk_child_modem')
        else:need(set(m['profile']) == set(RadioProfile.__dataclass_fields__), 'invalid_full_caps')
        profile=profile_from_plan(m,board_id=1)
        if policy.qualification_kind in (GFSK_BUNDLE_KIND,GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND,GFSK250_FAST_BUNDLE_KIND):
            actual=profile_record(profile);expected_profile=profile_record(profile_for(profile_name_for_kind(policy.qualification_kind)))
            actual.pop('lease_ms');expected_profile.pop('lease_ms')
            need(actual==expected_profile,'invalid_public32_gfsk_child')
        elif public32:
            need((profile.frequency_hz,profile.bandwidth_hz,profile.spreading_factor,profile.coding_rate,
                  profile.chip_power_dbm,profile.preamble,profile.sync_word)==(921375000,500000,7,5,0,8,0x42)
                 and profile.max_boot_attempts==256 and profile.max_boot_estimated_airtime_us==60000000,
                 'invalid_public32_child')
        expected = Cost(profile.max_boot_attempts, profile.max_boot_estimated_airtime_us)
        integer(m['cipher_frame_limit'], 1, 512)
    else:
        need(m['transport'] == 'tcp' and set(m['profile']) == set(IpLimits.__dataclass_fields__), 'invalid_full_caps')
        profile = IpLimits(**m['profile'])
        expected = Cost(ip_frames=profile.max_frames, tx_stream_bytes=profile.max_tx_stream_bytes,
                        rx_stream_bytes=profile.max_rx_stream_bytes)
        need(integer(m['cipher_frame_limit'], 1) == profile.max_frames, 'invalid_full_caps')
    if not gfsk:
        for v in m['profile'].values():
            need(type(v) is int, 'invalid_full_caps')
    cost = _cost(value['cost'])
    need(cost == expected and cost.within(unit.limits), 'invalid_full_caps')
    need(value['digest'] == sha(PARENT_DOMAIN+b'child\0'+canonical(m)), 'invalid_child_digest')
    return cost


def _child_ref(child):
    m = child['metadata']
    return {key: m[key] for key in ('unit', 'host_boot_id', 'run_id', 'campaign_id', 'resource_sha256',
        'sends', 'sender', 'receiver', 'transport', 'format', 'application_expires_epoch_us',
        'grant_digest', 'secret_sha256', 'plan_sha256', 'cipher_frame_limit')} | {
        'child_digest': child['digest'], 'full_caps': child['cost'], 'object': m['object']} | (
        {'public_response_max_bytes':m['public_response_max_bytes']} if 'public_response_max_bytes' in m else {}) | (
        {'bundle_context_sha256':m['bundle_context_sha256']} if 'bundle_context_sha256' in m else {})


def _policy(raw, scope):
    policy = ParentPolicy.from_dict(_body(raw))
    need(policy.parent_id == scope.parent_id and policy.digest == scope.policy_digest, 'policy_pin_mismatch')
    return policy


def _clock(db, policy, enrollment=None):
    rows = _rows(db, 'clock', ('id', 'epoch', 'mono'))
    need(len(rows) == 1 and type(rows[0][0]) is int and rows[0][0] == 1, 'invalid_clock_row')
    _, epoch, mono = rows[0]
    integer(epoch, 1); integer(mono, 1)
    original_epoch = policy.issued_epoch_us if enrollment is None else enrollment['original_epoch_us']
    original_mono = policy.original_monotonic_us if enrollment is None else enrollment['original_monotonic_us']
    deadline = policy.deadline_monotonic_us if enrollment is None else enrollment['deadline_monotonic_us']
    need(original_epoch <= epoch < policy.expires_epoch_us and original_mono <= mono < deadline, 'invalid_clock_row')
    return dict(epoch_us=epoch, monotonic_us=mono)


def _priors(policy):
    return dict(global_snapshot_sha256=policy.prior_snapshot_sha256,
                units=[dict(unit=u.unit, host_boot_id=u.host_boot_id, prior_snapshot_sha256=u.prior_snapshot_sha256,
                            prior_debt=asdict(u.prior_debt)) for u in policy.units],
                lineage_verified=False, deduplication_permitted=False)


def _controller(db, scope, reader):
    rows = _rows(db, 'policy', ('id', 'body', 'digest'))
    need(len(rows) == 1 and type(rows[0][0]) is int and rows[0][0] == 1, 'invalid_policy_row')
    policy = _policy(rows[0][1], scope)
    need(rows[0][2] == policy.digest, 'policy_pin_mismatch')
    clock = _clock(db, policy)
    paid = {u.unit: u.prior_debt for u in policy.units}
    additions = {u.unit: Cost() for u in policy.units}
    allocations, private_rows = [], []
    for aid, run, raw in _rows(db, 'allocations', ('id', 'run_id', 'body')):
        hex_value(aid, 64); identifier(run)
        children = _body(raw)
        need(type(children) is list and len(children) == 2, 'invalid_pair')
        costs = [_child(c, policy) for c in children]
        a, b = [c['metadata'] for c in children]
        need(a['unit'] != b['unit'] and a['sends'] != b['sends'] and a['run_id'] == b['run_id'] == run,
             'invalid_pair')
        shared = _META-{'unit', 'host_boot_id', 'sends', 'source_run_id', 'plan_sha256'}
        need(all(canonical(a[k]) == canonical(b[k]) for k in shared), 'invalid_pair')
        need([a['unit'], b['unit']] == sorted([a['unit'], b['unit']])
             and aid == sha(PARENT_DOMAIN+b'pair\0'+raw), 'invalid_pair_digest')
        for child, cost in zip(children, costs):
            unit = child['metadata']['unit']
            paid[unit] = paid[unit].plus(cost); additions[unit] = additions[unit].plus(cost)
        allocations.append(dict(allocation_id=aid, run_id=run, row_sha256=sha(raw),
                                children=[_child_ref(c) for c in children]))
        private_rows.append(dict(allocation_id=aid, run_id=run, children=children))
    total = Cost()
    for u in policy.units:
        need(paid[u.unit].within(u.limits), 'paid_caps_exceed_policy')
        total = total.plus(paid[u.unit])
    need(total.within(policy.aggregate_limits), 'paid_caps_exceed_policy')
    return policy, dict(stored_clock=clock, allocations=allocations,
        allocation_count=len(allocations), units=[dict(unit=u.unit, full_prepaid_caps=asdict(additions[u.unit]),
        prior_plus_prepaid_caps=asdict(paid[u.unit]), limit=asdict(u.limits)) for u in policy.units],
        aggregate_prior_plus_prepaid_caps=asdict(total)), private_rows


_RESOURCE = {'resource_sha256', 'application_expires_epoch_us', 'original_epoch_us',
             'original_monotonic_us', 'application_deadline_monotonic_us'}
_RECEIPT = {'schema', 'parent_id', 'policy_digest', 'controller_allocation_id', 'native_claim_id',
            'unit', 'host_boot_id', 'run_id', 'resource_sha256', 'application_expires_epoch_us',
            'application_deadline_monotonic_us', 'child_caps', 'original_plan_sha256',
            'native_plan_sha256', 'secret_sha256', 'grant_context_sha256', 'prepared'}


def _native(db, scope, reader):
    rows = _rows(db, 'enrollment', ('id', 'body'))
    need(len(rows) == 1 and type(rows[0][0]) is int and rows[0][0] == 1, 'invalid_enrollment')
    enrollment = _body(rows[0][1])
    fields = {'schema', 'policy', 'unit', 'host_boot_id',
              'original_epoch_us', 'original_monotonic_us', 'deadline_monotonic_us'}
    need(type(enrollment) is dict, 'invalid_enrollment')
    guarded = enrollment.get('schema') == 'meshticulum.native-parent-enrollment.v2'
    need(set(enrollment) == (fields | {'history_binding_sha256'} if guarded else fields)
         and enrollment['schema'] in ('meshticulum.native-parent-enrollment.v1',
                                      'meshticulum.native-parent-enrollment.v2'), 'invalid_enrollment')
    if guarded: hex_value(enrollment['history_binding_sha256'], 64)
    policy = _policy(canonical(enrollment['policy']), scope)
    unit = policy.unit(scope.unit)
    need(enrollment['unit'] == unit.unit and enrollment['host_boot_id'] == unit.host_boot_id, 'invalid_enrollment')
    epoch = integer(enrollment['original_epoch_us'], 1); mono = integer(enrollment['original_monotonic_us'], 1)
    deadline = integer(enrollment['deadline_monotonic_us'], 1)
    need(policy.issued_epoch_us <= epoch < policy.expires_epoch_us
         and deadline == mono+policy.expires_epoch_us-epoch, 'invalid_enrollment')
    total_limits = Cost()
    for candidate in policy.units:
        total_limits = total_limits.plus(candidate.limits)
    need(total_limits.within(policy.aggregate_limits), 'unpartitioned_policy')
    approval = reader.read(reader.name+'.approval', 4096)
    approval_matches = approval == (_native_hash(enrollment)+'\n').encode()
    clock = _clock(db, policy, enrollment)
    resources, resource_rows = {}, []
    for rid, raw in _rows(db, 'resources', ('id', 'body')):
        hex_value(rid, 64); r = _body(raw)
        bundle = policy.qualification_kind in BUNDLE_KINDS
        expected_fields=(_RESOURCE | {'campaign_id','bundle_context_sha256','bundle_original_epoch_us','bundle_original_monotonic_us','bundle_original_deadline_monotonic_us'} if bundle else _RESOURCE)
        if policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND:expected_fields|={'fast_stage_sha256','fast_stage_deadline_monotonic_us'}
        need(type(r) is dict and set(r)==expected_fields, 'invalid_resource')
        binding=dict(parent_id=policy.parent_id,unit=unit.unit,resource_sha256=r['resource_sha256'])
        if bundle:
            identifier(r['campaign_id']); need(r['bundle_context_sha256'] == policy.bundle_context_sha256, 'bundle_resource_context_changed')
            binding.update(campaign_id=r['campaign_id'],bundle_context_sha256=r['bundle_context_sha256'])
            need((r['bundle_original_epoch_us'],r['bundle_original_monotonic_us'],r['bundle_original_deadline_monotonic_us'])==(epoch,mono,deadline), 'bundle_original_native_clock_changed')
        hex_value(r['resource_sha256'], 64)
        e = integer(r['application_expires_epoch_us'], 1); re = integer(r['original_epoch_us'], 1)
        rm = integer(r['original_monotonic_us'], 1); rd = integer(r['application_deadline_monotonic_us'], 1)
        expected_deadline=min(deadline,rm+e-re,mono+e-epoch) if bundle else min(deadline,rm+e-re)
        if policy.qualification_kind==GFSK250_FAST_BUNDLE_KIND:
            hex_value(r['fast_stage_sha256'],64)
            stage_deadline=integer(r['fast_stage_deadline_monotonic_us'],1)
            need(mono<stage_deadline<=deadline,'fast_stage_original_deadline_changed')
            expected_deadline=min(expected_deadline,stage_deadline)
        need(epoch <= re < e <= policy.expires_epoch_us and rm >= mono
             and rm < rd == expected_deadline and rid == _native_hash(binding),'invalid_resource')
        resources[rid] = r
        resource_rows.append(dict(resource_id=rid, row_sha256=sha(raw), **r))
    claims, claim_rows, paid, additions = {}, [], unit.prior_debt, Cost()
    for cid, raw, run in _rows(db, 'claims', ('id', 'body', 'run_id')):
        hex_value(cid, 64); identifier(run); claim = _body(raw)
        need(type(claim) is dict and set(claim) == {'child', 'controller_allocation_id', 'resource_id', 'resource'}, 'invalid_claim')
        cost = _child(claim['child'], policy); m = claim['child']['metadata']
        hex_value(claim['controller_allocation_id'], 64); hex_value(claim['resource_id'], 64)
        need(m['unit'] == unit.unit and m['run_id'] == run and cid == _native_hash(claim)
             and canonical(resources.get(claim['resource_id'])) == canonical(claim['resource'])
             and m['resource_sha256'] == claim['resource']['resource_sha256']
             and m['application_expires_epoch_us'] == claim['resource']['application_expires_epoch_us'], 'invalid_claim')
        if policy.qualification_kind in BUNDLE_KINDS:
            need(m['campaign_id'] == claim['resource']['campaign_id'] and m['bundle_context_sha256'] == claim['resource']['bundle_context_sha256'], 'bundle_claim_resource_changed')
        paid = paid.plus(cost); additions = additions.plus(cost)
        claims[cid] = claim
        claim_rows.append(dict(native_claim_id=cid, controller_allocation_id=claim['controller_allocation_id'],
                               resource_id=claim['resource_id'], row_sha256=sha(raw), child=_child_ref(claim['child'])))
    need(paid.within(unit.limits), 'paid_caps_exceed_policy')
    need(set(resources) == {c['resource_id'] for c in claims.values()}, 'orphan_resource')
    prepared, starts = {}, {}
    for table, target in (('prepared', prepared), ('starts', starts)):
        for cid, raw in _rows(db, table, ('id', 'body')):
            hex_value(cid, 64); r = _body(raw)
            keys = _RECEIPT | ({'started'} if table == 'starts' else set())
            need(type(r) is dict and set(r) == keys and cid in claims, 'invalid_receipt')
            claim = claims[cid]; m = claim['child']['metadata']
            expected = dict(parent_id=policy.parent_id, policy_digest=policy.digest,
                controller_allocation_id=claim['controller_allocation_id'], native_claim_id=cid,
                unit=unit.unit, host_boot_id=unit.host_boot_id, run_id=m['run_id'], resource_sha256=m['resource_sha256'],
                application_expires_epoch_us=m['application_expires_epoch_us'], child_caps=claim['child'],
                original_plan_sha256=m['plan_sha256'], secret_sha256=m['secret_sha256'], grant_context_sha256=m['grant_digest'], prepared=True)
            need(canonical({k: r[k] for k in expected}) == canonical(expected) and type(r['prepared']) is bool,
                 'invalid_receipt')
            need(r['schema'] == ('meshticulum.native-parent-claim.v1' if table == 'prepared' else 'meshticulum.native-parent-start.v1'), 'invalid_receipt')
            rd = integer(r['application_deadline_monotonic_us'], 1)
            need(claim['resource']['original_monotonic_us'] < rd <= claim['resource']['application_deadline_monotonic_us'], 'invalid_receipt')
            hex_value(r['native_plan_sha256'], 64)
            if table == 'starts':
                need(r['started'] is True and cid in prepared and canonical(r) == canonical(dict(prepared[cid], schema='meshticulum.native-parent-start.v1', started=True)), 'invalid_start')
            target[cid] = r
    for row in claim_rows:
        cid = row['native_claim_id']; r = prepared.get(cid)
        row.update(prepared=r is not None, started=cid in starts,
                   prepared_row_sha256=None if r is None else sha(canonical(r)),
                   start_row_sha256=None if cid not in starts else sha(canonical(starts[cid])),
                   native_plan_sha256=None if r is None else r['native_plan_sha256'],
                   application_deadline_monotonic_us=None if r is None else r['application_deadline_monotonic_us'])
    detail = dict(enrollment=dict(unit=unit.unit, host_boot_id=unit.host_boot_id, original_epoch_us=epoch,
        original_monotonic_us=mono, deadline_monotonic_us=deadline, row_sha256=sha(rows[0][1])),
        approval_bytes_match_enrollment=approval_matches, stored_clock=clock, resources=resource_rows,
        claims=claim_rows, claim_count=len(claims), resource_count=len(resources), prepared_count=len(prepared),
        start_count=len(starts), full_prepaid_caps=asdict(additions), prior_plus_prepaid_caps=asdict(paid), limit=asdict(unit.limits),
        underlying_native_plan_correspondence_verified=False)
    if guarded: detail['enrollment']['history_binding_sha256'] = enrollment['history_binding_sha256']
    return policy, detail, [dict(native_claim_id=k, **v, prepared_receipt=prepared.get(k),
                                start_record=starts.get(k)) for k, v in claims.items()]


def _legacy_relation(policy, parent_rows, legacy_rows, retained):
    claims = {c['child']['metadata']['run_id']: c for c in parent_rows}
    readers = {r['run_id']: reader for r, reader in retained}
    links = []
    for run in legacy_rows:
        link = dict(run_id=run['run_id'], host_boot_id=run['host_boot_id'], transport=run['transport'],
                    actual_cost=run['actual_cost'], relation='unresolved', full_prepaid_caps=None,
                    no_refund=True, separate_debt_sum_permitted=False)
        try:
            need(run['status'] == 'scoped_rows_observed' and run['actual_cost'] is not None,
                 'legacy_rows_unavailable')
            claim = claims.get(run['run_id'])
            need(claim is not None and claim['prepared_receipt'] is not None and claim['start_record'] is not None,
                 'native_claim_prepared_or_start_missing')
            receipt = claim['prepared_receipt']; child = claim['child']; m = child['metadata']
            reader = readers[run['run_id']]
            provision = public_json(reader.files['provision.json'][2])
            plan = public_json(reader.files['grant.json'][2])
            need(canonical(provision.get('parent')) == canonical(receipt) and run['host_boot_id'] == m['host_boot_id']
                 and run['transport'] == m['transport'], 'legacy_parent_identity_mismatch')
            need(run['admitted_caps'] == child['cost'] and _cost(run['actual_cost']).within(_cost(child['cost'])),
                 'legacy_caps_mismatch')
            grant = run['grant']
            need(grant['context_sha256'] == m['grant_digest'] and grant['format'] == m['format']
                 and grant['direction'] == (0 if m['sends'] else 1)
                 and grant['application_expires_epoch_us'] == m['application_expires_epoch_us']
                 and grant['application_deadline_monotonic_us'] == receipt['application_deadline_monotonic_us'],
                 'legacy_grant_binding_mismatch')
            session = plan['session'] if m['transport'] == 'tcp' else plan
            need(canonical(session['initiator']) == canonical(asdict(policy.unit(m['sender']).peer))
                 and canonical(session['responder']) == canonical(asdict(policy.unit(m['receiver']).peer))
                 and canonical(plan['local_peer']) == canonical(asdict(policy.unit(m['unit']).peer)),
                 'legacy_peer_correspondence_mismatch')
            actual_profile = plan['limits'] if m['transport'] == 'tcp' else plan['profile']
            frames = actual_profile['max_frames'] if m['transport'] == 'tcp' else plan['max_frames']
            need(canonical(actual_profile) == canonical(m['profile']) and type(frames) is int
                 and frames == m['cipher_frame_limit']
                 and canonical({k: plan['object'][k] for k in ('bytes', 'sha256')}) == canonical(m['object']),
                 'legacy_descriptor_correspondence_mismatch')
            if m['transport'] == 'lora':
                need(plan['sender_unit'] == m['sender'] and plan['object']['object_id'] == m['object']['sha256']
                     and type(plan['object']['chunk_index']) is int and plan['object']['chunk_index'] == 0,
                     'legacy_descriptor_correspondence_mismatch')
            if policy.qualification_kind in (GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND,GFSK250_FAST_BUNDLE_KIND):
                need(grant.get('public_resource_representation')=='MB1'
                     and grant.get('public_transport_descriptor_sha256')==m['public_transport_descriptor_sha256'],
                     'legacy_mb1_descriptor_commitment_mismatch')
            else:
                need(grant.get('public_transport_descriptor_sha256') is None,
                     'legacy_mb1_descriptor_downgrade')
            normalized = dict(plan)
            if m['transport'] == 'tcp':
                normalized['application_deadline_monotonic_us'] = None
            else:
                normalized.pop('application_deadline_monotonic_us', None)
            need(sha(canonical(plan)) == receipt['native_plan_sha256']
                 and sha(canonical(normalized)) == m['plan_sha256'], 'legacy_plan_correspondence_mismatch')
            link.update(relation='same_endpoint_allocation', full_prepaid_caps=child['cost'],
                native_claim_id=claim['native_claim_id'], controller_allocation_id=claim['controller_allocation_id'],
                child_digest=child['digest'], resource_id=claim['resource_id'],
                application_expires_epoch_us=m['application_expires_epoch_us'],
                application_deadline_monotonic_us=receipt['application_deadline_monotonic_us'])
        except Exception as error:
            link['reason'] = error.reason if isinstance(error, SnapshotError) else 'legacy_relation_unavailable'
        links.append(link)
    scoped_runs = {r['run_id'] for r in legacy_rows}
    omitted = [dict(run_id=run, native_claim_id=c['native_claim_id'], prepared=c['prepared_receipt'] is not None,
                    started=c['start_record'] is not None, full_prepaid_caps=c['child']['cost'],
                    actual_cost=None, reason='not_in_explicit_run_scope')
               for run, c in claims.items() if run not in scoped_runs]
    return dict(links=links, native_claims_without_scoped_actuals=omitted,
                scoped_linkage_complete=all(link['relation'] == 'same_endpoint_allocation' for link in links),
                complete_run_coverage=False, serialized_cross_host_provenance_qualified=False)


def collect(state, scope, *, clock, boot_reader, legacy_scope=None):
    """Observe fixed native layout or an explicitly scoped controller directory.

    Controller `state` is its independently selected public ledger directory,
    not the native state root. Scope pins are observations, never approval.
    """
    need(type(scope) is Scope, 'invalid_scope')
    need(legacy_scope is None or (scope.role == 'native' and type(legacy_scope) is debt.Scope
         and legacy_scope.unit == scope.unit and legacy_scope.current_host_boot_id == scope.current_host_boot_id
         and legacy_scope.observation_expires_epoch_us == scope.observation_expires_epoch_us), 'invalid_legacy_scope')
    state = Path(state).absolute()
    observe = _Observation(scope, clock, boot_reader)
    legacy_observe = None if legacy_scope is None else debt._Observation(legacy_scope, clock, boot_reader)
    retained, legacy_rows = [], []
    def joint_observe():
        now = observe()
        if legacy_observe is not None:
            now = legacy_observe()
        return now
    reader = None
    rows = None
    report = dict(schema='meshticulum.public-parent-snapshot.v1', role=scope.role, unit=scope.unit,
        parent_id=scope.parent_id, policy_digest=scope.policy_digest, owner_uid=scope.owner_uid,
        current_host_boot_id=scope.current_host_boot_id, scope_canonical_sha256=sha(canonical(asdict(scope))),
        expected_database_sha256=scope.database_sha256, status='unavailable', scoped_rows_complete=False,
        pending_unknown=None, detail=None, prior_lineage=None, files=None, complete_global_debt=False,
        eligible_for_enrollment=False, live_terminal_ownership_observed=False, MCU_observations=None,
        physical_quiescence_observed=False, legacy_actual_usage_included=legacy_scope is not None,
        legacy_runs=None, legacy_relation=None, legacy_scoped_rows_complete=None,
        actual_plus_prepaid_sum_permitted=False, refund_permitted=False, database_writes=False,
        sqlite_parser=dict(source='bounded_held_bytes', native_path_opened=False, query_only_memory=True,
            wal_header_normalized_in_memory_only=True, whole_sqlite_consistency_proved=False))
    try:
        joint_observe()
        reader = _Reader(state, scope, joint_observe)
        with reader.database() as db:
            _schema(db, scope.role)
            policy, detail, rows = (_native if scope.role == 'native' else _controller)(db, scope, reader)
        if legacy_scope is not None:
            legacy_rows = [debt._one(state, legacy_scope, run, joint_observe, retained) for run in legacy_scope.runs]
        pending = reader.read(reader.name+'.pending', 4096, optional=True)
        report.update(status='scoped_rows_observed', scoped_rows_complete=True, detail=detail,
            prior_lineage=_priors(policy), pending_unknown=pending is not None,
            pending_marker_matches_policy=None if pending is None else pending == policy.digest.encode(),
            policy_row_sha256=sha(canonical(policy.to_dict())),
            policy_original_issued_epoch_us=policy.issued_epoch_us,
            policy_original_expires_epoch_us=policy.expires_epoch_us,
            policy_original_monotonic_us=policy.original_monotonic_us,
            policy_original_deadline_monotonic_us=policy.deadline_monotonic_us,
            policy_controller_boot_id=policy.controller_boot_id)
        final = joint_observe()
        reader.sidecars(); reader.check()
        for result, legacy_reader in retained:
            try:
                legacy_reader.check()
                for name in legacy_reader.files:
                    if name.endswith('.sqlite'):
                        legacy_reader.absent_sidecars(name)
            except Exception:
                result.update(status='unavailable', actual_cost=None, reason='final_input_changed')
        report.update(files=reader.evidence(), policy_expired_at_observation=final[0] >= policy.expires_epoch_us)
        if legacy_scope is not None:
            report.update(legacy_runs=legacy_rows, legacy_scoped_rows_complete=all(r['actual_cost'] is not None for r in legacy_rows),
                legacy_scope_canonical_sha256=sha(canonical(asdict(legacy_scope))),
                declared_runtime_sha256=legacy_scope.runtime_sha256, runtime_archive_observed=False,
                declared_manifest_sha256=legacy_scope.manifest_sha256,
                legacy_relation=_legacy_relation(policy, rows, legacy_rows, retained))
    except Exception as error:
        report.update(status='unavailable', reason=error.reason if isinstance(error, SnapshotError) else 'invalid_or_unavailable_ledger',
                      scoped_rows_complete=False, detail=None, prior_lineage=None)
        rows = None
    finally:
        for result, legacy_reader in retained:
            if not legacy_reader.close():
                result.update(status='unavailable', actual_cost=None, reason='reader_cleanup_unavailable')
                report.update(legacy_scoped_rows_complete=False, legacy_relation=None)
        if reader is not None and not reader.close():
            report.update(status='unavailable', reason='cleanup_unknown', scoped_rows_complete=False, detail=None, prior_lineage=None)
            rows = None
            report['legacy_relation'] = None
    if observe.first is not None:
        report.update(observation_original_epoch_us=observe.first[0], observation_original_monotonic_us=observe.first[1],
            observation_expires_epoch_us=scope.observation_expires_epoch_us,
            observation_deadline_monotonic_us=observe.deadline,
            observation_final_epoch_us=observe.last[0], observation_final_monotonic_us=observe.last[1])
    return _issue(report, rows)


def reconcile(controller, natives):
    """Link only exact local collected results. Neither reports nor links grant rights."""
    need(type(controller) is Snapshot and controller in _RESULTS and type(natives) is tuple
         and all(type(n) is Snapshot and n in _RESULTS for n in natives), 'unobserved_report')
    c = controller.to_dict(); ns = [n.to_dict() for n in natives]
    need(c['role'] == 'controller' and all(n['role'] == 'native' for n in ns)
         and len({n['unit'] for n in ns}) == len(ns), 'invalid_reconciliation_scope')
    result = dict(schema='meshticulum.public-parent-reconciliation.v1', parent_id=c['parent_id'],
        policy_digest=c['policy_digest'], complete_global_debt=False, eligible_for_enrollment=False,
        prior_lineage_verified=False, actual_usage_included=any(n['legacy_actual_usage_included'] for n in ns),
        actual_parent_overlap_resolved=False, serialized_cross_host_provenance_qualified=False,
        independent_debt_sum_permitted=False, refund_permitted=False, scoped_relation_complete=False,
        observations=[dict(role=r['role'], unit=r['unit'], status=r['status'],
            database_sha256=r['expected_database_sha256'], scope_canonical_sha256=r['scope_canonical_sha256']) for r in [c]+ns],
        matched=[], unmatched_claims=[], missing_native_endpoints=[], legacy_relations=[n['legacy_relation'] for n in ns],
        missing_native_units=[],
        omitted=['unscoped_legacy_run_journals', 'prior_lineage',
            'current_terminal_ownership', 'MCU_boot_and_debt', 'physical_quiescence', 'scope_completeness_authority'])
    if not all(r['scoped_rows_complete'] for r in [c]+ns):
        result['reason'] = 'source_scope_unavailable'
        return result
    need(all((n['parent_id'], n['policy_digest'], n['policy_row_sha256']) ==
             (c['parent_id'], c['policy_digest'], c['policy_row_sha256']) for n in ns), 'policy_relation_mismatch')
    allocations = public_json(_RESULTS[controller][1])
    result['missing_native_units'] = sorted({u['unit'] for u in c['detail']['units']} - {n['unit'] for n in ns})
    endpoints = {(a['allocation_id'], child['metadata']['unit']): child for a in allocations for child in a['children']}
    seen = set()
    for observed, n in zip(natives, ns):
        for claim in public_json(_RESULTS[observed][1]):
            key = claim['controller_allocation_id'], n['unit']
            exact = canonical(endpoints.get(key)) == canonical(claim['child']) and key not in seen
            projected = dict(unit=n['unit'], host_boot_id=claim['child']['metadata']['host_boot_id'],
                run_id=claim['child']['metadata']['run_id'], native_claim_id=claim['native_claim_id'],
                controller_allocation_id=key[0], child_digest=claim['child']['digest'], full_caps=claim['child']['cost'])
            (result['matched'] if exact else result['unmatched_claims']).append(projected)
            if exact:
                seen.add(key)
    for key, child in endpoints.items():
        if key not in seen:
            result['missing_native_endpoints'].append(dict(controller_allocation_id=key[0], **_child_ref(child)))
    unknown = any(r['pending_unknown'] for r in [c]+ns) or any(not n['detail']['approval_bytes_match_enrollment'] for n in ns)
    result['pending_or_unapproved_source'] = unknown
    result['scoped_relation_complete'] = not (result['unmatched_claims'] or result['missing_native_endpoints']
                                             or result['missing_native_units'] or unknown)
    result['accounting_relationship'] = 'matched controller endpoint and native claim cover the same full-cap allocation; never add or subtract these views'
    return result
