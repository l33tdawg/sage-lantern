"""Held-file freshness and cleanup at the comparison permission boundary."""
import os
import pytest
from lantern_meshticulum.bench.pilot_native_caps import open_for_provision
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests.test_lora_comparison import native, source_scope, qualified, pair, root_observation

def open_current(native):
    state,parent,c,payloads,now,clock,host,ctrl=native
    q=qualified(ctrl,pair(c,payloads[0][0],0,now))[host['unit']]
    caps=open_for_provision(state,host,clock,q)
    return caps,parent,now,canonical(dict(enrollment=caps._enrollment,phase='current'))

def test_every_permission_return_has_one_fresh_owner_and_runtime_read(native,monkeypatch):
    caps,parent,now,raw=open_current(native)
    count={'owner':0,'runtime':0}
    try:
        approval=caps._approve
        for label,obj in [('owner',approval.reader),('runtime',approval.runtime)]:
            original=obj.check
            def wrapped(original=original,label=label):
                count[label]+=1
                return original()
            monkeypatch.setattr(obj,'check',wrapped)
        assert approval(raw) is True
        assert count=={'owner':1,'runtime':1}
        assert approval(raw) is True
        assert count=={'owner':2,'runtime':2}
    finally:caps.close()

def test_file_mutation_during_pure_parse_fails_before_permission(native,monkeypatch):
    from lantern_meshticulum.bench import pilot_resume_rpc as rpc
    caps,parent,now,raw=open_current(native)
    approval=caps._approve;original=rpc.parse
    changed=[]
    def parse_then_mutate(value):
        decoded=original(value)
        if value==raw and not changed:
            path=parent/'owner.json';fd=os.open(path,os.O_WRONLY)
            try:os.pwrite(fd,b'[',0);os.fsync(fd)
            finally:os.close(fd)
            changed.append(True)
        return decoded
    monkeypatch.setattr(rpc,'parse',parse_then_mutate)
    try:
        with pytest.raises(RuntimeError,match='held_rpc_metadata_changed'):approval(raw)
        assert changed and approval._closed is True
    finally:caps.close()

def test_expiry_during_pure_parse_fails_before_permission(native,monkeypatch):
    from lantern_meshticulum.bench import pilot_resume_rpc as rpc
    caps,parent,now,raw=open_current(native)
    approval=caps._approve;original=rpc.parse;changed=[]
    def parse_then_expire(value):
        decoded=original(value)
        if value==raw and not changed:
            delta=approval._end-now[0];now[0]+=delta;now[1]+=delta;changed.append(True)
        return decoded
    monkeypatch.setattr(rpc,'parse',parse_then_expire)
    try:
        with pytest.raises(ValueError):approval(raw)
        assert changed and approval._closed is True
    finally:caps.close()


@pytest.mark.parametrize('damage',['context','clock','runtime'])
def test_entry_binding_refusal_closes_original_held_handles(native,damage):
    from copy import deepcopy
    import errno
    caps,parent,now,raw=open_current(native)
    approval=caps._approve
    reader,runtime=approval.reader,approval.runtime
    descriptors=tuple(reader._tokens)
    assert descriptors and all(os.fstat(fd) for fd in descriptors)
    if damage=='context':approval.context=deepcopy(approval.context)
    elif damage=='clock':approval.clock=lambda:tuple(now)
    else:approval.runtime=object()
    try:
        with pytest.raises(ValueError,match='comparison_approval_rebound'):
            approval(raw)
        assert reader._closed is True and runtime.closed is True
        for fd in descriptors:
            with pytest.raises(OSError) as error:os.fstat(fd)
            assert error.value.errno==errno.EBADF
    finally:caps.close()
