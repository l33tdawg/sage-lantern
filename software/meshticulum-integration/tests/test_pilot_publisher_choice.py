"""Real controller ledger; native outcomes are explicit local models."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import pytest
from meshticulum.bench.wire import WireError
from tests.test_pilot_controller import controller,REQUEST,RESPONSE,release_model_parents


def changed(pilot):
    return pilot


def timed(op):
    collect,verify=op.collect,op.verify
    def collected(unit,run):return dict(collect(unit,run),start_monotonic_us=10000000)
    def verified(unit,campaign,request,response):
        return dict(verify(unit,campaign,request,response),verified_monotonic_us=11000000,useful_verified_bytes=1000,reused=False)
    op.collect,op.verify=collected,verified


@pytest.mark.parametrize('publisher',[None,'unit-a'])
def test_explicit_A_and_original_default_B_keep_pinned_native_source_and_C_verification(publisher):
    pilot,op,_=controller();changed(pilot);timed(op)
    kwargs={} if publisher is None else {'publisher':publisher}
    verified=pilot.direct_piece(REQUEST,RESPONSE,'lora',**kwargs)
    source='unit-b' if publisher is None else publisher
    assert [(x['sender'],x['receiver']) for x in pilot.links]==[('unit-c',source),(source,'unit-c')]
    packets=[e[2] for e in op.events if e[0]=='prepare']
    assert {p['unit'] for p in packets}=={'unit-c',source}
    reply=next(p for p in packets if p['unit']==source and p['grant']['service']['format']=='MR1.response')
    assert reply['payload_base64'] is None
    assert reply['source_run_id']==dict(kind='served_public_chunk',request_run_id=pilot.links[0]['run_id'])
    assert all(p['application_expires_epoch_us']==pilot.expires_epoch_us for p in packets)
    assert verified['publisher_verified'] is True and verified['unit']=='unit-c' and verified['reused'] is False
    assert pilot.parent.snapshot()['allocations']==2
    assert not any(e[0]=='prepare' and e[1]=='unit-b' for e in op.events) if source=='unit-a' else True


@pytest.mark.parametrize('publisher',['unit-c','foreign',True,None])
def test_unqualified_explicit_publisher_never_pays_or_dispatches(publisher):
    pilot,op,_=controller();changed(pilot)
    with pytest.raises(WireError):pilot.direct_piece(REQUEST,RESPONSE,'lora',publisher=publisher)
    assert not pilot.links and not op.events and pilot.parent.snapshot()['allocations']==0


def test_publisher_missing_from_actual_parent_scope_refuses_before_preparation():
    pilot,op,_=controller();changed(pilot)
    pilot.parent.policy=replace(pilot.parent.policy,units=tuple(u for u in pilot.parent.policy.units if u.unit!='unit-a'))
    with pytest.raises(WireError):pilot.direct_piece(REQUEST,RESPONSE,'lora',publisher='unit-a')
    assert not pilot.links and not op.events


def test_wrong_verified_piece_does_not_become_A_publisher_success():
    pilot,op,_=controller('wrong_verified_chunk');changed(pilot);timed(op)
    with pytest.raises(WireError):pilot.direct_piece(REQUEST,RESPONSE,'lora',publisher='unit-a')
    assert not pilot.verified
