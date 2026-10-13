# SPDX-License-Identifier: Apache-2.0
"""Independent signed public-resource handling at native pilot endpoints.

These operations open no transport. A native root entry supplies fixed private
state and independently observed facts/clock. Quarantine is not a verified piece:
MR1 request binding and publisher/chunk checks must both pass before caching.
"""
from contextlib import contextmanager
import fcntl
import os
import hashlib
import json
from pathlib import Path

from meshticulum.ip.journal import private
from lantern_meshticulum.ip.node import _read, _write
from meshticulum.resources import lamplighter as ll
from meshticulum.resources import resource
from lantern_meshticulum.bench.pilot_context import original_context
from lantern_meshticulum.bench.pilot_provision import identifier
from meshticulum.bench.wire import require
from meshticulum.bench.public_block import public32_service,public_mb1_service


def _captured(state, run_id, *, facts, format, clock, public32=False):
    directory = Path(state) / 'runs' / identifier(run_id)
    private(directory.lstat(), True)
    result = json.loads(_read(directory / 'result.json', 100000))
    require(result.get('schema') in ('meshticulum.physical-service-run.v1', 'meshticulum.ip-public-node-run.v1')
            and result.get('unit') == facts['unit'] and result.get('host_boot_id') == facts['host_boot_id']
            and result.get('complete') is True and result.get('sends') is False
            and result.get('service_format') == format and result.get('service_object_durable') is True,
            'pilot_resource_requires_local_authenticated_capture')
    epoch, mono = clock()
    require(epoch < result['original_application_expires_epoch_us']
            and mono < result['original_application_deadline_us'], 'pilot_resource_original_deadline_expired')
    data = _read(directory / 'receiver.bin', 65536)
    require(hashlib.sha256(data).hexdigest() == result['service_object_sha256'], 'pilot_capture_changed')
    if public32:
        plan = json.loads(_read(directory/'grant.json',16384))
        require(public32_service(plan.get('service',{})) and plan['service']['format']==format
                and plan['object']['bytes']==len(data) and plan['object']['sha256']==hashlib.sha256(data).hexdigest()
                and result['parent']['native_plan_sha256']==hashlib.sha256(
                    json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'public32_capture_grant_rebound')
    return data, result


@contextmanager
def captured(state, run_id, *, facts, format, clock, public32=False):
    directory = Path(state) / 'runs' / identifier(run_id)
    private(directory.lstat(), True)
    fd = os.open(directory / 'active', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        private(os.fstat(fd))
        identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        def current():
            info = (directory / 'active').lstat(); private(info)
            require((info.st_dev, info.st_ino) == identity and os.pread(fd, 16, 0) == b'active\n',
                    'pilot_capture_authority_retired_or_changed')
        current()
        value = _captured(state, run_id, facts=facts, format=format, clock=clock, public32=public32)
        yield value
        current()
        require(_captured(state, run_id, facts=facts, format=format, clock=clock, public32=public32) == value,
                'pilot_capture_changed_during_use')
    finally:
        os.close(fd)


def _transport_part(transport,request):
    from .pilot_bundle_transport import MB1Transport
    require(type(transport) is MB1Transport,'protected_mb1_transport_required')
    transport._check()
    selected=[i for i in range(len(transport._parts)) if transport.request(i)==request]
    require(len(selected)==1,'mb1_request_not_in_transport_descriptor')
    return selected[0]


def _capture_transport(state,run,expected):
    plan=json.loads(_read(Path(state)/'runs'/identifier(run)/'grant.json',16384))
    require(public_mb1_service(plan.get('service',{}))==expected,'native_capture_transport_changed')


@contextmanager
def _native_capture(state,run,*,facts,format,clock,public32,transport,owner):
    if transport is None:
        require(owner is None,'unexpected_mb1_native_owner')
        with captured(state,run,facts=facts,format=format,clock=clock,public32=public32) as value:yield value
        return
    from .pilot_bundle import _NativeOwner,_HeldCapture
    from .profiles import GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND
    require(public32 and type(owner) is _NativeOwner and owner.context.transport is transport
        and owner.caps.policy.qualification_kind in (GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND) and owner.clock is clock
        and owner.state==Path(state).absolute() and owner.caps.unit.unit==facts['unit'],
        'protected_mb1_native_owner_required')
    with _HeldCapture(owner,run,sends=False,format=format) as held:
        stage=owner.stage(held.meta['campaign_id'],held.meta['application_expires_epoch_us'])
        require(public_mb1_service(held.plan['service'])==owner.context.value['transport_descriptor_sha256'],
            'native_capture_transport_changed')
        if format=='MR1.request':require(held.payload==transport.request(stage['index']),'native_mb1_request_changed')
        else:
            part=transport.part(stage['index'])
            require(len(held.payload)==part['response_bytes'] and hashlib.sha256(held.payload).hexdigest()==part['response_sha256'],
                'native_mb1_response_changed')
        held.check();yield held.payload,held.result;held.check();owner.check()


def serve_chunk(state, request_run_id, *, facts, clock, seed, trust, manifest_sha256, public32=False,mb1_transport=None,mb1_owner=None):
    require(not public32 or facts['unit']=='unit-c', 'public32_wrong_publisher')
    with _native_capture(state,request_run_id,facts=facts,format='MR1.request',clock=clock,public32=public32,
        transport=mb1_transport,owner=mb1_owner) as (request, receipt):
        path, offset, size = resource.decode_request(request, public32=public32)
        _capture_transport(state,request_run_id,mb1_transport._pin if mb1_transport is not None else None) if public32 else None
        require(not public32 or (offset==0 and (mb1_transport is not None or size==32740)), 'public32_piece_not_whole')
        require(path.startswith('/packs/' + manifest_sha256 + '/chunks/'), 'pilot_only_serves_selected_public_chunk')
        cache = ll.VerifiedCache(seed, trust, manifest_sha256)
        response = (mb1_transport.response(_transport_part(mb1_transport,request),cache) if mb1_transport is not None
                    else resource.BlockServer(cache.read, public32=public32).handle(request))
        _, _, status, _, _ = resource.RESPONSE.unpack(response[:resource.RESPONSE.size])
        require(status == 0 and (not public32 or mb1_transport is not None or len(response)==32768), 'pilot_requested_chunk_unavailable')
        if mb1_transport is not None:mb1_transport.decode_response(_transport_part(mb1_transport,request),request,response)
        if mb1_owner is not None:mb1_owner.check()
        return response

def verify_chunk(state, response_run_id, *, facts, clock, request, campaign_id,
                 manifest_sha256, destination, trust, request_deadline_us=None, public32=False,mb1_transport=None,mb1_owner=None):
    require(not public32 or facts['unit']=='unit-a', 'public32_wrong_recipient')
    with _native_capture(state,response_run_id,facts=facts,format='MR1.response',clock=clock,public32=public32,
        transport=mb1_transport,owner=mb1_owner) as (response, receipt):
        context_directory = Path(state) / 'campaigns' / identifier(campaign_id)
        original = original_context(context_directory, unit=facts['unit'], host_boot_id=facts['host_boot_id'],
            resource_sha256=manifest_sha256, expires_epoch_us=receipt['original_application_expires_epoch_us'], clock=clock)
        deadline = min(original['application_deadline_monotonic_us'], receipt['original_application_deadline_us'])
        if request_deadline_us is not None:
            require(type(request_deadline_us) is int and 0 < request_deadline_us < 2**63,
                    'invalid_pilot_original_request_deadline')
            deadline = min(deadline, request_deadline_us)
        path, offset, size = resource.decode_request(request, public32=public32)
        if public32:
            _capture_transport(state,response_run_id,mb1_transport._pin if mb1_transport is not None else None)
            require(offset==0 and (mb1_transport is not None or (size==32740 and len(response)==32768)), 'public32_piece_not_whole')
        require(offset == 0 and path.startswith('/packs/' + manifest_sha256 + '/chunks/')
                and path.endswith('.json'), 'pilot_verification_requires_whole_selected_chunk')
        magic, binding, status, total, at = resource.RESPONSE.unpack(response[:resource.RESPONSE.size])
        raw = response[resource.RESPONSE.size:]
        require(magic == resource.MAGIC and binding == hashlib.sha256(request).digest()[:16]
                and status == 0 and at == 0 and len(raw) == total and total <= size,
                'pilot_response_request_binding_mismatch')
        destination = Path(destination)
        private(destination.lstat(), True)
        manifest = ll.manifest(_read(destination / 'manifest.json', ll.MAX_MANIFEST),
            _read(destination / 'manifest.sig.json', 4096), trust, manifest_sha256)
        digest = path.split('/')[-1][:-5]
        entries = [entry for entry in manifest['chunks'] if entry['sha256'] == digest]
        require(len(entries) == 1, 'pilot_chunk_not_in_signed_manifest')
        if mb1_transport is not None:
            require(mb1_transport._manifest_raw==_read(destination/'manifest.json',ll.MAX_MANIFEST)
                and mb1_transport._signature_raw==_read(destination/'manifest.sig.json',4096)
                and mb1_transport._digest==manifest_sha256,'native_mb1_publisher_metadata_changed')
            raw=mb1_transport.decode_response(_transport_part(mb1_transport,request),request,response)
        record = ll.chunk(raw, entries[0])
        ll.kp.validate_pack_rules(manifest, [record])
        epoch, mono = clock()
        require(epoch < original['application_expires_epoch_us']
                and mono < deadline,
                'pilot_resource_original_deadline_expired')
        if mb1_owner is not None:mb1_owner.check()
        store = destination / 'chunks'
        store.mkdir(mode=0o700, exist_ok=True)
        private(store.lstat(), True)
        target = store / (digest + '.json')
        reused = False
        if target.exists() or target.is_symlink():
            existing = _read(target, ll.MAX_CHUNK)
            ll.chunk(existing, entries[0])
            require(existing == raw, 'pilot_verified_piece_changed')
            reused = True
        else:
            _write(target, raw)
        if mb1_owner is not None:mb1_owner.check()
        result=dict(schema='meshticulum.pilot-verified-piece.v1', unit=facts['unit'],
            response_run_id=response_run_id, campaign_id=campaign_id, manifest_sha256=manifest_sha256,
            chunk_sha256=digest, useful_verified_bytes=len(raw), publisher_verified=True,
            reused=reused, private_admission=False, sage_imported=False,
            original_application_expires_epoch_us=original['application_expires_epoch_us'],
            original_application_deadline_us=deadline)
        if mb1_transport is not None:
            result.update(transport_representation='MB1',transport_descriptor_sha256=mb1_transport._pin,
                response_wire_bytes=len(response),reconstructed_canonical_bytes=len(raw))
        return result
