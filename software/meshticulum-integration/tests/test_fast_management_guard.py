"""Real protected SQLite/ResumeParentGuard; clocks/facts and RF are modeled."""
import base64
from contextlib import ExitStack,contextmanager
import json
import os
from pathlib import Path
import pytest

from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.service_runner import ServiceRunner
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher
from meshticulum.scheduler.budget import BudgetPolicy,Ledger
from lantern_meshticulum.bench import fast_bundle as fast
from lantern_meshticulum.bench.profiles import admission_for,profile_from_plan
from lantern_meshticulum.bench import pilot_native_caps as native_caps
from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from lantern_meshticulum.bench.pilot_provision import provision
from lantern_meshticulum.bench.resume_parent_guard import ResumeParentGuard
from tests.test_fast_bundle import native,fast_data,admit,pair,wrap,ModelRadio,step
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path,protected_source,clock,facts,BOOT


@pytest.fixture(autouse=True)
def public_namespace_precedes_historical_capture(monkeypatch):
    """Match the launcher: public seeding precedes the exact history capture.

    The legacy fast model added public after sealing because it never reopened
    the management guard. Only fixture input/order changes; capture is genuine.
    """
    from lantern_meshticulum.bench import history_identity_seal as seals
    original=seals.capture
    def capture(root,**kwargs):
        directories=kwargs['expected_directories']
        if 'campaigns' in directories['']:
            (root/'public').mkdir(mode=0o700,exist_ok=True)
            directories=dict(directories)
            directories['']=list(dict.fromkeys((*directories[''],'public')))
            kwargs=dict(kwargs,expected_directories=directories)
        return original(root,**kwargs)
    monkeypatch.setattr(seals,'capture',capture)


def management_guard(n,unit,pins,runtime,approval_gate=None):
    p=n['policy'];directory=n['roots'][unit]/'parents'/p.parent_id
    owner=(directory/'owner.json').read_bytes();prior=(directory/'prior.json').read_bytes()
    binding=(directory/'history-binding.json').read_bytes()
    def approval(raw):
        value=json.loads(raw);e=value['enrollment']
        return (value['phase'] in ('enroll','current') and canonical(e['policy'])==canonical(p.to_dict())
            and e['unit']==unit and e['host_boot_id']==BOOT
            and e['history_binding_sha256']==rpc.sha(binding)
            and (approval_gate is None or approval_gate[0] is True))
    assert not hasattr(approval,'bundle') and not hasattr(approval,'reader')
    return ResumeParentGuard(n['roots'][unit],p,unit,
        owner_sha256=rpc.sha(owner),prior_sha256=rpc.sha(prior),binding_sha256=rpc.sha(binding),
        preparation_sha256=pins,management_phase='prelaunch',clock=n['clock'],
        facts=lambda:native_caps.NativeFacts(unit,BOOT,p.unit(unit).peer),approve=approval,
        runtime_check=runtime.check)


def prepare_request(n):
    expires,_=admit(n,0);current=pair(n,0,expires);wrap(n,current);pins={}
    for unit in ('unit-a','unit-c'):
        result=provision(current['packages'][unit],state=n['roots'][unit],facts=facts(unit),clock=n['clock'])
        pins[unit]={current['run_id']:result['prepared_identity_sha256']}
    return current,pins


def test_genuine_fresh_management_guard_opens_before_and_after_fast_prepare(native,protected_source,monkeypatch):
    n=native;runtime_root,manifest_pin=protected_source
    with rpc.Runtime(runtime_root,manifest_pin) as runtime:
        for unit in ('unit-a','unit-c'):
            with management_guard(n,unit,{},runtime) as guard:
                assert guard.check()['claims']==0
        current,pins=prepare_request(n)
        observed=[];original=native_caps.NativeCaps._validate
        def inspect_failure(self):
            try:return original(self)
            except Exception as error:
                observed.append((type(error).__name__,str(error)));raise
        monkeypatch.setattr(native_caps.NativeCaps,'_validate',inspect_failure)
        for unit in ('unit-a','unit-c'):
            try:
                with management_guard(n,unit,pins[unit],runtime) as guard:
                    value=guard.check()
                    assert value['claims']==value['prepared']==1 and value['starts']==0
            except Exception:
                print('SOURCE_ONLY_GUARD_FAILURE',observed)
                raise
        assert observed==[]


@pytest.mark.parametrize('fault',['stage','context','approval','owner','identity','source'])
def test_genuine_postprepare_guard_refuses_changed_protected_inputs(native,protected_source,fault):
    n=native;runtime_root,manifest_pin=protected_source;current,pins=prepare_request(n)
    unit='unit-a';directory=n['roots'][unit]/'parents'/n['policy'].parent_id
    with rpc.Runtime(runtime_root,manifest_pin) as runtime:
        if fault=='stage':
            path=directory/'fast-stage-0.json';value=json.loads(path.read_bytes());value['native_deadline_monotonic_us']+=1
            path.write_bytes(canonical(value))
        elif fault=='context':
            path=directory/'owner.json';value=json.loads(path.read_bytes())
            value['bundle_context']['stages'][0]['campaign_id']='f'*32;path.write_bytes(canonical(value))
        elif fault=='approval':(directory/'native.sqlite.approval').write_bytes(b'0'*64+b'\n')
        elif fault=='owner':
            path=directory/'owner.json';value=json.loads(path.read_bytes());value['manifest_sha256']='0'*64
            path.write_bytes(canonical(value))
        elif fault=='identity':pins[unit][current['run_id']]='0'*64
        else:
            source=runtime_root/'lantern_meshticulum/bench/resume_parent_guard.py'
            source.write_bytes(source.read_bytes()+b'\n# changed source control\n')
        with pytest.raises(Exception):management_guard(n,unit,pins[unit],runtime)
    assert not (directory/'native.sqlite.pending').exists()


@pytest.mark.parametrize('field',['reader','bundle','_reader_ref','_bundle_ref','_protected','_approve'])
def test_management_stage_inputs_cannot_be_replaced_with_caller_attributes(native,protected_source,field):
    n=native;runtime_root,manifest_pin=protected_source;_,pins=prepare_request(n)
    with rpc.Runtime(runtime_root,manifest_pin) as runtime, management_guard(n,'unit-a',pins['unit-a'],runtime) as guard:
        with pytest.raises(Exception,match='immutable'):setattr(guard.caps._approve,field,object())
        assert guard.caps.current() is True
        with pytest.raises(native_caps.NativeCapsError):guard.caps.public32_owner_current()
        raw=canonical(dict(enrollment=guard.caps._enrollment,phase='start'))
        with pytest.raises(Exception,match='phase_not_admission'):guard.caps._approve(raw)
        assert guard.check()['starts']==0


def test_stage_mutation_after_guard_open_is_detected_by_fresh_held_reader(native,protected_source):
    n=native;runtime_root,manifest_pin=protected_source;_,pins=prepare_request(n)
    with rpc.Runtime(runtime_root,manifest_pin) as runtime:
        guard=management_guard(n,'unit-a',pins['unit-a'],runtime)
        path=n['roots']['unit-a']/'parents'/n['policy'].parent_id/'fast-stage-0.json'
        path.write_bytes(path.read_bytes()+b'\n')
        with pytest.raises(Exception):guard.check()
        # A failed current check is retained; closing cannot declare it clean.
        with pytest.raises(Exception):guard.close()


def test_guard_rejects_original_management_callback_mutation(native,protected_source):
    n=native;runtime_root,manifest_pin=protected_source;_,pins=prepare_request(n)
    with rpc.Runtime(runtime_root,manifest_pin) as runtime:
        guard=management_guard(n,'unit-a',pins['unit-a'],runtime)
        guard.caps._approve._approve.__defaults__=()
        with pytest.raises(Exception):guard.check()
        with pytest.raises(Exception):guard.close()


def open_fd_identities():
    directory=Path('/proc/self/fd') if Path('/proc/self/fd').is_dir() else Path('/dev/fd')
    result={}
    for name in os.listdir(directory):
        if not name.isdecimal():continue
        try:info=os.fstat(int(name))
        except OSError:continue
        result[int(name)]=(info.st_dev,info.st_ino,info.st_mode)
    return result


@pytest.mark.parametrize('refused',[False,True])
def test_auxiliary_protected_reader_and_native_owner_close_without_fd_leaks(native,protected_source,refused):
    n=native;runtime_root,manifest_pin=protected_source;current,pins=prepare_request(n)
    if refused:pins['unit-a'][current['run_id']]='0'*64
    with rpc.Runtime(runtime_root,manifest_pin) as runtime:
        before=open_fd_identities()
        if refused:
            with pytest.raises(Exception):management_guard(n,'unit-a',pins['unit-a'],runtime)
        else:
            with management_guard(n,'unit-a',pins['unit-a'],runtime) as guard:assert guard.check()['prepared']==1
        assert open_fd_identities()==before


def test_original_approval_revocation_remains_fresh_and_does_not_create_start(native,protected_source):
    n=native;runtime_root,manifest_pin=protected_source;_,pins=prepare_request(n);allowed=[True]
    with rpc.Runtime(runtime_root,manifest_pin) as runtime:
        guard=management_guard(n,'unit-a',pins['unit-a'],runtime,approval_gate=allowed)
        assert guard.check()['starts']==0
        allowed[0]=False
        with pytest.raises(Exception):guard.check()
        assert guard.caps.db.execute('SELECT COUNT(*) FROM starts').fetchone()==(0,)
        with pytest.raises(Exception):guard.close()


@contextmanager
def started_request_after_real_management_barrier(n,current):
    """Actual crypto/authority/flow/journals; the ModelRadio never calls devices."""
    endpoints=[];request,_=n['context'].exchange(0)
    with ExitStack() as stack:
        for unit in ('unit-a','unit-c'):
            packet=current['packages'][unit];directory=n['roots'][unit]/'runs'/current['run_id']
            plan=json.loads((directory/'grant.json').read_bytes())
            caps=native_caps._open_existing(n['roots'][unit],n['policy'].parent_id,facts(unit),n['clock'])
            stack.callback(caps.close)
            caps.consume_start(current['run_id'],plan,base64.b64decode(packet['secret_base64']))
            profile=profile_from_plan(plan)
            authority=RunAuthority(directory,expected_unit=unit,host_boot_id=BOOT,clock=n['clock'],
                owner_probe=lambda c=caps:c.current(),radio_admission=admission_for(profile));stack.callback(authority.close)
            journal=AttemptJournal(directory/'model-attempts.sqlite',binding=authority.grant.digest,max_attempts=256,
                max_airtime_us=60000000,create=True);stack.callback(journal.close)
            store=SessionStore(directory/'model-wire.sqlite',authority.grant,authority.local_peer,boot_id=BOOT,
                clock=n['clock'],create=True);stack.callback(store.close)
            worker=ModelRadio(journal,n['clock'],profile)
            ledger=Ledger(BudgetPolicy(240000000,60000000,120000000,1000000,quantization_us=1),authority.ledger_epoch,n['now'][0][1])
            flow=BenchFlow(authority=authority,worker=worker,cipher=SessionCipher(authority.grant,authority.local_peer,store),
                journal=journal,flow_key=authority.flow_key,contract=authority.contract,profile=authority.carrier_profile,
                ledger=ledger,scheduler_epoch=authority.scheduler_epoch,broker_epoch=authority.broker_epoch,
                sends=authority.sends,clock=n['clock']);stack.callback(flow.close)
            runner=ServiceRunner(ServiceTransfer(flow),payload=request if authority.sends else None);runner.start()
            endpoints.append(dict(directory=directory,authority=authority,caps=caps,flow=flow,worker=worker,runner=runner))
        endpoints[0]['worker'].peer=endpoints[1]['worker'];endpoints[1]['worker'].peer=endpoints[0]['worker']
        yield endpoints


def test_real_prepare_guard_start_crypto_final_ack_and_receiver_completion(native,protected_source):
    n=native;runtime_root,manifest_pin=protected_source;current,pins=prepare_request(n)
    with rpc.Runtime(runtime_root,manifest_pin) as runtime:
        for unit in ('unit-a','unit-c'):
            with management_guard(n,unit,pins[unit],runtime) as guard:
                assert guard.check()['claims']==guard.check()['prepared']==1
                assert guard.check()['starts']==0
    with started_request_after_real_management_barrier(n,current) as endpoints:
        sender,receiver=endpoints
        for _ in range(80):
            step(n,endpoints)
            if sender['runner'].wire_complete:break
        assert sender['runner'].wire_complete and receiver['runner'].wire_complete
        proof=fast.final_ack_proof(sender['authority'],sender['caps'],sender['runner'])
        assert fast.receiver_completed(receiver['authority'],receiver['caps'],receiver['directory']) is False
        delivered=fast.deliver_final_ack('unit-c',current['run_id'],proof,state=n['roots']['unit-c'],
            clock=n['clock'],facts=facts('unit-c'))
        assert delivered['delivered'] and delivered['radio_opened'] is False
        assert fast.receiver_completed(receiver['authority'],receiver['caps'],receiver['directory']) is True
        for endpoint in endpoints:
            assert endpoint['caps'].snapshot()['starts']==1
            assert endpoint['flow'].snapshot()['complete'] is True
