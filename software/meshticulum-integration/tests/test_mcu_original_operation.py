"""Original producer/route source controls; no native permission or traffic.

Positive Controller coverage uses opaque ModelOperator receipts and a test-local
binding-validation seam. Production pending pins are never edited by these tests.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import MappingProxyType

import pytest

from lantern_meshticulum.bench import mcu_artifact as mcu,fast_bundle as fast,pilot_bundle as bundle
from lantern_meshticulum.bench.pilot_controller import Controller,pair_packages
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps,Cost,canonical
from lantern_meshticulum.bench.pilot_ssh import SSHOperator,preparation_schema
from tests.test_pilot_bundle import data
from tests.test_fast_bundle import fast_data
from tests.test_pilot_controller import ModelOperator
from tests.test_pilot_ssh import config


@pytest.fixture
def pending_selector_bindings(monkeypatch):
    """Explicit pending state for only the refusal cases requesting it."""
    monkeypatch.setattr(mcu,'PENDING_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256=None,history_receipt_sha256=None,preceding_parent=None)))


@pytest.fixture
def runner(monkeypatch):
    path=Path(__file__).parents[3]/'firmware/meshticulum-bench'
    monkeypatch.syspath_prepend(str(path))
    spec=importlib.util.spec_from_file_location('original_mcu_operation_control',path/'run_public_gfsk250_mb1_fast_bundle100k.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.fixture
def original(fast_data,runner,tmp_path):
    _,chunks,value,policy=fast_data
    from meshticulum.resources import lamplighter as ll
    from lantern_meshticulum.ip.node import _write
    context=bundle.BundleContext(value,policy)
    pack=tmp_path/'signed-pack';pack.mkdir(mode=0o700);(pack/'chunks').mkdir(mode=0o700)
    _write(pack/'manifest.json',context.manifest_raw);_write(pack/'manifest.sig.json',context.signature_raw)
    for name,raw in chunks.items():_write(pack/'chunks'/(name+'.json'),raw)
    cache=ll.VerifiedCache(pack,value['trust'],value['manifest_sha256'])
    responses=[context.transport.response(index,cache)for index in range(5)]
    manifest=dict(mcu_artifact=mcu.artifact(),firmware=mcu.FIRMWARE,
        firmware_by_unit={'unit-a':mcu.FIRMWARE,'unit-b':'mtb1-9816f92488'},
        radio_profile_name='gfsk250',native_operation_kind=mcu.KIND)
    op=dict(schema=mcu.OPERATION_SCHEMA,mcu_artifact_selector=mcu.SELECTOR,mcu_artifact=mcu.artifact(),
        core_commit=mcu.CORE,runtime_sha256='7'*64,manifest_sha256='8'*64,
        mb1_bundle_scope_sha256='9'*64,history_receipt_sha256='a'*64)
    owner=dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind=mcu.KIND,
        policy_digest=policy.digest,core_commit=mcu.CORE,runtime_sha256=op['runtime_sha256'],
        manifest_sha256=op['manifest_sha256'],mb1_bundle_scope_sha256=op['mb1_bundle_scope_sha256'],
        history_receipt_sha256=op['history_receipt_sha256'],bundle_context=value,
        radio_modem='gfsk250',radio_profile_name='gfsk250',
        rf_profile=fast.profile_record(fast.profile_for('gfsk250')),
        source_control_only=True)
    captures={u:dict(history_binding_sha256=str(i)*64,capture_record_sha256=str(i+3)*64)
        for i,u in enumerate(('unit-a','unit-b','unit-c'),1)}
    owners,common=runner.original_owners_for(op,owner,captures,policy,manifest)
    return dict(op=op,owner=owner,owners=owners,common=common,manifest=manifest,policy=policy,value=value,responses=responses)


def test_explicit_producer_wraps_original_owner_and_preserves_legacy_default(runner,original):
    n=original;assert runner.MCU_SCHEMA==mcu.OPERATION_SCHEMA
    for unit,wrapped in n['owners'].items():
        owner,selected=mcu.decode_owner(wrapped)
        assert selected==mcu.artifact() and owner['policy_digest']==n['policy'].digest
        assert owner['bundle_context']==n['value'] and owner['source_control_only']is True
        assert owner['history_binding_sha256']==str(('unit-a','unit-b','unit-c').index(unit)+1)*64
    assert mcu.decode_owner(n['common'])[0]==n['owner']
    old=dict(n['op'],schema=runner.SCHEMA);old.pop('mcu_artifact');old.pop('mcu_artifact_selector')
    manifest={'firmware_by_unit':{'unit-a':'mtb1-def16514fc','unit-b':'mtb1-9816f92488'}}
    captures={u:dict(history_binding_sha256='b'*64,capture_record_sha256='c'*64)for u in n['owners']}
    owners,common=runner.original_owners_for(old,n['owner'],captures,n['policy'],manifest)
    assert common is None and all(o['schema']=='meshticulum.finite-public-resume-owner.v1'for o in owners.values())


@pytest.mark.parametrize('damage',['legacy_selector','legacy_runtime','selector','core','artifact','A_image','B_image'])
def test_original_operation_cannot_relabel_or_substitute_runtime(runner,original,damage):
    op=deepcopy(original['op']);manifest=deepcopy(original['manifest'])
    if damage=='legacy_selector':op['schema']=runner.SCHEMA
    elif damage=='legacy_runtime':
        op['schema']=runner.SCHEMA;op.pop('mcu_artifact_selector');op.pop('mcu_artifact')
    elif damage=='selector':op['mcu_artifact_selector']='gfsk250'
    elif damage=='core':op['core_commit']='0'*40
    elif damage=='artifact':op['mcu_artifact']['image_sha256']='0'*64
    elif damage=='A_image':manifest['firmware_by_unit']['unit-a']='mtb1-def16514fc'
    else:manifest['firmware_by_unit']['unit-b']=mcu.FIRMWARE
    with pytest.raises(Exception):runner.operation_selection(op,manifest)


def test_pending_original_operation_refuses_before_fixture_credentials_or_native_work(runner,original,monkeypatch,pending_selector_bindings):
    n=original
    assert all(v is None for v in mcu.PENDING_NATIVE_BINDINGS.values())
    monkeypatch.setattr(runner,'review',lambda *a:(n['op'],Path('.'),Path('.'),n['manifest'],{},None))
    monkeypatch.setattr(runner,'fixture',lambda *a:pytest.fail('fixture/native work must not begin'))
    monkeypatch.setattr(runner.getpass,'getpass',lambda *a:pytest.fail('credentials must not be requested'))
    monkeypatch.setattr(sys,'argv',['source-control','--operation','unused','--operation-sha256','0'*64])
    with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):runner.main()


def controller_arguments(n,parent,operator,now):
    return dict(operator=operator,campaign_id=n['value']['stages'][0]['campaign_id'],
        resource_sha256=n['value']['manifest_sha256'],expires_epoch_us=now[0]+180000000,
        handoff_deadline_monotonic=parent.policy.deadline_monotonic_us/1000000,
        peers={u.unit:asdict(u.peer)for u in parent.policy.units},
        boots={u.unit:u.host_boot_id for u in parent.policy.units},parent=parent,
        radio_permission=dict(country='MY',antenna_confirmed=True),clock=lambda:now[1]/1000000,
        epoch=lambda:now[0],sleep=lambda _:None,public32=True,bundle_context=n['value'],
        radio_profile='gfsk250',mcu_owner=n['common'])


def test_pending_controller_and_campaign_never_prepay_or_admit_stage(original,tmp_path,pending_selector_bindings):
    n=original;p=n['policy'];now=[p.issued_epoch_us+1000000,p.original_monotonic_us+1000000]
    root=tmp_path/'controller';root.mkdir(mode=0o700)
    parent=ParentCaps.enroll(root/'caps.sqlite',p,clock=lambda:tuple(now),boot_reader=lambda:p.controller_boot_id)
    try:
        before=parent.snapshot()
        with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):
            Controller(**controller_arguments(n,parent,object(),now))
        with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):
            fast.FastBundleCampaign(object(),parent,n['value'],peers={},boots={},
                handoff_deadline_monotonic=p.deadline_monotonic_us/1000000,
                clock=lambda:now[1]/1000000,epoch=lambda:now[0],sleep=lambda _:None,mcu_owner=n['common'])
        assert parent.snapshot()==before
    finally:parent.close()


def selected_config(n):
    cfg,source=config();commitment=bundle.public_context_commitment(n['value'],parent_id=n['policy'].parent_id)
    cfg.update(schema=mcu.SSH_SCHEMA,mcu_artifact=mcu.artifact(),bundle_parent_id=n['policy'].parent_id,
        bundle_context=n['value'],bundle_capture_commitment=commitment,handoff_end_epoch_us=commitment['phase_end_epoch_us'])
    return cfg,source


def test_pending_ssh_config_refuses_before_connection(original,monkeypatch,pending_selector_bindings):
    cfg,source=selected_config(original)
    monkeypatch.setattr(SSHOperator,'_ssh',lambda *a,**k:pytest.fail('no SSH target may be reached'))
    with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):
        SSHOperator(cfg,provision_source=source,passwords=None)


@pytest.fixture
def model_binding_validation(monkeypatch,original):
    """Parser seam only: no NativeCaps, device facts or approved native scope."""
    original_bindings=mcu.PENDING_NATIVE_BINDINGS
    pins=dict(scope_sha256=original['op']['mb1_bundle_scope_sha256'],
        history_receipt_sha256=original['op']['history_receipt_sha256'],preceding_parent='source-control-only')
    calls=[]
    def validate(kind):
        assert kind==mcu.KIND;calls.append(kind);return dict(pins)
    monkeypatch.setattr(mcu,'require_selector_scope',validate)
    def native_binding(value,policy,manifest):
        # The real pending validator is covered separately. This seam validates
        # only the source fixture's structural binding, never current consent.
        owner,selected=mcu.decode_owner(value)
        assert mcu.select_runtime(manifest)==selected and owner['policy_digest']==policy.digest
        assert owner['mb1_bundle_scope_sha256']==pins['scope_sha256']
        assert owner['history_receipt_sha256']==pins['history_receipt_sha256']
        validate(policy.qualification_kind);return selected
    monkeypatch.setattr(mcu,'require_native_scope',native_binding)
    yield calls
    assert mcu.PENDING_NATIVE_BINDINGS is original_bindings


class StopBeforeNativeStart(RuntimeError):pass


class SelectedModelOperator(ModelOperator):
    def __init__(self,now,boots,damage=None):
        super().__init__(None);self.now=now;self.boots=boots;self.damage=damage
    def prepare(self,package):
        receipt=super().prepare(package)
        receipt.update(schema=preparation_schema(dict(schema=mcu.SSH_SCHEMA,mcu_artifact=mcu.artifact()),package),
            mcu_artifact=deepcopy(package['mcu_artifact']))
        receipt['parent']['host_boot_id']=self.boots[package['unit']]
        if self.damage=='schema':receipt['schema']='meshticulum.provisioned-public-pilot.v2'
        elif self.damage=='artifact':receipt['mcu_artifact']['image_sha256']='0'*64
        elif self.damage=='child':receipt['parent']['child_caps']=deepcopy(receipt['parent']['child_caps']);receipt['parent']['child_caps']['metadata']['mcu_artifact_sha256']='0'*64
        return receipt
    def start(self,*a):raise StopBeforeNativeStart('modeled source receipt accepted; no native start')


def test_actual_selected_controller_routes_all20_paid_child_slots(original,tmp_path,model_binding_validation):
    n=original;p=n['policy'];now=[p.issued_epoch_us+1000000,p.original_monotonic_us+1000000]
    root=tmp_path/'controller';root.mkdir(mode=0o700)
    parent=ParentCaps.enroll(root/'caps.sqlite',p,clock=lambda:tuple(now),boot_reader=lambda:p.controller_boot_id)
    boots={u.unit:u.host_boot_id for u in p.units};op=SelectedModelOperator(now,boots)
    try:
        context=bundle.BundleContext(n['value'],p)
        for index,declared in enumerate(n['value']['stages']):
            args=controller_arguments(n,parent,op,now);args['campaign_id']=declared['campaign_id']
            control=Controller(**args);request,_=context.exchange(index)
            for sender,receiver,format,body,source in (
                ('unit-a','unit-c','MR1.request',request,None),
                ('unit-c','unit-a','MR1.response',n['responses'][index],dict(kind='served_public_chunk',request_run_id='d'*32))):
                with pytest.raises(StopBeforeNativeStart):control.link(sender,receiver,'lora',format,body,source)
                assert control.links[-1]['prepared']==[receiver,sender]
                assert set(control.links[-1]['retired'])=={'unit-a','unit-c'}
        packages=[event[2]for event in op.events if event[0]=='prepare']
        assert len(packages)==20 and {p['unit']for p in packages}=={'unit-a','unit-c'}
        assert all(p['schema']==mcu.PAID_SCHEMA and p['mcu_artifact']==mcu.artifact()for p in packages)
        snapshot=parent.snapshot();assert snapshot['allocations']==10
        for unit in p.units:
            assert snapshot['per_unit'][unit.unit]==asdict(unit.limits)
        assert snapshot['aggregate']==asdict(p.aggregate_limits)
        assert model_binding_validation
    finally:parent.close()


@pytest.mark.parametrize('damage',['schema','artifact','child'])
def test_selected_controller_refuses_legacy_or_changed_receipts(original,tmp_path,model_binding_validation,damage):
    n=original;p=n['policy'];now=[p.issued_epoch_us+1000000,p.original_monotonic_us+1000000]
    root=tmp_path/'controller';root.mkdir(mode=0o700)
    parent=ParentCaps.enroll(root/'caps.sqlite',p,clock=lambda:tuple(now),boot_reader=lambda:p.controller_boot_id)
    try:
        op=SelectedModelOperator(now,{u.unit:u.host_boot_id for u in p.units},damage)
        control=Controller(**controller_arguments(n,parent,op,now));request,_=bundle.BundleContext(n['value'],p).exchange(0)
        with pytest.raises(Exception)as refused:control.link('unit-a','unit-c','lora','MR1.request',request)
        assert not isinstance(refused.value,StopBeforeNativeStart)
        assert not control.links[0]['prepared'] and parent.snapshot()['allocations']==1
    finally:parent.close()


@pytest.mark.parametrize('target',['controller','campaign'])
@pytest.mark.parametrize('damage',['clear_both','equal_bytes_substitution','private_choice'])
def test_original_constructor_choice_cannot_fall_back_before_stage_or_prepay(original,tmp_path,model_binding_validation,target,damage):
    n=original;p=n['policy'];now=[p.issued_epoch_us+1000000,p.original_monotonic_us+1000000]
    root=tmp_path/'controller';root.mkdir(mode=0o700)
    parent=ParentCaps.enroll(root/'caps.sqlite',p,clock=lambda:tuple(now),boot_reader=lambda:p.controller_boot_id)
    try:
        before=parent.snapshot()
        if target=='controller':
            value=Controller(**controller_arguments(n,parent,object(),now));private='_Controller__mcu_owner'
        else:
            value=fast.FastBundleCampaign(object(),parent,n['value'],peers={},boots={},
                handoff_deadline_monotonic=p.deadline_monotonic_us/1000000,
                clock=lambda:now[1]/1000000,epoch=lambda:now[0],sleep=lambda _:None,mcu_owner=n['common'])
            private='_FastBundleCampaign__mcu_owner'
        if damage=='private_choice':
            with pytest.raises(RuntimeError,match='constructor_choice_rebound'):setattr(value,private,None)
            with pytest.raises(RuntimeError,match='constructor_choice_rebound'):delattr(value,private)
        else:
            replacement=None if damage=='clear_both'else bytes(bytearray(value._mcu_owner_bytes))
            value._mcu_owner_bytes=value._mcu_owner_ref=replacement
            if target=='controller':value.mcu_artifact_selector=value._mcu_artifact_selector=None
            with pytest.raises(Exception,match='owner_rebound'):value.current()
        assert parent.snapshot()==before
    finally:parent.close()


def test_new_paid_package_cannot_stage_under_legacy_or_pending_config(original,tmp_path,pending_selector_bindings):
    n=original;p=n['policy'];now=p.issued_epoch_us+1000000
    request,_=bundle.BundleContext(n['value'],p).exchange(0)
    pair=pair_packages(campaign_id=n['value']['stages'][0]['campaign_id'],resource_sha256=n['value']['manifest_sha256'],
        application_expires_epoch_us=now+180000000,sender='unit-a',receiver='unit-c',transport='lora',format='MR1.request',
        descriptor=dict(bytes=len(request),sha256=hashlib.sha256(request).hexdigest()),source=None,request=request,
        peers={u.unit:asdict(u.peer)for u in p.units},boots={u.unit:u.host_boot_id for u in p.units},
        now_epoch_us=now,profile='gfsk250',radio_permission=dict(country='MY',antenna_confirmed=True),
        public32=True,mb1_descriptor_sha256=n['value']['transport_descriptor_sha256'],mcu_artifact_selector=mcu.SELECTOR)
    from lantern_meshticulum.bench.pilot_parent_caps import child_caps
    for unit,base in pair['packages'].items():
        package=dict(base,schema=mcu.PAID_SCHEMA,parent=dict(child=child_caps(base,p).to_dict()))
        with pytest.raises(Exception):preparation_schema(dict(schema='meshticulum.finite-ssh-pilot.v1'),package)
        cfg,_=selected_config(n)
        assert preparation_schema(cfg,package)==mcu.PROVISION_SCHEMA
        operator=object.__new__(SSHOperator);operator.config=cfg
        for u in cfg['units']:cfg['units'][u]['host_boot_id']=p.unit(u).host_boot_id
        operator._package=lambda *a,**k:pytest.fail('pending selection must not stage')
        with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):operator.prepare(package)


def test_final_ack_wrapped_owner_still_refuses_missing_native_scope(original,pending_selector_bindings):
    with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):
        fast.require_owner(original['common'],original['policy'])


@pytest.mark.parametrize('damage',[None,'archive_root','provision_artifact','child_context','mac','expired','aftercheck'])
def test_selected_final_ack_holds_actual_runtime_and_preserves_refusal_checks(original,model_binding_validation,monkeypatch,damage):
    """Real readers/source/HMAC; source-only deployment and approval seam."""
    import base64
    import os
    import shutil
    import tempfile
    from meshticulum.bench.wire import BenchGrant
    from meshticulum.contracts.identity import PeerID,SessionKey
    from lantern_meshticulum.bench.profiles import radio_route
    from lantern_meshticulum.bench.pilot_parent_caps import child_caps
    from lantern_meshticulum.bench import pilot_resume_rpc as rpc
    from lantern_meshticulum.ip.node import _write
    from tests.test_public32_pilot import protected_source
    n=original;p=n['policy'];protected=Path(tempfile.mkdtemp(prefix='.mcu-ack-control-',dir=Path(__file__).parent))
    readers=[];initialize=rpc.Reader.__init__
    def capture(reader):initialize(reader);readers.append(reader)
    try:
        runtime,_=protected_source.__wrapped__(protected,monkeypatch,None)
        manifest_path=runtime/'manifest.json';manifest=json.loads(manifest_path.read_bytes());manifest.update(n['manifest'])
        manifest_path.write_bytes(canonical(manifest));manifest_pin=hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        monkeypatch.setattr(fast,'__file__',str(runtime/'lantern_meshticulum/bench/fast_bundle.py'))
        state=protected/'native';state.mkdir(mode=0o700)
        (state/'runs').mkdir(mode=0o700);(state/'parents').mkdir(mode=0o700)
        parent=state/'parents'/p.parent_id;parent.mkdir(mode=0o700);_write(parent/'policy.json',canonical(p.to_dict()))
        issued=p.issued_epoch_us+1000000;expiry=issued+180000000
        request,_=bundle.BundleContext(n['value'],p).exchange(0)
        pair=pair_packages(campaign_id=n['value']['stages'][0]['campaign_id'],resource_sha256=n['value']['manifest_sha256'],
            application_expires_epoch_us=expiry,sender='unit-a',receiver='unit-c',transport='lora',format='MR1.request',
            descriptor=dict(bytes=len(request),sha256=hashlib.sha256(request).hexdigest()),source=None,request=request,
            peers={u.unit:asdict(u.peer)for u in p.units},boots={u.unit:u.host_boot_id for u in p.units},
            now_epoch_us=issued,profile='gfsk250',radio_permission=dict(country='MY',antenna_confirmed=True),
            public32=True,mb1_descriptor_sha256=n['value']['transport_descriptor_sha256'],mcu_artifact_selector=mcu.SELECTOR)
        package=pair['packages']['unit-c'];plan=deepcopy(package['grant']);child=child_caps(package,p).to_dict()
        deadline=p.original_monotonic_us+expiry-p.issued_epoch_us;plan['application_deadline_monotonic_us']=deadline
        run=plan['session_id'];directory=state/'runs'/run;directory.mkdir(mode=0o700)
        claim=dict(parent_id=p.parent_id,prepared=True,native_plan_sha256=hashlib.sha256(canonical(plan)).hexdigest(),
            original_plan_sha256=child['metadata']['plan_sha256'],grant_context_sha256=child['metadata']['grant_digest'],
            child_caps=child,secret_sha256=child['metadata']['secret_sha256'],application_deadline_monotonic_us=deadline)
        provision=dict(schema=mcu.PROVISION_SCHEMA,unit='unit-c',run_id=run,parent=claim,mcu_artifact=mcu.artifact())
        owner=deepcopy(n['owner']);owner.update(manifest_sha256=manifest_pin,runtime_sha256='7'*64)
        if damage=='archive_root':owner['runtime_sha256']='6'*64
        if damage=='provision_artifact':provision['mcu_artifact']['image_sha256']='0'*64
        if damage=='child_context':claim['grant_context_sha256']='0'*64
        _write(parent/'owner.json',canonical(mcu.owner_envelope(owner,mcu.SELECTOR)))
        _write(directory/'grant.json',canonical(plan));_write(directory/'provision.json',canonical(provision))
        secret=base64.b64decode(package['secret_base64'],validate=True)
        _write(directory/'secret.bin',secret);_write(directory/'active',b'active\n')
        grant=BenchGrant(SessionKey(PeerID(**plan['initiator']),PeerID(**plan['responder']),bytes.fromhex(run),bytes.fromhex(plan['generation'])),
            'bench_transfer',hashlib.sha256(canonical(radio_route(plan))).digest(),plan['issued_epoch_us'],plan['expires_epoch_us'],208,plan['max_frames'],secret)
        meta=child['metadata'];proof=fast._proof(dict(parent_id=p.parent_id,policy_digest=p.digest,
            bundle_context_sha256=p.bundle_context_sha256,part_index=0,campaign_id=meta['campaign_id'],session_id=run,
            generation=plan['generation'],grant_context_sha256=grant.digest.hex(),service_format='MR1.request',
            object=dict(meta['object']),application_expires_epoch_us=expiry,
            transport_descriptor_sha256=n['value']['transport_descriptor_sha256'],schema=fast.FINAL_ACK),secret)
        if damage=='mac':proof['mac']='0'*64
        now=(expiry,deadline)if damage=='expired'else(issued,p.original_monotonic_us+1000000)
        if damage=='aftercheck':
            check=rpc.Runtime.check;checks=[]
            def checked(runtime):
                result=check(runtime);checks.append(result)
                if len(checks)==3:raise RuntimeError('modeled_aftercheck_refusal')
                return result
            monkeypatch.setattr(rpc.Runtime,'check',checked)
        monkeypatch.setattr(rpc.Reader,'__init__',capture)
        args=dict(state=state,clock=lambda:now,facts=dict(host_boot_id=p.unit('unit-c').host_boot_id))
        if damage is None:
            result=fast.deliver_final_ack('unit-c',run,proof,**args)
            assert result['delivered']is True and result['complete']is False and result['radio_opened']is False
            assert json.loads((directory/'fast-final-ack.json').read_bytes())==proof
        else:
            with pytest.raises(Exception):fast.deliver_final_ack('unit-c',run,proof,**args)
            assert (directory/'fast-final-ack.json').exists()is(damage=='aftercheck')
        assert len(readers)==2 and all(r._closed for r in readers)
        for reader in readers:
            for fd in reader._tokens:
                with pytest.raises(OSError):os.fstat(fd)
    finally:
        for reader in readers:reader.close()
        shutil.rmtree(protected)
