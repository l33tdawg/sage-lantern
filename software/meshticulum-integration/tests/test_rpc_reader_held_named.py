"""Real filesystem controls for retained-parent named checks; no devices."""
from pathlib import Path
import os

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from tests.test_rpc_reader_ancestry import tmp_path, sample


def test_all_named_stamps_use_the_retained_parent_and_reread_every_byte(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path)
    named=[];reads=[];held=[];ancestry=[]
    original_stat=os.stat;original_pread=os.pread;original_fstat=os.fstat
    leaf_fd=reader._nodes[leaf][0]
    def observed_stat(name,**kwargs):
        if name in {p.name for p in paths}:named.append((name,dict(kwargs)))
        else:ancestry.append((name,dict(kwargs)))
        return original_stat(name,**kwargs)
    def observed_pread(fd,maximum,offset):
        raw=original_pread(fd,maximum,offset);reads.append((fd,maximum,offset,raw));return raw
    def observed_fstat(fd):
        if fd in {row[1] for row in reader.held}:held.append(fd)
        return original_fstat(fd)
    try:
        monkeypatch.setattr(os,'stat',observed_stat)
        monkeypatch.setattr(os,'pread',observed_pread)
        monkeypatch.setattr(os,'fstat',observed_fstat)
        monkeypatch.setattr(Path,'lstat',lambda *_a,**_k:pytest.fail('absolute named lookup'))
        assert reader.check() is True
        assert named==[(p.name,dict(dir_fd=leaf_fd,follow_symlinks=False))
                       for p in paths for _ in range(2)]
        assert held==[row[1] for row in reader.held for _ in range(2)]
        assert reads==[(fd,identity[5]+1,0,raw) for _p,fd,identity,raw in reader.held]
        assert len(ancestry)==2*len(reader._nodes)
        assert all(value['follow_symlinks'] is False for _name,value in ancestry)
    finally:reader.close()


def test_initial_read_uses_the_retained_parent_without_absolute_named_lookup(tmp_path,monkeypatch):
    parent=tmp_path/'private';parent.mkdir(mode=0o700)
    path=parent/'metadata.json';path.write_bytes(b'fresh bytes');path.chmod(0o600)
    named=[];original=os.stat
    def observed(name,**kwargs):
        if name==path.name:named.append(dict(kwargs))
        return original(name,**kwargs)
    with rpc.Reader() as reader:
        monkeypatch.setattr(os,'stat',observed)
        monkeypatch.setattr(Path,'lstat',lambda *_a,**_k:pytest.fail('absolute named lookup'))
        assert reader.read_held(path,1024)==b'fresh bytes'
        assert named==[dict(dir_fd=reader._nodes[parent][0],follow_symlinks=False)]*2
        assert reader._named[path]==(reader._nodes[parent][0],path.name)


@pytest.mark.parametrize('operation',['check','read'])
@pytest.mark.parametrize('fault',['missing','replacement','symlink','parent_preserving_leaf','same_inode_bytes'])
def test_mutation_after_byte_read_cannot_escape_named_or_ancestry_checks(tmp_path,monkeypatch,operation,fault):
    reader,parent,leaf,paths=sample(tmp_path);path=paths[0]
    original=os.pread;armed=[True];old_inode=path.stat().st_ino
    def observed(fd,maximum,offset):
        raw=original(fd,maximum,offset)
        if armed[0]:
            armed[0]=False
            if fault=='missing':path.rename(leaf/'saved')
            elif fault=='replacement':
                replacement=leaf/'replacement';replacement.write_bytes(raw)
                replacement.chmod(0o600);replacement.replace(path)
            elif fault=='symlink':
                path.rename(leaf/'saved');path.symlink_to(leaf/'saved')
            elif fault=='parent_preserving_leaf':
                parent.rename(tmp_path/'saved-parent');parent.mkdir(mode=0o700)
                (tmp_path/'saved-parent/leaf').rename(leaf)
                assert path.stat().st_ino==old_inode
            else:
                path.write_bytes(b'x'*len(raw));assert path.stat().st_ino==old_inode
        return raw
    try:
        monkeypatch.setattr(os,'pread',observed)
        with pytest.raises((RuntimeError,OSError)):
            if operation=='check':reader.check()
            else:reader.read_held(path,1024)
    finally:reader.close()


@pytest.mark.parametrize('fault',['mapping_object','parent_fd','name','added_entry'])
@pytest.mark.parametrize('during_read',[False,True])
def test_retained_named_state_substitution_refuses(tmp_path,monkeypatch,fault,during_read):
    reader,parent,leaf,paths=sample(tmp_path);path=paths[0];original=os.pread;armed=[True]
    def mutate():
        if fault=='mapping_object':reader._named=dict(reader._named)
        elif fault=='parent_fd':reader._named[path]=(reader._nodes[parent][0],path.name)
        elif fault=='name':reader._named[path]=(reader._nodes[leaf][0],paths[1].name)
        else:reader._named[leaf/'extra']=(reader._nodes[leaf][0],'extra')
    def observed(*args):
        raw=original(*args)
        if armed[0]:armed[0]=False;mutate()
        return raw
    try:
        if during_read:monkeypatch.setattr(os,'pread',observed)
        else:mutate()
        with pytest.raises((RuntimeError,OSError)):reader.check()
    finally:reader.close()


def test_parent_fd_reuse_during_named_stat_refuses_and_preserves_foreign_handle(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path);leaf_fd=reader._nodes[leaf][0]
    original=os.stat;foreign=None;armed=[True]
    def observed(name,**kwargs):
        nonlocal foreign
        info=original(name,**kwargs)
        if name==paths[0].name and armed[0]:
            armed[0]=False;os.close(leaf_fd)
            foreign=os.open(leaf,os.O_RDONLY|os.O_DIRECTORY)
            if foreign!=leaf_fd:os.dup2(foreign,leaf_fd);os.close(foreign);foreign=leaf_fd
            assert os.lseek(foreign,0,os.SEEK_CUR)==0
        return info
    try:
        monkeypatch.setattr(os,'stat',observed)
        with pytest.raises(RuntimeError,match='rpc_reader_descriptor_changed'):reader.check()
        reader.close()
        assert foreign is not None and os.fstat(foreign).st_ino==leaf.stat().st_ino
    finally:
        reader.close()
        if foreign is not None:os.close(foreign)
