# SPDX-License-Identifier: Apache-2.0
"""Exact two-profile public LoRa comparison; never an enrollment or radio entry."""
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import re
import sys
import threading

from .phase_timing import call as timed_call
from meshticulum.bench.wire import require
from .pilot_parent_caps import ParentPolicy, Cost, canonical
from .profiles import profile_for

KIND = 'c-lora-sf7-sf5-8k-comparison-v1'
SCHEMA = 'meshticulum.finite-public-lora-sf7-sf5-comparison.v1'
SSH_SCHEMA = 'meshticulum.finite-lora-sf7-sf5-comparison-ssh-pilot.v1'
PROFILE_NAMES = ('sf7-500', 'sf5-500')
COUNT = 2
EXPERIMENT_MODE = 'public-radio-experiment-only'
EXPERIMENT_OWNER_SCHEMA = 'meshticulum.public-radio-experiment-resume-owner.v1'
EXPERIMENT_ADMISSION_SCHEMA = 'meshticulum.public-radio-experiment-admission.v1'
HEX = re.compile(r'[0-9a-f]{64}\Z')
SID = re.compile(r'[0-9a-f]{32}\Z')


def validate_experiment_admission(value):
    """Keep the limited report mode explicit in the protected native owner.

    Root's source-pinned public runner verifies these exact report/exception
    bytes before enrollment. This descriptor neither creates a grant nor
    substitutes for the existing owner/history/runtime/phase/claim checks.
    """
    from .mcu_artifact import require_lora_selector_scope
    pins=require_lora_selector_scope(KIND)
    hashes=('experiment_source_run_sha256','experiment_source_tests_sha256',
        'experiment_source_provenance_sha256','experiment_suite_sha256',
        'experiment_exception_sha256','experiment_runner_sha256','experiment_validator_sha256')
    flags=('full_source_green','alpha_qualified','private_production_enabled',
        'physical_qualification','rf_qualified','ip_allowed','flash_allowed','reset_allowed')
    require(type(value) is dict and set(value)==set(hashes+flags+(
        'schema','admission_mode','operation_schema','ready_schema','comparison_scope_sha256',
        'history_receipt_sha256','private_gate_currently_failing')),
        'public_experiment_admission_shape')
    require(value['schema']==EXPERIMENT_ADMISSION_SCHEMA and value['admission_mode']==EXPERIMENT_MODE
        and value['operation_schema']=='meshticulum.public-radio-experiment-lora-operation.v1'
        and value['ready_schema']=='meshticulum.public-radio-experiment-source-ready.v1'
        and value['comparison_scope_sha256']==pins['scope_sha256']
        and value['history_receipt_sha256']==pins['history_receipt_sha256']
        and all(type(value[k]) is str and HEX.fullmatch(value[k]) for k in hashes)
        and all(value[k] is False for k in flags)
        and value['private_gate_currently_failing'] is True,
        'public_experiment_must_remain_limited_and_source_bound')
    return True


def owner_admission(owner):
    """Read the recorded schema directly; never rewrite it into standard mode."""
    require(type(owner) is dict,'comparison_original_owner_changed')
    if owner.get('schema')=='meshticulum.finite-public-resume-owner.v1':
        require('admission_mode' not in owner and 'experiment_admission' not in owner,
            'standard_comparison_owner_cannot_consume_experiment')
        return None
    require(owner.get('schema')==EXPERIMENT_OWNER_SCHEMA
        and owner.get('admission_mode')==EXPERIMENT_MODE
        and all(owner.get(k) is False for k in ('full_source_green','alpha_qualified',
            'private_production_enabled','physical_qualification','rf_qualified'))
        and owner.get('private_gate_currently_failing') is True,
        'explicit_public_experiment_owner_required')
    admission=owner.get('experiment_admission')
    validate_experiment_admission(admission)
    require(owner.get('comparison_scope_sha256')==admission['comparison_scope_sha256']
        and owner.get('history_receipt_sha256')==admission['history_receipt_sha256'],
        'public_experiment_owner_source_rebound')
    return admission


def validate_descriptor(descriptor):
    require(type(descriptor) is dict and set(descriptor) == {
        'schema','sample_count','phase_seconds','application_seconds','publisher','requester',
        'radio_profile_names','prepared_endpoint_limit','samples'}, 'comparison_descriptor_shape')
    require(descriptor['schema']==SCHEMA and type(descriptor['sample_count']) is int
        and descriptor['sample_count']==COUNT and type(descriptor['phase_seconds']) is int
        and descriptor['phase_seconds']==600 and type(descriptor['application_seconds']) is int
        and descriptor['application_seconds']==180 and descriptor['publisher']=='unit-c'
        and descriptor['requester']=='unit-a' and descriptor['radio_profile_names']==list(PROFILE_NAMES)
        and type(descriptor['prepared_endpoint_limit']) is int and descriptor['prepared_endpoint_limit']==8,
        'comparison_original_scope_changed')
    rows=descriptor['samples'];require(type(rows) is list and len(rows)==COUNT,'comparison_samples_changed')
    for index,row in enumerate(rows):
        require(type(row) is dict and set(row)=={'index','radio_profile_name','campaign_id','resource_sha256',
            'chunk_sha256','request','response'} and type(row['index']) is int and row['index']==index
            and row['radio_profile_name']==PROFILE_NAMES[index]
            and type(row['campaign_id']) is str and SID.fullmatch(row['campaign_id'])
            and all(type(row[k]) is str and HEX.fullmatch(row[k]) for k in ('resource_sha256','chunk_sha256')),
            'comparison_sample_binding_changed')
        for name,size in (('request',74),('response',8192)):
            value=row[name]
            require(type(value) is dict and set(value)=={'bytes','sha256'} and type(value['bytes']) is int
                and value['bytes']==size and type(value['sha256']) is str and HEX.fullmatch(value['sha256']),
                'comparison_sample_object_changed')
    require(all(len({r[key] for r in rows})==COUNT for key in ('campaign_id','resource_sha256','chunk_sha256')),
        'comparison_resource_reuse_refused')
    return True


class ComparisonContext:
    __slots__=('__anchor','__dict__')

    def __setattr__(self,name,value):
        if name=='_ComparisonContext__anchor' and hasattr(self,name):
            raise AttributeError('comparison constructor binding is read-only')
        object.__setattr__(self,name,value)

    def __delattr__(self,name):
        if name=='_ComparisonContext__anchor':raise AttributeError('comparison constructor binding is read-only')
        object.__delattr__(self,name)

    def __init__(self, descriptor, policy, *, phase_end_epoch_us, experiment_admission=None):
        from .mcu_artifact import require_lora_selector_scope
        pins=require_lora_selector_scope(KIND)
        require(type(policy) is ParentPolicy and policy.qualification_kind==KIND,'comparison_parent_policy_changed')
        validate_descriptor(descriptor)
        if experiment_admission is not None:validate_experiment_admission(experiment_admission)
        require(type(phase_end_epoch_us) is int and phase_end_epoch_us-policy.issued_epoch_us==600000000
            and policy.expires_epoch_us==phase_end_epoch_us-5000000
            and policy.deadline_monotonic_us-policy.original_monotonic_us==595000000,
            'comparison_original_phase_changed')
        for unit in policy.units:
            extra=Cost() if unit.unit=='unit-b' else Cost(1024,240000000)
            require(unit.limits==unit.prior_debt.plus(extra),'comparison_full_prepaid_partition_changed')
        self.descriptor,self.policy,self.phase_end_epoch_us=descriptor,policy,phase_end_epoch_us
        self._refs=(descriptor,policy,phase_end_epoch_us)
        self._descriptor_bytes=canonical(descriptor);self._policy_bytes=canonical(policy.to_dict())
        self._pid,self._thread=os.getpid(),threading.get_ident()
        self.experiment_admission=experiment_admission
        self.__anchor=(self._refs,self._descriptor_bytes,self._policy_bytes,self._pid,self._thread,
            canonical(dict(pins)),experiment_admission,canonical(experiment_admission))
        self.check()

    def check(self):
        from .mcu_artifact import require_lora_selector_scope
        anchor=self.__anchor
        require((os.getpid(),threading.get_ident())==(self._pid,self._thread)==anchor[3:5]
            and self._refs is anchor[0] and self._descriptor_bytes is anchor[1] and self._policy_bytes is anchor[2]
            and self.descriptor is self._refs[0] and self.policy is self._refs[1]
            and self.phase_end_epoch_us==self._refs[2]
            and canonical(self.descriptor)==self._descriptor_bytes and canonical(self.policy.to_dict())==self._policy_bytes,
            'comparison_context_rebound')
        require(canonical(dict(require_lora_selector_scope(KIND)))==anchor[5],
            'comparison_original_scope_rebound')
        require(self.experiment_admission is anchor[6]
            and canonical(self.experiment_admission)==anchor[7],'public_experiment_context_rebound')
        if self.experiment_admission is not None:validate_experiment_admission(self.experiment_admission)
        return True

    @classmethod
    def from_owner(cls, owner, policy):
        from .mcu_artifact import lora_artifact, require_lora_selector_scope
        pins=require_lora_selector_scope(KIND);selected=lora_artifact()
        admission=owner_admission(owner)
        require(type(owner) is dict
            and owner.get('qualification_kind')==KIND and owner.get('radio_profile_name')=='lora-sf7-sf5'
            and owner.get('rf_profile')==asdict(profile_for('sf7-500'))
            and owner.get('lora_mcu_artifact')==selected
            and owner.get('core_commit')==selected['runtime_core_revision']
            and owner.get('comparison_scope_sha256')==pins['scope_sha256']
            and owner.get('history_receipt_sha256')==pins['history_receipt_sha256']
            and owner.get('policy_digest')==policy.digest
            and owner.get('complete_global_debt') is False and owner.get('actual_cost') is None
            and owner.get('phase_start_epoch_us')==policy.issued_epoch_us,
            'comparison_original_owner_changed')
        return cls(owner['lora_comparison'],policy,phase_end_epoch_us=owner['phase_end_epoch_us'],
            experiment_admission=admission)

    def sample(self, campaign_id, resource_sha256, profile):
        self.check()
        rows=[row for row in self.descriptor['samples'] if row['campaign_id']==campaign_id
              and row['resource_sha256']==resource_sha256 and row['radio_profile_name']==profile]
        require(len(rows)==1,'comparison_undeclared_resource_or_profile')
        return rows[0]

    def metadata(self, metadata):
        self.check()
        require(type(metadata) is dict and metadata.get('unit') in ('unit-a','unit-c')
            and metadata.get('transport')=='lora' and metadata.get('format') in ('MR1.request','MR1.response')
            and 'radio_modem' not in metadata,'comparison_child_route_changed')
        rows=[row for row in self.descriptor['samples'] if row['campaign_id']==metadata['campaign_id']
              and row['resource_sha256']==metadata['resource_sha256']]
        require(len(rows)==1,'comparison_undeclared_resource')
        row=rows[0];request=metadata['format']=='MR1.request'
        require((metadata['sender'],metadata['receiver'])==(('unit-a','unit-c') if request else ('unit-c','unit-a'))
            and metadata['object']==row['request' if request else 'response'],'comparison_child_object_or_direction_changed')
        profile=dict(metadata['profile']);lease=profile.pop('lease_ms')
        wanted=asdict(profile_for(row['radio_profile_name']));wanted.pop('lease_ms')
        require(profile==wanted and all(type(profile[k]) is type(v) for k,v in wanted.items())
            and type(lease) is int and 10000<=lease<=90000,'comparison_child_profile_changed')
        self.check();return row

    def package(self, package):
        self.check();p=package['grant'];fmt=p['service']['format'];request=fmt=='MR1.request'
        row=self.metadata(dict(unit=package['unit'],transport=package['transport'],format=fmt,
            campaign_id=package['campaign_id'],resource_sha256=package['resource_sha256'],
            sender=p['sender_unit'],receiver='unit-c' if request else 'unit-a',
            object={k:p['object'][k] for k in ('bytes','sha256')},profile=p['profile']))
        require('radio_modem' not in p and package['application_expires_epoch_us']
            ==p['service']['application_expires_epoch_us']==p['expires_epoch_us']
            and self.policy.issued_epoch_us<=p['issued_epoch_us']<p['expires_epoch_us']<=self.policy.expires_epoch_us
            and 0<p['expires_epoch_us']-p['issued_epoch_us']<=181000000,
            'comparison_resource_original_window_changed')
        return row


class ComparisonApproval:
    """Held root-owned opt-in records for genuine NativeCaps worker/provision paths.

    Descriptor enforcement adds no radio entry. The ordinary single sample path
    remains unchanged. Every approval rechecks owner/prior/policy/capture bytes,
    original phase clocks and the authentic enrollment history binding.
    """
    __slots__=('__anchor','__dict__')

    def __setattr__(self,name,value):
        if name=='_ComparisonApproval__anchor' and hasattr(self,name):
            raise AttributeError('comparison approval binding is read-only')
        object.__setattr__(self,name,value)

    def __delattr__(self,name):
        if name=='_ComparisonApproval__anchor':raise AttributeError('comparison approval binding is read-only')
        object.__delattr__(self,name)

    def __init__(self, directory, policy, unit, facts, clock):
        from .pilot_resume_rpc import Reader,Runtime,parse,sha,stable
        self.reader=self.runtime=None;self._closed=False;self._pid,self._thread=os.getpid(),threading.get_ident()
        own=(self._pid,self._thread);self._owner_ref=own;self._owned_close=lambda:None
        self.policy,self.unit,self.facts,self.clock=policy,unit,facts,clock
        try:
            self.reader=Reader();reader=self.reader
            self._owned_close=lambda:reader.close() if own==(os.getpid(),threading.get_ident()) else None
            raw=self.reader.read(directory/'owner.json',200000);owner=parse(raw)
            self.context=ComparisonContext.from_owner(owner,policy)
            from .mcu_artifact import select_lora_runtime
            helper=sys.modules.get('_lantern_execution_runtime')
            runtime_root=helper.module_root(__name__) if helper is not None else Path(__file__).absolute().parents[2]
            self.runtime=Runtime(runtime_root,owner['manifest_sha256'])
            runtime=self.runtime
            def close_owned():
                if own==(os.getpid(),threading.get_ident()):
                    try:runtime.close()
                    finally:reader.close()
            self._owned_close=close_owned
            self.lora_mcu_artifact=select_lora_runtime(self.runtime.manifest)
            require(self.lora_mcu_artifact==owner['lora_mcu_artifact']
                and self.runtime.root.name=='runtime-'+owner['runtime_sha256'][:16]
                and type(owner['runtime_sha256']) is str and HEX.fullmatch(owner['runtime_sha256']),
                'comparison_runtime_binding_changed')
            self._runtime_bytes=canonical(self.runtime.manifest)
            self._artifact_bytes=canonical(self.lora_mcu_artifact)
            local=policy.unit(unit);prior_raw=self.reader.read(directory/'prior.json',200000);prior=parse(prior_raw)
            require(self.reader.read(directory/'policy.json',20000)==canonical(policy.to_dict()),'comparison_protected_policy_changed')
            binding_raw=self.reader.read(directory/'history-binding.json',16384);binding=parse(binding_raw)
            from .management_input import RAW_MAX
            from .capture_storage import decode
            captured_raw=decode(self.reader.read(directory/'capture.json',RAW_MAX));captured=parse(captured_raw)
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
                and facts['host_boot_id']==local.host_boot_id,'comparison_protected_owner_changed')
            auth=owner['authorization'];rf=owner['rf_record']
            require(auth['human_statement']=='run it bro' and type(auth['count_authorized']) is int and auth['count_authorized']==1
                and auth['public_only'] is True and auth['private_production'] is False
                and owner['rf_record_sha256']=='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9'
                and rf['country']=='MY' and rf['C_transmission_allowed'] is True and rf['A_B_transmission_allowed'] is True
                and all(rf['units'][u]['antenna_connected'] is True for u in ('unit-a','unit-b','unit-c')),
                'comparison_original_consent_changed')
            self._history=owner['history_binding_sha256'];self._start,self._end=owner['phase_start_epoch_us'],owner['phase_end_epoch_us']
            phase=owner['native_phase'][unit];self._phase=(phase['epoch_us'],phase['monotonic_us'],phase['deadline_monotonic_us'])
            self._facts_bytes=canonical(stable(facts));self._policy_bytes=canonical(policy.to_dict());self._last=None
            self._refs=(self.policy,self.unit,self.facts,self.clock,self.reader,self.context,self._phase,
                self._history,self._start,self._end,self._facts_bytes,self._policy_bytes,self.runtime,self.lora_mcu_artifact,
                self._runtime_bytes,self._artifact_bytes,self._pid,self._thread,self._owner_ref,self._owned_close)
            self.__anchor=self._refs
            self.check()
        except BaseException:self.close();raise

    def _bound(self,last):
        from .pilot_resume_rpc import stable
        refs=self.__anchor
        require(self._closed is False and (os.getpid(),threading.get_ident())==self._owner_ref
                and self._refs is refs and self._last is last
                and all(a is b for a,b in zip((self.policy,self.unit,self.facts,self.clock,self.reader,self.context,self._phase,
                    self._history,self._start,self._end,self._facts_bytes,self._policy_bytes,self.runtime,self.lora_mcu_artifact,
                    self._runtime_bytes,self._artifact_bytes,self._pid,self._thread,self._owner_ref,self._owned_close),refs))
                and canonical(self.policy.to_dict())==self._policy_bytes and canonical(stable(self.facts))==self._facts_bytes,
                'comparison_approval_rebound')
        from .mcu_artifact import select_lora_runtime
        require(canonical(self.runtime.manifest)==self._runtime_bytes
            and canonical(self.lora_mcu_artifact)==self._artifact_bytes
            and select_lora_runtime(self.runtime.manifest)==self.lora_mcu_artifact,'comparison_runtime_rebound')
        self.context.check()

    def _time(self,last,previous):
        self._bound(last);now=self.clock();self._bound(last)
        require(type(now) is tuple and len(now)==2 and all(type(v) is int for v in now)
                and self._start<=now[0]<self._end and self._phase[0]<=now[0]
                and self._phase[1]<=now[1]<self._phase[2]
                and (previous is None or all(a>=b for a,b in zip(now,previous))),
                'comparison_original_phase_expired')
        return now

    def check(self):
        last=self._last
        try:
            first=self._time(last,last);now=self._time(last,first)
            timed_call(getattr(self,'_phase_timing',None),'owner_reader',self.reader.check);self._bound(last)
            timed_call(getattr(self,'_phase_timing',None),'runtime_reader',self.runtime.check);self._bound(last)
            self._last=now;return True
        except BaseException:self.close();raise

    def __call__(self, raw):
        from .pilot_resume_rpc import parse
        # Parsing only compares the enrollment. Check the binding here and
        # read protected files once, immediately before granting permission.
        try:
            self._bound(self._last)
        except BaseException:
            self.close()
            raise
        value=parse(raw);e=value['enrollment'];local=self.policy.unit(self.unit)
        require(value['phase'] in ('enroll','current','prepare','finalize','start')
            and e['schema']=='meshticulum.native-parent-enrollment.v2'
            and canonical(e['policy'])==self._policy_bytes and e['unit']==self.unit and e['host_boot_id']==local.host_boot_id
            and e['history_binding_sha256']==self._history and self._start<=e['original_epoch_us']<self.policy.expires_epoch_us
            and e['deadline_monotonic_us']==e['original_monotonic_us']+self.policy.expires_epoch_us-e['original_epoch_us']
            and e['deadline_monotonic_us']<self._phase[2],'comparison_enrollment_changed')
        return self.check()

    def close(self):
        own=self.__anchor[18] if hasattr(self,'_ComparisonApproval__anchor') else self._owner_ref
        if own!=(os.getpid(),threading.get_ident()):return False
        self._closed=True
        # Cleanup retains the constructor's actual owners even if a callback
        # replaces mutable comparison/runtime fields before its after-check.
        closer=self.__anchor[-1] if hasattr(self,'_ComparisonApproval__anchor') else self._owned_close
        closer()
        return True
