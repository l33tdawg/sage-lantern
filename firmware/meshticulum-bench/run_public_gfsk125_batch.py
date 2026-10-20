# SPDX-License-Identifier: Apache-2.0
"""Portable exact three-sample GFSK125 operation; Root provides all reviewed paths.

No installation/flash/reset/private activation. One immutable operation permits
one parent, three distinct signed resources and twelve prepared endpoints.
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

from run_public_sf5_c_bulk_8k import read,write,sha,canonical,stable,need,bulk_metrics as _lora_metrics,RPC_ENTRY

SCHEMA='meshticulum.public-gfsk125-three-sample-operation.v1'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def review(path,pin):
    raw=read(path,2000000);need(sha(raw)==pin,'operation_pin_changed');op=json.loads(raw)
    need(op['schema']==SCHEMA and op['runner_sha256']==sha(read(Path(__file__),100000)), 'wrong_batch_operation')
    need(op['phase_seconds']==899 and op['application_seconds']==180 and op['sample_count']==3,'batch_original_limits_changed')
    auth=op['authorization'];need(auth==dict(human_statement='run it bro',count_authorized=1,public_only=True,private_production=False), 'batch_authorization_missing')
    core,lantern=Path(op['core_path']).absolute(),Path(op['lantern_path']).absolute()
    for folder,pin in ((core,op['core_commit']),(lantern,op['lantern_commit'])):
        need(subprocess.check_output(['git','-C',str(folder),'rev-parse','HEAD'],text=True).strip()==pin
             and not subprocess.check_output(['git','-C',str(folder),'status','--porcelain'],text=True),'batch_source_changed')
    sys.path[:0]=[str(core/'src'),str(lantern/'software/meshticulum-integration/src')]
    from lantern_meshticulum.bench import retained_capture
    from lantern_meshticulum.bench.gfsk_batch import validate_descriptor
    from lantern_meshticulum.bench.profiles import require_gfsk_bindings,GFSK_HISTORY_RECEIPT_SHA256,GFSK_FIRMWARE
    require_gfsk_bindings()
    need(op['history_receipt_sha256']==GFSK_HISTORY_RECEIPT_SHA256,'gfsk_exact_history_receipt_required')
    validate_descriptor(op['batch_descriptor'])
    need(op['batch_descriptor']['phase_seconds']==op['phase_seconds'],'batch_phase_descriptor_changed')
    absolute=op['absolute_device_window']
    need(type(absolute['issued_epoch_us']) is int and type(absolute['expires_epoch_us']) is int
         and 0<absolute['expires_epoch_us']-absolute['issued_epoch_us']<=1020000000
         and absolute['count_authorized']==1,'batch_absolute_window_not_finite')
    need(op['history_parent_ids']==list(retained_capture.PAID_HISTORY) and len(op['history_parent_ids'])==35,'batch_full_history_required')
    need(set(op['controller_directories'])==set(op['history_parent_ids']),'batch_controller_roster_changed')
    history=Path(op['history_path']).absolute();receipt_raw=read(history/'RECEIPT.json',100000)
    need(sha(receipt_raw)==op['history_receipt_sha256'],'batch_history_receipt_changed');receipt=json.loads(receipt_raw)
    need(receipt['local_controllers_reobserved']==35 and receipt['no_refund'] is True,'batch_full_old_debt_required')
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
        'firmware/meshticulum-bench/pair_launch_guard_sender_first.py'}
    need(required<=set(op['source_pins']),'batch_support_pins_missing')
    for name,pin in op['source_pins'].items():
        need(not Path(name).is_absolute() and '..' not in Path(name).parts,'batch_support_path_changed')
        need(sha(read(lantern/name,500000))==pin,'batch_support_source_changed')
    need(sha(read(op['rf_confirmation_path'],20000))=='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9','batch_rf_consent_changed')
    need(len(op['fixtures'])==3 and op['batch_descriptor']['sample_count']==3,'batch_fixture_count_changed')
    return op,core,lantern,manifest,receipt,helpers


def fixtures(op):
    from meshticulum.resources import lamplighter as ll,resource
    result=[]
    for i,decl in enumerate(op['fixtures']):
        path=Path(decl['path']).absolute();raw=read(path/'FIXTURE.json',50000);need(sha(raw)==decl['sha256'],'batch_fixture_changed');f=json.loads(raw)
        need(f['public_only'] is True and f['private_data'] is False and len(f['chunks'])==1,'batch_public_fixture_required')
        trust=json.loads(read(path/'trust.json',16384));cache=ll.VerifiedCache(path/'pack',trust,f['manifest_sha256']);piece=f['chunks'][0]
        chunk=cache.entries[piece['content_address_sha256']];request=resource.encode_request('/packs/'+f['manifest_sha256']+'/chunks/'+chunk['sha256']+'.json',0,8164)
        response=resource.BlockServer(cache.read).handle(request);sample=op['batch_descriptor']['samples'][i]
        need(len(request)==74 and len(response)==8192 and piece['chunk_bytes']==8164 and chunk['sha256']==sample['chunk_sha256']
            and f['manifest_sha256']==sample['resource_sha256'] and sha(request)==sample['request']['sha256']
            and sha(response)==sample['response']['sha256'] and piece['response_sha256']==sample['response']['sha256']
            and piece['request_sha256']==sample['request']['sha256']
            and sha(read(path/'trust.json',16384))==f['trust_sha256']
            and sha(read(path/'pack/manifest.sig.json',4096))==f['signature_sha256'],'batch_fixture_descriptor_changed')
        result.append(dict(path=path,fixture=f,trust=trust,cache=cache,piece=piece,request=request,response=response,sample=sample))
    return result


def gfsk_metrics(link,verified,piece):
    from lantern_meshticulum.bench.profiles import profile_for,profile_record,GFSK_NAME
    need(all(v.get('radio_modem')==GFSK_NAME for v in link['outcomes'].values()),'gfsk_metric_modem_unproved')
    metrics=_lora_metrics(link,verified,piece)
    metrics['radio_profile']=profile_record(profile_for(GFSK_NAME))
    metrics['radio_modem']=GFSK_NAME
    metrics['data_span_stretch_target_met']=metrics['stretch_target_met']
    metrics['stretch_target_met']=metrics['useful_encrypted_rf_goodput_Bps']>=5000
    return metrics


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


def finish_sample(operator,current,result,*,index,original_application,verified,metrics):
    """Return None only after this sample's actual retirement is confirmed."""
    cleanup=json.loads(canonical(current.retire()))
    result.setdefault('sample_cleanup',[]).append(dict(index=index,cleanup=cleanup))
    require_retired(current.links,cleanup,completed=True)
    statuses=[operator.status(u,link['run_id']) for link in current.links for u in ('unit-a','unit-c')]
    need(all(v['live'] is False for v in statuses),'batch_sample_child_terminal_unproved')
    need(all(link['phase']=='complete' and all(v['complete'] is True and v['disarmed_on_exit'] is True
         and not v.get('cleanup_errors') for v in link['outcomes'].values()) for link in current.links),
         'batch_sample_cleanup_unproved')
    result['samples'].append(dict(index=index,original_application=original_application,verified=verified,
        metrics=metrics,links=current.links,terminal_statuses=statuses,cleanup=cleanup))
    result['links'].extend(current.links)
    return None


def cleanup_current(current,result):
    cleanup=json.loads(canonical(current.retire()));result['cleanup']=cleanup
    try:require_retired(current.links,cleanup)
    except Exception:
        result['complete']=False;result['cleanup_error']='current_controller_cleanup_unproved'


def native_callback_seconds(action,phase_end_epoch_us,now_epoch_us):
    """Setup capture gets room for both seals; the original phase never moves."""
    cap=120 if action=='capture' else 45
    left=min(cap,max(0,(phase_end_epoch_us-now_epoch_us)//1000000-8))
    need(left>0,'batch_original_remote_window_expired')
    return left


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--operation',type=Path,required=True);parser.add_argument('--operation-sha256',required=True);parser.add_argument('--check',action='store_true');args=parser.parse_args();os.umask(0o077)
    op,core,lantern,manifest,receipt,helpers=review(args.operation,args.operation_sha256);pieces=fixtures(op)
    from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,ParentCaps,UnitCaps,Cost
    from lantern_meshticulum.bench.pilot_parent_snapshot import Scope,collect
    from lantern_meshticulum.bench.pilot_ssh import SSHOperator,Job,one_record
    from lantern_meshticulum.bench.pilot_controller import Controller
    from lantern_meshticulum.bench.gfsk_batch import BatchContext,KIND
    from lantern_meshticulum.bench.profiles import profile_for,profile_record,GFSK_NAME
    from meshticulum.contracts.identity import PeerID
    from run_public_sf5_c_bulk_8k import retained_controller_caps
    histories={u:json.loads(read(Path(op['history_path'])/(u+'.json'),2000000)) for u in ('unit-a','unit-b','unit-c')}
    for u,h in histories.items():need(sha(read(Path(op['history_path'])/(u+'.json'),2000000))==receipt['payload_sha256'][u],'batch_history_bytes_changed')
    if args.check:print(json.dumps(dict(source_checked=True,samples=3,radio_opened=False)));return
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
    password=getpass.getpass('A/B sudo for one finite public three-sample batch (memory only): ')
    epoch,mono=helpers.parent_clock();end=epoch+899000000;absolute=op['absolute_device_window'];need(absolute['issued_epoch_us']<=epoch and end<=absolute['expires_epoch_us'],'batch_original_absolute_window_unavailable')
    cfg['handoff_end_epoch_us']=end;parent_id=secrets.token_hex(16);out=Path(op['output_directory'])/('gfsk125-three-sample-'+parent_id);out.mkdir(mode=0o700)
    used=Path(op['output_directory'])/('OPERATION_USED-'+args.operation_sha256+'.json');write(used,dict(parent_id=parent_id,operation_sha256=args.operation_sha256,original_phase_end_epoch_us=end,count_used=1))
    result=dict(schema='meshticulum.public-gfsk125-three-sample-result.v1',parent_id=parent_id,complete=False,qualification_complete=False,private_production=False,refunds=0,original_phase_start_epoch_us=epoch,original_phase_end_epoch_us=end,samples=[],rpc_clients=[],links=[],native_final={},previous_parent_ids=op['history_parent_ids'])
    operator=parent=current=None;owned=[];owners={};priors={};preparations={u:{} for u in histories};facts={}
    def native(u,action,payload):
        raw=canonical(payload);blob=lzma.compress(raw,format=lzma.FORMAT_XZ,preset=6|lzma.PRESET_EXTREME);need(len(raw)<=2000000 and len(blob)<=84000,'batch_RPC_bound')
        import base64
        left=native_callback_seconds(action,end,time.time_ns()//1000)
        command=['/usr/bin/timeout','--kill-after=2s',str(left)+'s',cfg['units'][u]['python'],'-I','-B','-c',RPC_ENTRY,'/opt/meshticulum-bench/runtime-'+op['runtime_sha256'][:16],op['manifest_sha256'],op['native_adapter_sha256'],action,u,base64.b64encode(blob).decode(),sha(raw)]
        argv=operator._argv(u,command,root=True);need(len(argv[-1].encode())<125000,'batch_native_argv_bound');job=Job(argv,password=operator.passwords.get(u),deadline=min(operator.clock()+left+2,operator.deadline),clock=operator.clock);owned.append(job)
        audit=dict(unit=u,action=action,remote_terminal=False,local_terminal=False);result['rpc_clients'].append(audit)
        try:
            need(job.wait()==0,'batch_native_RPC_failed');value=one_record(bytes(job.output),'meshticulum.private-resume-native-rpc.v1')
            need(value['unit']==u and value['action']==action and value['radio_opened'] is False and value['private_production'] is False,'batch_native_scope_changed');audit['remote_terminal']=True;return value
        finally:job.close();audit['local_terminal']=job.process.poll() is not None;audit['returncode']=job.process.returncode
    def guarded(u,action='guard'):
        return native(u,action,dict(policy=policy.to_dict(),owner=owners[u],prior=priors[u],preparation_sha256=preparations[u],review_sha256=args.operation_sha256,expected_cut_run=None))
    try:
        operator=SSHOperator(cfg,provision_source=provision,runtime_source=bootstrap,passwords={'unit-a':password,'unit-b':password},parallel_preparation=True);password=None;operator.open_connections()
        inspection=operator._action('unit-a','inspect',schema='meshticulum.native-bench-inspection.v1');radio=inspection['radio'];expected=op['radio_status_expected']
        need(radio['disarmed'] is True and radio['firmware']==manifest['firmware_by_unit']['unit-a'] and radio['usb_serial']=='F8:5B:1B:A5:F6:08'
            and all(radio[k]==expected[k] for k in ('mcu_boot','boot_attempts','boot_reserved_estimated_airtime_us'))
            and 256-radio['boot_attempts']>=160 and inspection['arm_commands']==inspection['tx_commands']==0,'batch_A_fresh_status_changed');result['pre_run_radio_status']=inspection
        import base64
        public=[]
        for piece in pieces:
            f=piece['fixture'];public.append(dict(manifest=base64.b64encode(read(piece['path']/'pack/manifest.json',65536)).decode(),signature=base64.b64encode(read(piece['path']/'pack/manifest.sig.json',4096)).decode(),trust=base64.b64encode(read(piece['path']/'trust.json',16384)).decode(),pins=dict(manifest=f['manifest_sha256'],signature=f['signature_sha256'],trust=f['trust_sha256']),wanted=[piece['sample']['chunk_sha256']],recipient='unit-a'))
            absent=native('unit-a','observe-cache',public[-1])['observation'];need(not absent['verified_present'] and absent['absent']==public[-1]['wanted'],'batch_fresh_resource_exists')
            operator.seed(helpers.seed_package(cfg,'unit-c','source',piece['path']/'pack',piece['trust'],f['manifest_sha256'],piece['cache'].value));operator.seed(helpers.seed_package(cfg,'unit-a','recipient',piece['path']/'pack',piece['trust'],f['manifest_sha256'],piece['cache'].value))
        facts={u:stable(operator.facts(u)) for u in histories};captures={}
        for u in ('unit-c','unit-b','unit-a'):
            captures[u]=native(u,'capture',dict(parent_id=parent_id,history=histories[u],facts=facts[u],phase_start_epoch_us=epoch,phase_end_epoch_us=end,review_sha256=args.operation_sha256))
            cap=captures[u];need(cap['actual_history_verified'] is True and cap['refunds']==0,'batch_capture_unqualified')
            priors[u]=dict(schema='meshticulum.public-resume-conservative-prior.v1',history_binding_sha256=cap['history_binding_sha256'],legacy_inventory=histories[u]['legacy_inventory'],previous_parent_ids=op['history_parent_ids'],full_caps=histories[u]['previous'][-1]['paid_caps'],complete_global_debt=False,actual_cost=None,refunds=0)
        result['captures']=captures
        registry={u:dict(observation_sha256=sha(canonical(priors[u])),history_binding_sha256=captures[u]['history_binding_sha256'],full_caps=priors[u]['full_caps']) for u in histories};units=[]
        peers={u:next(v['peer'] for v in histories[u]['previous'][-1]['policy']['units'] if v['unit']==u) for u in histories};extra={u:Cost() if u=='unit-b' else Cost(1536,360000000) for u in histories}
        for u in histories:
            old=Cost(**priors[u]['full_caps']);units.append(UnitCaps(u,cfg['units'][u]['host_boot_id'],PeerID(**peers[u]),old.plus(extra[u]),old,sha(canonical(priors[u]))))
        aggregate=Cost()
        for unit in units:aggregate=aggregate.plus(unit.limits)
        policy=ParentPolicy(parent_id,helpers.controller_boot(),epoch,end-5000000,mono,mono+894000000,tuple(units),aggregate,sha(canonical(registry)),KIND)
        owner=dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind=KIND,policy_digest=policy.digest,authorization=op['authorization'],review_sha256=args.operation_sha256,prior_registry=registry,facts=facts,phase_start_epoch_us=epoch,phase_end_epoch_us=end,native_phase={u:captures[u]['native_phase'] for u in histories},runtime_sha256=op['runtime_sha256'],manifest_sha256=op['manifest_sha256'],rf_record=json.loads(read(op['rf_confirmation_path'],20000)),rf_record_sha256='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9',rf_profile=profile_record(profile_for(GFSK_NAME)),radio_profile_name=GFSK_NAME,radio_modem=GFSK_NAME,gfsk_batch=op['batch_descriptor'],complete_global_debt=False,actual_cost=None)
        context=BatchContext.from_owner(owner,policy);owners={u:dict(owner,history_binding_sha256=captures[u]['history_binding_sha256'],capture_record_sha256=captures[u]['capture_record_sha256']) for u in histories}
        write(out/'POLICY.json',policy.to_dict());write(out/'OWNER_APPROVAL.json',owners);write(out/'PRIOR.json',priors);write(out/'CONFIG.json',cfg);write(out/'OPERATION.json',op)
        parent=ParentCaps.enroll(out/'caps.sqlite',policy,clock=helpers.parent_clock,boot_reader=helpers.controller_boot)
        for u in ('unit-c','unit-b','unit-a'):native(u,'enroll',dict(policy=policy.to_dict(),owner=owners[u],prior=priors[u]))
        original_prepare=operator.prepare
        def prepare(package):
            # Each parallel metadata thread owns its own context; no controller
            # SQLite owner or main-thread retained object crosses this boundary.
            local=BatchContext(op['batch_descriptor'],policy,phase_end_epoch_us=end)
            local.package(package);guarded(package['unit']);receipt=original_prepare(package)
            pin=receipt['prepared_identity_sha256'];need(receipt['run_id'] not in preparations[package['unit']],'batch_prepare_reused')
            preparations[package['unit']][receipt['run_id']]=pin;return receipt
        operator.prepare=prepare
        load('batch_launch_guard',lantern/'firmware/meshticulum-bench/pair_launch_guard_sender_first.py').install(operator,guarded,lora_sender_first=True)
        for index,piece in enumerate(pieces):
            # No current Controller or application lease is carried into the next resource.
            issued,issued_mono=helpers.parent_clock();expires=issued+180000000;need(expires<=policy.expires_epoch_us-15000000,'batch_fresh180_window_unavailable')
            current=Controller(operator,campaign_id=piece['sample']['campaign_id'],resource_sha256=piece['sample']['resource_sha256'],expires_epoch_us=expires,handoff_deadline_monotonic=operator.deadline,peers=peers,boots={u:v['host_boot_id'] for u,v in cfg['units'].items()},parent=parent,radio_permission=dict(country='MY',antenna_confirmed=True),radio_profile=GFSK_NAME,lora_sender_first=True,batch_context=context)
            verified=current.direct_piece(piece['request'],piece['response'],'lora',publisher='unit-c',requester='unit-a');metrics=gfsk_metrics(current.links[1],verified,piece['piece'])
            current=finish_sample(operator,current,result,index=index,
                original_application=dict(epoch_us=issued,monotonic_us=issued_mono,expires_epoch_us=expires),
                verified=verified,metrics=metrics)
        for u in ('unit-c','unit-b','unit-a'):
            result['native_final'][u]=guarded(u,'audit');s=result['native_final'][u]['snapshot'];need(s['claims']==s['prepared']==s['starts']==(0 if u=='unit-b' else 6) and s['debt']==asdict(policy.unit(u).limits) and not s['uncertain'],'batch_final_accounting_changed')
        result['complete']=len(result['samples'])==3
    except BaseException as e:result.update(error_type=type(e).__name__,error=str(e)[:200])
    finally:
        password=None
        if current is not None:
            result['links'].extend(current.links)
            try:cleanup_current(current,result)
            except BaseException:result['complete']=False;result['cleanup_error']='current_controller_cleanup_unproved'
        if operator is not None:
            for (u,run),job in operator.jobs.items():
                try:need(operator.status(u,run)['live'] is False,'batch_native_terminal_unproved')
                except BaseException:result['complete']=False;result.setdefault('terminal_unproved',[]).append(dict(unit=u,run_id=run))
            for job in list(operator.jobs.values())+owned:
                try:operator.reap(job)
                except BaseException:result['complete']=False;result.setdefault('cleanup_errors',[]).append('owned_client_reap_failed')
                finally:job.password=None;job.output.clear()
            result['owned_local_clients_terminal']=all(job.closed and job.process.poll() is not None for job in list(operator.jobs.values())+owned)
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
        write(out/'RESULT.json',result);print(json.dumps(dict(complete=result['complete'],saved=str(out/'RESULT.json'),qualification_complete=False)))


if __name__=='__main__':main()
