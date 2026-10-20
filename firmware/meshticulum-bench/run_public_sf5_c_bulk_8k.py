# SPDX-License-Identifier: Apache-2.0
"""Private fresh public C-source encrypted SF5 8 KiB runner; requires exact review.

One signed resource and one original180-second application within one new600-second phase. No installation,
firmware reset, private production or publication. All retained debt stays paid.
"""
import argparse
import base64
from dataclasses import asdict
import datetime
import getpass
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import time
import lzma

SOURCE = Path(__file__).resolve().parent
BASE = Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-native-window-20261011')
WORK = BASE.parent
FIXTURE = SOURCE/'fresh-sf5-8k'
CORE_COMMIT = '0b2753397b705b629ed8aa417824a48e6c1a3d98'
OLD_RUNTIME = '5c0b09bb1307b7ec6cca31dd3dbb5d9563d8b1410200f9046359dc1dcb89f45d'
PAIR_SHA = 'bb04a5800ebf3b673ea9ab2269581e63da0b39a2ab3b7e3d2c0380d18271ecff'
RF_SHA = '40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9'
RF_FILE = WORK/'meshticulum-abc-deployment-20261009/OWNER_RF_CONFIRMATION.json'
HISTORY = ('28765d19f654ce3162525afe9c3f89c5', '5ad7e65b7e50901a6f63629c04a776e2', '4d1b5d6fe8d4b022c5cf1e21cef7f8ca', 'd60d8308326314a7753e651fa43a99e3', '4559277fa43913ce3935b078d6744a55', '4034f8979b985660df21859606c4f8a7', 'ba4572e95e598ad6c17fc954c3792db7', '1df30b47fa36b8d47328fb886528a7a9', 'f153a7a87a2803db85078df81a6a1aaf', '25a990a2d565c50b237ee45812a8c75f', 'cb5869e6aa8de545a267007b085d4762', 'f6e147e70ec7ce07f673ea5e9cd2cdc2', 'b8f9b5d9c5479c8d363c7b7e8014bc3b', '806fdca055e79dfc0b86ff8f304bcdf9', '4ea23c6b8fa83366904ac62ffa98e2bf', 'c26cd03a8fd427daf6b8d894e4b1d04c', '8dfcfdfe8f8be2c0b326520d70eab7f5', '6f89e2a21919991e15224e393446928c', '62bcef4f1c74532fd7ab3ada1b8b94e2', '141c892d83b6064a800c11aab6444f28', '05c36e09fd4056c94d341d2dd32243f4', 'bccb99afe6589968190b131eeaf372b3', '487ae574816ff5444de03e3daa4401e4', 'e01508ddcae7389728add074de4876b7', '2429969fcbb0f57b3deaeda7187036c2', '7746ddba2544ce79c01a195f1e8746ce')
HISTORY_PREPARATION = Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-retain7746-20261012/resume-history-preparation-26')
HISTORY_RECEIPT_SHA = '5ce7e5916db3b2453fb058f8334a246022d1266a926ce8319c979abf78405272'
PROJECTION = ('status','scoped_rows_complete','pending_unknown','policy_row_sha256','prior_lineage','detail','files')
HEX = re.compile(r'[0-9a-f]{64}\Z')
UNITS = ('unit-a','unit-b','unit-c')
PUBLISHER = 'unit-c'
REQUESTER = 'unit-a'
A_RADIO_BOOT = '000008f6a51b5bf80000000000000027'
A_RADIO_STATUS = dict(mcu_boot=A_RADIO_BOOT,boot_attempts=0,
                      boot_reserved_estimated_airtime_us=0)
RPC_SCHEMA = 'meshticulum.private-resume-native-rpc.v1'
RPC_MODULE = 'lantern_meshticulum.bench.pilot_resume_rpc'
RETAINED_CAPTURE_PARENT = 'd0f37a4030fbe55d2829e8c0cebcee5b'
SOURCE_PREPARATION_RESULT = BASE/'resume-public-20261011T023720Z-d0f37a40/RESULT.json'
SOURCE_PREPARATION_RESULT_SHA = 'd430f99d4970f086650f25bb43257ebeaad253674a37d8eb29e4974ae57c8ae8'


def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw): return hashlib.sha256(raw).hexdigest()
def need(value,reason):
    if not value: raise RuntimeError(reason)
def read(path,limit=300000):
    path = Path(path).absolute()
    for part in (path.parent,*path.parent.parents):
        s=part.lstat(); need(stat.S_ISDIR(s.st_mode) and part.resolve()==part
                            and s.st_uid in (0,os.geteuid()) and not s.st_mode&0o022,'unsafe_local_ancestry')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        s=os.fstat(fd); need(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_size<=limit
                              and s.st_uid==os.geteuid() and not s.st_mode&0o022,'unsafe_local_input')
        raw=os.pread(fd,limit+1,0)
        ident=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        need(len(raw)==s.st_size and ident(s)==ident(os.fstat(fd))==ident(path.lstat()),'local_input_changed')
        return raw
    finally: os.close(fd)
def write(path,value):
    raw=canonical(value)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        view=memoryview(raw)
        while view:
            n=os.write(fd,view); need(n>0,'short_local_write'); view=view[n:]
        os.fsync(fd)
    finally: os.close(fd)
    return sha(raw)
def stable(f): return {key:f[key] for key in ('unit','host_boot_id','services','service_lifetimes')}
def load_source(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value

def ordered_fixture_rows(fixture):
    need(len(fixture['chunks'])==1 and fixture['chunks'][0]['purpose']=='bulk',
         'one_fresh_bulk_piece_required')
    row=fixture['chunks'][0]
    need(all(type(row[key]) is int and row[key]==size for key,size in
             (('chunk_bytes',8164),('response_bytes',8192),('request_bytes',74))),
         'exact_8k_resource_required')
    return list(fixture['chunks'])


def qualification_sequence(controller, exchanges):
    need(type(exchanges) is list and len(exchanges)==1,'one_exact_bulk_exchange_required')
    return controller.direct_piece(*exchanges[0],'lora',publisher=PUBLISHER,requester=REQUESTER)


def inspect_a_before_seed(operator,review,config,profile):
    """Fresh HELLO/STATUS only; inspection grants no transport authority."""
    expected=review['radio_status_expected']
    need(type(expected) is dict and expected==A_RADIO_STATUS
         and all(type(expected[k]) is int for k in ('boot_attempts','boot_reserved_estimated_airtime_us')),
         'reviewed_A_radio_status_changed')
    need(profile.max_boot_attempts==256 and profile.max_boot_estimated_airtime_us==60000000,
         'reviewed_radio_profile_limits_changed')
    before=stable(operator.facts('unit-a'))
    inspection=operator._action('unit-a','inspect',schema='meshticulum.native-bench-inspection.v1')
    after=stable(operator.facts('unit-a'))
    need(before==after and before['unit']=='unit-a'
         and before['host_boot_id']==config['units']['unit-a']['host_boot_id'],
         'A_inspection_service_or_boot_changed')
    need(inspection['schema']=='meshticulum.native-bench-inspection.v1'
         and inspection['unit']=='unit-a' and inspection['host_boot_id']==before['host_boot_id']
         and inspection['bundle_manifest_sha256']==review['manifest_sha256']
         and inspection['services_before']==inspection['services_after']==before['services']
         and inspection['radio_opened'] is True
         and all(type(inspection[k]) is int and inspection[k]==0 for k in ('arm_commands','tx_commands')),
         'A_inspection_scope_or_commands_changed')
    radio=inspection['radio']
    need(radio['disarmed'] is True and radio['firmware']=='mtb1-5577916527' and radio['usb_serial']=='F8:5B:1B:A5:F6:08' and radio['mcu_boot']==A_RADIO_BOOT
         and all(type(radio[k]) is int and radio[k]==expected[k]
                 for k in ('boot_attempts','boot_reserved_estimated_airtime_us')),
         'A_radio_status_stale_or_armed')
    remaining=profile.max_boot_attempts-radio['boot_attempts']
    need(remaining>=80 and 0<=radio['boot_reserved_estimated_airtime_us']<profile.max_boot_estimated_airtime_us,
         'A_radio_remaining_budget_insufficient')
    return dict(inspection=inspection,facts_before=before,facts_after=after,
                remaining_attempts=remaining,min_remaining_attempts=80,
                remaining_estimated_airtime_us=profile.max_boot_estimated_airtime_us-radio['boot_reserved_estimated_airtime_us'])


def bulk_metrics(link, verified, piece):
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
    return dict(useful_verified_bytes=8164,mr1_response_bytes=8192,data_frames=43,
        sender_start_to_confirmed_ack_us=duration,useful_encrypted_rf_goodput_Bps=8164*1e6/duration,
        data_first_to_final_ACK_us=useful_span,data_first_to_final_ACK_useful_Bps=8164*1e6/useful_span,
        stretch_target_Bps=5000,stretch_target_met=8164*1e6/useful_span>=5000,
        radio_profile=asdict(profile_for('sf5-500')),minimum_target_Bps=1000,aspirational_target_Bps=1500,minimum_target_met=8164*1e6/duration>=1000,
        request_to_publisher_verification_us=verified['request_to_verification_us'],
        request_to_publisher_verification_Bps=verified['useful_verified_bytes_per_second'],
        received_durable_response_sha256=rx['service_object_sha256'],publisher_verified_chunk=verified['chunk_sha256'],
        attempts=journals,flow={unit:result['flow'] for unit,result in link['outcomes'].items()},
        driver_timing_events={unit:result['flow']['events'] for unit,result in link['outcomes'].items()},
        receiver_ack_grace_seconds=20,measured_airtime_us=None,measured_energy_joules=None,measured_eirp_dbm=None,
        security_scope='Pinned current SessionCipher DATA/ACK AEAD; public signed publisher verification',
        sustained_scope='One8164-byte encrypted RF sample; repeats and completion distributions remain separate')

def validate_review(args):
    raw=read(args.review_packet); need(sha(raw)==args.review_sha256,'review_packet_pin_changed')
    p=json.loads(raw)
    need(p['schema']=='meshticulum.private-public-sf5-c-bulk-8k-review.v1'
         and p['runner_sha256']==sha(read(Path(__file__),100000)),'wrong_runner_review')
    need(p['native_adapter_contract']==RPC_SCHEMA and p['native_adapter_reviewed'] is True
         and p['provision_held_staging_hook_reviewed'] is True,'native_integration_missing')
    need(p['core_commit']==CORE_COMMIT and p['runtime_sha256']!=OLD_RUNTIME
         and all(HEX.fullmatch(p[k]) for k in ('runtime_sha256','manifest_sha256',
                  'native_adapter_sha256','public_cache_observer_sha256')),'new_guarded_runtime_required')
    need(p['authorization']['human_statement']=='run it bro' and p['authorization']['count_authorized']==1
         and p['authorization']['public_only'] is True and p['authorization']['private_production'] is False,
         'exact_current_test_authorization_missing')
    preparation=read(SOURCE_PREPARATION_RESULT)
    need(sha(preparation)==SOURCE_PREPARATION_RESULT_SHA,'prior_source_preparation_changed')
    prior=json.loads(preparation)
    need(prior['parent_id']==RETAINED_CAPTURE_PARENT and prior['complete'] is False
         and not prior.get('links') and 'original_application' not in prior
         and prior['private_production'] is False and prior['refunds']==0,'prior_source_stage_not_preapplication')
    need(type(p['phase_seconds']) is int and p['phase_seconds']==600
         and p['application_seconds']==180 and p['qualification_kind']=='c-bulk-8k-v1'
         and p['bulk_useful_bytes']==8164,'original_bounds_changed')
    core,lantern=Path(p['core_path']),Path(p['lantern_path'])
    for path,pin in ((core,p['core_commit']),(lantern,p['lantern_commit'])):
        need(re.fullmatch('[0-9a-f]{40}',pin),'unqualified_source_commit')
        actual=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True,timeout=5).strip()
        need(actual==pin and not subprocess.check_output(['git','-C',str(path),'status','--porcelain'],
                                                         text=True,timeout=5),'source_commit_or_worktree_changed')
    archive=read(p['runtime_archive'],2000000)
    need(sha(archive)==p['runtime_sha256'],'runtime_archive_changed')
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
        members=tar.getmembers(); names=[m.name for m in members]
        need(len(names)==len(set(names)) and all(m.isfile() and not Path(m.name).is_absolute()
             and '..' not in Path(m.name).parts for m in members),'unsafe_runtime_archive')
        mf=tar.extractfile('manifest.json').read(65537)
        need(len(mf)<=65536 and sha(mf)==p['manifest_sha256'],'runtime_manifest_changed')
        manifest=json.loads(mf)
        need(manifest['private_services_enabled'] is False and manifest['public_parent_claims_required'] is True,
             'not_public_parent_runtime')
        need(set(names)==set(manifest['files'])|{'manifest.json'},'runtime_members_changed')
        for name,pin in manifest['files'].items():
            raw=tar.extractfile(name).read(500001)
            need(len(raw)<=500000 and sha(raw)==pin,'runtime_member_changed')
            source=core/'src'/name if name.startswith('meshticulum/') else (
                   lantern/'software/meshticulum-integration/src'/name if name.startswith('lantern_meshticulum/') else None)
            if source is not None: need(sha(read(source,500000))==pin,'source_runtime_closure_changed')
    rpc=RPC_MODULE.replace('.','/')+'.py'
    need(manifest['files'].get(rpc)==p['native_adapter_sha256'],'native_RPC_not_packaged')
    need(manifest['files'].get('lantern_meshticulum/bench/public_cache_observer.py')
         ==p['public_cache_observer_sha256'],'cache_observer_not_packaged')
    required_support={'firmware/meshticulum-bench/run_public_pilot.py',
                      'firmware/meshticulum-bench/pilot_grant_bootstrap.py',
                      'firmware/meshticulum-bench/runtime_bootstrap.py'}
    need(required_support<=set(p['source_pins']),'reviewed_support_pins_missing')
    for rel,pin in p['source_pins'].items():
        need(not Path(rel).is_absolute() and '..' not in Path(rel).parts,'invalid_source_pin')
        need(sha(read(lantern/rel,500000))==pin,'reviewed_support_source_changed')
    need(sha(read(SOURCE/'pair_launch_guard_sender_first.py',20000))==PAIR_SHA,'pair_guard_changed')
    need(sha(read(RF_FILE,20000))==RF_SHA,'physical_owner_confirmation_changed')
    need(HEX.fullmatch(p['fixture_sha256']) and sha(read(Path(p['fixture_path'])/'FIXTURE.json',50000))==p['fixture_sha256'],'fresh_fixture_changed')
    preparation=Path(p['history_preparation_path']).absolute()
    need(preparation==HISTORY_PREPARATION and p['history_receipt_sha256']==HISTORY_RECEIPT_SHA,'exact_26_history_preparation_required')
    preflight_raw=read(p['preflight_completion'],2000000)
    need(sha(preflight_raw)==p['preflight_completion_sha256'],'native_preflight_changed')
    preflight=json.loads(preflight_raw)
    need(preflight['complete'] is True and preflight['archive_sha256']==p['runtime_sha256']
         and preflight['manifest_sha256']==p['manifest_sha256']
         and preflight['history_receipt_sha256']==HISTORY_RECEIPT_SHA
         and preflight['callbacks_exit_zero']==3 and preflight['owned_local_clients_terminal'] is True
         and preflight['native_state_writes'] is False and preflight['radio_opened'] is False
         and preflight['started_epoch_us']<=preflight['finished_epoch_us']<preflight['original_end_epoch_us']
         and set(preflight['observation_sha256'])==set(UNITS),'exact_completed_readonly_native_preflight_required')
    need(type(p['absolute_device_window']) is dict
         and p['absolute_device_window']['expires_epoch_us']-p['absolute_device_window']['issued_epoch_us']==720000000
         and p['absolute_device_window']['count_authorized']==1,'one_original_720_second_absolute_window_required')
    return p,core,lantern,manifest

# Only metadata enters argv; bounded compressed SHA-checked native adapter
# input. No seed bodies, keys or MR1 response bodies are passed through it.
RPC_ENTRY=r'''
import base64,hashlib,importlib,json,os,stat,sys,lzma,types
from pathlib import Path
root,pin,adapter_pin,action,unit,blob,input_sha=sys.argv[1:]
def need(v,r):
 if not v:raise RuntimeError(r)
def read(path,limit):
 for p in (path.parent,*path.parent.parents):
  s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022 and p.resolve()==p,'runtime_ancestry')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
 try:
  s=os.fstat(fd);need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=limit,'runtime_record')
  raw=os.pread(fd,limit+1,0);need(len(raw)==s.st_size and os.fstat(fd)==path.lstat(),'runtime_record_changed');return raw
 finally:os.close(fd)
need(os.geteuid()==0,'actual_root_required');root=Path(root)
mraw=read(root/'manifest.json',65536);need(hashlib.sha256(mraw).hexdigest()==pin,'runtime_manifest_pin')
m=json.loads(mraw);need(m['private_services_enabled'] is False and m['public_parent_claims_required'] is True,'public_runtime_required')
for name,h in m['files'].items():
 part=Path(name);need(not part.is_absolute() and '..' not in part.parts,'runtime_member')
 limit=2250000 if m.get('schema')=='meshticulum.bench-runtime-bundle.v2' and name=='execution.zip' else 500000
 need(hashlib.sha256(read(root/part,limit)).hexdigest()==h,'runtime_member_pin')
sources=m['source_files'] if m.get('schema')=='meshticulum.bench-runtime-bundle.v2' else m['files']
need(sources.get('lantern_meshticulum/bench/pilot_resume_rpc.py')==adapter_pin,'native_adapter_pin')
need(len(blob)<=112000,'RPC_encoded_bound')
compressed=base64.b64decode(blob,validate=True);need(len(compressed)<=84000,'RPC_compressed_bound')
d=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=33554432);raw=d.decompress(compressed,max_length=2000001)
need(len(raw)<=2000000 and d.eof and not d.unused_data
 and hashlib.sha256(raw).hexdigest()==input_sha,'RPC_input_bound_or_pin')
if m.get('schema')=='meshticulum.bench-runtime-bundle.v2':
 hraw=read(root/'execution_runtime.py',500000);need(hashlib.sha256(hraw).hexdigest()==m['execution_layout']['loader_sha256'],'image_loader_pin')
 need('_lantern_execution_runtime' not in sys.modules,'image_loader_duplicate')
 helper=types.ModuleType('_lantern_execution_runtime');helper.__file__=str(root/'execution_runtime.py');sys.modules[helper.__name__]=helper
 exec(compile(hraw,helper.__file__,'exec'),helper.__dict__);helper.install(root,mraw,expected_manifest_sha256=pin)
else:sys.path.insert(0,str(root))
module=importlib.import_module('lantern_meshticulum.bench.pilot_resume_rpc')
need(getattr(module,'CONTRACT_SCHEMA',None)=='meshticulum.private-resume-native-rpc.v1','native_integration_contract_missing')
# dispatch MUST implement the reviewed real-admission integration described
# above; no local fallback, lambda approval or deserialized Snapshot allowed.
result=module.dispatch(action,unit,json.loads(raw),runtime_root=root,manifest_sha256=pin)
need(type(result) is dict and result.get('schema')=='meshticulum.private-resume-native-rpc.v1'
 and result.get('unit')==unit and result.get('action')==action,'RPC_result_scope')
print(json.dumps(result,sort_keys=True),flush=True)
'''


def retained_controller_caps(unit,row,observation):
    from lantern_meshticulum.bench.pilot_parent_caps import Cost,ParentPolicy
    from lantern_meshticulum.bench import fast_bundle_d1fd_history as d1fd,lora_enrollment_incomplete_history as incomplete_lora,lora_endpoint_incomplete_history as endpoint_lora
    policy=ParentPolicy.from_dict(row['policy']); local=policy.unit(unit)
    actual=next(v for v in observation['detail']['units'] if v['unit']==unit)
    need(observation['parent_id']==policy.parent_id and observation['policy_digest']==policy.digest==row['policy_digest']
         and observation['policy_expired_at_observation'] is True and observation['pending_unknown'] is False,
         'retained_controller_scope_changed')
    spent=Cost(**actual['prior_plus_prepaid_caps'])
    if policy.parent_id==incomplete_lora.PARENT:
        return incomplete_lora.controller_caps(unit,row,observation)
    if policy.parent_id==endpoint_lora.PARENT:
        return endpoint_lora.controller_caps(unit,row,observation)
    if policy.parent_id==d1fd.PARENT:
        d1fd.owner_view(row['owner'],policy,unit=unit)
        native=Cost(**row['actual_native_paid_caps'])
        native_unused=Cost(**row['conservative_unused_enrollment_caps'])
        controller_unused=Cost(**row['conservative_unused_controller_caps'])
        allocation=Cost() if unit=='unit-b' else Cost(256,60000000)
        need(actual['prior_plus_prepaid_caps']==row['actual_controller_paid_caps']
             and spent==local.prior_debt.plus(allocation) and native==local.prior_debt
             and native.plus(native_unused)==spent.plus(controller_unused)==local.limits
             and asdict(local.limits)==row['paid_caps']==actual['limit']
             and sha(canonical(row['controller_observed_endpoint_refs']))==d1fd.CONTROLLER_ENDPOINT_REFS_SHA256[unit]
             and row['native_controller_debt_views_may_be_summed'] is False
             and row['require_result_complete'] is row['original_cleanup_proved'] is False,
             'retained_d1fd_controller_and_native_views_changed')
    elif 'conservative_unused_enrollment_caps' in row:
        unused=Cost(**row['conservative_unused_enrollment_caps'])
        need(actual['prior_plus_prepaid_caps']==row['actual_controller_paid_caps']==row['actual_native_paid_caps']
             and spent.plus(unused)==local.limits and asdict(local.limits)==row['paid_caps']
             and actual['limit']==row['paid_caps'],'retained_unused_enrollment_caps_changed')
    else:need(row['paid_caps']==asdict(spent),'retained_paid_caps_changed')
    return row['paid_caps']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-packet',type=Path,required=True)
    parser.add_argument('--review-sha256',required=True)
    args=parser.parse_args(); os.umask(0o077)
    p,core,lantern,manifest=validate_review(args)
    global FIXTURE
    FIXTURE=Path(p['fixture_path']).absolute()
    sys.path[:0]=[str(core/'src'),str(lantern/'software/meshticulum-integration/src')]
    from lantern_meshticulum.bench.pilot_controller import Controller
    from lantern_meshticulum.bench.pilot_ssh import SSHOperator,Job,one_record
    from lantern_meshticulum.bench.pilot_parent_caps import Cost,UnitCaps,ParentPolicy,ParentCaps
    from lantern_meshticulum.bench.pilot_parent_snapshot import Scope,collect
    from lantern_meshticulum.bench.profiles import profile_for
    from meshticulum.ip.wire import IpLimits
    from meshticulum.contracts.identity import PeerID
    from meshticulum.resources import lamplighter as ll,resource
    helpers=load_source('reviewed_resume_helpers',lantern/'firmware/meshticulum-bench/run_public_pilot.py')
    fixture=json.loads(read(FIXTURE/'FIXTURE.json',50000)); trust=json.loads(read(FIXTURE/'trust.json',16384))
    need(sha(read(FIXTURE/'trust.json',16384))==fixture['trust_sha256']
         and sha(read(FIXTURE/'pack/manifest.sig.json',4096))==fixture['signature_sha256'],'fixture_trust_or_signature_changed')
    digest=fixture['manifest_sha256']; cache=ll.VerifiedCache(FIXTURE/'pack',trust,digest)
    proofs=ordered_fixture_rows(fixture)
    chunks=[cache.entries[row['content_address_sha256']] for row in proofs]
    exchanges=[]
    for index,(chunk,proof,size) in enumerate(zip(chunks,proofs,(8192,))):
        req=resource.encode_request('/packs/'+digest+'/chunks/'+chunk['sha256']+'.json',0,8164)
        reply=resource.BlockServer(cache.read).handle(req)
        need(chunk['sha256']==proof['content_address_sha256'] and sha(req)==proof['request_sha256']
             and sha(reply)==proof['response_sha256'] and len(reply)==size,'fixture_MR1_changed')
        exchanges.append((req,reply))
    wanted=[c['sha256'] for c in chunks]
    need(len(set(wanted))==1 and fixture['public_only'] is True and fixture['private_data'] is False,
         'nonpublic_or_reused_fixture')
    history_preparation=Path(p['history_preparation_path']).absolute()
    history_receipt=json.loads(read(history_preparation/'RECEIPT.json'))
    need(p['history_receipt_sha256']==sha(read(history_preparation/'RECEIPT.json')),'history_preparation_changed')
    controllers=[]; histories={}
    for index,parent_id in enumerate(HISTORY):
        matches=list(BASE.glob('*-'+parent_id[:8]))
        need(len(matches)==1,'exact_retained_controller_directory_required')
        path=matches[0]; policy=ParentPolicy.from_dict(json.loads(read(path/'POLICY.json')))
        need(policy.parent_id==parent_id,'retained_controller_parent_changed')
        old=json.loads(read(history_preparation/parent_id/'controller.json'))
        scope=Scope('meshticulum.public-parent-snapshot-scope.v1','controller',os.geteuid(),parent_id,
            policy.digest,old['expected_database_sha256'],None,helpers.controller_boot(),time.time_ns()//1000+45000000)
        actual=collect(path,scope,clock=helpers.parent_clock,boot_reader=helpers.controller_boot).to_dict()
        need(actual['status']=='scoped_rows_observed' and actual['scoped_rows_complete']
             and actual['policy_expired_at_observation'] and not actual['pending_unknown']
             and canonical({k:actual[k] for k in PROJECTION})==canonical({k:old[k] for k in PROJECTION}),
             'actual_controller_history_changed')
        controllers.append(actual)
    for unit,count in zip(UNITS,(41,64,63)):
        path=history_preparation/(unit+'.json'); raw=read(path,1000000)
        need(sha(raw)==history_receipt['payload_sha256'][unit],'history_input_changed')
        histories[unit]=json.loads(raw); inv=histories[unit]['legacy_inventory']; old=histories[unit]['previous']
        need(len(inv['rows'])==count and all(r['actual_cost'] is None for r in inv['rows'])
             and [v['policy']['parent_id'] for v in old]==list(HISTORY),'history_inventory_changed')
        for i,row in enumerate(old):
            paid=retained_controller_caps(unit,row,controllers[i])
            refs=sorted([dict(controller_allocation_id=a['allocation_id'],child=c)
                for a in controllers[i]['detail']['allocations'] for c in a['children'] if c['unit']==unit],
                key=lambda x:x['child']['run_id'])
            need(row['paid_caps']==paid and canonical(refs)==canonical(row['controller_endpoint_refs']),
                 'retained_controller_lineage_changed')
    config=json.loads(read(p['config_path'],30000)); provision=read(lantern/'firmware/meshticulum-bench/pilot_grant_bootstrap.py',500000)
    bootstrap=read(lantern/'firmware/meshticulum-bench/runtime_bootstrap.py',500000)
    need(config['runtime_sha256']==p['runtime_sha256'] and config['manifest_sha256']==p['manifest_sha256']
         and config['provision_sha256']==sha(provision) and config['runtime_bootstrap_sha256']==sha(bootstrap),
         'new_installed_runtime_config_required')
    password=getpass.getpass('A/B sudo (memory only; one public C-source encrypted 8 KiB test): ')
    epoch,mono=helpers.parent_clock(); phase_end=epoch+p['phase_seconds']*1000000
    absolute=p['absolute_device_window']
    need(absolute['issued_epoch_us']<=epoch and phase_end<=absolute['expires_epoch_us'],'original_absolute_window_unavailable')
    config['handoff_end_epoch_us']=phase_end; parent_id=secrets.token_hex(16)
    out=BASE/('sf5-c-bulk-8k-public-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+parent_id[:8])
    out.mkdir(mode=0o700)
    write(out/'REVIEW.json',p); write(out/'OLD_CONTROLLERS.json',controllers)
    result=dict(schema='meshticulum.actual-public-c-bulk-8k-execution.v1',complete=False,qualification_complete=False,
        publisher_verified=False,parent_id=parent_id,previous_parent_ids=list(HISTORY),private_production=False,
        complete_global_debt=False,actual_unknown_historical_cost=None,refunds=0,native_final={},captures={},rpc_clients=[],
        future_window_approved=False,sample_minimum_target_met=False,
        qualification_scope='single fresh 8 KiB encrypted RF measurement of the fixed runtime; sustained 1 kB/s and full programme gates remain open',
        measured_airtime_us=None,measured_energy_joules=None,measured_eirp_dbm=None)
    result['authorization_trace']=dict(human_statement=p['authorization']['human_statement'],
        scope='Continue transport qualification with a fresh sequence; preserve expired earlier source and paid transport attempts.',
        prior_paid_parent=HISTORY[-1],
        prior_source_preparation=dict(path=str(SOURCE_PREPARATION_RESULT),sha256=SOURCE_PREPARATION_RESULT_SHA,
            parent_id=RETAINED_CAPTURE_PARENT,retained_unenrolled_capture=True,old_phase_not_renewed=True),
        complete_global_debt=False,private_production=False)
    operator=parent=controller=None; owned=[]; preparations={u:{} for u in UNITS}; owner=None;priors={}; owners={};facts={}
    def native(unit,action,payload):
        raw=canonical(payload); blob=base64.b64encode(lzma.compress(raw,format=lzma.FORMAT_XZ,preset=6)).decode()
        need(len(raw)<=2000000 and len(blob)<=93336,'metadata_RPC_bound')
        runtime='/opt/meshticulum-bench/runtime-'+p['runtime_sha256'][:16]
        command=[config['units'][unit]['python'],'-I','-B','-c',RPC_ENTRY,runtime,p['manifest_sha256'],
                 p['native_adapter_sha256'],action,unit,blob,sha(raw)]
        argv=operator._argv(unit,command,root=True); need(len(argv[-1].encode())<100000,'native_argv_bound')
        job=Job(argv,password=operator.passwords.get(unit),deadline=min(operator.clock()+45,operator.deadline),clock=operator.clock)
        owned.append(job); begin=operator.clock()
        audit=dict(unit=unit,action=action,input_sha256=sha(raw),local_client_terminal=False,remote_RPC_terminal_proven=False)
        result['rpc_clients'].append(audit)
        try:
            need(job.wait()==0,'native_resume_RPC_failed'); value=one_record(bytes(job.output),RPC_SCHEMA)
            need(value['unit']==unit and value['action']==action and value['radio_opened'] is False
                 and value['private_production'] is False and value['global_debt_complete'] is False,'native_RPC_rebound')
            audit['remote_RPC_terminal_proven']=True
            operator.events.append(dict(unit=unit,action=action,schema=RPC_SCHEMA,
                elapsed_seconds=operator.clock()-begin,outside_handoff=operator.clock()>=operator.deadline))
            return value
        except BaseException as error:
            audit['error_type']=type(error).__name__; raise
        finally:
            try: job.close()
            finally:
                audit['local_client_terminal']=job.process.poll() is not None
                audit['returncode']=job.process.returncode
                audit['elapsed_seconds']=operator.clock()-begin
    def current_payload(unit):
        return dict(policy=policy.to_dict(),owner=owner,prior=priors[unit],
                    preparation_sha256=preparations[unit],review_sha256=args.review_sha256,
                    expected_cut_run=None)
    def guarded(unit,action='guard'):
        payload=current_payload(unit); payload['owner']=owners[unit]
        return native(unit,action,payload)
    try:
        operator=SSHOperator(config,provision_source=provision,runtime_source=bootstrap,
                             passwords={'unit-a':password,'unit-b':password},parallel_preparation=True); password=None
        write(BASE/('SF5_C_BULK_8K_REVIEW_USED-'+args.review_sha256+'.json'),dict(review_sha256=args.review_sha256,
            parent_id=parent_id,count_used=1,human_statement=p['authorization']['human_statement'],
            authorization_trace=result['authorization_trace'],original_phase_end_epoch_us=phase_end,retry_authorized=False))
        operator.open_connections()
        result['pre_run_radio_status']=inspect_a_before_seed(operator,p,config,profile_for('sf5-500'))
        public=dict(manifest=base64.b64encode(read(FIXTURE/'pack/manifest.json',65536)).decode(),
            signature=base64.b64encode(read(FIXTURE/'pack/manifest.sig.json',4096)).decode(),
            trust=base64.b64encode(read(FIXTURE/'trust.json',16384)).decode(),
            pins=dict(manifest=digest,signature=fixture['signature_sha256'],trust=fixture['trust_sha256']),wanted=wanted,recipient=REQUESTER)
        absent=native(REQUESTER,'observe-cache',public)['observation']; result['fresh_absence']=absent
        need(absent['status'] in ('target_absent','public_directory_absent')
             and absent['verified_present']==[] and set(absent['absent'])==set(wanted),'native_fresh_target_exists')
        # Signed seed creates source/recipient namespaces before immutable history capture.
        operator.seed(helpers.seed_package(config,PUBLISHER,'source',FIXTURE/'pack',trust,digest,cache.value))
        operator.seed(helpers.seed_package(config,REQUESTER,'recipient',FIXTURE/'pack',trust,digest,cache.value))
        facts={u:stable(operator.facts(u)) for u in UNITS}
        for unit in ('unit-c','unit-b','unit-a'):
            capture=native(unit,'capture',dict(parent_id=parent_id,history=histories[unit],facts=facts[unit],
                phase_start_epoch_us=epoch,phase_end_epoch_us=phase_end,review_sha256=args.review_sha256))
            need(capture['actual_history_verified'] is True and type(capture['history_binding_sha256']) is str
                 and HEX.fullmatch(capture['history_binding_sha256'])
                 and type(capture['capture_record_sha256']) is str and HEX.fullmatch(capture['capture_record_sha256'])
                 and capture['original_unknown_actual_cost'] is None and capture['refunds']==0,'actual_seal_capture_unqualified')
            result['captures'][unit]=capture
            priors[unit]=dict(schema='meshticulum.public-resume-conservative-prior.v1',
                history_binding_sha256=capture['history_binding_sha256'],legacy_inventory=histories[unit]['legacy_inventory'],
                previous_parent_ids=list(HISTORY),full_caps=histories[unit]['previous'][-1]['paid_caps'],
                complete_global_debt=False,actual_cost=None,refunds=0)
        registry={u:dict(observation_sha256=sha(canonical(priors[u])),history_binding_sha256=result['captures'][u]['history_binding_sha256'],
                   full_caps=priors[u]['full_caps']) for u in UNITS}
        rp=profile_for('sf5-500'); ip=IpLimits(); radio=Cost(rp.max_boot_attempts,rp.max_boot_estimated_airtime_us)
        tcp=Cost(ip_frames=ip.max_frames,tx_stream_bytes=ip.max_tx_stream_bytes,rx_stream_bytes=ip.max_rx_stream_bytes)
        extras={'unit-a':radio.plus(radio),'unit-b':Cost(),'unit-c':radio.plus(radio)}
        peers={u:next(v['peer'] for v in histories[u]['previous'][-1]['policy']['units'] if v['unit']==u) for u in UNITS}
        units=[]
        for unit in UNITS:
            old=Cost(**priors[unit]['full_caps']); added=extras[unit]
            units.append(UnitCaps(unit,config['units'][unit]['host_boot_id'],PeerID(**peers[unit]),
                                  old.plus(added),old,sha(canonical(priors[unit]))))
        aggregate=Cost()
        for unit in units: aggregate=aggregate.plus(unit.limits)
        policy=ParentPolicy(parent_id,helpers.controller_boot(),epoch,phase_end-5000000,mono,
                            mono+(p['phase_seconds']*1000000)-5000000,tuple(units),aggregate,sha(canonical(registry)))
        owner=dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind='c-bulk-8k-v1',policy_digest=policy.digest,
            authorization=p['authorization'],review_sha256=args.review_sha256,prior_registry=registry,facts=facts,
            phase_start_epoch_us=epoch,phase_end_epoch_us=phase_end,native_phase={u:result['captures'][u]['native_phase'] for u in UNITS},
            runtime_sha256=p['runtime_sha256'],manifest_sha256=p['manifest_sha256'],rf_record=json.loads(read(RF_FILE,20000)),
            native_python={u:config['units'][u]['python'] for u in UNITS},
            rf_record_sha256=RF_SHA,rf_profile=asdict(rp),radio_profile_name='sf5-500',complete_global_debt=False,actual_cost=None)
        owners={u:dict(owner,history_binding_sha256=result['captures'][u]['history_binding_sha256'],
            capture_record_sha256=result['captures'][u]['capture_record_sha256']) for u in UNITS}
        write(out/'CONFIG.json',config); write(out/'POLICY.json',policy.to_dict());write(out/'PRIOR.json',priors)
        write(out/'OWNER_APPROVAL.json',owners)
        parent=ParentCaps.enroll(out/'caps.sqlite',policy,clock=helpers.parent_clock,boot_reader=helpers.controller_boot)
        for unit in ('unit-c','unit-b','unit-a'):
            value=native(unit,'enroll',dict(policy=policy.to_dict(),owner=owners[unit],prior=priors[unit]))
            need(value['parent_id']==parent_id and value['policy_digest']==policy.digest,'native_enrollment_rebound')
        # Source seed contains response bodies ONLY to C. A seed has manifest/signature.
        # Every observation is native, signed public-cache validation; metadata
        # receipts carry no payloads and grant no transport or admission authority.
        baseline_files={}; result['cache_boundaries']=[]
        def observe_boundary(label,have):
            if controller is not None: controller.current()
            begin=helpers.parent_clock(); observation=native(REQUESTER,'observe-cache',public)['observation']
            expected=set(have); present=observation['verified_present']; absent=observation['absent']
            need(observation['manifest_sha256']==digest and observation['signature_sha256']==fixture['signature_sha256']
                 and observation['trust_sha256']==fixture['trust_sha256']
                 and set(observation['wanted'])==set(wanted) and len(observation['wanted'])==1
                 and set(present)==expected and len(present)==len(expected)
                 and set(absent)==set(wanted)-expected and len(absent)==1-len(expected)
                 and set(observation['files'])==expected
                 and observation['status']==('manifest_only' if not have else 'complete' if len(have)==1 else 'partial'),
                 'native_cache_boundary_set_or_context_changed')
            for piece in have:
                file=observation['files'][piece]; proof=proofs[wanted.index(piece)]
                need(file['stored_sha256']==proof['stored_chunk_sha256'] and file['bytes']==proof['chunk_bytes']
                     and (piece not in baseline_files or file==baseline_files[piece]),
                     'native_cache_boundary_verified_bytes_changed')
            if controller is not None: controller.current()
            evidence=dict(label=label,expected_verified_chunks=list(have),observation=observation,
                          started_clock=begin,finished_clock=helpers.parent_clock())
            result['cache_boundaries'].append(evidence)
            write(out/('CACHE-'+label+'.json'),evidence)
            for piece in have: baseline_files.setdefault(piece,dict(observation['files'][piece]))
            return observation
        observe_boundary('post-seed-manifest-only',[])
        need({u:stable(operator.facts(u)) for u in UNITS}==facts,'native_facts_changed_before_application')
        original_verify,original_cache_status=operator.verify,operator.cache_status
        boundary_counts={'verify':0,'cache_status':0}
        def checked_verify(unit,campaign,request,response):
            index=boundary_counts['verify']; need(index<1,'unexpected_extra_piece_verification')
            value=original_verify(unit,campaign,request,response)
            need(unit==REQUESTER and value['schema']=='meshticulum.pilot-verified-piece.v1'
                 and value['unit']==unit and value['campaign_id']==campaign==controller.campaign_id
                 and value['manifest_sha256']==digest and value['response_run_id']==response
                 and value['chunk_sha256']==wanted[index] and value['publisher_verified'] is True
                 and value['reused'] is False and value['private_admission'] is False and value['sage_imported'] is False
                 and value['original_application_expires_epoch_us']==controller.expires_epoch_us,
                 'native_boundary_verification_reused_or_rebound')
            observe_boundary('verified-piece-'+str(index),wanted[:index+1])
            boundary_counts['verify']+=1; return value
        operator.verify=checked_verify
        original_prepare=operator.prepare
        def checked_prepare(package):
            profile=dict(package['grant']['profile']); lease=profile.pop('lease_ms')
            expected=asdict(profile_for('sf5-500')); expected.pop('lease_ms')
            need(profile==expected and type(lease) is int and 10000<=lease<=90000,'prepared_SF5_profile_changed')
            guarded(package['unit']); receipt=original_prepare(package)
            pin=receipt.get('prepared_identity_sha256')
            need(type(pin) is str and HEX.fullmatch(pin),'held_staging_prepare_capture_missing')
            unit,run=package['unit'],receipt['run_id']; need(run not in preparations[unit],'prepare_identity_reused')
            preparations[unit][run]=pin; write(out/('PREPARED-'+unit+'-'+run+'.json'),dict(receipt=receipt,identity_sha256=pin))
            return receipt
        operator.prepare=checked_prepare
        load_source('reviewed_resume_pair_guard',SOURCE/'pair_launch_guard_sender_first.py').install(operator,guarded,lora_sender_first=True)
        app_epoch,app_mono=helpers.parent_clock(); expires=min(app_epoch+180000000,policy.expires_epoch_us-15000000)
        need(expires==app_epoch+180000000,'full_original_application_window_unavailable')
        result['original_application']=dict(epoch_us=app_epoch,monotonic_us=app_mono,expires_epoch_us=expires)
        controller=Controller(operator,campaign_id=secrets.token_hex(16),resource_sha256=digest,
            expires_epoch_us=expires,handoff_deadline_monotonic=operator.deadline,peers=peers,
            boots={u:r['host_boot_id'] for u,r in config['units'].items()},parent=parent,
            radio_permission=dict(country='MY',antenna_confirmed=True),radio_profile='sf5-500',lora_sender_first=True)
        bulk=qualification_sequence(controller,exchanges)
        result['sequence']=dict(bulk=bulk)
        need(bulk['reused'] is False and bulk['publisher_verified'] is True,
             'mixed_verification_failed')
        need(boundary_counts==dict(verify=1,cache_status=0) and len(controller.links)==2
             and all(link['phase']=='complete' for link in controller.links),'mixed_link_sequence_failed')
        result['bulk_metrics']=bulk_metrics(controller.links[1],bulk,proofs[0])
        controller.retire()
        final=native(REQUESTER,'observe-cache',public)['observation']; result['final_public_cache']=final
        need(set(final['verified_present'])==set(wanted) and not final['absent']
             and all(final['files'][d]['stored_sha256']==proofs[wanted.index(d)]['stored_chunk_sha256'] for d in wanted),
             'native_final_cache_does_not_match_exact_fresh_pieces')
        for unit in ('unit-c','unit-b','unit-a'):
            result['native_final'][unit]=guarded(unit,'audit'); snap=result['native_final'][unit]['snapshot']
            paid=Cost(**priors[unit]['full_caps']).plus(extras[unit])
            need(snap['claims']==snap['prepared']==snap['starts']=={'unit-a':2,'unit-b':0,'unit-c':2}[unit] and not snap['uncertain']
                 and snap['debt']==asdict(paid),'native_final_child_accounting_unqualified')
        need({u:stable(operator.facts(u)) for u in UNITS}==facts,'native_facts_changed_after_application')
        result.update(complete=True,publisher_verified=True,
                      sample_minimum_target_met=result['bulk_metrics']['minimum_target_met'],
                      qualification_complete=False)
    except BaseException as error:
        result.update(error_type=type(error).__name__,error=str(error)[:400])
    finally:
        password=None
        if controller is not None:
            result['links']=controller.links;result['verified']=controller.verified
            try: result['cleanup']=controller.retire()
            except BaseException as error: result['complete']=False;result['cleanup_error']=type(error).__name__
            if any('error' in r or 'stop_error' in r or 'retirement_error' in r for r in result.get('cleanup',[])): result['complete']=False
        if operator is not None:
            native_terminal=True
            for (unit,run),job in operator.jobs.items():
                try:
                    status=operator.status(unit,run)
                    need(status['unit']==unit and status['run_id']==run and status['live'] is False
                         and status['host_boot_id']==config['units'][unit]['host_boot_id'],'native_child_terminal_unproved')
                    result.setdefault('final_native_statuses',[]).append(status)
                except BaseException as error:
                    native_terminal=False;result['complete']=False
                    result.setdefault('final_status_errors',[]).append(dict(unit=unit,run_id=run,error_type=type(error).__name__))
            RPC_terminal=all(a['remote_RPC_terminal_proven'] for a in result['rpc_clients'])
            result['native_RPC_terminal_unproved']=not RPC_terminal
            if not RPC_terminal: result['complete']=False
            if owners and native_terminal and RPC_terminal:
                for unit in ('unit-c','unit-b','unit-a'):
                    if unit in result['native_final']: continue
                    try: result['native_final'][unit]=guarded(unit,'audit')
                    except BaseException as error:
                        result['complete']=False;result.setdefault('final_audit_errors',{})[unit]=type(error).__name__
                try:
                    result['facts_after_cleanup']={u:stable(operator.facts(u)) for u in UNITS}
                    need(result['facts_after_cleanup']==facts,'final_service_or_boot_changed')
                except BaseException as error:
                    result['complete']=False;result['final_facts_error']=type(error).__name__
            for job in list(operator.jobs.values())+owned:
                try: operator.reap(job)
                except BaseException: result['complete']=False; result.setdefault('cleanup_errors',[]).append('client_reap_failed')
            result['owned_local_clients_terminal']=all(job.process.poll() is not None for job in list(operator.jobs.values())+owned)
            if not result['owned_local_clients_terminal']: result['complete']=False
            try: operator.close_connections()
            except BaseException: result['complete']=False;result.setdefault('cleanup_errors',[]).append('owned_master_close_failed')
            operator.passwords.clear();result['management_events']=operator.events
        if parent is not None:
            try: result['controller_parent']=parent.snapshot()
            except BaseException: result['complete']=False;result.setdefault('cleanup_errors',[]).append('controller_snapshot_failed')
            try: need(parent.close() is True,'controller_parent_close_uncertain')
            except BaseException: result['complete']=False;result.setdefault('cleanup_errors',[]).append('controller_close_failed')
        result['finished_epoch_us']=time.time_ns()//1000;result['within_original_phase']=result['finished_epoch_us']<phase_end
        if not result['within_original_phase'] or any(e.get('outside_handoff') for e in result.get('management_events',[])): result['complete']=False
        result['qualification_complete']=result['qualification_complete'] and result['complete']
        write(out/'RESULT.json',result)
        print(json.dumps(dict(complete=result['complete'],qualification_complete=result['qualification_complete'],saved=str(out/'RESULT.json'))))


if __name__=='__main__': main()
