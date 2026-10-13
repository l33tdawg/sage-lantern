# SPDX-License-Identifier: Apache-2.0
"""Private A-only checkpoint; consumes original Root scope, creates no authority.

Acquire the reviewed low-FD serial owner BEFORE constructing ResumeParentGuard.
Root must declare bundle_radio_checkpoint in the ORIGINAL protected owner and
produce/pin its stage-handoff record after genuine controller/C observations.
That producer and next runtime/H19 source pins remain uncommissioned. This
module has no CLI/default activation, grant/credit/deadline or firmware writer.
"""
import hashlib
import json
import os
import threading
import types
import sys
import stat
from pathlib import Path

from meshticulum.bench.wire import require
from .pilot_parent_caps import canonical
from .pilot_bundle import BundleContext,KIND,BUNDLE_KINDS
from .resume_parent_guard import ResumeParentGuard,HISTORY
from .pilot_resume_rpc import Reader,boot,parse,write_new
from . import pilot_parent_snapshot as parent_snapshot,pilot_debt_snapshot as debt,pilot_job

PRIMITIVES_SHA='151856e1a8565a055c5cfdb848fb00cc490f2e3559bc1354c95985dc3ade96ea'
USB='F8:5B:1B:A5:F6:08'
FIRMWARE='mtb1-9816f92488'
PREFIX='000008f6a51b5bf8'
REQUIRED_ATTEMPTS=200  # Reviewed conservative headroom for a 170-DATA part.
REQUIRED_AIRTIME_US=40000000
SCHEMA='meshticulum.root-public100k-stage-handoff.v1'


def sha(raw):return hashlib.sha256(raw).hexdigest()

_PRIMITIVES={}


def load_primitives(path):
    """Root adapter loads once before low-FD acquisition; no device is opened."""
    path=Path(path).absolute();require(path.resolve()==path and os.geteuid()==0,'protected_primitives_path_required')
    for parent in (path.parent,*path.parent.parents):
        v=parent.lstat();require(stat.S_ISDIR(v.st_mode) and v.st_uid==0 and not v.st_mode&0o022,'protected_primitives_ancestry_required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        v=os.fstat(fd);require(stat.S_ISREG(v.st_mode) and v.st_uid==0 and v.st_nlink==1
            and not v.st_mode&0o022 and v.st_size<=100000,'protected_primitives_file_required')
        raw=os.pread(fd,100001,0);require(len(raw)==v.st_size and os.fstat(fd)==path.lstat() and sha(raw)==PRIMITIVES_SHA,'primitives_source_changed')
    finally:os.close(fd)
    module=types.ModuleType('reviewed_bundle_serial_primitives');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    cls=module.RetainedSerialOwner
    _PRIMITIVES[module]=((os.getpid(),threading.get_native_id()),(cls,module.one_pulse,module.protected_read,
        cls.check_device,cls.close,cls.reopen))
    return module


def primitives_current(module):
    require(type(module) is types.ModuleType and module in _PRIMITIVES,'unverified_checkpoint_primitives')
    context,refs=_PRIMITIVES[module];cls=module.RetainedSerialOwner
    require(cls is refs[0],'checkpoint_primitives_class_changed')
    require(context==(os.getpid(),threading.get_native_id()) and all(a is b for a,b in zip(
        (cls,module.one_pulse,module.protected_read,cls.check_device,cls.close,cls.reopen),refs)),
        'checkpoint_primitives_owner_or_code_changed')
    return refs


def radio_status(value):
    require(type(value) is dict and set(value)=={'usb_serial','firmware','mcu_boot','disarmed',
        'boot_attempts','boot_reserved_estimated_airtime_us'},'checkpoint_STATUS_shape_changed')
    boot=value['mcu_boot']
    require(value['usb_serial']==USB and value['firmware']==FIRMWARE and value['disarmed'] is True
        and type(boot) is str and len(boot)==32 and boot.startswith(PREFIX)
        and all(c in '0123456789abcdef' for c in boot),'checkpoint_radio_identity_or_disarm_changed')
    require(type(value['boot_attempts']) is int and 0<=value['boot_attempts']<=256
        and type(value['boot_reserved_estimated_airtime_us']) is int
        and 0<=value['boot_reserved_estimated_airtime_us']<=60000000,'checkpoint_counter_changed')
    return value


def headroom(value):
    radio_status(value)
    return 256-value['boot_attempts']>=REQUIRED_ATTEMPTS and 60000000-value['boot_reserved_estimated_airtime_us']>=REQUIRED_AIRTIME_US


def conditional_status(before,*,probe,live,pulse,reopen):
    """Bounded pure transition; caller supplies the owned, pinned operations."""
    radio_status(before);live()
    attempted=not headroom(before)
    if attempted:
        pulse();live();reopen();live()
    after=radio_status(probe());live()
    if attempted:
        require(after['mcu_boot'][:16]==before['mcu_boot'][:16]
            and int(after['mcu_boot'][16:],16)>int(before['mcu_boot'][16:],16)
            and after['boot_attempts']==after['boot_reserved_estimated_airtime_us']==0,
            'checkpoint_fresh_disarmed_boot_not_proven')
    else:require(after==before,'checkpoint_STATUS_changed_without_pulse')
    require(headroom(after),'checkpoint_headroom_not_proven');return after,attempted


def clock_bounds(now,previous,whole,anchor,last=None):
    require(type(now) is tuple and len(now)==2 and all(type(v) is int for v in now),'checkpoint_clock_changed')
    end=min(previous['handoff_end_epoch_us'],whole['expires_epoch_us'])
    mono_end=min(anchor['deadline_monotonic_us'],anchor['original_monotonic_us']+end-anchor['original_epoch_us'])
    require(anchor['original_epoch_us']<=now[0]<end and anchor['original_monotonic_us']<=now[1]<mono_end
        and (last is None or all(a>=b for a,b in zip(now,last))),'checkpoint_original_handoff_expired')
    return end,mono_end


def validate_root_handoff(value,*,context,policy,next_index,authorization):
    """Only the original protected Root opt-in makes this record admissible.

    Exported projections are comparison evidence, not independent authority.
    A is re-collected below; C/controller are the declared Root producer's
    observations. The producer must not publish this record before its own
    actual native callbacks and local clients are terminal and closed.
    """
    require(type(next_index) is int and 1<=next_index<5,'invalid_checkpoint_index')
    require(type(authorization) is dict and set(authorization)=={'schema','producer_sha256','primitives_sha256',
        'retained_history_count','maximum_checkpoints'} and authorization['schema']=='meshticulum.root-public100k-checkpoint-owner.v1'
        and authorization['primitives_sha256']==PRIMITIVES_SHA and type(authorization['retained_history_count']) is int and authorization['retained_history_count']==41
        and authorization['maximum_checkpoints']==4 and type(authorization['producer_sha256']) is str
        and len(authorization['producer_sha256'])==64 and all(c in '0123456789abcdef' for c in authorization['producer_sha256']),
        'original_Root_checkpoint_optin_required')
    require(value['schema']==SCHEMA and value['producer_sha256']==authorization['producer_sha256']
        and value['parent_id']==policy.parent_id and value['policy_digest']==policy.digest
        and value['bundle_context_sha256']==policy.bundle_context_sha256 and type(value['checkpoint_index']) is int and value['checkpoint_index']==next_index
        and value['next_campaign_id']==context.value['stages'][next_index]['campaign_id']
        and value['retained_parent_ids']==list(HISTORY) and len(HISTORY)==41
        and value['original_whole']=={k:context.value[k] for k in ('original_epoch_us','original_monotonic_us','expires_epoch_us','deadline_monotonic_us')}
        and value['original_handoff_end_epoch_us']==context.value['stages'][next_index-1]['handoff_end_epoch_us']
        and value['all_owned_clients_terminal'] is True and value['all_remote_callbacks_exit_zero'] is True
        and value['refunds']==0 and type(value['refunds']) is int and value['native_transport_permission'] is False,
        'checkpoint_Root_handoff_binding_changed')
    controller=value['controller'];require(controller['role']=='controller' and controller['parent_id']==policy.parent_id
        and controller['policy_digest']==policy.digest and controller['status']=='scoped_rows_observed'
        and controller['scoped_rows_complete'] is True and controller['pending_unknown'] is False,'checkpoint_controller_observation_missing')
    allocations=controller['detail']['allocations']
    require(controller['detail']['allocation_count']==len(allocations)==2*next_index,'checkpoint_controller_count_changed')
    endpoint_refs={(a['allocation_id'],c['unit']):c for a in allocations for c in a['children']}
    require(len(endpoint_refs)==4*next_index,'checkpoint_controller_endpoint_count_changed')
    expected=set()
    for unit in ('unit-a','unit-c'):
        native=value['native'][unit];require(native['parent_id']==policy.parent_id and native['policy_digest']==policy.digest
            and native['status']=='scoped_rows_observed' and native['scoped_rows_complete'] is True and native['pending_unknown'] is False
            and native['detail']['claim_count']==native['detail']['prepared_count']==native['detail']['start_count']==2*next_index
            and native['legacy_scoped_rows_complete'] is True and native['legacy_relation']['scoped_linkage_complete'] is True
            and not native['legacy_relation']['native_claims_without_scoped_actuals'],'checkpoint_native_projection_incomplete')
        require(len(native['detail']['claims'])==2*next_index and len(native['legacy_runs'])==2*next_index,
            'checkpoint_native_count_changed')
        staged=[]
        for claim in native['detail']['claims']:
            require(claim['prepared'] is True and claim['started'] is True
                and endpoint_refs.get((claim['controller_allocation_id'],unit))==claim['child'],
                'checkpoint_controller_native_ref_changed')
            child=claim['child'];stage=context.stage(child['campaign_id'],child['application_expires_epoch_us'])
            require(stage['index']<next_index and child['bundle_context_sha256']==policy.bundle_context_sha256,
                'checkpoint_future_stage_already_started')
            staged.append((stage['index'],child['format']))
        require(sorted(staged)==[(i,fmt) for i in range(next_index) for fmt in ('MR1.request','MR1.response')],
            'checkpoint_completed_stage_scope_changed')
        for row in native['legacy_runs']:
            require(row['status']=='scoped_rows_observed' and row['wire']['retired']==1 and not row['journal']['has_unknown_or_pending'],
                'checkpoint_unknown_or_unretired_child')
            expected.add((unit,row['run_id']))
    require(len(expected)==4*next_index and len(value['terminal_children'])==len(expected)
        and {(r['unit'],r['run_id']) for r in value['terminal_children']}==expected,'checkpoint_started_namespace_changed')
    require(all(r['job_record_found'] is True and r['live'] is False and r['disarmed_on_exit'] is True
        and r['retired'] is True for r in value['terminal_children']),'checkpoint_started_child_not_terminal')
    return value


def _hard_reset(primitives):
    raw=primitives.protected_read(primitives.ESPTOOL_RESET_SOURCE,200000)
    require(sha(raw)==primitives.ESPTOOL_RESET_SHA,'checkpoint_reset_source_not_pinned')
    import esptool
    require(esptool.__version__==primitives.ESPTOOL_VERSION,'checkpoint_reset_API_changed')
    reviewed=types.ModuleType('esptool.reviewed_bundle_checkpoint');reviewed.__package__='esptool'
    reviewed.__file__=primitives.ESPTOOL_RESET_SOURCE;sys.modules[reviewed.__name__]=reviewed
    exec(compile(raw,reviewed.__file__,'exec'),reviewed.__dict__)
    return reviewed.HardReset


def checkpoint(guard,*,serial_owner,primitives,next_index,handoff_sha256):
    """Consume one Root O_EXCL record; borrow original owner, at most one pulse.

    Only the verified4.5.1 callable loaded from151's exact source pin is used.
    The Root adapter/proof producer remains pending.
    No weak callback/default is accepted as a substitute for the actual guard.
    """
    primitive_refs=primitives_current(primitives)
    require(type(serial_owner) is primitive_refs[0],'reviewed_retained_serial_owner_required')
    owner=serial_owner;close=primitive_refs[4].__get__(owner)
    pid,thread=os.getpid(),threading.get_native_id();last=None;resetting=False
    try:
        require(type(guard) is ResumeParentGuard and guard.unit=='unit-a' and guard.policy.qualification_kind in BUNDLE_KINDS
            and os.geteuid()==0 and type(next_index) is int and 1<=next_index<5,'protected_A_bundle_guard_required')
        refs=(guard,guard.caps,guard.policy,guard.caps._clock,owner,owner.channel)
        source_owner=guard._records()['owner'];protected=json.loads(source_owner)
        context=BundleContext(protected['bundle_context'],guard.policy)
        previous=context.value['stages'][next_index-1]
        anchor={k:guard.caps._enrollment[k] for k in ('original_epoch_us','original_monotonic_us','deadline_monotonic_us')}
        anchor_raw=canonical(anchor)
        def refs_current():
            primitives_current(primitives)
            require((os.getpid(),threading.get_native_id())==(pid,thread) and all(a is b for a,b in zip(
                (guard,guard.caps,guard.policy,guard.caps._clock,owner,owner.channel),refs)),'checkpoint_owner_rebound')
            context._check();require(canonical(anchor)==anchor_raw,'checkpoint_anchor_mutated')
        def live():
            nonlocal last
            require((os.getpid(),threading.get_native_id())==(pid,thread) and all(a is b for a,b in zip(
                (guard,guard.caps,guard.policy,guard.caps._clock,owner,owner.channel),refs)), 'checkpoint_owner_rebound')
            refs_current();owner.check_device(named=not resetting);guard.check();refs_current()
            require(guard._records()['owner']==source_owner and canonical({k:guard.caps._enrollment[k] for k in anchor})==anchor_raw,
                'checkpoint_original_owner_or_anchor_changed')
            now=guard.caps._clock();refs_current();clock_bounds(now,previous,context.value,anchor,last);last=now
            reader.check();refs_current();owner.check_device(named=not resetting);return now
        with Reader() as reader:
            path=guard.state/'parents'/guard.policy.parent_id/('checkpoint-%02d.json'%next_index)
            raw=reader.read(path,2000000);require(sha(raw)==handoff_sha256,'checkpoint_protected_handoff_pin_changed')
            proof=validate_root_handoff(parse(raw),context=context,policy=guard.policy,next_index=next_index,
                authorization=protected.get('bundle_radio_checkpoint'))
            require(proof['runtime_sha256']==protected['runtime_sha256'] and proof['manifest_sha256']==protected['manifest_sha256'],
                'checkpoint_runtime_changed')
            live()
            observed=proof['native']['unit-a'];now=live();handoff_epoch,handoff_mono=clock_bounds(now,previous,context.value,anchor)
            expiry=handoff_epoch;observation_end=min(expiry,now[0]+55000000)
            runs=tuple(debt.RunScope(r['run_id'],r['host_boot_id'],r['transport'],r['files']['grant.json']['sha256'],
                r['grant']['context_sha256'],r['journal']['owner']['binding']) for r in observed['legacy_runs'])
            scope=parent_snapshot.Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,guard.policy.parent_id,
                guard.policy.digest,observed['expected_database_sha256'],'unit-a',guard.policy.unit('unit-a').host_boot_id,observation_end)
            legacy=debt.Scope('meshticulum.public-debt-scope.v1','unit-a',scope.current_host_boot_id,proof['runtime_sha256'],proof['manifest_sha256'],observation_end,runs)
            from .owned_native_snapshot import collect_owned_native,match_checkpoint_projection
            actual=collect_owned_native(guard.state,scope,caps=guard.caps,clock=guard.caps._clock,boot_reader=boot,legacy_scope=legacy).to_dict()
            match_checkpoint_projection(actual,observed,scope=scope,handoff_epoch_us=handoff_epoch,handoff_monotonic_us=handoff_mono)
            for row in runs:
                status=pilot_job.inspect(guard.state/'runs'/row.run_id,unit='unit-a',run_id=row.run_id,boot=scope.current_host_boot_id)
                require(status['job_record_found'] is True and status['live'] is False,'checkpoint_actual_A_worker_live')
                result=parse(reader.read(guard.state/'runs'/row.run_id/'result.json',300000))
                require(result['disarmed_on_exit'] is True and result['parent']['parent_id']==guard.policy.parent_id
                    and result['parent']['policy_digest']==guard.policy.digest,'checkpoint_actual_A_disarm_missing')
            live()
            # Root-only one-use metadata lives outside NativeSTATE. A failed
            # checkpoint stays consumed; neither retry nor a second pulse is
            # authorized by the old handoff record.
            run=Path('/run/meshticulum-bench');info=run.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o700,
                'protected_checkpoint_receipt_root_required')
            write_new(run,'bundle-checkpoint-'+guard.policy.parent_id+'-%02d.used.json'%next_index,
                canonical(dict(parent_id=guard.policy.parent_id,bundle_context_sha256=guard.policy.bundle_context_sha256,
                    checkpoint_index=next_index,handoff_sha256=handoff_sha256,original_handoff_end_epoch_us=expiry)))
            live()
            def probe():
                from meshticulum.bench.serial_worker import SerialWorker
                from meshticulum.bench.serial_protocol import Frame,Message
                live();worker=SerialWorker(owner.channel);identity=worker.discover(expected_board_id=1,expected_version=FIRMWARE);live()
                end=clock_bounds(last,previous,context.value,anchor)[1]/1000000
                until=min(owner.channel.monotonic()+2,end);request=guard.caps._clock()[0]
                worker._send(Frame(Message.STATUS,identity.boot,request),until);reply=worker._wait(request,until);live()
                require(reply.type is Message.STATUS_REPLY and reply.boot==identity.boot and len(reply.body)==49
                    and reply.body[46:]==bytes(3),'checkpoint_actual_STATUS_not_disarmed')
                return dict(usb_serial=USB,firmware=identity.version,mcu_boot=identity.boot.hex(),disarmed=True,
                    boot_attempts=int.from_bytes(reply.body[30:34],'big'),boot_reserved_estimated_airtime_us=int.from_bytes(reply.body[34:42],'big'))
            def pulse():
                nonlocal resetting
                owner.check_device();live();hard=_hard_reset(primitives);live();resetting=True
                primitives.one_pulse(owner,live,hard)
            def reopen():
                nonlocal resetting
                owner.reopen(live);resetting=False;live()
            before=probe();after,attempted=conditional_status(before,probe=probe,live=live,pulse=pulse,reopen=reopen)
            live();end,mono_end=clock_bounds(last,previous,context.value,anchor)
            result=dict(schema='meshticulum.A-public100k-checkpoint.v1',parent_id=guard.policy.parent_id,
                bundle_context_sha256=guard.policy.bundle_context_sha256,checkpoint_index=next_index,before=before,after=after,
                pulse_attempted=attempted,original_handoff_end_epoch_us=end,original_native_monotonic_deadline_us=mono_end,
                old_paid_caps_preserved=True,refunds=0,native_transport_permission=False)
    finally:close()
    guard.check();refs_current();final=guard.caps._clock();refs_current()
    clock_bounds(final,previous,context.value,anchor,last)
    result['native_owner_released']=True;return result
