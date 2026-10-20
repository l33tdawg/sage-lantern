# SPDX-License-Identifier: Apache-2.0
"""Opt-in five-part scheduling and final-ACK barrier; no implicit radio grant.

The signed bundle predeclares all five identities. Each native stage is admitted
once, after the previous part was publisher-verified and both local links were
retired. Its epoch and monotonic bounds are persisted without renewal. A sender
may authenticate completion only after the reliable flow confirms its final ACK.
Until that proof arrives, the receiver continues the original re-ACK loop.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from types import MappingProxyType

from meshticulum.bench.wire import BenchGrant, require
from .pilot_parent_caps import canonical, identifier, digest
from .profiles import GFSK250_FAST_BUNDLE_KIND as KIND, GFSK250_NAME, GFSK250_FAST_CORE_COMMIT, profile_for, profile_record

PHYSICAL_BINDINGS = MappingProxyType(dict(
    scope_sha256='afdca324e06df9f8769777b929f3ebcb82fc496bcf97c50a2bcfca319bf08c37',
    core_commit=GFSK250_FAST_CORE_COMMIT,
    history_receipt_sha256='68f7a680452b111ba6f826ff4566dd7a25e8299d7682e7979bd095730e87f489',
    preceding_parent='60d9ca2aa760cb4460d0af637101c997'))
FINAL_ACK = 'meshticulum.native-fast-final-ack.v1'
VERIFIED_PART = 'meshticulum.native-fast-verified-part.v1'
STAGE = 'meshticulum.native-fast-part-admission.v1'
DOMAIN = b'meshticulum.lantern.fast-bundle-proof.v1\0'


def require_bindings():
    from .retained_capture import PAID_HISTORY
    pins = dict(PHYSICAL_BINDINGS)
    require(set(pins) == {'scope_sha256','core_commit','history_receipt_sha256','preceding_parent'}
        and pins['core_commit'] == GFSK250_FAST_CORE_COMMIT and pins['preceding_parent'] == PAID_HISTORY[-1]
        and all(type(pins[k]) is str and len(pins[k]) == 64
            and all(c in '0123456789abcdef' for c in pins[k])
            for k in ('scope_sha256','history_receipt_sha256')), 'pending_fast_bundle_scope')
    return pins


def require_owner(owner, policy):
    from .pilot_bundle import BundleContext
    from .mcu_artifact import OWNER_SCHEMA, decode_owner, require_selector_scope
    if owner.get('schema')==OWNER_SCHEMA:
        owner,selected=decode_owner(owner)
        pins=dict(require_selector_scope(KIND),core_commit=selected.get('runtime_core_revision',selected['core_revision']))
    else:pins = require_bindings()
    require(policy.qualification_kind == owner.get('qualification_kind') == KIND
        and owner.get('radio_modem') == owner.get('radio_profile_name') == GFSK250_NAME
        and owner.get('mb1_bundle_scope_sha256') == pins['scope_sha256']
        and owner.get('core_commit') == pins['core_commit']
        and owner.get('history_receipt_sha256') == pins['history_receipt_sha256']
        and owner['rf_profile'] == profile_record(profile_for(GFSK250_NAME)), 'fast_bundle_owner_changed')
    for name in ('runtime_sha256','manifest_sha256'): digest(owner[name])
    context = BundleContext(owner['bundle_context'], policy)
    require(context.transport is not None, 'fast_bundle_requires_MB1')
    return context


def _proof(body, secret):
    require(type(body) is dict and type(secret) is bytes and len(secret) == 32, 'invalid_fast_proof_input')
    return dict(body=body, mac=hmac.new(secret, DOMAIN + canonical(body), hashlib.sha256).hexdigest())


def _verify(proof, expected, secret):
    require(type(proof) is dict and set(proof) == {'body','mac'}
        and type(proof['body']) is dict and canonical(proof['body']) == canonical(expected)
        and type(proof['mac']) is str and len(proof['mac']) == 64
        and hmac.compare_digest(proof['mac'], _proof(expected, secret)['mac']), 'fast_completion_proof_changed')
    return True


def _binding(caps, plan, grant, meta):
    require(caps.policy.qualification_kind == KIND and plan['session_id'] == meta['run_id']
        and grant.digest.hex() == meta['grant_digest'] and plan['object']['bytes'] == meta['object']['bytes']
        and plan['object']['sha256'] == meta['object']['sha256'], 'fast_completion_grant_rebound')
    context = caps._approve.bundle
    stage = context.stage(meta['campaign_id'], meta['application_expires_epoch_us'])
    return dict(parent_id=caps.policy.parent_id, policy_digest=caps.policy.digest,
        bundle_context_sha256=caps.policy.bundle_context_sha256, part_index=stage['index'],
        campaign_id=meta['campaign_id'], session_id=plan['session_id'], generation=plan['generation'],
        grant_context_sha256=grant.digest.hex(), service_format=plan['service']['format'],
        object=dict(meta['object']), application_expires_epoch_us=meta['application_expires_epoch_us'],
        transport_descriptor_sha256=context.value['transport_descriptor_sha256'])


def _authority_live(authority):
    # RunAuthority.live also checks original epoch/local application bounds.
    # The negotiated application/lease window can expire earlier than those.
    require(authority.live() is True and authority.deadlines is authority.contract.deadlines,
        'fast_completion_effective_authority_changed')
    now=authority.clock()
    require(now[1]/1000000<authority.deadlines.application_deadline_monotonic
        and now[1]<authority.application_deadline_us and now[1]<authority.deadline_us,
        'fast_completion_effective_deadline_expired')
    return True


def _live_binding(authority, caps):
    from meshticulum.bench.authority import RunAuthority
    from .pilot_native_caps import NativeCaps, _Public32Approval
    require(type(authority) is RunAuthority and type(caps) is NativeCaps
        and type(caps._approve) is _Public32Approval and caps.policy.qualification_kind == KIND
        and caps.public32_owner_current() is True and _authority_live(authority),
        'fast_completion_owner_not_live')
    receipt = json.loads(caps._active)
    require(receipt['schema'] == 'meshticulum.native-parent-start.v1'
        and receipt['started'] is True and receipt['run_id'] == authority.grant.session.session_id.hex()
        and receipt['native_plan_sha256'] == hashlib.sha256(canonical(authority.plan)).hexdigest()
        and receipt['grant_context_sha256'] == authority.grant.digest.hex(), 'fast_completion_start_rebound')
    meta = receipt['child_caps']['metadata']
    stage_receipt(caps, caps._approve.bundle.stage(meta['campaign_id'],meta['application_expires_epoch_us'])['index'],
        meta['application_expires_epoch_us'], caps._clock())
    body = _binding(caps, authority.plan, authority.grant, meta)
    require(_authority_live(authority) and caps.public32_owner_current() is True, 'fast_completion_owner_not_live')
    return body


def final_ack_proof(authority, caps, runner):
    """Called only on the genuine sender's authenticated reliable completion."""
    from meshticulum.bench.service_runner import ServiceRunner
    require(type(runner) is ServiceRunner and runner.transfer.flow.authority is authority
        and authority.sends is True and runner.wire_complete is True
        and runner.transfer.flow.snapshot()['complete'] is True, 'fast_sender_final_ack_not_confirmed')
    body = dict(_live_binding(authority, caps), schema=FINAL_ACK)
    return _proof(body, authority.grant.secret)


def receiver_completed(authority, caps, directory):
    """Freshly authenticate a relayed proof under the held original authority."""
    require(authority.sends is False, 'fast_completion_receiver_required')
    expected = dict(_live_binding(authority, caps), schema=FINAL_ACK)
    from .pilot_resume_rpc import Reader, parse
    with Reader() as reader:
        try: raw = reader.read(Path(directory)/'fast-final-ack.json', 4096)
        except FileNotFoundError: return False
        _verify(parse(raw), expected, authority.grant.secret)
        require(_authority_live(authority) and caps.public32_owner_current(), 'fast_completion_owner_not_live')
        reader.check()
        require(_authority_live(authority) and caps.public32_owner_current(), 'fast_completion_owner_not_live')
    return True


def deliver_final_ack(unit, run, proof, *, state, clock, facts):
    """Metadata-only one-use delivery; the running receiver verifies again.

    The live job owns the NativeCaps lock. This callback never opens that owner,
    marks a result complete, revokes a grant or dispatches RF. It verifies exact
    private plan/provision bytes and session MAC before publishing the proof.
    """
    from .pilot_resume_rpc import Reader, Runtime, parse
    from .pilot_parent_caps import ParentPolicy
    from meshticulum.contracts.identity import PeerID, SessionKey
    from .profiles import radio_route
    directory = Path(state)/'runs'/identifier(run)
    with Reader() as reader:
        plan = parse(reader.read(directory/'grant.json', 16384))
        provision = parse(reader.read(directory/'provision.json', 100000))
        claim = provision['parent']; parent = claim['parent_id']
        policy = ParentPolicy.from_dict(parse(reader.read(Path(state)/'parents'/parent/'policy.json',20000)))
        owner = parse(reader.read(Path(state)/'parents'/parent/'owner.json',200000))
        context = require_owner(owner, policy)
        runtime=None
        from .mcu_artifact import OWNER_SCHEMA, decode_owner, require_native_scope, validate_original_claim, validate_artifact, PROVISION_SCHEMA
        if owner.get('schema')==OWNER_SCHEMA:
            import sys
            original,selected=decode_owner(owner)
            helper=sys.modules.get('_lantern_execution_runtime')
            root=helper.module_root(__name__) if helper is not None else Path(__file__).absolute().parents[2]
            runtime=reader.stack.enter_context(Runtime(root,original['manifest_sha256']))
            require_native_scope(owner,policy,runtime.manifest)
            require(runtime.root.name=='runtime-'+original['runtime_sha256'][:16]
                and provision['schema']==PROVISION_SCHEMA
                and validate_artifact(provision['mcu_artifact'])==selected,'fast_completion_mcu_provision_changed')
            validate_original_claim(claim,plan,selected)
        require(unit == provision['unit'] == plan['unit'] and plan['sender_unit'] != unit
            and facts['host_boot_id'] == plan['host_boot_id'] == policy.unit(unit).host_boot_id
            and plan['session_id'] == provision['run_id'] == run and claim['prepared'] is True
            and hashlib.sha256(canonical(plan)).hexdigest() == claim['native_plan_sha256']
            and reader.read(directory/'active',16) == b'active\n', 'fast_completion_target_changed')
        secret = reader.read(directory/'secret.bin',32)
        require(hashlib.sha256(secret).hexdigest() == claim['secret_sha256'], 'fast_completion_secret_changed')
        session = SessionKey(PeerID(**plan['initiator']),PeerID(**plan['responder']),
            bytes.fromhex(run),bytes.fromhex(plan['generation']))
        grant = BenchGrant(session,'bench_transfer',hashlib.sha256(canonical(radio_route(plan))).digest(),
            plan['issued_epoch_us'],plan['expires_epoch_us'],208,plan['max_frames'],secret)
        meta = claim['child_caps']['metadata']; stage = context.stage(meta['campaign_id'],meta['application_expires_epoch_us'])
        expected = dict(parent_id=parent,policy_digest=policy.digest,bundle_context_sha256=policy.bundle_context_sha256,
            part_index=stage['index'],campaign_id=meta['campaign_id'],session_id=run,generation=plan['generation'],
            grant_context_sha256=grant.digest.hex(),service_format=plan['service']['format'],object=dict(meta['object']),
            application_expires_epoch_us=meta['application_expires_epoch_us'],
            transport_descriptor_sha256=context.value['transport_descriptor_sha256'],schema=FINAL_ACK)
        _verify(proof,expected,secret)
        first = clock(); context.current(first)
        require(first[0] < meta['application_expires_epoch_us'] and first[1] < claim['application_deadline_monotonic_us'],
            'fast_completion_original_deadline_expired')
        reader.check()
        if runtime is not None:runtime.check();require_native_scope(owner,policy,runtime.manifest)
        from lantern_meshticulum.ip.node import _write
        _write(directory/'fast-final-ack.json', canonical(proof))
        after = clock(); context.current(after); reader.check()
        if runtime is not None:runtime.check();require_native_scope(owner,policy,runtime.manifest)
        require(all(a >= b for a,b in zip(after,first)) and after[0] < meta['application_expires_epoch_us']
            and after[1] < claim['application_deadline_monotonic_us'], 'fast_completion_original_deadline_expired')
    return dict(schema='meshticulum.native-fast-completion-delivered.v1', unit=unit, run_id=run,
        proof_sha256=hashlib.sha256(canonical(proof)).hexdigest(), delivered=True, complete=False,
        radio_opened=False, arm_commands=0, tx_commands=0, refunds=0)


def stage_receipt(caps, index, expiry, now):
    require(caps.policy.qualification_kind == KIND and type(index) is int and 0 <= index < 5,
        'fast_stage_kind_changed')
    reader = caps._approve.reader
    path = caps.path.parent/('fast-stage-'+str(index)+'.json')
    from .pilot_resume_rpc import parse
    value = parse(reader.read(path,8192)); context = caps._approve.bundle
    declared = context.value['stages'][index]
    keys = {'schema','parent_id','policy_digest','unit','bundle_context_sha256','index','campaign_id',
        'response_bytes','response_sha256','issued_epoch_us','expires_epoch_us','handoff_end_epoch_us',
        'native_original_epoch_us','native_original_monotonic_us','native_deadline_monotonic_us',
        'previous_verified_proof_sha256'}
    require(set(value) == keys and value['schema'] == STAGE and value['parent_id'] == caps.policy.parent_id
        and value['policy_digest'] == caps.policy.digest and value['unit'] == caps.unit.unit
        and value['bundle_context_sha256'] == caps.policy.bundle_context_sha256
        and canonical({k:value[k] for k in declared}) == canonical(declared)
        and type(expiry) is int and value['expires_epoch_us'] == expiry
        and all(type(value[k]) is int for k in ('issued_epoch_us','expires_epoch_us','handoff_end_epoch_us',
            'native_original_epoch_us','native_original_monotonic_us','native_deadline_monotonic_us'))
        and context.value['original_epoch_us'] <= value['issued_epoch_us'] <= value['native_original_epoch_us'] <= now[0] < expiry
        and 0 < expiry-value['issued_epoch_us'] <= 180000000
        and value['handoff_end_epoch_us'] == expiry+10000000 <= caps.policy.expires_epoch_us
        and caps._enrollment['original_monotonic_us'] <= value['native_original_monotonic_us'] <= now[1]
            < value['native_deadline_monotonic_us'] <= caps._enrollment['deadline_monotonic_us']
        and value['native_deadline_monotonic_us'] <= value['native_original_monotonic_us']+expiry-value['native_original_epoch_us']
        and ((index==0 and value['previous_verified_proof_sha256'] is None) or
             (index>0 and type(value['previous_verified_proof_sha256']) is str
              and len(value['previous_verified_proof_sha256'])==64
              and all(c in '0123456789abcdef' for c in value['previous_verified_proof_sha256']))),
        'fast_stage_admission_changed_or_expired')
    reader.check()
    return value


def _completed_run(caps, reader, row):
    from .pilot_resume_rpc import parse
    from .pilot_job import inspect
    claim_id, raw, run = row; claim = parse(raw); meta = claim['child']['metadata']
    target = caps.path.parent.parent.parent/'runs'/run
    prepared = caps.db.execute('SELECT body FROM prepared WHERE id=?',(claim_id,)).fetchone()
    started = caps.db.execute('SELECT body FROM starts WHERE id=?',(claim_id,)).fetchone()
    require(prepared is not None and started is not None, 'fast_previous_run_not_started')
    receipt = parse(prepared[0]); start = parse(started[0]); plan = parse(reader.read(target/'grant.json',16384))
    provision = parse(reader.read(target/'provision.json',100000)); result = parse(reader.read(target/'result.json',300000))
    require(start == dict(receipt,schema='meshticulum.native-parent-start.v1',started=True)
        and provision['parent'] == receipt and result['parent'] == start
        and receipt['child_caps'] == claim['child'] and receipt['native_claim_id'] == claim_id
        and receipt['native_plan_sha256'] == hashlib.sha256(canonical(plan)).hexdigest()
        and result['schema'] == 'meshticulum.physical-service-run.v1' and result['unit'] == caps.unit.unit
        and result['host_boot_id'] == caps.unit.host_boot_id and result['session_id'] == run
        and result['grant_context_sha256'] == meta['grant_digest'] == receipt['grant_context_sha256']
        and result['complete'] is True and result['disarmed_on_exit'] is True
        and result.get('stop_requested',False) is False and not result.get('cleanup_errors')
        and result['service_format'] == meta['format'] and result['sends'] is (plan['sender_unit'] == caps.unit.unit)
        and {k:result['object'][k] for k in ('bytes','sha256')} == meta['object']
        and result['original_application_expires_epoch_us'] == meta['application_expires_epoch_us']
        and type(result['original_application_deadline_us']) is int
        and 0 < result['original_application_deadline_us'] <= receipt['application_deadline_monotonic_us']
        and result['attempt_journal']['fenced'] is False and result['flow']['complete'] is True
        and type(result['end_monotonic_us']) is int
        and result['end_monotonic_us']<result['original_application_deadline_us']
        and result['end_monotonic_us']<=caps._clock()[1]
        and reader.read(target/'active',16) == b'retired\n', 'fast_previous_completion_or_retirement_unproved')
    status = inspect(target,unit=caps.unit.unit,run_id=run,boot=caps.unit.host_boot_id)
    require(status['live'] is False and status['job_record_found'] is True
        and result['native_job']['pid'] == status['pid'] and result['native_job']['start_ticks'] == status['start_ticks'],
        'fast_previous_job_not_terminal')
    return target,claim,plan,result


def verified_part_proof(caps, response_run_id, verified):
    """Only the existing native canonical accept path calls this after verify."""
    from .pilot_resume_rpc import parse
    from meshticulum.contracts.identity import PeerID, SessionKey
    from .profiles import radio_route
    require(caps.policy.qualification_kind == KIND and caps.unit.unit == 'unit-a'
        and caps.public32_owner_current() is True and verified['publisher_verified'] is True,
        'fast_verified_part_owner_required')
    row = caps.db.execute('SELECT id,body,run_id FROM claims WHERE run_id=?',(response_run_id,)).fetchone()
    require(row is not None, 'fast_verified_response_missing')
    claim = parse(row[1]); meta = claim['child']['metadata']; reader = caps._approve.reader
    target = caps.path.parent.parent.parent/'runs'/response_run_id
    plan = parse(reader.read(target/'grant.json',16384)); secret = reader.read(target/'secret.bin',32)
    require(meta['format'] == 'MR1.response' and meta['secret_sha256'] == hashlib.sha256(secret).hexdigest(),
        'fast_verified_response_rebound')
    grant = BenchGrant(SessionKey(PeerID(**plan['initiator']),PeerID(**plan['responder']),
        bytes.fromhex(response_run_id),bytes.fromhex(plan['generation'])),'bench_transfer',
        hashlib.sha256(canonical(radio_route(plan))).digest(),plan['issued_epoch_us'],plan['expires_epoch_us'],208,plan['max_frames'],secret)
    binding = _binding(caps,plan,grant,meta); index = binding['part_index']; context = caps._approve.bundle
    require(verified['response_run_id'] == response_run_id and verified['campaign_id'] == meta['campaign_id']
        and verified['bundle_part']['part_index'] == index
        and verified['chunk_sha256'] == context.expected['parts'][index]['chunk_sha256']
        and verified['bundle_part']['bundle_context_sha256'] == caps.policy.bundle_context_sha256,
        'fast_verified_receipt_rebound')
    # A caller's publisher_verified DTO cannot create a progress capability.
    # Independently re-open the genuine completed capture and the canonical
    # part already accepted under the signed manifest/original native context.
    from .pilot_bundle import _NativeOwner, _HeldCapture, _receiver
    owner=_NativeOwner(caps,caps._clock)
    with _HeldCapture(owner,response_run_id,sends=False,format='MR1.response') as capture:
        receiver=_receiver(owner,create=False,captures=(capture,))
        require(receiver is not None,'fast_native_accepted_part_missing')
        with receiver:
            accepted,_=receiver._part(index)
            request,_=context.exchange(index)
            require(accepted==context.transport.decode_response(index,request,capture.payload),
                'fast_native_accepted_part_changed')
            capture.check();owner.check()
    body = dict(binding,schema=VERIFIED_PART,chunk_sha256=verified['chunk_sha256'],
        whole_sha256=context.value['whole_sha256'])
    require(caps.public32_owner_current() is True, 'fast_verified_owner_not_current')
    reader.check(); return _proof(body,secret)


def admit_stage(caps, index, issued_epoch_us, expires_epoch_us, previous_proof, *, clock):
    """One-use native admission under the ORIGINAL parent clock and paid caps."""
    from .pilot_bundle import _NativeOwner, _receiver
    from .pilot_resume_rpc import Reader, parse
    from meshticulum.contracts.identity import PeerID, SessionKey
    from .profiles import radio_route
    owner = _NativeOwner(caps,clock); now = owner.check(); context = owner.context
    require(caps.policy.qualification_kind == KIND and caps.unit.unit in ('unit-a','unit-c')
        and type(index) is int and 0 <= index < 5 and type(issued_epoch_us) is int and type(expires_epoch_us) is int
        and context.value['original_epoch_us'] <= issued_epoch_us <= now[0] < expires_epoch_us
        and 0 < expires_epoch_us-issued_epoch_us <= 180000000
        and expires_epoch_us+10000000 <= caps.policy.expires_epoch_us, 'fast_stage_window_changed')
    rows,_ = caps._validate()
    require(len(rows) == 2*index, 'fast_previous_part_or_duplicate_admission')
    with Reader() as reader:
        if index == 0: require(previous_proof is None, 'fast_unexpected_previous_proof')
        else:
            prior = context.value['stages'][index-1]
            previous = [r for r in rows if parse(r[1])['child']['metadata']['campaign_id'] == prior['campaign_id']]
            require(len(previous) == 2 and {parse(r[1])['child']['metadata']['format'] for r in previous}
                == {'MR1.request','MR1.response'}, 'fast_previous_pair_missing')
            runs = [_completed_run(caps,reader,row) for row in previous]
            target,claim,plan,result = next(v for v in runs if v[1]['child']['metadata']['format'] == 'MR1.response')
            meta = claim['child']['metadata']; secret = reader.read(target/'secret.bin',32)
            require(hashlib.sha256(secret).hexdigest() == meta['secret_sha256'], 'fast_previous_secret_changed')
            grant = BenchGrant(SessionKey(PeerID(**plan['initiator']),PeerID(**plan['responder']),
                bytes.fromhex(meta['run_id']),bytes.fromhex(plan['generation'])),'bench_transfer',
                hashlib.sha256(canonical(radio_route(plan))).digest(),plan['issued_epoch_us'],plan['expires_epoch_us'],208,plan['max_frames'],secret)
            expected = dict(_binding(caps,plan,grant,meta),schema=VERIFIED_PART,
                chunk_sha256=context.expected['parts'][index-1]['chunk_sha256'],whole_sha256=context.value['whole_sha256'])
            _verify(previous_proof,expected,secret)
            if caps.unit.unit == 'unit-a':
                saved = parse(reader.read(target/'verified-piece.json',100000))
                require(saved['fast_progress_proof'] == previous_proof and saved['publisher_verified'] is True,
                    'fast_previous_verified_receipt_missing')
                receiver = _receiver(owner,create=False); require(receiver is not None,'fast_previous_part_missing')
                with receiver: receiver._live(); receiver._part(index-1)
        owner.check(); reader.check()
        value = dict(context.value['stages'][index],schema=STAGE,parent_id=caps.policy.parent_id,
            policy_digest=caps.policy.digest,unit=caps.unit.unit,bundle_context_sha256=caps.policy.bundle_context_sha256,
            issued_epoch_us=issued_epoch_us,expires_epoch_us=expires_epoch_us,handoff_end_epoch_us=expires_epoch_us+10000000,
            native_original_epoch_us=now[0],native_original_monotonic_us=now[1],
            native_deadline_monotonic_us=min(caps._enrollment['deadline_monotonic_us'],now[1]+expires_epoch_us-now[0]),
            previous_verified_proof_sha256=None if previous_proof is None else hashlib.sha256(canonical(previous_proof)).hexdigest())
        from lantern_meshticulum.ip.node import _write
        _write(caps.path.parent/('fast-stage-'+str(index)+'.json'),canonical(value))
        after=owner.check();reader.check();stage_receipt(caps,index,expires_epoch_us,after)
        return value


def native_admit(unit, parent_id, index, issued, expires, previous_proof):
    from .pilot_native_caps import _open_existing
    from lantern_meshticulum.ip.node import _facts, clock
    state=Path('/var/lib/meshticulum-bench'); facts=_facts(unit)
    caps=_open_existing(state,identifier(parent_id),facts,clock)
    try:
        value=admit_stage(caps,index,issued,expires,previous_proof,clock=clock)
        require(caps.public32_owner_current() is True,'fast_stage_owner_not_current')
        return dict(schema='meshticulum.native-fast-stage-admitted.v1',unit=unit,parent_id=parent_id,
            admission=value,radio_opened=False,arm_commands=0,tx_commands=0,refunds=0)
    finally:require(caps.close(),'fast_stage_owner_close_unproved')


def relay_final_ack(operator, row, pair, *, expected_claim, boots):
    """Relay only a successful terminal sender's exact native completion proof."""
    sender,receiver,run=pair['sender'],pair['receiver'],pair['run_id']
    status=operator.status(sender,run); result=operator.collect(sender,run)
    expected_start=dict(expected_claim,schema='meshticulum.native-parent-start.v1',started=True)
    require(status['live'] is False and status['unit']==sender and status['run_id']==run
        and status['host_boot_id']==boots[sender] and result['parent']==expected_start
        and result['unit']==sender and result['host_boot_id']==boots[sender] and result['session_id']==run
        and result['sends'] is True and result['complete'] is True and result['disarmed_on_exit'] is True
        and result.get('stop_requested',False) is False and not result.get('cleanup_errors')
        and result['service_format']==pair['format']
        and {k:result['object'][k] for k in ('bytes','sha256')}==pair['descriptor']
        and result['original_application_expires_epoch_us']==expected_claim['application_expires_epoch_us']
        and result['grant_context_sha256']==expected_claim['grant_context_sha256']==status['grant_context_sha256']
        and result['native_job']['pid']==status['pid'] and result['native_job']['start_ticks']==status['start_ticks']
        and result['attempt_journal']['fenced'] is False and result['flow']['complete'] is True,
        'fast_sender_terminal_completion_unproved')
    proof=result['fast_final_ack_proof']; pin=hashlib.sha256(canonical(proof)).hexdigest()
    delivered=operator.fast_complete(receiver,run,proof)
    require(delivered['schema']=='meshticulum.native-fast-completion-delivered.v1'
        and delivered['unit']==receiver and delivered['run_id']==run and delivered['delivered'] is True
        and delivered['proof_sha256']==pin and delivered['complete'] is False
        and delivered['radio_opened'] is False and delivered['arm_commands']==delivered['tx_commands']==0,
        'fast_completion_delivery_unproved')
    return dict(sender=sender,receiver=receiver,run_id=run,proof_sha256=pin,delivery=delivered)


from .pilot_bundle import BundleCampaign


class FastBundleCampaign(BundleCampaign):
    """Progress-driven controller; no fixed inter-part sleeps or renewed clocks."""
    __slots__=('__mcu_owner',)
    def __setattr__(self,name,value):
        if name=='_FastBundleCampaign__mcu_owner' and hasattr(self,name):
            raise RuntimeError('fast_mcu_constructor_choice_rebound')
        object.__setattr__(self,name,value)
    def __delattr__(self,name):
        if name=='_FastBundleCampaign__mcu_owner':raise RuntimeError('fast_mcu_constructor_choice_rebound')
        object.__delattr__(self,name)
    def __init__(self,*args,mcu_owner=None,**kwargs):
        self._mcu_owner_bytes=self._mcu_owner_ref=None
        if mcu_owner is not None:
            from .mcu_artifact import decode_owner, require_selector_scope
            decode_owner(mcu_owner);require_selector_scope(KIND)
            self._mcu_owner_bytes=self._mcu_owner_ref=canonical(mcu_owner)
        self.__mcu_owner=self._mcu_owner_bytes
        super().__init__(*args,**kwargs)
        require(self.parent.policy.qualification_kind==KIND,'fast_bundle_kind_required')
        if mcu_owner is None:require_bindings()
        else:self._mcu_current()

    def _mcu_current(self):
        require(self._mcu_owner_bytes is self._mcu_owner_ref is self.__mcu_owner,'fast_mcu_original_owner_rebound')
        if self._mcu_owner_bytes is not None:
            original=json.loads(self._mcu_owner_bytes)
            context=require_owner(original,self.parent.policy)
            require(canonical(context.value)==canonical(self.context.value),'fast_mcu_original_context_changed')

    def current(self):
        self._mcu_current();super().current();self._mcu_current()

    def transfer_part(self,index,response):
        from .pilot_controller import Controller
        self.current(); status=self.status()
        require(type(index) is int and index==len(self.parts) and 0<=index<5
            and status['verified_parts']==list(range(index)), 'fast_part_reused_or_out_of_order')
        request,entry=self.context.exchange(index); declared=self.context.value['stages'][index]
        require(type(response) is bytes and len(response)==declared['response_bytes']
            and hashlib.sha256(response).hexdigest()==declared['response_sha256'],'fast_response_changed')
        self.context.transport.decode_response(index,request,response)
        issued=self.epoch(); expires=min(issued+180000000,self.parent.policy.expires_epoch_us-10000000)
        require(expires-issued>20000000,'fast_original_whole_time_insufficient')
        previous=None if index==0 else self.parts[-1]['fast_progress_proof']
        receipts={}
        for unit in ('unit-c','unit-a'):
            self.current()
            value=self.operator.fast_admit(unit,self.parent.policy.parent_id,index,issued,expires,previous)
            admission=value['admission']
            require(value['schema']=='meshticulum.native-fast-stage-admitted.v1'
                and value['unit']==unit and value['parent_id']==self.parent.policy.parent_id
                and value['radio_opened'] is False and value['arm_commands']==value['tx_commands']==0
                and admission['schema']==STAGE and admission['index']==index
                and admission['bundle_context_sha256']==self.parent.policy.bundle_context_sha256
                and admission['issued_epoch_us']==issued and admission['expires_epoch_us']==expires
                and {k:admission[k] for k in declared}==declared,'fast_stage_receipt_rebound')
            receipts[unit]=value;self.current()
        original_handoff=self.parent.policy.original_monotonic_us+expires+10000000-self.parent.policy.issued_epoch_us
        controller=Controller(self.operator,campaign_id=declared['campaign_id'],resource_sha256=self.context.value['manifest_sha256'],
            expires_epoch_us=expires,handoff_deadline_monotonic=min(self.handoff,original_handoff/1000000),
            peers=self.peers,boots=self.boots,parent=self.parent,radio_permission={'country':'MY','antenna_confirmed':True},
            clock=self.clock,epoch=self.epoch,sleep=self.sleep,public32=True,lora_sender_first=self.lora_sender_first,
            bundle_context=self.context.value,radio_profile=GFSK250_NAME,
            **({'mcu_owner':json.loads(self._mcu_owner_bytes)} if self._mcu_owner_bytes is not None else {}))
        self.controllers.append(controller)
        verified=None
        try:
            verified=controller.direct_piece(request,response,'lora',publisher='unit-c',requester='unit-a')
            part=verified['bundle_part']
            require(part['part_index']==index and part['useful_decoded_bytes']==entry['raw_bytes']
                and part['bundle_context_sha256']==self.parent.policy.bundle_context_sha256
                and type(verified.get('fast_progress_proof')) is dict,'fast_native_accept_missing')
            self.current();require(self.status()['verified_parts']==list(range(index+1)),'fast_native_part_missing')
        finally:
            cleanup=controller.retire();self.links.extend(controller.links)
            require(all('stop_error' not in row and 'retirement_error' not in row for row in cleanup),
                'fast_part_cleanup_unproved')
            for link in controller.links:
                expected=set(link['attempted'])|set(link['prepared'])
                require(set(link['retired'])==expected,'fast_part_retirement_unproved')
                for unit in expected:
                    matches=[r for r in cleanup if r.get('unit')==unit and r.get('run_id')==link['run_id']]
                    require(matches and all(r.get('retirement',{}).get('retired') is True for r in matches),
                        'fast_part_retirement_receipt_missing')
        self.current()
        require(verified is not None and len(controller.links)==2 and all(v['phase']=='complete' for v in controller.links),
            'fast_part_completion_unproved')
        verified['fast_stage_admissions']=receipts
        self.parts.append(verified)
        return verified
