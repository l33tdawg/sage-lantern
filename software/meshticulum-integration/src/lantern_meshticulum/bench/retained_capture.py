# SPDX-License-Identifier: Apache-2.0
"""Exact expired capture-only metadata; no enrollment, claims or debt."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from .history_identity_seal import _Ancestry
from .history_metadata_fence import _FLAGS, _need, _stamp
from meshticulum.ip.journal import private

PAID_HISTORY = (
    '28765d19f654ce3162525afe9c3f89c5',
    '5ad7e65b7e50901a6f63629c04a776e2',
    '4d1b5d6fe8d4b022c5cf1e21cef7f8ca',
    'd60d8308326314a7753e651fa43a99e3',
    '4559277fa43913ce3935b078d6744a55',
    '4034f8979b985660df21859606c4f8a7',
    'ba4572e95e598ad6c17fc954c3792db7',
    '1df30b47fa36b8d47328fb886528a7a9',
    'f153a7a87a2803db85078df81a6a1aaf',
    '25a990a2d565c50b237ee45812a8c75f',
    'cb5869e6aa8de545a267007b085d4762',
    'f6e147e70ec7ce07f673ea5e9cd2cdc2',
    'b8f9b5d9c5479c8d363c7b7e8014bc3b',
    '806fdca055e79dfc0b86ff8f304bcdf9',
    '4ea23c6b8fa83366904ac62ffa98e2bf',
    'c26cd03a8fd427daf6b8d894e4b1d04c',
    '8dfcfdfe8f8be2c0b326520d70eab7f5',
    '6f89e2a21919991e15224e393446928c',
    '62bcef4f1c74532fd7ab3ada1b8b94e2',
    '141c892d83b6064a800c11aab6444f28',
    '05c36e09fd4056c94d341d2dd32243f4',
    'bccb99afe6589968190b131eeaf372b3',
    '487ae574816ff5444de03e3daa4401e4',
    'e01508ddcae7389728add074de4876b7',
    '2429969fcbb0f57b3deaeda7187036c2',
    '7746ddba2544ce79c01a195f1e8746ce',
    '57339cca60ddf62b41eafac60147a252',
    '4aa3ad2bc1346cae82bc90d8abd6dcd4',
    'a3ded77ef251c9d7e085ab790b9ac5b3',
    '76fe38883690e058771940d51d0d57d6',
    '7630420c0e72181d6e7239c105c165c6',
    '4ca8d86644aa24e6fefd06872a9cd131',
    '6be212b13351cb9852ed07ba7998be96',
)
# Capture638 froze the first twelve paid parents; later completed runs do not alter it.
UNADMITTED_PAID_HISTORY = PAID_HISTORY[:12]
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

UNADMITTED_PARENT = '63830cfa1d045680a9e4961d0d6b69c8'
CAPTURE_ONLY_PARENTS = (CAPTURE_PARENT, UNADMITTED_PARENT)
UNADMITTED_START = 1791711235585621
UNADMITTED_END = 1791711535585621
UNADMITTED_REVIEW = '0d0bb487bd049804a4889e1f2ad7022a8e19b06c6e3ba499e8eb24d5f551e526'
UNADMITTED = {
    'unit-a': dict(boot='34b27358-8e12-44e7-a39f-410af5dbc491', phase={'deadline_monotonic_us': 170888293253, 'epoch_us': 1791711255803225, 'monotonic_us': 170608510857}, files={
        'capture.json': '51546180c3df786f17e61e60537147ad2012c8c32ccc714941345f0013ae3c50',
        'history-binding.json': '9ea86df63b1618b923a0ec3acc1a0caf299ce863378be4498ae534faa3954ecb',
        'history-identity-seal.json': 'a6bf6617626a31311d5309d2f1df7dba05a013a0ae1cf65483a3c955d87d0fa5',
        'legacy-grant-seal.json': '0dfaff049fb4321872fc68c2b29460673805d72f728c8e3e8b3133e9a3c2082e',
    }),
    'unit-b': dict(boot='dfe04f99-f765-45f0-aba7-27b8eaad902b', phase={'deadline_monotonic_us': 170839055770, 'epoch_us': 1791711250366579, 'monotonic_us': 170553836728}, files={
        'capture.json': 'e7c293cab1f857fcd265602c549fc07aefaf5e8e4efdf1a2df82fedd5585e041',
        'history-binding.json': '1caebbb6aca0ea73d5ea392125acb2aec77f94627d1f94917436f2684c2d3529',
        'history-identity-seal.json': '89f19e676cfcb99c8b738028dfb859c2b54f53fcf9e3c8f1689ea438fea6cc96',
        'legacy-grant-seal.json': '95539e6026afa5b68d01686e15c50661b2d3d1cca5079957d3c682e69f05859a',
    }),
    'unit-c': dict(boot='21f9c178-6090-40d9-9359-ecf1c7b950b1', phase={'deadline_monotonic_us': 167828305342, 'epoch_us': 1791711244657041, 'monotonic_us': 167537376762}, files={
        'capture.json': '6e08e8ba899c42fcd102862f24b32483a368d8123912943323df14163c7900fb',
        'history-binding.json': '53710d626619f5bf09a89e78500f9300c87c62fde3566de02b1cd16786b9839d',
        'history-identity-seal.json': '28604bac57bf3c4f7ecda50d6a3c6a11a2c71837a3e972f627bb884da6a27b64',
        'legacy-grant-seal.json': '7fa4b98f711e9a045c93ac5a1c864fe726ad414d4d8b502485ef31e8028f9cdc',
    }),
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
    rows = [(CAPTURE_PARENT, CAPTURE_FILES)] if unit == 'unit-c' else []
    try: (Path(state)/'parents'/UNADMITTED_PARENT).lstat()
    except FileNotFoundError: pass  # Optional absence is only for old local fixtures.
    else: rows.append((UNADMITTED_PARENT, UNADMITTED[unit]['files']))
    return dict(files={'parents/'+parent+'/'+name:pin for parent,pins in rows for name,pin in pins.items()},
                directories=tuple(path for parent,_ in rows for path in
                                  ('parents/'+parent, 'parents/'+parent+'/prepared-identities')))


def _artifact_present(state, unit, clock, parent_id, pins, pinned_boot, start, end, review,
                      required, expected_phase=None):
    target = Path(state).absolute()/'parents'/parent_id
    try: target.lstat()
    except FileNotFoundError:
        _need(not required, 'missing_retained_capture'); return False
    raw_files, descriptors = {}, []
    try:
        with _Ancestry(target, os.geteuid()) as parent, \
                _Ancestry(target/'prepared-identities', os.geteuid()) as empty:
            expected = set(pins)|{'prepared-identities'}
            directory_stamps = _stamp(os.fstat(parent.fds[-1])), _stamp(os.fstat(empty.fds[-1]))
            _need(set(os.listdir(parent.fds[-1])) == expected and not os.listdir(empty.fds[-1]),
                  'retained_capture_namespace_changed')
            for name, pin in pins.items():
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
                  and record['unit'] == binding['unit'] == unit
                  and record['parent_id'] == binding['parent_id'] == parent_id
                  and record['history_binding_sha256'] == pins['history-binding.json']
                  and record['review_sha256'] == review
                  and record['facts']['unit'] == unit
                  and record['facts']['host_boot_id'] == pinned_boot == current_boot()
                  and record['phase_start_epoch_us'] == start and record['phase_end_epoch_us'] == end
                  and record['actual_history_verified'] is True and record['global_debt_complete'] is False
                  and record['original_unknown_actual_cost'] is None and type(record['refunds']) is int
                  and record['refunds'] == 0, 'retained_capture_scope_changed')
            _need(binding['schema'] == 'meshticulum.private-resume-history-binding.v2'
                  and binding['strict_sha256'] == pins['history-identity-seal.json']
                  and binding['legacy_sha256'] == pins['legacy-grant-seal.json']
                  and binding['parent_identity'] == list(_stamp(os.fstat(parent.fds[-1]))[:4]),
                  'retained_capture_binding_changed')
            phase = record['native_phase']; now = clock()
            _need(expected_phase is None or phase == expected_phase, 'retained_capture_native_phase_changed')
            if parent_id == UNADMITTED_PARENT:
                _need(tuple(v['policy']['parent_id'] for v in record['history']['previous']) == UNADMITTED_PAID_HISTORY,
                      'unadmitted_capture_paid_history_changed')
            _need(type(now) is tuple and len(now) == 2 and all(type(v) is int and v > 0 for v in now)
                  and all(type(phase[k]) is int and phase[k] > 0 for k in
                          ('epoch_us','monotonic_us','deadline_monotonic_us'))
                  and start <= phase['epoch_us'] < end
                  and phase['deadline_monotonic_us'] == phase['monotonic_us']+end-phase['epoch_us']
                  and now[0] >= end and now[1] >= phase['deadline_monotonic_us'],
                  'retained_capture_phase_not_expired')
            for name, (raw, stamp, fd) in raw_files.items():
                _need(_stamp(os.fstat(fd)) == stamp == _stamp(os.stat(name,dir_fd=parent.fds[-1],follow_symlinks=False))
                      and os.pread(fd,len(raw)+1,0) == raw, 'retained_capture_final_drift')
            parent.check(); empty.check()
            _need(directory_stamps == (_stamp(os.fstat(parent.fds[-1])),_stamp(os.fstat(empty.fds[-1])))
                  and set(os.listdir(parent.fds[-1])) == expected and not os.listdir(empty.fds[-1]),
                  'retained_capture_namespace_changed')
        return True
    finally:
        for fd in reversed(descriptors): os.close(fd)


def historical_parent_ids(state, unit, clock=current_clock, *, require_retained=False,
                          require_unadmitted=False):
    """Read protected artifacts; native RPC explicitly requires both applicable captures."""
    _need(unit in ('unit-a','unit-b','unit-c') and callable(clock)
          and type(require_retained) is bool and type(require_unadmitted) is bool,
          'invalid_retained_capture_scope')
    retained = ()
    if unit == 'unit-c' and _artifact_present(state,unit,clock,CAPTURE_PARENT,CAPTURE_FILES,
            CAPTURE_BOOT,CAPTURE_START,CAPTURE_END,CAPTURE_REVIEW,require_retained):
        retained += (CAPTURE_PARENT,)
    row = UNADMITTED[unit]
    if _artifact_present(state,unit,clock,UNADMITTED_PARENT,row['files'],row['boot'],
            UNADMITTED_START,UNADMITTED_END,UNADMITTED_REVIEW,require_unadmitted,row['phase']):
        retained += (UNADMITTED_PARENT,)
    return PAID_HISTORY+retained
