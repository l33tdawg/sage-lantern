"""Real held-file/namespace controls; no devices or native authority."""
from pathlib import Path
from contextlib import ExitStack
import os
import shutil
import tempfile
from types import SimpleNamespace

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench import pilot_native_caps as native
from tests import test_public32_pilot as public32
from tests.test_public32_pilot import protected_source,large_pack


@pytest.fixture
def tmp_path():
    path=Path(tempfile.mkdtemp(prefix='.reader-control-',dir=Path(__file__).parent))
    path.chmod(0o700)
    try:yield path
    finally:shutil.rmtree(path)


def sample(root):
    parent=root/'parent';parent.mkdir(mode=0o700)
    leaf=parent/'leaf';leaf.mkdir(mode=0o700)
    paths=[leaf/name for name in ('one.json','two.json')]
    for path in paths:
        path.write_bytes(b'{"public":"fixture"}');path.chmod(0o600)
    reader=rpc.Reader()
    for path in paths:reader.read(path,1024)
    return reader,parent,leaf,paths


def test_shared_ancestry_rechecks_all_bytes_without_reopening(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path)
    try:
        assert len(reader._nodes)==len(leaf.absolute().parts)
        assert len(reader.held)==2
        opened=[];read=[];original_open=os.open;original_pread=os.pread
        monkeypatch.setattr(os,'open',lambda *a,**k:opened.append(a) or original_open(*a,**k))
        monkeypatch.setattr(os,'pread',lambda *a,**k:read.append(a) or original_pread(*a,**k))
        for _ in range(5):assert reader.check() is True
        assert not opened and len(read)==10
        assert all(row[1]==reader.held[index%2][2][5]+1 for index,row in enumerate(read))
    finally:reader.close()


def test_rebound_stack_cannot_run_foreign_cleanup_or_leak_original_files(tmp_path):
    reader,parent,leaf,paths=sample(tmp_path);held_fd=reader.held[0][1]
    foreign=ExitStack();called=[];foreign.callback(lambda:called.append(True))
    reader.stack=foreign
    try:
        with pytest.raises(Exception):reader.check()
        reader.close()
        assert not called
        with pytest.raises(OSError):os.fstat(held_fd)
    finally:
        reader.close();foreign.close()


@pytest.mark.parametrize('field',['held','stack'])
@pytest.mark.parametrize('operation',['check','read'])
def test_pread_callback_cannot_rebind_reader_state_before_acceptance(tmp_path,monkeypatch,field,operation):
    reader,parent,leaf,paths=sample(tmp_path);original=os.pread;foreign=ExitStack();called=[]
    foreign.callback(lambda:called.append(True));armed=[True]
    def observed(*args):
        raw=original(*args)
        if armed[0]:
            armed[0]=False
            if field=='held':reader.held.clear()
            else:reader.stack=foreign
        return raw
    try:
        monkeypatch.setattr(os,'pread',observed)
        with pytest.raises(Exception):
            if operation=='check':reader.check()
            else:reader.read(paths[0],1024)
        reader.close();assert not called
    finally:reader.close();foreign.close()


@pytest.mark.parametrize('operation',['check','read'])
@pytest.mark.parametrize('mutation',['file_bytes','file_fd','parent'])
def test_pread_callback_file_descriptor_and_namespace_mutation_refuses(tmp_path,monkeypatch,operation,mutation):
    reader,parent,leaf,paths=sample(tmp_path);original=os.pread;foreign=None;armed=[True]
    def observed(fd,*args):
        nonlocal foreign
        raw=original(fd,*args)
        if armed[0]:
            armed[0]=False
            if mutation=='file_bytes':paths[0].write_bytes(b'x'*len(raw))
            elif mutation=='parent':
                parent.rename(tmp_path/'saved-parent');parent.mkdir(mode=0o700)
                (tmp_path/'saved-parent/leaf').rename(leaf)
            else:
                os.close(fd);foreign=os.open(paths[0],os.O_RDONLY)
                if foreign!=fd:os.dup2(foreign,fd);os.close(foreign);foreign=fd
        return raw
    try:
        monkeypatch.setattr(os,'pread',observed)
        with pytest.raises(Exception):
            if operation=='check':reader.check()
            else:reader.read(paths[0],1024)
        reader.close()
        if foreign is not None:os.fstat(foreign)
    finally:
        reader.close()
        if foreign is not None:os.close(foreign)


@pytest.mark.parametrize('identity',['pid','thread'])
def test_foreign_owner_cannot_check_or_close_retained_files(tmp_path,monkeypatch,identity):
    import threading
    reader,parent,leaf,paths=sample(tmp_path);fd=reader.held[0][1]
    module,name=(os,'getpid') if identity=='pid' else (threading,'get_ident');original=getattr(module,name)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module,name,lambda:original()+1)
            with pytest.raises(Exception):reader.check()
            reader.close();os.fstat(fd)
        reader.close()
        with pytest.raises(OSError):os.fstat(fd)
    finally:reader.close()


@pytest.mark.parametrize('where',['leaf','parent_preserving_leaf'])
def test_same_byte_directory_replacement_refuses(tmp_path,where):
    reader,parent,leaf,paths=sample(tmp_path);old_leaf=leaf.stat().st_ino
    try:
        if where=='leaf':
            leaf.rename(parent/'saved');shutil.copytree(parent/'saved',leaf)
        else:
            parent.rename(tmp_path/'saved-parent');parent.mkdir(mode=0o700)
            (tmp_path/'saved-parent/leaf').rename(leaf)
            assert leaf.stat().st_ino==old_leaf
        with pytest.raises(Exception):reader.check()
    finally:reader.close()


@pytest.mark.parametrize('where',['parent','leaf'])
def test_directory_mode_change_refuses(tmp_path,where):
    reader,parent,leaf,paths=sample(tmp_path)
    try:
        (parent if where=='parent' else leaf).chmod(0o750)
        with pytest.raises(Exception):reader.check()
    finally:reader.close()


def test_retained_ancestor_uid_change_refuses(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path);fd=reader._nodes[parent][0];original=os.fstat
    fields=('st_dev','st_ino','st_mode','st_uid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
    def observed(value):
        info=original(value)
        if value==fd:return SimpleNamespace(**{key:(info.st_uid+1 if key=='st_uid' else getattr(info,key)) for key in fields})
        return info
    try:
        monkeypatch.setattr(os,'fstat',observed)
        with pytest.raises(Exception):reader.check()
    finally:reader.close()


def test_changed_process_uid_refuses(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path);uid=os.geteuid()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os,'geteuid',lambda:uid+1)
            with pytest.raises(Exception):reader.check()
            reader.close()
            with pytest.raises(OSError):os.fstat(reader.held[0][1])
    finally:reader.close()


@pytest.mark.parametrize('which',['file','directory'])
@pytest.mark.parametrize('reuse',[False,True])
def test_lost_or_reused_descriptor_refuses_and_does_not_close_foreign(tmp_path,which,reuse):
    reader,parent,leaf,paths=sample(tmp_path)
    fd=reader.held[0][1] if which=='file' else reader._nodes[leaf][0]
    named=paths[0] if which=='file' else leaf;foreign=None
    try:
        os.close(fd)
        if reuse:
            foreign=os.open(named,os.O_RDONLY|(os.O_DIRECTORY if which=='directory' else 0))
            if foreign!=fd:
                os.dup2(foreign,fd);os.close(foreign);foreign=fd
            # Same inode, different open description at offset zero.
            assert os.lseek(foreign,0,os.SEEK_CUR)==0
        with pytest.raises(Exception):reader.check()
        reader.close()
        if foreign is not None:assert os.fstat(foreign).st_ino==named.stat().st_ino
    finally:
        reader.close()
        if foreign is not None:os.close(foreign)


def test_lost_descriptor_cannot_be_reaccepted_by_another_read(tmp_path):
    reader,parent,leaf,paths=sample(tmp_path);fd=reader.held[0][1]
    try:
        os.close(fd)
        with pytest.raises(Exception):reader.read(paths[0],1024)
        with pytest.raises(Exception):reader.check()
    finally:reader.close()


@pytest.mark.parametrize('fault',['bytes','same_bytes_new_stamp','named_inode','symlink'])
def test_held_file_bytes_stamp_type_and_identity_are_rechecked(tmp_path,fault):
    reader,parent,leaf,paths=sample(tmp_path);path=paths[0];raw=path.read_bytes();inode=path.stat().st_ino
    try:
        if fault=='bytes':path.write_bytes(b'x'*len(raw));assert path.stat().st_ino==inode
        elif fault=='same_bytes_new_stamp':
            stamp=path.stat();os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns+1000000000))
            assert path.read_bytes()==raw and path.stat().st_ino==inode
        elif fault=='named_inode':
            replacement=leaf/'replacement';replacement.write_bytes(raw);replacement.chmod(0o600);replacement.replace(path)
        else:path.rename(leaf/'saved-file');path.symlink_to(leaf/'saved-file')
        with pytest.raises(Exception):reader.check()
    finally:reader.close()


def test_byte_hash_is_checked_even_if_metadata_observation_is_spoofed(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path);path,fd,identity,_=reader.held[0];raw=path.read_bytes()
    path.write_bytes(b'x'*len(raw));original=rpc._stamp
    def observed(info):
        result=original(info)
        return identity if tuple(result[:2])==identity[:2] else result
    try:
        monkeypatch.setattr(rpc,'_stamp',observed)
        with pytest.raises(Exception):reader.check()
    finally:reader.close()


def test_original_deadline_and_protected_runtime_still_refuse(tmp_path,large_pack,protected_source,monkeypatch):
    now=[100000000,800000000];monkeypatch.setattr(public32,'clock',lambda:tuple(now))
    p,values=public32.pair(large_pack,'MR1.request');root=tmp_path/'native'
    packet,parent,p=public32.enroll(root,p,values,'unit-a')
    public32.provision(packet,state=root,facts=public32.facts('unit-a'),clock=public32.clock)
    caps=native._open_existing(root,p.parent_id,public32.facts('unit-a'),public32.clock)
    try:
        assert caps.current() is True
        now[:]=[406000000,1106000000]
        assert caps.current() is False
        with pytest.raises(Exception):caps._approve.check()
    finally:caps.close()


@pytest.mark.parametrize('fault',['owner_inode','runtime_bytes'])
def test_genuine_enrollment_refuses_owner_or_source_change(tmp_path,large_pack,protected_source,fault):
    p,values=public32.pair(large_pack,'MR1.request');root=tmp_path/'native'
    packet,parent,p=public32.enroll(root,p,values,'unit-a')
    public32.provision(packet,state=root,facts=public32.facts('unit-a'),clock=public32.clock)
    caps=native._open_existing(root,p.parent_id,public32.facts('unit-a'),public32.clock)
    try:
        assert caps.current() is True
        if fault=='owner_inode':
            target=parent/'owner.json';replacement=parent/'replacement';replacement.write_bytes(target.read_bytes())
            replacement.chmod(0o600);replacement.replace(target)
        else:
            target=protected_source[0]/'meshticulum/bench/authority.py';target.write_bytes(target.read_bytes()+b'\n')
        assert caps.current() is False
    finally:caps.close()
