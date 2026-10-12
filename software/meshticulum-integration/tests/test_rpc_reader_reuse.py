"""Held-description reuse retains fresh bytes, names, locks and input bounds."""
import fcntl
import os

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from tests.test_rpc_reader_ancestry import tmp_path, sample


def test_repeated_reads_reuse_only_the_descriptor_and_reread_every_byte(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path)
    original=os.pread;reads=[];tokens=dict(reader._tokens);held=tuple(reader.held)
    def observed(*args):
        raw=original(*args);reads.append((*args,raw));return raw
    try:
        monkeypatch.setattr(os,'pread',observed)
        monkeypatch.setattr(os,'open',lambda *_a,**_k:pytest.fail('repeated input reopened'))
        for _ in range(3):
            for path in paths:assert reader.read(path,1024)==b'{"public":"fixture"}'
        assert len(reads)==6
        assert [row[0] for row in reads]==[record[1] for _ in range(3) for record in held]
        assert all(row[1:]==(1025,0,b'{"public":"fixture"}') for row in reads)
        assert dict(reader._tokens)==tokens and tuple(reader.held)==held
        assert reader.check() is True
    finally:reader.close()
    for fd in tokens:
        with pytest.raises(OSError):os.fstat(fd)


def test_each_repeated_read_enforces_its_own_smaller_limit(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path);size=len(reader.held[0][3]);fd=reader.held[0][1]
    try:
        assert reader.read(paths[0],size)==reader.held[0][3]
        monkeypatch.setattr(os,'pread',lambda *_a,**_k:pytest.fail('oversize input read'))
        with pytest.raises(RuntimeError,match='unsafe_rpc_metadata'):reader.read(paths[0],size-1)
        assert len(reader.held)==2 and os.fstat(fd).st_size==size
    finally:reader.close()


def test_repeated_lock_requests_upgrade_and_recheck_the_real_shared_lock(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path);fd=reader.held[0][1];original=fcntl.flock;calls=[]
    other=os.open(paths[0],os.O_RDONLY|os.O_CLOEXEC)
    def observed(*args):calls.append(args);return original(*args)
    try:
        monkeypatch.setattr(fcntl,'flock',observed)
        reader.read(paths[0],1024,lock=True);reader.read(paths[0],1024,lock=True)
        reader.read(paths[0],1024,lock=False)
        assert calls==[(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)]*2
        with pytest.raises(OSError):original(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        reader.close();original(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:reader.close();os.close(other)


def test_lock_upgrade_cannot_reuse_an_unlocked_observation(tmp_path):
    reader,parent,leaf,paths=sample(tmp_path);other=os.open(paths[0],os.O_RDONLY|os.O_CLOEXEC)
    try:
        fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(OSError):reader.read(paths[0],1024,lock=True)
        assert len(reader.held)==2
        fcntl.flock(other,fcntl.LOCK_UN)
        assert reader.read(paths[0],1024,lock=True)==reader.held[0][3]
    finally:reader.close();os.close(other)


@pytest.mark.parametrize('fault',['replacement','symlink','rewrite','same_bytes_new_stamp','parent'])
def test_repeated_read_refuses_replaced_or_changed_original_input(tmp_path,fault):
    reader,parent,leaf,paths=sample(tmp_path);path=paths[0];raw=path.read_bytes();ino=path.stat().st_ino
    try:
        if fault=='replacement':
            other=leaf/'new';other.write_bytes(raw);other.chmod(0o600);other.replace(path)
        elif fault=='symlink':path.rename(leaf/'saved');path.symlink_to(leaf/'saved')
        elif fault=='rewrite':path.write_bytes(b'x'*len(raw));assert path.stat().st_ino==ino
        elif fault=='same_bytes_new_stamp':
            info=path.stat();os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns+1000000000))
        else:
            parent.rename(tmp_path/'old-parent');parent.mkdir(mode=0o700)
            (tmp_path/'old-parent/leaf').rename(leaf)
            assert path.stat().st_ino==ino
        with pytest.raises((RuntimeError,OSError)):reader.read(path,1024)
    finally:reader.close()


def test_repeated_read_compares_fresh_bytes_even_with_spoofed_original_stamps(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path);path,fd,identity,raw=reader.held[0];original=rpc._stamp
    path.write_bytes(b'x'*len(raw))
    def observed(info):
        value=original(info)
        return identity if tuple(value[:2])==identity[:2] else value
    try:
        monkeypatch.setattr(rpc,'_stamp',observed)
        with pytest.raises(RuntimeError,match='rpc_repeated_metadata_bytes_changed'):reader.read(path,1024)
    finally:reader.close()


@pytest.mark.parametrize('fault',['replace_index','delete_entry','wrong_fd','wrong_bytes','extra_entry',
                                  'replace_index_state','replace_index_state_ref','rewrite_index_and_state'])
@pytest.mark.parametrize('when',['before','during_pread'])
def test_file_index_is_pinned_to_original_held_records(tmp_path,monkeypatch,fault,when):
    reader,parent,leaf,paths=sample(tmp_path);record=reader.held[0];original=os.pread;armed=[True]
    def mutate():
        if fault=='replace_index':reader._file_index=dict(reader._file_index)
        elif fault=='delete_entry':del reader._file_index[paths[0]]
        elif fault=='wrong_fd':reader._file_index[paths[0]]=(record[0],reader.held[1][1],*record[2:])
        elif fault=='wrong_bytes':reader._file_index[paths[0]]=(*record[:3],b'altered')
        elif fault=='extra_entry':reader._file_index[leaf/'extra']=record
        elif fault=='replace_index_state':reader._file_index_state=tuple(list(reader._file_index_state))
        elif fault=='replace_index_state_ref':reader._file_index_state_ref=tuple(list(reader._file_index_state))
        else:
            reader._file_index[paths[0]]=(*record[:3],b'altered')
            reader._file_index_state=tuple(reader._file_index.items())
    def observed(*args):
        raw=original(*args)
        if armed[0]:armed[0]=False;mutate()
        return raw
    try:
        if when=='before':mutate()
        else:monkeypatch.setattr(os,'pread',observed)
        with pytest.raises(RuntimeError):reader.read(paths[0],1024)
    finally:reader.close()


@pytest.mark.parametrize('fault',['lost','replaced_same_inode','changed_offset','cleared_nonblock','cleared_cloexec'])
def test_repeated_read_refuses_changed_open_description_without_closing_foreign_fd(tmp_path,fault):
    reader,parent,leaf,paths=sample(tmp_path);fd=reader.held[0][1];foreign=None
    try:
        if fault in ('lost','replaced_same_inode'):
            os.close(fd)
            if fault=='replaced_same_inode':
                foreign=os.open(paths[0],rpc._FLAGS)
                if foreign!=fd:os.dup2(foreign,fd);os.close(foreign);foreign=fd
        elif fault=='changed_offset':os.lseek(fd,0,os.SEEK_SET)
        elif fault=='cleared_nonblock':fcntl.fcntl(fd,fcntl.F_SETFL,fcntl.fcntl(fd,fcntl.F_GETFL)&~os.O_NONBLOCK)
        else:fcntl.fcntl(fd,fcntl.F_SETFD,0)
        with pytest.raises((RuntimeError,OSError)):reader.read(paths[0],1024)
        reader.close()
        if foreign is not None:assert os.fstat(foreign).st_ino==paths[0].stat().st_ino
    finally:
        reader.close()
        if foreign is not None:os.close(foreign)
