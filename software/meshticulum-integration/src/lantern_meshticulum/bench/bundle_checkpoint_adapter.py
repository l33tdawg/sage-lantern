# SPDX-License-Identifier: Apache-2.0
"""Explicit protected checkpoint adapter; no admission on import or defaults."""
from pathlib import Path
from .pilot_parent_caps import canonical
from .pilot_bundle import BundleContext,KIND,BUNDLE_KINDS
from . import bundle_radio_checkpoint as checkpoint_api,pilot_job
from .pilot_resume_rpc import need,parse,sha,STATE,boot,clock,write_new

PRIMITIVES_PATH=Path('/run/meshticulum-bench/a-radio-restart-v2.py')


def remote_guard(policy,owner,unit,payload,clock):
    """Bind a tighter call window to the original protected native anchor."""
    index=payload['checkpoint_index'];context_for(unit,policy,owner,index)
    phase=owner['native_phase'][unit];phase_raw=canonical(phase)
    end=payload['checkpoint_remote_end_epoch_us'];mono=payload['checkpoint_remote_deadline_monotonic_us']
    original_end=min(owner['bundle_context']['stages'][index-1]['handoff_end_epoch_us'],policy.expires_epoch_us)
    need(type(end) is int and type(mono) is int and end<=original_end-5000000
         and mono==min(phase['deadline_monotonic_us'],phase['monotonic_us']+end-phase['epoch_us']),
         'checkpoint_remote_window_not_original_bound')
    first=clock();last=[first]
    def check():
        need(owner['native_phase'][unit] is phase and canonical(phase)==phase_raw
             and payload.get('checkpoint_remote_end_epoch_us')==end
             and payload.get('checkpoint_remote_deadline_monotonic_us')==mono,
             'checkpoint_remote_anchor_changed')
        now=clock()
        need(type(now) is tuple and len(now)==2 and all(type(v) is int for v in now) and phase['epoch_us']<=now[0]<end
             and phase['monotonic_us']<=now[1]<mono and all(a>=b for a,b in zip(now,last[0])),
             'checkpoint_remote_original_window_expired')
        last[0]=now;return now
    check();return check


def context_for(unit,policy,owner,next_index):
    need(unit in ('unit-a','unit-c') and policy.qualification_kind in BUNDLE_KINDS
         and type(next_index) is int and 1<=next_index<5,'invalid_bundle_checkpoint_scope')
    context=BundleContext(owner['bundle_context'],policy)
    authorization=owner.get('bundle_radio_checkpoint')
    # The checkpoint gate validates the original descriptor in full. This
    # early check refuses missing opt-in before even opening the serial FD.
    need(type(authorization) is dict and authorization.get('schema')=='meshticulum.root-public100k-checkpoint-owner.v1'
         and authorization.get('primitives_sha256')==checkpoint_api.PRIMITIVES_SHA
         and type(authorization.get('retained_history_count')) is int
         and authorization['retained_history_count']==len(checkpoint_api.HISTORY)==30
         and type(authorization.get('maximum_checkpoints')) is int and authorization['maximum_checkpoints']==4
         and type(authorization.get('producer_sha256')) is str
         and len(authorization['producer_sha256'])==64
         and all(c in '0123456789abcdef' for c in authorization['producer_sha256']),
         'original_checkpoint_owner_optin_required')
    return context,authorization


def open_serial(unit,policy,owner,next_index):
    """Called after protected owner checks, before large history FD acquisition."""
    need(unit=='unit-a','checkpoint_requires_A')
    context,_=context_for(unit,policy,owner,next_index)
    phase=owner['native_phase'][unit]
    checkpoint_api.clock_bounds(clock(),context.value['stages'][next_index-1],context.value,
        dict(original_epoch_us=phase['epoch_us'],original_monotonic_us=phase['monotonic_us'],
             deadline_monotonic_us=phase['deadline_monotonic_us']))
    primitives=checkpoint_api.load_primitives(PRIMITIVES_PATH)
    from meshticulum.bench.serial_port import ExclusiveSerial,usb_identity
    from meshticulum.bench.linux_hat import _existing_openers
    path='/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_'+checkpoint_api.USB+'-if00'
    channel=ExclusiveSerial(path,usb_serial=checkpoint_api.USB)
    # The exact reviewed primitive captures/cleans construction failures and
    # verifies native locks, device inode, USB and process/thread ownership.
    serial_owner=primitives.retain_serial_owner(channel,path,usb_identity,_existing_openers)
    return serial_owner,primitives


def terminal_children(reader,unit,policy,observation):
    result=[]
    for row in observation['legacy_runs'] or []:
        directory=STATE/'runs'/row['run_id']
        raw=reader.read(directory/'result.json',300000);value=parse(raw)
        need(sha(raw)==row['files']['result.json']['sha256']
             and value['complete'] is True and value['disarmed_on_exit'] is True
             and value['parent']['parent_id']==policy.parent_id and value['parent']['policy_digest']==policy.digest,
             'checkpoint_completed_result_required')
        status=pilot_job.inspect(directory,unit=unit,run_id=row['run_id'],boot=boot())
        need(status['job_record_found'] is True and status['live'] is False
             and row['wire']['retired']==1 and not row['journal']['has_unknown_or_pending'],
             'checkpoint_native_job_not_terminal')
        result.append(dict(unit=unit,run_id=row['run_id'],job_record_found=True,live=False,
                           disarmed_on_exit=True,retired=True))
    reader.check();return result


def store(reader,unit,policy,owner,payload,observed,children):
    """Root-pinned comparison record after genuine own audit; no radio open."""
    need(unit=='unit-a','handoff_storage_requires_A')
    index=payload['checkpoint_index'];context,authorization=context_for(unit,policy,owner,index)
    proof=payload['handoff'];raw=canonical(proof)
    need(sha(raw)==payload['handoff_sha256'],'checkpoint_handoff_sha_changed')
    checkpoint_api.validate_root_handoff(proof,context=context,policy=policy,next_index=index,authorization=authorization)
    keys=('status','scoped_rows_complete','pending_unknown','policy_row_sha256','prior_lineage','detail','files',
          'legacy_runs','legacy_relation','legacy_scoped_rows_complete')
    need(canonical({k:observed[k] for k in keys})==canonical({k:proof['native']['unit-a'][k] for k in keys})
         and sorted(children,key=lambda r:r['run_id'])==sorted([r for r in proof['terminal_children'] if r['unit']=='unit-a'],key=lambda r:r['run_id'])
         and proof['runtime_sha256']==owner['runtime_sha256'] and proof['manifest_sha256']==owner['manifest_sha256'],
         'checkpoint_actual_A_or_runtime_changed')
    checkpoint_api.clock_bounds(clock(),context.value['stages'][index-1],context.value,observed['detail']['enrollment'])
    reader.check()
    write_new(STATE/'parents'/policy.parent_id,'checkpoint-%02d.json'%index,raw)
    reader.check()
    return dict(checkpoint_index=index,handoff_sha256=sha(raw),handoff_stored=True,
                native_transport_permission=False,refunds=0)
