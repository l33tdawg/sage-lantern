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
import resource
import stat
import threading
import time

from .history_identity_seal import _Ancestry, capture, SealedHistory, MAX_FILES, MAX_DIRS
from .history_metadata_fence import _FLAGS, _FILES, _stamp, MAX_FILE
from .legacy_grant_fence import capture as legacy_capture, LegacyGrantFence
from .legacy_grant_fence import MAX_RUNS, OWNERS
from .pilot_parent_snapshot import Scope, collect, _child_ref
from .pilot_debt_snapshot import Scope as DebtScope
from .pilot_parent_caps import ParentPolicy, Cost, canonical
from .pilot_native_caps import NativeCaps, NativeFacts
from .pilot_manage import facts as native_facts
from .resume_parent_guard import HISTORY, ResumeParentGuard, install_seals
from .retained_capture import historical_parent_ids, capture_paths, CAPTURE_PARENT, CAPTURE_ONLY_PARENTS
from .profiles import profile_for
from meshticulum.ip.wire import IpLimits
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.contracts.identity import PeerID

CONTRACT_SCHEMA = 'meshticulum.private-resume-native-rpc.v1'
STATE = Path('/var/lib/meshticulum-bench')
OLD_DIGESTS = ('86975afb2e94de9ae55c86269409f4d8456475499fc20995cd2bab6a4f4ed0af',
    '6d337c78faf8c2dbea7a0a0d29e82aa8af8d1452f954fb6b664106459c33d256',
    '1ca1774780928427f15befecf723064cd87aff6ad402b6ef94b137f5d45f6d33',
    '290fdcc66fb9696dd584e146f7c4e0ed271039fb51f75f430abf8bb0b9b5fdf1',
    '0d153ed60782be5028fe7b75f3345b0441e62ef95b8db629270d5b309fd0e049',
    'c4dac314abe5afac2d14c8f82f08466863bac438c7674864c0864c8eae5e2eb7',
    '617cdde5e1bb0bfb162552861abb6b84e25a389a981d16b5b17e7e5d5daaf10c',
    '83f56d8609b60532332631f86c3061c6f8b3fefa2ff7517406860e467dd7f2e8',
    'eb592d40f359afbc530aa61b4f1feb7d0c4afc74d27b1bbdd73e9ccf0f1faa01',
    '61f1f87bafdf307cd02e51ee5a17cd25d5a3728a80583dc6820dd2d9dbe9e119',
    '595d5864801330aeb4d1eb7d281599c090bbcf428602d6777a300761c442be55',
    'd9bd3b79c63843d29892d81846d7a5ebf4967eb01a676800d63b15e7d366a0b8',
    '44425235165ea6d49ce8b46760bdbc4c8519c98ece034c560566ae6dcd57bfb2',
    '85501a81327854cfa58ef52e65ab2253f17d18925d74617f7b2bb11644664c39',
    '25e8beb9049826a4dc1a5ddde3fcc239e6e7e67f4b265b85da80f0b9a22b9627',
    '1411f2db5d331a56b3d395e7b8443a516a766d69139275dd3276d7a8bfe19293',
    '5a837b0e365421ff9b9c5511ec768a16d458b16b3720af8c4aa1e6a085a8c827')
LATEST_PAID = 'd60d8308326314a7753e651fa43a99e3'
COMPLETE_PAID = '4559277fa43913ce3935b078d6744a55'
BULK_PAID = '4034f8979b985660df21859606c4f8a7'
BULK_RECEIVER_RUN = 'b024cea0883f00b3b6978533f66c1a19'
BULK_RECEIVER_RESULT_SHA = '3144a2ab5108bd8a5363a03ca6da541cf56a150048c40753b2cd6f91ef7758ab'
BULK_OBJECT_SHA = '29aab48fe22c08588643e5d1e11f48696405697e8a59607ef34702cc2a1e0465'
MIXED_PAID = 'ba4572e95e598ad6c17fc954c3792db7'
MIXED_RECEIVER_RUN = '9dd5b8dd6466600acfdc8f1884db3da8'
MIXED_RECEIVER_RESULT_SHA = '5f594b1cba94b24883327f2457d1a79541e5cbffbe4fd073fc547ddeaabb08b6'
MIXED_OBJECT_SHA = 'd61126423d81a20aab75ada153b03c326d60fcc781884bba9f84d3d5821d134d'
SUCCESS_PAID = '1df30b47fa36b8d47328fb886528a7a9'
PARTIAL_PAID = 'f153a7a87a2803db85078df81a6a1aaf'
PARTIAL_RUN = '13ba2dddea2c9aa53e6e806a83c78b66'
PARTIAL_REFS_SHA = {'unit-a':'022830b83a534b91e5d121b178a8341ed074b48f7d5e6e009c5ea95f1019a78a',
 'unit-b':'4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
 'unit-c':'884ab23c305bf40fe3be93f7e70d01bb23dad860fde20a94b82537584a294fb4'}
ACCEPTED_BULK_PAID = '25a990a2d565c50b237ee45812a8c75f'
REVERSE_BULK_PAID = 'cb5869e6aa8de545a267007b085d4762'
SMALL_BULK_PAID = 'f6e147e70ec7ce07f673ea5e9cd2cdc2'
ZERO_RETRY_BULK_PAID = 'b8f9b5d9c5479c8d363c7b7e8014bc3b'
IMMEDIATE_RX_BULK_PAID = '806fdca055e79dfc0b86ff8f304bcdf9'
SF7_REPLICATE_PAID = '4ea23c6b8fa83366904ac62ffa98e2bf'
PUBLIC32_PAID = 'c26cd03a8fd427daf6b8d894e4b1d04c'
PUBLIC32_REQUEST = '0a947ca87b3c1c58eee35c4facdc189f'
PUBLIC32_RESPONSE = '5224795f932cffcde4580f20683340e2'
PUBLIC32_RESULT_CANONICAL = {
    'unit-a': {PUBLIC32_REQUEST:'eaf6fb995f2d7d85ec214ad8d0446275d83cb9a982d7de86e8e397284fb43ec7',
               PUBLIC32_RESPONSE:'aa3f9443ec397e261a933f9f783d7c46b7023176b4f95be540e47e3fa2864ee6'},
    'unit-b': {},
    'unit-c': {PUBLIC32_REQUEST:'52df8064aa3f826d3837c2e6ab1cfe924dcec326e1d8d5dc2735be730ca23c81',
               PUBLIC32_RESPONSE:'7020e800d9c12344225eba986cadd0cfcce1bf5af661b72757ef6e907f3d4bd7'}}
PUBLIC32_RETRY_PAID = '8dfcfdfe8f8be2c0b326520d70eab7f5'
PUBLIC32_RETRY_REQUEST = '848e99e224df8139164df9629299d20d'
PUBLIC32_RETRY_RESPONSE = '4b24f0d33b859635a2736c0b11fb7963'
PUBLIC32_RETRY_RESULT_CANONICAL = {'unit-a': {'848e99e224df8139164df9629299d20d': '31facf2ac27c9eb8f9c3f6a5d683acfa378a13bf042c6b0154e787d1d986f904', '4b24f0d33b859635a2736c0b11fb7963': 'bb1b0bf523aa2636ca40c704e9164bb901290bb01615760da77e45bfbed1af8e'}, 'unit-b': {}, 'unit-c': {'848e99e224df8139164df9629299d20d': 'cca0a8b85417d2fb8a31b960674389ad4221b3fe54b4884a31b26f6f70e73b89', '4b24f0d33b859635a2736c0b11fb7963': '84310ee546c96558afdb0a2d827f61dd20a2a1a8287a4f65e50c85060c877278'}}
GUARDED_PAID = {LATEST_PAID: OLD_DIGESTS[3], COMPLETE_PAID: OLD_DIGESTS[4],
                BULK_PAID: OLD_DIGESTS[5], MIXED_PAID: OLD_DIGESTS[6], SUCCESS_PAID: OLD_DIGESTS[7], PARTIAL_PAID: OLD_DIGESTS[8], ACCEPTED_BULK_PAID: OLD_DIGESTS[9], REVERSE_BULK_PAID: OLD_DIGESTS[10], SMALL_BULK_PAID: OLD_DIGESTS[11], ZERO_RETRY_BULK_PAID: OLD_DIGESTS[12], IMMEDIATE_RX_BULK_PAID: OLD_DIGESTS[13], SF7_REPLICATE_PAID: OLD_DIGESTS[14], PUBLIC32_PAID: OLD_DIGESTS[15], PUBLIC32_RETRY_PAID: OLD_DIGESTS[16]}
RETIRED_UNSTARTED = 'd17b99c6932332f4ae649166dd32f129'
KNOWN_CUT_RUN = '01ab075dd06d1399fe2dc27c4a9331d3'
KNOWN_CUTS = {LATEST_PAID: KNOWN_CUT_RUN, COMPLETE_PAID: '55dffd06faee2ca08ccfc61d816a34ef'}
HEX = re.compile(r'[0-9a-f]{64}\Z')
SID = re.compile(r'[0-9a-f]{32}\Z')


def need(value, reason):
    if not value: raise RuntimeError(reason)


def sha(raw): return hashlib.sha256(raw).hexdigest()
def clock(): return time.time_ns()//1000, time.monotonic_ns()//1000
def boot(): return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def stable(value): return {k:value[k] for k in ('unit','host_boot_id','services','service_lifetimes')}

def reserve_history_descriptor_capacity():
    """Reserve before history Reader growth, retaining all continuity handles.

    Reader duplicates for verified inputs and strict capture are bounded by
    published metadata limits. This affects only the current process soft limit.
    """
    current=len(os.listdir('/proc/self/fd'))
    required=current+3*MAX_FILES+3*MAX_DIRS+2*MAX_RUNS*(3+len(OWNERS))+4*64+32+21
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    need(hard==resource.RLIM_INFINITY or required<=hard,'history_descriptor_hard_limit_insufficient')
    changed=soft!=resource.RLIM_INFINITY and soft<required
    if changed:resource.setrlimit(resource.RLIMIT_NOFILE,(required,hard))
    actual_soft,actual_hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    need(actual_hard==hard and (actual_soft==resource.RLIM_INFINITY or actual_soft>=required),
         'history_descriptor_capacity_not_established')
    return dict(current_descriptors=current,required_soft_limit=required,soft_limit=actual_soft,
                hard_limit=actual_hard,changed=changed,scope='this process only; before history reads')

def reserve_descriptor_capacity(files, directories, legacy_rows):
    """Keep every continuity FD; raise only this RPC's soft limit within hard."""
    need(type(files) is dict and len(files)<=MAX_FILES and type(directories) is dict
         and len(directories)<=MAX_DIRS and type(legacy_rows) in (dict,list,tuple)
         and len(legacy_rows)<=MAX_RUNS,'descriptor_scope_exceeds_source_bounds')
    current=len(os.listdir('/proc/self/fd'))
    # One retained strict fence plus the nested legacy fence; both may hold
    # their ancestry(max64 each). Scandir duplicates and systemctl pipes need16.
    # A reopened guard adds its parent and at most20 prepared run-directory FDs.
    required=current+len(files)+len(directories)+len(legacy_rows)*(3+len(OWNERS))+128+16+21
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    need(hard==resource.RLIM_INFINITY or required<=hard,'descriptor_hard_limit_insufficient')
    changed=soft!=resource.RLIM_INFINITY and soft<required
    if changed:resource.setrlimit(resource.RLIMIT_NOFILE,(required,hard))
    actual_soft,actual_hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    need(actual_hard==hard and (actual_soft==resource.RLIM_INFINITY or actual_soft>=required),
         'descriptor_capacity_not_established')
    return dict(current_descriptors=current,required_soft_limit=required,soft_limit=actual_soft,
                hard_limit=actual_hard,changed=changed,scope='this process only')


def endpoint_budget(unit, qualification_kind, radio, tcp):
    """Exact owner-bound public scope; no caller-selected arbitrary maxima."""
    need(unit in ('unit-a','unit-b','unit-c'), 'unknown_endpoint_budget_unit')
    if qualification_kind is None:
        return Cost() if unit=='unit-a' else radio.plus(radio).plus(
            Cost(ip_frames=tcp.ip_frames*8,tx_stream_bytes=tcp.tx_stream_bytes*8,
                 rx_stream_bytes=tcp.rx_stream_bytes*8))
    if qualification_kind=='mixed-only-v1':
        if unit=='unit-a':return radio.plus(radio).plus(tcp).plus(tcp)
        if unit=='unit-b':return tcp.plus(tcp)
        return radio.plus(radio)
    if qualification_kind == 'c-bundle100k-v1':
        return Cost() if unit == 'unit-b' else Cost(radio.radio_attempts*10,radio.estimated_airtime_us*10)
    if qualification_kind in ('a-bulk-v1','c-bulk-v1','c-bulk-8k-v1','c-bulk32-v1'):
        return Cost() if unit=='unit-b' else radio.plus(radio)
    need(qualification_kind=='mixed-bulk-v1', 'unknown_public_qualification_kind')
    if unit=='unit-c': return radio.plus(radio).plus(radio).plus(radio)
    return radio.plus(radio).plus(tcp).plus(tcp)


def parse(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, 'duplicate_rpc_metadata_key'); value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique)


class Reader:
    """Held protected regular metadata and read locks, with no SQLite path open."""
    def __init__(self):
        self.stack, self.held, self.directories = ExitStack(), [], {}
        self._nodes, self._tokens = {}, {}
        self._node_state = self._held_state = self._directory_state = ()
        self._token_state = ()
        self._owner = (os.getpid(), threading.get_ident(), os.geteuid())
        self._owner_ref = self._owner
        self._refs = (self.stack, self.held, self.directories, self._nodes, self._tokens)
        self._closed = False
    def _available(self, *, full=True):
        need(not self._closed and self._owner is self._owner_ref
             and self._owner == (os.getpid(), threading.get_ident(), os.geteuid())
             and all(a is b for a,b in zip((self.stack,self.held,self.directories,self._nodes,self._tokens),self._refs))
             and len(self._nodes)==len(self._node_state) and len(self.held)==len(self._held_state)
             and len(self.directories)==len(self._directory_state) and len(self._tokens)==len(self._token_state),
             'rpc_reader_owner_changed')
        if full:
            need(tuple(self._nodes.items())==self._node_state and tuple(self.held)==self._held_state
                 and tuple((path,tuple(identity)) for path,identity in self.directories.items())==self._directory_state
                 and tuple(self._tokens.items())==self._token_state, 'rpc_reader_state_changed')
    @staticmethod
    def _close_owned(fd, identity, token):
        try:
            if tuple(_stamp(os.fstat(fd))[:2]) == identity and os.lseek(fd,0,os.SEEK_CUR) == token:
                os.close(fd)
        except OSError: pass
    def _retain(self, fd):
        if fd in self._tokens:
            os.close(fd); need(False,'rpc_descriptor_reused')
        identity = tuple(_stamp(os.fstat(fd))[:2])
        token = MAX_FILE + fd + 1
        try: os.lseek(fd,token,os.SEEK_SET)
        except BaseException:
            os.close(fd); raise
        self.stack.callback(self._close_owned,fd,identity,token)
        self._tokens[fd] = token; self._token_state = tuple(self._tokens.items())
        return token
    def _check_nodes(self):
        self._available()
        need(all(os.lseek(fd,0,os.SEEK_CUR)==token for fd,token in self._token_state),
             'rpc_reader_descriptor_changed')
        for fd,parent,name,identity,token in self._nodes.values():
            need(tuple(_stamp(os.fstat(fd))[:4]) == identity
                 == tuple(_stamp(os.stat(name,dir_fd=parent,follow_symlinks=False))[:4])
                 and os.lseek(fd,0,os.SEEK_CUR) == token, 'rpc_ancestry_changed')
        self._available()
    def directory(self, path):
        self._available()
        absolute = Path(os.path.abspath(path)); names = ('/',*absolute.parts[1:])
        need(len(names)<=64 and len(str(absolute))<=4096, 'ancestry_too_long')
        parent = None; ancestor = Path('/')
        for index,name in enumerate(names):
            if index: ancestor = ancestor/name
            if ancestor not in self._nodes:
                fd = os.open(name,_FLAGS|os.O_DIRECTORY,dir_fd=parent)
                token = self._retain(fd); info = os.fstat(fd)
                need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0,os.geteuid())
                     and not stat.S_IMODE(info.st_mode)&0o022, 'writable_or_foreign_ancestor')
                self._nodes[ancestor] = (fd,parent,name,tuple(_stamp(info)[:4]),token)
                self._node_state = tuple(self._nodes.items())
            parent = self._nodes[ancestor][0]
        self._check_nodes(); info = os.fstat(parent)
        need(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700, 'unsafe_private_leaf')
        identity = list(_stamp(info)[:4])
        need(path not in self.directories or self.directories[path]==identity, 'rpc_directory_rebound')
        self.directories[path] = identity
        self._directory_state = tuple((path,tuple(value)) for path,value in self.directories.items())
        return parent
    def read(self, path, maximum, *, lock=False):
        parent = self.directory(path.parent)
        fd = os.open(path.name,_FLAGS,dir_fd=parent); self._retain(fd); self._check_nodes()
        s = os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
             and stat.S_IMODE(s.st_mode) == 0o600 and s.st_nlink == 1
             and 0 <= s.st_size <= maximum, 'unsafe_rpc_metadata')
        if lock: fcntl.flock(fd, fcntl.LOCK_SH|fcntl.LOCK_NB)
        raw = os.pread(fd, maximum+1, 0)
        need(len(raw) == s.st_size, 'short_rpc_metadata')
        identity = tuple(_stamp(s))
        need(tuple(_stamp(os.fstat(fd))) == identity == tuple(_stamp(path.lstat())), 'rpc_metadata_changed')
        self._check_nodes()
        need(tuple(_stamp(os.fstat(fd))) == identity == tuple(_stamp(path.lstat()))
             and os.lseek(fd,0,os.SEEK_CUR)==self._tokens[fd], 'rpc_metadata_changed_after_check')
        self._available()
        self.held.append((path,fd,identity,sha(raw))); self._held_state = tuple(self.held)
        return raw
    def check(self):
        self._check_nodes()
        for path, fd, identity, digest in self._held_state:
            token=self._tokens[fd]
            need(tuple(_stamp(os.fstat(fd))) == identity == tuple(_stamp(path.lstat()))
                 and os.lseek(fd,0,os.SEEK_CUR) == token, 'held_rpc_metadata_changed')
            raw=os.pread(fd,identity[5]+1,0);self._available(full=False)
            need(tuple(_stamp(os.fstat(fd))) == identity == tuple(_stamp(path.lstat()))
                 and os.lseek(fd,0,os.SEEK_CUR) == token
                 and sha(raw) == digest, 'held_rpc_metadata_changed_after_read')
        self._check_nodes()
        return True
    def close(self):
        if self._closed or self._owner_ref[:2] != (os.getpid(),threading.get_ident()): return
        self._closed = True; self._refs[0].close()
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
    reserve_history_descriptor_capacity()
    old = history['previous']; inventory = history['legacy_inventory']
    policies = [ParentPolicy.from_dict(v['policy']) for v in old]
    need(tuple(p.parent_id for p in policies) == HISTORY
         and tuple(p.digest for p in policies) == OLD_DIGESTS, 'wrong_retained_parents')
    original = {r['run_id']:r for r in inventory['rows']}
    newer = {r['run_id']:r for v in old for r in v['runs']+v.get('retired_unstarted',[])}
    need(len(original) == {'unit-a':41,'unit-b':64,'unit-c':63}[unit]
         and len(original) == len(inventory['rows']) and not set(original)&set(newer), 'wrong_legacy_inventory')
    known, reports = {}, []
    def names():
        reader.directory(STATE); reader.directory(STATE/'parents'); reader.directory(STATE/'runs')
        retained = historical_parent_ids(STATE,unit,clock,require_retained=True,require_unadmitted=True)
        need(own_parent not in CAPTURE_ONLY_PARENTS
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
        if policy.parent_id in GUARDED_PAID:
            for relative,pin in latest_preparation_files(reader,unit,policy,observed,
                                                       v['preparation_sha256']).items(): known[relative]=pin
        refs = sorted([dict(controller_allocation_id=c['controller_allocation_id'],child=c['child'])
                       for c in claims],key=lambda c:c['child']['run_id'])
        unstarted = v.get('retired_unstarted',[])
        if policy.parent_id==PARTIAL_PAID:
            qualify_partial_costs(unit,observed,v,refs)
        else:need(refs == v['controller_endpoint_refs'], 'old_child_relation_changed')
        qualify_history_claims(policy,unit,observed,v['runs'],unstarted)
        for row in unstarted:
            evidence = retired_unstarted(reader,unit,policy,observed,row,clock)
            for name,pin in evidence['files'].items(): known[name] = pin
        if policy.parent_id == BULK_PAID:
            need(v.get('failed_started_runs',[]) == ([BULK_RECEIVER_RUN] if unit=='unit-c' else []),
                 'bulk_failed_endpoint_scope_changed')
        if policy.parent_id == MIXED_PAID:
            need(v.get('failed_started_runs',[]) == ([MIXED_RECEIVER_RUN] if unit=='unit-c' else []),
                 'mixed_failed_endpoint_scope_changed')
        if policy.parent_id in (SUCCESS_PAID,ACCEPTED_BULK_PAID,REVERSE_BULK_PAID,ZERO_RETRY_BULK_PAID,IMMEDIATE_RX_BULK_PAID,SF7_REPLICATE_PAID):
            need(v.get('failed_started_runs',[])==[], 'successful_mixed_failure_scope_changed')
        if policy.parent_id in (PUBLIC32_PAID,PUBLIC32_RETRY_PAID):
            qualify_public32_failure_scope(unit,policy.parent_id,v)
        if v['runs']:
            need(sha(canonical(observed['legacy_runs'])) == v['legacy_sha256'], 'old_actual_rows_changed')
            relation = observed['legacy_relation']
            omitted = relation['native_claims_without_scoped_actuals'] if relation is not None else []
            need(relation is not None and {r['run_id'] for r in omitted} == {r['run_id'] for r in unstarted}
                 and len(relation['links']) == len(v['runs']) and all(r['no_refund'] is True
                 and r['separate_debt_sum_permitted'] is False for r in relation['links']), 'old_actual_coverage_missing')
            if v['require_result_complete'] or policy.parent_id in (PUBLIC32_PAID,PUBLIC32_RETRY_PAID):
                need(observed['legacy_scoped_rows_complete'] and relation['scoped_linkage_complete']
                     and all(r['relation'] == 'same_endpoint_allocation' for r in relation['links']), 'old_endpoint_overlap_unqualified')
            for row in observed['legacy_runs']:
                need(row['status'] == 'scoped_rows_observed' and row['wire']['retired'] == 1,
                     'old_actual_journal_unsettled')
                if row['journal']['has_unknown_or_pending']:
                    qualify_known_cut(reader,unit,policy,v['owner'],observed,row,
                                     KNOWN_CUTS.get(policy.parent_id))
                result_raw = metadata(STATE/'runs'/row['run_id']/'result.json',300000); result = parse(result_raw)
                need(sha(result_raw) == row['files']['result.json']['sha256']
                     and result['complete'] is (row['run_id'] not in v.get('failed_started_runs',[])
                          if policy.parent_id in GUARDED_PAID else v['require_result_complete'])
                     and (row['transport'] == 'tcp' or result['disarmed_on_exit'] is True)
                     and result['parent']['started'] is True and result['parent']['run_id'] == row['run_id']
                     and result['parent']['parent_id'] == policy.parent_id, 'old_actual_result_rebound')
                if policy.parent_id == BULK_PAID and unit=='unit-c' and row['run_id']==BULK_RECEIVER_RUN:
                    qualify_bulk_receiver(row,result,result_raw)
                if policy.parent_id == MIXED_PAID and unit=='unit-c' and row['run_id']==MIXED_RECEIVER_RUN:
                    qualify_mixed_receiver(row,result,result_raw)
                if policy.parent_id in (PUBLIC32_PAID,PUBLIC32_RETRY_PAID): qualify_public32_result(unit,row['run_id'],result,parent_id=policy.parent_id)
                for name, record in row['files'].items():
                    need(name in _FILES, 'unallowlisted_old_metadata')
                    raw = metadata(STATE/'runs'/row['run_id']/name)
                    need(sha(raw) == record['sha256'], 'old_typed_file_changed')
        for name, record in observed['files'].items():
            need(name in _FILES and sha(metadata(target/name)) == record['sha256'], 'old_parent_file_changed')
        if policy.parent_id!=PARTIAL_PAID:
            need(observed['detail']['prior_plus_prepaid_caps'] == v['paid_caps'], 'old_paid_caps_changed')
        if index: need(asdict(policy.unit(unit).prior_debt) == old[index-1]['paid_caps'], 'old_paid_debt_chain_changed')
        reports.append(dict(parent_id=policy.parent_id,paid_caps=v['paid_caps'],actual_children=len(v['runs'])))
    names(); reader.check(); guard()
    return known, original, newer, reports


def latest_preparation_files(reader,unit,policy,observed,pins):
    ids={c['child']['run_id'] for c in observed['detail']['claims']}
    need(set(pins)==ids,'latest_preparation_scope_changed')
    target=STATE/'parents'/policy.parent_id/'prepared-identities';reader.directory(target)
    need(set(os.listdir(target))=={run+'.json' for run in ids},'latest_preparation_namespace_changed')
    files={}
    for run,pin in pins.items():
        raw=reader.read(target/(run+'.json'),4096)
        need(sha(raw)==pin,'latest_preparation_record_changed')
        files[str((target/(run+'.json')).relative_to(STATE))]=pin
    reader.check();return files


def qualify_history_claims(policy,unit,observed,started,unstarted):
    """Predicate over the actual collector result; never DTO admission."""
    detail=observed['detail']; claims=detail['claims']
    ids={r['run_id'] for r in started}; absent={r['run_id'] for r in unstarted}
    actual_started={c['child']['run_id'] for c in claims if c['started'] is True}
    actual_absent={c['child']['run_id'] for c in claims if c['started'] is False}
    need(not ids&absent and detail['claim_count']==detail['prepared_count']==len(ids|absent)
         and detail['start_count']==len(ids) and all(c['prepared'] is True for c in claims)
         and actual_started==ids and actual_absent==absent,'old_child_relation_changed')
    if policy.parent_id == LATEST_PAID:
        need((len(ids),absent)==((0,set()) if unit=='unit-a' else (8,{RETIRED_UNSTARTED})),
             'latest_paid_claim_scope_changed')
    elif policy.parent_id == COMPLETE_PAID:
        need((len(ids),absent)==((0,set()) if unit=='unit-a' else (10,set())),
             'complete_paid_claim_scope_changed')
    elif policy.parent_id == BULK_PAID:
        expected={'unit-a':{'27e27a319d4a86cdc32236554de58cf7','7abd5cfd2c3da01ae4a5f687867073d4','da4ef48b14383c3f6cad0a8bc1ba396a','deea3ba36c13cd67b350cdd01785a80a'},
            'unit-b':{'7abd5cfd2c3da01ae4a5f687867073d4',BULK_RECEIVER_RUN,'d19e8fa1002399b8db98f8ddf26b4296','deea3ba36c13cd67b350cdd01785a80a'},
            'unit-c':{'27e27a319d4a86cdc32236554de58cf7',BULK_RECEIVER_RUN,'d19e8fa1002399b8db98f8ddf26b4296','da4ef48b14383c3f6cad0a8bc1ba396a'}}
        need(ids==expected[unit] and not absent,'bulk_paid_claim_scope_changed')
    elif policy.parent_id == MIXED_PAID:
        expected={'unit-a':{'2b12eb6b8edad86af9ef926820bdca34','76fb18ac4571c12a4c461a9b9c18de43',MIXED_RECEIVER_RUN,'bab9e8237656cf6860cae9e2c0eb9cbe'},
            'unit-b':{'76fb18ac4571c12a4c461a9b9c18de43','8c749476914d876554f0052ab841a0d2','9fcc14ee8e50a8f9093e0e1362f34d76','bab9e8237656cf6860cae9e2c0eb9cbe'},
            'unit-c':{'2b12eb6b8edad86af9ef926820bdca34','8c749476914d876554f0052ab841a0d2',MIXED_RECEIVER_RUN,'9fcc14ee8e50a8f9093e0e1362f34d76'}}
        need(ids==expected[unit] and not absent,'mixed_paid_claim_scope_changed')
    elif policy.parent_id == SUCCESS_PAID:
        expected={'unit-a':{'1dc2d03da057186c81d1476aabe611b2','2900ab6e27f2abca9e7d7a77b5d60c10','3e387ba3fcdabef7a0ac1351655022c9','5dc283b3f4af4fea45d83158a6cf8846'},
            'unit-b':{'2900ab6e27f2abca9e7d7a77b5d60c10','3e387ba3fcdabef7a0ac1351655022c9'},
            'unit-c':{'1dc2d03da057186c81d1476aabe611b2','5dc283b3f4af4fea45d83158a6cf8846'}}
        need(ids==expected[unit] and not absent,'successful_mixed_claim_scope_changed')
    elif policy.parent_id==PARTIAL_PAID:
        need(not ids and absent==({PARTIAL_RUN} if unit=='unit-c' else set()),'partial_claim_scope_changed')
    elif policy.parent_id==ACCEPTED_BULK_PAID:
        expected=set() if unit=='unit-b' else {'b481688a3efc8b1adb2ce75e6e78fe8c','d76fa4db7c77a96dff2f5fc5d1a54091'}
        need(ids==expected and not absent,'accepted_bulk_claim_scope_changed')
    elif policy.parent_id==REVERSE_BULK_PAID:
        expected=set() if unit=='unit-b' else {'dca09df76b8115eef1216b7c29de31b1','b4b2c3afe5c937738bb3a9fd1ece281c'}
        need(ids==expected and not absent,'reverse_bulk_claim_scope_changed')
    elif policy.parent_id==SMALL_BULK_PAID:
        expected=set() if unit=='unit-b' else {'28a613163ef48a171afae569b8a4e5a7','77b646ef8e40d770e08ed0729d48c4b9'}
        need(ids==expected and not absent,'small_bulk_claim_scope_changed')
    elif policy.parent_id==ZERO_RETRY_BULK_PAID:
        expected=set() if unit=='unit-b' else {'79d9fe1ec599a095f615d81e76fdfc4c','9b03b50546a1db49d48d0d60453f2b2b'}
        need(ids==expected and not absent,'zero_retry_bulk_claim_scope_changed')
    elif policy.parent_id==IMMEDIATE_RX_BULK_PAID:
        expected=set() if unit=='unit-b' else {'8986606635a4ef2d0b96b2700d946ee4','c08de47e5afdd8b287e46354afe4d79a'}
        need(ids==expected and not absent,'immediate_rx_bulk_claim_scope_changed')
    elif policy.parent_id==SF7_REPLICATE_PAID:
        expected=set() if unit=='unit-b' else {'ad817046664eef590433541ac976ba34','c64aede596ce549e1af12542affca4cc'}
        need(ids==expected and not absent,'sf7_replicate_claim_scope_changed')
    elif policy.parent_id in (PUBLIC32_PAID,PUBLIC32_RETRY_PAID):
        expected=PUBLIC32_RESULT_CANONICAL if policy.parent_id==PUBLIC32_PAID else PUBLIC32_RETRY_RESULT_CANONICAL
        need(ids==set(expected[unit]) and not absent,'public32_claim_scope_changed')
    else: need(not unstarted,'unexpected_old_unstarted_claim')


def qualify_public32_failure_scope(unit,parent_id,history):
    need(parent_id in (PUBLIC32_PAID,PUBLIC32_RETRY_PAID) and unit in ('unit-a','unit-b','unit-c'),
         'unknown_public32_paid_parent')
    failed=PUBLIC32_RESPONSE if parent_id==PUBLIC32_PAID else PUBLIC32_RETRY_RESPONSE
    need(history.get('failed_started_runs')==([] if unit=='unit-b' else [failed])
         and history['require_result_complete'] is False,'public32_failure_scope_changed')


def qualify_public32_result(unit,run,result,*,parent_id=PUBLIC32_PAID):
    """Exact failed paid outcome; never promote transport or global handoff."""
    need(parent_id in (PUBLIC32_PAID,PUBLIC32_RETRY_PAID),'unknown_public32_paid_parent')
    hashes=PUBLIC32_RESULT_CANONICAL if parent_id==PUBLIC32_PAID else PUBLIC32_RETRY_RESULT_CANONICAL
    request=PUBLIC32_REQUEST if parent_id==PUBLIC32_PAID else PUBLIC32_RETRY_REQUEST
    policy_digest=OLD_DIGESTS[15] if parent_id==PUBLIC32_PAID else OLD_DIGESTS[16]
    need(unit in hashes and run in hashes[unit]
         and sha(canonical(result))==hashes[unit][run]
         and result['complete'] is (run==request) and result['disarmed_on_exit'] is True
         and result['parent']['started'] is True and result['parent']['parent_id']==parent_id
         and result['parent']['policy_digest']==policy_digest and result['parent']['run_id']==run,
         'public32_retained_result_changed')


def qualify_bulk_receiver(row,result,raw):
    """One exact failed but durable receiver; never publisher acceptance."""
    need(row['run_id']==BULK_RECEIVER_RUN and row['transport']=='lora'
         and row['status']=='scoped_rows_observed' and row['wire']['retired']==1
         and not row['journal']['has_unknown_or_pending']
         and sha(raw)==row['files']['result.json']['sha256']==BULK_RECEIVER_RESULT_SHA
         and result['unit']=='unit-c' and result['session_id']==BULK_RECEIVER_RUN
         and result['complete'] is False and result['error']=='WireError:service_transfer_not_live'
         and result['sends'] is False and result['service_format']=='MR1.response'
         and result['parent']['parent_id']==BULK_PAID and result['parent']['started'] is True
         and result['disarmed_on_exit'] is True and not result['attempt_journal']['fenced']
         and result['service_object_durable'] is True and result['service_object_sha256']==BULK_OBJECT_SHA
         and result['object']['bytes']==16384 and result['object']['sha256']==BULK_OBJECT_SHA
         and result['flow']['complete'] is True and result['flow']['received_bytes']==16384
         and result['publisher_verified'] is False and not result.get('cleanup_errors'),
         'bulk_failed_durable_receiver_changed')

def qualify_mixed_receiver(row,result,raw):
    """Exact expired H7 receiver: durable wire object, application still failed."""
    need(row['run_id']==MIXED_RECEIVER_RUN and row['transport']=='lora'
         and row['status']=='scoped_rows_observed' and row['wire']['retired']==1
         and not row['journal']['has_unknown_or_pending']
         and sha(raw)==row['files']['result.json']['sha256']==MIXED_RECEIVER_RESULT_SHA
         and result['unit']=='unit-c' and result['session_id']==MIXED_RECEIVER_RUN
         and result['complete'] is False and result['error']=='WireError:bench_application_deadline'
         and result['sends'] is False and result['service_format']=='MR1.response'
         and result['parent']['parent_id']==MIXED_PAID and result['parent']['started'] is True
         and result['disarmed_on_exit'] is True and not result['attempt_journal']['fenced']
         and result['service_object_durable'] is True and result['service_object_sha256']==MIXED_OBJECT_SHA
         and result['object']['bytes']==1028 and result['object']['sha256']==MIXED_OBJECT_SHA
         and result['flow']['complete'] is True and result['flow']['received_bytes']==1028
         and result['publisher_verified'] is False and not result.get('cleanup_errors'),
         'mixed_failed_durable_receiver_changed')

def qualify_partial_costs(unit,observed,value,native_refs):
    """Fixed historical conservative charge; never a grant or native claim."""
    actual=observed['detail']['prior_plus_prepaid_caps']
    need(actual==value['actual_native_paid_caps'] and sha(canonical(value['controller_endpoint_refs']))==PARTIAL_REFS_SHA[unit],
         'partial_actual_or_controller_proof_changed')
    need(native_refs==(value['controller_endpoint_refs'] if unit=='unit-c' else []), 'partial_native_endpoint_projection_changed')
    expected=Cost(256,60000000) if unit=='unit-a' else Cost()
    need(value['conservative_controller_only_caps']==asdict(expected)
         and value['paid_caps']==asdict(Cost(**actual).plus(expected)), 'partial_conservative_cost_changed')

def retired_partial_radio(reader,unit,policy,observed,row,now):
    need(unit=='unit-c' and row['run_id']==PARTIAL_RUN and row['transport']=='lora','partial_radio_scope_changed')
    claim=observed['detail']['claims'][0];target=STATE/'runs'/PARTIAL_RUN
    need(len(observed['detail']['claims'])==1 and claim['prepared'] is True and claim['started'] is False
         and claim['start_row_sha256'] is None,'partial_radio_start_present')
    reader.directory(target);need(set(os.listdir(target))=={'grant.json','active','provision.json','secret.bin','sender.bin'},
                                'partial_radio_runtime_namespace_changed')
    raw=reader.read(target/'grant.json',16384);plan=parse(raw)
    provision_raw=reader.read(target/'provision.json',32768);provision=parse(provision_raw);receipt=provision['parent']
    identity_raw=reader.read(STATE/'parents'/policy.parent_id/'prepared-identities'/(PARTIAL_RUN+'.json'),4096)
    identity=parse(identity_raw);sample=now()
    need(sha(raw)==row['grant_sha256']=='5eccb5834f02ebb8e452fcb7986d4f0a37388febbf999d55abfb2ef094fe6865'
         and sha(provision_raw)=='d2bdd53798dac8ce8ca8e40f0a8fc42c19789d751472be03efc247d18cec7fcf'
         and sha(identity_raw)==row['prepared_identity_sha256']=='a4e7600fe7c6fb4f6cfa87bfb8e7f0880376bb959b311b4b8e9533595a17df05'
         and plan['schema']=='meshticulum.service-run.v1' and plan['unit']==unit and plan['session_id']==PARTIAL_RUN
         and plan['host_boot_id']==policy.unit(unit).host_boot_id==boot() and plan['expires_epoch_us']<sample[0]
         and plan['application_deadline_monotonic_us']<sample[1] and plan['service']['format']=='MR1.request'
         and provision['run_id']==PARTIAL_RUN and provision['unit']==unit and provision['transport']=='lora'
         and receipt['parent_id']==PARTIAL_PAID and receipt['prepared'] is True and receipt['native_claim_id']==claim['native_claim_id']
         and sha(canonical(receipt))==claim['prepared_row_sha256'] and sha(canonical(plan))==claim['native_plan_sha256']
         and identity['directory_identity']==reader.directories[target] and identity['prepared_sha256']==sha(canonical(receipt))
         and identity['provision_sha256']==sha(provision_raw) and reader.read(target/'active',128,lock=True)==b'retired\n',
         'partial_radio_provision_or_identity_changed')
    reader.check();return dict(run_id=PARTIAL_RUN,prepared=True,started=False,retired=True,actual_cost=None,
        full_prepaid_caps=claim['child']['full_caps'],no_refund=True,separate_debt_sum_permitted=False,
        physical_quiescence_observed=False,live_terminal_ownership_observed=False,
        files={str((target/'grant.json').relative_to(STATE)):sha(raw),str((target/'provision.json').relative_to(STATE)):sha(provision_raw),
        str((STATE/'parents'/policy.parent_id/'prepared-identities'/(PARTIAL_RUN+'.json')).relative_to(STATE)):sha(identity_raw)})


def retired_unstarted(reader,unit,policy,observed,row,now):
    """Bind one actual paid/prepared/nonstarted retired TCP endpoint; cost unknown.

    No missing file becomes a zero measurement or process-terminal claim.
    The canonical collector must have read the native append-only start table.
    """
    if policy.parent_id==PARTIAL_PAID:return retired_partial_radio(reader,unit,policy,observed,row,now)
    run=row['run_id'];need(policy.parent_id==LATEST_PAID and unit in ('unit-b','unit-c')
         and run==RETIRED_UNSTARTED,'unexpected_retired_unstarted_scope')
    claims=[c for c in observed['detail']['claims'] if c['child']['run_id']==run]
    need(len(claims)==1 and claims[0]['prepared'] is True and claims[0]['started'] is False
         and claims[0]['start_row_sha256'] is None,'retired_unstarted_native_start_present')
    claim=claims[0];target=STATE/'runs'/run;files={}
    def public(path,maximum):
        raw=reader.read(path,maximum);files[str(path.relative_to(STATE))]=sha(raw);return raw
    grant_raw=public(target/'grant.json',16384);plan=parse(grant_raw)
    provision=parse(public(target/'provision.json',32768));receipt=provision['parent']
    need(_child_ref(receipt['child_caps'])==claim['child'],'retired_unstarted_child_projection_changed')
    meta=receipt['child_caps']['metadata']
    need(meta['transport']=='tcp' and plan['schema']=='meshticulum.ip-service-run.v1'
         and provision['schema']=='meshticulum.provisioned-public-pilot.v2'
         and provision['unit']==unit and provision['run_id']==run and provision['transport']=='tcp'
         and sha(grant_raw)==row['grant_sha256'] and sha(canonical(receipt))==claim['prepared_row_sha256']
         and receipt['native_claim_id']==claim['native_claim_id'] and receipt['child_caps']['cost']==claim['child']['full_caps']
         and receipt['run_id']==run and receipt['parent_id']==policy.parent_id
         and receipt['policy_digest']==policy.digest and receipt['unit']==unit
         and receipt['host_boot_id']==policy.unit(unit).host_boot_id==boot()
         and sha(canonical(plan))==receipt['native_plan_sha256']==claim['native_plan_sha256'],
         'retired_unstarted_provision_rebound')
    normalized=dict(plan,application_deadline_monotonic_us=None)
    need(sha(canonical(normalized))==meta['plan_sha256']==receipt['original_plan_sha256']
         and canonical(plan['limits'])==canonical(meta['profile'])
         and canonical({k:plan['object'][k] for k in ('bytes','sha256')})==canonical(meta['object'])
         and plan['session']['session_id']==run and plan['format']==meta['format']
         and canonical(plan['session']['initiator'])==canonical(asdict(policy.unit(meta['sender']).peer))
         and canonical(plan['session']['responder'])==canonical(asdict(policy.unit(meta['receiver']).peer))
         and canonical(plan['local_peer'])==canonical(asdict(policy.unit(unit).peer)),
         'retired_unstarted_plan_rebound')
    sampled=now();need(plan['expires_epoch_us']<sampled[0]
         and receipt['application_expires_epoch_us']<sampled[0]
         and receipt['application_deadline_monotonic_us']<sampled[1]
         and public(target/'active',128)==b'retired\n','retired_unstarted_live_or_not_retired')
    for name in os.listdir(target):
        need(name not in ('job.json','started','bytes.sqlite','wire.sqlite','attempts.sqlite')
             and not name.endswith(('-wal','-shm','-journal')),'retired_unstarted_runtime_record_present')
    identity_path=STATE/'parents'/policy.parent_id/'prepared-identities'/(run+'.json')
    identity_raw=public(identity_path,4096);identity=parse(identity_raw);reader.directory(target)
    need(sha(identity_raw)==row['prepared_identity_sha256'] and identity['run_id']==run
         and identity['parent_id']==policy.parent_id and identity['policy_digest']==policy.digest
         and identity['unit']==unit and identity['directory_identity']==reader.directories[target]
         and identity['prepared_sha256']==sha(canonical(receipt))
         and identity['provision_sha256']==files[str((target/'provision.json').relative_to(STATE))],
         'retired_unstarted_capture_rebound')
    reader.check();return dict(run_id=run,prepared=True,started=False,retired=True,actual_cost=None,
        full_prepaid_caps=claim['child']['full_caps'],no_refund=True,separate_debt_sum_permitted=False,
        physical_quiescence_observed=False,live_terminal_ownership_observed=False,files=files)


def seal_inputs(reader, unit, known, original, newer):
    """Reobserve complete verified metadata before any own namespace creation."""
    identities={STATE/'parents'/parent/'prepared-identities' for parent in GUARDED_PAID}
    paths=[STATE,STATE/'parents',STATE/'runs',*[STATE/'parents'/v for v in HISTORY],
           *[STATE/v for v in capture_paths(STATE,unit)['directories']],
           *sorted(identities),*[STATE/'runs'/v for v in sorted(newer)]]
    files,dirs={},{}
    for path in paths:
        reader.directory(path);rel=str(path.relative_to(STATE)) if path!=STATE else ''
        entries=sorted(os.listdir(path));dirs[rel]=entries
        if path in identities:
            expected={key[len(rel)+1:] for key in known if key.startswith(rel+'/')}
            need(set(entries)==expected and all(re.fullmatch(r'[0-9a-f]{32}\.json',name)
                 and int(name[:-5],16)!=0 for name in entries),'prepared_identity_namespace_changed_before_seal')
        for name in entries:
            if name in _FILES or path in identities:
                key=(rel+'/' if rel else '')+name;raw=reader.read(path/name,MAX_FILE)
                files[key]=sha(raw)
                need(key not in known or files[key]==known[key],'metadata_changed_before_seal')
    strict_known={key:pin for key,pin in known.items()
                  if not any(key.startswith('runs/'+run+'/') for run in original)}
    need(all(files.get(key)==pin for key,pin in strict_known.items()),'known_metadata_unsealed')
    reader.check()
    return files,dirs


def capture_history(unit, p, runtime):
    parent = p['parent_id']; need(SID.fullmatch(parent) and parent not in HISTORY
                                 and parent not in CAPTURE_ONLY_PARENTS, 'invalid_own_parent')
    need(HEX.fullmatch(p['review_sha256']) and p['facts']['unit'] == unit, 'invalid_capture_review')
    first = clock(); phase = dict(epoch_us=first[0],monotonic_us=first[1],
        deadline_monotonic_us=first[1]+p['phase_end_epoch_us']-first[0])
    def guard():
        runtime.check(); phase_check(unit,p['facts'],p['phase_start_epoch_us'],p['phase_end_epoch_us'],phase)
        return dict(phase_end_epoch_us=p['phase_end_epoch_us'])
    with Reader() as reader:
        known, original, newer, reports = full_history(reader,unit,p['history'],guard)
        files, dirs = seal_inputs(reader,unit,known,original,newer)
        reserve_descriptor_capacity(files,dirs,p['history']['legacy_inventory']['rows'])
        strict_raw, strict_pin = capture(STATE,owner_uid=0,expected_files=files,expected_directories=dirs)
        def exact(strict): strict.check(); reader.check(); guard(); return True
        with SealedHistory(STATE,owner_uid=0,seal_bytes=strict_raw,expected_sha256=strict_pin) as strict:
            legacy_raw, _ = legacy_capture(STATE/'runs',owner_uid=0,expected_rows=p['history']['legacy_inventory']['rows'],
                independently_verified=True,exact_namespace_gate=lambda:exact(strict),max_seconds=30)
            exact(strict)
        reader.check(); guard()
        with _Ancestry(STATE/'parents',0) as ancestry:
            need(sorted(os.listdir(ancestry.fds[-1])) == sorted(
                 historical_parent_ids(STATE,unit,clock,require_retained=True,require_unadmitted=True)), 'blind_capture_rerun_refused')
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
    need(policy.parent_id not in HISTORY and policy.parent_id not in CAPTURE_ONLY_PARENTS
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
        need((owner.get('qualification_kind') in ('c-bulk32-v1','c-bundle100k-v1')) == (policy.qualification_kind in ('c-bulk32-v1','c-bundle100k-v1'))
             and (policy.qualification_kind is None or owner.get('qualification_kind') == policy.qualification_kind),
             'public32_policy_kind_not_owner_bound')
        if policy.qualification_kind == 'c-bundle100k-v1':
            from .pilot_bundle import BundleContext
            BundleContext(owner['bundle_context'],policy).current(clock())
        rf, rp, ip = owner['rf_record'], profile_for('sf7-500'), IpLimits()
        need(owner['rf_record_sha256'] == '40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9'
             and rf['country'] == 'MY' and rf['C_transmission_allowed'] is True
             and rf['A_B_transmission_allowed'] is True and all(rf['units'][u]['antenna_connected'] is True
             for u in ('unit-a','unit-b','unit-c')) and asdict(rp) == owner['rf_profile']
             and (rp.frequency_hz,rp.bandwidth_hz,rp.spreading_factor,rp.coding_rate,rp.chip_power_dbm,
                  rp.preamble,rp.sync_word) == (921375000,500000,7,5,0,8,0x42), 'owner_RF_basis_changed')
        radio = Cost(rp.max_boot_attempts,rp.max_boot_estimated_airtime_us)
        tcp = Cost(ip_frames=ip.max_frames,tx_stream_bytes=ip.max_tx_stream_bytes,
                   rx_stream_bytes=ip.max_rx_stream_bytes)
        extra = endpoint_budget(unit,owner.get('qualification_kind'),radio,tcp)
        need(local.limits == local.prior_debt.plus(extra),
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
        strict=parse(reader.read(target/'history-identity-seal.json',2000000))
        legacy=parse(reader.read(target/'legacy-grant-seal.json',2000000))
        reserve_descriptor_capacity(strict['files'],strict['directories'],legacy['rows'])
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
            terminal = terminal_observation(reader,unit,policy,owner,p['preparation_sha256'],p.get('expected_cut_run'))
        else: terminal = None
        records(); return dict(parent_id=policy.parent_id,policy_digest=policy.digest,snapshot=snapshot,
                               terminal_observation=terminal)


def terminal_observation(reader, unit, policy, owner, preparations, expected_cut_run=None):
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
             and r['wire']['retired'] == 1
             for r in observed['legacy_runs']), 'own_terminal_journals_unsettled')
        for row in observed['legacy_runs']:
            if row['journal']['has_unknown_or_pending']:
                qualify_known_cut(reader,unit,policy,owner,observed,row,expected_cut_run)
    reader.check(); return observed


def qualify_terminal(observed, owner, preparations):
    detail = observed['detail']
    need(detail['approval_bytes_match_enrollment'] is True
         and detail['enrollment']['history_binding_sha256'] == owner['history_binding_sha256']
         and detail['claim_count'] == detail['prepared_count'] == detail['start_count'] == len(preparations)
         and {c['child']['run_id'] for c in detail['claims']} == set(preparations), 'own_terminal_enrollment_or_claims_changed')
    if preparations:
        relation = observed['legacy_relation']
        need(relation is not None and relation['scoped_linkage_complete'] is True
             and not relation['native_claims_without_scoped_actuals'] and len(relation['links']) == len(preparations)
             and {r['run_id'] for r in relation['links']} == set(preparations)
             and all(r['relation'] == 'same_endpoint_allocation' and r['no_refund'] is True
             and r['separate_debt_sum_permitted'] is False for r in relation['links']), 'own_terminal_endpoint_relation_unqualified')


def qualify_cut_journal(journal):
    """Typed known-byte fence predicate; never clears or edits journal state."""
    j=journal['owner'];attempts=journal['attempts']
    need(journal['has_unknown_or_pending'] is True and j['fenced'] is True
         and type(j['rx_pending']) is int and j['rx_pending']==0
         and type(j['rx_unknown']) is int and j['rx_unknown']==0
         and type(j['rx_settled']) is int and j['rx_settled']==512
         and attempts and all(a['state']=='KERNEL_ACCEPTED' and a['lane']=='ACK'
             and type(a['written_bytes']) is int and a['written_bytes']==a['reserved_bytes']>0 for a in attempts),
         'fenced_cut_has_unsettled_bytes')
    return None


def qualify_known_cut(reader,unit,policy,owner,observed,row,expected_run):
    """Exact intentional receiver cutoff; original fenced flag stays TRUE.

    This is scoped retired evidence, not a repaired journal or reusable credit.
    All full prepaid caps remain charged; no missing/unknown byte is forgiven.
    """
    need(unit=='unit-c' and expected_run is not None and row['run_id']==expected_run
         and row['transport']=='tcp' and row['status']=='scoped_rows_observed'
         and row['wire']['retired']==1,'unexpected_fenced_cut_scope')
    run=row['run_id'];qualify_cut_journal(row['journal'])
    if policy.parent_id in GUARDED_PAID:
        attempts=row['journal']['attempts']
        need(run==KNOWN_CUTS[policy.parent_id] and len(attempts)==1 and attempts[0]['reserved_bytes']==74
             and attempts[0]['written_bytes']==74 and row['actual_cost']==dict(radio_attempts=0,
                 estimated_airtime_us=0,ip_frames=1,tx_stream_bytes=74,rx_stream_bytes=512),
             'historical_fenced_cut_cost_changed')
    target=STATE/'runs'/run;raw=reader.read(target/'result.json',300000);result=parse(raw)
    provision=parse(reader.read(target/'provision.json',32768));receipt=provision['parent']
    claims=[c for c in observed['detail']['claims'] if c['child']['run_id']==run]
    need(len(claims)==1 and claims[0]['started'] is True and claims[0]['prepared'] is True
         and sha(canonical(receipt))==claims[0]['prepared_row_sha256']
         and _child_ref(receipt['child_caps'])==claims[0]['child'],'fenced_cut_parent_projection_changed')
    meta=receipt['child_caps']['metadata'];loss=result['test_tcp_loss']
    need(sha(raw)==row['files']['result.json']['sha256'] and result['complete'] is False
         and result.get('error')=='IpError:explicit_public_pilot_tcp_loss'
         and loss['injected'] is True and type(loss['requested_rx_stream_bytes']) is int
         and type(loss['known_rx_stream_bytes']) is int
         and loss['requested_rx_stream_bytes']==loss['known_rx_stream_bytes']==512
         and result['byte_journal']['fenced'] is True and not result.get('service_object_durable')
         and result['session_id']==run and result['service_format']==meta['format']=='MR1.response'
         and meta['sends'] is False and meta['unit']==unit and meta['receiver']==unit
         and meta['sender']=='unit-b' and meta['transport']=='tcp' and meta['object']['bytes']>512
         and result['parent']==dict(receipt,schema='meshticulum.native-parent-start.v1',started=True)
         and row['admitted_caps']==row['conservative_unknown_caps']==claims[0]['child']['full_caps'],
         'fenced_cut_result_or_credit_changed')
    try: (target/'receiver.bin').lstat()
    except FileNotFoundError: pass
    else: raise RuntimeError('fenced_cut_receiver_body_exists')
    plan=parse(reader.read(target/'grant.json',16384))
    need(sha(canonical(plan))==receipt['native_plan_sha256'] and plan['format']=='MR1.response'
         and plan['session']['session_id']==run,'fenced_cut_plan_changed')
    from .pilot_ssh import INVOKE
    python=owner.get('native_python',{}).get(unit)
    if python is None and KNOWN_CUTS.get(policy.parent_id)==run: python='/usr/bin/python3'
    need(type(python) is str and python.startswith('/'),'fenced_cut_native_python_unbound')
    argv=[python,'-I','-B','-c',INVOKE,'/opt/meshticulum-bench/runtime-'+owner['runtime_sha256'][:16]+'/bench_node.py',
          owner['manifest_sha256'],'ip-service-run',unit,'--run-id',run,'--test-rx-cutoff','512']
    command=sha(b'\0'.join(a.encode() for a in argv)+b'\0')
    need(row['terminal_metadata']['command_sha256']==command
         and result['native_job']['command_sha256']==command,'fenced_cut_native_argv_changed')
    reader.check();return dict(run_id=run,classification='intentional_known_512_byte_receiver_cut',
                               fenced_preserved=True,refunds=0,full_caps_retained=True)


def inspect_paid_parent(unit,p,runtime):
    """Read-only typed rollover evidence, independently of expired admission.

    No enrollment/current constructor, radio, grant or ledger write occurs.
    Started membership comes from actual protected native rows, not input DTOs.
    Missing unstarted cost stays null and full prepaid debt remains charged.
    """
    policy=ParentPolicy.from_dict(p['policy']);owner=p['owner'];prior=p['prior'];local=policy.unit(unit)
    need(policy.parent_id in GUARDED_PAID and policy.digest==GUARDED_PAID[policy.parent_id]
         and local.host_boot_id==boot(),'wrong_paid_parent_scope')
    end=clock()[0]+35000000;target=STATE/'parents'/policy.parent_id
    with Reader() as reader:
        for name,value in (('policy.json',policy.to_dict()),('owner.json',owner),('prior.json',prior)):
            need(reader.read(target/name,200000)==canonical(value),'paid_parent_input_changed')
        sampled=clock();phase=owner['native_phase'][unit]
        need(sampled[0]>=owner['phase_end_epoch_us']>policy.expires_epoch_us
             and sampled[1]>=phase['deadline_monotonic_us'],'paid_parent_phase_not_expired')
        before=stable(native_facts(unit));database=reader.read(target/'native.sqlite',MAX_FILE)
        scope=Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,policy.parent_id,
                    policy.digest,sha(database),unit,boot(),end)
        initial=collect(STATE,scope,clock=clock,boot_reader=boot).to_dict()
        need(initial['status']=='scoped_rows_observed' and initial['scoped_rows_complete']
             and initial['policy_expired_at_observation'] and not initial['pending_unknown']
             and initial['detail']['approval_bytes_match_enrollment'] is True
             and initial['detail']['enrollment']['history_binding_sha256']==owner['history_binding_sha256'],
             'paid_parent_actual_rows_unavailable')
        latest_preparation_files(reader,unit,policy,initial,p['preparation_sha256'])
        started=[];unstarted=[]
        from .pilot_job import inspect as inspect_job
        for claim in initial['detail']['claims']:
            meta=claim['child'];run=meta['run_id'];child=STATE/'runs'/run
            grant_raw=reader.read(child/'grant.json',16384);grant=parse(grant_raw)
            provision=parse(reader.read(child/'provision.json',32768));context=provision['parent']['grant_context_sha256']
            row=dict(run_id=run,host_boot_id=boot(),transport=meta['transport'],grant_sha256=sha(grant_raw),
                     grant_context_sha256=context,attempt_binding_sha256=context)
            need(reader.read(child/'active',128,lock=True)==b'retired\n','paid_child_not_retired')
            if claim['started'] is False:
                row['prepared_identity_sha256']=p['preparation_sha256'][run];unstarted.append(row);continue
            status=inspect_job(child,unit=unit,run_id=run,boot=boot())
            need(status['job_record_found'] is True and status['live'] is False,'paid_started_job_not_terminal')
            result=parse(reader.read(child/'result.json',300000))
            if policy.parent_id in (PUBLIC32_PAID,PUBLIC32_RETRY_PAID):qualify_public32_result(unit,run,result,parent_id=policy.parent_id)
            if meta['transport']=='lora':
                profile=RadioProfile(**grant['profile'])
                if unit=='unit-c':binding=sha(b'meshticulum.hat-attempts.v1\0'+bytes.fromhex(context)+boot().encode('ascii')+profile.encode(board_id=2))
                else:
                    radio=result['radio'];binding=sha(b'meshticulum.radio-attempts.v1\0'+bytes.fromhex(context)
                        +bytes.fromhex(radio['mcu_boot'])+bytes([1])+radio['firmware'].encode('ascii')+profile.encode(board_id=1))
                need(result['disarmed_on_exit'] is True,'paid_radio_exit_unqualified')
                row['attempt_binding_sha256']=binding
            started.append(row)
        qualify_history_claims(policy,unit,initial,started,unstarted)
        legacy=DebtScope.from_dict(dict(schema='meshticulum.public-debt-scope.v1',unit=unit,
            current_host_boot_id=boot(),runtime_sha256=owner['runtime_sha256'],manifest_sha256=owner['manifest_sha256'],
            observation_expires_epoch_us=end,runs=started)) if started else None
        actual=collect(STATE,scope,clock=clock,boot_reader=boot,legacy_scope=legacy).to_dict()
        need(actual['status']=='scoped_rows_observed' and actual['scoped_rows_complete'] and not actual['pending_unknown']
             and actual['detail']==initial['detail'],'paid_parent_rows_changed_during_inspection')
        if policy.parent_id==BULK_PAID and unit=='unit-c':
            failed=next(row for row in actual['legacy_runs'] if row['run_id']==BULK_RECEIVER_RUN)
            raw=reader.read(STATE/'runs'/BULK_RECEIVER_RUN/'result.json',300000)
            qualify_bulk_receiver(failed,parse(raw),raw)
        if policy.parent_id==MIXED_PAID and unit=='unit-c':
            failed=next(row for row in actual['legacy_runs'] if row['run_id']==MIXED_RECEIVER_RUN)
            raw=reader.read(STATE/'runs'/MIXED_RECEIVER_RUN/'result.json',300000)
            qualify_mixed_receiver(failed,parse(raw),raw)
        unknown=[retired_unstarted(reader,unit,policy,actual,row,clock) for row in unstarted]
        reasons=[]
        if started:
            relation=actual['legacy_relation']
            qualified=(relation is not None and relation['scoped_linkage_complete'] is True
                 and len(relation['links'])==len(started) and {r['run_id'] for r in relation['native_claims_without_scoped_actuals']}
                     =={r['run_id'] for r in unstarted}
                 and all(r['status']=='scoped_rows_observed' and r['wire']['retired']==1
                         and r['journal']['has_unknown_or_pending'] is False for r in actual['legacy_runs']))
            if not qualified:
                try:
                    need(relation is not None and relation['scoped_linkage_complete'] is True
                         and len(relation['links'])==len(started)
                         and {r['run_id'] for r in relation['native_claims_without_scoped_actuals']}
                             =={r['run_id'] for r in unstarted},'paid_started_relation_unqualified')
                    for row in actual['legacy_runs']:
                        need(row['status']=='scoped_rows_observed' and row['wire']['retired']==1,
                             'paid_started_row_unqualified')
                        if row['journal']['has_unknown_or_pending']:
                            qualify_known_cut(reader,unit,policy,owner,actual,row,KNOWN_CUTS[policy.parent_id])
                except Exception as error: reasons.append(str(error))
        need(stable(native_facts(unit))==before,'paid_parent_inspection_facts_changed');reader.check();runtime.check()
        return dict(parent_id=policy.parent_id,policy_digest=policy.digest,observation=actual,
                    started_runs=started,retired_unstarted=unstarted,retired_unstarted_evidence=unknown,
                    eligible_for_rollover=False,scoped_retirement_checks_satisfied=not reasons,
                    rollover_refusal_reasons=reasons,
                    physical_handoff_proven=False,complete_global_debt=False,native_ledger_writes=False)


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


def cache_recipient(unit, payload):
    """C remains default; an A public recipient must be selected explicitly."""
    recipient=payload.get('recipient','unit-c')
    need(type(recipient) is str and recipient in ('unit-a','unit-c') and unit==recipient,
         'cache_observer_requires_selected_public_recipient')


def dispatch(action, unit, payload, *, runtime_root, manifest_sha256):
    """Actual root entry; import alone performs no native operation."""
    need(os.geteuid() == 0 and unit in ('unit-a','unit-b','unit-c')
         and action in ('capture','enroll','guard','audit','observe-cache','inspect-paid-parent')
         and type(payload) is dict and len(canonical(payload)) <= 2000000, 'invalid_native_resume_RPC')
    with Runtime(runtime_root,manifest_sha256) as runtime:
        if action == 'capture': result = capture_history(unit,payload,runtime)
        elif action in ('enroll','guard','audit'): result = enrolled_action(action,unit,payload,runtime)
        elif action=='inspect-paid-parent': result=inspect_paid_parent(unit,payload,runtime)
        else:
            cache_recipient(unit,payload)
            from .public_cache_observer import observe_native
            before = stable(native_facts(unit))
            raw = [base64.b64decode(payload[k],validate=True) for k in ('manifest','signature','trust')]
            result = dict(observation=observe_native(*raw,payload['pins'],tuple(payload['wanted'])))
            need(before == stable(native_facts(unit)), 'cache_observation_facts_changed')
        runtime.check()
    return response_metadata(action,unit,result)
