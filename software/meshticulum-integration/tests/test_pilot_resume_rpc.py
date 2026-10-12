# SPDX-License-Identifier: Apache-2.0
"""Local filesystem controls only; no native or transport qualification."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc


@pytest.fixture
def anchor():
    # The real ancestry control refuses ordinary world-writable /tmp ancestors.
    root = Path(tempfile.mkdtemp(prefix='rpc-control-', dir=Path(__file__).parent))
    os.chmod(root,0o700)
    try: yield root
    finally: shutil.rmtree(root)


def file(path, raw=b'public metadata'):
    path.write_bytes(raw); os.chmod(path,0o600); return path


def test_reader_stable_actual_file(anchor):
    path = file(anchor/'grant.json')
    with rpc.Reader() as reader:
        assert reader.read(path,100) == b'public metadata'
        assert reader.check() is True


@pytest.mark.parametrize('attack',['replace','mutate','restore_mtime','mode','unlink'])
def test_reader_rejects_later_change(anchor,attack):
    path = file(anchor/'grant.json')
    with rpc.Reader() as reader:
        reader.read(path,100); before = path.stat()
        if attack == 'replace':
            replacement = file(anchor/'replacement'); replacement.replace(path)
        elif attack in ('mutate','restore_mtime'):
            path.write_bytes(b'altered bytes!!')
            if attack == 'restore_mtime': os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        elif attack == 'mode': os.chmod(path,0o644)
        else: path.unlink()
        with pytest.raises((RuntimeError,FileNotFoundError)): reader.check()


@pytest.mark.parametrize('attack',['symlink','hardlink','fifo','oversize','mode'])
def test_reader_rejects_unsafe_initial_file(anchor,attack):
    original = file(anchor/'original'); path = anchor/'grant.json'
    if attack == 'symlink': path.symlink_to(original)
    elif attack == 'hardlink': os.link(original,path)
    elif attack == 'fifo': os.mkfifo(path,0o600)
    else:
        file(path,b'x'*101 if attack == 'oversize' else b'x')
        if attack == 'mode': os.chmod(path,0o644)
    with rpc.Reader() as reader, pytest.raises((RuntimeError,OSError)):
        reader.read(path,100)


def test_reader_nonblocking_owner_lock_refuses_exclusive_holder(anchor):
    path = file(anchor/'active',b'retired\n')
    fd = os.open(path,os.O_RDONLY)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with rpc.Reader() as reader, pytest.raises(BlockingIOError): reader.read(path,128,lock=True)
    finally: os.close(fd)


def test_reader_shared_lock_prevents_new_exclusive_owner(anchor):
    path = file(anchor/'active',b'retired\n')
    with rpc.Reader() as reader:
        reader.read(path,128,lock=True); fd = os.open(path,os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError): fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally: os.close(fd)


def test_reader_rejects_unsafe_ancestor(anchor):
    inner = anchor/'inner'; inner.mkdir(mode=0o777); os.chmod(inner,0o777)
    with rpc.Reader() as reader, pytest.raises(RuntimeError): reader.read(file(inner/'grant.json'),100)


def test_create_only_metadata_retains_existing_bytes(anchor):
    rpc.write_new(anchor,'capture.json',b'{"actual":true}')
    assert (anchor/'capture.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): rpc.write_new(anchor,'capture.json',b'replaced')
    assert (anchor/'capture.json').read_bytes() == b'{"actual":true}'


def test_parse_rejects_duplicate_keys():
    with pytest.raises(RuntimeError): rpc.parse(b'{"debt":1,"debt":0}')
    assert rpc.parse(b'{"actual_cost":null}')['actual_cost'] is None


@pytest.mark.parametrize('action,unit,payload',[('start','unit-c',{}),('capture','unit-z',{}),
    ('capture','unit-c',[]),('capture','unit-c',{'oversize':'x'*2000000})])
def test_dispatch_refuses_before_runtime_open(action,unit,payload):
    with pytest.raises(RuntimeError): rpc.dispatch(action,unit,payload,
        runtime_root=Path('/does-not-exist'),manifest_sha256='a'*64)


def test_runtime_refuses_unpinned_or_foreign_root():
    with pytest.raises(RuntimeError): rpc.Runtime(Path('/does-not-exist'),'not-a-digest')
    with pytest.raises(RuntimeError): rpc.Runtime(Path('/does-not-exist'),'a'*64)


def test_rpc_contract_schema_and_import_scope():
    assert rpc.CONTRACT_SCHEMA == 'meshticulum.private-resume-native-rpc.v1'
    assert len(rpc.HISTORY) == 36 and len(set(rpc.HISTORY)) == 36
    assert rpc.HISTORY[22] == rpc.V2_FAILED_BUNDLE_PAID
    assert rpc.HISTORY[23] == rpc.V3_PARTIAL_BUNDLE_PAID
    assert rpc.HISTORY[:5] == ('28765d19f654ce3162525afe9c3f89c5',
        '5ad7e65b7e50901a6f63629c04a776e2', '4d1b5d6fe8d4b022c5cf1e21cef7f8ca',
        'd60d8308326314a7753e651fa43a99e3', '4559277fa43913ce3935b078d6744a55')
    assert rpc.HISTORY[9] == rpc.ACCEPTED_BULK_PAID == '25a990a2d565c50b237ee45812a8c75f'
    assert rpc.HISTORY[10] == rpc.REVERSE_BULK_PAID == 'cb5869e6aa8de545a267007b085d4762'
    assert rpc.HISTORY[11] == rpc.SMALL_BULK_PAID == 'f6e147e70ec7ce07f673ea5e9cd2cdc2'
    assert rpc.HISTORY[12] == rpc.ZERO_RETRY_BULK_PAID == 'b8f9b5d9c5479c8d363c7b7e8014bc3b'
    assert rpc.HISTORY[13] == rpc.IMMEDIATE_RX_BULK_PAID == '806fdca055e79dfc0b86ff8f304bcdf9'
    assert rpc.HISTORY[14] == rpc.SF7_REPLICATE_PAID == '4ea23c6b8fa83366904ac62ffa98e2bf'
    assert rpc.HISTORY[15] == rpc.PUBLIC32_PAID == 'c26cd03a8fd427daf6b8d894e4b1d04c'
    assert rpc.HISTORY[16] == rpc.PUBLIC32_RETRY_PAID == '8dfcfdfe8f8be2c0b326520d70eab7f5'
    assert rpc.HISTORY[17] == rpc.PUBLIC32_SENDER_READY_PAID == '6f89e2a21919991e15224e393446928c'
    assert rpc.HISTORY[18] == rpc.PUBLIC32_COMPLETE_PAID == '62bcef4f1c74532fd7ab3ada1b8b94e2'
    assert rpc.HEX.fullmatch('a'*64) and rpc.SID.fullmatch('a'*32)
    assert rpc.stable(dict(unit='unit-c',host_boot_id='b',services={},service_lifetimes={},epoch_us=5)) == \
        dict(unit='unit-c',host_boot_id='b',services={},service_lifetimes={})
