"""Genuine signed bundle/protected source controls; no native/device action."""
from pathlib import Path
from types import MethodType,SimpleNamespace
import pytest
from tests.test_pilot_bundle import data,tmp_path,protected_source,protected_bundle,facts,clock as original_clock
from lantern_meshticulum.bench import pilot_native_caps as native
from lantern_meshticulum.bench.pilot_bundle import BundleContext

class Clock:
    def __init__(self):self.calls=0;self.action=None
    def __call__(self):
        self.calls+=1
        if self.action:self.action(self.calls)
        return original_clock()

@pytest.fixture
def case(data,tmp_path,protected_source):
    _,_,value,p=data;root=tmp_path/'native'
    p,directory=protected_bundle(root,p,value,'unit-a')
    clock=Clock();caps=native._open_existing(root,p.parent_id,facts('unit-a'),clock)
    ap=caps._approve;raw=b'{"enrollment":'+caps._enrollment_bytes+b',"phase":"current"}'
    clock.calls=0
    try:yield ap,clock,caps,raw
    finally:caps.close()


def test_original_genuine_bundle_is_checked_at_both_boundaries(case,monkeypatch):
    ap,clock,caps,raw=case;events=[];current=BundleContext.current;original=ap.bundle
    def checked(self,now,**kwargs):
        events.append((self,now));return current(self,now,**kwargs)
    monkeypatch.setattr(BundleContext,'current',checked)
    assert ap(raw) is True and clock.calls==2
    assert len(events)==2 and all(item[0] is original for item in events)
    assert len(ap._state_refs)==20 and ap._state_refs[17] is original


@pytest.mark.parametrize('when',[1,2])
@pytest.mark.parametrize('mutation',['clear_both','foreign_both','value','manifest','expected','raw'])
def test_bundle_clock_mutation_cannot_skip_or_replace_original_current(case,when,mutation):
    ap,clock,caps,raw=case;foreign=[]
    def action(index):
        if index!=when:return
        if mutation=='clear_both':ap.bundle=ap._bundle_ref=None
        elif mutation=='foreign_both':ap.bundle=ap._bundle_ref=SimpleNamespace(current=lambda _:foreign.append('called'))
        elif mutation=='value':ap.bundle.value['expires_epoch_us']+=1
        elif mutation=='manifest':ap.bundle.manifest['pack']['title']='changed'
        elif mutation=='expected':ap.bundle.expected['parts'][0]['chunk_sha256']='0'*64
        else:ap.bundle.raw=bytes(bytearray(ap.bundle.raw))
    clock.action=action
    with pytest.raises(Exception):ap(raw)
    clock.action=None
    assert ap._closed and caps.current() is False and not foreign
    with pytest.raises(Exception):ap(raw)


def test_indexed_old_guard_reproduces_double_clear_bypass(case):
    ap,clock,caps,raw=case
    # Exact pre-repair production predicate saved from Root's Git index.
    namespace=dict(require=native.require,os=native.os,threading=native.threading,_unchanged=native._unchanged)
    exec(compile(Path(__file__).with_name('PRE_REPAIR_BOUND.py').read_bytes(),'PRE_REPAIR_BOUND.py','exec'),namespace)
    ap._bound=MethodType(namespace['_bound'],ap)
    refs=ap._captured_refs()[:17];ap._state_refs=ap._state_refs_ref=refs;ap._captured_refs=lambda:refs
    clock.action=lambda _: (setattr(ap,'bundle',None),setattr(ap,'_bundle_ref',None))
    assert ap(raw) is True and ap.bundle is None and clock.calls==2
