"""Root-only explicit prepare/stage/issue/run for a NEW bounded single-process bundle handoff file.

prepare is local/source-only. stage installs a pinned wheel without opening a
radio. issue writes fresh local grants; run consumes them once. No action
automatically advances to another operation, retries failures, or resets debt.
Passwords enter only through stdin and the reviewed bounded sudo transport.

An optional config useful_input={"path": "...", "sha256": "..."} reuses only
the pinned useful bytes for a matched workload. It must be a regular file of
exactly useful_bytes, at most 1,000,000 bytes. Omission generates random bytes;
explicit null is invalid. Preparation still creates a fresh pack ID/signature,
and separate issuance still creates fresh sessions and grants. Reusing input
bytes conveys no authority or performance evidence from the earlier run.

Optional preamble_symbols selects exact integer 12 (default) or 32 for the
SF5 source experiment. The admitted waveform, grant profile and cold native/
host estimates use that same selection; all original caps remain unchanged.
Optional sync_word selects exact integer 66 (default) or 18. Both peers' profile
and admission bind the same selection; airtime estimates and caps are unchanged.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import email
import io
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import zipfile

import campaign as c
from common import *
from transport import rpc

BASE=Path(__file__).resolve().parent
HARNESSES=('campaign.py','common.py','controller.py','transport.py','remote.py',
           'file_run.py','remote_file_body.py','sdk_entry.py','sdk_batch_entry.py','small_fixture.py','install_environment.py','verify_environment.py')
# Exact shared codec accepted in ROOT_CODEC_ACCEPTANCE.json (64223210a8ee...).
# The code is copied unchanged; this hash does not authorize a campaign.
HISTORY_CODEC_SHA256='3aea3c585e030c5163992cbf76a196c51c04559cf6cb96dd06c853be22747d42'
HISTORY_CLOSURE_SCHEMA='meshticulum.campaign-historical-closure.v1'
HISTORY_CLOSURE_KEYS={'schema','root_index','previous_prepared','root_reconciliation'}

def closure_codec():
    need(HISTORY_CODEC_SHA256 is not None,'history_codec_not_accepted')
    digest(HISTORY_CODEC_SHA256)
    raw=read_history(str(BASE/'history_encoding.py'),HISTORY_CODEC_SHA256,limit=2_000_000)
    namespace={'__name__':'_checked_campaign_history','__file__':str(BASE/'history_encoding.py')}
    # Execute only the exact approved local source bytes, never an indexed leaf.
    exec(compile(raw,str(BASE/'history_encoding.py'),'exec'),namespace)
    need(callable(namespace.get('checked_closure')),'history_codec_api_missing')
    return namespace['checked_closure'],raw

def controller_harnesses(value):
    return HARNESSES+(('history_encoding.py',) if 'historical_closure' in value else ())

def _history_rows(value):
    need(type(value) is list and 0<len(value)<=512,'invalid_direct_history')
    result={}
    for entry in value:
        history_ref(entry,sized=False)
        need(entry['path'] not in result,'duplicate_direct_history')
        result[entry['path']]=entry['sha256']
    return result

def _closure_input(value):
    need(type(value) is dict and set(value)==HISTORY_CLOSURE_KEYS
         and value['schema']==HISTORY_CLOSURE_SCHEMA,'invalid_historical_closure')
    for key in HISTORY_CLOSURE_KEYS-{'schema'}:history_ref(value[key])
    need(value['root_index']['bytes']<=2_000_000,'history_index_too_large')
    need(len({value[k]['path'] for k in HISTORY_CLOSURE_KEYS-{'schema'}})==3,'history_binding_alias')

def checked_historical_closure(value,history):
    """Read-only complete evidence validation, with no clock or authority use."""
    # Deliberately before evidence reads/imports: no unreviewed codec fallback.
    need(HISTORY_CODEC_SHA256 is not None,'history_codec_not_accepted')
    _closure_input(value);direct=_history_rows(history)
    resolver,_=closure_codec()
    root=value['root_index']
    need(direct.get(root['path'])==root['sha256'],'root_index_not_direct_history')
    root_raw=read_history(root['path'],root['sha256'],size=root['bytes'],limit=2_000_000)
    rows,metadata,stats=resolver(root_raw,lambda path,pin:read_history(path,pin,limit=2_000_000))
    # Defensive checks at the API boundary supplement the accepted codec caps.
    need(type(rows) is list and 0<len(rows)<=2048 and type(metadata) is dict
         and type(stats) is dict,'invalid_history_closure_result')
    expanded={}
    for row in rows:
        history_ref(row)
        need(row['path'] not in expanded,'duplicate_expanded_history')
        expanded[row['path']]=row
    need(list(expanded)==sorted(expanded) and sum(r['bytes'] for r in rows)<=512*1024*1024,
         'expanded_history_bound')
    need(set(stats)=={'index_documents','index_metadata_bytes','direct_files','expanded_files','expanded_bytes'}
         and all(type(n) is int and n>=0 for n in stats.values())
         and 1<=stats['index_documents']<=8 and stats['index_metadata_bytes']<=16_000_000
         and 1<=stats['direct_files']<=500 and stats['expanded_files']==len(rows)
         and stats['expanded_bytes']==sum(r['bytes'] for r in rows),'history_summary_changed')
    for path,pin in direct.items():
        need((path==root['path'] and pin==root['sha256'])
             or (path in expanded and expanded[path]['sha256']==pin),'direct_history_not_in_closure')
    for name in ('previous_prepared','root_reconciliation'):
        ref=value[name];need(expanded.get(ref['path'])==ref,'history_binding_not_in_closure')
    # Verify every leaf, including parent metadata, once through the bounded
    # filesystem reader. Keep only the two small binding JSON documents.
    retained={}
    for row in rows:
        raw=read_history(row['path'],row['sha256'],size=row['bytes'])
        if row['path'] in metadata:need(raw==metadata[row['path']],'parent_index_changed')
        if row['path'] in (value['previous_prepared']['path'],value['root_reconciliation']['path']):
            retained[row['path']]=strict_json(raw)
    previous=retained[value['previous_prepared']['path']]
    reconciliation=retained[value['root_reconciliation']['path']]
    need(type(previous) is dict and previous.get('schema')=='meshticulum.alpha3-individual-prepared.v1',
         'invalid_previous_prepared')
    inherited=_history_rows(previous['historical_evidence'])
    for path,pin in inherited.items():
        need(path in expanded and expanded[path]['sha256']==pin,'inherited_history_omitted')
    if 'historical_closure' in previous:
        prior=previous['historical_closure']
        need(type(prior) is dict and set(prior)==HISTORY_CLOSURE_KEYS|{'source_sha256','validation'},
             'invalid_previous_closure')
        _closure_input({k:prior[k] for k in HISTORY_CLOSURE_KEYS});digest(prior['source_sha256'])
        # Presence as an ordinary file is insufficient. This exact prior root
        # must have been traversed in the SAME bounded global resolver call.
        prior_root=prior['root_index'];prior_raw=metadata.get(prior_root['path'])
        need(type(prior_raw) is bytes and len(prior_raw)==prior_root['bytes']
             and sha(prior_raw)==prior_root['sha256'],'previous_closure_not_traversed')
    previous_ref=value['previous_prepared']
    need(type(reconciliation) is dict and reconciliation.get('prepared')==
         {k:previous_ref[k] for k in ('path','sha256')},'reconciliation_prepared_changed')
    for flag in ('complete','trial_closed','scope_expired_at_reconciliation','all_bursts_drained',
                 'all_radios_unarmed_after_original_remote_deadlines','all_serial_channels_closed'):
        need(reconciliation.get(flag) is True,'previous_trial_not_closed')
    need(reconciliation.get('new_rf_authorized') is False and reconciliation.get('automatic_retry') is False
         and type(reconciliation.get('refunds')) is int
         and reconciliation['refunds']==0,'history_cannot_authorize_or_refund')
    need(reconciliation.get('global_historical_debt_complete') is False
         and reconciliation.get('historical_unknown_actual_cost','missing') is None,'historical_unknown_changed')
    scope_ref=history_ref(reconciliation['scope'],sized=False)
    need(scope_ref['path'] in expanded and expanded[scope_ref['path']]['sha256']==scope_ref['sha256'],
         'previous_scope_not_in_closure')
    scope_row=expanded[scope_ref['path']]
    scope=strict_json(read_history(scope_ref['path'],scope_ref['sha256'],size=scope_row['bytes']))
    need(type(scope) is dict and scope['prepared_sha256']==previous_ref['sha256'],'previous_scope_changed')
    observation=reconciliation['expiry_observation']
    need(type(scope.get('controller_boot')) is str and 0<len(scope['controller_boot'])<=128
         and type(observation) is dict and observation['controller_boot']==scope['controller_boot'],
         'previous_clock_boot_changed')
    for source,deadline in (('epoch_us','expires_epoch_us'),('monotonic_us','original_deadline_monotonic_us')):
        need(type(observation[source]) is int and type(scope[deadline]) is int
             and 0<scope[deadline]<=observation[source]<2**63,'previous_scope_not_expired')
    for key,deadline in (('original_scope_expires_epoch_us','expires_epoch_us'),
                         ('original_scope_deadline_monotonic_us','original_deadline_monotonic_us')):
        need(type(reconciliation.get(key)) is int and reconciliation[key]==scope[deadline],
             'reconciliation_deadline_changed')
    summary=dict(schema='meshticulum.campaign-history-validation.v1',**stats,
        expanded_rows_sha256=sha(canonical(rows)),inherited_direct_files=len(inherited),
        inherited_direct_sha256=sha(canonical(previous['historical_evidence'])),
        previous_recursive_closure='historical_closure' in previous,
        global_historical_debt_complete=False,historical_unknown_actual_cost=None,
        refunds=0,new_rf_authorized=False)
    return dict(value,source_sha256=HISTORY_CODEC_SHA256,validation=summary)

def validate_historical_closure(prepared):
    if 'historical_closure' not in prepared:
        need('history_encoding.py' not in prepared.get('controller_sources',{}),'unexpected_history_codec')
        return None
    bound=prepared['historical_closure']
    need(type(bound) is dict and set(bound)==HISTORY_CLOSURE_KEYS|{'source_sha256','validation'},
         'invalid_bound_history_closure')
    sources=prepared.get('controller_sources',{})
    need(type(sources) is dict and set(sources)==set(controller_harnesses(prepared)),
         'history_controller_inventory_changed')
    for pin in sources.values():digest(pin)
    need(bound['source_sha256']==HISTORY_CODEC_SHA256
         and sources['history_encoding.py']==HISTORY_CODEC_SHA256,
         'history_codec_binding_changed')
    result=checked_historical_closure({k:bound[k] for k in HISTORY_CLOSURE_KEYS},prepared['historical_evidence'])
    need(canonical(bound)==canonical(result),'history_validation_changed')
    return result

def boot_id():
    if sys.platform=='darwin':
        return sha(subprocess.check_output(['/usr/sbin/sysctl','-n','kern.boottime']))
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def clock():return time.time_ns()//1000,time.monotonic_ns()//1000

def scope_rpc_seconds(scope, now_epoch, now_mono):
    remaining=min(scope['expires_epoch_us']-now_epoch,scope['original_deadline_monotonic_us']-now_mono)
    need(remaining>0,'original_scope_expired')
    return math.ceil(remaining/1000000)+8

def pinned(spec,limit=4_000_000):return read_pinned(spec['path'],spec['sha256'],limit)

def useful_data(config,size):
    """Select a bounded workload without changing signing or grant state."""
    c.integer(size,1);need(size<=1_000_000,'workload_too_large')
    if 'useful_input' not in config:
        return secrets.token_bytes(size)
    spec=config['useful_input']
    need(type(spec) is dict and set(spec)=={'path','sha256'},'invalid_useful_input')
    need(type(spec['path']) is str and bool(spec['path']) and '\x00' not in spec['path'],
         'invalid_useful_input_path')
    pin=c.digest(spec['sha256']);path=Path(spec['path'])
    need(stat.S_ISREG(path.lstat().st_mode),'invalid_useful_input_file')
    # A regular path can be replaced after lstat. Nonblocking open prevents a
    # replacement FIFO from waiting for a writer before fstat can reject it.
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1
             and 0<before.st_size<=1_000_000,'invalid_input_file')
        data=os.read(fd,1_000_001)
        after=os.fstat(fd)
        stamp=lambda x:(x.st_dev,x.st_ino,x.st_mode,x.st_uid,x.st_gid,
                        x.st_nlink,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        need(stamp(before)==stamp(after)==stamp(path.lstat())
             and len(data)==before.st_size,'input_changed')
        need(sha(data)==pin,'input_pin_mismatch')
    finally:
        os.close(fd)
    need(len(data)==size,'useful_input_size_mismatch')
    return data

def sdk(core,commit):
    need(subprocess.check_output(['git','-C',str(core),'rev-parse','HEAD'],text=True).strip()==commit,'candidate_commit_changed')
    need(not subprocess.check_output(['git','-C',str(core),'status','--porcelain'],text=True),'candidate_checkout_dirty')
    sys.path[:0]=[str(core/'src'),str(core/'examples')]

def checked_data_burst(config):
    """Require identical selected policy in the exact caller-pinned SDK."""
    from meshticulum.bench import burst_protocol
    policy=selected_data_burst(config)
    if policy is not None:
        if 'train_frames' in config:
            selector=getattr(burst_protocol,'policy_for_train12',None)
            need(callable(selector),'sdk_train12_policy_unavailable')
            expected=selector()
        elif 'selective_repair_train' in config:
            selector=getattr(burst_protocol,'policy_for_repair_train',None)
            need(callable(selector),'sdk_repair_train_policy_unavailable')
            expected=selector()
        elif 'selective_repeat_retry' in config:
            selector=getattr(burst_protocol,'policy_for_repeat_retry',None)
            need(callable(selector),'sdk_repeat_retry_policy_unavailable')
            expected=selector()
        elif 'scalar_fill_pair' in config:
            selector=getattr(burst_protocol,'policy_for_scalar_fill_pair',None)
            need(callable(selector),'sdk_scalar_fill_pair_policy_unavailable')
            expected=selector()
        elif 'selective_terminal_retry' in config:
            selector=getattr(burst_protocol,'policy_for_terminal_retry',None)
            need(callable(selector),'sdk_terminal_retry_policy_unavailable')
            expected=selector()
        elif 'selective_repair_fill' in config:
            selector=getattr(burst_protocol,'policy_for_repair_fill',None)
            need(callable(selector),'sdk_repair_fill_policy_unavailable')
            expected=selector()
        elif 'flow_window_frames' in config:
            selector=getattr(burst_protocol,'policy_for_flow_window',None)
            need(callable(selector),'sdk_flow_window_policy_unavailable')
            expected=selector(config['flow_window_frames'])
        else:
            expected=burst_protocol.policy_for_depth(config.get('train_depth',4))
        need(canonical(policy)==canonical(expected),'sdk_train_policy_mismatch')
    return policy

def checked_sync_word(value=66):
    need(type(value) is int and value in (66, 18), 'invalid_sync_word')
    return value

def sf5_radio_parameters(policy, *, preamble_symbols=12, sync_word=66):
    """Build one exact profile/admission pair without writing or issuing work."""
    from meshticulum.bench.admission import RadioAdmission
    from meshticulum.bench.serial_protocol import RadioProfile
    preamble=c.checked_preamble_symbols(preamble_symbols)
    sync=checked_sync_word(sync_word)
    profile=dict(frequency_hz=921375000,bandwidth_hz=500000,spreading_factor=5,coding_rate=5,
        chip_power_dbm=0,preamble=preamble,sync_word=sync,lease_ms=policy['radio_lease_ms'],
        max_boot_attempts=policy['max_boot_attempts'],max_boot_estimated_airtime_us=policy['max_boot_estimated_airtime_us'])
    admission=dict(country='MY',waveforms=[[921375000,500000,5,5,0,preamble,sync]],min_lease_ms=10000,
        max_lease_ms=180000,max_boot_attempts=policy['max_boot_attempts'],
        max_boot_estimated_airtime_us=policy['max_boot_estimated_airtime_us'])
    RadioAdmission(**{**admission,'waveforms':tuple(tuple(x) for x in admission['waveforms'])}).check('MY',True,RadioProfile(**profile))
    return profile,admission

def prepare(config_path,output):
    raw=Path(config_path).read_bytes();config=strict_json(raw)
    history_binding=(checked_historical_closure(config['historical_closure'],config['historical_evidence'])
                     if 'historical_closure' in config else None)
    need(config['schema']=='meshticulum.alpha3-individual-input.v1','new_config_required')
    need({config['sender'],config['receiver']}==set(UNITS),'this_adapter_is_ab_only')
    need(config['comparison_mode'] in ('baseline','queued') and type(config['profile_host']) is bool,'mode_required')
    preamble=c.checked_preamble_symbols(config.get('preamble_symbols',12))
    sync=checked_sync_word(config.get('sync_word',66))
    core=Path(config['core']['path']).resolve();commit=config['core']['commit'];sdk(core,commit)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from meshticulum.resources import byte_bundle,knowledge_pack as kp
    import radio_bundle,radio_file
    burst_policy=checked_data_burst(config)
    wheel=pinned(config['candidate_wheel'],2_000_000)
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        names=archive.namelist();need(len(names)==len(set(names)),'duplicate_wheel_entry')
        runtime={n:sha(archive.read(n)) for n in names if n.startswith('meshticulum/') and not n.endswith('/')}
        need(runtime and all(sha((core/'src'/n).read_bytes())==pin for n,pin in runtime.items()),'wheel_source_mismatch')
        metas=[n for n in names if n.endswith('.dist-info/METADATA')];need(len(metas)==1,'wheel_metadata')
        meta=email.message_from_bytes(archive.read(metas[0]));version=meta['Version']
        need(meta['Name']=='meshticulum' and version=='1.0.0a3','exact_alpha3_required')
    firmware=pinned(config['firmware'],65536);identity=radio_file.firmware_source_identity(json.loads(firmware))
    observations={u:json.loads(pinned(config['preflights'][u])) for u in UNITS}
    for u,o in observations.items():
        need(o['complete'] is True and o['facts_before']==o['facts_after']
             and o['radio']['firmware']==identity and o['owned_channel_closed'] is True,'preflight_not_ready')
        need(config['units'][u]['host_boot_id']==o['facts_after']['host_boot_id'],'host_boot_changed')
    history=config['historical_evidence'];need(type(history) is list and history,'history_required')
    for entry in history:
        if history_binding is None:pinned(entry,8_000_000)
        else:read_history(entry['path'],entry['sha256'])
    # CLOCK captures are evidence only; actual fresh clock mappings still occur
    # inside the public worker for every bounded queued burst.
    clock_evidence=config.get('clock_preflights',{})
    if config['comparison_mode']=='queued':
        need(set(clock_evidence)==set(UNITS),'queued_clock_captures_required')
        for entry in clock_evidence.values():
            capture=json.loads(pinned(entry));need(capture['complete'] is True,'clock_preflight_failed')
    policy=dict(config['policy'])
    for v in policy.values():c.integer(v,1)
    need(60000<=policy['radio_lease_ms']<=180000 and 60<=policy['scope_seconds']<=1200
         and policy['radio_lease_ms']+10000<policy['scope_seconds']*1000
         and policy['max_frames']<=512,'finite_policy_required')
    profile,admission=sf5_radio_parameters(policy,preamble_symbols=preamble,sync_word=sync)
    size=c.integer(config['useful_bytes'],1);need(size<=1_000_000,'workload_too_large')
    part_bytes=c.integer(config['part_bytes'],1);need(part_bytes<=23900,'fixture_part_size')
    count=math.ceil(size/part_bytes)
    need(count*ACK_GRACE_US+5_000_000<policy['scope_seconds']*1000000<=radio_bundle.max_file_window_us(count),'whole_file_window_cannot_fit')
    need(type(config['order']) is list and all(type(i) is int for i in config['order']) and sorted(config['order'])==list(range(count)),'exact_part_permutation_required')
    run_id=secrets.token_hex(16);data=useful_data(config,size);key=Ed25519PrivateKey.generate()
    from small_fixture import build_bundle
    manifest,chunks,signature=build_bundle(data,part_bytes=part_bytes,pack_id='handoff-'+run_id,
        created_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),private_key=key)
    trust=dict(schema='sage-foundation.knowledge-pack.trust.v1',keys=[dict(key_id=signature['key_id'],
        public_key_hex=kp.public_key_hex(key),status='active',pack_classes=['reference'])]);key=None
    responses=[]
    for i,entry in enumerate(manifest['chunks']):
        response=radio_bundle.response_for(kp.manifest_digest(manifest),entry,kp.chunk_bytes(chunks[entry['sha256']]))
        responses.append(dict(part=i,useful_bytes=min(part_bytes,size-i*part_bytes),
            response_bytes=len(response),response_sha256=sha(response),
            cold_cost=c.part_cost(len(response),extra_data_attempts=config.get('extra_data_attempts',8),
                                 receiver_ack_allowance=config.get('receiver_ack_allowance',64),
                                 preamble_symbols=profile['preamble'])))
    native={u:dict(attempts=o['radio']['boot_attempts'],estimated_airtime_us=o['radio']['boot_reserved_estimated_airtime_us']) for u,o in observations.items()}
    roles=operation_limits(dict(policy=policy))
    operation={u:roles['sender' if u==config['sender'] else 'receiver'] for u in UNITS}
    fit=c.admission_check([r['cold_cost'] for r in responses],sender=config['sender'],receiver=config['receiver'],
        native=native,campaign_remaining=config['run_budget'],operation_caps=operation,
        max_boot_attempts=policy['max_boot_attempts'],max_boot_airtime_us=policy['max_boot_estimated_airtime_us'],
        max_frames=policy['max_frames'],own_limit_us=policy['max_boot_estimated_airtime_us'],
        shared_limit_us=2*policy['max_boot_estimated_airtime_us'])
    for u in UNITS:
        need(config['run_budget'][u]['attempts'] >= count*operation[u]['attempts']
             and config['run_budget'][u]['estimated_airtime_us'] >= count*operation[u]['estimated_airtime_us'],
             'full_operation_reservation_required')
    lock=json.loads(pinned(config['dependency_lock']));unit_runtime={}
    for u,o in observations.items():
        lock_unit=config.get('dependency_unit_map',{}).get(u,u)
        if lock_unit != u:
            need(lock_unit in observations and o['python_environment']['version'].split()[0] == observations[lock_unit]['python_environment']['version'].split()[0]
                 and o['python_environment']['machine'] == observations[lock_unit]['python_environment']['machine'], 'dependency_runtime_mapping_changed')
        rows=[r for r in lock['units'][lock_unit] if r['name']!='pip']
        need(all(r['url'].startswith('https://files.pythonhosted.org/') for r in rows),'untrusted_dependency_host')
        unit_runtime[u]=dict(python=list(map(int,o['python_environment']['version'].split()[0].split('.')[:2])),
            machine=o['python_environment']['machine'],dependencies=rows)
    spec=dict(schema='meshticulum.clean-installed-wheel-runtime.v1',sdk_version=version,core_commit=commit,
        sdk_wheel=dict(filename=Path(config['candidate_wheel']['path']).name,sha256=sha(wheel),bytes=len(wheel)),
        runtime_files=runtime,units=unit_runtime,editable=False,system_site_packages=False)
    target=Path(output).resolve();target.mkdir(mode=0o700);dest=target/'prepared';dest.mkdir(mode=0o700)
    kp.write_pack(dest/'pack',manifest,chunks,signature)
    for p in (dest/'pack').rglob('*'):p.chmod(0o600 if p.is_file() else 0o700)
    for name,value,is_raw in [('trust.json',trust,False),('SOURCE.json',firmware,True),('admission.json',admission,False),('RUNTIME.json',spec,False),('wheelhouse/'+spec['sdk_wheel']['filename'],wheel,True)]:write_new(dest/name,value,raw=is_raw)
    write_new(target/'INPUT.bin',data,raw=True)
    harnesses=controller_harnesses(config)
    for name in harnesses:
        code=(read_history(str(BASE/name),HISTORY_CODEC_SHA256,limit=2_000_000)
              if name=='history_encoding.py' else (BASE/name).read_bytes())
        write_new(target/name,code,raw=True)
    for name in ('file_run.py','remote_file_body.py','sdk_entry.py','sdk_batch_entry.py','small_fixture.py','install_environment.py','verify_environment.py'):write_new(dest/name,(BASE/name).read_bytes(),raw=True)
    for name in ('radio_file.py','radio_bundle.py'):write_new(dest/'examples'/name,(core/'examples'/name).read_bytes(),raw=True)
    files={str(p.relative_to(dest)):sha(p.read_bytes()) for p in dest.rglob('*') if p.is_file()}
    policy['unit_limits']={u:dict(max_boot_attempts=policy['max_boot_attempts'],max_boot_estimated_airtime_us=policy['max_boot_estimated_airtime_us']) for u in UNITS}
    prepared=dict(schema='meshticulum.alpha3-individual-prepared.v1',id=run_id,campaign_id=config['campaign_id'],
        campaign_directory=str(Path(config['campaign_directory']).resolve()),run_index=config['run_index'],
        campaign_sha256=sha(raw),sender=config['sender'],receiver=config['receiver'],profile_host=config['profile_host'],
        core_path=str(core),core_commit=commit,candidate_wheel_sha256=sha(wheel),runtime_spec=spec,
        comparison_mode=config['comparison_mode'],data_burst=burst_policy,
        useful_bytes=size,useful_sha256=sha(data),manifest_sha256=kp.manifest_digest(manifest),object_id=byte_bundle.descriptor(manifest)['sha256'],
        part_count=count,part_bytes=part_bytes,order=config['order'],parts=responses,units=config['units'],policy=policy,profile=profile,
        files=files,staged_root='/var/lib/meshticulum-public-sdk/'+run_id,
        expected_mcu={u:o['radio'] for u,o in observations.items()},expected_facts={u:o['facts_after'] for u,o in observations.items()},
        by_id={u:o['by_id'] for u,o in observations.items()},peers={u:dict(chain_id='meshticulum-public-sdk-alpha3',agent_id=sha((run_id+u).encode())) for u in UNITS},
        firmware_sources={u:dict(path='SOURCE.json',sha256=sha(firmware)) for u in UNITS},
        receiver_retirement=dict(cli_flags=['--confirm-receipt']),historical_evidence=history,
        run_budget=config['run_budget'],cold_planning=fit,clock_preflights=clock_evidence,
        controller_sources={name:sha((target/name).read_bytes()) for name in harnesses},
        continuation_pause=config.get('continuation_pause'),source_only=True,scopes_issued=0)
    if history_binding is not None:prepared['historical_closure']=history_binding
    write_new(target/'PREPARED.json',prepared);write_new(target/'INPUT_CONFIG.json',raw,raw=True)
    return dict(prepared=str(target/'PREPARED.json'),sha256=sha(canonical(prepared)),parts=count,cold_planning=fit)

def load_run():
    p=strict_json((BASE/'PREPARED.json').read_bytes())
    validate_historical_closure(p)
    closure='historical_closure' in p
    for name,pin in p['controller_sources'].items():
        if closure:read_history(str(BASE/name),pin,limit=4_000_000)
        else:read_pinned(BASE/name,pin)
    for name,pin in p['files'].items():
        if closure:read_history(str(BASE/'prepared'/name),pin)
        else:read_pinned(BASE/'prepared'/name,pin,20_000_000)
    for item in p['historical_evidence']:
        if closure:read_history(item['path'],item['sha256'])
        else:pinned(item,8_000_000)
    sdk(Path(p['core_path']),p['core_commit'])
    return p

def remote_source():
    campaign=(BASE/'campaign.py').read_text()
    common=(BASE/'common.py').read_text().replace('from campaign import need,digest,sha,canonical','')
    prefix=(BASE/'remote.py').read_text().split("assert request['action']=='run'",1)[0]
    return campaign+'\n'+common+'\n'+prefix+(BASE/'remote_file_body.py').read_text()

def stage(p,password):
    validate_historical_closure(p)
    epoch,_=clock();window=dict(issued_epoch_us=epoch,expires_epoch_us=epoch+180_000_000,
        prepared_sha256=sha(canonical(p)),owner_window_sha256=p['campaign_sha256'])
    payload={n:base64.b64encode(read_history(str(BASE/'prepared'/n),pin)
        if 'historical_closure' in p else (BASE/'prepared'/n).read_bytes()).decode() for n,pin in p['files'].items()}
    results={}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs={u:pool.submit(rpc,p['units'][u],remote_source(),dict(action='stage',unit=u,prepared=p,
            files=payload,stage_window=window),password=password,seconds=190) for u in UNITS}
        for u,j in jobs.items():results[u]=j.result()
    write_new(BASE/'STAGED.json',results);return dict(complete=all(r['complete'] for r in results.values()),units=list(results))

def issue(p):
    validate_historical_closure(p)
    parse=strict_json if 'historical_closure' in p else json.loads
    staged=parse((BASE/'STAGED.json').read_bytes());need(all(staged[u]['complete'] for u in UNITS),'stage_required')
    import radio_bundle
    epoch,mono=clock();end=epoch+p['policy']['scope_seconds']*1000000
    directory=Path(p['campaign_directory']);index=c.integer(p['run_index'])
    need(index==0 and directory.is_dir(),'bounded_campaign_directory_required')
    need(not (directory/'EXECUTION_CLOSED.json').exists(),'campaign_execution_closed')
    # Reserve each complete per-run allowance without refunds. Failed/missing
    # endpoint reports therefore cannot create budget for another run.
    window_path=directory/'CAMPAIGN_WINDOW.json'
    if index==0:
        window=dict(schema='meshticulum.alpha3-campaign-window.v1',campaign_id=p['campaign_id'],
            issued_epoch_us=epoch,expires_epoch_us=end,
            original_monotonic_us=mono,original_deadline_monotonic_us=mono+end-epoch,
            controller_boot=boot_id(),max_attempts_per_endpoint=max(r['attempts'] for r in p['run_budget'].values()),
            max_reserved_airtime_us_per_endpoint=max(r['estimated_airtime_us'] for r in p['run_budget'].values()),historical_evidence=p['historical_evidence'])
        if 'historical_closure' in p:window['historical_closure']=p['historical_closure']
        write_new(window_path,window)
    else:window=parse(window_path.read_bytes())
    bind_historical_closure(p,window)
    need(window['campaign_id']==p['campaign_id'],'campaign_binding_changed')
    validate_window(window,epoch,mono,boot_id())
    reserved={u:dict(attempts=0,estimated_airtime_us=0) for u in UNITS}
    for ordinal in range(index):
        claim=json.loads((directory/f'CAMPAIGN_CLAIM_{ordinal}.json').read_bytes())
        need(claim['campaign_id']==p['campaign_id'] and claim['index']==ordinal,'campaign_claim_changed')
        need(claim['campaign_window_sha256']==sha(canonical(window)) and claim['refunds']==0,'original_campaign_claim_required')
        prior=json.loads(read_pinned(Path(claim['run_directory'])/'SCOPE.json',claim['scope_sha256']))
        previous_prepared=json.loads(read_pinned(Path(claim['run_directory'])/'PREPARED.json',prior['prepared_sha256']))
        need(claim['reserved']==previous_prepared['run_budget'],'claim_reservation_changed')
        need(epoch>=prior['expires_epoch_us'] and mono>=prior['original_deadline_monotonic_us'],'previous_scope_still_live')
        result=json.loads((Path(claim['run_directory'])/'RESULT.json').read_bytes())
        need(result['scope_sha256']==claim['scope_sha256'] and result['complete'] is True,'prior_failure_requires_explicit_reconciliation')
        for u in UNITS:
            for key in reserved[u]:reserved[u][key]+=c.integer(claim['reserved'][u][key])
    for u in UNITS:
        need(reserved[u]['attempts']+p['run_budget'][u]['attempts']<=window['max_attempts_per_endpoint']
             and reserved[u]['estimated_airtime_us']+p['run_budget'][u]['estimated_airtime_us']<=window['max_reserved_airtime_us_per_endpoint'],'campaign_reservation_exhausted')
    grants={u:{} for u in UNITS};manifest=json.loads((BASE/'prepared/pack/manifest.json').read_bytes())
    operation=operation_limits(p)
    for i,entry in enumerate(manifest['chunks']):
        response=radio_bundle.response_for(p['manifest_sha256'],entry,(BASE/'prepared/pack/chunks'/(entry['sha256']+'.json')).read_bytes())
        need(len(response)==p['parts'][i]['response_bytes'] and sha(response)==p['parts'][i]['response_sha256'],'signed_response_changed')
        secret=secrets.token_bytes(32);session,generation=secrets.token_hex(16),secrets.token_hex(16)
        for u in UNITS:
            plan=dict(schema='meshticulum.service-run.v1',unit=u,host_boot_id=p['units'][u]['host_boot_id'],
                local_peer=p['peers'][u],initiator=p['peers'][p['sender']],responder=p['peers'][p['receiver']],
                session_id=session,generation=generation,issued_epoch_us=epoch,expires_epoch_us=end,profile=p['profile'],
                sender_unit=p['sender'],country='MY',antenna_confirmed=True,max_frames=p['policy']['max_frames'],
                object=dict(object_id=p['object_id'],chunk_index=i,bytes=len(response),sha256=sha(response)),
                service=radio_bundle.service_for(entry,response,end),operation_limits=operation)
            if p['data_burst'] is not None:plan['data_burst']=dict(p['data_burst'])
            validate_plan_mode(plan,p)
            folder=BASE/'issued'/u/str(i)
            for name,raw in [('grant.json',canonical(plan)),('secret.bin',secret),('active',b'active\n')]:write_new(folder/name,raw,raw=True)
            grants[u][str(i)]={n:sha((folder/n).read_bytes()) for n in ('grant.json','secret.bin','active')}
    scope=dict(schema='meshticulum.alpha3-individual-scope.v1',fresh_scope=True,campaign_id=p['campaign_id'],
        prepared_id=p['id'],prepared_sha256=sha(canonical(p)),core_commit=p['core_commit'],
        comparison_mode=p['comparison_mode'],data_burst=p['data_burst'],policy=p['policy'],
        manifest_sha256=p['manifest_sha256'],useful_bytes=p['useful_bytes'],useful_sha256=p['useful_sha256'],
        part_count=p['part_count'],order=p['order'],issued_epoch_us=epoch,expires_epoch_us=end,original_monotonic_us=mono,
        original_deadline_monotonic_us=mono+end-epoch,controller_boot=boot_id(),count_authorized=1,
        grants=grants,root_sole_device_writer=True,historical_evidence=p['historical_evidence'])
    if 'historical_closure' in p:scope['historical_closure']=p['historical_closure']
    bind_historical_closure(p,scope,window)
    write_new(BASE/'SCOPE.json',scope)
    write_new(directory/f'CAMPAIGN_CLAIM_{index}.json',dict(campaign_id=p['campaign_id'],index=index,
        run_directory=str(BASE),scope_sha256=sha(canonical(scope)),reserved=p['run_budget'],
        campaign_window_sha256=sha(canonical(window)),refunds=0))
    return dict(scope=str(BASE/'SCOPE.json'),sha256=sha(canonical(scope)),expires_epoch_us=end)

def run(p,password,scope_pin):
    validate_historical_closure(p)
    from file_run import run_file
    return run_file(sys.modules[__name__],p,password,scope_pin)


def main():
    os.umask(0o077);parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','stage','issue','run'))
    parser.add_argument('--config',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--scope-sha256');parser.add_argument('--password-stdin',action='store_true')
    args=parser.parse_args();password=None
    try:
        if args.action=='prepare':
            need(args.config and args.output,'config_and_output_required');result=prepare(args.config,args.output)
        else:
            p=load_run()
            if args.action in ('stage','run'):
                need(args.password_stdin,'password_stdin_required')
                password=sys.stdin.readline(1024).rstrip('\r\n');need(0<len(password)<=512,'invalid_credential_input')
            if args.action=='stage':result=stage(p,password)
            elif args.action=='issue':result=issue(p)
            else:need(args.scope_sha256,'scope_pin_required');result=run(p,password,args.scope_sha256)
        print(json.dumps(result,sort_keys=True))
    finally:password=None

if __name__=='__main__':main()
