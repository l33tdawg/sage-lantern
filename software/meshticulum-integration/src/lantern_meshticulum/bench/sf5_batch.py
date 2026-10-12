# SPDX-License-Identifier: Apache-2.0
"""Exact opt-in three-resource SF5 public batch; no radio or enrollment entry."""
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import re
import threading

from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy, Cost, canonical
from .profiles import profile_for

KIND = 'c-bulk-8k-batch-v1'
SCHEMA = 'meshticulum.finite-public-sf5-batch.v1'
COUNT = 3
HEX = re.compile(r'[0-9a-f]{64}\Z')
SID = re.compile(r'[0-9a-f]{32}\Z')


def validate_descriptor(descriptor):
    require(type(descriptor) is dict and set(descriptor) == {
        'schema','sample_count','phase_seconds','application_seconds','publisher','requester',
        'radio_profile_name','prepared_endpoint_limit','samples'}, 'batch_descriptor_shape')
    require(descriptor['schema']==SCHEMA and type(descriptor['sample_count']) is int
        and descriptor['sample_count']==COUNT and descriptor['application_seconds']==180
        and type(descriptor['phase_seconds']) is int and 600<=descriptor['phase_seconds']<=899
        and descriptor['publisher']=='unit-c' and descriptor['requester']=='unit-a'
        and descriptor['radio_profile_name']=='sf5-500'
        and type(descriptor['prepared_endpoint_limit']) is int and descriptor['prepared_endpoint_limit']==12,
        'batch_original_scope_changed')
    rows=descriptor['samples'];require(type(rows) is list and len(rows)==COUNT,'batch_samples_changed')
    for index,row in enumerate(rows):
        require(type(row) is dict and set(row)=={'index','campaign_id','resource_sha256','chunk_sha256','request','response'}
                and type(row['index']) is int and row['index']==index
                and type(row['campaign_id']) is str and SID.fullmatch(row['campaign_id'])
                and all(type(row[k]) is str and HEX.fullmatch(row[k]) for k in ('resource_sha256','chunk_sha256')),
                'batch_sample_binding_changed')
        for name,size in (('request',74),('response',8192)):
            value=row[name]
            require(type(value) is dict and set(value)=={'bytes','sha256'} and type(value['bytes']) is int
                    and value['bytes']==size and type(value['sha256']) is str and HEX.fullmatch(value['sha256']),
                    'batch_sample_object_changed')
    require(len({r['campaign_id'] for r in rows})==len({r['resource_sha256'] for r in rows})
            ==len({r['chunk_sha256'] for r in rows})==COUNT,'batch_resource_reuse_refused')
    return True


class BatchContext:
    """Original descriptor and policy remain immutable through all samples."""
    def __init__(self, descriptor, policy, *, phase_end_epoch_us):
        require(type(policy) is ParentPolicy and policy.qualification_kind==KIND,
                'batch_parent_policy_changed')
        require(type(descriptor) is dict and set(descriptor) == {
            'schema','sample_count','phase_seconds','application_seconds','publisher','requester',
            'radio_profile_name','prepared_endpoint_limit','samples'}, 'batch_descriptor_shape')
        require(descriptor['schema']==SCHEMA and type(descriptor['sample_count']) is int
            and descriptor['sample_count']==COUNT and descriptor['application_seconds']==180
            and type(descriptor['phase_seconds']) is int and 600<=descriptor['phase_seconds']<=899
            and descriptor['publisher']=='unit-c' and descriptor['requester']=='unit-a'
            and descriptor['radio_profile_name']=='sf5-500'
            and type(descriptor['prepared_endpoint_limit']) is int and descriptor['prepared_endpoint_limit']==12,
            'batch_original_scope_changed')
        require(type(phase_end_epoch_us) is int and phase_end_epoch_us-policy.issued_epoch_us
                ==descriptor['phase_seconds']*1000000 and policy.expires_epoch_us==phase_end_epoch_us-5000000
                and policy.deadline_monotonic_us-policy.original_monotonic_us==descriptor['phase_seconds']*1000000-5000000,
                'batch_original_phase_changed')
        rows=descriptor['samples'];require(type(rows) is list and len(rows)==COUNT,'batch_samples_changed')
        for index,row in enumerate(rows):
            require(type(row) is dict and set(row)=={'index','campaign_id','resource_sha256','chunk_sha256','request','response'}
                    and type(row['index']) is int and row['index']==index
                    and type(row['campaign_id']) is str and SID.fullmatch(row['campaign_id'])
                    and all(type(row[k]) is str and HEX.fullmatch(row[k]) for k in ('resource_sha256','chunk_sha256')),
                    'batch_sample_binding_changed')
            for name,size in (('request',74),('response',8192)):
                value=row[name]
                require(type(value) is dict and set(value)=={'bytes','sha256'} and type(value['bytes']) is int
                        and value['bytes']==size and type(value['sha256']) is str and HEX.fullmatch(value['sha256']),
                        'batch_sample_object_changed')
        require(len({r['campaign_id'] for r in rows})==len({r['resource_sha256'] for r in rows})
                ==len({r['chunk_sha256'] for r in rows})==COUNT,'batch_resource_reuse_refused')
        self.descriptor,self.policy,self.phase_end_epoch_us=descriptor,policy,phase_end_epoch_us
        self._refs=(descriptor,policy,phase_end_epoch_us)
        self._descriptor_bytes=canonical(descriptor);self._policy_bytes=canonical(policy.to_dict())
        self._pid,self._thread=os.getpid(),threading.get_ident()
        self.check()

    def check(self):
        require((os.getpid(),threading.get_ident())==(self._pid,self._thread)
                and self.descriptor is self._refs[0] and self.policy is self._refs[1]
                and self.phase_end_epoch_us==self._refs[2]
                and canonical(self.descriptor)==self._descriptor_bytes
                and canonical(self.policy.to_dict())==self._policy_bytes,'batch_context_rebound')
        return True

    @classmethod
    def from_owner(cls, owner, policy):
        require(type(owner) is dict and owner.get('qualification_kind')==KIND
                and owner.get('radio_profile_name')=='sf5-500'
                and owner['rf_profile']==asdict(profile_for('sf5-500')),
                'batch_owner_profile_changed')
        value=cls(owner['sf5_batch'],policy,phase_end_epoch_us=owner['phase_end_epoch_us'])
        require(owner['phase_start_epoch_us']==policy.issued_epoch_us,'batch_owner_original_clock_changed')
        radio=Cost(256,60000000)
        for unit in policy.units:
            extra=Cost() if unit.unit=='unit-b' else Cost(radio.radio_attempts*6,radio.estimated_airtime_us*6)
            require(unit.limits==unit.prior_debt.plus(extra),'batch_full_prepaid_partition_changed')
        return value

    def metadata(self, metadata):
        self.check()
        require(type(metadata) is dict and metadata.get('unit') in ('unit-a','unit-c')
                and metadata.get('transport')=='lora' and metadata.get('format') in ('MR1.request','MR1.response'),
                'batch_child_route_changed')
        matches=[r for r in self.descriptor['samples'] if r['campaign_id']==metadata['campaign_id']
                 and r['resource_sha256']==metadata['resource_sha256']]
        require(len(matches)==1,'batch_undeclared_resource')
        row=matches[0];request=metadata['format']=='MR1.request'
        require((metadata['sender'],metadata['receiver'])==(('unit-a','unit-c') if request else ('unit-c','unit-a')),
                'batch_child_direction_changed')
        expected=row['request' if request else 'response']
        require(metadata['object']==expected,'batch_child_object_changed')
        profile=dict(metadata['profile']);lease=profile.pop('lease_ms');wanted=asdict(profile_for('sf5-500'));wanted.pop('lease_ms')
        require(profile==wanted and type(lease) is int and 10000<=lease<=90000,'batch_child_profile_changed')
        self.check();return row

    def package(self, package):
        self.check();p=package['grant'];fmt=p['service']['format'];request=fmt=='MR1.request'
        row=self.metadata(dict(unit=package['unit'],transport=package['transport'],format=fmt,
            campaign_id=package['campaign_id'],resource_sha256=package['resource_sha256'],
            sender=p['sender_unit'],receiver='unit-c' if request else 'unit-a',
            object={k:p['object'][k] for k in ('bytes','sha256')},profile=p['profile']))
        require(package['application_expires_epoch_us']==p['service']['application_expires_epoch_us']==p['expires_epoch_us']
                and self.policy.issued_epoch_us<=p['issued_epoch_us']<p['expires_epoch_us']<=self.policy.expires_epoch_us
                and 0<p['expires_epoch_us']-p['issued_epoch_us']<=181000000,
                'batch_resource_original_window_changed')
        return row


class BatchApproval:
    """Held root-owned opt-in records for genuine NativeCaps worker/provision paths.

    Descriptor enforcement adds no radio entry. The ordinary single sample path
    remains unchanged. Every approval rechecks owner/prior/policy/capture bytes,
    original phase clocks and the authentic enrollment history binding.
    """
    def __init__(self, directory, policy, unit, facts, clock):
        from .pilot_resume_rpc import Reader,parse,sha,stable
        self.reader=None;self._closed=False;self._pid,self._thread=os.getpid(),threading.get_ident()
        self.policy,self.unit,self.facts,self.clock=policy,unit,facts,clock
        try:
            self.reader=Reader();raw=self.reader.read(directory/'owner.json',200000);owner=parse(raw)
            self.context=BatchContext.from_owner(owner,policy)
            local=policy.unit(unit);prior_raw=self.reader.read(directory/'prior.json',200000);prior=parse(prior_raw)
            require(self.reader.read(directory/'policy.json',20000)==canonical(policy.to_dict()),'batch_protected_policy_changed')
            binding_raw=self.reader.read(directory/'history-binding.json',16384);binding=parse(binding_raw)
            captured_raw=self.reader.read(directory/'capture.json',2000000);captured=parse(captured_raw)
            require(sha(binding_raw)==owner['history_binding_sha256']==prior['history_binding_sha256']
                and sha(prior_raw)==local.prior_snapshot_sha256
                and sha(canonical(owner['prior_registry']))==policy.prior_snapshot_sha256
                and owner['prior_registry'][unit]['observation_sha256']==sha(prior_raw)
                and binding['schema']=='meshticulum.private-resume-history-binding.v2'
                and binding['parent_id']==policy.parent_id and binding['unit']==unit
                and binding['parent_identity']==self.reader.directories[directory]
                and sha(self.reader.read(directory/'history-identity-seal.json',2000000))==binding['strict_sha256']
                and sha(self.reader.read(directory/'legacy-grant-seal.json',2000000))==binding['legacy_sha256']
                and captured['parent_id']==policy.parent_id and captured['unit']==unit
                and captured['review_sha256']==owner['review_sha256']
                and prior['full_caps']==captured['history']['previous'][-1]['paid_caps']==asdict(local.prior_debt)
                and prior['legacy_inventory']==captured['history']['legacy_inventory']
                and prior['complete_global_debt'] is False and prior['actual_cost'] is None and prior['refunds']==0
                and captured['actual_history_verified'] is True and sha(captured_raw)==owner['capture_record_sha256']
                and captured['history_binding_sha256']==owner['history_binding_sha256']
                and captured['phase_start_epoch_us']==owner['phase_start_epoch_us']
                and captured['phase_end_epoch_us']==owner['phase_end_epoch_us']
                and captured['native_phase']==owner['native_phase'][unit]
                and captured['facts']==owner['facts'][unit]==stable(facts)
                and facts['host_boot_id']==local.host_boot_id,'batch_protected_owner_changed')
            auth=owner['authorization'];rf=owner['rf_record']
            require(auth['human_statement']=='run it bro' and type(auth['count_authorized']) is int and auth['count_authorized']==1
                and auth['public_only'] is True and auth['private_production'] is False
                and owner['rf_record_sha256']=='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9'
                and rf['country']=='MY' and rf['C_transmission_allowed'] is True and rf['A_B_transmission_allowed'] is True
                and all(rf['units'][u]['antenna_connected'] is True for u in ('unit-a','unit-b','unit-c')),
                'batch_original_consent_changed')
            self._history=owner['history_binding_sha256'];self._start,self._end=owner['phase_start_epoch_us'],owner['phase_end_epoch_us']
            phase=owner['native_phase'][unit];self._phase=(phase['epoch_us'],phase['monotonic_us'],phase['deadline_monotonic_us'])
            self._facts_bytes=canonical(stable(facts));self._policy_bytes=canonical(policy.to_dict());self._last=None
            self._refs=(self.policy,self.unit,self.facts,self.clock,self.reader,self.context,self._phase,
                self._history,self._start,self._end,self._facts_bytes,self._policy_bytes)
            refs=self._refs;self._captured_refs=lambda:refs
            self.check()
        except BaseException:self.close();raise

    def check(self):
        from .pilot_resume_rpc import stable
        require(not self._closed and (os.getpid(),threading.get_ident())==(self._pid,self._thread)
                and self._refs is self._captured_refs()
                and all(a is b for a,b in zip((self.policy,self.unit,self.facts,self.clock,self.reader,self.context,self._phase,
                    self._history,self._start,self._end,self._facts_bytes,self._policy_bytes),self._refs))
                and canonical(self.policy.to_dict())==self._policy_bytes and canonical(stable(self.facts))==self._facts_bytes,
                'batch_approval_rebound')
        self.context.check();self.reader.check();now=self.clock()
        require(type(now) is tuple and len(now)==2 and all(type(v) is int for v in now)
                and self._start<=now[0]<self._end and self._phase[0]<=now[0]
                and self._phase[1]<=now[1]<self._phase[2]
                and (self._last is None or all(a>=b for a,b in zip(now,self._last))),
                'batch_original_phase_expired')
        self._last=now;return True

    def __call__(self, raw):
        from .pilot_resume_rpc import parse
        self.check();value=parse(raw);e=value['enrollment'];local=self.policy.unit(self.unit)
        require(value['phase'] in ('enroll','current','prepare','finalize','start')
            and e['schema']=='meshticulum.native-parent-enrollment.v2'
            and canonical(e['policy'])==self._policy_bytes and e['unit']==self.unit and e['host_boot_id']==local.host_boot_id
            and e['history_binding_sha256']==self._history and self._start<=e['original_epoch_us']<self.policy.expires_epoch_us
            and e['deadline_monotonic_us']==e['original_monotonic_us']+self.policy.expires_epoch_us-e['original_epoch_us']
            and e['deadline_monotonic_us']<self._phase[2],'batch_enrollment_changed')
        return self.check()

    def close(self):
        if (os.getpid(),threading.get_ident())!=(self._pid,self._thread):return False
        self._closed=True
        if self.reader is not None:self.reader.close()
        return True
