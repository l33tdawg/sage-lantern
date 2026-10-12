# SPDX-License-Identifier: Apache-2.0
"""Protected public100k bundle binding; no enrollment or transport activation.

A signed whole descriptor plus five predeclared part campaigns is hashed into
ParentPolicy. The original whole clock includes capture/setup, every request,
response, retry, ACK grace, verification and cleanup. Fresh part contexts never
change the whole deadline or an earlier part's expiry. One MB is not admitted.
"""
import base64
import hashlib
import json
import os
import stat
import fcntl
from contextlib import ExitStack
from pathlib import Path
from types import MappingProxyType

from meshticulum.bench.wire import require
from meshticulum.resources import byte_bundle as bundle, knowledge_pack as kp, lamplighter as ll, resource
from .pilot_parent_caps import BUNDLE_KINDS, canonical, digest, identifier, Cost
from .profiles import GFSK_BUNDLE_KIND,GFSK_NAME

KIND = 'c-bundle100k-v1'
SCHEMA = 'meshticulum.native-public100k-bundle-context.v1'
KIND_V2 = 'c-bundle100k-v2'
SCHEMA_V2 = 'meshticulum.native-public100k-bundle-context.v2'
COMMITMENT_SCHEMA = 'meshticulum.public100k-capture-commitment.v2'
SSH_SCHEMA_V2 = 'meshticulum.finite-ssh-bundle100k.v2'
KIND_V3 = 'c-bundle100k-v3'
SCHEMA_V3 = 'meshticulum.native-public100k-bundle-context.v3'
COMMITMENT_SCHEMA_V3 = 'meshticulum.public100k-capture-commitment.v3'
SSH_SCHEMA_V3 = 'meshticulum.finite-ssh-bundle100k.v3'
SCHEMA_GFSK='meshticulum.native-public100k-gfsk125-bundle-context.v1'
# Versioned original scopes; never upgrade or renew an existing context.
COMMITTED_CONTRACTS = MappingProxyType({
    KIND_V2:(SCHEMA_V2,1200000000,1205000000,150000000,COMMITMENT_SCHEMA,SSH_SCHEMA_V2,'meshticulum.private-resume-actual-capture.v2'),
    KIND_V3:(SCHEMA_V3,1350000000,1355000000,180000000,COMMITMENT_SCHEMA_V3,SSH_SCHEMA_V3,'meshticulum.private-resume-actual-capture.v3'),
    GFSK_BUNDLE_KIND:(SCHEMA_GFSK,1350000000,1355000000,180000000,'meshticulum.public100k-gfsk125-capture-commitment.v1','meshticulum.finite-ssh-gfsk125-bundle100k.v1','meshticulum.private-resume-actual-gfsk125-capture.v1'),
})


def _context_data(value, *, parent_id=None):
    require(type(value) is dict, 'missing_bundle_context')
    raw=canonical(value); require(len(raw)<=100000,'bundle_context_too_large')
    v=json.loads(raw)
    required={'schema','manifest_base64','signature_base64','trust','manifest_sha256','whole_bytes','whole_sha256',
          'original_epoch_us','original_monotonic_us','expires_epoch_us','deadline_monotonic_us','stages'}
    require(set(v)==required and v['schema'] in (SCHEMA,SCHEMA_V2,SCHEMA_V3,SCHEMA_GFSK),'bundle_context_policy_changed')
    kind={SCHEMA:KIND,SCHEMA_V2:KIND_V2,SCHEMA_V3:KIND_V3,SCHEMA_GFSK:GFSK_BUNDLE_KIND}[v['schema']]
    for key in ('manifest_sha256','whole_sha256'):digest(v[key])
    manifest_raw=base64.b64decode(v['manifest_base64'],validate=True)
    signature_raw=base64.b64decode(v['signature_base64'],validate=True)
    require(base64.b64encode(manifest_raw).decode()==v['manifest_base64']
        and base64.b64encode(signature_raw).decode()==v['signature_base64'],'noncanonical_bundle_metadata')
    manifest=ll.manifest(manifest_raw,signature_raw,v['trust'],v['manifest_sha256'])
    expected=bundle.descriptor(manifest)
    require(type(v['whole_bytes']) is int and v['whole_bytes']==expected['bytes']==100000
        and v['whole_sha256']==expected['sha256'] and len(expected['parts'])==5
        and all(c['bytes']==32740 for c in manifest['chunks']), 'wrong_signed_whole_bundle')
    require(all(type(v[k]) is int and v[k]>0 for k in ('original_epoch_us','original_monotonic_us','expires_epoch_us','deadline_monotonic_us'))
        and 0<v['original_monotonic_us']<v['deadline_monotonic_us']
        <=v['original_monotonic_us']+v['expires_epoch_us']-v['original_epoch_us']
        and 0<v['expires_epoch_us']-v['original_epoch_us']<=(894000000 if kind==KIND else COMMITTED_CONTRACTS[kind][1]),'original_whole_window_changed')
    if kind in COMMITTED_CONTRACTS:
        whole=COMMITTED_CONTRACTS[kind][1]
        require(v['expires_epoch_us']-v['original_epoch_us']==whole
                and v['deadline_monotonic_us']-v['original_monotonic_us']==whole,'v2_original_whole_window_changed')
    require(type(v['stages']) is list and len(v['stages'])==5,'exact_five_bundle_stages_required')
    previous=v['original_epoch_us']; campaigns=set()
    for index,stage in enumerate(v['stages']):
        require(type(stage) is dict and set(stage)=={'index','campaign_id','issued_epoch_us','expires_epoch_us','handoff_end_epoch_us','response_sha256'}
            and type(stage['index']) is int and stage['index']==index,'bundle_part_order_changed')
        digest(stage['response_sha256'])
        identifier(stage['campaign_id']);require(stage['campaign_id'] != parent_id and stage['campaign_id'] not in campaigns,'bundle_campaign_reused')
        campaigns.add(stage['campaign_id'])
        a,z,h=(stage[k] for k in ('issued_epoch_us','expires_epoch_us','handoff_end_epoch_us'))
        require(all(type(n) is int for n in (a,z,h)) and previous<=a<z
            and z-a<=180000000 and z+10000000<=h<=v['expires_epoch_us'], 'bundle_stage_window_changed')
        if kind in COMMITTED_CONTRACTS:require(z-a==COMMITTED_CONTRACTS[kind][3],'v2_part_window_changed')
        previous=h
    return raw,v,manifest_raw,signature_raw,manifest,expected,kind


def public_context_commitment(value, *, parent_id):
    """Pure signed versioned schedule commitment; supplies no enrollment authority."""
    identifier(parent_id)
    raw,v,*_,kind=_context_data(value,parent_id=parent_id)
    require(kind in COMMITTED_CONTRACTS,'explicit_v2_capture_required')
    contract=COMMITTED_CONTRACTS[kind]
    return dict(schema=contract[4],parent_id=parent_id,qualification_kind=kind,context_schema=v['schema'],
        context_sha256=hashlib.sha256(raw).hexdigest(),
        **{k:v[k] for k in ('original_epoch_us','original_monotonic_us','expires_epoch_us','deadline_monotonic_us')},
        phase_start_epoch_us=v['original_epoch_us'],phase_end_epoch_us=v['original_epoch_us']+contract[2])


def validate_capture_commitment(owner, captured, policy):
    """Require the pre-enrollment commitment on every protected versioned open."""
    if policy.qualification_kind in COMMITTED_CONTRACTS:
        expected=public_context_commitment(owner['bundle_context'],parent_id=policy.parent_id)
        require(captured['schema']==COMMITTED_CONTRACTS[policy.qualification_kind][6]
            and captured['bundle_capture_commitment']==owner['bundle_capture_commitment']==expected
            and expected['context_sha256']==policy.bundle_context_sha256
            and (captured['phase_start_epoch_us'],captured['phase_end_epoch_us'])
                ==(expected['phase_start_epoch_us'],expected['phase_end_epoch_us'])
            and (policy.issued_epoch_us,policy.expires_epoch_us,policy.original_monotonic_us,policy.deadline_monotonic_us)
                ==tuple(expected[k] for k in ('original_epoch_us','expires_epoch_us','original_monotonic_us','deadline_monotonic_us')),
            'v2_capture_owner_policy_commitment_changed')
    else:
        require(captured.get('schema','meshticulum.private-resume-actual-capture.v1')=='meshticulum.private-resume-actual-capture.v1'
            and 'bundle_capture_commitment' not in captured and 'bundle_capture_commitment' not in owner
            and captured['phase_end_epoch_us']-captured['phase_start_epoch_us']<=899000000,
            'legacy_capture_window_changed')


class BundleContext:
    """Signed data + immutable schedule; protected owner supplies authority."""
    def __init__(self, value, policy):
        data=_context_data(value,parent_id=policy.parent_id)
        self.raw,self.value,self.manifest_raw,self.signature_raw,self.manifest,self.expected,kind=data
        self.policy=policy
        require(policy.qualification_kind==kind and hashlib.sha256(self.raw).hexdigest()==policy.bundle_context_sha256
                and tuple(self.value[k] for k in ('original_epoch_us','original_monotonic_us','expires_epoch_us','deadline_monotonic_us'))
                ==(policy.issued_epoch_us,policy.original_monotonic_us,policy.expires_epoch_us,policy.deadline_monotonic_us),
                'bundle_context_policy_changed')
        self._refs=(policy,self.raw,self.manifest_raw,self.signature_raw)
        self._policy_bytes=canonical(policy.to_dict())
        self._manifest_bytes=canonical(self.manifest);self._expected_bytes=canonical(self.expected)
        from .pilot_native_caps import _state,_state_comparator
        self._policy_comparison,self._value_comparison,self._manifest_comparison,self._expected_comparison=(
            _state_comparator(_state(item)) for item in (policy,self.value,self.manifest,self.expected))
        self._refs+=(self._policy_comparison,self._value_comparison,
                     self._manifest_comparison,self._expected_comparison,
                     self._policy_bytes,self._manifest_bytes,self._expected_bytes)
        refs=self._refs;self._captured_refs=lambda:refs
        self._last=None

    def _check(self):
        from .pilot_native_caps import _compare_state
        refs=self._captured_refs()
        require(self._refs is refs
                and all(a is b for a,b in zip((self.policy,self.raw,self.manifest_raw,self.signature_raw,
                    self._policy_comparison,self._value_comparison,self._manifest_comparison,self._expected_comparison,
                    self._policy_bytes,self._manifest_bytes,self._expected_bytes),refs))
                and _compare_state(self.policy,self._policy_comparison[0],refs[4])
                and _compare_state(self.value,self._value_comparison[0],refs[5])
                and _compare_state(self.manifest,self._manifest_comparison[0],refs[6])
                and _compare_state(self.expected,self._expected_comparison[0],refs[7]),
                'bundle_context_mutated')
        require(hashlib.sha256(self.raw).hexdigest()==self.policy.bundle_context_sha256,'bundle_context_policy_changed')
    def current(self, now, *, controller_clock=False):
        self._check()
        require(type(now) is tuple and len(now)==2 and all(type(n) is int and n>0 for n in now)
                and self.value['original_epoch_us']<=now[0]<self.value['expires_epoch_us'],'whole_bundle_expired')
        if controller_clock:
            require(self.value['original_monotonic_us']<=now[1]<self.value['deadline_monotonic_us']
                    and (self._last is None or all(a>=b for a,b in zip(now,self._last))), 'bundle_controller_clock_expired_or_regressed')
            self._last=now
        self._check();return True
    def monotonic_bounds(self,stage,anchor):
        self._check()
        require(type(anchor) is dict and all(type(anchor[k]) is int for k in ('original_epoch_us','original_monotonic_us','deadline_monotonic_us')),
                'original_bundle_clock_required')
        first=anchor['original_monotonic_us'];epoch=anchor['original_epoch_us'];end=anchor['deadline_monotonic_us']
        return (first+stage['issued_epoch_us']-epoch,min(end,first+stage['expires_epoch_us']-epoch),
                min(end,first+stage['handoff_end_epoch_us']-epoch))

    def stage(self,campaign,expiry):
        self._check()
        stages=[v for v in self.value['stages'] if v['campaign_id']==campaign]
        require(len(stages)==1 and type(expiry) is int and stages[0]['expires_epoch_us']==expiry,'unplanned_bundle_campaign')
        return stages[0]

    def exchange(self,index):
        self._check()
        require(type(index) is int and 0<=index<5,'unplanned_bundle_part')
        entry=self.expected['parts'][index]
        request=resource.encode_request('/packs/'+self.value['manifest_sha256']+'/chunks/'+entry['chunk_sha256']+'.json',0,32740,public32=True)
        return request,entry

    def package(self,package,epoch):
        self._check()
        require(type(epoch) is int,'invalid_bundle_stage_clock')
        stage=self.stage(package['campaign_id'],package['application_expires_epoch_us'])
        require(stage['issued_epoch_us']<=epoch<stage['expires_epoch_us']
                and package['resource_sha256']==self.value['manifest_sha256'] and package['transport']=='lora', 'bundle_stage_expired_or_rebound')
        request,entry=self.exchange(stage['index']);plan=package['grant'];fmt=plan['service']['format']
        expected=(len(request),hashlib.sha256(request).hexdigest()) if fmt=='MR1.request' else (32768,None)
        if fmt=='MR1.response':
            require(plan['object']['sha256']==stage['response_sha256'],'bundle_response_descriptor_changed')
        require(fmt in ('MR1.request','MR1.response') and package['unit'] in ('unit-a','unit-c')
                and plan['service']['public_response_max_bytes']==32768
                and plan['object']['bytes']==expected[0], 'bundle_package_changed')
        if fmt=='MR1.request':require(plan['object']['sha256']==expected[1],'bundle_request_selected_part_changed')
        return stage


class _NativeOwner:
    """Borrow the actual EX native owner; observer labels never supply authority."""
    def __init__(self,caps,clock):
        from .pilot_native_caps import NativeCaps,_Public32Approval
        from lantern_meshticulum.ip import node
        require(type(caps) is NativeCaps and type(caps._approve) is _Public32Approval
                and caps.policy.qualification_kind in BUNDLE_KINDS and clock is caps._clock,'protected_bundle_owner_required')
        self.caps,self.clock,self.facts_reader=caps,clock,node._facts
        self.context=caps._approve.bundle;require(type(self.context) is BundleContext,'protected_bundle_context_required')
        self.state=self._state_ref=caps.path.parent.parent.parent
        self.refs=(caps,caps.policy,caps._approve,self.context,clock,node._facts)
        self.policy_raw=canonical(caps.policy.to_dict());self.enrollment_raw=canonical(caps._enrollment)
        self.anchor={k:caps._enrollment[k] for k in ('original_epoch_us','original_monotonic_us','deadline_monotonic_us')}
        self._enrollment_ref=caps._enrollment;self._anchor_ref=self.anchor
        self._anchor_values=tuple(self.anchor.items())
        self.last=None;self.busy=False;self.check()
    def refs_current(self):
        from lantern_meshticulum.ip import node
        require(all(a is b for a,b in zip((self.caps,self.caps.policy,self.caps._approve,self.context,self.clock,node._facts),self.refs))
                and self.caps._approve.bundle is self.context and self.caps._approve.clock is self.clock
                and self.state is self._state_ref and self.state==self.caps.path.parent.parent.parent
                and self.facts_reader is self.refs[-1]
                and canonical(self.caps.policy.to_dict())==self.policy_raw
                and self.caps._enrollment is self._enrollment_ref
                and canonical(self.caps._enrollment)==self.enrollment_raw
                and self.anchor is self._anchor_ref and tuple(self.anchor.items())==self._anchor_values
                and self.anchor=={k:self.caps._enrollment[k] for k,_ in self._anchor_values},
                'native_bundle_owner_rebound')
        self.context._check()
    def check(self):
        require(not self.busy,'native_bundle_observer_reentered');self.busy=True
        try:
            self.refs_current();require(self.caps.public32_owner_current() is True,'bundle_owner_not_current')
            self.refs_current();observed=self.facts_reader(self.caps.unit.unit);self.refs_current()
            expected=json.loads(self.caps._approve._facts_bytes)
            require({k:observed[k] for k in ('unit','host_boot_id','services','service_lifetimes')}==expected
                    and observed['host_boot_id']==self.caps.unit.host_boot_id,'bundle_native_facts_changed')
            now=self.clock();self.refs_current();self.context.current(now)
            require(self.anchor['original_epoch_us']<=now[0] and self.anchor['original_monotonic_us']<=now[1]
                    <self.anchor['deadline_monotonic_us'] and (self.last is None or all(a>=b for a,b in zip(now,self.last))),
                    'native_whole_deadline_expired_or_regressed')
            self.last=now;return now
        finally:self.busy=False
    def boot(self):self.check();return self.caps.unit.host_boot_id
    def stage(self,campaign,expiry):
        now=self.check();stage=self.context.stage(campaign,expiry)
        first,end,_=self.context.monotonic_bounds(stage,self.anchor)
        require(stage['issued_epoch_us']<=now[0]<stage['expires_epoch_us'] and first<=now[1]<end,
                'native_original_stage_expired');return stage


class _HeldCapture:
    """Retain own prepared/run dirFD and every consumed file through publication."""
    def __init__(self,owner,run,*,sends,format):
        from .history_identity_seal import _Ancestry
        from .history_metadata_fence import _stamp
        from .pilot_resume_rpc import parse
        self.owner,self.run,self.files=owner,identifier(run),{}
        self.stack=ExitStack();self.stamp=_stamp;self.parse=parse
        owned=[];close_file=self._close_file
        def close_owned():
            while owned:
                fd,identity,token=owned.pop();close_file(fd,identity,token)
        self._register=owned.append;self._cleanup=close_owned
        self._refs=(owner,self.run,self.files,self.stack,_stamp,parse,self._register,self._cleanup)
        self._file_entries=();self._descriptor_tokens=();self._sealed=False
        try:
            owner.check();self.chain=_Ancestry(owner.state/'runs'/run,os.geteuid())
            self._chain_ref=self.chain;self._chain_lists=(self.chain.fds,self.chain.records)
            self._chain_records=tuple((fd,name,tuple(identity)) for fd,(name,identity) in zip(self.chain.fds,self.chain.records))
            for fd,_,identity in self._chain_records:self._retain_fd(fd,identity[:2])
            self.fd=self._fd_ref=self.chain.fds[-1];self.read('active',128)
            fcntl.flock(self.files['active'][0],fcntl.LOCK_SH|fcntl.LOCK_NB)
            self.plan=self.json('grant.json',16384);self.provision=self.json('provision.json',100000);self.result=self.json('result.json',100000)
            self.payload=self.read('sender.bin' if sends else 'receiver.bin',32768)
            caps=owner.caps;rows,_=caps._validate()
            matches=[(cid,json.loads(raw)) for cid,raw,sid in rows if sid==run];require(len(matches)==1,'bundle_capture_claim_missing')
            claim_id,claim=matches[0];meta=claim['child']['metadata'];resource_row=claim['resource']
            prepared=caps.db.execute('SELECT body FROM prepared WHERE id=?',(claim_id,)).fetchone()
            started=caps.db.execute('SELECT body FROM starts WHERE id=?',(claim_id,)).fetchone()
            require(prepared is not None and started is not None,'bundle_capture_not_prepared_and_started')
            receipt=json.loads(prepared[0]);start=json.loads(started[0])
            require(self.provision['parent']==receipt and self.result['parent']==start
                    and start==dict(receipt,schema='meshticulum.native-parent-start.v1',started=True)
                    and receipt['child_caps']==claim['child'] and receipt['native_claim_id']==claim_id,
                    'bundle_capture_ledger_rebound')
            require(self.provision['campaign_id']==meta['campaign_id'] and self.provision['unit']==meta['unit']==caps.unit.unit
                    and self.provision['run_id']==run and self.provision['transport']=='lora'
                    and self.provision['object']==self.plan['object']
                    and {k:self.plan['object'][k] for k in ('bytes','sha256')}==meta['object'] and self.result['schema']=='meshticulum.physical-service-run.v1'
                    and self.result['unit']==meta['unit'] and self.result['host_boot_id']==caps.unit.host_boot_id
                    and self.result['session_id']==run and self.result['sends'] is sends and self.result['service_format']==format
                    and self.result['complete'] is True and self.result['disarmed_on_exit'] is True
                    and self.result['original_application_expires_epoch_us']==meta['application_expires_epoch_us']
                    and type(self.result['original_application_deadline_us']) is int
                    and 0<self.result['original_application_deadline_us']<=receipt['application_deadline_monotonic_us'],
                    'bundle_capture_result_rebound')
            require(hashlib.sha256(canonical(self.plan)).hexdigest()==receipt['native_plan_sha256']
                    and hashlib.sha256(self.payload).hexdigest()==meta['object']['sha256'] and len(self.payload)==meta['object']['bytes']
                    and resource_row['campaign_id']==meta['campaign_id'] and resource_row['bundle_context_sha256']==caps.policy.bundle_context_sha256,
                    'bundle_capture_plan_or_bytes_changed')
            if not sends:require(self.result['service_object_durable'] is True and self.result['service_object_sha256']==meta['object']['sha256'],
                                 'bundle_capture_not_durable')
            self.meta=meta;self.receipt=receipt;self.resource=resource_row
            self._deadline_inputs=(resource_row['application_deadline_monotonic_us'],self.result['original_application_deadline_us'])
            self.original_deadline=min(self._deadline_inputs)
            self._parsed_refs=(self.meta,self.plan,self.provision,self.result,self.receipt,self.resource)
            self._parsed_bytes=canonical(self._parsed_refs);self._payload_ref=self.payload;self._sealed=True
            self.check()
        except BaseException:self.close();raise
    def read(self,name,limit):
        self.check_files()
        require(not self._sealed or name in self.files,'unplanned_bundle_capture_read')
        if name not in self.files:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=self.fd)
            s=os.fstat(fd)
            self._retain_fd(fd,(s.st_dev,s.st_ino))
            require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o600
                    and s.st_nlink==1 and 0<s.st_size<=limit,'unsafe_bundle_capture_file')
            raw=os.pread(fd,limit+1,0);require(len(raw)==s.st_size,'short_bundle_capture_read')
            self.files[name]=(fd,self.stamp(s),raw)
            self._file_entries=tuple(self.files.items())
        self.check_files();return self.files[name][2]
    @staticmethod
    def _close_file(fd,identity,token):
        try:
            info=os.fstat(fd)
            if (info.st_dev,info.st_ino)==identity and os.lseek(fd,0,os.SEEK_CUR)==token:os.close(fd)
        except OSError:pass
    def _retain_fd(self,fd,identity):
        # Match the protected Reader's open-description continuity token. All
        # consumed bytes use pread, so ordinary same-inode reopening at offset0
        # cannot inherit the retained description/lock or its cleanup rights.
        token=16*1024*1024+fd+1
        os.lseek(fd,token,os.SEEK_SET);self._register((fd,identity,token))
        self._descriptor_tokens+=((fd,token),)
    def json(self,name,limit):return self.parse(self.read(name,limit))
    def check_files(self):
        require(all(a is b for a,b in zip((self.owner,self.run,self.files,self.stack,self.stamp,self.parse,self._register,self._cleanup),self._refs))
                and self.chain is self._chain_ref and self.fd==self._fd_ref
                and self.chain.fds is self._chain_lists[0] and self.chain.records is self._chain_lists[1]
                and tuple((fd,name,tuple(identity)) for fd,(name,identity) in zip(self.chain.fds,self.chain.records))==self._chain_records
                and len(self.chain.fds)==len(self.chain.records)==len(self._chain_records)
                and len(self.files)==len(self._file_entries)
                and all(self.files.get(name) is entry for name,entry in self._file_entries),
                'bundle_capture_owner_or_files_rebound')
        require(all(os.lseek(fd,0,os.SEEK_CUR)==token for fd,token in self._descriptor_tokens),
                'bundle_capture_descriptor_rebound')
        self.chain.check()
        if hasattr(self,'_parsed_bytes'):
            parsed=(self.meta,self.plan,self.provision,self.result,self.receipt,self.resource)
            require(all(a is b for a,b in zip(parsed,self._parsed_refs)) and canonical(parsed)==self._parsed_bytes
                    and self.payload is self._payload_ref and type(self.original_deadline) is int
                    and self._deadline_inputs==(self.resource['application_deadline_monotonic_us'],self.result['original_application_deadline_us'])
                    and self.original_deadline==min(self._deadline_inputs),'bundle_capture_parsed_state_changed')
        for name,(fd,stamp,raw) in self._file_entries:
            require(self.stamp(os.fstat(fd))==stamp==self.stamp(os.stat(name,dir_fd=self.fd,follow_symlinks=False))
                    and os.pread(fd,len(raw)+1,0)==raw,'bundle_capture_file_rebound')
        if 'active' in self.files:require(self.files['active'][2]==b'active\n','bundle_capture_retired')
    def check(self):
        self.check_files()
        now=self.owner.check();self.check_files()
        if hasattr(self,'meta'):
            self.owner.stage(self.meta['campaign_id'],self.meta['application_expires_epoch_us'])
            require(now[1]<self.original_deadline,'bundle_capture_original_deadline_expired')
    def close(self):self._refs[7]()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()


class _CaptureScope:
    def __init__(self,owner,captures):
        require(type(captures) is tuple and all(type(c) is _HeldCapture and c.owner is owner for c in captures),'invalid_native_capture_scope')
        self.owner,self.captures=owner,captures;self.refs=(owner,captures)
    def check(self):
        require(self.owner is self.refs[0] and self.captures is self.refs[1],'native_capture_scope_rebound')
        for capture in self.captures:capture.check_files()
        now=self.owner.check()
        for capture in self.captures:capture.check_files();require(now[1]<capture.original_deadline,'native_capture_scope_expired')
        return now
    def boot(self):self.check();return self.owner.caps.unit.host_boot_id


def _receiver(owner,*,create,captures=()):
    """Private data below existing campaigns, leaving strict public-cache names unchanged."""
    from .history_identity_seal import _Ancestry
    from .history_metadata_fence import _stamp
    require(not create or len(captures)==2,'native_part_requires_both_held_captures')
    scope=_CaptureScope(owner,captures)
    c=owner.context;caps=owner.caps;root=owner.state/'campaigns'
    chain=_Ancestry(root,os.geteuid());opened=[]
    try:
        parent=chain.fds[-1]
        for name in (caps.policy.parent_id,'bundle'):
            try:fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            except FileNotFoundError:
                if not create:return None
                owner.check();chain.check();os.mkdir(name,0o700,dir_fd=parent);os.fsync(parent)
                fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            s=os.fstat(fd);require(s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,'unsafe_bundle_data_directory')
            opened.append((fd,parent,name,_stamp(s)[:4]));parent=fd
        def current():
            scope.check();chain.check()
            for fd,p,name,identity in opened:require(_stamp(os.fstat(fd))[:4]==identity==_stamp(os.stat(name,dir_fd=p,follow_symlinks=False))[:4],
                                                    'bundle_data_directory_rebound')
        current()
        original=dict(schema=bundle.CONTEXT,manifest_sha256=c.value['manifest_sha256'],bundle_sha256=c.value['whole_sha256'],
            bundle_bytes=100000,host_boot_id=caps.unit.host_boot_id,expires_epoch_us=c.value['expires_epoch_us'],**owner.anchor)
        raw=canonical(original)
        try:context_fd=os.open('context.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
        except FileNotFoundError:
            require(create and not os.listdir(parent),'missing_or_unverified_bundle_context')
            context_fd=os.open('context.json',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
            try:
                view=memoryview(raw)
                while view:n=os.write(context_fd,view);require(n>0,'short_native_bundle_context_write');view=view[n:]
                os.fsync(context_fd);current();os.mkdir('parts',0o700,dir_fd=parent);os.fsync(parent)
            except BaseException:os.close(context_fd);raise
        try:
            s=os.fstat(context_fd);require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1
                and stat.S_IMODE(s.st_mode)==0o600 and os.pread(context_fd,2049,0)==raw,'native_bundle_original_context_changed')
        finally:os.close(context_fd)
        current();receiver=bundle.Receiver(root/caps.policy.parent_id/'bundle',c.manifest_raw,c.signature_raw,c.value['trust'],
            c.value['manifest_sha256'],expires_epoch_us=c.value['expires_epoch_us'],clock=scope.check,boot_reader=scope.boot)
        try:current();require(receiver.context==original,'receiver_native_original_clock_changed')
        except BaseException:receiver.close();raise
        return receiver
    finally:
        for fd,_,_,_ in reversed(opened):os.close(fd)
        chain.close()


def accept_native_part(caps, *, state, facts, campaign_id, request_run_id, response_run_id, verified, clock):
    """Read genuine started captures locally; a caller verification DTO is not authority."""
    owner=_NativeOwner(caps,clock);require(owner.state==Path(state).absolute() and facts['unit']=='unit-a'
        and facts['host_boot_id']==caps.unit.host_boot_id,'bundle_native_state_changed')
    context=owner.context
    with _HeldCapture(owner,request_run_id,sends=True,format='MR1.request') as sent, _HeldCapture(owner,response_run_id,sends=False,format='MR1.response') as captured:
        stage=owner.stage(campaign_id,sent.meta['application_expires_epoch_us']);request,entry=context.exchange(stage['index'])
        require(sent.payload==request and sent.meta['campaign_id']==captured.meta['campaign_id']==campaign_id
                and captured.meta['application_expires_epoch_us']==stage['expires_epoch_us']
                and captured.meta['source_run_id'] is None
                and hashlib.sha256(captured.payload).hexdigest()==stage['response_sha256'],'bundle_native_part_rebound')
        response=captured.payload;magic,binding,status,total,offset=resource.RESPONSE.unpack(response[:resource.RESPONSE.size])
        require((magic,binding,status,total,offset)==(resource.MAGIC,hashlib.sha256(request).digest()[:16],0,32740,0),'bundle_capture_request_changed')
        require(verified['schema']=='meshticulum.pilot-verified-piece.v1' and verified['publisher_verified'] is True
                and verified['campaign_id']==campaign_id and verified['response_run_id']==response_run_id
                and verified['manifest_sha256']==context.value['manifest_sha256'] and verified['chunk_sha256']==entry['chunk_sha256'],
                'bundle_verification_receipt_rebound')
        sent.check();captured.check()
        with _receiver(owner,create=True,captures=(sent,captured)) as receiver:
            name=entry['chunk_sha256']+'.json';reused=name in os.listdir(receiver.parts_fd)
            if reused:
                previous,_=receiver._part(stage['index']);require(previous==response[resource.RESPONSE.size:],'resumed_bundle_part_changed')
            else:receiver.accept(stage['index'],response[resource.RESPONSE.size:])
            sent.check();captured.check();owner.check()
            return dict(bundle_context_sha256=caps.policy.bundle_context_sha256,part_index=stage['index'],useful_decoded_bytes=entry['raw_bytes'],
                reused_verified_part=reused,whole_bytes=100000,whole_sha256=context.value['whole_sha256'],
                original_whole_expires_epoch_us=context.value['expires_epoch_us'],original_whole_deadline_monotonic_us=owner.anchor['deadline_monotonic_us'],
                native_bundle_complete=False,native_transport_qualified=False)


def finish_native_bundle(caps, *, state, clock):
    owner=_NativeOwner(caps,clock);require(owner.state==Path(state).absolute() and caps.unit.unit=='unit-a','bundle_native_state_changed')
    receiver=_receiver(owner,create=False);require(receiver is not None,'native_bundle_missing')
    with receiver:
        owner.check();value=receiver.finish();owner.check()
        native_keys=('original_start_epoch_us','original_start_monotonic_us','original_deadline_monotonic_us',
                     'verified_epoch_us','verified_monotonic_us')
        value['native_receiver_timeline']={key:value.pop(key) for key in native_keys}
        # Controller capture/setup precedes native enrollment. Its original
        # signed clock is the start of the whole campaign; native monotonic
        # verification remains separately labeled and cannot be subtracted.
        value.update(original_start_epoch_us=owner.context.value['original_epoch_us'],
            original_start_monotonic_us=owner.context.value['original_monotonic_us'],
            original_deadline_monotonic_us=owner.context.value['deadline_monotonic_us'],
            whole_complete=True,bundle_context_sha256=caps.policy.bundle_context_sha256,native_transport_qualified=False)
        return value


def context_value(manifest_raw,signature_raw,trust,manifest_sha256,*,chunks,policy_clock,stages,qualification_kind=KIND):
    """Prepare signed-data pins for Root review; creates no approval or records."""
    manifest=ll.manifest(manifest_raw,signature_raw,trust,manifest_sha256);expected=bundle.descriptor(manifest)
    require(expected['bytes']==100000 and len(expected['parts'])==5 and type(stages) is list and len(stages)==5,
            'only100k_native_bundle_prepared')
    rows=[]
    for index,stage in enumerate(stages):
        entry=expected['parts'][index];raw=chunks[entry['chunk_sha256']]
        ll.chunk(raw,manifest['chunks'][index]);require(len(raw)==32740,'bundle_requires_exact32_records')
        request=resource.encode_request('/packs/'+manifest_sha256+'/chunks/'+entry['chunk_sha256']+'.json',0,32740,public32=True)
        response=resource.RESPONSE.pack(resource.MAGIC,hashlib.sha256(request).digest()[:16],0,len(raw),0)+raw
        rows.append(dict(stage,index=index,response_sha256=hashlib.sha256(response).hexdigest()))
    issued,mono,expires,deadline=policy_clock
    require(qualification_kind in BUNDLE_KINDS,'unknown_bundle_contract')
    return dict(schema=SCHEMA if qualification_kind==KIND else COMMITTED_CONTRACTS[qualification_kind][0],manifest_base64=base64.b64encode(manifest_raw).decode(),
        signature_base64=base64.b64encode(signature_raw).decode(),trust=trust,manifest_sha256=manifest_sha256,
        whole_bytes=expected['bytes'],whole_sha256=expected['sha256'],original_epoch_us=issued,
        original_monotonic_us=mono,expires_epoch_us=expires,deadline_monotonic_us=deadline,stages=rows)


def native_action(unit,parent_id,action):
    """Fixed native-state metadata entry; caller supplies no policy or body."""
    from .pilot_native_caps import _open_existing
    from lantern_meshticulum.ip.node import _facts,clock
    from meshticulum.ip.journal import private
    require(unit=='unit-a' and action in ('status','finish'),'invalid_bundle_native_action')
    state=Path('/var/lib/meshticulum-bench');facts=_facts(unit)
    caps=_open_existing(state,identifier(parent_id),facts,clock)
    try:
        require(caps.policy.qualification_kind in BUNDLE_KINDS and caps.public32_owner_current() is True,'bundle_parent_not_current')
        owner=_NativeOwner(caps,clock);c=owner.context;indices=[]
        if action=='finish':result=finish_native_bundle(caps,state=state,clock=clock)
        else:
            receiver=_receiver(owner,create=False)
            if receiver is None:result=dict(verified_parts=[],all_parts_present=False,whole_complete=False)
            else:
                with receiver:
                    names=set(os.listdir(receiver.parts_fd))
                    for i,e in enumerate(c.expected['parts']):
                        if e['chunk_sha256']+'.json' in names:receiver._live();receiver._part(i);indices.append(i)
                    owner.check();result=dict(verified_parts=indices,all_parts_present=len(indices)==5,whole_complete=False)
        caps.current()
        result.update(schema='meshticulum.native-public100k-bundle-'+action+'.v1',unit=unit,parent_id=parent_id,
            bundle_context_sha256=caps.policy.bundle_context_sha256,manifest_sha256=c.value['manifest_sha256'],
            whole_file_sha256=c.value['whole_sha256'],whole_bytes=100000,original_whole_expires_epoch_us=c.value['expires_epoch_us'],
            radio_opened=False,native_transport_qualified=False,global_debt_complete=False,refunds=0)
        return result
    finally:require(caps.close(),'bundle_owner_close_unproved')


class BundleCampaign:
    """Existing Controller stages under one original reviewed whole context.

    Root must supply the genuine enrolled parent and the existing owned operator.
    No owner records, keys, firmware, enrollment, grants or source pins are made.
    Metadata is requested from the fixed native entry; chunk bodies stay native.
    """
    def __init__(self,operator,parent,context,*,peers,boots,handoff_deadline_monotonic,clock,epoch,sleep,lora_sender_first=False):
        from .pilot_parent_caps import ParentCaps
        require(type(lora_sender_first) is bool,'invalid_bundle_startup_order')
        self.lora_sender_first=self._lora_sender_first=lora_sender_first
        require(type(parent) is ParentCaps and parent.current() is True,'existing_bundle_parent_required')
        self.context=self._context_ref=BundleContext(context,parent.policy)
        self.operator,self.parent,self.peers,self.boots=operator,parent,peers,boots
        self.clock,self.epoch,self.sleep=clock,epoch,sleep
        self.handoff=handoff_deadline_monotonic;self._refs=(operator,parent,clock,epoch,sleep)
        require(handoff_deadline_monotonic<=parent.policy.deadline_monotonic_us/1000000+5,'whole_handoff_extended')
        self.links=[];self.parts=[];self.controllers=[]
    def current(self):
        require(self.lora_sender_first is self._lora_sender_first,'bundle_startup_order_changed')
        require(self.context is self._context_ref and self.context.policy is self.parent.policy,'bundle_context_rebound')
        require(all(a is b for a,b in zip((self.operator,self.parent,self.clock,self.epoch,self.sleep),self._refs))
                and self.parent.current() is True,'bundle_owner_rebound')
        self.context.current((self.epoch(),int(self.clock()*1000000)),controller_clock=True)
    def status(self):
        self.current();v=self.operator.bundle_status('unit-a',self.parent.policy.parent_id);self.current()
        require(v['schema']=='meshticulum.native-public100k-bundle-status.v1' and v['unit']=='unit-a'
                and v['parent_id']==self.parent.policy.parent_id and v['bundle_context_sha256']==self.parent.policy.bundle_context_sha256
                and v['manifest_sha256']==self.context.value['manifest_sha256']
                and v['whole_file_sha256']==self.context.value['whole_sha256'] and v['whole_bytes']==100000
                and v['original_whole_expires_epoch_us']==self.context.value['expires_epoch_us']
                and v['all_parts_present'] is (len(v['verified_parts'])==5) and v['whole_complete'] is False
                and type(v['verified_parts']) is list and v['verified_parts']==sorted(set(v['verified_parts']))
                and all(type(i) is int and 0<=i<5 for i in v['verified_parts']),'native_bundle_status_rebound')
        return v
    def transfer_part(self,index,response):
        from .pilot_controller import Controller
        self.current();status=self.status()
        request,entry=self.context.exchange(index);stage=self.context.value['stages'][index]
        if index in status['verified_parts']:return dict(index=index,reused_verified=True,new_credit=False)
        require(self.epoch()>=stage['issued_epoch_us'] and self.epoch()<stage['expires_epoch_us']
                and len(response)==32768 and hashlib.sha256(response).hexdigest()==stage['response_sha256'], 'bundle_part_stage_not_current')
        anchor=dict(original_epoch_us=self.parent.policy.issued_epoch_us,original_monotonic_us=self.parent.policy.original_monotonic_us,
            deadline_monotonic_us=self.parent.policy.deadline_monotonic_us)
        _,_,original_handoff=self.context.monotonic_bounds(stage,anchor)
        stage_handoff=min(self.handoff,original_handoff/1000000)
        controller=Controller(self.operator,campaign_id=stage['campaign_id'],resource_sha256=self.context.value['manifest_sha256'],
            expires_epoch_us=stage['expires_epoch_us'],handoff_deadline_monotonic=stage_handoff,peers=self.peers,boots=self.boots,parent=self.parent,
            radio_permission={'country':'MY','antenna_confirmed':True},clock=self.clock,epoch=self.epoch,sleep=self.sleep,
            public32=True,lora_sender_first=self.lora_sender_first,bundle_context=self.context.value,
            radio_profile=GFSK_NAME if self.parent.policy.qualification_kind==GFSK_BUNDLE_KIND else 'sf7-500')
        self.controllers.append(controller)
        try:
            verified=controller.direct_piece(request,response,'lora',publisher='unit-c',requester='unit-a')
            part=verified.get('bundle_part')
            require(type(part) is dict and part['part_index']==index and part['useful_decoded_bytes']==entry['raw_bytes']
                    and part['bundle_context_sha256']==self.parent.policy.bundle_context_sha256,'native_bundle_part_accept_missing')
            self.current();require(index in self.status()['verified_parts'],'native_bundle_part_missing')
            self.parts.append(verified);return verified
        finally:
            cleanup=controller.retire();self.links.extend(controller.links)
            if self.parent.policy.qualification_kind==GFSK_BUNDLE_KIND:
                require(all('stop_error' not in row and 'retirement_error' not in row for row in cleanup),'gfsk_bundle_retirement_unproved')
                for link in controller.links:
                    expected=set(link['attempted'])|set(link['prepared'])
                    require(set(link['retired'])==expected,'gfsk_bundle_retirement_unproved')
                    for unit in expected:
                        matches=[r for r in cleanup if r.get('unit')==unit and r.get('run_id')==link['run_id']]
                        require(matches and all(r.get('retirement',{}).get('retired') is True for r in matches),
                                'gfsk_bundle_retirement_unproved')
    def retire(self):
        evidence=[]
        for controller in self.controllers:evidence.extend(controller.retire())
        return evidence
    def finish(self):
        self.current();require(self.status()['verified_parts']==list(range(5)),'whole_bundle_parts_missing')
        value=self.operator.bundle_finish('unit-a',self.parent.policy.parent_id);self.current()
        require(value['schema']=='meshticulum.native-public100k-bundle-finish.v1' and value['complete'] is True and value['whole_complete'] is True
                and value['bundle_context_sha256']==self.parent.policy.bundle_context_sha256
                and value['whole_file_sha256']==self.context.value['whole_sha256'] and value['useful_decoded_bytes']==100000
                and value['original_start_epoch_us']==self.context.value['original_epoch_us']
                and value['original_start_monotonic_us']==self.context.value['original_monotonic_us']
                and value['original_deadline_monotonic_us']==self.context.value['deadline_monotonic_us'],
                'native_whole_bundle_not_verified')
        return value
