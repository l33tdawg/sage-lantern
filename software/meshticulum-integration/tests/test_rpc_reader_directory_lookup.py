"""Fresh ancestry checks still surround retained lexical directory lookups."""
import os
from pathlib import Path
import threading

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from tests.test_rpc_reader_ancestry import tmp_path, sample


def test_repeated_directory_keeps_every_fresh_ancestry_stat_and_token(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path)
    calls={'fstat':[],'stat':[],'lseek':[],'open':[]}
    originals={name:getattr(os,name) for name in calls}
    try:
        for name,original in originals.items():
            def observed(*args,_name=name,_original=original,**kwargs):
                calls[_name].append((args,kwargs));return _original(*args,**kwargs)
            monkeypatch.setattr(os,name,observed)
        assert reader.directory(leaf)==reader._nodes[leaf][0]
        assert not calls['open']
        assert len(calls['stat'])==len(reader._nodes)
        assert len(calls['fstat'])==len(reader._nodes)+1
        assert len(calls['lseek'])==len(reader._tokens)+len(reader._nodes)
        assert all(kwargs['follow_symlinks'] is False for _args,kwargs in calls['stat'])
        assert [(a[0],k['dir_fd']) for a,k in calls['stat']]==[
            (name,fd) for _held,fd,name,_stamp,_token in reader._nodes.values()]
    finally:reader.close()


def test_repeated_path_preserves_equal_registry_snapshot_without_rebuilding_it(tmp_path):
    reader,parent,leaf,paths=sample(tmp_path)
    try:
        snapshot=reader._directory_state;identity=reader.directories[leaf]
        assert reader.read(paths[0],1024)==b'{"public":"fixture"}'
        assert reader._directory_state is snapshot and reader.directories[leaf] is identity
        assert reader.check() is True
    finally:reader.close()


def test_known_parent_new_leaf_registers_all_new_handles_and_refuses_later_drift(tmp_path):
    reader,parent,leaf,paths=sample(tmp_path)
    sibling=parent/'sibling';sibling.mkdir(mode=0o700)
    path=sibling/'new.json';path.write_bytes(b'new metadata');path.chmod(0o600)
    try:
        assert sibling not in reader._nodes
        assert reader.read(path,1024)==b'new metadata'
        assert sibling in reader._nodes and sibling in reader.directories
        assert reader.check() is True
        sibling.chmod(0o755)
        with pytest.raises(RuntimeError):reader.directory(sibling)
    finally:reader.close()


def test_normalized_existing_alias_keeps_named_ancestry_checks(tmp_path):
    reader,parent,leaf,paths=sample(tmp_path)
    alias=leaf/'..'/'leaf'
    try:
        assert reader.directory(alias)==reader._nodes[leaf][0]
        assert reader.directories[alias]==reader.directories[leaf]
        assert reader.check() is True
    finally:reader.close()


@pytest.mark.parametrize('where',['parent','leaf'])
@pytest.mark.parametrize('fault',['mode','replacement','symlink'])
def test_retained_lookup_refuses_current_ancestor_or_leaf_changes(tmp_path,where,fault):
    reader,parent,leaf,paths=sample(tmp_path)
    selected=parent if where=='parent' else leaf
    try:
        if fault=='mode':selected.chmod(0o777)
        else:
            saved=selected.with_name(selected.name+'-saved');selected.rename(saved)
            if fault=='symlink':selected.symlink_to(saved,target_is_directory=True)
            elif where=='parent':
                selected.mkdir(mode=0o700);(saved/'leaf').rename(leaf)
            else:selected.mkdir(mode=0o700)
        with pytest.raises((RuntimeError,OSError)):reader.directory(leaf)
    finally:reader.close()


@pytest.mark.parametrize('fault',['token','node','directories','owner'])
def test_retained_lookup_refuses_handle_state_and_owner_substitution(tmp_path,fault):
    reader,parent,leaf,paths=sample(tmp_path)
    try:
        if fault=='token':os.lseek(reader._nodes[parent][0],0,os.SEEK_SET)
        elif fault=='node':reader._nodes.pop(parent)
        elif fault=='directories':reader.directories[leaf][0]+=1
        else:reader._owner=(reader._owner[0],reader._owner[1]+1,reader._owner[2])
        with pytest.raises(RuntimeError):reader.directory(leaf)
    finally:reader.close()


def test_retained_lookup_is_not_a_foreign_thread_capability(tmp_path):
    reader,parent,leaf,paths=sample(tmp_path);errors=[]
    def foreign():
        try:reader.directory(leaf)
        except BaseException as error:errors.append(type(error))
    thread=threading.Thread(target=foreign)
    try:
        thread.start();thread.join(timeout=2)
        assert not thread.is_alive() and errors==[RuntimeError]
        assert reader.check() is True
    finally:reader.close()
