# SPDX-License-Identifier: Apache-2.0
"""PRIVATE source-only resume guard; no enrollment, transport or radio entry.

Full actual inventory/lineage verification and runtime source verification must
precede seal capture outside the application deadline. install_seals only stores
already captured immutable baselines. Its returned binding digest MUST be in
both protected owner/prior records before genuine NativeCaps enrollment. The
binding excludes policy digest: construct prior, registry and policy AFTER it.

record_prepared must run inside the reviewed provision operation immediately
after its final rename, with the same genuine NativeCaps instance. Existing
stock SSH prepare cannot supply that integration. A later RPC must not call it
to repair a missing identity; it deliberately refuses existing records.

ResumeParentGuard uses canonical NativeCaps.open/current with genuine clock, facts
and protected approval callbacks. Caller record digests must be pinned by the
reviewed owner, not obtained by hashing whatever currently exists. Runtime
validation is a mandatory independent held-source callback, never this guard's
authority. Historical SQLite contents are not interpreted here.
"""
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .history_identity_seal import _Ancestry, SealedHistory, MAX_SEAL
from .history_metadata_fence import FenceError, _FLAGS, _need, _stamp
from .legacy_grant_fence import LegacyGrantFence
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps, Preparation, hashed
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy, canonical
from .retained_capture import CAPTURE_PARENT, CAPTURE_ONLY_PARENTS, UNADMITTED_PARENT, PAID_HISTORY as HISTORY, historical_parent_ids, current_clock
HEX = re.compile('[0-9a-f]{64}\\Z')
SID = re.compile('[0-9a-f]{32}\\Z')
BINDING = 'meshticulum.private-resume-history-binding.v2'
IDENTITY = 'meshticulum.private-resume-prepared-identity.v2'


def _hash(raw): return hashlib.sha256(raw).hexdigest()


def _parse(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _need(key not in value, 'duplicate_metadata_key'); value[key] = item
        return value
    try: return json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise FenceError('invalid_metadata_json') from error


def _read(parent, name, maximum):
    """Read no opaque key/payload paths; caller supplies a held safe parent FD."""
    _need(name in ('owner.json', 'prior.json', 'policy.json', 'provision.json',
                  'history-identity-seal.json', 'legacy-grant-seal.json', 'history-binding.json')
          or SID.fullmatch(name.removesuffix('.json')), 'prohibited_guard_file')
    fd = os.open(name, _FLAGS, dir_fd=parent)
    try:
        first = os.fstat(fd)
        _need(stat.S_ISREG(first.st_mode) and first.st_uid == os.geteuid()
              and stat.S_IMODE(first.st_mode) == 0o600 and first.st_nlink == 1
              and 0 <= first.st_size <= maximum, 'unsafe_guard_metadata')
        raw = os.pread(fd, maximum + 1, 0)
        _need(len(raw) == first.st_size and _stamp(first) == _stamp(os.fstat(fd))
              == _stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)), 'guard_metadata_changed')
        return raw
    finally: os.close(fd)


def _write(parent, name, raw):
    _need(type(raw) is bytes and len(raw) <= MAX_SEAL, 'invalid_guard_write')
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent)
    try:
        _need(stat.S_IMODE(os.fstat(fd).st_mode) == 0o600, 'wrong_created_mode')
        view = memoryview(raw)
        while view:
            done = os.write(fd, view); _need(done > 0, 'short_guard_write'); view = view[done:]
        os.fsync(fd)
    finally: os.close(fd)
    os.fsync(parent)


def install_seals(state, parent_id, unit, strict_raw, legacy_raw, *, clock=current_clock):
    """O_EXCL installation only; does NOT claim capture provenance or enroll.

    The caller must create exactly this own parent after independently checking
    the old namespace and capture both seals BEFORE that namespace grows.
    Returned bytes must be bound into own owner/prior before enrollment.
    """
    _need(type(parent_id) is str and SID.fullmatch(parent_id) and parent_id not in HISTORY+CAPTURE_ONLY_PARENTS
          and unit in ('unit-a','unit-b','unit-c'), 'invalid_own_identity')
    strict, legacy = _parse(strict_raw), _parse(legacy_raw)
    expected_history = historical_parent_ids(state,unit,clock,
        require_retained=CAPTURE_PARENT in strict['directories']['parents']['entries'],
        require_unadmitted=UNADMITTED_PARENT in strict['directories']['parents']['entries'])
    _need(strict['schema'] == 'meshticulum.private-history-identity-seal.v2'
          and legacy['schema'] == 'meshticulum.private-legacy-grant-fence.v1'
          and type(legacy['rows']) is dict and 0 < len(legacy['rows']) <= 128
          and strict['uid'] == legacy['uid'] == os.geteuid()
          and sorted(strict['directories']['parents']['entries']) == sorted(expected_history),
          'wrong_historical_parent_seal')
    _need(type(strict_raw) is bytes and type(legacy_raw) is bytes
          and max(len(strict_raw), len(legacy_raw)) <= MAX_SEAL, 'seal_size_bound')
    target = Path(state).absolute()/'parents'/parent_id
    with _Ancestry(target, os.geteuid()) as ancestry:
        binding = dict(schema=BINDING, parent_id=parent_id,
            unit=unit, strict_sha256=_hash(strict_raw), legacy_sha256=_hash(legacy_raw),
            legacy_rows_sha256=_hash(canonical(legacy['rows'])),
            parent_identity=list(_stamp(os.fstat(ancestry.fds[-1]))[:4]))
        os.mkdir('prepared-identities', mode=0o700, dir_fd=ancestry.fds[-1])
        for name, raw in (('history-identity-seal.json', strict_raw),
                          ('legacy-grant-seal.json', legacy_raw), ('history-binding.json', canonical(binding))):
            ancestry.check(); _write(ancestry.fds[-1], name, raw)
        ancestry.check()
    raw = canonical(binding)
    return raw, _hash(raw)


def _actual_prepared(caps):
    """Actual canonical ledger only; receipts/DTOs cannot create claim rows."""
    _need(type(caps) is NativeCaps, 'not_genuine_native_caps')
    rows, _ = caps._validate()
    prepared = caps.db.execute('SELECT id,body FROM prepared').fetchall()
    by_claim = {claim_id: (raw, run) for claim_id, raw, run in rows}
    result = {}
    for claim_id, raw in prepared:
        _need(claim_id in by_claim, 'prepared_without_paid_claim')
        receipt = _parse(raw); claim_raw, run = by_claim[claim_id]; claim = _parse(claim_raw)
        _need(hashed(claim) == claim_id and canonical(receipt) == raw
              and receipt['native_claim_id'] == claim_id and receipt['run_id'] == run
              and receipt['parent_id'] == caps.policy.parent_id and receipt['policy_digest'] == caps.policy.digest
              and receipt['unit'] == caps.unit.unit and receipt['host_boot_id'] == caps.unit.host_boot_id
              and receipt['child_caps'] == claim['child']
              and receipt['controller_allocation_id'] == claim['controller_allocation_id']
              and receipt['resource_sha256'] == claim['resource']['resource_sha256']
              and receipt['prepared'] is True and SID.fullmatch(run) and run not in result,
              'actual_prepared_relation_changed')
        result[run] = receipt
    return result


def _held_provision(chain, run, receipt):
    raw = _read(chain.fds[-1], 'provision.json', 32768); value = _parse(raw)
    _need(value['schema'] == 'meshticulum.provisioned-public-pilot.v2'
          and value['run_id'] == run and value['unit'] == receipt['unit']
          and value['parent'] == receipt, 'protected_provision_relation_changed')
    chain.check()
    return list(_stamp(os.fstat(chain.fds[-1]))[:4]), _hash(raw)


def _provision(state, run, receipt):
    with _Ancestry(Path(state)/'runs'/run, os.geteuid()) as chain:
        return _held_provision(chain, run, receipt)


def record_prepared(state, caps, preparation, held_staging_fd):
    """Missing integration hook: invoke during actual provision finalization.

    Requires original O_DIRECTORY staging FD retained by provision ACROSS rename,
    plus the SAME instance-owned finalized Preparation. A freshly reopened
    NativeCaps has no such object and cannot repair missing capture provenance.
    Reviewed integration must invoke it immediately after the final rename.
    Records one paid/prepared/provisioned run; never modifies native ledgers.
    """
    _need(type(caps) is NativeCaps and caps.current() is True, 'own_parent_not_current')
    _need(type(preparation) is Preparation and preparation._owner is caps
          and caps._preparations.get(id(preparation)) ==
              (preparation, preparation._claim_id, preparation._raw, True),
          'not_same_finalized_preparation')
    run = preparation.run_id; _need(SID.fullmatch(run), 'invalid_run')
    receipts = _actual_prepared(caps); _need(run in receipts, 'run_not_actually_prepared')
    target = Path(state)/'parents'/caps.policy.parent_id/'prepared-identities'
    with _Ancestry(Path(state)/'runs'/run, os.geteuid()) as run_chain, \
            _Ancestry(target, os.geteuid()) as chain:
        _need(type(held_staging_fd) is int, 'missing_original_staging_fd')
        held = os.fstat(held_staging_fd)
        flags = fcntl.fcntl(held_staging_fd, fcntl.F_GETFL)
        _need(stat.S_ISDIR(held.st_mode) and held.st_uid == os.geteuid()
              and stat.S_IMODE(held.st_mode) == 0o700
              and flags & os.O_ACCMODE == os.O_RDONLY
              and fcntl.fcntl(held_staging_fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
              and _stamp(held) == _stamp(os.fstat(run_chain.fds[-1])), 'renamed_staging_rebound')
        directory_identity, provision_sha = _held_provision(run_chain, run, receipts[run])
        record = dict(schema=IDENTITY, parent_id=caps.policy.parent_id, policy_digest=caps.policy.digest,
            unit=caps.unit.unit, run_id=run, directory_identity=directory_identity,
            prepared_sha256=_hash(canonical(receipts[run])), provision_sha256=provision_sha)
        _write(chain.fds[-1], run+'.json', canonical(record)); chain.check()
        _need(_held_provision(run_chain, run, receipts[run]) == (directory_identity, provision_sha)
              and _stamp(os.fstat(held_staging_fd)) == _stamp(os.fstat(run_chain.fds[-1]))
              and _actual_prepared(caps)[run] == receipts[run]
              and caps.current() is True, 'prepared_capture_drift')
    return record


class ResumeParentGuard:
    """PRELAUNCH or POSTTERMINAL management guard. Never supplies ARM/start.

    It opens canonical EX owner lock and must refuse overlap with any live job.
    All paired prelaunch guards must finish before first launch. It cannot be
    used as a live-worker callback or bypass native locking for convenience.

    The real callbacks validate independent boot/services, original phase clocks
    and protected approval. This guard never substitutes lambdas for them.
    Expected owner/prior/binding digests must come from the reviewed exact scope.
    Canonical native clock checks stay intact; history fences write nothing.
    No old database is opened with SQLite by this module.
    """
    def __init__(self, state, policy, unit, *, owner_sha256, prior_sha256,
                 binding_sha256, preparation_sha256, management_phase,
                 clock, facts, approve, runtime_check):
        self._stack, self.caps, self._closed, self._failed = ExitStack(), None, False, False
        try:
            _need(type(policy) is ParentPolicy and policy.parent_id not in HISTORY+CAPTURE_ONLY_PARENTS
                  and management_phase in ('prelaunch','postterminal')
                  and all(callable(c) for c in (clock, facts, approve, runtime_check)), 'invalid_guard_scope')
            _need(all(type(d) is str and HEX.fullmatch(d)
                      for d in (owner_sha256, prior_sha256, binding_sha256)), 'invalid_guard_pins')
            self.state, self.policy, self.unit = Path(state).absolute(), policy, unit
            self._runtime = runtime_check
            _need(type(preparation_sha256) is dict and len(preparation_sha256) <= 20
                  and all(type(run) is str and SID.fullmatch(run) and type(d) is str and HEX.fullmatch(d)
                          for run, d in preparation_sha256.items()), 'invalid_preparation_digest_pins')
            # Exact record digests from the reviewed held prepare capture, retained
            # in controller lineage. This map grants no run: actual paid/prepared
            # native rows independently determine the only permitted namespace.
            self._prepared_pins = dict(preparation_sha256)
            self._pins = dict(owner=owner_sha256, prior=prior_sha256, binding=binding_sha256)
            self._chain = self._stack.enter_context(_Ancestry(self.state/'parents'/policy.parent_id, os.geteuid()))
            self._own_raw = self._records(); binding = _parse(self._own_raw['binding'])
            _need(set(binding) == {'schema','parent_id','unit','strict_sha256',
                  'legacy_sha256','legacy_rows_sha256','parent_identity'} and binding['schema'] == BINDING
                  and binding['parent_id'] == policy.parent_id
                  and binding['unit'] == unit and binding['parent_identity'] == self._chain.records[-1][1],
                  'protected_binding_scope_changed')
            self._binding = binding
            _need(runtime_check() is True, 'runtime_source_refused')
            self.caps = NativeCaps.open(self.state/'parents'/policy.parent_id/'native.sqlite', policy,
                unit=unit, clock=clock, facts=facts, approve=approve)
            def close_caps(): _need(self.caps.close() is True, 'own_caps_close_uncertain')
            self._stack.callback(close_caps)
            strict_raw = _read(self._chain.fds[-1], 'history-identity-seal.json', MAX_SEAL)
            legacy_raw = _read(self._chain.fds[-1], 'legacy-grant-seal.json', MAX_SEAL)
            _need(_hash(strict_raw) == binding['strict_sha256']
                  and _hash(legacy_raw) == binding['legacy_sha256'], 'protected_seal_digest_changed')
            strict, legacy = _parse(strict_raw), _parse(legacy_raw)
            expected_history = historical_parent_ids(self.state,unit,clock,
                require_retained=CAPTURE_PARENT in strict['directories']['parents']['entries'],
                require_unadmitted=UNADMITTED_PARENT in strict['directories']['parents']['entries'])
            _need(sorted(strict['directories']['parents']['entries']) == sorted(expected_history)
                  and _hash(canonical(legacy['rows'])) == binding['legacy_rows_sha256'], 'protected_seal_scope_changed')
            self._identities = self._own_relations()
            self._history = self._stack.enter_context(SealedHistory(self.state, owner_uid=os.geteuid(),
                seal_bytes=strict_raw, expected_sha256=binding['strict_sha256'],
                own_directories=self._identities, validate_actual_relation=self._relation))
            def outer(): self._history.check(); return True
            self._legacy = self._stack.enter_context(LegacyGrantFence(self.state/'runs', owner_uid=os.geteuid(),
                expected_rows=list(legacy['rows'].values()), independently_verified=True,
                exact_namespace_gate=outer, seal_bytes=legacy_raw, expected_sha256=binding['legacy_sha256']))
            self.check()
        except BaseException:
            self.close(); raise

    def _records(self):
        self._chain.check(); fd = self._chain.fds[-1]
        raw = {key: _read(fd, name, limit) for key, name, limit in
               [('owner','owner.json',200000), ('prior','prior.json',200000),
                ('binding','history-binding.json',16384)]}
        _need(all(_hash(raw[key]) == digest for key, digest in self._pins.items()), 'own_guard_pin_changed')
        _need(_parse(raw['owner'])['history_binding_sha256'] == self._pins['binding']
              == _parse(raw['prior'])['history_binding_sha256'], 'owner_prior_seal_not_bound')
        owner = _parse(raw['owner']); local = self.policy.unit(self.unit)
        _need(owner['policy_digest'] == self.policy.digest
              and _hash(raw['prior']) == local.prior_snapshot_sha256
              and _hash(canonical(owner['prior_registry'])) == self.policy.prior_snapshot_sha256
              and owner['prior_registry'][self.unit]['observation_sha256'] == self._pins['prior'],
              'authentic_own_prior_hash_changed')
        policy_raw = _read(fd, 'policy.json', 20000)
        _need(policy_raw == canonical(self.policy.to_dict()), 'own_policy_changed')
        self._chain.check(); return raw

    def _own_relations(self):
        receipts = _actual_prepared(self.caps)
        owner = _parse(self._own_raw['owner'])
        if owner.get('radio_profile_name') == 'sf5-500':
            expected = dict(owner['rf_profile']); expected.pop('lease_ms')
            _need(owner.get('qualification_kind') == 'c-bulk-8k-v1'
                  and self.policy.qualification_kind is None, 'own_sf5_scope_changed')
            for receipt in receipts.values():
                metadata = receipt['child_caps']['metadata']
                selected = dict(metadata['profile']); lease = selected.pop('lease_ms')
                _need(metadata['transport'] == 'lora' and selected == expected
                      and type(lease) is int and 10000 <= lease <= 90000,
                      'own_sf5_child_profile_changed')
        _need(set(receipts) == set(self._prepared_pins), 'missing_or_foreign_preparation_digest')
        path = self.state/'parents'/self.policy.parent_id/'prepared-identities'
        with _Ancestry(path, os.geteuid()) as chain:
            _need(sorted(os.listdir(chain.fds[-1])) == sorted(run+'.json' for run in receipts),
                  'missing_or_foreign_preparation_identity')
            result = {'parents/'+self.policy.parent_id: tuple(self._binding['parent_identity'])}
            for run, receipt in receipts.items():
                raw = _read(chain.fds[-1], run+'.json', 4096)
                _need(_hash(raw) == self._prepared_pins[run], 'captured_preparation_digest_changed')
                record = _parse(raw)
                identity, provision_sha = _provision(self.state, run, receipt)
                expected = dict(schema=IDENTITY, parent_id=self.policy.parent_id, policy_digest=self.policy.digest,
                    unit=self.unit, run_id=run, directory_identity=identity,
                    prepared_sha256=_hash(canonical(receipt)), provision_sha256=provision_sha)
                _need(record == expected, 'captured_preparation_identity_changed')
                result['runs/'+run] = tuple(record['directory_identity'])
            chain.check(); return result

    def _relation(self, paths):
        _need(self._records() == self._own_raw, 'own_record_relation_refused')
        actual = self._own_relations()
        _need(actual == self._identities and tuple(sorted(actual)) == paths, 'actual_own_namespace_changed')
        return True

    def check(self):
        try:
            _need(not self._closed and not self._failed and self._records() == self._own_raw, 'guard_unavailable')
            _need(self._runtime() is True and self.caps.current() is True, 'runtime_or_current_refused')
            self._history.check(); self._legacy.check()
            _need(self._records() == self._own_raw and self.caps.current() is True, 'own_current_changed')
            return self.caps.snapshot()
        except BaseException:
            self._failed = True; raise

    def close(self):
        self._closed = True; self._stack.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
