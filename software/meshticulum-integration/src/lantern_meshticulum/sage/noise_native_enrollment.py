# SPDX-License-Identifier: Apache-2.0
"""Explicit root-authorized enrollment and signed native Noise observation.

Read observers create no key, record, grant, client, lease or transport. The
explicit enroll method creates one public record only when the SAME original
authenticated root request additionally grants ENROLL_SCOPE. Missing records
still refuse read observers. Setup approval (before key generation) and
full exchange approval (after both pins form ForwardContext) remain distinct.
The local record explicitly approves the remote pin; it never reads another
actor's NoisePurpose record with the local client. Source use is not native
qualification or production activation, and Python close is not secure erasure.
"""
import base64
import hashlib
import os
import json
import threading

from nacl.signing import VerifyKey
from meshticulum.privacy.forward_session import ForwardContext
from .forward_native_owner import SCOPES,approval_binding,host_boot,clock
from .forward_receive_journal import ReceiveIntention
from .mesh_inbox import _canonical,_id,_uuid
from .noise_purpose import NoisePublic,PURPOSE,ALGORITHM
from .workflow_store import WorkflowStore,WorkflowNotFound
from .bound_storage_pair import BoundStoragePair,BoundStoragePairError

SCHEMA='sage.lantern.native-noise-enrollment.v1'
SIGNATURE_DOMAIN=b'sage.lantern.native-noise-enrollment.v1/signature\0'
KIND='mesh_outbound'
ENROLL_SCOPE='meshticulum_forward_noise_enroll'


class NativeNoiseEnrollmentError(RuntimeError):
    def __init__(self):super().__init__('native_noise_enrollment_unavailable')


def require(value):
    if not value:raise NativeNoiseEnrollmentError()


class NativeNoiseEnrollment:
    """Borrow exact native owner/request/store, and observe approved public pins.

    local_public is public comparison input, never enrollment authority. Only
    the actual signed protected record plus live original root grant can pass.
    Guards work inside a caller's held lease and after exit; no second lease.
    """
    def __init__(self,resource,authority,native_context,store,*,context,intention,
                 local_initiator,local_public,remote_generation_id,boot_reader=host_boot,clock_reader=clock,
                 storage_pair=None):
        from sage_lantern.mesh_daemon import HardwareRuntime
        from sage_lantern.mesh_community import MeshActionAuthority
        from sage_lantern.mesh_runtime import MeshUserContext
        require(type(resource) is HardwareRuntime and type(authority) is MeshActionAuthority
            and type(native_context) is MeshUserContext and type(store) is WorkflowStore
            and type(context) is ForwardContext and type(intention) is ReceiveIntention
            and type(local_initiator) is bool and type(local_public) is NoisePublic
            and callable(boot_reader) and callable(clock_reader)
            and (storage_pair is None or type(storage_pair) is BoundStoragePair))
        _uuid(remote_generation_id)
        local=context.initiator if local_initiator else context.responder
        remote=context.responder if local_initiator else context.initiator
        local_key=context.initiator_noise_public_key if local_initiator else context.responder_noise_public_key
        remote_key=context.responder_noise_public_key if local_initiator else context.initiator_noise_public_key
        require(local_public.binding.local==local and local_public.binding.remote==remote
            and local_public.binding.local_initiator is local_initiator
            and local_public.binding.intention==intention and local_public.public_key==local_key
            and local_public.binding.issued_epoch_us==context.issued_epoch_us
            and intention.authority_digest==context.binding_digest.hex()
            and intention.application_expires_epoch_us==context.expires_epoch_us
            and store.client is store.storage.client is resource.probe.client
            and store.storage.actor.chain_id==local.chain_id
            and store.storage.actor.agent_id==local.public_key.hex())
        self.resource,self.authority,self.native_context,self.store=resource,authority,native_context,store
        self.context,self.intention,self.local_public=context,intention,local_public
        self.local,self.remote,self.local_initiator=local,remote,local_initiator
        self._boot,self._clock=boot_reader,clock_reader
        self._storage_pair=self._storage_pair_ref=storage_pair
        self._storage_pair_check=self._storage_pair_check_ref=None if storage_pair is None else storage_pair.observe
        self._storage_pair_delegates=None if storage_pair is None else (
            storage_pair._BoundStoragePair__matches,storage_pair._BoundStoragePair__observe)
        self._pid_thread=(os.getpid(),threading.get_ident());self._closed=self._busy=self._uncertain=False
        self._last=None;self._highest=-1;self._body=None
        self._refs=(resource,authority,native_context,store,store.client,store.storage,resource.supervisor,
                    resource.probe,resource.identity,resource.identity._signing_key,context,intention,local_public,
                    boot_reader,clock_reader,storage_pair,self._storage_pair_check,self._storage_pair_delegates)
        self.config_revision=hashlib.sha256(_canonical(resource.config)).hexdigest()
        self.consent=resource.consent();self.broker_id=resource.supervisor.broker_id
        self.storage_epoch=store.storage.snapshot().epoch
        self.exchange_approval=approval_binding(context,intention,local_initiator,
            config_revision=self.config_revision,broker_id=self.broker_id,storage_epoch=self.storage_epoch)
        require(native_context.approval_binding==self.exchange_approval
            and native_context.actor==resource.actor and resource.actor.chain_id==local.chain_id
            and resource.actor.agent_id==resource.identity.agent_id==local.public_key.hex())
        self.record_id=_id(SCHEMA,[local.to_dict(),intention.operation_id])
        binding=dict(local_public=local_public.to_dict(),remote=remote.to_dict(),remote_public_key=remote_key.hex(),
            remote_generation_id=remote_generation_id,context_digest=context.digest.hex(),
            exchange_approval_digest=self.exchange_approval,config_revision=self.config_revision,
            broker_id=self.broker_id,storage_epoch=self.storage_epoch)
        self._binding=_canonical(binding)
        self._facts=(self.record_id,self.config_revision,self.consent,self.broker_id,self.storage_epoch,
                     self.exchange_approval,self._binding,local,remote,local_initiator)
        self._original_public=_canonical(local_public.to_dict())
        self._original_context=context.digest
        self._original_intention=_canonical(intention.to_dict())
        self._guard()

    def __repr__(self):return '<NativeNoiseEnrollment>'

    def _available(self):
        require(not self._closed and not self._uncertain
                and self._pid_thread==(os.getpid(),threading.get_ident()))
        h=self.resource
        current=(h,self.authority,self.native_context,self.store,self.store.client,self.store.storage,h.supervisor,
                 h.probe,h.identity,h.identity._signing_key,self.context,self.intention,self.local_public,
                 self._boot,self._clock,self._storage_pair,self._storage_pair_check,self._storage_pair_delegates)
        if not all(a is b for a,b in zip(self._refs,current)):
            self._uncertain=True;raise NativeNoiseEnrollmentError()
        require(self._storage_pair is self._storage_pair_ref and self._storage_pair_check is self._storage_pair_check_ref)
        if self._storage_pair is None:
            require(self._storage_pair_check is None and self._storage_pair_delegates is None)
        else:
            require(type(self._storage_pair) is BoundStoragePair
                and self._storage_pair_check.__self__ is self._storage_pair
                and self._storage_pair_check.__func__ is BoundStoragePair.observe
                and all(current is original for current,original in zip(
                    (getattr(self._storage_pair,'_BoundStoragePair__matches',None),
                     getattr(self._storage_pair,'_BoundStoragePair__observe',None)),self._storage_pair_delegates)))
            try: paired=self._storage_pair.matches(h.probe,self.store)
            except BoundStoragePairError: raise NativeNoiseEnrollmentError() from None
            require(paired is True)
        current_facts=(self.record_id,self.config_revision,self.consent,self.broker_id,self.storage_epoch,
                       self.exchange_approval,self._binding,self.local,self.remote,self.local_initiator)
        if (current_facts!=self._facts or _canonical(self.local_public.to_dict())!=self._original_public
                or self.context.digest!=self._original_context
                or _canonical(self.intention.to_dict())!=self._original_intention):
            self._uncertain=True;raise NativeNoiseEnrollmentError()
        require(h.supervisor._resource is h and h.supervisor.phase=='active'
            and not h._closed and not h._closing and not h._uncertain
            and self.native_context.approval_binding==self.exchange_approval
            and self.native_context.actor==h.actor)

    def _checked(self,callback,*args):
        self._available();value=callback(*args);self._available();return value

    def _access(self):
        h=self.resource;self._available()
        require(self._checked(h.configured) is True and self._checked(h.active) is True
            and self._checked(h.consent)==self.consent
            and self.consent.messaging_enabled is True and self.consent.dispatch_enabled is True
            and hashlib.sha256(_canonical(h.config)).hexdigest()==self.config_revision
            and h.supervisor.broker_id==self.broker_id
            and self._checked(h.supervisor.resource_lifecycle_check) is True
            and self._checked(h.supervisor.verified_epoch)==self.storage_epoch
            and self._checked(h._epoch)==self.native_context.epoch
            and self._checked(self._boot)==self.intention.host_boot_id)
        for scope in SCOPES:
            require(self._checked(self.authority.authorized,self.native_context,scope,None) is True)
        require(any(p['chain_id']==self.remote.chain_id and p['agent_id']==self.remote.public_key.hex()
                    for p in h.config['peers']))

    def _time(self):
        now=self._checked(self._clock)
        require(type(now) is tuple and len(now)==2 and all(type(n) is int and n>=0 for n in now)
            and (self._last is None or all(a>=b for a,b in zip(now,self._last)))
            and self.context.issued_epoch_us<=now[0]<self.intention.application_expires_epoch_us
            and now[1]<self.intention.application_deadline_monotonic_us)
        self._last=now

    def _workflow_guard(self):
        # Every invocation rechecks the original live root grant and lifetime.
        # WorkflowStore independently takes fresh HTTP epochs before and after
        # its SDK call; full outer guards still fence this signed observation.
        self._access();self._time();self._access();self._time();return True

    def _guard(self):
        self._workflow_guard()
        if self._storage_pair_check is None:
            require(self._checked(self.resource.probe.snapshot).epoch==self.storage_epoch
                and self._checked(self.store.storage.snapshot).epoch==self.storage_epoch)
        else:
            try: require(self._checked(self._storage_pair_check,self.storage_epoch)==self.storage_epoch)
            except BoundStoragePairError: raise NativeNoiseEnrollmentError() from None
        self._workflow_guard();return True

    def _validate(self,record):
        value=record['payload']
        require(type(value) is dict and set(value)=={'schema','body','signature'} and value['schema']==SCHEMA)
        signature=base64.b64decode(value['signature'],validate=True)
        require(len(signature)==64 and base64.b64encode(signature).decode()==value['signature'])
        body=value['body'];VerifyKey(self.local.public_key).verify(SIGNATURE_DOMAIN+_canonical(body),signature)
        require(type(body) is dict and set(body)=={'record_id','revision','purpose','algorithm','binding','state'}
            and body['record_id']==record['record_id']==self.record_id
            and type(body['revision']) is int and body['revision']==record['revision'] and body['revision']>0
            and body['purpose']==PURPOSE and body['algorithm']==ALGORITHM
            and _canonical(body['binding'])==self._binding and body['state'] in ('active','revoked'))
        raw=_canonical(body)
        require(body['revision']>=self._highest and (body['revision']!=self._highest or raw==self._body))
        self._highest,self._body=body['revision'],raw
        require(body['state']=='active')

    def _observe(self):
        entered=False
        try:
            self._available()
            if self._busy:self._uncertain=True;raise NativeNoiseEnrollmentError()
            self._busy=entered=True;self._guard()
            record=self.store.get(self.record_id,kind=KIND,access_check=self._workflow_guard,
                                  expected_epoch=self.storage_epoch)
            self._validate(record);self._guard();return True
        except BaseException as error:
            if entered:self._uncertain=True
            if not isinstance(error,Exception):raise
            raise NativeNoiseEnrollmentError() from None
        finally:
            if entered:self._busy=False

    def _enroll_guard(self):
        self._guard()
        require(self._checked(self.authority.authorized,self.native_context,ENROLL_SCOPE,None) is True)
        self._guard();return True

    def _enroll_workflow_guard(self):
        self._workflow_guard()
        require(self._checked(self.authority.authorized,self.native_context,ENROLL_SCOPE,None) is True)
        self._workflow_guard();return True

    def enroll(self):
        """Create-only signed public enrollment under explicit original approval.

        Existing active, revoked or malformed records are NEVER overwritten.
        CAS uncertainty fences this instance; retained committed public state
        must be re-read by a fresh authorized observer. No lease, key generation,
        peer registration, native transport or production activation occurs.
        """
        entered=False
        try:
            self._available()
            if self._busy:self._uncertain=True;raise NativeNoiseEnrollmentError()
            self._busy=entered=True;self._enroll_guard()
            try:self.store.get(self.record_id,kind=KIND,access_check=self._enroll_workflow_guard,
                               expected_epoch=self.storage_epoch)
            except WorkflowNotFound:pass
            else:raise NativeNoiseEnrollmentError()
            self._enroll_guard()
            body=dict(record_id=self.record_id,revision=1,purpose=PURPOSE,algorithm=ALGORITHM,
                      binding=json.loads(self._binding),state='active')
            signed=self._checked(self._refs[9].sign,SIGNATURE_DOMAIN+_canonical(body))
            payload=dict(schema=SCHEMA,body=body,signature=base64.b64encode(signed.signature).decode('ascii'))
            self._enroll_guard()
            returned=self.store.put(self.record_id,KIND,0,payload,access_check=self._enroll_workflow_guard,
                                    expected_epoch=self.storage_epoch)
            self._validate(returned);require(self._body==_canonical(body));self._enroll_guard()
            observed=self.store.get(self.record_id,kind=KIND,access_check=self._enroll_workflow_guard,
                                    expected_epoch=self.storage_epoch)
            self._validate(observed);require(self._body==_canonical(body));self._enroll_guard()
            return True
        except BaseException as error:
            if entered:self._uncertain=True
            if not isinstance(error,Exception):raise
            raise NativeNoiseEnrollmentError() from None
        finally:
            if entered:self._busy=False

    def enrollment_check(self,info,context_digest):
        require(type(info) is NoisePublic and _canonical(info.to_dict())==_canonical(self.local_public.to_dict())
                and context_digest==self.context.digest)
        return self._observe()

    def key_check(self,public_key,context_digest):
        require(public_key==self.local_public.public_key and context_digest==self.context.digest)
        return self._observe()

    def peer_key_check(self,peer,public_key,context_digest):
        expected=self.context.responder_noise_public_key if self.local_initiator else self.context.initiator_noise_public_key
        require(peer==self.remote and public_key==expected and context_digest==self.context.digest)
        return self._observe()

    def exchange_check(self,info,remote_peer,remote_public_key,context_digest):
        """Validate both approved pins with one fresh signed enrollment read."""
        expected=self.context.responder_noise_public_key if self.local_initiator else self.context.initiator_noise_public_key
        require(type(info) is NoisePublic and _canonical(info.to_dict())==_canonical(self.local_public.to_dict())
            and remote_peer==self.remote and remote_public_key==expected and context_digest==self.context.digest)
        return self._observe()

    def close(self):
        self._closed=True
        return self._pid_thread==(os.getpid(),threading.get_ident()) and not self._busy and not self._uncertain
