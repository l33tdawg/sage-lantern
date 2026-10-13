# SPDX-License-Identifier: Apache-2.0
"""Explicit five-part MB1 GFSK250 operation; all physical bindings start pending.

No installation/flash/reset/private activation. One immutable operation permits
one parent, one signed100000-byte file, five original part scopes and twenty prepared endpoints.
"""
import argparse
from dataclasses import asdict
import getpass
import importlib.util
import json
import lzma
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from run_public_sf5_c_bulk_8k import read,write,sha,canonical,stable,need,RPC_ENTRY

SCHEMA='meshticulum.public-gfsk250-mb1-bundle100k-operation.v1'
# Full H34 C metadata plus the signed five-part schedule exceeds the old
# 84k envelope. Only this explicit whole-file entry uses the bounded 91k cap.
need(RPC_ENTRY.count("need(len(blob)<=112000,'RPC_encoded_bound')")==1
     and RPC_ENTRY.count("need(len(compressed)<=84000,'RPC_compressed_bound')")==1,
     'bundle_RPC_template_changed')
BUNDLE_RPC_ENTRY=RPC_ENTRY.replace("need(len(blob)<=112000,'RPC_encoded_bound')",
    "need(len(blob)<=121336,'RPC_encoded_bound')").replace(
    "need(len(compressed)<=84000,'RPC_compressed_bound')",
    "need(len(compressed)<=91000,'RPC_compressed_bound')")


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def phase_timing_mode(op):
    value=op.get('native_phase_timing',False)
    need(type(value) is bool,'invalid_native_phase_timing_mode')
    return value


def review(path,pin):
    raw=read(path,2000000);need(sha(raw)==pin,'operation_pin_changed');op=json.loads(raw)
    need(op['schema']==SCHEMA and op['runner_sha256']==sha(read(Path(__file__),100000)), 'wrong_batch_operation')
    phase_timing_mode(op)
    need(op.get('require_same_boot_gfsk250_capability') is True
        and op.get('first_inspect_before_claims') is True,'gfsk250_first_inspection_required')
    need(op['phase_seconds']==1355 and op['whole_seconds']==1350 and op['application_seconds']==180 and op['part_count']==5,'batch_original_limits_changed')
    auth=op['authorization'];need(auth==dict(human_statement='run it bro',count_authorized=1,public_only=True,private_production=False), 'batch_authorization_missing')
    core,lantern=Path(op['core_path']).absolute(),Path(op['lantern_path']).absolute()
    for folder,pin in ((core,op['core_commit']),(lantern,op['lantern_commit'])):
        need(subprocess.check_output(['git','-C',str(folder),'rev-parse','HEAD'],text=True).strip()==pin
             and not subprocess.check_output(['git','-C',str(folder),'status','--porcelain'],text=True),'batch_source_changed')
    sys.path[:0]=[str(core/'src'),str(lantern/'software/meshticulum-integration/src')]
    from lantern_meshticulum.bench import retained_capture
    from lantern_meshticulum.bench.gfsk250_mb1_bundle import require_bindings
    from lantern_meshticulum.bench.profiles import GFSK250_FIRMWARE as GFSK_FIRMWARE
    bindings=require_bindings()
    need(op['core_commit']==bindings['core_commit'],'mb1_exact_core_required')
    # Exact caller-supplied runtime/manifest pins are authenticated below by
    # the preflight and archive. They cannot be constants inside that archive.
    need(all(type(op[k]) is str and len(op[k])==64 and all(c in '0123456789abcdef' for c in op[k])
        for k in ('runtime_sha256','manifest_sha256')),'mb1_exact_runtime_manifest_required')
    need(op['history_receipt_sha256']==bindings['history_receipt_sha256'],'gfsk_exact_history_receipt_required')
    need(op['mb1_bundle_scope_sha256']==bindings['scope_sha256'],'exact_whole_bundle_scope_required')
    absolute=op['absolute_device_window']
    need(type(absolute['issued_epoch_us']) is int and type(absolute['expires_epoch_us']) is int
         and 0<absolute['expires_epoch_us']-absolute['issued_epoch_us']<=1500000000
         and absolute['count_authorized']==1,'batch_absolute_window_not_finite')
    need(op['history_parent_ids']==list(retained_capture.PAID_HISTORY),'batch_full_history_required')
    need(set(op['controller_directories'])==set(op['history_parent_ids']),'batch_controller_roster_changed')
    history=Path(op['history_path']).absolute();receipt_raw=read(history/'RECEIPT.json',100000)
    need(sha(receipt_raw)==op['history_receipt_sha256'],'batch_history_receipt_changed');receipt=json.loads(receipt_raw)
    need(type(receipt['local_controllers_reobserved']) is int
        and receipt['local_controllers_reobserved']==len(retained_capture.PAID_HISTORY)
        and receipt['no_refund'] is True,'batch_full_old_debt_required')
    proof_raw=read(op['preflight_completion'],2000000);need(sha(proof_raw)==op['preflight_completion_sha256'],'batch_preflight_changed');proof=json.loads(proof_raw)
    need(proof['complete'] is True and proof['archive_sha256']==op['runtime_sha256'] and proof['manifest_sha256']==op['manifest_sha256']
         and proof['history_receipt_sha256']==op['history_receipt_sha256'] and proof['callbacks_exit_zero']==3
         and proof['owned_local_clients_terminal'] is True and proof['native_state_writes'] is False and proof['radio_opened'] is False,
         'batch_exact_preflight_required')
    helpers=load('reviewed_batch_helpers',lantern/'firmware/meshticulum-bench/run_public_pilot.py')
    import io,tarfile
    archive=read(op['runtime_archive'],2000000);need(sha(archive)==op['runtime_sha256'],'batch_archive_changed')
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
        mf=tar.extractfile('manifest.json').read();need(sha(mf)==op['manifest_sha256'],'batch_manifest_changed');manifest=json.loads(mf)
        need(manifest['firmware_by_unit']['unit-a']==GFSK_FIRMWARE and manifest['firmware_by_unit']['unit-b']=='mtb1-9816f92488','gfsk_firmware_manifest_required')
        need(manifest['private_services_enabled'] is False and manifest['public_parent_claims_required'] is True,'batch_public_runtime_required')
        names=[m.name for m in tar.getmembers()]
        need(len(names)==len(set(names)) and set(names)==set(manifest['files'])|{'manifest.json'}
             and all(m.isfile() and not Path(m.name).is_absolute() and '..' not in Path(m.name).parts for m in tar.getmembers()),
             'batch_archive_namespace_changed')
        for name,pin in manifest['files'].items():
            data=tar.extractfile(name).read();need(sha(data)==pin,'batch_member_changed')
            source=core/'src'/name if name.startswith('meshticulum/') else lantern/'software/meshticulum-integration/src'/name if name.startswith('lantern_meshticulum/') else None
            if source is not None:need(sha(read(source,500000))==pin,'batch_source_closure_changed')
    need(manifest['files']['lantern_meshticulum/bench/pilot_resume_rpc.py']==op['native_adapter_sha256'],'batch_native_adapter_changed')
    required={'firmware/meshticulum-bench/run_public_pilot.py','firmware/meshticulum-bench/pilot_grant_bootstrap.py',
        'firmware/meshticulum-bench/runtime_bootstrap.py','firmware/meshticulum-bench/run_public_sf5_c_bulk_8k.py',
        'firmware/meshticulum-bench/pair_launch_guard_sender_first.py',
        'firmware/meshticulum-bench/management_file_rpc.py'}
    need(required<=set(op['source_pins']),'batch_support_pins_missing')
    for name,pin in op['source_pins'].items():
        need(not Path(name).is_absolute() and '..' not in Path(name).parts,'batch_support_path_changed')
        need(sha(read(lantern/name,500000))==pin,'batch_support_source_changed')
    need(sha(read(op['rf_confirmation_path'],20000))=='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9','batch_rf_consent_changed')
    need(type(op['fixture']) is dict and set(op['fixture'])=={'path','sha256'},'bundle_fixture_required')
    return op,core,lantern,manifest,receipt,helpers


def fixture(op):
    from meshticulum.resources import lamplighter as ll,byte_bundle as bb,resource
    from lantern_meshticulum.bench.pilot_bundle_transport import MB1Transport,KIND as TRANSPORT_KIND
    path=Path(op['fixture']['path']).absolute();raw=read(path/'FIXTURE.json',50000)
    need(sha(raw)==op['fixture']['sha256'],'whole_fixture_changed');f=json.loads(raw)
    need(f['schema']=='meshticulum.synthetic-public100k-gfsk250-mb1-fixture.v1'
         and f['transport_kind']==TRANSPORT_KIND
         and f['public_only'] is True and f['private_data'] is False
         and f['model_or_evaluation_content'] is False and f['licence']=='CC0-1.0'
         and f['publisher_private_key_persisted_or_exported'] is False
         and f['native_admission'] is False and f['rf_activation'] is False,'public_whole_fixture_required')
    trust_raw=read(path/'trust.json',16384);trust=json.loads(trust_raw)
    need(sha(trust_raw)==f['trust_sha256'] and sha(read(path/'pack/manifest.sig.json',4096))==f['signature_sha256'],
         'whole_fixture_publisher_metadata_changed')
    baseline=op['baseline_fixture'];need(type(baseline) is dict and set(baseline)=={'path','sha256'},'baseline_fixture_required')
    braw=read(Path(baseline['path'])/'FIXTURE.json',50000)
    need(sha(braw)==baseline['sha256']==f.get('baseline_fixture_sha256'),'baseline_fixture_changed')
    previous=json.loads(braw)
    need(previous['schema']=='meshticulum.synthetic-public100k-gfsk125-mb1-fixture.v1'
        and previous['whole_bytes']==f['whole_bytes']==100000
        and previous['whole_sha256']==f['whole_sha256']
        and previous['manifest_sha256']!=f['manifest_sha256'],'same_content_fresh_namespace_required')
    cache=ll.VerifiedCache(path/'pack',trust,f['manifest_sha256'])
    whole=bb.descriptor(cache.value);need(whole['bytes']==100000 and len(whole['parts'])==5,'exact100000_signed_file_required')
    need(f['whole_bytes']==whole['bytes'] and f['whole_sha256']==whole['sha256'],'whole_fixture_descriptor_changed')
    descriptor_raw=read(path/'TRANSPORT.json',65536)
    transport=MB1Transport(descriptor_raw,f['transport_descriptor_sha256'],cache.raw,cache.signature,trust,f['manifest_sha256'])
    need(len(f['parts'])==5,'exact_five_mb1_fixture_parts_required')
    replies=[]
    for part in whole['parts']:
        index=part['index'];request=transport.request(index);reply=transport.response(index,cache)
        expected=dict(index=index,decoded_bytes=part['raw_bytes'],chunk_sha256=part['chunk_sha256'],
            request_bytes=len(request),response_bytes=len(reply),response_sha256=sha(reply))
        need(f['parts'][index]==expected and len(reply)<32768,'exact_mb1_fixture_part_required')
        replies.append(reply)
    return dict(path=path,fixture=f,trust=trust,cache=cache,whole=whole,responses=replies,transport=transport)


def planned_stages(epoch):
    # Fresh schedule only: first stage follows full capture/setup. No existing
    # context or deadline is renewed; whole1350/phase1355/stages180 stay fixed.
    return [dict(index=i,campaign_id=secrets.token_hex(16),issued_epoch_us=epoch+(150 if i==0 else 105+i*240)*1000000,
        expires_epoch_us=epoch+(330 if i==0 else 285+i*240)*1000000,
        handoff_end_epoch_us=epoch+(340 if i==0 else 345+i*240 if i<4 else 1255)*1000000) for i in range(5)]


def wait_for_stage(current,issued_epoch_us,epoch,sleep):
    """A fast prior part does not move the next original issued time."""
    now=epoch()
    while now<issued_epoch_us:
        current.current();sleep(min(.1,(issued_epoch_us-now)/1000000));now=epoch()
    current.current()


def require_retired(links,cleanup,*,completed=False):
    """Returned cleanup errors remain fatal even if a later retry succeeds."""
    need(type(cleanup) is list and all(type(row) is dict and 'retirement_error' not in row
         and 'stop_error' not in row for row in cleanup),'batch_retirement_errors')
    for link in links:
        attempted,prepared,retired=link['attempted'],link['prepared'],link['retired']
        expected=set(attempted)|set(prepared)
        need(len(attempted)==len(set(attempted)) and len(prepared)==len(set(prepared))
             and set(prepared)<=set(attempted) and len(retired)==len(set(retired))
             and set(retired)==expected,'batch_endpoint_retirement_unproved')
        if completed:
            need(expected=={'unit-a','unit-c'} and set(prepared)==expected
                 and link['phase']=='complete','batch_sample_retirement_scope_changed')
        for unit in expected:
            matches=[row for row in cleanup if row.get('unit')==unit and row.get('run_id')==link['run_id']]
            need(matches and all(type(row.get('retirement')) is dict
                 and row['retirement'].get('retired') is True and row['retirement'].get('unit')==unit
                 and row['retirement'].get('run_id')==link['run_id'] for row in matches),
                 'batch_endpoint_retirement_receipt_missing')


def cleanup_current(current,result,*,completed=False):
    cleanup=json.loads(canonical(current.retire()));result['cleanup']=cleanup
    try:require_retired(current.links,cleanup,completed=completed)
    except Exception:
        result['complete']=False;result['cleanup_error']='current_controller_cleanup_unproved'


def native_callback_seconds(action,phase_end_epoch_us,now_epoch_us):
    """Setup capture gets room for both seals; the original phase never moves."""
    cap=120 if action=='capture' else 45
    left=min(cap,max(0,(phase_end_epoch_us-now_epoch_us)//1000000-8))
    need(left>0,'batch_original_remote_window_expired')
    return left


def rpc_command(operator,cfg,op,unit,action,payload,seconds,*,operation_sha256=None):
    """Explicit MB1 management-file lane; existing JSON/argv runners stay unchanged."""
    from management_file_rpc import command as file_command
    from lantern_meshticulum.bench.management_input import parameters
    need(type(seconds) is int and 0<seconds<=120,'batch_file_callback_bound')
    nonce=secrets.token_hex(16);now=time.time_ns()//1000;local_start=operator.clock()
    end=min(cfg['handoff_end_epoch_us'],now+seconds*1000000)
    local_deadline=min(operator.deadline,local_start+max(0,(end-now)/1000000))
    parameters(unit,action,cfg['bundle_parent_id'],operation_sha256,nonce,end)
    staged=operator.stage_management_input(unit,action,payload,parent_id=cfg['bundle_parent_id'],
        operation_sha256=operation_sha256,nonce=nonce,original_end_epoch_us=end)
    try:
        values=file_command('/opt/meshticulum-bench/runtime-'+op['runtime_sha256'][:16],op['manifest_sha256'],
            op['native_adapter_sha256'],staged,cfg['units'][unit]['python'],seconds)
        need(operator.clock()<local_deadline and time.time_ns()//1000<end,'batch_file_original_window_expired')
    except BaseException:
        cleanup_rpc_input(operator,cfg,op,staged);raise
    return FileRPCCommand(values,staged,end,local_deadline)


class FileRPCCommand(list):
    """Per-call descriptor; no shared mutable input slot across prepare threads."""
    def __init__(self,values,staged,end,local_deadline):
        super().__init__(values);self.staged=staged;self.original_end_epoch_us=end;self.local_deadline=local_deadline


def cleanup_rpc_input(operator,cfg,op,staged):
    from management_file_rpc import command as file_command
    command=file_command('/opt/meshticulum-bench/runtime-'+op['runtime_sha256'][:16],op['manifest_sha256'],
        op['native_adapter_sha256'],staged,cfg['units'][staged['unit']]['python'],5,cleanup=True)
    value=operator._command(staged['unit'],command,'meshticulum.management-input-cleaned.v1',root=True,cleanup=True,
        cleanup_transport_headroom=2)
    need(value['cleaned'] is True and value['unit']==staged['unit'] and value['nonce']==staged['nonce']
        and value['native_dispatch_called'] is False,'batch_management_input_cleanup_unproved')
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--operation',type=Path,required=True);parser.add_argument('--operation-sha256',required=True);parser.add_argument('--check',action='store_true');args=parser.parse_args();os.umask(0o077)
    op,core,lantern,manifest,receipt,helpers=review(args.operation,args.operation_sha256);piece=fixture(op)
    from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,ParentCaps,UnitCaps,Cost
    from lantern_meshticulum.bench.pilot_parent_snapshot import Scope,collect
    from lantern_meshticulum.bench.pilot_ssh import SSHOperator,Job,one_record
    from lantern_meshticulum.bench.pilot_controller import Controller
    from lantern_meshticulum.bench.pilot_bundle import BundleContext,BundleCampaign,context_value,public_context_commitment,COMMITTED_CONTRACTS
    from lantern_meshticulum.bench.profiles import GFSK250_MB1_BUNDLE_KIND as KIND
    from lantern_meshticulum.bench.gfsk_bundle import fresh_boot_status
    from lantern_meshticulum.bench.profiles import profile_for,profile_record,GFSK250_NAME as GFSK_NAME
    from meshticulum.contracts.identity import PeerID
    from run_public_sf5_c_bulk_8k import retained_controller_caps
    histories={u:json.loads(read(Path(op['history_path'])/(u+'.json'),2000000)) for u in ('unit-a','unit-b','unit-c')}
    for u,h in histories.items():need(sha(read(Path(op['history_path'])/(u+'.json'),2000000))==receipt['payload_sha256'][u],'batch_history_bytes_changed')
    if args.check:print(json.dumps(dict(source_checked=True,decoded_file_bytes=100000,parts=5,radio_opened=False)));return
    controllers=[];projection=('status','scoped_rows_complete','pending_unknown','policy_row_sha256','prior_lineage','detail','files')
    for i,pid in enumerate(op['history_parent_ids']):
        directory=Path(op['controller_directories'][pid]);policy=ParentPolicy.from_dict(json.loads(read(directory/'POLICY.json')))
        old=json.loads(read(Path(op['history_path'])/pid/'controller.json',2000000));scope=Scope('meshticulum.public-parent-snapshot-scope.v1','controller',os.geteuid(),pid,policy.digest,old['expected_database_sha256'],None,helpers.controller_boot(),time.time_ns()//1000+45000000)
        actual=collect(directory,scope,clock=helpers.parent_clock,boot_reader=helpers.controller_boot).to_dict()
        need(actual['status']=='scoped_rows_observed' and actual['scoped_rows_complete'] and actual['policy_expired_at_observation']
            and not actual['pending_unknown'] and canonical({k:actual[k] for k in projection})==canonical({k:old[k] for k in projection}),'batch_old_controller_changed')
        for u in histories:retained_controller_caps(u,histories[u]['previous'][i],actual)
        controllers.append(actual)
    cfg=json.loads(read(op['config_path'],30000));need(cfg['runtime_sha256']==op['runtime_sha256'] and cfg['manifest_sha256']==op['manifest_sha256'],'batch_installed_config_changed')
    provision=read(lantern/'firmware/meshticulum-bench/pilot_grant_bootstrap.py',500000);bootstrap=read(lantern/'firmware/meshticulum-bench/runtime_bootstrap.py',500000)
    need(cfg['provision_sha256']==sha(provision) and cfg['runtime_bootstrap_sha256']==sha(bootstrap),'batch_native_support_pin_changed')
    password=getpass.getpass('A/B sudo for one finite public100k whole-file transfer (memory only): ')
    epoch,mono=helpers.parent_clock();end=epoch+1355000000;absolute=op['absolute_device_window'];need(absolute['issued_epoch_us']<=epoch and end<=absolute['expires_epoch_us'],'batch_original_absolute_window_unavailable')
    cfg['handoff_end_epoch_us']=end;parent_id=secrets.token_hex(16);out=Path(op['output_directory'])/('gfsk250-mb1-bundle100k-'+parent_id);out.mkdir(mode=0o700)
    used=Path(op['output_directory'])/('OPERATION_USED-'+args.operation_sha256+'.json');write(used,dict(parent_id=parent_id,operation_sha256=args.operation_sha256,original_phase_end_epoch_us=end,count_used=1))
    result=dict(schema='meshticulum.public-gfsk250-mb1-bundle100k-result.v1',parent_id=parent_id,complete=False,qualification_complete=False,private_production=False,refunds=0,original_phase_start_epoch_us=epoch,original_phase_end_epoch_us=end,bundle_parts=[],rpc_clients=[],links=[],native_final={},previous_parent_ids=op['history_parent_ids'],native_phase_timing_enabled=phase_timing_mode(op))
    stages=planned_stages(epoch)
    mraw=read(piece['path']/'pack/manifest.json',65536);sraw=read(piece['path']/'pack/manifest.sig.json',4096)
    chunks={p['chunk_sha256']:piece['cache'].piece(p['chunk_sha256']) for p in piece['whole']['parts']}
    bundle_context=context_value(mraw,sraw,piece['trust'],piece['fixture']['manifest_sha256'],chunks=chunks,
        policy_clock=(epoch,mono,epoch+1350000000,mono+1350000000),stages=stages,qualification_kind=KIND)
    need(bundle_context['transport_descriptor_sha256']==piece['fixture']['transport_descriptor_sha256'],
        'mb1_context_transport_descriptor_changed')
    commitment=public_context_commitment(bundle_context,parent_id=parent_id)
    cfg.update(schema=COMMITTED_CONTRACTS[KIND][5],bundle_parent_id=parent_id,bundle_context=bundle_context,
        bundle_capture_commitment=commitment,handoff_end_epoch_us=end)
    operator=parent=current=None;owned=[];owners={};priors={};preparations={u:{} for u in histories};facts={}
    def native(u,action,payload):
        left=native_callback_seconds(action,end,time.time_ns()//1000)
        command=rpc_command(operator,cfg,op,u,action,payload,left,operation_sha256=args.operation_sha256)
        job=None;audit=dict(unit=u,action=action,remote_terminal=False,local_terminal=False);result['rpc_clients'].append(audit)
        try:
            argv=operator._argv(u,command,root=True);need(len(argv[-1].encode())<125000,'batch_native_argv_bound')
            job=Job(argv,password=operator.passwords.get(u),deadline=min(command.local_deadline,operator.deadline),clock=operator.clock);owned.append(job)
            need(job.wait()==0,'batch_native_RPC_failed');value=one_record(bytes(job.output),'meshticulum.private-resume-native-rpc.v1')
            need(value['unit']==u and value['action']==action and value['radio_opened'] is False and value['private_production'] is False,'batch_native_scope_changed');audit['remote_terminal']=True;return value
        finally:
            try:
                if job is not None:
                    job.close();audit['local_terminal']=job.process.poll() is not None;audit['returncode']=job.process.returncode
            finally:
                original=sys.exc_info()[1]
                try:
                    cleanup=cleanup_rpc_input(operator,cfg,op,command.staged)
                    audit['management_input_cleanup_confirmed']=True;audit['management_input_already_absent']=cleanup['already_absent']
                except BaseException:
                    audit['management_input_cleanup_confirmed']=False
                    if original is None:raise
                    original.add_note('batch_management_input_cleanup_unproved')
    def guarded(u,action='guard'):
        return native(u,action,dict(policy=policy.to_dict(),owner=owners[u],prior=priors[u],preparation_sha256=preparations[u],review_sha256=args.operation_sha256,expected_cut_run=None))
    try:
        operator=SSHOperator(cfg,provision_source=provision,runtime_source=bootstrap,passwords={'unit-a':password,'unit-b':password},parallel_preparation=True,
            native_phase_timing=phase_timing_mode(op));password=None;operator.open_connections()
        inspection=operator._action('unit-a','inspect',schema='meshticulum.native-bench-inspection.v1');radio=inspection['radio'];expected=op['radio_status_expected']
        capability=inspection.get('radio_capability',{})
        need(capability.get('verified') is True and capability.get('radio_modem')==GFSK_NAME
            and capability.get('record_version')==2 and capability.get('mcu_boot')==radio['mcu_boot']
            and capability.get('firmware')==radio['firmware'],'same_boot_gfsk250_capability_required')
        need(radio['disarmed'] is True and radio['firmware']==manifest['firmware_by_unit']['unit-a'] and radio['usb_serial']=='F8:5B:1B:A5:F6:08'
            and all(radio[k]==expected[k] for k in ('mcu_boot','boot_attempts','boot_reserved_estimated_airtime_us'))
            and 256-radio['boot_attempts']==256 and inspection['arm_commands']==inspection['tx_commands']==0,'batch_A_fresh_status_changed');result['pre_run_radio_status']=inspection;fresh_boot_status(radio)
        import base64
        f=piece['fixture'];wanted=[p['chunk_sha256'] for p in piece['whole']['parts']]
        public=dict(manifest=base64.b64encode(mraw).decode(),signature=base64.b64encode(sraw).decode(),
            trust=base64.b64encode(read(piece['path']/'trust.json',16384)).decode(),
            pins=dict(manifest=f['manifest_sha256'],signature=f['signature_sha256'],trust=f['trust_sha256']),wanted=wanted,recipient='unit-a')
        absent=native('unit-a','observe-cache',public)['observation']
        result['pre_run_cache_observation']=absent
        need(not absent['verified_present'] and absent['absent']==sorted(wanted),'whole_fixture_already_present')
        for unit,role in (('unit-c','source'),('unit-a','recipient')):
            operator.seed(helpers.seed_package(cfg,unit,role,piece['path']/'pack',piece['trust'],f['manifest_sha256'],piece['cache'].value))
        facts={u:stable(operator.facts(u)) for u in histories};captures={}
        for u in ('unit-c','unit-b','unit-a'):
            captures[u]=native(u,'capture',dict(parent_id=parent_id,history=histories[u],facts=facts[u],phase_start_epoch_us=epoch,phase_end_epoch_us=end,review_sha256=args.operation_sha256,qualification_kind=KIND,bundle_context=bundle_context,bundle_capture_commitment=commitment))
            cap=captures[u];need(cap['actual_history_verified'] is True and cap['refunds']==0,'batch_capture_unqualified')
            priors[u]=dict(schema='meshticulum.public-resume-conservative-prior.v1',history_binding_sha256=cap['history_binding_sha256'],legacy_inventory=histories[u]['legacy_inventory'],previous_parent_ids=op['history_parent_ids'],full_caps=histories[u]['previous'][-1]['paid_caps'],complete_global_debt=False,actual_cost=None,refunds=0)
        result['captures']=captures
        registry={u:dict(observation_sha256=sha(canonical(priors[u])),history_binding_sha256=captures[u]['history_binding_sha256'],full_caps=priors[u]['full_caps']) for u in histories};units=[]
        peers={u:next(v['peer'] for v in histories[u]['previous'][-1]['policy']['units'] if v['unit']==u) for u in histories}
        profile=profile_for(GFSK_NAME)
        extra={u:Cost() if u=='unit-b' else Cost(2*5*profile.max_boot_attempts,2*5*profile.max_boot_estimated_airtime_us) for u in histories}
        for u in histories:
            old=Cost(**priors[u]['full_caps']);units.append(UnitCaps(u,cfg['units'][u]['host_boot_id'],PeerID(**peers[u]),old.plus(extra[u]),old,sha(canonical(priors[u]))))
        aggregate=Cost()
        for unit in units:aggregate=aggregate.plus(unit.limits)
        policy=ParentPolicy(parent_id,helpers.controller_boot(),epoch,end-5000000,mono,mono+1350000000,tuple(units),aggregate,sha(canonical(registry)),KIND,bundle_context_sha256=sha(canonical(bundle_context)))
        owner=dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind=KIND,policy_digest=policy.digest,authorization=op['authorization'],review_sha256=args.operation_sha256,prior_registry=registry,facts=facts,phase_start_epoch_us=epoch,phase_end_epoch_us=end,native_phase={u:captures[u]['native_phase'] for u in histories},runtime_sha256=op['runtime_sha256'],manifest_sha256=op['manifest_sha256'],rf_record=json.loads(read(op['rf_confirmation_path'],20000)),rf_record_sha256='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9',rf_profile=profile_record(profile_for(GFSK_NAME)),radio_profile_name=GFSK_NAME,radio_modem=GFSK_NAME,mb1_bundle_scope_sha256=op['mb1_bundle_scope_sha256'],core_commit=op['core_commit'],history_receipt_sha256=op['history_receipt_sha256'],bundle_context=bundle_context,bundle_capture_commitment=commitment,complete_global_debt=False,actual_cost=None)
        context=BundleContext(bundle_context,policy);owners={u:dict(owner,history_binding_sha256=captures[u]['history_binding_sha256'],capture_record_sha256=captures[u]['capture_record_sha256']) for u in histories}
        write(out/'POLICY.json',policy.to_dict());write(out/'OWNER_APPROVAL.json',owners);write(out/'PRIOR.json',priors);write(out/'CONFIG.json',cfg);write(out/'OPERATION.json',op)
        parent=ParentCaps.enroll(out/'caps.sqlite',policy,clock=helpers.parent_clock,boot_reader=helpers.controller_boot)
        for u in ('unit-c','unit-b','unit-a'):native(u,'enroll',dict(policy=policy.to_dict(),owner=owners[u],prior=priors[u]))
        original_prepare=operator.prepare
        def prepare(package):
            # Each parallel metadata thread owns its own context; no controller
            # SQLite owner or main-thread retained object crosses this boundary.
            local=BundleContext(bundle_context,policy)
            local.package(package,helpers.parent_clock()[0]);guarded(package['unit']);receipt=original_prepare(package)
            pin=receipt['prepared_identity_sha256'];need(receipt['run_id'] not in preparations[package['unit']],'batch_prepare_reused')
            preparations[package['unit']][receipt['run_id']]=pin;return receipt
        operator.prepare=prepare
        load('batch_launch_guard',lantern/'firmware/meshticulum-bench/pair_launch_guard_sender_first.py').install(operator,guarded,lora_sender_first=True)
        current=BundleCampaign(operator,parent,bundle_context,peers=peers,boots={u:v['host_boot_id'] for u,v in cfg['units'].items()},
            handoff_deadline_monotonic=min(operator.deadline,policy.deadline_monotonic_us/1000000),clock=operator.clock,
            epoch=lambda:helpers.parent_clock()[0],sleep=time.sleep,lora_sender_first=True)
        result['original_whole']=dict(epoch_us=epoch,monotonic_us=mono,expires_epoch_us=policy.expires_epoch_us,deadline_monotonic_us=policy.deadline_monotonic_us)
        for index,response in enumerate(piece['responses']):
            wait_for_stage(current,stages[index]['issued_epoch_us'],lambda:helpers.parent_clock()[0],time.sleep)
            result['bundle_parts'].append(current.transfer_part(index,response))
        result['whole_verification']=current.finish()
        need(result['whole_verification']['complete'] is True and result['whole_verification']['whole_complete'] is True
            and result['whole_verification']['useful_decoded_bytes']==100000
            and result['whole_verification']['whole_file_sha256']==piece['whole']['sha256'],'whole_file_verification_required')
        result['links']=current.links
        need(len(result['links'])==10,'whole_ten_links_required')
        cleanup_current(current,result,completed=True);need(not result.get('cleanup_error'),'whole_controller_cleanup_required');current=None
        for u in ('unit-c','unit-b','unit-a'):
            result['native_final'][u]=guarded(u,'audit');s=result['native_final'][u]['snapshot'];need(s['claims']==s['prepared']==s['starts']==(0 if u=='unit-b' else 10) and s['debt']==asdict(policy.unit(u).limits) and not s['uncertain'],'batch_final_accounting_changed')
        result['complete']=len(result['bundle_parts'])==5 and not result.get('cleanup_error')
    except BaseException as e:result.update(error_type=type(e).__name__,error=str(e)[:200])
    finally:
        password=None
        if current is not None:
            result['links']=current.links
            try:cleanup_current(current,result)
            except BaseException:result['complete']=False;result['cleanup_error']='current_controller_cleanup_unproved'
        if operator is not None:
            for (u,run),job in operator.jobs.items():
                try:need(operator.status(u,run)['live'] is False,'batch_native_terminal_unproved')
                except BaseException:result['complete']=False;result.setdefault('terminal_unproved',[]).append(dict(unit=u,run_id=run))
            for job in list(operator.jobs.values())+owned+operator.management_cleanup_jobs:
                try:operator.reap(job)
                except BaseException:result['complete']=False;result.setdefault('cleanup_errors',[]).append('owned_client_reap_failed')
                finally:job.password=None;job.output.clear()
            result['management_cleanup_commands']=operator.management_cleanup_audits
            result['owned_local_clients_terminal']=all(job.closed and job.process.poll() is not None for job in list(operator.jobs.values())+owned+operator.management_cleanup_jobs)
            if not result['owned_local_clients_terminal'] or not all(v['remote_terminal'] and v['local_terminal'] for v in result['rpc_clients']):result['complete']=False
            try:operator.close_connections()
            except BaseException:result['complete']=False;result.setdefault('cleanup_errors',[]).append('owned_connection_close_failed')
            operator.passwords.clear()
        if parent is not None:
            try:result['controller_parent']=parent.snapshot()
            except BaseException:result['complete']=False;result.setdefault('cleanup_errors',[]).append('controller_snapshot_unproved')
            finally:
                try:need(parent.close(),'batch_parent_close_uncertain')
                except BaseException:result['complete']=False;result.setdefault('cleanup_errors',[]).append('controller_close_unproved')
        result['finished_epoch_us']=time.time_ns()//1000;result['within_original_phase']=result['finished_epoch_us']<end
        if not result['within_original_phase']:result['complete']=False
        result['within_original_whole']=result['finished_epoch_us']<epoch+1350000000 and time.monotonic_ns()//1000<mono+1350000000
        if not result['within_original_whole']:result['complete']=False
        result['decoded_useful_bytes']=100000 if result['complete'] else None
        result['whole_controller_elapsed_us']=(time.monotonic_ns()//1000)-mono
        result['whole_controller_decoded_goodput_Bps']=100000*1000000/result['whole_controller_elapsed_us'] if result['complete'] else None
        result['whole_rate_includes_setup_and_original_schedule_waits']=True
        write(out/'RESULT.json',result);print(json.dumps(dict(complete=result['complete'],saved=str(out/'RESULT.json'),qualification_complete=False)))


if __name__=='__main__':main()
