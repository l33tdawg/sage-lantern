"""Genuine batch/SQLite checks and typed-state mutation controls; no RF."""
import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import gfsk_batch as batch,pilot_native_caps as native_caps
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests.test_gfsk125_pilot import (native,root_observation,synthetic_deployment_bindings,value,pair,qualified)
from tests.test_native_state_comparison import leaves,changed

@pytest.mark.parametrize('kind',['descriptor','policy'])
def test_each_live_leaf_still_matches_canonical_validation(kind):
    context,_,_=value()
    obj=context.descriptor if kind=='descriptor' else context.policy
    baseline=canonical(obj if kind=='descriptor' else obj.to_dict())
    comparison=getattr(context,'_'+kind+'_comparison')
    assert native_caps._compare_state(obj,comparison[0],comparison)
    for path,item in leaves(obj):
        replacement=(not item if type(item) is bool else item+1 if type(item) is int else item+'changed' if type(item) is str else 'changed')
        mutant=changed(obj,path,replacement)
        original=canonical(mutant if kind=='descriptor' else mutant.to_dict())==baseline
        assert native_caps._compare_state(mutant,comparison[0],comparison) is original,path

@pytest.mark.parametrize('kind',['descriptor','policy'])
@pytest.mark.parametrize('attack',['function','code','defaults','tuple'])
def test_context_comparator_metadata_cannot_be_replaced(kind,attack):
    context,_,_=value();field='_'+kind+'_comparison';comparison=getattr(context,field)
    if attack=='function':
        fake=lambda _:True
        setattr(context,field,(fake,fake.__code__,fake.__defaults__))
    elif attack=='code':comparison[0].__code__=(lambda *_:True).__code__
    elif attack=='defaults':comparison[0].__defaults__=tuple(list(comparison[0].__defaults__))
    else:setattr(context,field,tuple(list(comparison)))
    with pytest.raises((WireError,native_caps.NativeCapsError)):context.check()

@pytest.mark.parametrize('kind',['policy','facts'])
@pytest.mark.parametrize('attack',['function','code','defaults','tuple'])
def test_approval_comparator_mutation_refuses_genuine_native_current(native,kind,attack):
    state,parent,context,payloads,now,clock,host,ctrl=native
    package=qualified(ctrl,pair(context,payloads[0][0],0,now))['unit-a']
    caps=native_caps.open_for_provision(state,host,clock,package)
    try:
        approval=caps._approve;field='_'+kind+'_comparison';comparison=getattr(approval,field)
        if attack=='function':
            fake=lambda _:True;setattr(approval,field,(fake,fake.__code__,fake.__defaults__))
        elif attack=='code':comparison[0].__code__=(lambda *_:True).__code__
        elif attack=='defaults':comparison[0].__defaults__=tuple(list(comparison[0].__defaults__))
        else:setattr(approval,field,tuple(list(comparison)))
        assert caps.current() is False
        assert caps._uncertain is True
        assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone()==(0,)
    finally:caps.close()

@pytest.mark.parametrize('attack',['file','facts','descriptor','expiry','reverse_clock'])
def test_second_validation_rejects_clock_callback_mutation(native,attack):
    state,parent,context,payloads,now,clock,host,ctrl=native
    armed=[False];calls=[0]
    def current_clock():
        calls[0]+=1
        if armed[0]:
            armed[0]=False
            if attack=='file':
                p=parent/'capture.json';raw=p.read_bytes();p.write_bytes(raw.replace(b'"unit-a"',b'"unit-c"',1))
            elif attack=='facts':host['services'][next(iter(host['services']))]='changed'
            elif attack=='descriptor':approval.context.descriptor['samples'][0]['request']['bytes']+=1
            elif attack=='expiry':now[:]=[context.phase_end_epoch_us,context.policy.deadline_monotonic_us]
            else:now[0]-=1
        return tuple(now)
    approval=batch.BatchApproval(parent,context.policy,'unit-a',host,current_clock)
    caps=native_caps.NativeCaps.open(parent/'native.sqlite',context.policy,unit='unit-a',
        clock=clock,facts=lambda:native_caps.NativeFacts('unit-a',host['host_boot_id'],context.policy.unit('unit-a').peer),approve=lambda _:True)
    try:
        raw=canonical(dict(enrollment=caps._enrollment,phase='current'))
        armed[0]=True
        with pytest.raises((WireError,native_caps.NativeCapsError,RuntimeError)):approval(raw)
        assert calls[0]>=2
        assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone()==(0,)
    finally:caps.close();approval.close()

def test_full_content_reader_passes_are_preserved_and_serialization_removed(native,monkeypatch):
    state,parent,context,payloads,now,clock,host,ctrl=native
    package=qualified(ctrl,pair(context,payloads[0][0],0,now))['unit-a']
    caps=native_caps.open_for_provision(state,host,clock,package)
    try:
        approval=caps._approve;reads=[0];calls=[]
        original_check=approval.reader.check;original_canonical=batch.canonical
        def checked():reads[0]+=1;return original_check()
        def encoded(v):calls.append(v);return original_canonical(v)
        monkeypatch.setattr(approval.reader,'check',checked)
        monkeypatch.setattr(batch,'canonical',encoded)
        assert caps.current() is True
        assert reads[0]==2
        # Only the independently parsed enrollment.policy remains encoded.
        assert len(calls)==1 and type(calls[0]) is dict and 'parent_id' in calls[0]
    finally:assert caps.close()

def test_revocation_after_a_paid_claim_refuses_without_refunding(native):
    state,parent,context,payloads,now,clock,host,ctrl=native
    package=qualified(ctrl,pair(context,payloads[0][0],0,now))['unit-a']
    caps=native_caps.open_for_provision(state,host,clock,package)
    try:
        caps.prepare_claim(package);before=caps.snapshot()
        (parent/'owner.json').unlink()
        assert caps.current() is False
        after=caps.snapshot()
        assert before['claims']==after['claims']==1
        assert before['debt']==after['debt']
        assert after['uncertain'] is True
    finally:caps.close()
