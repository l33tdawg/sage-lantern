# SPDX-License-Identifier: Apache-2.0
"""Held scoped native debt, independent of private dispatch permission.

Only an explicitly pinned public-pilot native ledger is inspected. Its complete
conservative prior-plus-prepaid claim is retained, including unknown usage.
This source proves neither global lineage nor physical retirement. The root
must separately establish full historical coverage and approve private limits.
No Snapshot DTO, caller cost, grant, database writer or transport is accepted.
"""
from dataclasses import asdict
from functools import wraps
import hashlib
import os
from pathlib import Path
import sqlite3
import threading

from ..bench import pilot_parent_snapshot as parent
from ..bench.pilot_resume_rpc import Reader
from .forward_budget import Cost
from .forward_native_owner import clock, host_boot
from .forward_native_provider import NativeAccountingObservation, NativeAccountingSource
from .forward_receive_journal import ReceiveIntention
from .mesh_inbox import _canonical


_BASENAMES = ('native.sqlite', 'native.sqlite.owner', 'native.sqlite.approval',
              'native.sqlite.pending')
_SIDECARS = ('native.sqlite-wal', 'native.sqlite-shm', 'native.sqlite-journal')
_DOMAIN = b'sage.lantern.held-native-parent-accounting.v1\0'


class NativeAccountingUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('held_native_accounting_unavailable')


def require(value):
    if not value:
        raise NativeAccountingUnavailable()


def _accounting_operation(method):
    @wraps(method)
    def using(self, *args, **kwargs):
        require(self._gate is self._gate_ref)
        with self._gate:
            self._busy += 1
            try:
                return method(self, *args, **kwargs)
            finally:
                self._busy -= 1
    return using


class HeldParentAccounting(NativeAccountingSource):
    """Own a held metadata reader until endpoint/key cleanup releases it.

    The observation window keeps the existing maximum sixty-second bound and
    must fit the original private intention. It cannot be renewed by a call.
    SQLite interpretation is prepared once from pinned immutable bytes; every
    observation freshly rereads those same bytes, names, ancestry and locks.
    This scoped conservative debt is data for an independent root approval,
    never a complete native/global-accounting or private-authority claim.
    """
    name = 'native.sqlite'

    def __init__(self, resource, intention, state, scope, *,
                 clock_reader=clock, boot_reader=host_boot):
        from sage_lantern.mesh_daemon import HardwareRuntime
        require(type(resource) is HardwareRuntime and type(intention) is ReceiveIntention
                and type(scope) is parent.Scope and scope.role == 'native' and scope.owner_uid == 0
                and scope.current_host_boot_id == intention.host_boot_id
                and scope.observation_expires_epoch_us <= intention.application_expires_epoch_us
                and callable(clock_reader) and callable(boot_reader))
        self.resource, self.intention, self.scope = resource, intention, scope
        self.state = Path(state).absolute()
        self.directory = self.state / 'parents' / scope.parent_id
        self._clock, self._boot = clock_reader, boot_reader
        self._owner = os.getpid(), threading.get_ident()
        self._closed = self._uncertain = False
        self._gate = self._gate_ref = threading.RLock()
        self._busy = 0
        self._reader = self._reader_ref = None
        self._reader_close = None
        self._observation = parent._Observation(scope, clock_reader, boot_reader)
        self._refs = (resource, intention, scope, self.state, self.directory,
                      clock_reader, boot_reader, self._observation)
        self._native_refs = (resource.supervisor, resource.probe, resource.probe.client,
                            resource.identity, resource.identity._signing_key, resource._mutex)
        self._configuration = _canonical(resource.config)
        self._intention = _canonical(intention.to_dict())
        self._prepared = None
        try:
            self._sample()
            self._reader = self._reader_ref = Reader()
            # Shared owner locks prohibit an exclusive producer while this
            # create-free observer is holding the exact original descriptions.
            require(self.read('native.sqlite.owner', 4096, lock=True) == b'')
            self._absent()
            raw = self.read('native.sqlite', parent.MAX_DB)
            require(hashlib.sha256(raw).hexdigest() == scope.database_sha256
                    and len(raw) >= 100 and raw[:16] == b'SQLite format 3\0'
                    and raw[18] in (1, 2) and raw[19] in (1, 2))
            image = bytearray(raw)
            image[18] = image[19] = 1
            db = sqlite3.connect(':memory:', isolation_level=None)
            try:
                require(hasattr(db, 'deserialize'))
                db.deserialize(bytes(image))
                db.execute('PRAGMA query_only=ON')
                db.execute('PRAGMA trusted_schema=OFF')
                require(db.execute('PRAGMA query_only').fetchone() == (1,))
                parent._schema(db, 'native')
                policy, detail, _ = parent._native(db, scope, self)
            finally:
                db.close()
            self._absent()
            self._reader.check()
            self._debt = Cost(**detail['prior_plus_prepaid_caps'])
            # This evidence explicitly preserves the old parser's limitations;
            # only private root approval can grant a new, bounded operation.
            evidence = dict(schema='sage.lantern.held-native-parent-accounting.v1',
                scope=asdict(scope), policy=policy.to_dict(), detail=detail,
                state=str(self.state), intention=intention.to_dict(),
                retained_debt=asdict(self._debt), actual_usage=None,
                prior_lineage_verified=False, complete_global_debt=False,
                private_dispatch_authority=False, physical_retirement_proved=False,
                actual_plus_prepaid_sum_permitted=False, refunds=0,
                files={path.name: hashlib.sha256(data).hexdigest()
                       for path, _, _, data in self._reader.held})
            encoded = _canonical(evidence)
            self._evidence = encoded
            self._evidence_sha256 = hashlib.sha256(_DOMAIN + encoded).hexdigest()
            self._prepared = (self._debt, _canonical(asdict(self._debt)), encoded, self._evidence_sha256)
            self.observe(resource, intention)
            token = self._reader.prepare_close_delegation()
            self._reader_close = self._reader, type(self._reader).close_delegated, token
        except BaseException:
            self._uncertain = True
            self.close()
            raise

    def __repr__(self):
        return '<HeldParentAccounting>'

    def _bound(self):
        require(not self._closed and not self._uncertain
                and self._owner == (os.getpid(), threading.get_ident()))
        h = self.resource
        require(all(a is b for a, b in zip(self._refs, (h, self.intention, self.scope,
            self.state, self.directory, self._clock, self._boot, self._observation))))
        require(all(a is b for a, b in zip(self._native_refs, (h.supervisor, h.probe,
            h.probe.client, h.identity, h.identity._signing_key, h._mutex))))
        require(h.supervisor._resource is h and h.supervisor.phase == 'active'
                and not h._closed and not h._closing and not h._uncertain
                and h.probe.actor == h.actor
                and _canonical(h.config) == self._configuration
                and _canonical(self.intention.to_dict()) == self._intention)
        if self._reader is not None:
            require(self._reader is self._reader_ref and type(self._reader) is Reader)
        if self._prepared is not None:
            debt, encoded_debt, evidence, digest = self._prepared
            require(self._debt is debt and type(debt) is Cost
                    and _canonical(asdict(debt)) == encoded_debt
                    and self._evidence is evidence and self._evidence_sha256 == digest)

    def _sample(self):
        self._bound()
        now = self._observation()
        self._bound()
        require(now[0] < self.intention.application_expires_epoch_us
                and now[1] < self.intention.application_deadline_monotonic_us)
        return now

    def _absent(self):
        self._bound()
        # Reader.check() covers the held parent and complete ancestry before
        # and after these create-free named checks. An appearing sidecar or
        # uncertainty marker is refused, never interpreted as zero debt.
        self._reader.check()
        for name in (*_SIDECARS, 'native.sqlite.pending'):
            require(not os.path.lexists(self.directory / name))
        self._reader.check()
        self._bound()

    @_accounting_operation
    def read(self, name, limit, *, lock=False):
        """Fixed-basename facade for the existing bounded native SQL parser."""
        require(name in _BASENAMES and name != 'native.sqlite.pending')
        self._sample()
        value = self._reader.read(self.directory / name, limit, lock=lock)
        self._sample()
        return value

    @_accounting_operation
    def observe(self, resource, intention):
        try:
            self._bound()
            require(resource is self._refs[0] and intention is self._refs[1]
                    and self._prepared is not None)
            self._sample()
            self._absent()
            require(self._reader.read_held(self.directory / 'native.sqlite.owner',
                                          4096, lock=True) == b'')
            self._reader.check()
            self._absent()
            now = self._sample()
            return NativeAccountingObservation(self._debt, None, self._evidence_sha256,
                self.scope.current_host_boot_id, now[0], now[1])
        except BaseException:
            self._uncertain = True
            raise

    @_accounting_operation
    def evidence(self):
        self._bound()
        # Read-only explanatory data, with its limitations retained.
        import json
        return json.loads(self._evidence)

    def _close_retained(self):
        if self._closed:
            return not self._uncertain
        self._closed = True
        reader = self._reader_ref
        if reader is not None:
            if self._reader_close is not None:
                retained, close, token = self._reader_close
                if (retained is not reader or retained is not self._reader
                    or type(retained) is not Reader or close is not Reader.close_delegated):
                    self._uncertain = True
                try:
                    if close(retained, token) is not True:
                        self._uncertain = True
                except BaseException:
                    self._uncertain = True
                    raise
                return not self._uncertain
            try:
                # Never run a live clock/permission check while containing an
                # expired observer. Reader closes only its retained OFDs.
                reader._available()
                require(all(os.lseek(fd, 0, os.SEEK_CUR) == token
                            for fd, token in reader._token_state))
            except Exception:
                self._uncertain = True
            finally:
                try:
                    reader.close()
                except BaseException:
                    self._uncertain = True
                    raise
        return not self._uncertain

    def close(self):
        if (self._owner != (os.getpid(), threading.get_ident())
            or self._gate is not self._gate_ref or not self._gate.acquire(blocking=False)):
            return False
        try:
            if self._busy:
                return False
            return self._close_retained()
        finally:
            self._gate.release()

    def close_from_registry(self, permit):
        """Cleanup under one idle registry permit after both native legs close.

        The creator-prepared Reader token grants only descriptor cleanup. The
        registry separately proves the original owner is fenced, all producers
        have unwound and every tracked child has terminal cleanup. Neither token
        authorizes observing files, renewing the scope or dispatching traffic.
        """
        from .forward_children import ForwardClosePermit
        if (type(permit) is not ForwardClosePermit
            or permit.original_owner != self._owner or os.getpid() != self._owner[0]
            or permit.resource is not self.resource or self.resource is not self._refs[0]
            or self._gate is not self._gate_ref or not self._gate.acquire(blocking=False)):
            return False
        try:
            if self._busy:
                return False
            require(permit.consume(self) is True)
            return self._close_retained()
        finally:
            self._gate.release()
