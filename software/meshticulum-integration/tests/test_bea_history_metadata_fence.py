"""Pinned H43/public bytes in owned fence fixtures; no native observations/grants.

The four capture-named leaves use actual H43 payload bytes as opaque fixture
content. They test filename confinement only, and are not persisted captures.
Native database bytes are neither synthesized nor opened by these controls.
"""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pytest
from lantern_meshticulum.bench import fast_bundle_bea_history as history
from lantern_meshticulum.bench import history_metadata_fence as fence_source
from lantern_meshticulum.bench.history_metadata_fence import FenceError, HistoricalMetadataFence
from lantern_meshticulum.bench.history_identity_seal import capture, SealedHistory
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy
from lantern_meshticulum.bench import pilot_resume_rpc as rpc

sha = lambda raw:hashlib.sha256(raw).hexdigest()
CAPTURE_NAMES = ('capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json')
PARENT = 'parents/'+history.PARENT
PREPARED = PARENT+'/prepared-identities'


def pinned(path, digest, maximum=2500000):
    raw = path.read_bytes()
    assert len(raw) <= maximum and sha(raw) == digest
    return raw, json.loads(raw)


@pytest.fixture(scope='module', params=['unit-a','unit-b','unit-c'])
def actual(request):
    value = os.environ.get('MESHTICULUM_H43_FIXTURE')
    if not value:pytest.skip('MESHTICULUM_H43_FIXTURE must name the actual reviewed H43 preparation lane')
    base = Path(value).resolve()
    raw, receipt = pinned(base/'resume-history-preparation-43/RECEIPT.json', history.NEXT_HISTORY_RECEIPT_SHA256)
    raw, payload = pinned(base/'resume-history-preparation-43'/(request.param+'.json'), receipt['payload_sha256'][request.param])
    completion_path = base.parent/'meshticulum-mcu-hardware-preflight-20261014/EXPIRED_BEA_ABC_COMPLETION.json'
    _, completion = pinned(completion_path, history.EXPIRY_COMPLETION_SHA256)
    entry = completion['units'][request.param]
    _, wrapper = pinned(Path(entry['path']), entry['sha256'])
    native = wrapper['observation'];row = payload['previous'][-1]
    assert wrapper['process_closed'] is wrapper['credentials_cleared'] is True
    assert native['original_clocks_expired'] is True and row['policy']['parent_id'] == history.PARENT
    assert native['native_state_writes'] is native['radio_opened'] is False
    assert row['prior_metadata']['actual_cost'] is None and row['prior_metadata']['complete_global_debt'] is False
    return request.param, native, row, raw


@pytest.fixture
def owned(actual):
    unit, native, row, payload_raw = actual
    directory = tempfile.TemporaryDirectory(prefix='.bea-fence-', dir=Path(__file__).parent)
    root = Path(directory.name)/'state';root.mkdir(mode=0o700)
    (root/PREPARED).mkdir(parents=True,mode=0o700)
    (root/'runs').mkdir(mode=0o700)
    contents = {}
    for relative, record in native['public_metadata'].items():
        raw = base64.b64decode(record['base64'],validate=True)
        assert sha(raw) == record['sha256'];contents[relative] = raw
    for name in CAPTURE_NAMES:contents[PARENT+'/'+name] = payload_raw
    for relative, raw in contents.items():
        path = root/relative;path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        path.write_bytes(raw);path.chmod(0o600)
    for path in root.rglob('*'):
        if path.is_dir():path.chmod(0o700)
    def expected():
        directories = {'':sorted(p.name for p in root.iterdir())}
        for path in root.rglob('*'):
            if path.is_dir():directories[str(path.relative_to(root))] = sorted(p.name for p in path.iterdir())
        return {name:sha(raw) for name,raw in contents.items()}, directories
    try:yield root, contents, expected, unit, native, row
    finally:directory.cleanup()


def test_actual_h43_metadata_fences_and_seals(owned):
    root, contents, expected, unit, native, row = owned
    files, directories = expected()
    count = 0 if unit == 'unit-b' else 10
    assert len(directories[PREPARED]) == count
    rpc.qualify_gfsk250_mb1_partial_costs(unit,ParentPolicy.from_dict(row['policy']),native['observation'],row)
    with HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories) as fence:
        fence.check()
        class Reader:
            def directory(self, path):fence.check();assert path.is_dir()
            def read(self, path, maximum):
                raw = fence.around(path.read_bytes);assert len(raw) <= maximum;return raw
            def check(self):fence.check()
        actual_files = history.metadata_files(Reader(),root,unit,row)
        assert len(actual_files) == {'unit-a':15,'unit-b':0,'unit-c':10}[unit]
        fds = tuple(fence._fds)
    assert fence._closed and not fence._fds and fds
    raw, pin = capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)
    with SealedHistory(root,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin) as held:
        held.check()
        assert set(held._data['files']) == set(files)
    assert held._closed and not held._fence._fds and not held._chain.fds


@pytest.mark.parametrize('fault',['extra_stage','foreign_stage_parent','foreign_ack_run','foreign_piece_run','secret'])
def test_exact_bea_allowance_refuses_unrelated_metadata(owned, fault):
    root, contents, expected, unit, native, row = owned
    relative = {'extra_stage':PARENT+'/fast-stage-5.json',
        'foreign_stage_parent':'parents/'+('f'*32)+'/fast-stage-0.json',
        'foreign_ack_run':'runs/'+('f'*32)+'/fast-final-ack.json',
        'foreign_piece_run':'runs/0446fd2ef134ce6e222945b87a5c46d4/verified-piece.json',
        'secret':PARENT+'/private-key.bin'}[fault]
    raw = next(iter(contents.values()));path = root/relative
    path.parent.mkdir(parents=True,mode=0o700,exist_ok=True);path.write_bytes(raw);path.chmod(0o600)
    contents[relative] = raw;files,directories = expected()
    with pytest.raises(FenceError):
        capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)


@pytest.mark.parametrize('fault',['missing_identity_pin','wrong_identity_pin','nine_identities','eleven_identities'])
def test_ten_actual_identity_namespace_has_no_partial_acceptance(owned,fault):
    root, contents, expected, unit, native, row = owned
    if unit == 'unit-b':pytest.skip('zero endpoint peer has no prepared identity record')
    relative = next(name for name in contents if name.startswith(PREPARED+'/'))
    if fault == 'nine_identities':(root/relative).unlink();contents.pop(relative)
    elif fault == 'eleven_identities':
        extra = PREPARED+'/'+('f'*32)+'.json';raw = contents[relative]
        (root/extra).write_bytes(raw);(root/extra).chmod(0o600);contents[extra] = raw
    files,directories = expected()
    if fault == 'missing_identity_pin':files.pop(relative)
    elif fault == 'wrong_identity_pin':files[relative] = 'f'*64
    with pytest.raises(FenceError):
        capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)


@pytest.mark.parametrize('kind',['stage','ack','piece','identity','capture'])
def test_original_seal_refuses_changed_actual_metadata(owned,kind):
    root, contents, expected, unit, native, row = owned
    endings = {'stage':'fast-stage-0.json','ack':'fast-final-ack.json','piece':'verified-piece.json',
               'identity':'.json','capture':'capture.json'}
    matches = [p for p in contents if p.endswith(endings[kind]) and (kind != 'identity' or p.startswith(PREPARED+'/'))]
    if not matches:pytest.skip('this exact unit has no '+kind+' metadata')
    files,directories = expected();raw,pin = capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)
    with SealedHistory(root,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin) as held:
        held.check();(root/matches[0]).write_bytes(b'changed')
        with pytest.raises(FenceError):held.check()
        with pytest.raises(FenceError):held.check()


def test_original_metadata_allowances_remain_exact():
    expected = set()
    for unit in ('unit-a','unit-c'):
        expected.update('parents/'+history.PARENT+'/'+name for name in history.FAST_STAGE_SHA256[unit])
        expected.update('runs/'+run+'/fast-final-ack.json' for run in history.FINAL_ACK_SHA256[unit])
        expected.update('runs/'+run+'/verified-piece.json' for run in history.VERIFIED_PIECE_SHA256[unit])
    assert len(expected) == 20
    assert expected <= fence_source._EXTRA_FILES
    assert fence_source._IDENTITY_COUNTS[PREPARED] == (0,10)
