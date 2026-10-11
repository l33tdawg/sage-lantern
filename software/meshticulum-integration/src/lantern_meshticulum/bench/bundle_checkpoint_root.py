# SPDX-License-Identifier: Apache-2.0
"""Root-owned finite checkpoint producer; native callbacks carry metadata only.

The original owner must pin this exact source before enrollment. No default
permission, record adoption, lease, key, credit or deadline is created here.
"""
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import threading
import stat
import math

from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentCaps,canonical
from .pilot_bundle import BundleCampaign,BundleContext
from .pilot_parent_snapshot import Scope,Snapshot,collect
from .pilot_resume_rpc import Reader,HISTORY,CONTRACT_SCHEMA
from .pilot_ssh import SSHOperator,Job,one_record
from .bundle_radio_checkpoint import validate_root_handoff,clock_bounds
from .history_metadata_fence import _stamp

RPC_ENTRY='\nimport base64,hashlib,importlib,json,os,stat,sys,lzma,time\nfrom pathlib import Path\nroot,pin,adapter_pin,action,unit,blob,input_sha,epoch_end,mono_end=sys.argv[1:]\nepoch_end,mono_end=int(epoch_end),int(mono_end)\nfirst=(time.time_ns()//1000,time.monotonic_ns()//1000);last=first\ndef window():\n global last\n now=(time.time_ns()//1000,time.monotonic_ns()//1000)\n need(now[0]<epoch_end and now[1]<mono_end and all(a>=b for a,b in zip(now,last)),"remote_checkpoint_window_expired")\n last=now;return now\ndef need(v,r):\n if not v:raise RuntimeError(r)\ndef read(path,limit):\n window()\n for p in (path.parent,*path.parent.parents):\n  s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022 and p.resolve()==p,\'runtime_ancestry\')\n fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)\n try:\n  s=os.fstat(fd);need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=limit,\'runtime_record\')\n  raw=os.pread(fd,limit+1,0);need(len(raw)==s.st_size and os.fstat(fd)==path.lstat(),\'runtime_record_changed\');return raw\n finally:os.close(fd)\nwindow()\nneed(os.geteuid()==0,\'actual_root_required\');root=Path(root)\nmraw=read(root/\'manifest.json\',65536);need(hashlib.sha256(mraw).hexdigest()==pin,\'runtime_manifest_pin\')\nm=json.loads(mraw);need(m[\'private_services_enabled\'] is False and m[\'public_parent_claims_required\'] is True,\'public_runtime_required\')\nfor name,h in m[\'files\'].items():\n part=Path(name);need(not part.is_absolute() and \'..\' not in part.parts,\'runtime_member\')\n need(hashlib.sha256(read(root/part,500000)).hexdigest()==h,\'runtime_member_pin\')\nneed(m[\'files\'].get(\'lantern_meshticulum/bench/pilot_resume_rpc.py\')==adapter_pin,\'native_adapter_pin\')\ncompressed=base64.b64decode(blob,validate=True);need(len(compressed)<=70000,\'RPC_compressed_bound\')\nd=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=33554432);raw=d.decompress(compressed,max_length=2000001)\nneed(len(raw)<=2000000 and d.eof and not d.unused_data\n and hashlib.sha256(raw).hexdigest()==input_sha,\'RPC_input_bound_or_pin\')\nsys.path.insert(0,str(root));module=importlib.import_module(\'lantern_meshticulum.bench.pilot_resume_rpc\')\nneed(getattr(module,\'CONTRACT_SCHEMA\',None)==\'meshticulum.private-resume-native-rpc.v1\',\'native_integration_contract_missing\')\n# dispatch MUST implement the reviewed real-admission integration described\n# above; no local fallback, lambda approval or deserialized Snapshot allowed.\npayload=json.loads(raw)\nneed(payload["checkpoint_remote_end_epoch_us"]==epoch_end and payload["checkpoint_remote_deadline_monotonic_us"]==mono_end,"remote_checkpoint_window_rebound")\nwindow()\nresult=module.dispatch(action,unit,payload,runtime_root=root,manifest_sha256=pin)\nfinished=window()\nresult.update(checkpoint_callback_completed=True,checkpoint_remote_end_epoch_us=epoch_end,checkpoint_callback_finished_epoch_us=finished[0])\nneed(type(result) is dict and result.get(\'schema\')==\'meshticulum.private-resume-native-rpc.v1\'\n and result.get(\'unit\')==unit and result.get(\'action\')==action,\'RPC_result_scope\')\nprint(json.dumps(result,sort_keys=True),flush=True)\n'

REMOTE_TIMEOUT_ENTRY="import os,sys,time\nend,mono,python,*args=sys.argv[1:]\nend,mono=int(end),int(mono)\nleft=min(end-time.time_ns()//1000,mono-time.monotonic_ns()//1000)-100000\nif left<=0:raise RuntimeError('original_checkpoint_remote_window_expired')\nseconds=str(left//1000000)+'.'+str(left%1000000).zfill(6)+'s'\nos.execv('/usr/bin/timeout',['/usr/bin/timeout','--signal=TERM','--kill-after=1s',seconds,python,*args])\n"

PROJECTION=('status','scoped_rows_complete','pending_unknown','policy_row_sha256','prior_lineage','detail','files')


def sha(raw):return hashlib.sha256(raw).hexdigest()


def callback_window(policy,stage_epoch,stage_mono,operator_end,campaign_end,count,now):
    """Budget all existing sequential Job.close waits before original bounds."""
    require(type(count) is int and 1<=count<=2 and type(now) is tuple and len(now)==2
        and all(type(v) is int for v in now) and all(type(v) in (int,float) and math.isfinite(v)
        for v in (operator_end,campaign_end)),'invalid_checkpoint_callback_window')
    owner_mono=min(stage_mono,int(operator_end*1000000),int(campaign_end*1000000),policy.deadline_monotonic_us)
    owner_epoch=min(stage_epoch,policy.expires_epoch_us,
        policy.issued_epoch_us+owner_mono-policy.original_monotonic_us)
    # Job.close may wait2s after TERM and another2s after KILL, per job.
    # One second remains for local bookkeeping; remote TERM/KILL gets2s
    # before that entire local cleanup reserve starts.
    cleanup=4000000*count+1000000
    local=owner_mono-cleanup
    remote_epoch=owner_epoch-cleanup-2000000
    remote_mono=owner_mono-cleanup-2000000
    require(now[0]<remote_epoch and now[1]<remote_mono,'checkpoint_remote_cleanup_reserve_exhausted')
    return remote_epoch,remote_mono,local,owner_epoch,owner_mono


def source_pin():
    path=Path(__file__).absolute();ancestry=[]
    for parent in (path.parent,*path.parent.parents):
        value=parent.lstat()
        require(parent.resolve()==parent and stat.S_ISDIR(value.st_mode) and value.st_uid in (0,os.geteuid())
                and not value.st_mode&0o022,'producer_source_ancestry_changed')
        ancestry.append((parent,value.st_dev,value.st_ino,value.st_mode,value.st_uid))
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        value=os.fstat(fd);require(stat.S_ISREG(value.st_mode) and value.st_uid==os.geteuid()
            and value.st_nlink==1 and not value.st_mode&0o022 and value.st_size<=100000,'producer_source_file_changed')
        raw=os.pread(fd,100001,0)
        require(len(raw)==value.st_size and _stamp(os.fstat(fd))==_stamp(path.lstat())==_stamp(value)
            and all((p.lstat().st_dev,p.lstat().st_ino,p.lstat().st_mode,p.lstat().st_uid)==tuple(identity)
                    for p,*identity in ancestry),'producer_source_changed')
        return sha(raw)
    finally:os.close(fd)


def controller_observation(parent,context,index):
    """Release/reopen the exact owned ledger; never enroll or renew its clocks."""
    require(type(parent) is ParentCaps and type(context) is BundleContext
        and context.policy is parent.policy and parent.current() is True,'genuine_controller_owner_required')
    policy=parent.policy;policy_bytes=canonical(policy.to_dict())
    path,clock,boot=parent.path,parent._clock,parent._boot
    identity=tuple(parent._ids['db']);now=clock();parent._bound();original_boot=boot();parent._bound()
    context._check();end,mono_end=clock_bounds(now,context.value['stages'][index-1],context.value,
        dict(original_epoch_us=policy.issued_epoch_us,original_monotonic_us=policy.original_monotonic_us,
             deadline_monotonic_us=policy.deadline_monotonic_us))
    require(original_boot==policy.controller_boot_id and parent.close() is True
        and parent._closed and not parent._uncertain,'controller_owner_close_unproved')
    reopened=None
    try:
        with Reader() as reader:
            raw=reader.read(path,2000000)
            require((path.lstat().st_dev,path.lstat().st_ino)==identity
                and parent.path is path and parent.policy is policy and parent._clock is clock and parent._boot is boot
                and canonical(policy.to_dict())==policy_bytes and boot()==original_boot,'controller_handoff_binding_changed')
            snapshot=collect(path.parent,Scope('meshticulum.public-parent-snapshot-scope.v1','controller',os.geteuid(),
                policy.parent_id,policy.digest,sha(raw),None,original_boot,end),clock=clock,boot_reader=boot)
            require(type(snapshot) is Snapshot,'genuine_typed_controller_observation_required')
            observed=snapshot.to_dict()
            require(observed['status']=='scoped_rows_observed' and observed['scoped_rows_complete'] is True
                and observed['pending_unknown'] is False and observed['detail']['allocation_count']==2*index,
                'controller_checkpoint_rows_unqualified')
            reader.check();later=clock()
            require(all(a>=b for a,b in zip(later,now)) and later[0]<end and later[1]<mono_end
                and boot()==original_boot and canonical(policy.to_dict())==policy_bytes,'controller_checkpoint_clock_changed')
            reopened=ParentCaps.open(path,policy,clock=clock,boot_reader=boot)
            require(reopened.path==path and reopened.policy is policy and reopened._clock is clock and reopened._boot is boot
                and tuple(reopened._ids['db'])==identity and reopened.current() is True,'controller_same_ledger_reopen_required')
            reader.check()
        return reopened,observed
    except BaseException:
        if reopened is not None:require(reopened.close(),'controller_reopened_cleanup_unproved')
        raise


class CheckpointProducer:
    """One Root process/thread, exact operator, original owner/context and source."""
    def __init__(self,operator,campaign,owners,priors,preparations,*,rpc_sha256):
        require(type(operator) is SSHOperator and type(campaign) is BundleCampaign
            and campaign.operator is operator and campaign.lora_sender_first is True,'owned_sender_first_bundle_required')
        self.operator,self.campaign=operator,campaign
        self.owners,self.priors,self.preparations=owners,priors,preparations
        self.rpc_sha256,self.rpc_entry=rpc_sha256,RPC_ENTRY
        self.pin=source_pin();self._pid=(os.getpid(),threading.get_native_id())
        self._refs=(operator,campaign,owners,priors,preparations,operator._argv.__func__,operator.clock,
                    campaign.clock,campaign.epoch,campaign.sleep)
        self._owner_bytes=canonical(owners);self._prior_bytes=canonical(priors)
        self._rpc=(rpc_sha256,RPC_ENTRY,REMOTE_TIMEOUT_ENTRY);self._last=None;self._index=0
        self._job_refs=(Job,Job.__init__,Job.wait,Job.close)
        self.callback_attempts=[]
        self._config_ref=operator.config;self._config_bytes=canonical(operator.config)
        self._owner_deadlines=(operator.deadline,campaign.handoff)
        require(all(owner['bundle_radio_checkpoint']['producer_sha256']==self.pin for owner in owners.values()),
                'producer_source_not_original_owner_bound')

    def current(self,index):
        require((os.getpid(),threading.get_native_id())==self._pid
            and all(a is b for a,b in zip((self.operator,self.campaign,self.owners,self.priors,self.preparations,
                self.operator._argv.__func__,self.operator.clock,self.campaign.clock,self.campaign.epoch,self.campaign.sleep),self._refs))
            and canonical(self.owners)==self._owner_bytes and canonical(self.priors)==self._prior_bytes
            and self.operator.config is self._config_ref and canonical(self.operator.config)==self._config_bytes
            and (self.operator.deadline,self.campaign.handoff)==self._owner_deadlines
            and all(a is b for a,b in zip((Job,Job.__init__,Job.wait,Job.close),self._job_refs))
            and self.rpc_entry is RPC_ENTRY and (self.rpc_sha256,self.rpc_entry,REMOTE_TIMEOUT_ENTRY)==self._rpc and source_pin()==self.pin,'Root_checkpoint_producer_rebound')
        self.campaign.current();context=self.campaign.context;policy=self.campaign.parent.policy
        now=(self.campaign.epoch(),int(self.campaign.clock()*1000000))
        require(now[1]<int(min(self.operator.deadline,self.campaign.handoff)*1000000),
                'original_checkpoint_manager_window_expired')
        end,mono=clock_bounds(now,context.value['stages'][index-1],context.value,
            dict(original_epoch_us=policy.issued_epoch_us,original_monotonic_us=policy.original_monotonic_us,
                 deadline_monotonic_us=policy.deadline_monotonic_us),self._last)
        self._last=now;return end,mono

    def _payload(self,unit,index):
        return dict(policy=self.campaign.parent.policy.to_dict(),owner=self.owners[unit],prior=self.priors[unit],
                    preparation_sha256=dict(self.preparations[unit]),checkpoint_index=index)

    def _jobs(self,actions,index):
        """Launch genuine owned jobs; publish nothing until every callback closes."""
        jobs=[];values={};input_pins={};states=[];preparation_bytes=canonical(self.preparations)
        end,mono=self.current(index)
        remote_end,_,local_deadline,_,_=callback_window(self.campaign.parent.policy,end,mono,
            self.operator.deadline,self.campaign.handoff,len(actions),
            (self.campaign.epoch(),int(self.operator.clock()*1000000)))
        try:
            for unit,action,extra in actions:
                self.current(index)
                phase=self.owners[unit]['native_phase'][unit]
                native_mono=min(phase['deadline_monotonic_us'],phase['monotonic_us']+remote_end-phase['epoch_us'])
                require(remote_end>self.campaign.epoch() and local_deadline>int(self.operator.clock()*1000000),
                        'checkpoint_remote_cleanup_reserve_exhausted')
                payload=self._payload(unit,index)|extra|dict(checkpoint_remote_end_epoch_us=remote_end,
                    checkpoint_remote_deadline_monotonic_us=native_mono);raw=canonical(payload)
                blob=base64.b64encode(lzma.compress(raw,format=lzma.FORMAT_XZ,preset=6)).decode()
                require(len(raw)<=2000000 and len(blob)<=93336,'checkpoint_metadata_bound')
                owner=self.owners[unit];runtime='/opt/meshticulum-bench/runtime-'+owner['runtime_sha256'][:16]
                python=self.operator.config['units'][unit]['python']
                command=[python,'-I','-B','-c',REMOTE_TIMEOUT_ENTRY,str(remote_end),str(native_mono),python,
                         '-I','-B','-c',self.rpc_entry,runtime,owner['manifest_sha256'],self.rpc_sha256,
                         action,unit,blob,sha(raw),str(remote_end),str(native_mono)]
                argv=self.operator._argv(unit,command,root=True,use_connections=False);self.current(index)
                job=Job(argv,password=self.operator.passwords.get(unit),deadline=local_deadline/1000000,clock=self.operator.clock)
                attempt=dict(unit=unit,action=action,remote_terminal_proven=False,remote_end_epoch_us=remote_end)
                self.callback_attempts.append(attempt)
                jobs.append((unit,action,job,attempt));input_pins[unit]=sha(raw)
            for unit,action,job,attempt in jobs:
                require(canonical(self.preparations)==preparation_bytes,'checkpoint_preparations_changed')
                require(job.wait()==0,'checkpoint_native_callback_failed')
                value=one_record(bytes(job.output),CONTRACT_SCHEMA)
                require(value['unit']==unit and value['action']==action and value['private_production'] is False
                    and value['global_debt_complete'] is False and value['radio_opened'] is (action=='checkpoint'),
                    'checkpoint_native_response_changed')
                require(value.get('checkpoint_callback_completed') is True
                    and value.get('checkpoint_remote_end_epoch_us')==attempt['remote_end_epoch_us']
                    and type(value.get('checkpoint_callback_finished_epoch_us')) is int
                    and value['checkpoint_callback_finished_epoch_us']<attempt['remote_end_epoch_us'],
                    'checkpoint_remote_callback_completion_unproved')
                attempt['remote_terminal_proven']=True
                values[unit]=value;self.current(index)
        finally:
            errors=[]
            for unit,action,job,attempt in jobs:
                try:job.close()
                except BaseException as error:errors.append(type(error).__name__)
                states.append(dict(unit=unit,action=action,returncode=job.process.poll(),closed=job.closed,remote_terminal_proven=attempt['remote_terminal_proven']))
            require(not errors and all(type(v['returncode']) is int and v['returncode']==0 and v['closed'] is True and v['remote_terminal_proven'] is True for v in states),
                    'checkpoint_owned_clients_not_terminal')
        self.current(index);return values,input_pins,states

    def handoff(self,index):
        require(type(index) is int and index==self._index+1 and 1<=index<5,'checkpoint_order_or_replay')
        self.current(index);old=self.campaign;old.retire();self.current(index)
        # Native job termination and the local SSH client's completion are
        # distinct. Both must hold before any checkpoint proof is published.
        local_jobs=self.operator.jobs
        require(len(local_jobs)==4*index and all(type(job) is Job and job.closed is True
                and type(job.process.poll()) is int and job.process.returncode==0 for job in local_jobs.values()),
                'checkpoint_endpoint_clients_not_terminal')
        native,inputs,states=self._jobs([(u,'checkpoint-audit',{}) for u in ('unit-a','unit-c')],index)
        # All remote callbacks are now exited/closed. Genuine controller collect
        # needs the original EX owner released; reopening retains identical paid
        # ledger/policy and clocks. An old wrapper is never used after close.
        parent,controller=controller_observation(old.parent,old.context,index)
        try:
            resumed=BundleCampaign(self.operator,parent,old.context.value,peers=old.peers,boots=old.boots,
                handoff_deadline_monotonic=old.handoff,clock=old.clock,epoch=old.epoch,sleep=old.sleep,lora_sender_first=True)
            self.campaign=resumed;self._refs=(self._refs[0],resumed,*self._refs[2:]);self.current(index)
            context=resumed.context;owner=self.owners['unit-a']
            proof=dict(schema='meshticulum.root-public100k-stage-handoff.v1',producer_sha256=self.pin,
                parent_id=parent.policy.parent_id,policy_digest=parent.policy.digest,bundle_context_sha256=parent.policy.bundle_context_sha256,
                checkpoint_index=index,next_campaign_id=context.value['stages'][index]['campaign_id'],retained_parent_ids=list(HISTORY),
                original_whole={k:context.value[k] for k in ('original_epoch_us','original_monotonic_us','expires_epoch_us','deadline_monotonic_us')},
                original_handoff_end_epoch_us=context.value['stages'][index-1]['handoff_end_epoch_us'],
                all_owned_clients_terminal=True,all_remote_callbacks_exit_zero=True,refunds=0,native_transport_permission=False,
                controller=controller,native={u:v['terminal_observation'] for u,v in native.items()},
                terminal_children=[r for v in native.values() for r in v['terminal_children']],
                runtime_sha256=owner['runtime_sha256'],manifest_sha256=owner['manifest_sha256'],
                callback_inputs=inputs,owned_callbacks=states)
            validate_root_handoff(proof,context=context,policy=parent.policy,next_index=index,authorization=owner['bundle_radio_checkpoint'])
            self.current(index);digest=sha(canonical(proof))
            stored,_,_=self._jobs([('unit-a','store-handoff',dict(handoff=proof,handoff_sha256=digest))],index)
            require(stored['unit-a']['handoff_stored'] is True and stored['unit-a']['handoff_sha256']==digest,'handoff_store_unproved')
            checked,_,_=self._jobs([('unit-a','checkpoint',dict(handoff_sha256=digest))],index)
            result=checked['unit-a']['checkpoint']
            require(result['native_owner_released'] is True and result['old_paid_caps_preserved'] is True
                and result['refunds']==0 and result['native_transport_permission'] is False,'checkpoint_release_unproved')
            self.current(index);self._index=index;return resumed,dict(proof=proof,checkpoint=result)
        except BaseException:
            require(parent.close(),'checkpoint_controller_cleanup_unproved');raise
