"""Genuine local held-source/NativeCaps fixture; modeled facts/clock, no RF."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.test_public32_pilot import protected_source,tmp_path,large_pack
from tests import test_public32_pilot as f
from lantern_meshticulum.bench import pilot_native_caps as native,pilot_resume_rpc as rpc

class Clock:
    def __init__(self):self.action=None;self.calls=0;self.value=None
    def __call__(self):
        self.calls+=1
        if self.action is not None:self.action(self.calls)
        return self.value if self.value is not None else f.clock()

@pytest.fixture
def approval_case(tmp_path,large_pack):
    policy,pair=f.pair(large_pack,'MR1.request');root=tmp_path/'native'
    packet,parent,policy=f.enroll(root,policy,pair,'unit-a')
    f.provision(packet,state=root,facts=f.facts('unit-a'),clock=f.clock)
    clock=Clock();caps=native._open_existing(root,policy.parent_id,f.facts('unit-a'),clock)
    approval=caps._approve
    raw=b'{"enrollment":'+caps._enrollment_bytes+b',"phase":"current"}'
    clock.calls=0
    try:yield approval,clock,caps,raw,root/'parents'/policy.parent_id
    finally:caps.close()


def test_each_approval_calls_both_clocks_before_exact_one_full_owner_and_runtime_pass(approval_case,monkeypatch):
    ap,clock,caps,raw,path=approval_case;events=[]
    clock.action=lambda _:events.append('clock')
    reader,runtime=ap.reader.check,ap.runtime.check
    monkeypatch.setattr(ap.reader,'check',lambda:events.append('owner') or reader())
    monkeypatch.setattr(ap.runtime,'check',lambda:events.append('runtime') or runtime())
    assert ap(raw) is True and events==['clock','clock','owner','runtime']
    assert ap(raw) is True and events==['clock','clock','owner','runtime']*2


@pytest.mark.parametrize('when',[1,2])
@pytest.mark.parametrize('mutation',['same_inode_bytes','same_bytes_inode','mode','runtime_source'])
def test_files_mutated_at_either_clock_are_refused_and_latched(approval_case,when,mutation):
    ap,clock,caps,raw,path=approval_case
    def action(index):
        if index!=when:return
        target=path/'owner.json'
        if mutation=='same_inode_bytes':target.write_bytes(target.read_bytes()+b' ')
        elif mutation=='same_bytes_inode':
            data=target.read_bytes();target.unlink();target.write_bytes(data);target.chmod(0o600)
        elif mutation=='mode':target.chmod(0o644)
        else:
            target=ap.runtime.root/'lantern_meshticulum/bench/pilot_resume_rpc.py'
            target.write_bytes(target.read_bytes()+b'\n')
    clock.action=action
    with pytest.raises(RuntimeError):ap(raw)
    clock.action=None
    assert ap._closed and caps.current() is False
    with pytest.raises(RuntimeError):ap(raw)


@pytest.mark.parametrize('mutation',['policy','facts','reader','runtime','clock','bounds','last'])
def test_binding_parser_state_mutation_refuses_before_verification_and_stays_failed(approval_case,monkeypatch,mutation):
    ap,clock,caps,raw,path=approval_case;parse=rpc.parse;foreign=[]
    def changing_parse(data):
        result=parse(data)
        if mutation=='policy':ap.policy=replace(ap.policy,expires_epoch_us=ap.policy.expires_epoch_us+1)
        elif mutation=='facts':ap.facts['host_boot_id']='changed'
        elif mutation=='reader':ap.reader=SimpleNamespace(close=lambda:foreign.append('reader'))
        elif mutation=='runtime':ap.runtime=SimpleNamespace(close=lambda:foreign.append('runtime'))
        elif mutation=='clock':ap.clock=lambda:f.clock()
        elif mutation=='bounds':ap._phase_bounds=tuple(x+1 for x in ap._phase_bounds)
        elif mutation=='last':ap._last=tuple(x+1 for x in ap._last)
        return result
    monkeypatch.setattr(rpc,'parse',changing_parse)
    with pytest.raises(RuntimeError):ap(raw)
    assert ap._closed and not foreign
    assert ap._state_refs_ref[4]._closed and ap._state_refs_ref[5].reader._closed
    with pytest.raises(RuntimeError):ap(raw)


@pytest.mark.parametrize('when',[1,2])
def test_original_deadline_at_either_clock_refuses_without_revival(approval_case,when):
    ap,clock,caps,raw,path=approval_case
    first_clock=ap.clock
    # Keep the captured callback object; change its source-return behavior only.
    original=Clock.__call__
    def selected(self):
        value=original(self)
        return (ap._end,ap._phase_bounds[2]) if self is first_clock and self.calls==when else value
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Clock,'__call__',selected)
        with pytest.raises(RuntimeError):ap(raw)
    assert ap._closed and caps.current() is False


def test_bad_binding_phase_keeps_caps_spent_and_closes_approval(approval_case):
    ap,clock,caps,raw,path=approval_case
    bad=json.loads(raw);bad['phase']='refund'
    with pytest.raises(RuntimeError):ap(native.canonical(bad))
    assert ap._closed and caps.current() is False


def test_direct_check_also_checks_both_clock_boundaries_and_all_sources(approval_case):
    ap,clock,caps,raw,path=approval_case
    assert ap.check() is True and clock.calls==2


@pytest.mark.parametrize('mutation',['clear','truncate','rebind','reader_ref','runtime_ref','owner_ref','phase_refs','policy_state','facts_state'])
def test_comparison_or_cleanup_metadata_cannot_replace_captured_original_owners(approval_case,mutation):
    ap,clock,caps,raw,path=approval_case;foreign=[];reader,runtime=ap.reader,ap.runtime
    def changing(index):
        if index!=1:return
        if mutation=='clear':ap._state_refs=()
        elif mutation=='truncate':ap._state_refs=ap._state_refs[:4]
        elif mutation=='rebind':ap._state_refs_ref=tuple(list(ap._state_refs))
        elif mutation=='reader_ref':ap._reader_ref=SimpleNamespace(close=lambda:foreign.append('reader'))
        elif mutation=='runtime_ref':ap._runtime_ref=SimpleNamespace(close=lambda:foreign.append('runtime'))
        elif mutation=='owner_ref':ap._owner_ref=(0,0)
        elif mutation=='phase_refs':ap._phase_bounds=ap._bounds_ref=tuple(v+1 for v in ap._phase_bounds)
        elif mutation=='policy_state':ap._policy_state=native._state(replace(ap.policy,expires_epoch_us=ap.policy.expires_epoch_us+1))
        else:ap._facts_state=native._state({})
    clock.action=changing
    with pytest.raises(RuntimeError):ap(raw)
    assert ap._closed and reader._closed and runtime.reader._closed and not foreign
    clock.action=None
    with pytest.raises(RuntimeError):ap(raw)


def test_outer_native_current_rechecks_original_deadline_after_full_source_verification(approval_case,monkeypatch):
    ap,clock,caps,raw,path=approval_case;check=ap.runtime.check
    def elapsed():
        result=check();clock.value=(ap._end,ap._phase_bounds[2]);return result
    monkeypatch.setattr(ap.runtime,'check',elapsed)
    assert caps.current() is False and caps._uncertain
    # Restoring a modeled clock cannot revive the failed NativeCaps operation.
    clock.value=None
    assert caps.current() is False


def test_original_owner_reader_closes_even_if_runtime_cleanup_raises(approval_case,monkeypatch):
    ap,clock,caps,raw,path=approval_case;runtime=ap.runtime;reader=ap.reader;close=runtime.close
    def raising():close();raise RuntimeError('modeled_owned_runtime_cleanup_refusal')
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'close',raising)
        with pytest.raises(RuntimeError):ap.close()
    assert ap._closed and reader._closed and runtime.reader._closed


@pytest.mark.parametrize('target_kind',['owner','runtime'])
def test_binding_parser_filesystem_mutation_is_verified_after_parser_returns(approval_case,monkeypatch,target_kind):
    ap,clock,caps,raw,path=approval_case;parse=rpc.parse
    target=(path/'owner.json') if target_kind=='owner' else (ap.runtime.root/'lantern_meshticulum/bench/pilot_resume_rpc.py')
    def changing(data):
        value=parse(data);target.write_bytes(target.read_bytes()+b' ');return value
    monkeypatch.setattr(rpc,'parse',changing)
    with pytest.raises(RuntimeError):ap(raw)
    assert ap._closed and caps.current() is False
