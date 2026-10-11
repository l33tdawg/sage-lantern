# SPDX-License-Identifier: Apache-2.0
"""Finite public-pilot stages over an explicitly supplied management operator.

The operator owns actual child handles, native status, preparation, collection,
verification and revocation. This module installs no runtime, discovers no host
and grants no RF window. A source/model operator provides source evidence only.
"""
import base64
from dataclasses import asdict, replace
import hashlib
import json
import secrets
import time
import uuid

from meshticulum.contracts.identity import PeerID
from meshticulum.ip.wire import IpLimits
from meshticulum.resources import resource
from lantern_meshticulum.bench.pilot_provision import identifier
from lantern_meshticulum.bench.profiles import RADIO_ADMISSION, profile_for
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps, canonical
from meshticulum.bench.wire import require

UNITS = ('unit-a', 'unit-b', 'unit-c')
# Receiver setup has a ten-second bound. Leave another five seconds for native
# preparation and the driver's ARM receipt; never buy this time by renewing the
# original resource. The native driver independently checks the remaining time.
RADIO_SETUP_RESERVE_MS = 15000


def pair_packages(*, campaign_id, resource_sha256, application_expires_epoch_us,
                  sender, receiver, transport, format, descriptor, source,
                  request, peers, boots, now_epoch_us, endpoints=None, profile='sf7-500', radio_permission=None):
    """Fresh independent adjacent-link sessions; receiver never gets a body."""
    identifier(campaign_id)
    require(sender in UNITS and receiver in UNITS and sender != receiver
            and transport in ('lora', 'tcp') and format in ('MR1.request', 'MR1.response'), 'invalid_pilot_pair')
    require(type(application_expires_epoch_us) is int and type(now_epoch_us) is int
            and now_epoch_us < application_expires_epoch_us <= now_epoch_us + 180000000,
            'invalid_pilot_original_expiry')
    require(type(descriptor) is dict and set(descriptor) == {'bytes','sha256'}
            and type(descriptor['bytes']) is int and 0 < descriptor['bytes'] <= 16384
            and len(bytes.fromhex(descriptor['sha256'])) == 32
            and len(bytes.fromhex(resource_sha256)) == 32, 'invalid_pilot_pair_object')
    require((source is None and format == 'MR1.request' and type(request) is bytes
             and len(request) == descriptor['bytes'] and hashlib.sha256(request).hexdigest() == descriptor['sha256'])
            or (source is not None and request is None), 'pilot_pair_payload_provenance_required')
    for unit in (sender,receiver):
        require(str(uuid.UUID(boots[unit])) == boots[unit], 'invalid_pilot_pair_boot')
        PeerID(**peers[unit])
    radio_profile = None
    if transport == 'lora':
        require(type(radio_permission) is dict and radio_permission == {'country':'MY','antenna_confirmed':True},
                'pilot_explicit_radio_confirmation_required')
        selected = profile_for(profile)
        remaining_ms = (application_expires_epoch_us - now_epoch_us) // 1000
        lease_ms = min(selected.lease_ms, remaining_ms - RADIO_SETUP_RESERVE_MS)
        require(lease_ms >= RADIO_ADMISSION.min_lease_ms,
                'pilot_original_time_too_short_for_radio_admission')
        radio_profile = replace(selected, lease_ms=lease_ms)
        RADIO_ADMISSION.check(radio_permission['country'], radio_permission['antenna_confirmed'], radio_profile)
    run_id, generation, key = secrets.token_hex(16), secrets.token_hex(16), secrets.token_bytes(32)
    shared = dict(issued_epoch_us=max(1,now_epoch_us-1000000), expires_epoch_us=application_expires_epoch_us)
    values = {}
    for unit in (sender,receiver):
        if transport == 'tcp':
            require(type(endpoints) is dict and set(endpoints) == {sender,receiver}, 'pilot_exact_ip_endpoints_required')
            route = dict(campaign_id=campaign_id,resource_sha256=resource_sha256,
                sender=sender,receiver=receiver,transport=transport,format=format)
            grant = dict(shared,schema='meshticulum.ip-service-run.v1',host_boot_id=boots[unit],
                local_peer=peers[unit],session=dict(initiator=peers[sender],responder=peers[receiver],
                    session_id=run_id,generation=generation),format=format,object=descriptor,
                route_binding_digest=hashlib.sha256(json.dumps(route,sort_keys=True).encode()).hexdigest(),
                application_expires_epoch_us=application_expires_epoch_us,
                application_deadline_monotonic_us=None,initiator_address=list(endpoints[sender]),
                responder_address=list(endpoints[receiver]),limits=asdict(IpLimits()))
        else:
            grant = dict(shared,schema='meshticulum.service-run.v1',unit=unit,host_boot_id=boots[unit],
                local_peer=peers[unit],initiator=peers[sender],responder=peers[receiver],
                session_id=run_id,generation=generation,profile=asdict(radio_profile),
                object=dict(descriptor,object_id=descriptor['sha256'],chunk_index=0),
                sender_unit=sender,country='MY',antenna_confirmed=True,max_frames=512,
                service=dict(kind='knowledge',format=format,application_expires_epoch_us=application_expires_epoch_us))
        values[unit] = dict(schema='meshticulum.public-pilot-package.v1',unit=unit,
            campaign_id=campaign_id,resource_sha256=resource_sha256,
            application_expires_epoch_us=application_expires_epoch_us,transport=transport,grant=grant,
            secret_base64=base64.b64encode(key).decode(),
            payload_base64=base64.b64encode(request).decode() if unit==sender and request is not None else None,
            source_run_id=source if unit==sender else None)
    return dict(run_id=run_id,sender=sender,receiver=receiver,transport=transport,format=format,
        descriptor=descriptor,packages=values)


class Controller:
    def __init__(self, operator, *, campaign_id, resource_sha256, expires_epoch_us,
                 handoff_deadline_monotonic, peers, boots, parent, radio_permission=None,
                 clock=time.monotonic, epoch=None, sleep=time.sleep):
        self.operator,self.clock,self.sleep=operator,clock,sleep
        require(type(parent) is ParentCaps and parent.current() is True,
                'pilot_existing_parent_required')
        self.parent = self._parent_ref = parent
        self.epoch=epoch or (lambda:time.time_ns()//1000)
        self.campaign_id=identifier(campaign_id);self.resource_sha256=resource_sha256
        self.expires_epoch_us=expires_epoch_us;self.peers,self.boots=peers,boots
        self.radio_permission=radio_permission
        require(expires_epoch_us <= parent.policy.expires_epoch_us
                and set(peers) == set(boots) == {u.unit for u in parent.policy.units}
                and all(PeerID(**peers[u.unit]) == u.peer and boots[u.unit] == u.host_boot_id
                        for u in parent.policy.units), 'pilot_parent_scope_changed')
        now=self.clock();remaining=(expires_epoch_us-self.epoch())/1e6
        require(0 < remaining <= 180 and handoff_deadline_monotonic > now+10,
                'pilot_current_finite_window_required')
        # Reserve ten seconds for revocation and owned-job cleanup.
        self.deadline=min(now+remaining,handoff_deadline_monotonic-10,
                          parent.policy.deadline_monotonic_us/1000000)
        self.last_clock=(self.epoch(),now)
        self.links=[];self.verified=[];self.cleanup=[]

    def current(self):
        require(self.parent is self._parent_ref and self.parent.current() is True,
                'pilot_parent_not_current')
        mono,epoch=self.clock(),self.epoch()
        require(epoch>=self.last_clock[0] and mono>=self.last_clock[1], 'pilot_controller_clock_regressed')
        self.last_clock=(epoch,mono)
        require(mono<self.deadline and epoch<self.expires_epoch_us,
                'pilot_original_resource_or_handoff_expired')

    def link(self,sender,receiver,transport,format,payload,source=None,*,test_rx_cutoff=None):
        self.current()
        descriptor=dict(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest())
        pair=pair_packages(campaign_id=self.campaign_id,resource_sha256=self.resource_sha256,
            application_expires_epoch_us=self.expires_epoch_us,sender=sender,receiver=receiver,
            transport=transport,format=format,descriptor=descriptor,source=source,
            request=payload if source is None else None,peers=self.peers,boots=self.boots,
            now_epoch_us=self.epoch(),endpoints=self.operator.endpoints(sender,receiver) if transport=='tcp' else None,
            radio_permission=self.radio_permission)
        require(test_rx_cutoff is None or (transport=='tcp' and format=='MR1.response'
                and type(test_rx_cutoff) is int and 8<=test_rx_cutoff<len(payload)),
                'invalid_controller_tcp_loss_control')
        if test_rx_cutoff is not None:pair['test_rx_cutoff']=test_rx_cutoff
        row={k:v for k,v in pair.items() if k!='packages'}
        row.update(phase='preparing',started_monotonic=self.clock(),outcomes={})
        self.links.append(row);handles={};prepared=[];row['prepared']=prepared
        row['attempted']=[];row['retired']=[]
        try:
            self.current()
            permit = self.parent.prepay_pair(tuple(pair['packages'].values()))
            children = self.parent.consume(permit)
            row['parent_allocation_id'] = permit.key
            proofs = {child.unit: child.to_dict() for child in children}
            frozen_packages = {}
            for unit, package in pair['packages'].items():
                wrapped = dict(package, schema='meshticulum.public-pilot-package.v2',
                    parent=dict(schema='meshticulum.public-parent-allocation.v1',
                        parent_id=self.parent.policy.parent_id, policy_digest=self.parent.policy.digest,
                        allocation_id=permit.key, child=proofs[unit]))
                frozen_packages[unit] = canonical(wrapped)
            row['prepared_parent_claims'] = {}
            parallel=getattr(self.operator,'parallel_preparation',False)
            require(type(parallel) is bool,'invalid_parallel_preparation_mode')
            receipts=None
            if parallel:
                require(callable(getattr(self.operator,'prepare_pair',None)),'parallel_preparation_api_missing')
                self.current()
                row['attempted'].extend((receiver,sender))
                packages={unit:json.loads(frozen_packages[unit]) for unit in (receiver,sender)}
                receipts=self.operator.prepare_pair(packages)
                require(type(receipts) is dict and set(receipts)=={receiver,sender},'parallel_preparation_receipts_missing')
                require(all(canonical(packages[unit])==frozen_packages[unit] for unit in (receiver,sender)),
                        'pilot_prepare_package_mutated')
                self.current()
            for unit in (receiver,sender):
                self.current()
                package = json.loads(frozen_packages[unit])
                if receipts is None:
                    row['attempted'].append(unit)
                    receipt = self.operator.prepare(package)
                else:receipt=receipts[unit]
                require(canonical(package) == frozen_packages[unit], 'pilot_prepare_package_mutated')
                claim = receipt.get('parent') if type(receipt) is dict else None
                require(type(receipt) is dict and receipt.get('schema') == 'meshticulum.provisioned-public-pilot.v2'
                        and receipt.get('unit') == unit and receipt.get('run_id') == pair['run_id']
                        and type(claim) is dict and claim.get('schema') == 'meshticulum.native-parent-claim.v1'
                        and claim.get('prepared') is True and claim.get('parent_id') == self.parent.policy.parent_id
                        and claim.get('policy_digest') == self.parent.policy.digest
                        and claim.get('controller_allocation_id') == permit.key
                        and claim.get('unit') == unit and claim.get('host_boot_id') == self.boots[unit]
                        and claim.get('run_id') == pair['run_id']
                        and claim.get('resource_sha256') == self.resource_sha256
                        and claim.get('application_expires_epoch_us') == self.expires_epoch_us
                        and claim.get('child_caps') == proofs[unit]
                        and claim.get('original_plan_sha256') == hashlib.sha256(canonical(package['grant'])).hexdigest()
                        and type(claim.get('application_deadline_monotonic_us')) is int
                        and claim['application_deadline_monotonic_us'] > 0,
                        'pilot_native_parent_receipt_missing_or_rebound')
                self.current()
                row['prepared_parent_claims'][unit] = json.loads(canonical(claim))
                prepared.append(unit)
            row['phase']='starting_receiver'
            handles[receiver]=self.operator.start(receiver,pair)
            setup_deadline=min(self.deadline,self.clock()+10)
            while True:
                self.current()
                require(handles[receiver].poll() is None and self.clock()<setup_deadline,
                        'pilot_receiver_failed_or_setup_expired')
                status=self.operator.status(receiver,pair['run_id'])
                if status.get('kernel_handle_opened') is True and status.get('live') is True and status.get('ready') is True:
                    require(status['unit']==receiver and status['run_id']==pair['run_id']
                            and status['host_boot_id']==self.boots[receiver], 'pilot_receiver_identity_changed')
                    break
                self.sleep(.02)
            handles[sender]=self.operator.start(sender,pair)
            row['phase']='running'
            while True:
                # Poll every owned child: poll() also services its one sudo
                # prompt. Short-circuiting on a live receiver starves the
                # sender's authentication until the receiver times out.
                codes=[handle.poll() for handle in handles.values()]
                if all(code is not None for code in codes):break
                self.current();self.sleep(.02)
            statuses={};collection_errors={};row['terminal_statuses']=statuses
            for unit in (sender,receiver):
                try:
                    statuses[unit]=self.operator.status(unit,pair['run_id'])
                    row['outcomes'][unit]=self.operator.collect(unit,pair['run_id'])
                except Exception as error:
                    collection_errors[unit]=type(error).__name__+':'+str(error)[:200]
            if collection_errors:
                row['collection_errors']=collection_errors
                raise RuntimeError('pilot_native_outcome_collection_failed')
            for unit in (sender,receiver):
                status=statuses[unit]
                require(status['unit']==unit and status['host_boot_id']==self.boots[unit]
                        and status['run_id']==pair['run_id'] and status['live'] is False,
                        'pilot_native_process_not_terminal')
                result=row['outcomes'][unit]
                start = result.get('parent')
                expected = dict(row['prepared_parent_claims'][unit],
                                schema='meshticulum.native-parent-start.v1', started=True)
                require(start == expected and result['grant_context_sha256'] == start['grant_context_sha256']
                        and type(result['original_application_deadline_us']) is int
                        and 0 < result['original_application_deadline_us'] <= start['application_deadline_monotonic_us'],
                        'pilot_native_parent_start_missing_or_rebound')
                require(result['unit']==unit and result['host_boot_id']==self.boots[unit]
                        and result['session_id']==pair['run_id'] and result['service_format']==format
                        and result['sends'] is (unit==sender)
                        and result['original_application_expires_epoch_us']==self.expires_epoch_us
                        and result['object']['bytes']==descriptor['bytes']
                        and result['object']['sha256']==descriptor['sha256']
                        , 'pilot_link_incomplete_or_rebound')
                require(result['native_job']['pid']==status['pid']
                        and result['native_job']['start_ticks']==status['start_ticks']
                        and result['grant_context_sha256']==status['grant_context_sha256'],
                        'pilot_result_process_binding_changed')
                if test_rx_cutoff is not None:
                    require(handles[unit].poll()!=0 and result['complete'] is False,
                            'pilot_tcp_loss_not_observed')
                    if unit==receiver:
                        require(handles[unit].poll()!=0 and result['complete'] is False
                                and result['test_tcp_loss']['injected'] is True
                                and result['test_tcp_loss']['known_rx_stream_bytes']==test_rx_cutoff
                                and result['byte_journal']['fenced'] is True
                                and not result.get('service_object_durable'), 'pilot_tcp_loss_not_observed')
                    continue
                require(handles[unit].poll()==0 and result['complete'] is True
                        and not result.get('cleanup_errors'), 'pilot_link_incomplete_or_rebound')
                if transport=='lora':
                    require(result.get('disarmed_on_exit') is True
                            and result['attempt_journal']['fenced'] is False, 'pilot_radio_cleanup_unproven')
                if unit==receiver:
                    require(result.get('service_object_durable') is True
                            and result['service_object_sha256']==descriptor['sha256'], 'pilot_receiver_capture_missing')
            row.update(phase='complete',ended_monotonic=self.clock())
            if test_rx_cutoff is not None:
                row['phase']='expected_tcp_loss'
                self._retire(row)
                require(set(row['retired'])==set(row['attempted']),'pilot_failed_ip_retirement_unconfirmed')
            return pair['run_id']
        except BaseException as error:
            row.update(phase='failed',error=type(error).__name__+':'+str(error)[:200])
            self._retire(row,stop=True)
            raise
        finally:
            for handle in handles.values(): self.operator.reap(handle)

    def mixed_piece(self,request,response):
        """Actual C→A RF→B IP request; actual B→A IP→C RF response."""
        path,offset,size=resource.decode_request(request)
        require(offset==0 and path.startswith('/packs/'+self.resource_sha256+'/chunks/')
                and path.endswith('.json'), 'pilot_requires_whole_selected_public_chunk')
        chunk_sha256=path.rsplit('/',1)[-1][:-5]
        first_index=len(self.links)
        try:
            first=self.link('unit-c','unit-a','lora','MR1.request',request)
            second=self.link('unit-a','unit-b','tcp','MR1.request',request,source=first)
            third=self.link('unit-b','unit-a','tcp','MR1.response',response,
                source=dict(kind='served_public_chunk',request_run_id=second))
            fourth=self.link('unit-a','unit-c','lora','MR1.response',response,source=third)
            self.current()
            verified=self.operator.verify('unit-c',self.campaign_id,first,fourth)
            self.current()
            require(verified['schema']=='meshticulum.pilot-verified-piece.v1'
                    and verified['publisher_verified'] is True and verified['private_admission'] is False
                    and verified['sage_imported'] is False and verified['campaign_id']==self.campaign_id
                    and verified['manifest_sha256']==self.resource_sha256
                    and verified['unit']=='unit-c' and verified['response_run_id']==fourth
                    and verified['chunk_sha256']==chunk_sha256
                    and verified['original_application_expires_epoch_us']==self.expires_epoch_us
                    and type(verified['original_application_deadline_us']) is int
                    and 0 < verified['original_application_deadline_us'] <= min(
                        self.links[first_index]['outcomes']['unit-c']['original_application_deadline_us'],
                        self.links[-1]['outcomes']['unit-c']['original_application_deadline_us']),
                    'pilot_native_piece_verification_missing_or_rebound')
            self.verified.append(verified)
            return verified
        except BaseException:
            self.retire()
            raise

    def direct_piece(self,request,response,transport,*,test_rx_cutoff=None,publisher='unit-b',requester='unit-c'):
        """Direct A/B→C piece, or explicit C→A public reverse piece."""
        path,offset,size=resource.decode_request(request)
        require(offset==0 and path.startswith('/packs/'+self.resource_sha256+'/chunks/')
                and path.endswith('.json'),'pilot_requires_whole_selected_public_chunk')
        require(type(publisher) is str and type(requester) is str
                and (publisher,requester) in (('unit-a','unit-c'),('unit-b','unit-c'),('unit-c','unit-a'))
                and {publisher,requester} <= {unit.unit for unit in self.parent.policy.units},
                'pilot_direct_publisher_not_in_parent')
        index=len(self.links)
        try:
            first=self.link(requester,publisher,transport,'MR1.request',request)
            second=self.link(publisher,requester,transport,'MR1.response',response,
                source=dict(kind='served_public_chunk',request_run_id=first),test_rx_cutoff=test_rx_cutoff)
            if test_rx_cutoff is not None:return dict(interrupted=True,request_run_id=first,response_run_id=second)
            self.current()
            verified=self.operator.verify(requester,self.campaign_id,first,second)
            self.current()
            require(verified['schema']=='meshticulum.pilot-verified-piece.v1'
                    and verified['publisher_verified'] is True and verified['private_admission'] is False
                    and verified['sage_imported'] is False and verified['unit']==requester
                    and verified['campaign_id']==self.campaign_id and verified['manifest_sha256']==self.resource_sha256
                    and verified['response_run_id']==second and verified['chunk_sha256']==path.rsplit('/',1)[-1][:-5]
                    and verified['original_application_expires_epoch_us']==self.expires_epoch_us
                    and type(verified['original_application_deadline_us']) is int
                    and 0<verified['original_application_deadline_us']<=min(
                        self.links[index]['outcomes'][requester]['original_application_deadline_us'],
                        self.links[index+1]['outcomes'][requester]['original_application_deadline_us']),
                    'pilot_direct_piece_verification_missing_or_rebound')
            start=self.links[index]['outcomes'][requester]['start_monotonic_us']
            end=verified['verified_monotonic_us']
            require(type(start) is int and type(end) is int and end>start,'pilot_native_verification_timing_missing')
            verified=dict(verified,request_to_verification_us=end-start,transport=transport,
                request_run_id=first,response_run_id=second,
                useful_verified_bytes_per_second=verified['useful_verified_bytes']*1e6/(end-start))
            self.verified.append(verified)
            return verified
        except BaseException:
            self.retire();raise

    def failure_resume(self,exchanges,*,cutoff=512):
        """One original resource: IP loss, fresh LoRa, fresh measured IP return."""
        require(type(exchanges) is list and len(exchanges)==4,'pilot_resume_requires_four_distinct_pieces')
        wanted=[resource.decode_request(request)[0].rsplit('/',1)[-1][:-5] for request,_ in exchanges]
        require(len(set(wanted))==4,'pilot_resume_requires_four_distinct_pieces')
        first=self.direct_piece(*exchanges[0],'tcp')
        before=self._cache_status()
        require(wanted[0] in before['verified_chunks'],'pilot_first_verified_piece_not_in_cache')
        interrupted=self.direct_piece(*exchanges[1],'tcp',test_rx_cutoff=cutoff)
        after=self._cache_status()
        require(after['verified_chunks']==before['verified_chunks'] and wanted[1] not in after['verified_chunks'],
                'pilot_incomplete_piece_entered_cache')
        radio=self.direct_piece(*exchanges[1],'lora')
        resumed=self._cache_status()
        require(wanted[0] in resumed['verified_chunks'] and wanted[1] in resumed['verified_chunks'],
                'pilot_verified_piece_resume_missing')
        sample=self.direct_piece(*exchanges[2],'tcp')
        faster=sample['useful_verified_bytes_per_second']>radio['useful_verified_bytes_per_second']
        returned=self.direct_piece(*exchanges[3],'tcp') if faster else None
        return dict(schema='meshticulum.native-public-resume-sequence.v1',first=first,
            interrupted=interrupted,resumed=radio,fresh_ip_sample=sample,faster_ip_observed=faster,
            returned_to_ip=returned is not None,return_piece=returned,
            reused_verified_chunks=before['verified_chunks'],original_application_expires_epoch_us=self.expires_epoch_us)

    def _cache_status(self):
        self.current()
        value=self.operator.cache_status('unit-c',self.campaign_id)
        require(value['schema']=='meshticulum.public-pilot-cache-status.v1'
                and value['unit']=='unit-c' and value['host_boot_id']==self.boots['unit-c']
                and value['campaign_id']==self.campaign_id and value['manifest_sha256']==self.resource_sha256
                and value['original_application_expires_epoch_us']==self.expires_epoch_us,
                'pilot_cache_original_context_changed')
        return value

    def _retire(self,row,*,stop=False):
        for unit in row['attempted']:
            if unit in row['retired']: continue
            evidence=dict(unit=unit,run_id=row['run_id'],safe_handoff_proven=False)
            try:
                value=self.operator.revoke(unit,row['run_id'],row['transport'])
                require(value['retired'] is True and value['unit']==unit and value['run_id']==row['run_id'],
                        'pilot_retirement_unconfirmed')
                row['retired'].append(unit);evidence['retirement']=value
            except BaseException as error:
                evidence['retirement_error']=type(error).__name__
            if stop:
                try:evidence['stop_status']=self.operator.stop(unit,row['run_id'])
                except BaseException as error:evidence['stop_error']=type(error).__name__
            self.cleanup.append(evidence)

    def retire(self):
        """Explicitly retire every prepared child; never refunds its journal."""
        for row in self.links:
            self._retire(row)
        return self.cleanup
