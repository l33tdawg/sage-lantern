# SPDX-License-Identifier: Apache-2.0
"""Process soft-limit controls only; modeled limits, no kernel policy change."""
import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc


@pytest.fixture
def limits(monkeypatch):
    current=[1024,4096];calls=[]
    monkeypatch.setattr(rpc.os,'listdir',lambda path:list(range(900)))
    monkeypatch.setattr(rpc.resource,'getrlimit',lambda kind:tuple(current))
    def set_limit(kind,value):calls.append(value);current[:]=value
    monkeypatch.setattr(rpc.resource,'setrlimit',set_limit)
    return current,calls


def test_actual_retained_descriptor_count_and_source_bounds_raise_soft_only(limits):
    current,calls=limits
    result=rpc.reserve_descriptor_capacity({str(i):'pin' for i in range(100)},
        {str(i):[] for i in range(20)},[{}]*63)
    expected=900+100+20+63*(3+len(rpc.OWNERS))+128+16+21
    assert calls==[(expected,4096)] and current==[expected,4096]
    assert result['required_soft_limit']==expected and result['changed'] is True


@pytest.mark.parametrize('soft',[4096,rpc.resource.RLIM_INFINITY])
def test_existing_capacity_is_never_lowered_or_reset(limits,soft):
    current,calls=limits;current[0]=soft
    assert rpc.reserve_descriptor_capacity({}, {}, [])['changed'] is False
    assert not calls and current[0]==soft


def test_insufficient_hard_limit_refuses_without_mutation(limits):
    current,calls=limits;current[:]=[1024,1024]
    with pytest.raises(RuntimeError,match='hard_limit_insufficient'):
        rpc.reserve_descriptor_capacity({}, {}, [])
    assert current==[1024,1024] and not calls


@pytest.mark.parametrize('field',['files','directories','legacy'])
def test_oversized_scope_refuses_before_limit_change(limits,field):
    current,calls=limits
    values=[{}, {}, []]
    if field=='files':values[0]={str(i):'pin' for i in range(rpc.MAX_FILES+1)}
    elif field=='directories':values[1]={str(i):[] for i in range(rpc.MAX_DIRS+1)}
    else:values[2]=[{}]*(rpc.MAX_RUNS+1)
    with pytest.raises(RuntimeError,match='source_bounds'):rpc.reserve_descriptor_capacity(*values)
    assert current==[1024,4096] and not calls


def test_unapplied_limit_change_is_not_reported_as_capacity(limits,monkeypatch):
    current,calls=limits
    monkeypatch.setattr(rpc.resource,'setrlimit',lambda kind,value:None)
    with pytest.raises(RuntimeError,match='not_established'):
        rpc.reserve_descriptor_capacity({}, {}, [])
    assert current==[1024,4096]
