"""Typed observational decoder controls; no new grant or RF authority."""
from copy import deepcopy
from dataclasses import asdict,replace

import pytest
from meshticulum.bench.public_block import PUBLIC32_MARKER,REPRESENTATION_MARKER,TRANSPORT_DESCRIPTOR_MARKER
from lantern_meshticulum.bench import pilot_debt_snapshot as module
from lantern_meshticulum.bench.profiles import profile_for,profile_record,radio_route
from tests.test_pilot_debt_snapshot import fixture

@pytest.fixture
def plan(tmp_path):
    state,directory,p,scope=fixture(tmp_path,'lora')
    p['profile']=profile_record(profile_for('gfsk125'));p['radio_modem']='gfsk125'
    p['service'].update({PUBLIC32_MARKER:32768,REPRESENTATION_MARKER:'MB1',TRANSPORT_DESCRIPTOR_MARKER:'d'*64})
    # Scope context is omitted only in this explicit pure decoder control.
    run=replace(scope.runs[0],grant_context_sha256=None)
    return p,scope,run

@pytest.mark.parametrize('fmt,size',[('MR1.request',74),('MR1.response',24566),('MR1.response',1),('MR1.response',32768)])
def test_explicit_typed_MB1_keeps_exact_wire_length_and_descriptor(plan,fmt,size):
    p,scope,run=plan;p['service']['format']=fmt;p['object']['bytes']=size
    if fmt=='MR1.response':p['sender_unit']='unit-c';p['local_peer']=p['responder']
    grant=module._public_grant(p,scope,run)
    assert grant['format']==fmt and grant['public_resource_representation']=='MB1'
    assert grant['public_transport_descriptor_sha256']=='d'*64 and grant['public_response_max_bytes']==32768
    assert grant['caps']['radio_attempts']==256

@pytest.mark.parametrize('fault',['no_representation','no_descriptor','no_public32','bad_descriptor','wrong_representation','extra','oversize','legacy_variable'])
def test_MB1_downgrade_or_unknown_marker_refuses(plan,fault):
    p,scope,run=plan;p['service']['format']='MR1.response';p['object']['bytes']=24566;p['sender_unit']='unit-c';p['local_peer']=p['responder']
    if fault=='no_representation':p['service'].pop(REPRESENTATION_MARKER)
    elif fault=='no_descriptor':p['service'].pop(TRANSPORT_DESCRIPTOR_MARKER)
    elif fault=='no_public32':p['service'].pop(PUBLIC32_MARKER)
    elif fault=='bad_descriptor':p['service'][TRANSPORT_DESCRIPTOR_MARKER]='bad'
    elif fault=='wrong_representation':p['service'][REPRESENTATION_MARKER]='JSON'
    elif fault=='extra':p['service']['private']=True
    elif fault=='oversize':p['object']['bytes']=32769
    else:p['service'].pop(REPRESENTATION_MARKER);p['service'].pop(TRANSPORT_DESCRIPTOR_MARKER)
    with pytest.raises(Exception):module._public_grant(p,scope,run)


def test_legacy_exact32_JSON_keeps_original_output_shape(plan):
    p,scope,run=plan;p['service'].pop(REPRESENTATION_MARKER);p['service'].pop(TRANSPORT_DESCRIPTOR_MARKER)
    p['service']['format']='MR1.response';p['object']['bytes']=32768;p['sender_unit']='unit-c';p['local_peer']=p['responder']
    grant=module._public_grant(p,scope,run)
    assert 'public_resource_representation' not in grant and 'public_transport_descriptor_sha256' not in grant


def test_authenticated_MB1_route_context_refuses_later_descriptor_change(plan):
    p,scope,run=plan;accepted=module._public_grant(p,scope,run)
    run=replace(run,grant_context_sha256=accepted['context_sha256']);p['service'][TRANSPORT_DESCRIPTOR_MARKER]='e'*64
    with pytest.raises(module.SnapshotError,match='grant_context_changed'):module._public_grant(p,scope,run)
