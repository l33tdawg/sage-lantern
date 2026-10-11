# SPDX-License-Identifier: Apache-2.0
"""Private, bounded public loss/resume management RPC; never a bearer grant.

Capture re-observes retained typed rows before sealing their protected metadata.
Enrollment binds those actual capture bytes before any application starts.
Guards reopen immutable history and genuine NativeCaps; they never repair a
missing prepare capture, open radio, refund debt, or authorize private services.
Returned dictionaries are comparison receipts, never in-process Snapshots.
"""
import base64
from contextlib import ExitStack
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .history_identity_seal import _Ancestry, capture, SealedHistory
from .history_metadata_fence import _FLAGS, _FILES, _stamp, MAX_FILE
from .legacy_grant_fence import capture as legacy_capture, LegacyGrantFence
from .pilot_parent_snapshot import Scope, collect
from .pilot_debt_snapshot import Scope as DebtScope
from .pilot_parent_caps import ParentPolicy, Cost, canonical
from .pilot_native_caps import NativeCaps, NativeFacts
from .pilot_manage import facts as native_facts
from .resume_parent_guard import HISTORY, ResumeParentGuard, install_seals
from .retained_capture import historical_parent_ids, capture_paths, CAPTURE_PARENT
from .profiles import profile_for
from meshticulum.ip.wire import IpLimits
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.contracts.identity import PeerID

CONTRACT_SCHEMA = 'meshticulum.private-resume-native-rpc.v1'
STATE = Path('/var/lib/meshticulum-bench')
OLD_DIGESTS = ('86975afb2e94de9ae55c86269409f4d8456475499fc20995cd2bab6a4f4ed0af',
    '6d337c78faf8c2dbea7a0a0d29e82aa8af8d1452f954fb6b664106459c33d256',
    '1ca1774780928427f15befecf723064cd87aff6ad402b6ef94b137f5d45f6d33')
HEX = re.compile(r'[0-9a-f]{64}\Z')
SID = re.compile(r'[0-9a-f]{32}\Z')


def need(value, reason):
    if not value: raise RuntimeError(reason)


def sha(raw): return hashlib.sha256(raw).hexdigest()
def clock(): return time.time_ns()//1000, time.monotonic_ns()//1000
def boot(): return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def stable(value): return {k:value[k] for k in ('unit','host_boot_id','services','service_lifetimes')}


def parse(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, 'duplicate_rpc_metadata_key'); value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique)


class Reader:
    """Held protected regular metadata and read locks, with no SQLite path open."""
    def __init__(self): self.stack, self.held, self.directories = ExitStack(), [], {}
    def directory(self, path):
        with _Ancestry(path, os.geteuid()) as chain:
            chain.check(); identity = list(_stamp(os.fstat(chain.fds[-1]))[:4])
            need(path not in self.directories or self.directories[path] == identity, 'rpc_directory_rebound')
            self.directories[path] = identity
    def read(self, path, maximum, *, lock=False):
        self.directory(path.parent)
        with _Ancestry(path.parent, os.geteuid()) as chain:
            fd = os.open(path.name, _FLAGS, dir_fd=chain.fds[-1]); chain.check()
        self.stack.callback(os.close, fd)
        s = os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
             and stat.S_IMODE(s.st_mode) == 0o600 and s.st_nlink == 1
             and 0 <= s.st_size <= maximum, 'unsafe_rpc_metadata')
        if lock: fcntl.flock(fd, fcntl.LOCK_SH|fcntl.LOCK_NB)
        raw = os.pread(fd, maximum+1, 0)
        need(len(raw) == s.st_size, 'short_rpc_metadata')
        identity = _stamp(s)
        need(_stamp(os.fstat(fd)) == identity == _stamp(path.lstat()), 'rpc_metadata_changed')
        self.held.append((path, fd, identity, sha(raw))); return raw
    def check(self):
        for path, identity in self.directories.items():
            with _Ancestry(path, os.geteuid()) as chain:
                need(list(_stamp(os.fstat(chain.fds[-1]))[:4]) == identity, 'rpc_directory_rebound')
        for path, fd, identity, digest in self.held:
            need(_stamp(os.fstat(fd)) == identity == _stamp(path.lstat())
                 and sha(os.pread(fd, identity[5]+1, 0)) == digest, 'held_rpc_metadata_changed')
        return True
    def close(self): self.stack.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class Runtime:
    """Full independently pinned installed SDK-free source closure, held per RPC."""
    def __init__(self, root, digest):
        need(type(digest) is str and HEX.fullmatch(digest), 'invalid_runtime_manifest_pin')
        self.root, self.reader = Path(root), Reader()
        try:
            need(self.root.is_absolute() and self.root.parent == Path('/opt/meshticulum-bench')
                 and re.fullmatch(r'runtime-[0-9a-f]{16}', self.root.name), 'unsafe_runtime_root')
            raw = self.reader.read(self.root/'manifest.json', 65536)
            need(sha(raw) == digest, 'runtime_manifest_changed'); self.manifest = parse(raw)
            m = self.manifest
            need(m['private_services_enabled'] is False and m['public_parent_claims_required'] is True
                 and type(m['files']) is dict and 75 <= len(m['files']) <= 150, 'wrong_public_runtime')
            for name, pin in m['files'].items():
                part = Path(name)
                need(type(name) is str and not part.is_absolute() and '..' not in part.parts
                     and '.' not in part.parts and HEX.fullmatch(pin), 'unsafe_runtime_member')
                need(sha(self.reader.read(self.root/part, 500000)) == pin, 'runtime_payload_changed')
            relative = 'lantern_meshticulum/bench/pilot_resume_rpc.py'
            need(relative in m['files'] and Path(__file__).absolute() == self.root/relative,
                 'RPC_not_installed_pinned_source')
            self.check()
        except BaseException: self.close(); raise
    def check(self): return self.reader.check()
    def close(self): self.reader.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def phase_check(unit, expected, start, end, phase):
    now = clock()
    need(type(start) is int and type(end) is int and start <= now[0] < end
         and phase['epoch_us'] <= now[0] and phase['monotonic_us'] <= now[1]
         < phase['deadline_monotonic_us'] and end-start <= 899000000,
         'original_native_phase_expired')
    observed = stable(native_facts(unit))
    need(observed == expected and observed['host_boot_id'] == boot(), 'native_facts_changed')
    return observed


def write_new(directory, name, raw):
    need(type(raw) is bytes and len(raw) <= 2000000, 'RPC_write_bound')
    with _Ancestry(directory, os.geteuid()) as chain:
        fd = os.open(name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
                     0o600, dir_fd=chain.fds[-1])
        try:
            view = memoryview(raw)
            while view:
                done = os.write(fd, view); need(done > 0, 'short_rpc_write'); view = view[done:]
            os.fsync(fd)
        finally: os.close(fd)
        os.fsync(chain.fds[-1]); chain.check()


def full_history(reader, unit, history, guard, *, own_parent=None, own_runs=()):
    """Actual three-parent collect plus all original full grants, not DTO authority."""
    old = history['previous']; inventory = history['legacy_inventory']
    policies = [ParentPolicy.from_dict(v['policy']) for v in old]
    need(tuple(p.parent_id for p in policies) == HISTORY
         and tuple(p.digest for p in policies) == OLD_DIGESTS, 'wrong_retained_parents')
    original = {r['run_id']:r for r in inventory['rows']}
    newer = {r['run_id']:r for v in old for r in v['runs']}
    need(len(original) == {'unit-a':41,'unit-b':64,'unit-c':63}[unit]
         and len(original) == len(inventory['rows']) and not set(original)&set(newer), 'wrong_legacy_inventory')
    known, reports = {}, []
    def names():
        reader.directory(STATE); reader.directory(STATE/'parents'); reader.directory(STATE/'runs')
        retained = historical_parent_ids(STATE,unit,clock,require_retained=True)
        need(own_parent != CAPTURE_PARENT
             and sorted(os.listdir(STATE/'parents')) == sorted(retained+((own_parent,) if own_parent else ()))
             and sorted(os.listdir(STATE/'runs')) == sorted(set(original)|set(newer)|set(own_runs)),
             'foreign_or_missing_historical_namespace')
    def metadata(path, limit=MAX_FILE, lock=False):
        raw = reader.read(path, limit, lock=lock); known[str(path.relative_to(STATE))] = sha(raw); return raw
    guard(); names(); totals = {k:0 for k in inventory['full_caps_retained_totals']}
    for relative, pin in capture_paths(STATE,unit)['files'].items():
        need(sha(metadata(STATE/relative)) == pin, 'retained_capture_file_changed')
    for run, row in list(original.items())+list(newer.items()):
        target = STATE/'runs'/run; reader.directory(target)
        raw = metadata(target/'grant.json', 16384); g = parse(raw)
        need(sha(raw) == row['grant_sha256'] and g['expires_epoch_us'] < clock()[0], 'old_grant_changed_or_live')
        marker = metadata(target/'active', 128, True)
        need(marker == ((row['active_marker']+'\n').encode() if run in original else b'retired\n'),
             'old_active_marker_changed')
        if run not in original: continue
        need(g['issued_epoch_us'] == row['original_issued_epoch_us']
             and g['expires_epoch_us'] == row['original_expires_epoch_us']
             and g.get('host_boot_id') == row['host_boot_id'] == boot()
             and row['actual_cost'] is None, 'old_original_binding_changed')
        if g['schema'] == 'meshticulum.ip-service-run.v1':
            need(g['session']['session_id'] == run, 'old_IP_session_changed'); limits = g['limits']
            cost = dict(ip_frames=limits['max_frames'],tx_stream_bytes=limits['max_tx_stream_bytes'],
                        rx_stream_bytes=limits['max_rx_stream_bytes'],radio_attempts=0,radio_estimated_airtime_us=0)
        else:
            need(g['schema'] in ('meshticulum.bench-run.v1','meshticulum.service-run.v1')
                 and g['session_id'] == run and g['unit'] == unit, 'old_radio_session_changed')
            cost = dict(radio_attempts=g['profile']['max_boot_attempts'],
                radio_estimated_airtime_us=g['profile']['max_boot_estimated_airtime_us'],
                ip_frames=0,tx_stream_bytes=0,rx_stream_bytes=0)
        need(cost == row['caps'], 'original_full_caps_changed')
        for key, value in cost.items():
            need(type(value) is int and value >= 0, 'unsafe_old_cost'); totals[key] += value
    need(totals == inventory['full_caps_retained_totals'], 'original_cap_sum_changed')
    for index, (v, policy) in enumerate(zip(old, policies)):
        guard(); target = STATE/'parents'/policy.parent_id; reader.directory(target)
        prior = inventory if v['prior_kind'] == 'legacy' else dict(v['prior_metadata'],legacy_inventory=inventory)
        for name, value in (('policy.json',v['policy']),('owner.json',v['owner']),('prior.json',prior)):
            need(metadata(target/name,200000) == canonical(value), 'old_parent_input_changed')
        need(policy.expires_epoch_us < clock()[0] and policy.unit(unit).host_boot_id == boot(), 'old_parent_live_or_rebooted')
        need(sha(metadata(target/'native.sqlite')) == v['database_sha256'], 'old_native_database_changed')
        end = min(clock()[0]+35000000, guard()['phase_end_epoch_us'])
        legacy = None
        if v['runs']:
            legacy = DebtScope.from_dict(dict(schema='meshticulum.public-debt-scope.v1',unit=unit,
                current_host_boot_id=boot(),runtime_sha256=v['owner']['runtime_sha256'],
                manifest_sha256=v['owner']['manifest_sha256'],observation_expires_epoch_us=end,runs=v['runs']))
        observed = collect(STATE,Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,
            policy.parent_id,policy.digest,v['database_sha256'],unit,boot(),end),
            clock=clock,boot_reader=boot,legacy_scope=legacy).to_dict()
        need(observed['status'] == 'scoped_rows_observed' and observed['scoped_rows_complete']
             and observed['policy_expired_at_observation'] and not observed['pending_unknown'], 'old_parent_uncertain')
        need(sha(canonical({k:observed[k] for k in v['projection_keys']})) == v['projection_sha256'], 'old_parent_rows_changed')
        need(observed['prior_lineage']['global_snapshot_sha256'] == policy.prior_snapshot_sha256
             == sha(canonical(v['owner']['prior_registry'])), 'old_global_lineage_changed')
        local = next(r for r in observed['prior_lineage']['units'] if r['unit'] == unit)
        need(local['prior_snapshot_sha256'] == sha(canonical(prior)) == policy.unit(unit).prior_snapshot_sha256,
             'old_local_lineage_changed')
        claims = observed['detail']['claims']
        refs = sorted([dict(controller_allocation_id=c['controller_allocation_id'],child=c['child'])
                       for c in claims],key=lambda c:c['child']['run_id'])
        need(refs == v['controller_endpoint_refs'] and all(c['prepared'] is True and c['started'] is True for c in claims)
             and observed['detail']['claim_count'] == observed['detail']['prepared_count']
             == observed['detail']['start_count'] == len(v['runs']), 'old_child_relation_changed')
        if v['runs']:
            need(sha(canonical(observed['legacy_runs'])) == v['legacy_sha256'], 'old_actual_rows_changed')
            relation = observed['legacy_relation']
            need(relation is not None and not relation['native_claims_without_scoped_actuals']
                 and len(relation['links']) == len(v['runs']) and all(r['no_refund'] is True
                 and r['separate_debt_sum_permitted'] is False for r in relation['links']), 'old_actual_coverage_missing')
            if v['require_result_complete']:
                need(observed['legacy_scoped_rows_complete'] and relation['scoped_linkage_complete']
                     and all(r['relation'] == 'same_endpoint_allocation' for r in relation['links']), 'old_endpoint_overlap_unqualified')
            for row in observed['legacy_runs']:
                need(row['status'] == 'scoped_rows_observed' and row['wire']['retired'] == 1
                     and row['journal']['has_unknown_or_pending'] is False, 'old_actual_journal_unsettled')
                result_raw = metadata(STATE/'runs'/row['run_id']/'result.json',300000); result = parse(result_raw)
                need(sha(result_raw) == row['files']['result.json']['sha256']
                     and result['complete'] is v['require_result_complete']
                     and (row['transport'] == 'tcp' or result['disarmed_on_exit'] is True)
                     and result['parent']['started'] is True and result['parent']['run_id'] == row['run_id']
                     and result['parent']['parent_id'] == policy.parent_id, 'old_actual_result_rebound')
                for name, record in row['files'].items():
                    need(name in _FILES, 'unallowlisted_old_metadata')
                    raw = metadata(STATE/'runs'/row['run_id']/name)
                    need(sha(raw) == record['sha256'], 'old_typed_file_changed')
        for name, record in observed['files'].items():
            need(name in _FILES and sha(metadata(target/name)) == record['sha256'], 'old_parent_file_changed')
        need(observed['detail']['prior_plus_prepaid_caps'] == v['paid_caps'], 'old_paid_caps_changed')
        if index: need(asdict(policy.unit(unit).prior_debt) == old[index-1]['paid_caps'], 'old_paid_debt_chain_changed')
        reports.append(dict(parent_id=policy.parent_id,paid_caps=v['paid_caps'],actual_children=len(v['runs'])))
    names(); reader.check(); guard()
    return known, original, newer, reports


def capture_history(unit, p, runtime):
    parent = p['parent_id']; need(SID.fullmatch(parent) and parent not in HISTORY
                                 and parent != CAPTURE_PARENT, 'invalid_own_parent')
    need(HEX.fullmatch(p['review_sha256']) and p['facts']['unit'] == unit, 'invalid_capture_review')
    first = clock(); phase = dict(epoch_us=first[0],monotonic_us=first[1],
        deadline_monotonic_us=first[1]+p['phase_end_epoch_us']-first[0])
    def guard():
        runtime.check(); phase_check(unit,p['facts'],p['phase_start_epoch_us'],p['phase_end_epoch_us'],phase)
        return dict(phase_end_epoch_us=p['phase_end_epoch_us'])
    with Reader() as reader:
        known, original, newer, reports = full_history(reader,unit,p['history'],guard)
        files, dirs = {}, {}
        for path in [STATE,STATE/'parents',STATE/'runs',*[STATE/'parents'/v for v in HISTORY],
                     *[STATE/v for v in capture_paths(STATE,unit)['directories']],
                     *[STATE/'runs'/v for v in sorted(newer)]]:
            reader.directory(path); rel = str(path.relative_to(STATE)) if path != STATE else ''
            entries = sorted(os.listdir(path)); dirs[rel] = entries
            for name in entries:
                if name in _FILES:
                    key = (rel+'/' if rel else '')+name; raw = reader.read(path/name,MAX_FILE)
                    files[key] = sha(raw); need(key not in known or files[key] == known[key], 'metadata_changed_before_seal')
        strict_known = {k:v for k,v in known.items() if not any(k.startswith('runs/'+r+'/') for r in original)}
        need(all(files.get(k) == v for k,v in strict_known.items()), 'known_metadata_unsealed')
        strict_raw, strict_pin = capture(STATE,owner_uid=0,expected_files=files,expected_directories=dirs)
        def exact(strict): strict.check(); reader.check(); guard(); return True
        with SealedHistory(STATE,owner_uid=0,seal_bytes=strict_raw,expected_sha256=strict_pin) as strict:
            legacy_raw, _ = legacy_capture(STATE/'runs',owner_uid=0,expected_rows=p['history']['legacy_inventory']['rows'],
                independently_verified=True,exact_namespace_gate=lambda:exact(strict),max_seconds=30)
            exact(strict)
        reader.check(); guard()
        with _Ancestry(STATE/'parents',0) as ancestry:
            need(sorted(os.listdir(ancestry.fds[-1])) == sorted(
                 historical_parent_ids(STATE,unit,clock,require_retained=True)), 'blind_capture_rerun_refused')
            os.mkdir(parent,0o700,dir_fd=ancestry.fds[-1]); os.fsync(ancestry.fds[-1]); ancestry.check()
        target = STATE/'parents'/parent
        _, binding_pin = install_seals(STATE,parent,unit,strict_raw,legacy_raw,clock=clock)
        record = dict(schema='meshticulum.private-resume-actual-capture.v1',unit=unit,parent_id=parent,
            history=p['history'],history_binding_sha256=binding_pin,facts=p['facts'],native_phase=phase,
            phase_start_epoch_us=p['phase_start_epoch_us'],phase_end_epoch_us=p['phase_end_epoch_us'],
            review_sha256=p['review_sha256'],reports=reports,actual_history_verified=True,
            original_unknown_actual_cost=None,refunds=0,global_debt_complete=False)
        raw = canonical(record); write_new(target,'capture.json',raw); runtime.check()
        return dict(actual_history_verified=True,history_binding_sha256=binding_pin,
            capture_record_sha256=sha(raw),native_phase=phase,original_unknown_actual_cost=None,refunds=0,
            full_caps=reports[-1]['paid_caps'],global_debt_complete=False)


def enrolled_action(action, unit, p, runtime):
    policy = ParentPolicy.from_dict(p['policy']); local = policy.unit(unit)
    owner, prior = p['owner'], p['prior']; target = STATE/'parents'/policy.parent_id
    need(policy.parent_id not in HISTORY and policy.parent_id != CAPTURE_PARENT
         and owner['schema'] == 'meshticulum.finite-public-resume-owner.v1'
         and owner['policy_digest'] == policy.digest and owner['manifest_sha256'] == sha(
             runtime.reader.read(runtime.root/'manifest.json',65536)), 'wrong_own_parent_scope')
    need(runtime.root.name == 'runtime-'+owner['runtime_sha256'][:16]
         and HEX.fullmatch(owner['runtime_sha256']) and owner['review_sha256'] == p.get('review_sha256',owner['review_sha256']),
         'wrong_runtime_or_review_scope')
    with Reader() as reader:
        capture_raw = reader.read(target/'capture.json',2000000)
        need(sha(capture_raw) == owner['capture_record_sha256'], 'actual_capture_record_changed')
        saved = parse(capture_raw); binding = owner['history_binding_sha256']
        need(saved['unit'] == unit and saved['parent_id'] == policy.parent_id
             and saved['actual_history_verified'] is True and saved['history_binding_sha256'] == binding
             == prior['history_binding_sha256'] and saved['review_sha256'] == owner['review_sha256']
             and saved['facts'] == owner['facts'][unit] and saved['native_phase'] == owner['native_phase'][unit]
             and saved['phase_start_epoch_us'] == owner['phase_start_epoch_us']
             and saved['phase_end_epoch_us'] == owner['phase_end_epoch_us'], 'capture_scope_changed')
        need(prior['full_caps'] == saved['history']['previous'][-1]['paid_caps'] == asdict(local.prior_debt)
             and prior['legacy_inventory'] == saved['history']['legacy_inventory']
             and prior['complete_global_debt'] is False and prior['actual_cost'] is None and prior['refunds'] == 0,
             'own_prior_refund_or_debt_changed')
        authorization = owner['authorization']
        need(authorization['human_statement'] == 'run it bro' and authorization['count_authorized'] == 1
             and authorization['public_only'] is True and authorization['private_production'] is False,
             'wrong_reviewed_public_test_direction')
        need(owner['prior_registry'][unit]['observation_sha256'] == sha(canonical(prior)) == local.prior_snapshot_sha256
             and sha(canonical(owner['prior_registry'])) == policy.prior_snapshot_sha256, 'own_prior_registry_changed')
        need(policy.issued_epoch_us == owner['phase_start_epoch_us']
             and policy.expires_epoch_us == owner['phase_end_epoch_us']-5000000,
             'original_parent_window_changed')
        rf, rp, ip = owner['rf_record'], profile_for('sf7-500'), IpLimits()
        need(owner['rf_record_sha256'] == '40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9'
             and rf['country'] == 'MY' and rf['C_transmission_allowed'] is True
             and rf['A_B_transmission_allowed'] is True and all(rf['units'][u]['antenna_connected'] is True
             for u in ('unit-a','unit-b','unit-c')) and asdict(rp) == owner['rf_profile']
             and (rp.frequency_hz,rp.bandwidth_hz,rp.spreading_factor,rp.coding_rate,rp.chip_power_dbm,
                  rp.preamble,rp.sync_word) == (921375000,500000,7,5,0,8,0x42), 'owner_RF_basis_changed')
        extra = Cost(rp.max_boot_attempts,rp.max_boot_estimated_airtime_us).plus(
            Cost(rp.max_boot_attempts,rp.max_boot_estimated_airtime_us))
        for _ in range(8):
            extra = extra.plus(Cost(ip_frames=ip.max_frames,tx_stream_bytes=ip.max_tx_stream_bytes,
                                   rx_stream_bytes=ip.max_rx_stream_bytes))
        need(local.limits == local.prior_debt.plus(Cost() if unit == 'unit-a' else extra),
             'resume_endpoint_caps_not_exact')
        owner_raw, prior_raw, policy_raw = canonical(owner), canonical(prior), canonical(policy.to_dict())
        last_clock = [clock()]
        def bounds():
            now = clock(); phase = saved['native_phase']
            need(owner['phase_start_epoch_us'] <= now[0] < owner['phase_end_epoch_us']
                 and phase['epoch_us'] <= now[0] and phase['monotonic_us'] <= now[1]
                 < phase['deadline_monotonic_us'] and all(a >= b for a,b in zip(now,last_clock[0]))
                 and boot() == local.host_boot_id, 'native_phase_expired_regressed_or_rebooted')
            last_clock[0] = now; reader.check(); return True
        def current_facts():
            bounds(); value = stable(native_facts(unit)); bounds()
            need(value == owner['facts'][unit], 'native_service_lifetime_changed')
            need(value['host_boot_id'] == local.host_boot_id and local.peer.chain_id == 'meshticulum-public-pilot-v1'
                 and local.peer.agent_id == sha(('meshticulum-public-pilot-v1/'+unit+'/'+boot()).encode()), 'own_public_peer_changed')
            return NativeFacts(unit,value['host_boot_id'],PeerID(local.peer.chain_id,local.peer.agent_id))
        def records():
            bounds()
            with Reader() as check:
                need(check.read(target/'owner.json',200000) == owner_raw
                     and check.read(target/'prior.json',200000) == prior_raw
                     and check.read(target/'policy.json',20000) == policy_raw
                     and sha(check.read(target/'history-binding.json',16384)) == binding, 'own_parent_records_changed')
            return True
        def approve(raw):
            records(); value = parse(raw); e = value['enrollment']
            need(value['phase'] in ('enroll','current') and e['schema'] == 'meshticulum.native-parent-enrollment.v2'
                 and canonical(e['policy']) == canonical(policy.to_dict()) and e['history_binding_sha256'] == binding
                 and e['unit'] == unit and e['host_boot_id'] == local.host_boot_id
                 and owner['phase_start_epoch_us'] <= e['original_epoch_us'] < policy.expires_epoch_us
                 and e['deadline_monotonic_us'] == e['original_monotonic_us']+policy.expires_epoch_us-e['original_epoch_us']
                 and e['deadline_monotonic_us'] < saved['native_phase']['deadline_monotonic_us'], 'own_approval_binding_changed')
            records(); return True
        def history_gate():
            current_facts(); return dict(phase_end_epoch_us=owner['phase_end_epoch_us'])
        if action == 'enroll':
            for name, raw in (('owner.json',owner_raw),('prior.json',prior_raw),('policy.json',policy_raw)):
                write_new(target,name,raw)
            # Before enrollment there is no NativeCaps to reopen. Independently
            # recheck old typed rows and protected capture, retaining debt once.
            def initial_prior(raw):
                need(parse(raw) == dict(parent_id=policy.parent_id,policy_digest=policy.digest,
                     unit=asdict(local),prior_snapshot_sha256=policy.prior_snapshot_sha256), 'native_prior_callback_rebound')
                full_history(reader,unit,saved['history'],history_gate,own_parent=policy.parent_id)
                records(); return True
            caps = NativeCaps.enroll(target/'native.sqlite',policy,unit=unit,clock=clock,
                facts=current_facts,approve=approve,prior_check=initial_prior,history_binding_sha256=binding)
            try:
                need(caps.current() is True and caps.history_binding_sha256 == binding, 'own_enrollment_not_current')
                return dict(parent_id=policy.parent_id,policy_digest=policy.digest,snapshot=caps.snapshot())
            finally: need(caps.close() is True, 'own_enrollment_close_uncertain')
        records()
        with ResumeParentGuard(STATE,policy,unit,owner_sha256=sha(owner_raw),prior_sha256=sha(prior_raw),
                binding_sha256=binding,preparation_sha256=p['preparation_sha256'],
                management_phase='postterminal' if action == 'audit' else 'prelaunch',clock=clock,
                facts=current_facts,approve=approve,runtime_check=runtime.check) as guard:
            # Constructor ends with genuine guard.check; export its still-held
            # canonical ledger rather than repeat native systemctl samples.
            need(guard.caps.history_binding_sha256 == binding, 'native_enrollment_history_tag_changed')
            snapshot = guard.caps.snapshot()
        if action == 'audit':
            full_history(reader,unit,saved['history'],history_gate,own_parent=policy.parent_id,
                         own_runs=tuple(p['preparation_sha256']))
            terminal = terminal_observation(reader,unit,policy,owner,p['preparation_sha256'])
        else: terminal = None
        records(); return dict(parent_id=policy.parent_id,policy_digest=policy.digest,snapshot=snapshot,
                               terminal_observation=terminal)


def terminal_observation(reader, unit, policy, owner, preparations):
    """Scoped actual endpoint linkage after exclusive native ownership ended."""
    runs = []
    for run in preparations:
        target = STATE/'runs'/run
        grant_raw = reader.read(target/'grant.json',16384); grant = parse(grant_raw)
        provision = parse(reader.read(target/'provision.json',32768))
        result = parse(reader.read(target/'result.json',300000))
        context = provision['parent']['grant_context_sha256']
        tcp = grant['schema'] == 'meshticulum.ip-service-run.v1'; binding = context
        need(result['parent']['parent_id'] == policy.parent_id and result['parent']['run_id'] == run
             and result['parent']['started'] is True, 'own_terminal_parent_rebound')
        if not tcp:
            profile = RadioProfile(**grant['profile'])
            if unit == 'unit-c':
                binding = sha(b'meshticulum.hat-attempts.v1\0'+bytes.fromhex(context)+boot().encode('ascii')+profile.encode(board_id=2))
            else:
                radio = result['radio']
                binding = sha(b'meshticulum.radio-attempts.v1\0'+bytes.fromhex(context)
                    +bytes.fromhex(radio['mcu_boot'])+bytes([1])+radio['firmware'].encode('ascii')+profile.encode(board_id=1))
            need(result['disarmed_on_exit'] is True, 'own_radio_exit_not_disarmed')
        runs.append(dict(run_id=run,host_boot_id=boot(),transport='tcp' if tcp else 'lora',
            grant_sha256=sha(grant_raw),grant_context_sha256=context,attempt_binding_sha256=binding))
    end = min(clock()[0]+35000000,owner['phase_end_epoch_us'])
    legacy = DebtScope.from_dict(dict(schema='meshticulum.public-debt-scope.v1',unit=unit,
        current_host_boot_id=boot(),runtime_sha256=owner['runtime_sha256'],manifest_sha256=owner['manifest_sha256'],
        observation_expires_epoch_us=end,runs=runs)) if runs else None
    database = reader.read(STATE/'parents'/policy.parent_id/'native.sqlite',MAX_FILE)
    observed = collect(STATE,Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,
        policy.parent_id,policy.digest,sha(database),unit,boot(),end),clock=clock,boot_reader=boot,legacy_scope=legacy).to_dict()
    need(observed['status'] == 'scoped_rows_observed' and observed['scoped_rows_complete']
         and not observed['pending_unknown'], 'own_terminal_parent_uncertain')
    qualify_terminal(observed,owner,preparations)
    if runs:
        relation = observed['legacy_relation']
        need(relation is not None and not relation['native_claims_without_scoped_actuals']
             and len(relation['links']) == len(runs) and all(r['status'] == 'scoped_rows_observed'
             and r['wire']['retired'] == 1 and r['journal']['has_unknown_or_pending'] is False
             for r in observed['legacy_runs']), 'own_terminal_journals_unsettled')
    reader.check(); return observed


def qualify_terminal(observed, owner, preparations):
    detail = observed['detail']
    need(detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256'] == owner['history_binding_sha256']
         and detail['claim_count'] == detail['prepared_count'] == detail['start_count'] == len(preparations)
         and {c['child']['metadata']['run_id'] for c in detail['claims']} == set(preparations), 'own_terminal_enrollment_or_claims_changed')
    if preparations:
        relation = observed['legacy_relation']
        need(relation is not None and relation['scoped_linkage_complete'] is True
             and not relation['native_claims_without_scoped_actuals'] and len(relation['links']) == len(preparations)
             and {r['run_id'] for r in relation['links']} == set(preparations)
             and all(r['relation'] == 'same_endpoint_allocation' and r['no_refund'] is True
             and r['separate_debt_sum_permitted'] is False for r in relation['links']), 'own_terminal_endpoint_relation_unqualified')


def response_metadata(action, unit, result):
    """Compose invariant envelope without rejecting matching writer metadata.

    Action results may repeat exact invariant fields; conflicting values or
    types are refused rather than silently overridden. A response is metadata,
    never native admission, capture provenance or transport qualification.
    """
    envelope = dict(schema=CONTRACT_SCHEMA,unit=unit,action=action,radio_opened=False,
                    private_production=False,global_debt_complete=False)
    need(type(result) is dict and all(key not in result or
         (type(result[key]) is type(value) and result[key] == value)
         for key, value in envelope.items()), 'native_resume_result_invariant_conflict')
    return result | envelope


def dispatch(action, unit, payload, *, runtime_root, manifest_sha256):
    """Actual root entry; import alone performs no native operation."""
    need(os.geteuid() == 0 and unit in ('unit-a','unit-b','unit-c')
         and action in ('capture','enroll','guard','audit','observe-cache')
         and type(payload) is dict and len(canonical(payload)) <= 2000000, 'invalid_native_resume_RPC')
    with Runtime(runtime_root,manifest_sha256) as runtime:
        if action == 'capture': result = capture_history(unit,payload,runtime)
        elif action in ('enroll','guard','audit'): result = enrolled_action(action,unit,payload,runtime)
        else:
            need(unit == 'unit-c', 'cache_observer_requires_C_recipient')
            from .public_cache_observer import observe_native
            before = stable(native_facts(unit))
            raw = [base64.b64decode(payload[k],validate=True) for k in ('manifest','signature','trust')]
            result = dict(observation=observe_native(*raw,payload['pins'],tuple(payload['wanted'])))
            need(before == stable(native_facts(unit)), 'cache_observation_facts_changed')
        runtime.check()
    return response_metadata(action,unit,result)
