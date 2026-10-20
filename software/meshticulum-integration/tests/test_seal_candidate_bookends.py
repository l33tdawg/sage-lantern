# SPDX-License-Identifier: Apache-2.0
"""Real owned files and descriptors; no remote state or native permission."""
import fcntl
import os

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from tests.test_seal_inputs_held_read import seal_fixture, counts


@pytest.mark.parametrize('fault',[
    'bytes','replacement','parent','offset','cloexec','nonblock',
    'unrequested-bytes','unrequested-offset','unrequested-cloexec',
])
def test_iteration_drift_refuses_before_candidates_escape(seal_fixture,monkeypatch,fault):
    state,reader,path,raw=seal_fixture
    extra=state/'unrequested.json';extra.write_bytes(b'other held public bytes');extra.chmod(0o600)
    reader.read(extra,1024)
    target=extra if fault.startswith('unrequested-') else path
    fd=reader._file_index[target][1]
    token=reader._tokens[fd]
    flags=fcntl.fcntl(fd,fcntl.F_GETFL);fdflags=fcntl.fcntl(fd,fcntl.F_GETFD)
    known={str(path.relative_to(state)):rpc.sha(raw)};sha=rpc.sha;changed=[]
    def during_iteration(value):
        result=sha(value)
        if not changed:
            changed.append(True)
            if fault.endswith('bytes'):target.write_bytes(b'x'*len(target.read_bytes()))
            elif fault=='replacement':
                replacement=target.parent/'replacement';replacement.write_bytes(raw)
                replacement.chmod(0o600);replacement.replace(target)
            elif fault=='parent':
                target.parent.rename(state/'replaced-parent');target.parent.mkdir(mode=0o700)
            elif fault.endswith('offset'):os.lseek(fd,0,os.SEEK_SET)
            elif fault.endswith('cloexec'):fcntl.fcntl(fd,fcntl.F_SETFD,fdflags&~fcntl.FD_CLOEXEC)
            else:fcntl.fcntl(fd,fcntl.F_SETFL,flags&~os.O_NONBLOCK)
        return result
    monkeypatch.setattr(rpc,'sha',during_iteration)
    try:
        with pytest.raises((RuntimeError,OSError)):
            rpc.seal_inputs(reader,'unit-b',known,{}, {})
        assert changed==[True]
    finally:
        os.lseek(fd,token,os.SEEK_SET)
        fcntl.fcntl(fd,fcntl.F_SETFL,flags);fcntl.fcntl(fd,fcntl.F_SETFD,fdflags)


def test_unheld_path_uses_ordinary_fresh_read_fallback(seal_fixture):
    state,reader,path,raw=seal_fixture
    new=path.parent/'prior.json';new.write_bytes(b'new public metadata');new.chmod(0o600)
    known={str(path.relative_to(state)):rpc.sha(raw),str(new.relative_to(state)):rpc.sha(new.read_bytes())}
    (files,_),observed=counts(lambda:rpc.seal_inputs(reader,'unit-b',known,{},{}))
    assert files==known
    assert observed['read_held_calls']==observed['read_calls']==1
    assert len(reader.held)==2


def test_candidate_maximum_is_not_replaced_by_original_read_bound(seal_fixture):
    _,reader,path,raw=seal_fixture
    with pytest.raises(RuntimeError,match='unsafe_rpc_metadata'):
        reader.seal_candidate_bytes(path,len(raw)-1)


def test_batch_keeps_full_fresh_bookends_and_removes_per_file_global_sweeps(seal_fixture,record_property):
    state,reader,path,raw=seal_fixture
    paths=[path]
    for index in range(15):
        parent=state/'parents'/rpc.HISTORY[index+1]
        extra=parent/'policy.json';extra.write_bytes(raw);extra.chmod(0o600)
        reader.read(extra,1024);paths.append(extra)
    known={str(p.relative_to(state)):rpc.sha(raw) for p in paths}
    # Ordinary APIs stay the reference: each retained read has two ancestry
    # sweeps and one fresh pread, followed by the original full final check.
    def ordinary():
        result={str(p.relative_to(state)):rpc.sha(reader.read_held(p,rpc.MAX_FILE)) for p in paths}
        reader.check();return result
    expected,ordinary_counts=counts(ordinary)
    (actual,_),batch_counts=counts(lambda:rpc.seal_inputs(reader,'unit-b',known,{},{}))
    # Account for unchanged directory() sweeps separately from byte candidates.
    directory_sweeps=batch_counts['directory_calls']
    assert expected==actual==known
    assert ordinary_counts['fresh_preads']==2*len(paths)
    assert ordinary_counts['ancestry_sweeps']==2*len(paths)+2
    assert batch_counts['fresh_preads']==2*len(paths)
    assert batch_counts['ancestry_sweeps']-directory_sweeps==4
    assert batch_counts['seal_candidate_calls']==2*len(paths)
    assert batch_counts.get('read_held_calls',0)==batch_counts.get('read_calls',0)==0
    record_property('held_files',len(paths))
    record_property('ordinary_fresh_preads',ordinary_counts['fresh_preads'])
    record_property('batch_fresh_preads',batch_counts['fresh_preads'])
    record_property('ordinary_ancestry_sweeps',ordinary_counts['ancestry_sweeps'])
    record_property('batch_non_directory_ancestry_sweeps',batch_counts['ancestry_sweeps']-directory_sweeps)
