"""Real protected files through the unmodified nested history metadata helper.

Only the enclosing history/collector context is omitted: this executes its exact
AST with a real Reader and a local STATE. It grants no native/history authority.
"""
import ast
import copy
import fcntl
import inspect
import os

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from tests.test_rpc_reader_ancestry import tmp_path, sample


def metadata(reader,root,known):
    tree=ast.parse(inspect.getsource(rpc.full_history))
    node=next(n for n in tree.body[0].body if isinstance(n,ast.FunctionDef) and n.name=='metadata')
    namespace=dict(vars(rpc),reader=reader,STATE=root,known=known)
    exec(compile(ast.Module(body=[copy.deepcopy(node)],type_ignores=[]),rpc.__file__,'exec'),namespace)
    return namespace['metadata']


def test_history_helper_retains_new_files_and_freshly_rereads_known_bytes(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path);known={};helper=metadata(reader,tmp_path,known)
    fresh=leaf/'fresh.json';fresh.write_bytes(b'new protected history');fresh.chmod(0o600)
    try:
        assert helper(fresh,1024)==b'new protected history'
        held=tuple(reader.held);tokens=dict(reader._tokens);reads=[];original=os.pread
        def observed(*args):reads.append(args);return original(*args)
        monkeypatch.setattr(os,'pread',observed)
        monkeypatch.setattr(os,'open',lambda *_a,**_k:pytest.fail('known metadata reopened'))
        # The existing held-read path must not fall back to directory setup.
        monkeypatch.setattr(reader,'directory',lambda *_a,**_k:pytest.fail('known directory setup repeated'))
        assert helper(fresh,1024)==b'new protected history'
        assert helper(paths[0],1024)==b'{"public":"fixture"}'
        assert [v[0] for v in reads]==[held[-1][1],held[0][1]]
        assert tuple(reader.held)==held and reader._tokens==tokens
        assert known=={str(p.relative_to(tmp_path)):rpc.sha(raw)
                      for p,raw in ((fresh,b'new protected history'),(paths[0],b'{"public":"fixture"}'))}
    finally:reader.close()


@pytest.mark.parametrize('fault',['bytes','replacement','symlink','mode','parent_preserving_leaf'])
@pytest.mark.parametrize('during_read',[False,True])
def test_history_helper_refuses_changed_bytes_identity_or_ancestry(tmp_path,monkeypatch,fault,during_read):
    reader,parent,leaf,paths=sample(tmp_path);known={};helper=metadata(reader,tmp_path,known)
    path=paths[0];original=os.pread;armed=[True]
    def mutate():
        if fault=='bytes':path.write_bytes(b'x'*path.stat().st_size)
        elif fault=='replacement':
            other=leaf/'other';other.write_bytes(path.read_bytes());other.chmod(0o600);other.replace(path)
        elif fault=='symlink':path.rename(leaf/'saved');path.symlink_to(leaf/'saved')
        elif fault=='mode':path.chmod(0o640)
        else:
            parent.rename(tmp_path/'old-parent');parent.mkdir(mode=0o700)
            (tmp_path/'old-parent/leaf').rename(leaf)
    def observed(*args):
        raw=original(*args)
        if armed[0]:armed[0]=False;mutate()
        return raw
    try:
        if during_read:monkeypatch.setattr(os,'pread',observed)
        else:mutate()
        with pytest.raises((RuntimeError,OSError)):helper(path,1024)
        assert not known
    finally:reader.close()


@pytest.mark.parametrize('target',['unrelated_file','unrelated_directory'])
def test_history_helper_rechecks_unrelated_tokens_before_bytes(tmp_path,monkeypatch,target):
    reader,parent,leaf,paths=sample(tmp_path);known={};helper=metadata(reader,tmp_path,known)
    fd=reader.held[1][1] if target=='unrelated_file' else reader._nodes[parent][0]
    try:
        os.lseek(fd,0,os.SEEK_SET)
        monkeypatch.setattr(os,'pread',lambda *_a,**_k:pytest.fail('bytes preceded global token refusal'))
        with pytest.raises(RuntimeError,match='rpc_reader_descriptor_changed'):helper(paths[0],1024)
        assert not known
    finally:os.lseek(fd,reader._tokens[fd],os.SEEK_SET);reader.close()


def test_history_helper_rechecks_requested_limit_and_shared_lock(tmp_path,monkeypatch):
    reader,parent,leaf,paths=sample(tmp_path);known={};helper=metadata(reader,tmp_path,known)
    path=paths[0];fd=reader.held[0][1];other=os.open(path,rpc._FLAGS)
    try:
        fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(OSError):helper(path,1024,lock=True)
        assert not known
        fcntl.flock(other,fcntl.LOCK_UN)
        assert helper(path,1024,lock=True)==reader.held[0][3]
        with pytest.raises(OSError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        known.clear()
        with pytest.raises(RuntimeError,match='unsafe_rpc_metadata'):helper(path,1)
        assert not known and os.fstat(fd).st_size>1
    finally:reader.close();os.close(other)
