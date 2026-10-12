# SPDX-License-Identifier: Apache-2.0
"""Public-only full-cap accounting; no native dispatch or reset authority.

Explicit enrollment binds one approved handoff policy and prior-debt snapshot.
Opening never creates missing state or recovers live permits. A pair's complete
endpoint maxima are charged atomically before its instance-owned permit exists.
Unknown commit results keep a durable fence; cancellation, expiry, retirement
and smaller actual use never refund an allocation. Native per-unit claims,
entry-point/controller wiring and qualified debt provenance remain external.
"""
from dataclasses import dataclass, asdict
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import uuid

from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.wire import BenchGrant
from meshticulum.bench.services import _shape
from meshticulum.contracts.identity import PeerID, SessionKey
from meshticulum.ip.authority import grant_from_plan
from meshticulum.resources.resource import decode_request
from .profiles import RADIO_ADMISSION, LEGACY_SF5_8_PARENT, LEGACY_SF5_8_ADMISSION

BUNDLE_KINDS = frozenset(('c-bundle100k-v1','c-bundle100k-v2','c-bundle100k-v3'))
PUBLIC32_KINDS = BUNDLE_KINDS | {'c-bulk32-v1'}
BATCH_KIND = 'c-bulk-8k-batch-v1'
POLICY_KINDS = PUBLIC32_KINDS | {BATCH_KIND}

SCHEMA = 'meshticulum.public-parent-caps.v1'
DOMAIN = b'meshticulum.public-parent-caps.v1\0'
MAX = 2**63 - 1
UNITS = ('unit-a', 'unit-b', 'unit-c')


class ParentCapsError(RuntimeError):
    def __init__(self): super().__init__('public_parent_caps_unavailable')


def require(value):
    if not value: raise ParentCapsError()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    require(type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value))
    return value


def identifier(value):
    require(type(value) is str and len(value) == 32 and any(c != '0' for c in value)
            and all(c in '0123456789abcdef' for c in value))
    return value


def boot(value):
    require(type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0)
    return value


@dataclass(frozen=True, slots=True)
class Cost:
    radio_attempts: int = 0
    estimated_airtime_us: int = 0
    ip_frames: int = 0
    tx_stream_bytes: int = 0
    rx_stream_bytes: int = 0
    def __post_init__(self):
        require(all(type(v) is int and 0 <= v <= MAX for v in (
            self.radio_attempts,self.estimated_airtime_us,self.ip_frames,
            self.tx_stream_bytes,self.rx_stream_bytes)))
    def plus(self, other):
        require(type(other) is Cost)
        return Cost(self.radio_attempts+other.radio_attempts,
            self.estimated_airtime_us+other.estimated_airtime_us,self.ip_frames+other.ip_frames,
            self.tx_stream_bytes+other.tx_stream_bytes,self.rx_stream_bytes+other.rx_stream_bytes)
    def within(self, other):
        require(type(other) is Cost)
        return all(a <= b for a,b in zip((self.radio_attempts,self.estimated_airtime_us,
            self.ip_frames,self.tx_stream_bytes,self.rx_stream_bytes),(other.radio_attempts,
            other.estimated_airtime_us,other.ip_frames,other.tx_stream_bytes,other.rx_stream_bytes)))


@dataclass(frozen=True, slots=True)
class UnitCaps:
    unit: str
    host_boot_id: str
    peer: PeerID
    limits: Cost
    prior_debt: Cost
    prior_snapshot_sha256: str
    def __post_init__(self):
        require(self.unit in UNITS and type(self.peer) is PeerID and type(self.limits) is Cost
                and type(self.prior_debt) is Cost and self.prior_debt.within(self.limits))
        boot(self.host_boot_id); digest(self.prior_snapshot_sha256)


@dataclass(frozen=True, slots=True)
class ParentPolicy:
    parent_id: str
    controller_boot_id: str
    issued_epoch_us: int
    expires_epoch_us: int
    original_monotonic_us: int
    deadline_monotonic_us: int
    units: tuple
    aggregate_limits: Cost
    prior_snapshot_sha256: str
    qualification_kind: str | None = None
    bundle_context_sha256: str | None = None
    def __post_init__(self):
        require(self.qualification_kind is None or (type(self.qualification_kind) is str
                and self.qualification_kind in POLICY_KINDS))
        require((self.qualification_kind in BUNDLE_KINDS) == (self.bundle_context_sha256 is not None))
        if self.bundle_context_sha256 is not None: digest(self.bundle_context_sha256)
        identifier(self.parent_id); boot(self.controller_boot_id); digest(self.prior_snapshot_sha256)
        require(all(type(v) is int and 0 < v <= MAX for v in
            (self.issued_epoch_us, self.expires_epoch_us, self.original_monotonic_us, self.deadline_monotonic_us))
            and self.issued_epoch_us < self.expires_epoch_us
            and self.original_monotonic_us < self.deadline_monotonic_us
                <= self.original_monotonic_us + self.expires_epoch_us - self.issued_epoch_us
            and type(self.units) is tuple and 2 <= len(self.units) <= 3
            and all(type(u) is UnitCaps for u in self.units)
            and len({u.unit for u in self.units}) == len(self.units)
            and len({u.peer for u in self.units}) == len(self.units)
            and type(self.aggregate_limits) is Cost)
        prior = Cost()
        for unit in self.units: prior = prior.plus(unit.prior_debt)
        require(prior.within(self.aggregate_limits))
    def to_dict(self):
        value = asdict(self)
        if self.qualification_kind is None: value.pop('qualification_kind')
        if self.bundle_context_sha256 is None: value.pop('bundle_context_sha256')
        return value
    @classmethod
    def from_dict(cls, value):
        """Strict metadata decoding; this supplies no enrollment approval."""
        try:
            require(type(value) is dict and set(value) - {'qualification_kind','bundle_context_sha256'} == {
                'parent_id', 'controller_boot_id', 'issued_epoch_us', 'expires_epoch_us',
                'original_monotonic_us', 'deadline_monotonic_us', 'units',
                'aggregate_limits', 'prior_snapshot_sha256'})
            require('qualification_kind' not in value or value['qualification_kind'] in POLICY_KINDS)
            require(type(value['units']) in (list, tuple))
            units = []
            for unit in value['units']:
                require(type(unit) is dict and set(unit) == {
                    'unit', 'host_boot_id', 'peer', 'limits', 'prior_debt', 'prior_snapshot_sha256'})
                require(type(unit['peer']) is dict and set(unit['peer']) == {'chain_id', 'agent_id'})
                for key in ('limits', 'prior_debt'):
                    require(type(unit[key]) is dict and set(unit[key]) == set(asdict(Cost())))
                units.append(UnitCaps(unit['unit'], unit['host_boot_id'], PeerID(**unit['peer']),
                    Cost(**unit['limits']), Cost(**unit['prior_debt']), unit['prior_snapshot_sha256']))
            require(type(value['aggregate_limits']) is dict
                    and set(value['aggregate_limits']) == set(asdict(Cost())))
            return cls(value['parent_id'], value['controller_boot_id'], value['issued_epoch_us'],
                value['expires_epoch_us'], value['original_monotonic_us'], value['deadline_monotonic_us'],
                tuple(units), Cost(**value['aggregate_limits']), value['prior_snapshot_sha256'], value.get('qualification_kind'), value.get('bundle_context_sha256'))
        except Exception:
            raise ParentCapsError() from None
    @property
    def digest(self): return hashlib.sha256(DOMAIN + canonical(self.to_dict())).hexdigest()
    def unit(self, value):
        matches = [u for u in self.units if u.unit == value]
        require(len(matches) == 1); return matches[0]


@dataclass(frozen=True, slots=True, repr=False)
class ChildCaps:
    unit: str
    run_id: str
    sends: bool
    metadata: bytes
    cost: Cost
    expires_epoch_us: int
    @property
    def digest(self): return hashlib.sha256(DOMAIN + b'child\0' + self.metadata).hexdigest()
    def to_dict(self): return dict(metadata=json.loads(self.metadata), cost=asdict(self.cost), digest=self.digest)


def child_caps(package, policy):
    """Pure v1 public-package extraction; no clocks, files, keys or body retention."""
    try:
        require(type(policy) is ParentPolicy and type(package) is dict
            and set(package) == {'schema','unit','campaign_id','resource_sha256','application_expires_epoch_us',
                                 'transport','grant','secret_base64','payload_base64','source_run_id'}
            and package['schema'] == 'meshticulum.public-pilot-package.v1')
        unit = policy.unit(package['unit']); p = package['grant']
        identifier(package['campaign_id']); digest(package['resource_sha256'])
        secret = base64.b64decode(package['secret_base64'], validate=True)
        require(len(secret) == 32 and base64.b64encode(secret).decode() == package['secret_base64']
                and p['host_boot_id'] == unit.host_boot_id)
        if package['transport'] == 'tcp':
            require(p['application_deadline_monotonic_us'] is None)
            grant = grant_from_plan(p, secret, local_peer=unit.peer, host_boot_id=unit.host_boot_id)
            fmt = grant.format; sends = grant.direction(unit.peer) == 0
            run = grant.session.session_id.hex(); cost = Cost(ip_frames=grant.limits.max_frames,
                tx_stream_bytes=grant.limits.max_tx_stream_bytes, rx_stream_bytes=grant.limits.max_rx_stream_bytes)
            profile = asdict(grant.limits); frame_limit = grant.max_frames
            peers = grant.session.initiator, grant.session.responder
            obj = dict(bytes=grant.object_bytes, sha256=grant.object_sha256.hex())
            expiry = grant.application_expires_epoch_us
        else:
            fields = {'schema','unit','host_boot_id','local_peer','initiator','responder','session_id',
                'generation','profile','object','sender_unit','country','antenna_confirmed','max_frames',
                'service','issued_epoch_us','expires_epoch_us'}
            require(package['transport'] == 'lora' and p['schema'] == 'meshticulum.service-run.v1'
                and set(p) == fields and set(p['service']) in ({'kind','format','application_expires_epoch_us'},
                    {'kind','format','application_expires_epoch_us','public_response_max_bytes'})
                and set(p['object']) == {'bytes','sha256','object_id','chunk_index'}
                and p['object']['object_id'] == p['object']['sha256']
                and type(p['object']['chunk_index']) is int and p['object']['chunk_index'] == 0
                and p['unit'] == unit.unit and p['local_peer'] == asdict(unit.peer)
                and p['service']['kind'] == 'knowledge')
            rp = RadioProfile(**p['profile']); rp.encode(board_id=1)
            admission = (LEGACY_SF5_8_ADMISSION if policy.parent_id == LEGACY_SF5_8_PARENT
                         else RADIO_ADMISSION)
            admission.check(p['country'], p['antenna_confirmed'], rp)
            route = {k: p[k] for k in ('initiator','responder','session_id','generation','profile','object','sender_unit','country','service')}
            session = SessionKey(PeerID(**p['initiator']), PeerID(**p['responder']), bytes.fromhex(p['session_id']), bytes.fromhex(p['generation']))
            grant = BenchGrant(session, 'bench_transfer', hashlib.sha256(canonical(route)).digest(),
                p['issued_epoch_us'], p['expires_epoch_us'], 208, p['max_frames'], secret)
            require(grant.max_frames <= 512 and p['expires_epoch_us'] - p['issued_epoch_us'] <= 240000000)
            sends = p['sender_unit'] == unit.unit
            require(unit.peer == (session.initiator if sends else session.responder))
            fmt = p['service']['format']; run = p['session_id']; peers = session.initiator, session.responder
            obj = dict(bytes=p['object']['bytes'], sha256=p['object']['sha256'])
            cost = Cost(rp.max_boot_attempts, rp.max_boot_estimated_airtime_us)
            profile = asdict(rp); frame_limit = grant.max_frames; expiry = p['service']['application_expires_epoch_us']
        from meshticulum.bench.public_block import public32_service, object_limit
        public32 = public32_service(p.get('service',{}))
        require(public32 == (policy.qualification_kind in PUBLIC32_KINDS))
        if public32:
            require(package['transport'] == 'lora' and (rp.frequency_hz,rp.bandwidth_hz,rp.spreading_factor,
                rp.coding_rate,rp.chip_power_dbm,rp.preamble,rp.sync_word)==(921375000,500000,7,5,0,8,0x42)
                and rp.max_boot_attempts==256 and rp.max_boot_estimated_airtime_us==60000000)
        identifier(run); digest(obj['sha256'])
        require(fmt in ('MR1.request','MR1.response') and type(obj['bytes']) is int and 0 < obj['bytes'] <= object_limit(p.get('service',{}))
                and (not public32 or fmt != 'MR1.response' or obj['bytes'] == 32768)
                and type(expiry) is int and expiry == package['application_expires_epoch_us']
                and policy.issued_epoch_us <= p['issued_epoch_us'] < expiry <= p['expires_epoch_us'] <= policy.expires_epoch_us)
        sender, receiver = [next((u.unit for u in policy.units if u.peer == peer), None) for peer in peers]
        require(sender is not None and receiver is not None and sender != receiver)
        if public32:
            require((sender,receiver)==(('unit-a','unit-c') if fmt=='MR1.request' else ('unit-c','unit-a')))
        source = package['source_run_id']
        if type(source) is dict:
            require(set(source) == {'kind','request_run_id'} and source['kind'] == 'served_public_chunk'
                    and fmt == 'MR1.response')
            identifier(source['request_run_id']); require(source['request_run_id'] != run)
        elif source is not None:
            identifier(source); require(source != run)
        payload = package['payload_base64']
        require((not sends and payload is None and source is None)
                or (sends and source is not None and payload is None)
                or (sends and source is None and fmt == 'MR1.request' and type(payload) is str))
        if payload is not None:
            raw = base64.b64decode(payload, validate=True)
            require(base64.b64encode(raw).decode() == payload and len(raw) == obj['bytes']
                    and hashlib.sha256(raw).hexdigest() == obj['sha256'])
            _shape(raw, fmt, public32=public32)
            decoded = decode_request(raw, public32=public32)
            if public32: require(decoded[1:] == (0,32740) and '/chunks/' in decoded[0])
            require(decoded[0].startswith('/packs/' + package['resource_sha256'] + '/'))
        metadata = dict(parent_id=policy.parent_id, policy_digest=policy.digest, unit=unit.unit,
            host_boot_id=unit.host_boot_id, campaign_id=package['campaign_id'], resource_sha256=package['resource_sha256'],
            run_id=run, sends=sends, sender=sender, receiver=receiver, transport=package['transport'], format=fmt,
            object=obj, source_run_id=source, application_expires_epoch_us=expiry,
            grant_digest=grant.digest.hex(), profile=profile, cipher_frame_limit=frame_limit,
            secret_sha256=hashlib.sha256(secret).hexdigest(), plan_sha256=hashlib.sha256(canonical(p)).hexdigest())
        if public32: metadata['public_response_max_bytes'] = 32768
        if policy.qualification_kind in BUNDLE_KINDS: metadata['bundle_context_sha256'] = policy.bundle_context_sha256
        return ChildCaps(unit.unit, run, sends, canonical(metadata), cost, expiry)
    except Exception:
        raise ParentCapsError() from None


class PairPermit:
    def __init__(self, owner, key, children):
        self.owner, self.key, self.children, self.used = owner, key, children, False
    def __repr__(self): return '<PublicParentPairPermit>'


def private(info, directory=False):
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
            and (directory or info.st_nlink == 1))


class ParentCaps:
    """Lifetime-owned controller accounting. A permit cannot start native work."""
    @classmethod
    def enroll(cls, path, policy, *, clock, boot_reader):
        return cls(path, policy, clock=clock, boot_reader=boot_reader, enroll=True)
    @classmethod
    def open(cls, path, policy, *, clock, boot_reader):
        return cls(path, policy, clock=clock, boot_reader=boot_reader, enroll=False)
    def __init__(self, path, policy, *, clock, boot_reader, enroll):
        require(type(policy) is ParentPolicy and callable(clock) and callable(boot_reader))
        self.path = Path(path).absolute(); self.policy = policy; self._clock = clock; self._boot = boot_reader
        self._policy_ref = policy; self._policy_bytes = canonical(policy.to_dict())
        self._owner = os.getpid(), threading.get_ident(); self._closed = self._busy = self._uncertain = False
        self._permits = {}; self.db = self._db_ref = None; self._fds = {}; self._ids = {}; self._fd_refs = {}
        self._operation_pending = False
        self._last_read_clock = None
        self.pending = Path(str(self.path) + '.pending')
        try:
            private(self.path.parent.lstat(), True)
            require(self.path.parent.resolve() == self.path.parent)
            names = {'lock': Path(str(self.path) + '.owner'), 'db': self.path}
            for name, target in names.items():
                flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
                if enroll: flags |= os.O_CREAT | os.O_EXCL
                fd = os.open(target, flags, 0o600); self._fds[name] = fd
                info = os.fstat(fd); private(info); self._ids[name] = info.st_dev, info.st_ino
                self._fd_refs[name] = fd
                if name == 'lock': fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=.25)
            self._db_ref = self.db
            require(self.db.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',))
            self.db.execute('PRAGMA synchronous=FULL')
            require(self.db.execute('PRAGMA synchronous').fetchone() == (2,))
            self._refs = (self.path,self.pending,self._clock,self._boot,self._fds,self._ids,
                          tuple(self._fds.items()),tuple(self._ids.items()),self.db)
            self._busy = True
            if enroll:
                now = self._time()
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('CREATE TABLE policy (id INTEGER PRIMARY KEY CHECK(id=1), body BLOB, digest TEXT)')
                self.db.execute('INSERT INTO policy VALUES(1,?,?)', (self._policy_bytes, policy.digest))
                self.db.execute('CREATE TABLE clock (id INTEGER PRIMARY KEY CHECK(id=1), epoch INTEGER, mono INTEGER)')
                self.db.execute('INSERT INTO clock VALUES(1,?,?)', now)
                self.db.execute('CREATE TABLE allocations (id TEXT PRIMARY KEY, run_id TEXT UNIQUE, body BLOB NOT NULL)')
                self._commit(); self._sync_directory()
            self._validate()
            self._uncertain = self.pending.exists() or self.pending.is_symlink()
        except BaseException:
            if self.db is not None and self.db.in_transaction: self.db.execute('ROLLBACK')
            self._busy = False; self.close(); raise ParentCapsError() from None
        finally:
            self._busy = False
    def __repr__(self): return '<PublicParentCaps>'
    def _bound(self):
        require(not self._closed and self._owner == (os.getpid(), threading.get_ident())
                and self.policy is self._policy_ref and canonical(self.policy.to_dict()) == self._policy_bytes
                and self._fds == self._fd_refs and set(self._fd_refs) == {'db','lock'} and self.db is self._db_ref)
        refs = self._refs
        require(all(a is b for a,b in zip((self.path,self.pending,self._clock,self._boot,self._fds,self._ids),refs[:6]))
                and tuple(self._fds.items()) == refs[6] and tuple(self._ids.items()) == refs[7]
                and self.db is refs[8])
        private(self.path.parent.lstat(), True); require(self.path.parent.resolve() == self.path.parent)
        for name, fd in self._fds.items():
            target = self.path if name == 'db' else Path(str(self.path) + '.owner')
            held, named = os.fstat(fd), target.lstat(); private(held); private(named)
            require((held.st_dev, held.st_ino) == (named.st_dev, named.st_ino) == self._ids[name])
    def _sync_directory(self):
        fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
    def _time(self):
        self._bound(); current_boot = self._boot(); self._bound()
        require(not self._uncertain and current_boot == self.policy.controller_boot_id)
        now = self._clock(); self._bound()
        require(not self._uncertain and type(now) is tuple and len(now) == 2 and all(type(v) is int and 0 < v <= MAX for v in now)
            and self.policy.issued_epoch_us <= now[0] < self.policy.expires_epoch_us
            and self.policy.original_monotonic_us <= now[1] < self.policy.deadline_monotonic_us
            and (self._last_read_clock is None or all(a >= b for a,b in zip(now,self._last_read_clock))))
        self._last_read_clock = now
        return now
    def _validate(self):
        self._bound()
        require(self.db.execute('SELECT body,digest FROM policy WHERE id=1').fetchone() == (self._policy_bytes, self.policy.digest))
        rows = self.db.execute('SELECT id,run_id,body FROM allocations ORDER BY rowid').fetchall()
        require(len(rows) <= 4096)
        used = {u.unit: u.prior_debt for u in self.policy.units}
        for key, run, raw in rows:
            value = json.loads(raw); require(canonical(value) == raw and len(value) == 2)
            require(key == hashlib.sha256(DOMAIN + b'pair\0' + raw).hexdigest())
            units = set()
            for child in value:
                meta = child['metadata']; cost = Cost(**child['cost'])
                require(meta['policy_digest'] == self.policy.digest and meta['run_id'] == run
                        and meta['unit'] not in units and meta['unit'] in used
                        and child['digest'] == hashlib.sha256(DOMAIN + b'child\0' + canonical(meta)).hexdigest())
                units.add(meta['unit']); used[meta['unit']] = used[meta['unit']].plus(cost)
        aggregate = Cost()
        for unit in self.policy.units:
            require(used[unit.unit].within(unit.limits)); aggregate = aggregate.plus(used[unit.unit])
        require(aggregate.within(self.policy.aggregate_limits))
        return rows, used, aggregate
    def _commit(self): self.db.execute('COMMIT')
    def _marker(self):
        fd = os.open(self.pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try: os.write(fd, self.policy.digest.encode()); os.fsync(fd)
        finally: os.close(fd)
        self._sync_directory()
        self._operation_pending = True
    def _run(self, action, *, child_handoff=False):
        entered = False
        try:
            self._bound(); require(not self._uncertain)
            if self._busy: self._uncertain = True; raise ParentCapsError()
            self._busy = entered = True
            now = self._time(); before = self.db.execute('SELECT epoch,mono FROM clock WHERE id=1').fetchone()
            require(before is not None and all(a >= b for a, b in zip(now, before)))
            result = action(now)
            after = self._time(); require(all(a >= b for a, b in zip(after, now)))
            if child_handoff:
                require(type(result) is tuple and all(type(c) is ChildCaps and after[0] < c.expires_epoch_us for c in result))
            if not self._operation_pending: self._marker()
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('UPDATE clock SET epoch=?,mono=? WHERE id=1', after)
            self._commit()
            self._bound(); require(not self._uncertain)
            os.unlink(self.pending); self._sync_directory(); self._operation_pending = False
            return result
        except BaseException as error:
            if entered: self._uncertain = True
            if entered and self.db is self._db_ref and self.db is not None and self.db.in_transaction:
                self.db.execute('ROLLBACK')
            if self._operation_pending and not self.pending.exists():
                try: self._marker()
                except Exception: pass
            if not isinstance(error, Exception): raise
            raise ParentCapsError() from None
        finally:
            if entered: self._busy = False
    def prepay_pair(self, packages):
        def prepay(now):
            require(type(packages) in (tuple, list) and len(packages) == 2)
            children = tuple(sorted((child_caps(p, self.policy) for p in packages), key=lambda c: c.unit))
            first, second = children; a, b = json.loads(first.metadata), json.loads(second.metadata)
            require(first.unit != second.unit and first.run_id == second.run_id and first.sends != second.sends
                and all(a[k] == b[k] for k in ('campaign_id','resource_sha256','run_id','sender','receiver','transport','format','object',
                                               'application_expires_epoch_us','grant_digest','profile','cipher_frame_limit','secret_sha256'))
                and all(now[0] < c.expires_epoch_us for c in children))
            raw = canonical([c.to_dict() for c in children]); key = hashlib.sha256(DOMAIN + b'pair\0' + raw).hexdigest()
            rows, used, total = self._validate()
            require(not any(run == first.run_id for _, run, _ in rows) and len(rows) < 4096)
            for child in children:
                used[child.unit] = used[child.unit].plus(child.cost)
                require(used[child.unit].within(self.policy.unit(child.unit).limits)); total = total.plus(child.cost)
            require(total.within(self.policy.aggregate_limits))
            self._marker()
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT INTO allocations VALUES(?,?,?)', (key, first.run_id, raw))
            self.db.execute('UPDATE clock SET epoch=?,mono=? WHERE id=1', now)
            self._commit()
            self._validate(); self._time()
            permit = PairPermit(self, key, children)
            self._permits[id(permit)] = (permit, key, children, False)
            return permit
        return self._run(prepay)
    def consume(self, permit):
        def consume(now):
            require(type(permit) is PairPermit and permit.owner is self
                    and self._permits.get(id(permit)) == (permit, permit.key, permit.children, False)
                    and not permit.used and all(now[0] < c.expires_epoch_us for c in permit.children))
            rows, _, _ = self._validate()
            require(any(key == permit.key and raw == canonical([c.to_dict() for c in permit.children]) for key, _, raw in rows))
            self._permits[id(permit)] = (permit, permit.key, permit.children, True)
            permit.used = True; return permit.children
        return self._run(consume, child_handoff=True)
    def snapshot(self):
        self._bound(); require(not self._busy); rows, used, total = self._validate()
        return dict(parent_id=self.policy.parent_id, policy_digest=self.policy.digest, allocations=len(rows),
            per_unit={unit: asdict(cost) for unit, cost in used.items()}, aggregate=asdict(total),
            uncertain=self._uncertain or self.pending.exists(), native_enforcement=False)
    def current(self):
        """Read-only current parent observation; never allocates or refunds."""
        entered = False
        try:
            self._bound(); require(not self._uncertain and not self.pending.exists())
            if self._busy:
                self._uncertain = True
                raise ParentCapsError()
            self._busy = entered = True
            now = self._time()
            persisted = self.db.execute('SELECT epoch,mono FROM clock WHERE id=1').fetchone()
            require(persisted is not None and all(a >= b for a, b in zip(now, persisted))
                    and (self._last_read_clock is None
                         or all(a >= b for a, b in zip(now, self._last_read_clock))))
            self._bound(); require(not self._uncertain)
            self._last_read_clock = now
            return True
        except Exception:
            if entered:
                self._uncertain = True
            raise ParentCapsError() from None
        finally:
            if entered:
                self._busy = False
    def close(self):
        if self._owner != (os.getpid(), threading.get_ident()): return False
        self._closed = True
        if self._busy: return False
        if self._db_ref is not None: self._db_ref.close(); self.db = self._db_ref = None
        valid = self._fds == self._fd_refs
        for name, fd in list(self._fd_refs.items()):
            try:
                info = os.fstat(fd); require((info.st_dev, info.st_ino) == self._ids[name]); os.close(fd)
            except Exception: valid = False
        self._fds.clear()
        self._fd_refs.clear()
        return valid and not self._uncertain
