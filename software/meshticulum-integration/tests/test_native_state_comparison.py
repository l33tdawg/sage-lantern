"""Comparison acceleration retains every typed field and live callback fence."""
from copy import deepcopy
import pytest
from lantern_meshticulum.bench import pilot_native_caps as native
from tests.test_pilot_native_caps import state,policy


def children(value):
    kind=type(value)
    if kind in (native.ParentPolicy,native.UnitCaps,native.Cost,native.PeerID):
        return [(('attr',f.name),getattr(value,f.name)) for f in native.fields(kind)]
    if kind is dict:return [(('item',k),v) for k,v in value.items()]
    if kind in (tuple,list):return [(('item',i),v) for i,v in enumerate(value)]
    return []


def leaves(value,path=()):
    nested=children(value)
    if not nested:yield path,value
    for key,item in nested:yield from leaves(item,path+(key,))


def changed(value,path,replacement):
    result=deepcopy(value);node=result
    for kind,key in path[:-1]:node=getattr(node,key) if kind=='attr' else node[key]
    kind,key=path[-1]
    if kind=='attr':object.__setattr__(node,key,replacement)
    else:node[key]=replacement
    return result


@pytest.mark.parametrize('which',['policy','enrollment'])
def test_compiled_comparison_matches_reference_and_rejects_each_changed_leaf(state,which):
    caps,_,_,_,_=state;value=caps.policy if which=='policy' else caps._enrollment
    descriptor=native._state(value);pinned=native._state_comparator(descriptor)
    assert native._compare_state(value,pinned[0],pinned) and native._unchanged(value,descriptor)
    assert native._compare_state(deepcopy(value),pinned[0],pinned)
    for path,item in leaves(value):
        replacement=(not item if type(item) is bool else item+1 if type(item) is int
                     else item+'changed' if type(item) is str else 'changed')
        mutant=changed(value,path,replacement)
        assert native._compare_state(mutant,pinned[0],pinned) is False, path
        assert native._unchanged(mutant,descriptor) is False, path


@pytest.mark.parametrize('value',[
    {'a':[1,True,None], 'b':('safe',3)},
    {"__import__('os').system('must never execute')":'literal data'},
])
def test_compiled_structure_rejects_type_key_and_length_changes(value):
    class String(str):pass
    descriptor=native._state(value);pinned=native._state_comparator(descriptor)
    assert native._compare_state(value,pinned[0],pinned)
    key=next(iter(value));mutant=deepcopy(value);content=mutant.pop(key);mutant[String(key)]=content
    assert native._compare_state(mutant,pinned[0],pinned) is False
    mutant=deepcopy(value);mutant['extra']=0
    assert native._compare_state(mutant,pinned[0],pinned) is False
    mutant=deepcopy(value);mutant.pop(key)
    assert native._compare_state(mutant,pinned[0],pinned) is False


@pytest.mark.parametrize('which',['policy','enrollment'])
@pytest.mark.parametrize('fault',['function','code','defaults','metadata'])
def test_live_callback_cannot_replace_comparison_or_make_it_always_accept(state,which,fault):
    caps,observer,_,_,_=state;prefix='_'+which
    def attack(_):
        original=getattr(caps,prefix+'_comparator')
        if fault=='function':setattr(caps,prefix+'_comparator',lambda _:True)
        elif fault=='code':original.__code__=(lambda *_:True).__code__
        elif fault=='defaults':original.__defaults__=tuple(list(original.__defaults__))
        else:setattr(caps,prefix+'_comparison',tuple(list(getattr(caps,prefix+'_comparison'))))
    observer.hook=attack
    assert caps.current() is False

