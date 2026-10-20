# SPDX-License-Identifier: Apache-2.0
"""Typed snapshot of one genuine live NativeCaps owner; never a grant.

Ordinary snapshots retain their independent owner/sidecar refusal. This entry
borrows the actual owned connection and its WAL-inclusive serialized image,
while retaining all ownership, permissions, source callbacks and typed rows.
"""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl,os,sqlite3,stat,threading
from pathlib import Path
from . import pilot_parent_snapshot as parent
from .pilot_parent_snapshot import (Scope,_Observation,_Reader,_schema,_native,_controller,
    _priors,_legacy_relation,_issue)
from .pilot_native_caps import NativeCaps
from . import pilot_debt_snapshot as debt
from .pilot_debt_snapshot import (need,canonical,sha,SnapshotError,MAX_DB,_identity)


class _OwnedImage:
    def __init__(self,caps,scope,observe):
        need(type(caps) is NativeCaps and type(scope) is Scope and scope.role=='native'
            and caps.unit.unit==scope.unit and caps.unit.host_boot_id==scope.current_host_boot_id
            and caps.policy.parent_id==scope.parent_id and caps.policy.digest==scope.policy_digest
            and caps.path.name=='native.sqlite' and not caps._busy,'genuine_native_owner_required')
        self.caps,self.scope,self.observe=caps,scope,observe
        self.refs=(caps,scope,observe,caps.db,caps._clock,caps._facts,caps._approve,caps._prior,
            caps._refs,caps._fds,caps._ids,NativeCaps._available,NativeCaps._observe,NativeCaps._validate)
        self.thread=(os.getpid(),threading.get_ident());self.dups=[];self.sidecars=[];self.absent=[];self._closed=False
        try:
            need(caps.current() is True,'native_owner_not_current')
            self.identity=tuple(caps._ids.items());self.fdstate=tuple(caps._fds.items())
            # A retained duplicate shares the original open description and EX
            # flock. The offset token distinguishes ordinary same-inode reopen.
            for name,fd in self.fdstate:
                held=os.fstat(fd);token=MAX_DB+fd+10000000;dup=os.dup(fd)
                self.dups.append((name,fd,dup,(held.st_dev,held.st_ino),token))
                os.lseek(dup,token,os.SEEK_SET)
            self.dups=tuple(self.dups)
            for suffix in ('-wal','-shm','-journal'):
                path=Path(str(caps.path)+suffix)
                try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
                except FileNotFoundError:self.absent.append(path);continue
                info=os.fstat(fd)
                try:
                    need(stat.S_ISREG(info.st_mode) and info.st_uid==scope.owner_uid
                        and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1 and info.st_size<=MAX_DB,
                        'owned_snapshot_sidecar_unsafe')
                    raw=os.pread(fd,MAX_DB+1,0);need(len(raw)==info.st_size,'owned_snapshot_sidecar_short')
                    token=MAX_DB+fd+20000000;os.lseek(fd,token,os.SEEK_SET)
                    self.sidecars.append((path,fd,_identity(info),raw,token))
                except BaseException:os.close(fd);raise
            self.sidecars=tuple(self.sidecars);self.absent=tuple(self.absent)
            fcntl.flock(caps._fds['owner'],fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.changes=caps.db.total_changes
            self.versions=(caps.db.execute('PRAGMA data_version').fetchone(),caps.db.execute('PRAGMA schema_version').fetchone())
            self.raw=caps.db.serialize();need(len(self.raw)<=MAX_DB,'owned_image_too_large')
            self._held=(self.dups,self.sidecars,self.absent,self.raw,self.changes,self.versions,self.identity,self.fdstate)
            self.check()
        except BaseException:
            self.close();raise
    def check(self):
        c=self.caps
        need(not self._closed and (os.getpid(),threading.get_ident())==self.thread
            and all(a is b for a,b in zip((c,self.scope,self.observe,c.db,c._clock,c._facts,c._approve,c._prior,
                c._refs,c._fds,c._ids,NativeCaps._available,NativeCaps._observe,NativeCaps._validate),self.refs)),
            'owned_snapshot_reference_changed')
        need((self.dups,self.sidecars,self.absent,self.raw,self.changes,self.versions,self.identity,self.fdstate)==self._held,
            'owned_snapshot_capture_changed')
        c._available();need(not c._busy and not c._uncertain and not c.pending.exists()
            and tuple(c._ids.items())==self.identity and tuple(c._fds.items())==self.fdstate,
            'owned_snapshot_owner_uncertain')
        for name,fd,dup,identity,token in self.dups:
            need((os.fstat(fd).st_dev,os.fstat(fd).st_ino)==identity
                 ==(os.fstat(dup).st_dev,os.fstat(dup).st_ino)
                 and os.lseek(fd,0,os.SEEK_CUR)==os.lseek(dup,0,os.SEEK_CUR)==token,
                 'owned_snapshot_open_description_changed')
        for path,fd,identity,raw,token in self.sidecars:
            need(_identity(os.fstat(fd))==identity==_identity(path.lstat())
                and os.lseek(fd,0,os.SEEK_CUR)==token and os.pread(fd,len(raw)+1,0)==raw,
                'owned_snapshot_sidecar_changed')
        need(all(not path.exists() and not path.is_symlink() for path in self.absent),'owned_snapshot_sidecar_appeared')
        # Prove the retained lock excludes a separately opened owner, never
        # accept another owner's lock as continuity of this open description.
        probe=os.open(c.path.with_name(c.path.name+'.owner'),os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        try:
            need((os.fstat(probe).st_dev,os.fstat(probe).st_ino)==c._ids['owner'],'owned_snapshot_lock_identity_changed')
            try:fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:pass
            else:need(False,'owned_snapshot_exclusive_lock_lost')
        finally:os.close(probe)
        # Revalidate through the existing read/current approval contract. The
        # genuine RPC approval callback rejects unrecognised phases, including
        # the former 'owned-snapshot' label. No new authority is needed to read
        # the already owned connection and its immutable serialized image.
        self.observe();c._observe('current');c._validate();c._available()
        need((self.dups,self.sidecars,self.absent,self.raw,self.changes,self.versions,self.identity,self.fdstate)==self._held,
            'owned_snapshot_capture_changed')
        need(c.db.total_changes==self.changes and not c.db.in_transaction
            and (c.db.execute('PRAGMA data_version').fetchone(),c.db.execute('PRAGMA schema_version').fetchone())==self.versions
            and c.db.serialize()==self.raw,'owned_snapshot_database_changed')
        self.observe();c._available()
        need((self.dups,self.sidecars,self.absent,self.raw,self.changes,self.versions,self.identity,self.fdstate)==self._held,
            'owned_snapshot_capture_changed')
        for name,fd,dup,identity,token in self.dups:
            need((os.fstat(fd).st_dev,os.fstat(fd).st_ino)==identity==(os.fstat(dup).st_dev,os.fstat(dup).st_ino)
                and os.lseek(fd,0,os.SEEK_CUR)==os.lseek(dup,0,os.SEEK_CUR)==token,'owned_snapshot_open_description_changed')
        for path,fd,identity,raw,token in self.sidecars:
            need(_identity(os.fstat(fd))==identity==_identity(path.lstat())
                and os.lseek(fd,0,os.SEEK_CUR)==token and os.pread(fd,len(raw)+1,0)==raw,'owned_snapshot_sidecar_changed')
        need(all(not path.exists() and not path.is_symlink() for path in self.absent)
            and c.db.total_changes==self.changes and not c.db.in_transaction
            and (c.db.execute('PRAGMA data_version').fetchone(),c.db.execute('PRAGMA schema_version').fetchone())==self.versions
            and c.db.serialize()==self.raw,'owned_snapshot_database_changed')
    def close(self):
        if self._closed:return
        self._closed=True
        captured=getattr(self,'_held',(tuple(self.dups),tuple(self.sidecars)))[0]
        for name,fd,dup,identity,token in captured:
            try:
                held=os.fstat(dup)
                if (held.st_dev,held.st_ino)==identity and os.lseek(dup,0,os.SEEK_CUR)==token:os.close(dup)
            except OSError:pass
        for path,fd,identity,raw,token in getattr(self,'_held',(None,tuple(self.sidecars)))[1]:
            try:
                if _identity(os.fstat(fd))[:2]==identity[:2] and os.lseek(fd,0,os.SEEK_CUR)==token:os.close(fd)
            except OSError:pass


class _OwnedReader(_Reader):
    def __init__(self,state,scope,observe,image):
        need(type(image) is _OwnedImage and image.caps.path==Path(state)/'parents'/scope.parent_id/'native.sqlite',
            'owned_snapshot_state_changed')
        self.image=image
        super().__init__(state,scope,observe)
    @contextmanager
    def database(self):
        image=self.image;image.check()
        need(self.read(self.name+'.owner',4096)==b'','invalid_owner_lock')
        raw=self.read(self.name,MAX_DB);need(sha(raw)==self.scope.database_sha256,'database_hash_mismatch')
        need(self.read(self.name+'.approval',4096)==image.caps._approval_bytes,'owned_snapshot_approval_changed')
        image.check();data=bytearray(image.raw);need(len(data)>=100 and data[:16]==b'SQLite format 3\0','invalid_sqlite_image')
        data[18]=data[19]=1
        db=sqlite3.connect(':memory:',isolation_level=None)
        try:
            db.deserialize(bytes(data));db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
            need(db.execute('PRAGMA query_only').fetchone()==(1,),'owned_snapshot_memory_not_readonly')
            yield db
            image.check();self.check()
        finally:db.close()


def collect_owned_native(state, scope, *, caps, clock, boot_reader, legacy_scope=None):
    """Observe fixed native layout or an explicitly scoped controller directory.

    Controller `state` is its independently selected public ledger directory,
    not the native state root. Scope pins are observations, never approval.
    """
    need(type(scope) is Scope and scope.role=='native' and type(caps) is NativeCaps
        and clock is caps._clock, 'invalid_owned_scope')
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
    image = None
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
        sqlite_parser=dict(source='genuine_native_owner_serialized_image', native_path_opened=False, query_only_memory=True,
            wal_header_normalized_in_memory_only=True, whole_sqlite_consistency_proved=False))
    try:
        joint_observe()
        image = _OwnedImage(caps,scope,joint_observe)
        reader = _OwnedReader(state,scope,joint_observe,image)
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
        image.check(); reader.check()
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
        if image is not None:
            image.close()
    if observe.first is not None:
        report.update(observation_original_epoch_us=observe.first[0], observation_original_monotonic_us=observe.first[1],
            observation_expires_epoch_us=scope.observation_expires_epoch_us,
            observation_deadline_monotonic_us=observe.deadline,
            observation_final_epoch_us=observe.last[0], observation_final_monotonic_us=observe.last[1])
    return _issue(report, rows)



def match_checkpoint_projection(actual,observed,*,scope,handoff_epoch_us,handoff_monotonic_us):
    """Only a fresh native stored clock may advance; every other byte is fixed."""
    keys=('status','scoped_rows_complete','pending_unknown','policy_row_sha256','prior_lineage',
        'files','legacy_runs','legacy_relation')
    need(type(actual) is dict and type(observed) is dict and type(scope) is Scope
        and actual['status']=='scoped_rows_observed' and actual['scoped_rows_complete'] is True
        and actual['pending_unknown'] is False and all(canonical(actual[k])==canonical(observed[k]) for k in keys),
        'checkpoint_actual_A_ledger_changed')
    a,b=actual['detail'],observed['detail']
    need(type(a) is dict and type(b) is dict and set(a)==set(b) and 'stored_clock' in a
        and canonical({k:v for k,v in a.items() if k!='stored_clock'})
            ==canonical({k:v for k,v in b.items() if k!='stored_clock'}),'checkpoint_actual_A_ledger_changed')
    original=a['enrollment'];before=b['stored_clock'];after=a['stored_clock']
    need(type(before) is dict and type(after) is dict and set(before)==set(after)=={'epoch_us','monotonic_us'}
        and all(type(v) is int for v in (*before.values(),*after.values()))
        and original['original_epoch_us']<=before['epoch_us']<=after['epoch_us']
            <=actual['observation_final_epoch_us']<min(scope.observation_expires_epoch_us,handoff_epoch_us)
        and original['original_monotonic_us']<=before['monotonic_us']<=after['monotonic_us']
            <=actual['observation_final_monotonic_us']<min(original['deadline_monotonic_us'],handoff_monotonic_us),
        'checkpoint_owned_stored_clock_changed')
    return True
