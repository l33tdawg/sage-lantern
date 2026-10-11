# SPDX-License-Identifier: Apache-2.0
"""Signed SDK/record controls; native resource and REST backend explicitly modeled.

No network/socket/daemon is opened. The existing SDK MockTransport boundary is
used because this source-only task forbids network; these are not real encrypted
backend or native enrollment qualification results.
"""
from contextlib import contextmanager
import base64
import copy
import uuid

import pytest
from lantern_meshticulum.sage.message_contacts import RecipientID
from lantern_meshticulum.sage.message_storage import MessageStorageProbe
from lantern_meshticulum.sage.workflow_store import WorkflowStore
from lantern_meshticulum.sage.noise_purpose import NoiseBinding,NoisePublic,PURPOSE,ALGORITHM
from lantern_meshticulum.sage.noise_native_enrollment import NativeNoiseEnrollment,NativeNoiseEnrollmentError,SCHEMA,SIGNATURE_DOMAIN,KIND
from lantern_meshticulum.sage.mesh_inbox import _canonical
from meshticulum.privacy.forward_session import public
from tests.integrations.sage.test_forward_native_owner import setup
from lantern_meshticulum.sage.forward_native_owner import SCOPES


@contextmanager
def fixture(scopes=SCOPES):
    with setup(scopes=scopes) as r:
        local=r.context.initiator;remote=r.context.responder
        info=NoisePublic(NoiseBinding(local,remote,True,str(uuid.uuid4()),r.intention,
            r.context.issued_epoch_us,'c'*64),public(r.noise[0]))
        probe=MessageStorageProbe(r.client,actor=RecipientID(local.chain_id,local.public_key.hex()),endpoint=r.hardware.probe.endpoint)
        store=WorkflowStore(r.client,storage=probe)
        provider=NativeNoiseEnrollment(r.hardware,r.authority,r.native_context,store,
            context=r.context,intention=r.intention,local_initiator=True,local_public=info,
            remote_generation_id=str(uuid.uuid4()),boot_reader=lambda:r.state.boot,clock_reader=lambda:r.state.now)
        def record(revision=1,state='active',change=None,sign=True):
            binding=__import__('json').loads(provider._binding)
            body=dict(record_id=provider.record_id,revision=revision,purpose=PURPOSE,algorithm=ALGORITHM,binding=binding,state=state)
            if change:change(body)
            signature=r.hardware.identity._signing_key.sign(SIGNATURE_DOMAIN+_canonical(body)).signature
            value=dict(schema=SCHEMA,body=body,signature=base64.b64encode(signature if sign else b'x'*64).decode())
            row=dict(schema='sage.workflow-journal.v1',agent_id=local.public_key.hex(),record_id=provider.record_id,
                     revision=revision,kind=KIND,payload=value,trust='untrusted_auxiliary')
            r.state.rows[provider.record_id]=row;return copy.deepcopy(row)
        try:yield r,provider,info,record
        finally:provider.close()


def test_actual_signed_store_gets_verify_pins_without_creating_record_or_second_lease():
    with fixture() as (r,p,info,record):
        saved=record();original=r.hardware.capture_lease
        r.hardware.capture_lease=lambda *args:pytest.fail('validator must never acquire a lease')
        assert info.binding.approval_digest!=p.exchange_approval  # Setup/exchange are distinct.
        assert p.enrollment_check(info,r.context.digest) is True
        with r.hardware._mutex:
            assert p.key_check(info.public_key,r.context.digest) is True
            assert p.peer_key_check(r.context.responder,public(r.noise[1]),r.context.digest) is True
        assert p.key_check(info.public_key,r.context.digest) is True
        assert r.state.rows=={p.record_id:saved} and r.client._client.is_closed is False
        r.hardware.capture_lease=original


@pytest.mark.parametrize('attack',['missing','bad_signature','revoked','remote_pin','local_pin','generation','context',
                                 'setup_approval','exchange_approval','deadline','storage_epoch','schema'])
def test_signed_record_and_exact_root_binding_refuse_without_registration(attack):
    with fixture() as (r,p,info,record):
        def change(body):
            binding=body['binding']
            if attack=='remote_pin':binding['remote_public_key']='f'*64
            elif attack=='local_pin':binding['local_public']['public_key']='f'*64
            elif attack=='generation':binding['local_public']['binding']['generation_id']=str(uuid.uuid4())
            elif attack=='context':binding['context_digest']='f'*64
            elif attack=='setup_approval':binding['local_public']['binding']['approval_digest']='f'*64
            elif attack=='exchange_approval':binding['exchange_approval_digest']='f'*64
            elif attack=='deadline':binding['local_public']['binding']['intention']['application_expires_epoch_us']+=1
            elif attack=='storage_epoch':binding['storage_epoch']='foreign'
        if attack!='missing':record(state='revoked' if attack=='revoked' else 'active',change=change,sign=attack!='bad_signature')
        if attack=='schema':r.state.rows[p.record_id]['payload']['schema']='foreign'
        before=copy.deepcopy(r.state.rows)
        with pytest.raises(NativeNoiseEnrollmentError):p.key_check(info.public_key,r.context.digest)
        assert r.state.rows==before and p._uncertain is True


@pytest.mark.parametrize('attack',['root_access','consent','boot','storage','config','expired','clock_replaced',
                                 'key_info_replaced','config_baseline_replaced','peer_replaced'])
def test_current_native_permission_and_pinned_provider_inputs_are_mandatory(attack):
    with fixture() as (r,p,info,record):
        record();assert p.key_check(info.public_key,r.context.digest)
        if attack=='root_access':r.state.owner_live=False
        elif attack=='consent':r.hardware.config['dispatch_enabled']=False
        elif attack=='boot':r.state.boot=str(uuid.uuid4())
        elif attack=='storage':r.state.instance_id='f'*64
        elif attack=='config':r.hardware.config['peers']=[]
        elif attack=='expired':r.state.now=(r.context.expires_epoch_us,r.state.now[1])
        elif attack=='clock_replaced':p._clock=lambda:r.state.now
        elif attack=='key_info_replaced':p.local_public=copy.copy(info)
        elif attack=='config_baseline_replaced':p.config_revision='f'*64
        else:p.remote=r.context.initiator
        with pytest.raises(NativeNoiseEnrollmentError):p.key_check(info.public_key,r.context.digest)


@pytest.mark.parametrize('missing',SCOPES)
def test_every_root_forward_scope_is_mandatory_before_record_dispatch(missing):
    with pytest.raises(NativeNoiseEnrollmentError):
        with fixture(tuple(scope for scope in SCOPES if scope!=missing)):pass


@pytest.mark.parametrize('attack',['close','reentry','clock_swap','permission_revoked'])
def test_signed_get_callback_cannot_publish_after_closure_reentry_or_revocation(attack):
    with fixture() as (r,p,info,record):
        record();original=r.client.workflow_get
        def get(**args):
            if attack=='close':p.close()
            elif attack=='reentry':
                with pytest.raises(NativeNoiseEnrollmentError):p.key_check(info.public_key,r.context.digest)
            elif attack=='clock_swap':p._clock=lambda:r.state.now
            else:r.state.owner_live=False
            return original(**args)
        r.client.workflow_get=get
        with pytest.raises(NativeNoiseEnrollmentError):p.key_check(info.public_key,r.context.digest)
        assert p._uncertain is True


def test_higher_signed_revision_cannot_be_rolled_back_or_equivocated_live():
    with fixture() as (r,p,info,record):
        first=record();assert p.key_check(info.public_key,r.context.digest)
        record(revision=2);assert p.key_check(info.public_key,r.context.digest)
        r.state.rows[p.record_id]=first
        with pytest.raises(NativeNoiseEnrollmentError):p.key_check(info.public_key,r.context.digest)
    with fixture() as (r,p,info,record):
        record();assert p.key_check(info.public_key,r.context.digest)
        record(state='revoked')
        with pytest.raises(NativeNoiseEnrollmentError):p.key_check(info.public_key,r.context.digest)
