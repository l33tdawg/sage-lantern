"""Real local caps/approval/AEAD readiness; runtime, host and RF are source models."""
import base64
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace, MappingProxyType

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.service_runner import ServiceRunner
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher, WireError
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from lantern_meshticulum.bench import node, mcu_artifact as mcu, pilot_native_caps
from lantern_meshticulum.bench.profiles import admission_for, profile_for
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests.test_lora_comparison import native, source_scope, pair, qualified
from tests.test_pilot_parent_snapshot import root_observation
from tests.test_meshticulum_bench_runtime import ModelWorker


@contextmanager
def sender(native, *, flow=True, index=0):
    state,parent,context,payloads,now,clock,host,controller=native
    response=host['unit']=='unit-c';payload=payloads[index][response]
    package=qualified(controller,pair(context,payload,index,now,response))[host['unit']]
    with ExitStack() as stack:
        caps=pilot_native_caps.open_for_provision(state,host,clock,package);stack.callback(caps.close)
        prepared=caps.prepare_claim(package);plan=deepcopy(package['grant'])
        plan['application_deadline_monotonic_us']=prepared.application_deadline_monotonic_us
        receipt=caps.finalize_provision(prepared,plan)
        runs=state/'runs';runs.mkdir(mode=0o700)
        directory=runs/prepared.run_id;directory.mkdir(mode=0o700)
        secret=base64.b64decode(package['secret_base64'])
        for name,raw in [('grant.json',canonical(plan)),('secret.bin',secret),('active',b'active\n'),('sender.bin',payload)]:
            path=directory/name;path.write_bytes(raw);path.chmod(0o600)
        result=SimpleNamespace(caps=caps,directory=directory,now=now,clock=clock,host=host,plan=plan)
        if flow:
            caps.consume_start(receipt['run_id'],plan,secret)
            authority=RunAuthority(directory,expected_unit=host['unit'],host_boot_id=host['host_boot_id'],
                clock=clock,owner_probe=lambda:True,radio_admission=admission_for(profile_for(['sf7-500','sf5-500'][index])))
            stack.callback(authority.close)
            journal=AttemptJournal(directory/'attempts.sqlite',binding=authority.grant.digest,
                max_attempts=256,max_airtime_us=60000000,create=True,request_clock=lambda:clock()[0])
            stack.callback(journal.close);worker=ModelWorker(journal,clock)
            store=SessionStore(directory/'wire.sqlite',authority.grant,authority.local_peer,
                boot_id=host['host_boot_id'],clock=clock,create=True);stack.callback(store.close)
            ledger=Ledger(BudgetPolicy(180000000,60000000,120000000,1000000,quantization_us=1),authority.ledger_epoch,clock()[1])
            endpoint=BenchFlow(authority=authority,worker=worker,cipher=SessionCipher(authority.grant,authority.local_peer,store),
                journal=journal,flow_key=authority.flow_key,contract=authority.contract,profile=authority.carrier_profile,
                ledger=ledger,scheduler_epoch=authority.scheduler_epoch,broker_epoch=authority.broker_epoch,sends=True,clock=clock)
            stack.callback(endpoint.close)
            runner=ServiceRunner(ServiceTransfer(endpoint),payload=payload);runner.start()
            result.authority,result.worker,result.flow,result.runner=authority,worker,endpoint,runner
        yield result


def test_genuine_comparison_sf7_runs_actual_zero_credit_receive_without_public32(native):
    with sender(native) as s:
        assert s.authority.public32 is False and not node._fast_profile(s.authority.profile)
        assert node._comparison_sender_readiness(s.authority,s.caps) is True
        assert node._sender_readiness_pass(True,s.runner,s.flow,s.worker,s.authority,s.caps.current,comparison_caps=s.caps) is True
        assert s.flow.flow._peer_credit==0 and not s.worker.sent
        assert s.flow.journal.snapshot()['attempts']==0 and s.caps.snapshot()['starts']==1
        assert s.authority.profile==profile_for('sf7-500')


def test_genuine_comparison_sf7_passes_run_gate_before_original_job_and_driver(native,monkeypatch):
    from lantern_meshticulum.bench import pilot_job
    from lantern_meshticulum.ip import node as ip_node
    with sender(native,flow=False) as s:
        reached=[]
        def record(*args,**kwargs):reached.append(True);raise RuntimeError('stop_before_job')
        monkeypatch.setattr(ip_node,'_facts',lambda unit:s.host)
        monkeypatch.setattr(node,'clock',s.clock)
        monkeypatch.setattr(pilot_native_caps,'open_for_run',lambda *a,**k:s.caps)
        monkeypatch.setattr(pilot_job,'record',record)
        monkeypatch.setattr(node,'_serial',lambda *a,**k:pytest.fail('driver reached'))
        monkeypatch.setattr(node,'LinuxHAT',lambda *a,**k:pytest.fail('driver reached'))
        result=node.run_public_service(s.host['unit'],s.directory,sender_receive_ready=True,
            lora_mcu_artifact_selector=mcu.LORA_SELECTOR if s.host['unit']=='unit-a' else None)
        assert reached==[True] and result['error']=='RuntimeError:stop_before_job'
        assert result['sends'] is True and result['parent']['started'] is True
        assert not (s.directory/'job.json').exists() and not (s.directory/'attempts.sqlite').exists()


@pytest.mark.parametrize('damage',['missing','foreign-kind','foreign-approval','context','runtime','profile','pending','rebound','expired','retired'])
def test_comparison_readiness_refuses_before_receive_for_noncurrent_original_binding(native,monkeypatch,damage):
    with sender(native) as s:
        caps=s.caps;approval=caps._approve
        if damage=='missing':caps=None
        elif damage=='foreign-kind':object.__setattr__(caps.policy,'qualification_kind','c-bulk8-v1')
        elif damage=='foreign-approval':caps._approve=SimpleNamespace(check=lambda:True)
        elif damage=='context':approval.context=None
        elif damage=='runtime':approval.runtime.manifest['firmware']='mtb1-b433927ba6'
        elif damage=='profile':s.authority.profile=replace(s.authority.profile,spreading_factor=6)
        elif damage in ('pending','rebound'):
            pins=dict(mcu.LORA_NATIVE_BINDINGS,scope_sha256=None if damage=='pending' else 'a'*64)
            monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(pins))
        elif damage=='expired':s.now[0]+=600000000;s.now[1]+=600000000
        elif damage=='retired':(s.directory/'active').write_bytes(b'retired\n')
        monkeypatch.setattr(s.runner,'step',lambda:pytest.fail('receive must remain uncalled'))
        with pytest.raises((ValueError,RuntimeError)):
            node._sender_readiness_pass(True,s.runner,s.flow,s.worker,s.authority,s.caps.current,comparison_caps=caps)
        assert not s.worker.sent and s.flow.journal.snapshot()['attempts']==0
