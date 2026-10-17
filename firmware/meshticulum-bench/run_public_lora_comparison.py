# SPDX-License-Identifier: Apache-2.0
"""One exact finite same-power SF7/SF5 comparison on current020 A and C.

Root supplies reviewed source/scope/runtime/install/full46/status inputs. --check
uses no credentials, host connection, radio or new clock window. A real run
consumes the scope once and retains all prepaid debt even after refusal.
"""
import argparse
from dataclasses import asdict
import getpass
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET

from run_public_sf5_c_bulk_8k import read,write,sha,canonical,stable,need,PUBLISHER,REQUESTER
from run_public_gfsk250_mb1_fast_bundle100k import (archive_source_members,FileRPCCommand,
    cleanup_rpc_input,rpc_failure_diagnostic,parallel_captures)

SCHEMA='meshticulum.public-lora-sf7-sf5-comparison-operation.v1'
SCOPE_SCHEMA='meshticulum.root-reviewed-lora-sf7-sf5-comparison-scope.v1'
HISTORY_SHA='cf01a817a18458f0ca2b91dca012afcc29044a9c65bb426ee9316e003cb32826'
LATEST='360514104bf80fa187276f24d6aa9d9c'
MCU_BOOT='000008f6a51b5bf80000000000000038'
RF_SHA='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9'
HEX=re.compile(r'[0-9a-f]{64}\Z')
RETAINED_SCOPES=('c922e922b97590a69fc331a83134c6d94f0ee700a2524b803c7e2dc0928c5f1f',
    'ba84af81f913d73013fdefa1b550725795f91d0bea11ee0e7367169b72999e50',
    '3d88fb98b68c02c865fae66faa935744687227bf6cf9a07f595890ec774f0152',
    'c4ce1ce81748084ede8b8731c1479fc70f36a196ad5601a7651c570e66014c9e')


def current_peers(config):
    """Use current host boots for new grants; historical peers stay immutable."""
    return {unit:dict(chain_id='meshticulum-public-pilot-v1',agent_id=sha(
        ('meshticulum-public-pilot-v1/'+unit+'/'+row['host_boot_id']).encode()))
        for unit,row in config['units'].items()}


def parallel_enroll(native,policy,owners,priors,capture_payloads):
    """Join every independent native enrollment before any worker setup."""
    payloads={u:dict(policy=policy.to_dict(),owner=owners[u],prior=priors[u])
        for u in ('unit-c','unit-b','unit-a')}
    return parallel_captures(lambda u,_action,_capture:native(u,'enroll',payloads[u]),capture_payloads)


def controller_boot():
    # Same observation as run_public_pilot.controller_boot; available before
    # importing the not-yet-verified paired source during operation review.
    if platform.system()=='Linux':value=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    elif platform.system()=='Darwin':
        value=subprocess.check_output(['/usr/sbin/sysctl','-n','kern.bootsessionuuid'],text=True,timeout=5).strip().lower()
    else:raise RuntimeError('unsupported_controller_boot_observation')
    return str(uuid.UUID(value))


class OuterWindow:
    """The issuer's original interval; checking never maps a fresh deadline."""
    def __init__(self,op,*,clock=None,boot_reader=None):
        self._op=op;self._clock=clock or (lambda:(time.time_ns()//1000,time.monotonic_ns()//1000))
        self._boot=boot_reader or controller_boot;self._pid=os.getpid();self._lock=threading.RLock()
        self._original=self._binding();self._last=None;self.boundary()

    def _binding(self):
        absolute=self._op['absolute_device_window']
        epoch,end=absolute['issued_epoch_us'],absolute['expires_epoch_us']
        mono,mono_end=(self._op['original_outer_monotonic_us'],self._op['original_outer_deadline_monotonic_us'])
        boot=self._op['original_outer_controller_boot']
        need(type(absolute) is dict and set(absolute)=={'issued_epoch_us','expires_epoch_us','count_authorized'}
            and type(absolute['count_authorized']) is int and absolute['count_authorized']==1
            and all(type(v) is int and v>=0 for v in (epoch,end,mono,mono_end))
            and end-epoch==mono_end-mono==720000000 and type(boot) is str
            and str(uuid.UUID(boot))==boot,'comparison_original_outer_binding_changed')
        return epoch,end,mono,mono_end,boot

    def check(self):
        with self._lock:
            need(os.getpid()==self._pid and self._binding()==self._original,'comparison_original_outer_rebound')
            now=self._clock();need(os.getpid()==self._pid and self._binding()==self._original,'comparison_original_outer_rebound')
            epoch,end,mono,mono_end,_=self._original
            need(type(now) is tuple and len(now)==2 and all(type(v) is int for v in now)
                and epoch<=now[0]<end and mono<=now[1]<mono_end
                and (self._last is None or all(a>=b for a,b in zip(now,self._last))),
                'comparison_original_outer_expired_or_regressed')
            self._last=now;return now

    def boundary(self):
        self.check();boot=self._boot();self.check()
        need(boot==self._original[4],'comparison_original_outer_boot_changed')
        return self._last

    def phase(self):
        now=self.boundary()
        need(now[0]+600000000<=self._original[1] and now[1]+600000000<=self._original[3],
            'comparison_original_outer_cannot_fit_phase')
        return now

    def monotonic(self):return self.check()[1]/1000000
    def epoch(self):return self.check()[0]
    @property
    def deadline(self):return self._original[3]/1000000

    def read(self,path,limit=300000):
        self.check();raw=read(path,limit);self.check();return raw

    def record(self,path,pin,limit=2000000):
        raw=self.read(path,limit);need(type(pin) is str and HEX.fullmatch(pin) and sha(raw)==pin,
            'comparison_input_pin_changed');value=json.loads(raw);self.check();return value


def outer_ssh_operator_type(base,outer):
    # Retain the admitted end outside the operator's mutable optional field.
    admitted=outer.deadline
    class OuterSSHOperator(base):
        def _outer_bound(self):
            current=getattr(self,'_outer_deadline_monotonic',None)
            need(type(current) is type(admitted) and current==admitted
                and outer.deadline==admitted,'comparison_ssh_outer_deadline_changed')
            outer.check()
        def _argv(self,*args,**kwargs):
            self._outer_bound();outer.boundary()
            value=super()._argv(*args,**kwargs)
            self._outer_bound();return value
        def _command(self,*args,**kwargs):
            self._outer_bound()
            value=super()._command(*args,**kwargs)
            self._outer_bound();outer.boundary();return value
    return OuterSSHOperator


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def record(path,pin,limit=2000000):
    raw=read(path,limit);need(type(pin) is str and HEX.fullmatch(pin) and sha(raw)==pin,'comparison_input_pin_changed');return json.loads(raw)


def safe_reason(error):
    value=str(error)
    return value if re.fullmatch('[A-Za-z0-9_.:-]{1,100}',value) else None


def check_unissued(records,*,outer=None):
    record_input=record if outer is None else outer.record
    if outer is not None:outer.check()
    need(type(records) is list and [v['sha256'] for v in records]==list(RETAINED_SCOPES),'comparison_unissued_roster_changed')
    for row in records:
        path=Path(row['path']).absolute();old=record_input(path,row['sha256'])
        need(old['window_issued'] is False and old['native_permission'] is False,
            'retained_comparison_scope_already_issued')
        folder=Path(row['operation_directory']).absolute()
        need(folder.is_dir() and not os.path.lexists(folder/('ISSUE_USED-'+row['sha256']+'.json'))
            and not list(folder.glob('OPERATION-[0-9]*.json')),'retained_comparison_scope_consumed')
    if outer is not None:outer.check()


def source_tests(ready,*,outer=None):
    record_input=record if outer is None else outer.record
    read_input=read if outer is None else outer.read
    folder=Path(ready['source_controls_directory']).absolute()
    provenance=record_input(folder/'SOURCE_TEST_PROVENANCE.json',ready['source_tests_provenance_sha256'])
    run=record_input(folder/'SOURCE_TEST_RUN.json',ready['full_source_run_sha256'])
    report=read_input(Path(provenance['report']),20000000)
    need(sha(report)==ready['source_tests_sha256']==provenance['report_sha256'],'comparison_source_report_changed')
    suites=list(ET.fromstring(report).iter('testsuite'))
    need(suites and sum(int(v.get('tests',0)) for v in suites)>3800
        and not any(int(v.get('errors',0)) or int(v.get('failures',0)) for v in suites),'comparison_full_green_source_required')
    need(provenance['schema']=='meshticulum.exact-pair-source-controls.v1'
        and provenance['single_run'] is provenance['source_controls_only'] is True
        and provenance['native_qualification'] is False
        and provenance['core_commit']==ready['core_commit'] and provenance['lantern_commit']==ready['lantern_commit']
        and provenance['full_source_run_sha256']==ready['full_source_run_sha256']
        and run['schema']=='meshticulum.exact-pair-full-source-run.v1'
        and run['single_run'] is run['source_unchanged'] is run['source_controls_only'] is True
        and run['native_qualification'] is False and type(run['returncode']) is int and run['returncode']==0
        and run['core_commit']==ready['core_commit'] and run['lantern_commit']==ready['lantern_commit'],
        'comparison_exact_source_provenance_required')
    for name,pin in run['files'].items():need(sha(read_input(folder/name,20000000))==pin,'comparison_source_run_member_changed')
    need(sha(read_input(Path(provenance['core_report']),20000000))==provenance['core_report_sha256'],'comparison_core_source_report_changed')


def review(path,pin):
    raw=read(path,2000000);need(sha(raw)==pin,'comparison_operation_pin_changed');op=json.loads(raw)
    need(op['schema']==SCHEMA,'wrong_comparison_operation')
    need(op.get('preparation_only') is False and op.get('not_for_issuance') is False
        and op.get('window_issued') is True,'comparison_operation_still_unissued')
    required=('core_commit','lantern_commit','source_lock_sha256','comparison_scope_sha256','ready_sha256',
        'runtime_sha256','manifest_sha256','installation_result_sha256','previous_installation_result_sha256',
        'preflight_completion_sha256','inspection_receipt_sha256','native_adapter_sha256')
    need(all(type(op.get(k)) is str and re.fullmatch('[0-9a-f]{40}' if k.endswith('_commit') else '[0-9a-f]{64}',op[k]) for k in required),
        'pending_reviewed_comparison_inputs')
    outer=OuterWindow(op)
    need(op['runner_sha256']==sha(outer.read(Path(__file__),100000)),'wrong_comparison_operation')
    need(op['sample_count']==2 and op['phase_seconds']==600 and op['application_seconds']==180,
        'comparison_original_limits_changed')
    need(op['authorization']==dict(human_statement='run it bro',count_authorized=1,public_only=True,private_production=False),
        'comparison_original_authorization_missing')
    core,lantern=Path(op['core_path']).absolute(),Path(op['lantern_path']).absolute()
    for folder,commit in ((core,op['core_commit']),(lantern,op['lantern_commit'])):
        outer.check()
        need(subprocess.check_output(['git','-C',str(folder),'rev-parse','HEAD'],text=True,timeout=5).strip()==commit
            and not subprocess.check_output(['git','-C',str(folder),'status','--porcelain'],text=True,timeout=5),'comparison_source_changed');outer.check()
    sys.path[:0]=[str(core/'src'),str(lantern/'software/meshticulum-integration/src')]
    from lantern_meshticulum.bench.lora_comparison import validate_descriptor,KIND
    from lantern_meshticulum.bench import mcu_artifact as mcu,retained_capture
    validate_descriptor(op['comparison_descriptor']);bindings=mcu.require_lora_selector_scope(KIND)
    need(op['lora_mcu_artifact']==mcu.lora_artifact() and op['core_commit']==op['lora_mcu_artifact']['runtime_core_revision']
        and op['source_lock_sha256']==op['lora_mcu_artifact']['source_lock_sha256']
        and op['comparison_scope_sha256']==bindings['scope_sha256']
        and op['history_receipt_sha256']==bindings['history_receipt_sha256']==HISTORY_SHA
        and bindings['preceding_parent']==LATEST,'comparison_exact_source_scope_required')
    scope=outer.record(op['scope_path'],op['comparison_scope_sha256'])
    need(scope['schema']==SCOPE_SCHEMA and scope['qualification_kind']==KIND and scope['core_commit']==op['core_commit']
        and scope['lora_mcu_artifact']==op['lora_mcu_artifact'] and scope['history_receipt_sha256']==HISTORY_SHA
        and scope['preceding_parent']==LATEST and scope['paid_history_count']==46 and scope['expected_mcu_boot']==MCU_BOOT
        and scope['comparison_descriptor']==op['comparison_descriptor'] and scope['fixtures']==op['fixtures']
        and scope['phase_seconds']==600 and scope['application_seconds']==180 and scope['absolute_window_seconds']==720
        and scope['prepared_endpoint_limit']==8 and scope['window_issued'] is scope['native_permission'] is False
        and scope['preparation_only'] is scope['not_for_issuance'] is False
        and scope['lantern_commit']==scope['reviewed_implementation_commit'],
        'comparison_reviewed_scope_changed')
    need(scope['new_caps_per_A_C']==dict(radio_attempts=1024,estimated_airtime_us=240000000)
        and scope['new_caps_B']==0 and scope['refunds']==0 and scope['unknown_actual_cost'] is None
        and scope['global_debt_complete'] is False and scope['reset_permitted'] is False
        and op['output_directory']==scope['operation_directory']
        and type(scope['reviewed_implementation_commit']) is str
        and re.fullmatch('[0-9a-f]{40}',scope['reviewed_implementation_commit']),
        'comparison_original_scope_caps_or_source_changed')
    need(not os.path.lexists(Path(op['output_directory'])/('COMPARISON_USED-'+op['comparison_scope_sha256']+'.json')),
        'comparison_scope_already_consumed')
    need(subprocess.run(['git','-C',str(lantern),'merge-base','--is-ancestor',scope['reviewed_implementation_commit'],
        op['lantern_commit']],timeout=5,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0,
        'comparison_reviewed_source_prefix_missing')
    check_unissued(scope['retained_unissued_scopes'],outer=outer);need(op['retained_unissued_scopes']==scope['retained_unissued_scopes'],'comparison_retained_scopes_changed')
    absolute=op['absolute_device_window']
    need(type(absolute['issued_epoch_us']) is int and type(absolute['expires_epoch_us']) is int
        and absolute['expires_epoch_us']-absolute['issued_epoch_us']==720000000 and absolute['count_authorized']==1,
        'comparison_original_absolute_window_changed')
    need(op['history_parent_ids']==list(retained_capture.PAID_HISTORY) and len(op['history_parent_ids'])==46
        and set(op['controller_directories'])==set(op['history_parent_ids']),'comparison_full_history_required')
    receipt=outer.record(Path(op['history_path'])/'RECEIPT.json',HISTORY_SHA,100000)
    need(receipt['local_controllers_reobserved']==46 and receipt['no_refund'] is True,'comparison_old_debt_required')
    ready=outer.record(op['ready_path'],op['ready_sha256'])
    need(all(ready[k]==op[k] for k in ('core_commit','lantern_commit','runtime_sha256','manifest_sha256'))
        and ready['scope_sha256']==op['comparison_scope_sha256'],'comparison_ready_changed');source_tests(ready,outer=outer)
    install=outer.record(op['installation_result'],op['installation_result_sha256'])
    previous=outer.record(op['previous_installation_result'],op['previous_installation_result_sha256'])
    need(all(install[k] is True for k in ('complete','local_clients_terminal','within_original_window',
        'all_owned_clients_exit_zero','source_unchanged','old_debt_retained')) and install['cleanup_errors']==[]
        and set(install['installations'])=={'unit-a','unit-b','unit-c'}
        and all(install[k] is False for k in ('services_changed','private_production_enabled','physical_qualification'))
        and install['rf_tx_commands']==install['grants_created']==0,'comparison_closed_metadata_install_required')
    for key,opkey in (('archive_sha256','runtime_sha256'),('manifest_sha256','manifest_sha256'),('core_commit','core_commit'),('lantern_commit','lantern_commit')):
        need(install[key]==op[opkey] and install['previous_'+key]==previous[key],'comparison_installation_ancestry_changed')
    need(install['phase_end_epoch_us']-install['started_epoch_us']==300000000
        and install['started_epoch_us']<=install['finished_epoch_us']<install['phase_end_epoch_us'],'comparison_installation_window_changed')
    cfg=outer.record(op['config_path'],op['config_sha256'],30000)
    original=outer.record(op['previous_config_path'],op['previous_config_sha256'],30000)
    allowed={'runtime_sha256','manifest_sha256','provision_sha256','runtime_bootstrap_sha256','handoff_end_epoch_us'}
    need({k:v for k,v in cfg.items() if k not in allowed}=={k:v for k,v in original.items() if k not in allowed}
        and cfg['runtime_sha256']==op['runtime_sha256'] and cfg['manifest_sha256']==op['manifest_sha256'],
        'comparison_installed_config_changed')
    proof=outer.record(op['preflight_completion'],op['preflight_completion_sha256'])
    need(proof['schema']=='meshticulum.lora-readonly-full46-capture-complete.v1' and proof['complete'] is True
        and proof['archive_sha256']==op['runtime_sha256'] and proof['manifest_sha256']==op['manifest_sha256']
        and proof['history_receipt_sha256']==HISTORY_SHA and proof['callbacks_exit_zero']==3
        and proof['owned_local_clients_terminal'] is True and proof['worker_threads_terminal'] is True
        and proof['native_state_writes'] is proof['radio_opened'] is False,'comparison_full46_preflight_required')
    inspection=outer.record(op['inspection_receipt'],op['inspection_receipt_sha256'])
    need(inspection['complete'] is True and inspection['archive_sha256']==op['runtime_sha256']
        and inspection['manifest_sha256']==op['manifest_sha256'] and inspection['owned_local_clients_terminal'] is True
        and inspection['arm_commands']==inspection['tx_commands']==inspection['reset_commands']==0,
        'comparison_closed_current_inspection_required')
    validate_a_status(inspection['observation'],op,None,min_remaining=160)
    archive=outer.read(op['runtime_archive'],2000000);need(sha(archive)==op['runtime_sha256'],'comparison_archive_changed')
    manifest,logical=archive_source_members(archive,op['manifest_sha256'])
    need(mcu.select_lora_runtime(manifest)==op['lora_mcu_artifact']
        and manifest['private_services_enabled'] is False and manifest['public_parent_claims_required'] is True,
        'comparison_runtime_changed')
    for name,data in logical.items():
        source=(core/'src'/name if name.startswith('meshticulum/') else
            lantern/'software/meshticulum-integration/src'/name if name.startswith('lantern_meshticulum/') else
            lantern/'firmware/meshticulum-bench/bench_node.py' if name=='bench_node.py' else
            lantern/'software/meshticulum-integration/meshticulum-source-lock.json' if name=='provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json' else core/name)
        need(sha(outer.read(source,500000))==sha(data),'comparison_runtime_source_closure_changed')
    need(sha(logical['lantern_meshticulum/bench/pilot_resume_rpc.py'])==op['native_adapter_sha256'],'comparison_native_adapter_changed')
    required_support={'run_public_pilot.py','pilot_grant_bootstrap.py','runtime_bootstrap.py','run_public_sf5_c_bulk_8k.py',
        'run_public_gfsk250_mb1_fast_bundle100k.py','pair_launch_guard_sender_first.py','management_file_rpc.py'}
    need({f'firmware/meshticulum-bench/{v}' for v in required_support}<=set(op['source_pins']),'comparison_support_pins_missing')
    for name,pin in op['source_pins'].items():
        need(not Path(name).is_absolute() and '..' not in Path(name).parts and sha(outer.read(lantern/name,500000))==pin,'comparison_support_source_changed')
    need(sha(outer.read(op['rf_confirmation_path'],20000))==RF_SHA,'comparison_rf_consent_changed')
    helpers=load('comparison_pilot_helpers',lantern/'firmware/meshticulum-bench/run_public_pilot.py')
    outer.boundary();need(helpers.controller_boot()==op['original_outer_controller_boot'],'comparison_verified_controller_boot_changed');outer.check()
    return op,core,lantern,manifest,receipt,helpers,outer


def consume_scope(op,operation_sha,parent_id,start,end,*,outer=None):
    if outer is not None:outer.boundary()
    if outer is None:check_unissued(op['retained_unissued_scopes'])
    else:check_unissued(op['retained_unissued_scopes'],outer=outer)
    scope=(record if outer is None else outer.record)(op['scope_path'],op['comparison_scope_sha256'])
    folder=Path(op['output_directory']).absolute()
    need(str(folder)==scope['operation_directory'],'comparison_scope_marker_directory_changed')
    for path in (folder,*folder.parents):
        st=path.lstat();need(stat.S_ISDIR(st.st_mode) and path.resolve()==path
            and st.st_uid in (0,os.geteuid()) and not st.st_mode&0o022,'unsafe_comparison_marker_ancestry')
    fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd);identity=(st.st_dev,st.st_ino,st.st_mode,st.st_uid)
        need(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid() and not st.st_mode&0o022,'comparison_scope_directory_changed')
        marker='COMPARISON_USED-'+op['comparison_scope_sha256']+'.json'
        raw=canonical(dict(parent_id=parent_id,operation_sha256=operation_sha,scope_sha256=op['comparison_scope_sha256'],original_phase_start_epoch_us=start,original_phase_end_epoch_us=end,count_used=1))
        target=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
        try:
            view=memoryview(raw)
            while view:
                count=os.write(target,view);need(count>0,'comparison_marker_short_write');view=view[count:]
            os.fsync(target)
        finally:os.close(target)
        os.fsync(fd);after=folder.lstat()
        need(identity==(after.st_dev,after.st_ino,after.st_mode,after.st_uid),'comparison_scope_directory_rebound')
        if outer is not None:outer.boundary()
    finally:os.close(fd)


def validate_a_status(inspection,op,previous,*,min_remaining):
    need(inspection['schema']=='meshticulum.native-bench-inspection.v1' and inspection['unit']=='unit-a'
        and inspection['arm_commands']==inspection['tx_commands']==0
        and inspection['services_before']==inspection['services_after'],'comparison_inspection_changed')
    radio=inspection['radio'];expected=op['radio_status_expected']
    expected_drops=expected.get('dropped_receive',0)
    need(type(expected_drops) is int and expected_drops>=0,'comparison_A_drop_baseline_invalid')
    need(radio['disarmed'] is True and radio['firmware']=='mtb1-020e287255'
        and radio['usb_serial']=='F8:5B:1B:A5:F6:08' and radio['mcu_boot']==expected['mcu_boot']==MCU_BOOT
        and all(type(radio[k]) is int and radio[k]>=0 for k in ('boot_attempts','boot_reserved_estimated_airtime_us'))
        and type(radio['dropped_receive']) is int and radio['dropped_receive']==expected_drops
        and 256-radio['boot_attempts']>=min_remaining
        and radio['boot_reserved_estimated_airtime_us']<60000000,'comparison_A_boot_or_cumulative_caps_changed')
    old=expected if previous is None else previous['radio']
    if previous is None:need(all(radio[k]==expected[k] for k in ('boot_attempts','boot_reserved_estimated_airtime_us')),'comparison_A_initial_counters_changed')
    need(all(radio[k]>=old[k] for k in ('boot_attempts','boot_reserved_estimated_airtime_us')),
        'comparison_A_counters_regressed')
    return inspection


def inspect_current_a(operator,op,manifest,previous,*,min_remaining):
    before=stable(operator.facts('unit-a'))
    inspection=operator._action('unit-a','inspect',schema='meshticulum.native-bench-inspection.v1')
    after=stable(operator.facts('unit-a'))
    need(before==after and inspection['host_boot_id']==before['host_boot_id']==operator.config['units']['unit-a']['host_boot_id']
        and inspection['services_before']==before['services'] and inspection['bundle_manifest_sha256']==op['manifest_sha256']
        and manifest['firmware_by_unit']['unit-a']=='mtb1-020e287255','comparison_current_facts_changed')
    return validate_a_status(inspection,op,previous,min_remaining=min_remaining)


def rpc_command(operator,cfg,op,unit,action,payload,seconds,parent_id,operation_sha256):
    from management_file_rpc import command as file_command
    from lantern_meshticulum.bench.management_input import parameters
    need(type(seconds) is int and 0<seconds<=120,'comparison_callback_bound')
    nonce=secrets.token_hex(16);now=time.time_ns()//1000;local_start=operator.clock()
    end=min(cfg['handoff_end_epoch_us'],now+seconds*1000000)
    deadline=min(operator.deadline,local_start+max(0,(end-now)/1000000))
    parameters(unit,action,parent_id,operation_sha256,nonce,end)
    staged=operator.stage_management_input(unit,action,payload,parent_id=parent_id,
        operation_sha256=operation_sha256,nonce=nonce,original_end_epoch_us=end)
    try:
        values=file_command('/opt/meshticulum-bench/runtime-'+op['runtime_sha256'][:16],op['manifest_sha256'],
            op['native_adapter_sha256'],staged,cfg['units'][unit]['python'],seconds)
        need(operator.clock()<deadline and time.time_ns()//1000<end,'comparison_callback_original_window_expired')
    except BaseException:cleanup_rpc_input(operator,cfg,op,staged);raise
    return FileRPCCommand(values,staged,end,deadline)


def fixtures(op,*,outer=None):
    read_input=read if outer is None else outer.read
    from meshticulum.resources import lamplighter as ll,resource
    result=[]
    for i,decl in enumerate(op['fixtures']):
        path=Path(decl['path']).absolute();raw=read_input(path/'FIXTURE.json',50000);need(sha(raw)==decl['sha256'],'batch_fixture_changed');f=json.loads(raw)
        need(f['public_only'] is True and f['private_data'] is False and len(f['chunks'])==1,'batch_public_fixture_required')
        trust=json.loads(read(path/'trust.json',16384));cache=ll.VerifiedCache(path/'pack',trust,f['manifest_sha256']);piece=f['chunks'][0]
        chunk=cache.entries[piece['content_address_sha256']];request=resource.encode_request('/packs/'+f['manifest_sha256']+'/chunks/'+chunk['sha256']+'.json',0,8164)
        response=resource.BlockServer(cache.read).handle(request);sample=op['comparison_descriptor']['samples'][i]
        need(len(request)==74 and len(response)==8192 and piece['chunk_bytes']==8164 and chunk['sha256']==sample['chunk_sha256']
            and f['manifest_sha256']==sample['resource_sha256'] and sha(request)==sample['request']['sha256']
            and sha(response)==sample['response']['sha256'] and piece['response_sha256']==sample['response']['sha256']
            and piece['request_sha256']==sample['request']['sha256']
            and sha(read_input(path/'trust.json',16384))==f['trust_sha256']
            and sha(read_input(path/'pack/manifest.sig.json',4096))==f['signature_sha256'],'batch_fixture_descriptor_changed')
        result.append(dict(path=path,fixture=f,trust=trust,cache=cache,piece=piece,request=request,response=response,sample=sample))
    if outer is not None:outer.check()
    return result


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



def bulk_metrics(link, verified, piece, profile_name):
    """Same-host wall clocks; useful bytes exclude the MR1 response header."""
    from lantern_meshticulum.bench.profiles import profile_for
    need(link['transport']=='lora' and link['format']=='MR1.response'
         and link['sender']==PUBLISHER and link['receiver']==REQUESTER and link['phase']=='complete',
         'bulk_response_path_rebound')
    tx,rx=link['outcomes'][PUBLISHER],link['outcomes'][REQUESTER]
    need(piece['chunk_bytes']==8164 and piece['response_bytes']==8192
         and link['descriptor']==dict(bytes=8192,sha256=piece['response_sha256'])
         and tx['unit']==PUBLISHER and rx['unit']==REQUESTER
         and tx['complete'] is True and rx['complete'] is True and tx['sends'] is True and rx['sends'] is False
         and all(v['service_format']=='MR1.response' and v['disarmed_on_exit'] is True
                 and not v['attempt_journal']['fenced'] and not v.get('cleanup_errors') for v in (tx,rx))
         and rx['service_object_durable'] is True and rx['service_object_sha256']==piece['response_sha256']
         and verified['publisher_verified'] is True and verified['reused'] is False
         and verified['chunk_sha256']==piece['content_address_sha256']
         and verified['useful_verified_bytes']==8164 and tx['flow']['acked_frames']==43,
         'bulk_completed_bytes_or_ownership_unqualified')
    duration=tx['flow_complete_monotonic_us']-tx['start_monotonic_us']
    need(type(duration) is int and 0<duration<180000000,'invalid_bulk_sender_wall_time')
    journals={unit:dict(result['attempt_journal']) for unit,result in link['outcomes'].items()}
    data=[e for e in tx['flow']['events'] if e.get('event')=='tx' and e.get('outer_kind')=='DATA']
    need(data and all(type(e.get('worker_call_elapsed_us')) is int and e['worker_call_elapsed_us']>=0 for e in data),'DATA_worker_timing_required')
    first=min(e['mono_us']-e['worker_call_elapsed_us'] for e in data)
    useful_span=tx['flow_complete_monotonic_us']-first
    need(0<useful_span<=duration,'DATA_first_to_final_ACK_clock_invalid')
    signals={unit:[{key:event.get(key) for key in ('mono_us','outer_kind','rssi_dbm','snr_db')}
        for event in outcome['flow']['events'] if event.get('event')=='rx']
        for unit,outcome in link['outcomes'].items()}
    return dict(useful_verified_bytes=8164,mr1_response_bytes=8192,data_frames=43,
        sender_start_to_confirmed_ack_us=duration,sender_start_useful_Bps=8164*1e6/duration,
        data_first_to_final_ACK_us=useful_span,data_first_to_final_ACK_useful_Bps=8164*1e6/useful_span,
        radio_profile=asdict(profile_for(profile_name)),minimum_target_Bps=1000,minimum_target_met=8164*1e6/duration>=1000,
        request_to_publisher_verification_us=verified['request_to_verification_us'],
        request_to_publisher_verification_Bps=verified['useful_verified_bytes_per_second'],
        received_durable_response_sha256=rx['service_object_sha256'],publisher_verified_chunk=verified['chunk_sha256'],
        attempts=journals,flow={unit:result['flow'] for unit,result in link['outcomes'].items()},
        driver_timing_events={unit:result['flow']['events'] for unit,result in link['outcomes'].items()},
        rssi_snr_observations=signals,
        receiver_ack_grace_seconds=20,measured_airtime_us=None,measured_energy_joules=None,measured_eirp_dbm=None,
        security_scope='Pinned current SessionCipher DATA/ACK AEAD; public signed publisher verification',
        sustained_scope='One8164-byte encrypted RF sample; repeats and completion distributions remain separate')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--operation',type=Path,required=True);parser.add_argument('--operation-sha256',required=True);parser.add_argument('--check',action='store_true');args=parser.parse_args();os.umask(0o077)
    op,core,lantern,manifest,receipt,helpers,outer=review(args.operation,args.operation_sha256);pieces=fixtures(op,outer=outer)
    from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,ParentCaps,UnitCaps,Cost
    from lantern_meshticulum.bench.pilot_parent_snapshot import Scope,collect
    from lantern_meshticulum.bench.pilot_ssh import SSHOperator,Job,one_record
    from lantern_meshticulum.bench.pilot_controller import Controller
    from lantern_meshticulum.bench.lora_comparison import ComparisonContext,KIND,SSH_SCHEMA
    from lantern_meshticulum.bench.profiles import profile_for
    from meshticulum.contracts.identity import PeerID
    from run_public_sf5_c_bulk_8k import retained_controller_caps
    histories={u:json.loads(outer.read(Path(op['history_path'])/(u+'.json'),2000000)) for u in ('unit-a','unit-b','unit-c')}
    for u,h in histories.items():need(sha(outer.read(Path(op['history_path'])/(u+'.json'),2000000))==receipt['payload_sha256'][u],'batch_history_bytes_changed')
    if args.check:print(json.dumps(dict(source_checked=True,samples=2,radio_opened=False)));return
    controllers=[];projection=('status','scoped_rows_complete','pending_unknown','policy_row_sha256','prior_lineage','detail','files')
    for i,pid in enumerate(op['history_parent_ids']):
        outer.check();directory=Path(op['controller_directories'][pid]);policy=ParentPolicy.from_dict(json.loads(outer.read(directory/'POLICY.json')))
        old=json.loads(outer.read(Path(op['history_path'])/pid/'controller.json',2000000));scope=Scope('meshticulum.public-parent-snapshot-scope.v1','controller',os.geteuid(),pid,policy.digest,old['expected_database_sha256'],None,helpers.controller_boot(),min(outer.epoch()+45000000,op['absolute_device_window']['expires_epoch_us']))
        actual=collect(directory,scope,clock=outer.check,boot_reader=helpers.controller_boot).to_dict()
        need(actual['status']=='scoped_rows_observed' and actual['scoped_rows_complete'] and actual['policy_expired_at_observation']
            and not actual['pending_unknown'] and canonical({k:actual[k] for k in projection})==canonical({k:old[k] for k in projection}),'batch_old_controller_changed')
        for u in histories:retained_controller_caps(u,histories[u]['previous'][i],actual)
        controllers.append(actual);outer.check()
    cfg=json.loads(outer.read(op['config_path'],30000));need(cfg['runtime_sha256']==op['runtime_sha256'] and cfg['manifest_sha256']==op['manifest_sha256'],'batch_installed_config_changed')
    provision=outer.read(lantern/'firmware/meshticulum-bench/pilot_grant_bootstrap.py',500000);bootstrap=outer.read(lantern/'firmware/meshticulum-bench/runtime_bootstrap.py',500000)
    need(cfg['provision_sha256']==sha(provision) and cfg['runtime_bootstrap_sha256']==sha(bootstrap),'batch_native_support_pin_changed')
    outer.boundary()
    password=getpass.getpass('A/B sudo for one finite public two-profile comparison (memory only): ')
    try:
        epoch,mono=outer.phase();end=epoch+600000000
        cfg.update(schema=SSH_SCHEMA,lora_mcu_artifact=op['lora_mcu_artifact'],handoff_end_epoch_us=end,comparison_clock=dict(phase_start_epoch_us=epoch,phase_end_epoch_us=end,original_monotonic_us=mono,deadline_monotonic_us=mono+600000000));parent_id=secrets.token_hex(16);out=Path(op['output_directory'])/('lora-sf7-sf5-comparison-'+parent_id)
        consume_scope(op,args.operation_sha256,parent_id,epoch,end,outer=outer);outer.boundary();out.mkdir(mode=0o700);outer.check()
    except BaseException:
        password=None;raise
    result=dict(schema='meshticulum.public-lora-sf7-sf5-comparison-result.v1',parent_id=parent_id,complete=False,qualification_complete=False,private_production=False,refunds=0,original_phase_start_epoch_us=epoch,original_phase_end_epoch_us=end,samples=[],rpc_clients=[],links=[],native_final={},previous_parent_ids=op['history_parent_ids'])
    OuterSSHOperator=outer_ssh_operator_type(SSHOperator,outer)
    operator=parent=current=None;owned=[];owners={};priors={};preparations={u:{} for u in histories};facts={}
    def native(u,action,payload):
        outer.boundary();started=time.monotonic_ns();left=min(120 if action=='capture' else 45,max(0,int((end-time.time_ns()//1000)/1000000)-8))
        need(left>0,'comparison_original_remote_window_expired')
        command=rpc_command(operator,cfg,op,u,action,payload,left,parent_id,args.operation_sha256)
        job=None;audit=dict(unit=u,action=action,remote_terminal=False,local_terminal=False,
            callback_budget_seconds=left,original_callback_end_epoch_us=command.original_end_epoch_us)
        result['rpc_clients'].append(audit)
        try:
            argv=operator._argv(u,command,root=True);need(len(argv[-1].encode())<125000,'comparison_native_argv_bound')
            job=Job(argv,password=operator.passwords.get(u),deadline=min(command.local_deadline,operator.deadline),clock=operator.clock);owned.append(job)
            code=job.wait();audit['output_sha256']=sha(bytes(job.output))
            if code!=0:audit['failure']=rpc_failure_diagnostic(bytes(job.output),code)
            need(code==0,'comparison_native_RPC_failed');value=one_record(bytes(job.output),'meshticulum.private-resume-native-rpc.v1')
            need(value['unit']==u and value['action']==action and value['radio_opened'] is False and value['private_production'] is False,'comparison_native_scope_changed')
            outer.boundary();audit['remote_terminal']=True;return value
        finally:
            try:
                if job is not None:
                    job.close();audit['local_terminal']=job.process.poll() is not None;audit['returncode']=job.process.returncode
                    job.password=None;job.output.clear()
            finally:
                audit['callback_elapsed_us']=(time.monotonic_ns()-started)//1000
                original=sys.exc_info()[1]
                try:
                    cleanup=cleanup_rpc_input(operator,cfg,op,command.staged)
                    audit['management_input_cleanup_confirmed']=True
                    audit['management_input_already_absent']=cleanup['already_absent']
                except BaseException:
                    audit['management_input_cleanup_confirmed']=False
                    if original is None:raise
                    original.add_note('comparison_management_input_cleanup_unproved')
    def guarded(u,action='guard'):
        return native(u,action,dict(policy=policy.to_dict(),owner=owners[u],prior=priors[u],preparation_sha256=preparations[u],review_sha256=args.operation_sha256,expected_cut_run=None))
    try:
        operator=OuterSSHOperator(cfg,provision_source=provision,runtime_source=bootstrap,passwords={'unit-a':password,'unit-b':password},parallel_preparation=True,clock=outer.monotonic,epoch=outer.epoch,outer_deadline_monotonic=outer.deadline);password=None;operator.open_connections()
        status=inspect_current_a(operator,op,manifest,None,min_remaining=160);result['pre_run_radio_status']=status
        import base64
        public=[]
        for piece in pieces:
            f=piece['fixture'];public.append(dict(manifest=base64.b64encode(outer.read(piece['path']/'pack/manifest.json',65536)).decode(),signature=base64.b64encode(outer.read(piece['path']/'pack/manifest.sig.json',4096)).decode(),trust=base64.b64encode(outer.read(piece['path']/'trust.json',16384)).decode(),pins=dict(manifest=f['manifest_sha256'],signature=f['signature_sha256'],trust=f['trust_sha256']),wanted=[piece['sample']['chunk_sha256']],recipient='unit-a'))
            absent=native('unit-a','observe-cache',public[-1])['observation'];need(not absent['verified_present'] and absent['absent']==public[-1]['wanted'],'batch_fresh_resource_exists')
            operator.seed(helpers.seed_package(cfg,'unit-c','source',piece['path']/'pack',piece['trust'],f['manifest_sha256'],piece['cache'].value));operator.seed(helpers.seed_package(cfg,'unit-a','recipient',piece['path']/'pack',piece['trust'],f['manifest_sha256'],piece['cache'].value))
        facts={u:stable(operator.facts(u)) for u in histories}
        capture_payloads={u:dict(parent_id=parent_id,history=histories[u],facts=facts[u],phase_start_epoch_us=epoch,phase_end_epoch_us=end,review_sha256=args.operation_sha256,capture_storage='plain-json-v1') for u in histories}
        captures=parallel_captures(native,capture_payloads)
        for u in ('unit-c','unit-b','unit-a'):
            cap=captures[u];need(cap['actual_history_verified'] is True and cap['refunds']==0,'batch_capture_unqualified')
            priors[u]=dict(schema='meshticulum.public-resume-conservative-prior.v1',history_binding_sha256=cap['history_binding_sha256'],legacy_inventory=histories[u]['legacy_inventory'],previous_parent_ids=op['history_parent_ids'],full_caps=histories[u]['previous'][-1]['paid_caps'],complete_global_debt=False,actual_cost=None,refunds=0)
        result['captures']=captures
        registry={u:dict(observation_sha256=sha(canonical(priors[u])),history_binding_sha256=captures[u]['history_binding_sha256'],full_caps=priors[u]['full_caps']) for u in histories};units=[]
        peers=current_peers(cfg);extra={u:Cost() if u=='unit-b' else Cost(1024,240000000) for u in histories}
        for u in histories:
            old=Cost(**priors[u]['full_caps']);units.append(UnitCaps(u,cfg['units'][u]['host_boot_id'],PeerID(**peers[u]),old.plus(extra[u]),old,sha(canonical(priors[u]))))
        aggregate=Cost()
        for unit in units:aggregate=aggregate.plus(unit.limits)
        policy=ParentPolicy(parent_id,helpers.controller_boot(),epoch,end-5000000,mono,mono+595000000,tuple(units),aggregate,sha(canonical(registry)),KIND)
        owner=dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind=KIND,policy_digest=policy.digest,authorization=op['authorization'],review_sha256=args.operation_sha256,prior_registry=registry,facts=facts,phase_start_epoch_us=epoch,phase_end_epoch_us=end,native_phase={u:captures[u]['native_phase'] for u in histories},runtime_sha256=op['runtime_sha256'],manifest_sha256=op['manifest_sha256'],rf_record=json.loads(outer.read(op['rf_confirmation_path'],20000)),rf_record_sha256='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9',rf_profile=asdict(profile_for('sf7-500')),radio_profile_name='lora-sf7-sf5',lora_comparison=op['comparison_descriptor'],lora_mcu_artifact=op['lora_mcu_artifact'],core_commit=op['core_commit'],comparison_scope_sha256=op['comparison_scope_sha256'],history_receipt_sha256=op['history_receipt_sha256'],complete_global_debt=False,actual_cost=None)
        context=ComparisonContext.from_owner(owner,policy);owners={u:dict(owner,history_binding_sha256=captures[u]['history_binding_sha256'],capture_record_sha256=captures[u]['capture_record_sha256']) for u in histories}
        for name,value in (('POLICY.json',policy.to_dict()),('OWNER_APPROVAL.json',owners),('PRIOR.json',priors),('CONFIG.json',cfg),('OPERATION.json',op)):
            outer.check();write(out/name,value);outer.check()
        parent=ParentCaps.enroll(out/'caps.sqlite',policy,clock=outer.check,boot_reader=helpers.controller_boot)
        parallel_enroll(native,policy,owners,priors,capture_payloads)
        original_prepare=operator.prepare
        def prepare(package):
            # Each parallel metadata thread owns its own context; no controller
            # SQLite owner or main-thread retained object crosses this boundary.
            local=ComparisonContext(op['comparison_descriptor'],policy,phase_end_epoch_us=end)
            local.package(package);guarded(package['unit']);receipt=original_prepare(package)
            pin=receipt['prepared_identity_sha256'];need(receipt['run_id'] not in preparations[package['unit']],'batch_prepare_reused')
            preparations[package['unit']][receipt['run_id']]=pin;return receipt
        operator.prepare=prepare
        load('batch_launch_guard',lantern/'firmware/meshticulum-bench/pair_launch_guard_sender_first.py').install(operator,guarded,lora_sender_first=True)
        for index,piece in enumerate(pieces):
            # No current Controller or application lease is carried into the next resource.
            status=inspect_current_a(operator,op,manifest,status,min_remaining=80);result.setdefault('sample_radio_status',[]).append(status)
            issued,issued_mono=outer.check();expires=issued+180000000;need(expires<=policy.expires_epoch_us-15000000,'batch_fresh180_window_unavailable')
            current=Controller(operator,campaign_id=piece['sample']['campaign_id'],resource_sha256=piece['sample']['resource_sha256'],expires_epoch_us=expires,handoff_deadline_monotonic=operator.deadline,peers=peers,boots={u:v['host_boot_id'] for u,v in cfg['units'].items()},parent=parent,radio_permission=dict(country='MY',antenna_confirmed=True),radio_profile=piece['sample']['radio_profile_name'],lora_sender_first=True,comparison_context=context,clock=outer.monotonic,epoch=outer.epoch)
            verified=current.direct_piece(piece['request'],piece['response'],'lora',publisher='unit-c',requester='unit-a');metrics=bulk_metrics(current.links[1],verified,piece['piece'],piece['sample']['radio_profile_name'])
            current=finish_sample(operator,current,result,index=index,
                original_application=dict(epoch_us=issued,monotonic_us=issued_mono,expires_epoch_us=expires),
                verified=verified,metrics=metrics)
        for u in ('unit-c','unit-b','unit-a'):
            result['native_final'][u]=guarded(u,'audit');s=result['native_final'][u]['snapshot'];need(s['claims']==s['prepared']==s['starts']==(0 if u=='unit-b' else 4) and s['debt']==asdict(policy.unit(u).limits) and not s['uncertain'],'batch_final_accounting_changed')
        result['complete']=len(result['samples'])==2
    except BaseException as e:result.update(error_type=type(e).__name__,error=safe_reason(e))
    finally:
        password=None
        if current is not None:
            result['links'].extend(current.links)
            try:outer.boundary();cleanup_current(current,result);outer.boundary()
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
            if not result['owned_local_clients_terminal'] or not all(v['remote_terminal'] and v['local_terminal'] and v.get('management_input_cleanup_confirmed') is True for v in result['rpc_clients']):result['complete']=False
            try:operator.close_connections()
            except BaseException:result['complete']=False;result.setdefault('cleanup_errors',[]).append('owned_connection_close_failed')
            operator.passwords.clear()
        if parent is not None:
            try:result['controller_parent']=parent.snapshot()
            except BaseException:result['complete']=False;result.setdefault('cleanup_errors',[]).append('controller_snapshot_unproved')
            finally:
                try:need(parent.close(),'batch_parent_close_uncertain')
                except BaseException:result['complete']=False;result.setdefault('cleanup_errors',[]).append('controller_close_unproved')
        try:outer.boundary();result['within_original_outer']=True
        except BaseException:result['within_original_outer']=False;result['complete']=False
        result['original_outer_controller_boot']=op['original_outer_controller_boot'];result['original_outer_monotonic_us']=op['original_outer_monotonic_us'];result['original_outer_deadline_monotonic_us']=op['original_outer_deadline_monotonic_us']
        result['finished_epoch_us']=time.time_ns()//1000;result['controller_elapsed_us']=result['finished_epoch_us']-epoch;result['placement']=op['placement'];result['within_original_phase']=result['finished_epoch_us']<end
        if not result['within_original_phase']:result['complete']=False
        # Retain a truthful failure receipt even after the original window;
        # this local audit write grants no controller/native operation.
        write(out/'RESULT.json',result);print(json.dumps(dict(complete=result['complete'],saved=str(out/'RESULT.json'),qualification_complete=False)));return 0 if result['complete'] else 1


if __name__=='__main__':raise SystemExit(main())
