# SPDX-License-Identifier: Apache-2.0
"""Exact expired C capture metadata only; no enrollment, claims or debt."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from .history_identity_seal import _Ancestry
from .history_metadata_fence import _FLAGS, _need, _stamp
from meshticulum.ip.journal import private

PAID_HISTORY = ('28765d19f654ce3162525afe9c3f89c5', '5ad7e65b7e50901a6f63629c04a776e2',
                '4d1b5d6fe8d4b022c5cf1e21cef7f8ca', 'd60d8308326314a7753e651fa43a99e3',
                '4559277fa43913ce3935b078d6744a55', '4034f8979b985660df21859606c4f8a7',
                'ba4572e95e598ad6c17fc954c3792db7')
CAPTURE_PARENT = 'd0f37a4030fbe55d2829e8c0cebcee5b'
CAPTURE_BOOT = '21f9c178-6090-40d9-9359-ecf1c7b950b1'
CAPTURE_START = 1791686240849957
CAPTURE_END = 1791687139849957
CAPTURE_REVIEW = '59856b67b9b76ee42a3cd6d7ff2b65565f012198914cbfa47db84cf1bbc3bfd5'
CAPTURE_FILES = {
    'capture.json': '2366b0e885b7df309b89274340e7dee082690841e553dabc26638d83b93cac6d',
    'history-binding.json': '93d18455bc2a3816a82e246f0f3651a5b740a0688071b371a2e9c325cd36e39b',
    'history-identity-seal.json': '9ac3b639e2654f04d0104ae21894832a021673b6d3055abf1ce472f83c5248e8',
    'legacy-grant-seal.json': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc',
}

def current_clock(): return time.time_ns()//1000, time.monotonic_ns()//1000
def current_boot(): return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def _sha(raw): return hashlib.sha256(raw).hexdigest()

def _parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result, 'duplicate_retained_capture_key'); result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    _need(type(value) is dict, 'invalid_retained_capture_json')
    return value

def capture_paths(state, unit):
    """Fixed path/hash descriptors, never proof that those paths were observed."""
    _need(unit in ('unit-a','unit-b','unit-c'), 'invalid_retained_capture_unit')
    parent = 'parents/'+CAPTURE_PARENT
    return dict(files={} if unit != 'unit-c' else {parent+'/'+n:h for n,h in CAPTURE_FILES.items()},
                directories=() if unit != 'unit-c' else (parent,parent+'/prepared-identities'))

def historical_parent_ids(state, unit, clock=current_clock, *, require_retained=False):
    """Read exact expired artifact; optional absence retains only paid parents."""
    _need(unit in ('unit-a','unit-b','unit-c') and callable(clock)
          and type(require_retained) is bool, 'invalid_retained_capture_scope')
    if unit != 'unit-c': return PAID_HISTORY
    target = Path(state).absolute()/'parents'/CAPTURE_PARENT
    try: target.lstat()
    except FileNotFoundError:
        _need(not require_retained, 'missing_retained_capture'); return PAID_HISTORY
    raw_files, descriptors = {}, []
    try:
        with _Ancestry(target, os.geteuid()) as parent, \
                _Ancestry(target/'prepared-identities', os.geteuid()) as empty:
            expected = set(CAPTURE_FILES)|{'prepared-identities'}
            directory_stamps = _stamp(os.fstat(parent.fds[-1])), _stamp(os.fstat(empty.fds[-1]))
            _need(set(os.listdir(parent.fds[-1])) == expected and not os.listdir(empty.fds[-1]),
                  'retained_capture_namespace_changed')
            for name, pin in CAPTURE_FILES.items():
                fd = os.open(name, _FLAGS, dir_fd=parent.fds[-1]); descriptors.append(fd)
                first = os.fstat(fd); private(first)
                _need(first.st_size <= 2000000, 'retained_capture_size_bound')
                fcntl.flock(fd, fcntl.LOCK_SH|fcntl.LOCK_NB)
                raw = os.pread(fd, first.st_size+1, 0)
                _need(len(raw) == first.st_size and _sha(raw) == pin
                      and _stamp(first) == _stamp(os.fstat(fd))
                      == _stamp(os.stat(name,dir_fd=parent.fds[-1],follow_symlinks=False)),
                      'retained_capture_file_changed')
                raw_files[name] = (raw, _stamp(first), fd)
            record = _parse(raw_files['capture.json'][0]); binding = _parse(raw_files['history-binding.json'][0])
            _need(record['schema'] == 'meshticulum.private-resume-actual-capture.v1'
                  and record['unit'] == binding['unit'] == 'unit-c'
                  and record['parent_id'] == binding['parent_id'] == CAPTURE_PARENT
                  and record['history_binding_sha256'] == CAPTURE_FILES['history-binding.json']
                  and record['review_sha256'] == CAPTURE_REVIEW
                  and record['facts']['unit'] == 'unit-c'
                  and record['facts']['host_boot_id'] == CAPTURE_BOOT == current_boot()
                  and record['phase_start_epoch_us'] == CAPTURE_START and record['phase_end_epoch_us'] == CAPTURE_END
                  and record['actual_history_verified'] is True and record['global_debt_complete'] is False
                  and record['original_unknown_actual_cost'] is None and type(record['refunds']) is int
                  and record['refunds'] == 0, 'retained_capture_scope_changed')
            _need(binding['schema'] == 'meshticulum.private-resume-history-binding.v2'
                  and binding['strict_sha256'] == CAPTURE_FILES['history-identity-seal.json']
                  and binding['legacy_sha256'] == CAPTURE_FILES['legacy-grant-seal.json']
                  and binding['parent_identity'] == list(_stamp(os.fstat(parent.fds[-1]))[:4]),
                  'retained_capture_binding_changed')
            phase = record['native_phase']; now = clock()
            _need(type(now) is tuple and len(now) == 2 and all(type(v) is int and v > 0 for v in now)
                  and all(type(phase[k]) is int and phase[k] > 0 for k in
                          ('epoch_us','monotonic_us','deadline_monotonic_us'))
                  and CAPTURE_START <= phase['epoch_us'] < CAPTURE_END
                  and phase['deadline_monotonic_us'] == phase['monotonic_us']+CAPTURE_END-phase['epoch_us']
                  and now[0] >= CAPTURE_END and now[1] >= phase['deadline_monotonic_us'],
                  'retained_capture_phase_not_expired')
            for name, (raw, stamp, fd) in raw_files.items():
                _need(_stamp(os.fstat(fd)) == stamp == _stamp(os.stat(name,dir_fd=parent.fds[-1],follow_symlinks=False))
                      and os.pread(fd,len(raw)+1,0) == raw, 'retained_capture_final_drift')
            parent.check(); empty.check()
            _need(directory_stamps == (_stamp(os.fstat(parent.fds[-1])),_stamp(os.fstat(empty.fds[-1])))
                  and set(os.listdir(parent.fds[-1])) == expected and not os.listdir(empty.fds[-1]),
                  'retained_capture_namespace_changed')
        return PAID_HISTORY+(CAPTURE_PARENT,)
    finally:
        for fd in reversed(descriptors): os.close(fd)
