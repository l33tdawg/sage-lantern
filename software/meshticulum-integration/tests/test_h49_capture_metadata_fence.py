"""Local strict-seal regression using pinned public H49 bytes; no native calls.

The existing prepared namespace is a source model. Full lineage/expiry/native
qualification remains separate; these controls verify its strict-seal boundary.
"""
import hashlib
import os
from pathlib import Path

import pytest

from lantern_meshticulum.bench import history_metadata_fence as fence_source
from lantern_meshticulum.bench import lora_management_incomplete_history as history
from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.history_identity_seal import capture, SealedHistory
from lantern_meshticulum.bench.history_metadata_fence import FenceError, HistoricalMetadataFence
from tests.test_capture_seal_inputs import prepared


NAMES = ('capture.json', 'history-binding.json', 'history-identity-seal.json', 'legacy-grant-seal.json')
PARENT = 'parents/' + history.PARENT
OLD_FENCE_SHA = '60ae79d8b0dd480efeb4a89d1faddac3a370777836886601d6b87f64e444ee10'


@pytest.fixture
def verified_h49(prepared):
    state, known = prepared
    public = Path(os.environ['MESHTICULUM_H49_FIXTURE']) / 'resume-history-preparation-49/unit-c-public'
    for name in NAMES:
        relative = PARENT + '/' + name
        raw = (public / relative).read_bytes()
        specification = history.PUBLIC_FILES['unit-c'][relative]
        assert len(raw) == specification['bytes']
        assert hashlib.sha256(raw).hexdigest() == specification['sha256']
        path = state / relative
        path.write_bytes(raw)
        path.chmod(0o600)
        known[relative] = specification['sha256']
    # Hashes originate from the independent immutable H49 record, not the
    # candidate hashes returned by seal_inputs or the seal being tested.
    return state, known


def seal_inputs(verified):
    state, known = verified
    with rpc.Reader() as reader:
        return rpc.seal_inputs(reader, 'unit-c', known, {}, {})


@pytest.mark.parametrize('changed', NAMES)
def test_four_verified_H49_capture_files_seal_reopen_and_detect_drift(verified_h49, changed):
    state, known = verified_h49
    files, directories = seal_inputs(verified_h49)
    assert all(files[PARENT + '/' + name] == known[PARENT + '/' + name] for name in NAMES)
    assert directories[PARENT + '/prepared-identities'] == []
    raw, pin = capture(state, owner_uid=os.geteuid(), expected_files=files, expected_directories=directories)
    with SealedHistory(state, owner_uid=os.geteuid(), seal_bytes=raw, expected_sha256=pin) as held:
        held.check()
        (state / PARENT / changed).write_bytes(b'local public mutation control')
        with pytest.raises(FenceError):
            held.check()


def test_same_verified_inputs_fail_with_exact_original_whitelist(verified_h49):
    state, _ = verified_h49
    files, directories = seal_inputs(verified_h49)
    current = Path(fence_source.__file__).read_text()
    fixed = '_LORA_INCOMPLETE_DIRECTORY,_LORA_ENDPOINT_INCOMPLETE_DIRECTORY,_LORA_MANAGEMENT_INCOMPLETE_DIRECTORY),'
    original = '_LORA_INCOMPLETE_DIRECTORY,_LORA_ENDPOINT_INCOMPLETE_DIRECTORY),'
    assert current.count(fixed) == 1
    original_source = current.replace(fixed, original)
    assert hashlib.sha256(original_source.encode()).hexdigest() == OLD_FENCE_SHA
    namespace = {'__name__': 'original_H49_whitelist_source_control'}
    exec(compile(original_source, 'original-H49-whitelist-control', 'exec'), namespace)
    with pytest.raises(namespace['FenceError'], match='outside_retained_capture_metadata'):
        namespace['HistoricalMetadataFence'](state, owner_uid=os.geteuid(),
            expected_files=files, expected_directories=directories)


def test_neighbor_parent_cannot_borrow_H49_capture_allowance(verified_h49):
    state, _ = verified_h49
    files, directories = seal_inputs(verified_h49)
    neighbor = history.PARENT[:-1] + '1'
    assert neighbor != history.PARENT and 'parents/' + neighbor not in directories
    target = state / 'parents' / neighbor
    target.mkdir(mode=0o700)
    path = target / 'capture.json'
    path.write_bytes(b'local public neighbor control')
    path.chmod(0o600)
    directories['parents'].append(neighbor)
    directories['parents/' + neighbor] = ['capture.json']
    files['parents/' + neighbor + '/capture.json'] = rpc.sha(path.read_bytes())
    with pytest.raises(FenceError, match='outside_retained_capture_metadata'):
        capture(state, owner_uid=os.geteuid(), expected_files=files, expected_directories=directories)


def test_H49_private_leaf_cannot_be_hashed_or_opened_as_capture_metadata(verified_h49, monkeypatch):
    state, _ = verified_h49
    files, directories = seal_inputs(verified_h49)
    path = state / PARENT / 'secret.bin'
    raw = b'local synthetic opaque leaf'
    path.write_bytes(raw)
    path.chmod(0o600)
    directories[PARENT].append('secret.bin')
    files[PARENT + '/secret.bin'] = rpc.sha(raw)
    original_open = os.open
    def guarded_open(name, *args, **kwargs):
        assert Path(name).name != 'secret.bin', 'private leaf was opened'
        return original_open(name, *args, **kwargs)
    monkeypatch.setattr(os, 'open', guarded_open)
    with pytest.raises(FenceError, match='prohibited_metadata_file'):
        capture(state, owner_uid=os.geteuid(), expected_files=files, expected_directories=directories)


def test_H49_unverified_public_hash_refuses_before_strict_seal(verified_h49):
    state, known = verified_h49
    known[PARENT + '/capture.json'] = '0' * 64
    with pytest.raises(RuntimeError, match='metadata_changed_before_seal'):
        seal_inputs((state, known))
