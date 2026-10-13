"""Generated offline canary fixtures; hardware/accounting/backend are modeled.

MeshActionAuthority, provider, enrollment signatures, endpoint crypto and signed
workflow CAS run normally. This helper opens no socket, daemon, RF or vault.
"""
from contextlib import contextmanager,ExitStack
import copy
import hashlib
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import uuid
from types import SimpleNamespace

runtime=os.environ.get('LANTERN_NATIVE_RUNTIME_SOURCE')
if runtime:sys.path.insert(0,str(Path(runtime).resolve()))
from nacl.signing import SigningKey
from sage_lantern.mesh_daemon import HardwareRuntime
from sage_lantern.mesh_community import MeshActionAuthority,MeshRequestAccess
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.message_contacts import RecipientID
from meshticulum.privacy.mesh_envelope import Peer,seal_message
from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.privacy.forward_session import ForwardContext
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.forward_native_owner import approval_binding
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_forward_operation import backend
from tests.test_meshticulum_bench_runtime import BOOT

CANARY_PRIOR_DEBT=Cost(7,7000,3,512,512)


def canary_limits():
    """Fresh source-fixture permission includes five full child caps and prior debt."""
    return Cost(2000,300000000,12000,10000000,10000000).plus(CANARY_PRIOR_DEBT)


class SharedClock:
    def __init__(self):self.now=(100000000,50000000)
    def __call__(self):return self.now
    def advance(self,us):
        assert type(us) is int and us>=0
        self.now=tuple(v+us for v in self.now)


def resource_fixture(key,local,remote,clock,backend_factory=backend):
    state,client,endpoint=backend_factory(key)
    state.owner_live=True;state.epoch=secrets.token_hex(16);state.boot=BOOT
    expected_storage_epoch=getattr(state,'expected_storage_epoch',state.instance_id+':1')
    assert type(expected_storage_epoch) is str and re.fullmatch(r'[0-9a-f]{64}:[1-9][0-9]{0,19}',expected_storage_epoch)
    assert 0<int(expected_storage_epoch.rsplit(':',1)[1])<2**64
    resource=HardwareRuntime.__new__(HardwareRuntime) # Explicit modeled lifecycle; no daemon/radio init.
    resource.identity=client._identity;resource.actor=RecipientID(local.chain_id,local.public_key.hex())
    resource.probe=MessageStorageProbe(client,actor=resource.actor,endpoint=endpoint)
    resource._mutex=threading.Lock();resource._closed=resource._closing=resource._uncertain=False;resource._provider=None
    resource._epoch=lambda:state.epoch;resource._lock_marker=lambda:True
    resource.config=dict(agent_id=local.public_key.hex(),chain_id=local.chain_id,messaging_enabled=True,
        receive_while_locked=False,dispatch_enabled=True,dispatch_while_locked=False,
        peers=[dict(chain_id=remote.chain_id,agent_id=remote.public_key.hex(),radio_public_key='a'*64)])
    resource._config_reader=lambda:copy.deepcopy(resource.config)
    resource.supervisor=SimpleNamespace(phase='active',broker_id=secrets.token_hex(16),_resource=resource,
        resource_lifecycle_check=lambda:state.live,verified_epoch=lambda:expected_storage_epoch)
    return SimpleNamespace(state=state,client=client,resource=resource,authority=MeshActionAuthority(),
        clock=clock,key=key,local=local,remote=remote)


def accounting_fixture(module,clock,trace,label):
    class ModeledAccounting(module.NativeAccountingSource):
        """Typed seam only: these observations never establish native debt."""
        native_qualification=False
        def __init__(self):
            self.retained_debt=CANARY_PRIOR_DEBT;self.actual_usage=None
            self.evidence_sha256=hashlib.sha256(('source-only-'+label).encode()).hexdigest()
            self.closed=False;self.calls=0
        def observe(self,resource,intention):
            if self.closed:raise RuntimeError('modeled_accounting_closed')
            self.calls+=1;epoch,mono=clock()
            return module.NativeAccountingObservation(self.retained_debt,self.actual_usage,self.evidence_sha256,
                BOOT,epoch,mono)
        def close(self):self.closed=True;trace.append((label,'accounting_closed'));return True
    return ModeledAccounting()


@contextmanager
def generated_canary_pair(module,*,start=True,backend_factory=backend,clock=None,keys=None):
    """One original UUID/deadline, independent endpoint stores and fresh keys."""
    clock=SharedClock() if clock is None else clock
    keys=[SigningKey.generate(),SigningKey.generate()] if keys is None else list(keys)
    assert len(keys)==2 and all(isinstance(key,SigningKey) for key in keys)
    peers=[Peer('native-canary-left',bytes(keys[0].verify_key)),Peer('native-canary-right',bytes(keys[1].verify_key))]
    content='MESHTICULUM-GENERATED-CANARY-'+secrets.token_hex(32)
    issued,mono=clock();expires=issued+80000000;deadline=mono+80000000
    message=str(uuid.uuid4());wire=seal_message(keys[0],sender=peers[0],recipient=peers[1],message_id=message,
        content=content,issued_at=issued//1000000,expires_at=expires//1000000).wire
    packet=to_binary(wire);assert len(packet)<=2144
    binding=secrets.token_bytes(32);intention=ReceiveIntention(str(uuid.uuid4()),hashlib.sha256(packet).hexdigest(),
        len(packet),binding.hex(),BOOT,expires,deadline)
    # Freshly declared fixture limits include the conservative prior debt;
    # no live grant is widened and no unknown usage is treated as a refund.
    limits=canary_limits();trace=[]
    endpoints=[resource_fixture(keys[i],peers[i],peers[1-i],clock,backend_factory) for i in (0,1)]
    sources=[accounting_fixture(module,clock,trace,str(i)) for i in (0,1)];providers=[]
    with ExitStack() as stack:
        try:
            for i,e in enumerate(endpoints):
                h=e.resource;observation=sources[i].observe(h,intention)
                digest=module.setup_approval_binding(local=peers[i],remote=peers[1-i],local_initiator=i==0,
                    intention=intention,issued_epoch_us=issued,limits=limits,accounting=observation,
                    config_revision=hashlib.sha256(_canonical(h.config)).hexdigest(),broker_id=h.supervisor.broker_id,
                    storage_epoch=h.probe.snapshot().epoch)
                access=MeshRequestAccess(e.state.epoch,lambda e=e:e.state.owner_live)
                setup=stack.enter_context(e.authority.grant(h.actor,access,module.SETUP_SCOPES,approval_binding=digest))
                providers.append(module.ForwardNativeProvider(h,e.authority,setup,remote=peers[1-i],local_initiator=i==0,
                    intention=intention,issued_epoch_us=issued,limits=limits,accounting=sources[i],
                    boot_reader=lambda e=e:e.state.boot,clock_reader=clock))
            publics=[p.generate() for p in providers]
            context=ForwardContext(peers[0],peers[1],uuid.uuid4().bytes,binding,issued,expires,64,
                publics[0].public_key,publics[1].public_key)
            for i,e in enumerate(endpoints):
                h=e.resource;digest=approval_binding(context,intention,i==0,
                    config_revision=hashlib.sha256(_canonical(h.config)).hexdigest(),broker_id=h.supervisor.broker_id,
                    storage_epoch=h.probe.snapshot().epoch)
                exchange=stack.enter_context(e.authority.grant(h.actor,MeshRequestAccess(e.state.epoch,lambda e=e:e.state.owner_live),
                    module.SETUP_SCOPES,approval_binding=digest))
                providers[i].bind(context,exchange,remote_generation_id=publics[1-i].binding.generation_id)
            operations=[p.start(sealed_record=packet if i==0 else None) for i,p in enumerate(providers)] if start else []
            yield SimpleNamespace(clock=clock,context=context,intention=intention,packet=packet,wire=wire,
                content=content,message_id=message,peers=peers,keys=keys,endpoints=endpoints,sources=sources,
                providers=providers,owners=[p.owner for p in providers] if start else [],operations=operations,trace=trace,
                native_accounting_qualified=False,private_transport_qualified=False,backend_modeled=True)
        finally:
            for p in reversed(providers):p.close()
            for e in endpoints:e.client._client.close()


def modeled_stage(directory,pair,index,*,replay=False):
    """Actual flow/AEAD/SQLite journals; only RF and terminal IO are modeled."""
    from tests.test_meshticulum_private_carrier import carrier_pair
    from meshticulum.bench.services import ServiceTransfer
    from lantern_meshticulum.sage.forward_operation import describe_child
    source=index%2;destination=1-source;packet=pair.operations[source].packet()
    service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=pair.context.expires_epoch_us)
    with carrier_pair(directory/str(index),packet,pair.clock,'unit-a' if source==0 else 'unit-b',
        'unit-b' if source==0 else 'unit-a',index+1,service=service,
        original_expires_epoch_us=pair.context.expires_epoch_us) as carrier:
        if replay:carrier.workers[0].replay_once=True
        runners=[]
        for n,(flow,owner) in enumerate(zip((carrier.sender,carrier.receiver),(pair.owners[source],pair.owners[destination]))):
            transfer=ServiceTransfer(flow);plan=describe_child(transfer,slot=index,kind=index+1)
            def native(context,child,t,owner=owner):
                return owner.phase_check(context,child.kind,child.sends) and t.flow.authority.live()
            def retire(t):
                for _ in range(5):t.flow.step();pair.clock.advance(50000)
                return t.flow.flow.complete and not t.flow.flow.failure and t.flow.journal.db.execute(
                    "SELECT COUNT(*) FROM attempts WHERE state IN ('RESERVED','UNKNOWN')").fetchone()==(0,)
            def terminal(child,runner):
                return runner.retired and runner.transfer.flow.flow.complete and not runner.transfer.flow.flow.failure and runner.transfer.flow.journal.db.execute(
                    "SELECT COUNT(*) FROM attempts WHERE state IN ('RESERVED','UNKNOWN')").fetchone()==(0,)
            runner=owner.prepare_stage(plan,open_child=lambda child,t=transfer:t,native_check=native,
                retire_child=retire,contain_child=lambda *a:False,terminal_check=terminal,
                payload=packet if n==0 else None)
            runner.start();runners.append(runner)
        for _ in range(1600):
            runners[0].step();runners[1].step();pair.clock.advance(50000)
            if all(r.wire_complete for r in runners):break
        else:raise AssertionError('finite source canary stage did not complete')
        for owner,runner in zip((pair.owners[source],pair.owners[destination]),runners):
            assert owner.retire_stage(runner)
        return dict(modeled_transport=True,physical_qualified=False,
            replay_refusals=carrier.receiver.counts['rx_refused'],
            attempt_counts=[f.journal.snapshot()['attempts'] for f in (carrier.sender,carrier.receiver)])
