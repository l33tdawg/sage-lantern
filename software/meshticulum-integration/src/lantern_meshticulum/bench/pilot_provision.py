# SPDX-License-Identifier: Apache-2.0
"""Native preparation for finite public LoRa/TCP children, never activation.

The caller supplies the independently checked host facts and clocks. Linux-root
entry points acquire those themselves. Relay bytes come from this host's prior
authenticated public quarantine, never an operator-provided replacement body.
"""
from contextlib import ExitStack
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from meshticulum.contracts.identity import PeerID
from meshticulum.ip.authority import grant_from_plan
from meshticulum.ip.journal import private
from lantern_meshticulum.ip.node import _read, _write
from lantern_meshticulum.bench.pilot_context import original_context
from meshticulum.bench.services import _shape
from meshticulum.bench.public_block import (public32_service, public_mb1_service, object_limit,
    REPRESENTATION_MARKER,TRANSPORT_DESCRIPTOR_MARKER)
from meshticulum.bench.wire import require
from .pilot_parent_caps import BUNDLE_KINDS, PUBLIC32_KINDS
from meshticulum.resources import resource


def identifier(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{32}', value) and int(value, 16) != 0,
            'invalid_pilot_identifier')
    return value


def _directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    private(path.lstat(), True)
    return path


def _history_guarded(state, caps):
    """Legacy absence is allowed; any guarded declaration must be complete."""
    parent = state / 'parents' / caps.policy.parent_id
    enrolled_binding = caps.history_binding_sha256
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    def stamp(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'pilot_duplicate_history_key'); result[key] = value
        return result
    try:
        parent_identity = os.fstat(fd); private(parent_identity, True)
        values, raw_binding = {}, None
        for name in ('owner.json', 'prior.json', 'history-binding.json'):
            try:
                source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
            except FileNotFoundError:
                values[name] = None; continue
            try:
                original = os.fstat(source); private(original)
                maximum = 16384 if name == 'history-binding.json' else 200000
                require(original.st_size <= maximum, 'pilot_history_metadata_too_large')
                raw = os.pread(source, maximum + 1, 0)
                require(len(raw) == original.st_size and stamp(original) == stamp(os.fstat(source))
                        == stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)), 'pilot_history_metadata_changed')
                value = json.loads(raw, object_pairs_hook=unique)
                require(type(value) is dict, 'invalid_pilot_history_metadata')
                values[name] = value
                if name == 'history-binding.json': raw_binding = raw
                if name == 'prior.json':
                    require(hashlib.sha256(raw).hexdigest() == caps.unit.prior_snapshot_sha256,
                            'pilot_history_prior_mismatch')
            finally:
                os.close(source)
        private(parent.lstat(), True)
        require(stamp(os.fstat(fd))[:4] == stamp(parent_identity)[:4] == stamp(parent.lstat())[:4],
                'pilot_history_parent_rebound')
        key = 'history_binding_sha256'
        declared = [item is not None and key in item for item in (values['owner.json'], values['prior.json'])]
        if enrolled_binding is None and raw_binding is None and not any(declared): return False
        require(all(declared) and raw_binding is not None, 'pilot_incomplete_history_binding')
        expected = values['owner.json'][key]
        require(type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected)
                and values['prior.json'][key] == expected
                and enrolled_binding == expected
                and hashlib.sha256(raw_binding).hexdigest() == expected, 'pilot_history_binding_mismatch')
        binding = values['history-binding.json']
        require(binding.get('schema') == 'meshticulum.private-resume-history-binding.v2'
                and binding.get('parent_id') == caps.policy.parent_id
                and binding.get('unit') == caps.unit.unit, 'invalid_pilot_history_binding')
        return True
    finally:
        os.close(fd)


def provision(package, *, state, facts, clock):
    from lantern_meshticulum.bench.pilot_native_caps import open_for_provision
    caps = open_for_provision(state, facts, clock, package)
    try:
        with ExitStack() as leases:
            result = _provision(package, state=state, facts=facts, clock=clock, leases=leases, caps=caps)
            require(caps.current() is True, 'pilot_parent_not_current')
            return result
    finally:
        caps.close()


def _provision(package, *, state, facts, clock, leases, caps):
    fields = {'schema', 'unit', 'campaign_id', 'resource_sha256', 'application_expires_epoch_us',
              'transport', 'grant', 'secret_base64', 'payload_base64', 'source_run_id', 'parent'}
    require(type(package) is dict and set(package) == fields
            and package['schema'] == 'meshticulum.public-pilot-package.v2', 'invalid_pilot_package')
    require(package['unit'] == facts['unit'] and package['transport'] in ('lora', 'tcp'), 'wrong_pilot_unit_or_transport')
    identifier(package['campaign_id'])
    state = Path(state).absolute()
    private(state.lstat(), True)
    if caps.policy.qualification_kind in PUBLIC32_KINDS:
        require(caps.public32_owner_current() is True, 'public32_owner_not_current')
    # This independently enrolled local owner charges the complete original
    # child before any campaign context or run can be created. Package input
    # cannot enroll a parent or replace its approval/prior-debt observations.
    preparation = caps.prepare_claim(package)
    require(caps.current() is True, 'pilot_parent_not_current')
    contexts = _directory(state / 'campaigns')
    directory = _directory(contexts / package['campaign_id'])
    original = original_context(directory, unit=facts['unit'], host_boot_id=facts['host_boot_id'],
        resource_sha256=package['resource_sha256'], expires_epoch_us=package['application_expires_epoch_us'], clock=clock)
    require(caps.current() is True, 'pilot_parent_not_current')
    plan = package['grant']
    require(type(plan) is dict and plan['host_boot_id'] == facts['host_boot_id'], 'pilot_wrong_boot')
    plan = dict(plan)
    require(plan.get('application_deadline_monotonic_us') is None, 'operator_cannot_replace_native_deadline')
    plan['application_deadline_monotonic_us'] = min(original['application_deadline_monotonic_us'],
        preparation.application_deadline_monotonic_us)
    secret = base64.b64decode(package['secret_base64'], validate=True)
    require(len(secret) == 32, 'invalid_pilot_secret')
    if package['transport'] == 'tcp':
        local_peer = PeerID(**plan['local_peer'])
        grant = grant_from_plan(plan, secret, local_peer=local_peer, host_boot_id=facts['host_boot_id'])
        format = grant.format
        sends = grant.direction(local_peer) == 0
        run_id = identifier(grant.session.session_id.hex())
        descriptor = dict(bytes=grant.object_bytes, sha256=grant.object_sha256.hex())
        expires = grant.application_expires_epoch_us
    else:
        require(plan.get('schema') == 'meshticulum.service-run.v1' and plan.get('unit') == facts['unit']
                and plan.get('country') == 'MY' and plan.get('antenna_confirmed') is True, 'invalid_public_radio_pilot')
        service = plan['service']
        require(type(service) is dict and set(service) in ({'kind', 'format', 'application_expires_epoch_us'},
                {'kind', 'format', 'application_expires_epoch_us', 'public_response_max_bytes'},
                {'kind', 'format', 'application_expires_epoch_us', 'public_response_max_bytes',
                 REPRESENTATION_MARKER,TRANSPORT_DESCRIPTOR_MARKER})
                and service['kind'] == 'knowledge', 'invalid_public_radio_service')
        format = service['format']
        sends = plan['sender_unit'] == facts['unit']
        run_id = identifier(plan['session_id'])
        descriptor = plan['object']
        expires = service['application_expires_epoch_us']
    require(format in ('MR1.request', 'MR1.response') and expires == original['application_expires_epoch_us'],
            'pilot_requires_original_public_format_expiry')
    epoch, mono = clock()
    require(type(plan['issued_epoch_us']) is int and type(plan['expires_epoch_us']) is int
            and plan['issued_epoch_us'] <= epoch < expires <= plan['expires_epoch_us']
            and plan['expires_epoch_us'] - plan['issued_epoch_us'] <= 240000000
            and mono < plan['application_deadline_monotonic_us'], 'pilot_grant_not_current')
    public32 = public32_service(plan.get('service',{}))
    mb1_pin=public_mb1_service(plan.get('service',{}))
    from .profiles import GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND,GFSK250_FAST_BUNDLE_KIND
    require((mb1_pin is not None)==(caps.policy.qualification_kind in (GFSK_MB1_BUNDLE_KIND,GFSK250_MB1_BUNDLE_KIND,GFSK250_FAST_BUNDLE_KIND)),
        'mb1_parent_kind_mismatch')
    require(not public32 or (caps.policy.qualification_kind in PUBLIC32_KINDS
            and package['transport'] == 'lora'), 'public32_parent_kind_mismatch')
    require(type(descriptor) is dict and type(descriptor['bytes']) is int
            and 1 <= descriptor['bytes'] <= (object_limit(plan.get('service',{})) if package['transport'] == 'lora' else 65536)
            and re.fullmatch('[0-9a-f]{64}', descriptor['sha256']), 'invalid_pilot_object')
    source_run = package['source_run_id']
    payload = None
    source_basis = 'none_receiver'
    if not sends:
        require(package['payload_base64'] is None and source_run is None, 'receiver_must_not_have_management_payload')
    elif source_run is not None:
        require(package['payload_base64'] is None, 'relay_cannot_use_operator_payload')
        from lantern_meshticulum.bench.pilot_resources import captured, serve_chunk
        if type(source_run) is dict:
            require(set(source_run) == {'kind', 'request_run_id'}
                    and source_run['kind'] == 'served_public_chunk' and format == 'MR1.response',
                    'invalid_pilot_native_response_source')
            identifier(source_run['request_run_id'])
            _, receipt = leases.enter_context(captured(state, source_run['request_run_id'],
                facts=facts, format='MR1.request', clock=clock, public32=public32))
            public = state / 'public' / package['resource_sha256']
            private(public.lstat(), True)
            trust = json.loads(_read(public / 'trust.json', 16384))
            mb1_owner=None
            if mb1_pin is not None:
                from .pilot_bundle import _NativeOwner
                mb1_owner=_NativeOwner(caps,clock)
            payload = serve_chunk(state, source_run['request_run_id'], facts=facts, clock=clock,
                seed=public / 'pack', trust=trust, manifest_sha256=package['resource_sha256'], public32=public32,
                mb1_transport=caps._approve.bundle.transport if mb1_pin is not None else None,mb1_owner=mb1_owner)
            source_basis = 'native_verified_public_store_response'
        else:
            identifier(source_run)
            require(source_run != run_id, 'relay_cannot_use_operator_payload')
            payload, receipt = leases.enter_context(captured(state, source_run, facts=facts, format=format, clock=clock))
            require(receipt.get('service_object_sha256') == descriptor['sha256'],
                    'relay_requires_local_authenticated_public_capture')
            require(type(receipt.get('parent')) is dict
                    and receipt['parent'].get('resource_sha256') == package['resource_sha256'],
                    'relay_requires_original_parent_resource')
            source_basis = 'prior_local_authenticated_public_quarantine'
        require(receipt.get('original_application_expires_epoch_us') == expires,
                'relay_requires_local_authenticated_public_capture')
        prior_deadline = receipt['original_application_deadline_us']
        plan['application_deadline_monotonic_us'] = min(plan['application_deadline_monotonic_us'], prior_deadline)
    else:
        require(format == 'MR1.request', 'pilot_response_requires_native_received_request')
        require(type(package['payload_base64']) is str, 'pilot_sender_payload_missing')
        payload = base64.b64decode(package['payload_base64'], validate=True)
        source_basis = 'operator_public_fixture'
    if payload is not None:
        require(len(payload) == descriptor['bytes'] and hashlib.sha256(payload).hexdigest() == descriptor['sha256'],
                'pilot_sender_object_mismatch')
        _shape(payload, format, public32=public32,mb1_descriptor_sha256=mb1_pin)
        if format == 'MR1.request':
            path, _, _ = resource.decode_request(payload, public32=public32)
            require(path.startswith('/packs/' + package['resource_sha256'] + '/'),
                    'pilot_request_resource_mismatch')
    require(caps.current() is True, 'pilot_parent_not_current')
    parent = caps.finalize_provision(preparation, plan)
    require(caps.current() is True, 'pilot_parent_not_current')
    runs = _directory(state / 'runs')
    target = runs / run_id
    require(not target.exists() and not target.is_symlink(), 'pilot_run_already_provisioned')
    staging = Path(tempfile.mkdtemp(prefix='.pilot-', dir=runs))
    files = {'grant.json': json.dumps(plan, sort_keys=True, separators=(',', ':')).encode() + b'\n',
             'secret.bin': secret, 'active': b'active\n'}
    if payload is not None:
        files['sender.bin'] = payload
    receipt = dict(schema='meshticulum.provisioned-public-pilot.v2', unit=facts['unit'],
        run_id=run_id, campaign_id=package['campaign_id'], transport=package['transport'],
        object=descriptor, source_basis=source_basis, source_run_id=source_run,
        original_application_deadline_us=plan['application_deadline_monotonic_us'],
        original_application_expires_epoch_us=expires, radio_opened=False, socket_opened=False,
        receiver_payload_installed=False, parent=parent)
    files['provision.json'] = json.dumps(receipt, sort_keys=True).encode() + b'\n'
    for name, raw in files.items():
        require(caps.current() is True, 'pilot_parent_not_current')
        _write(staging / name, raw)
    require(caps.current() is True, 'pilot_parent_not_current')
    guarded = _history_guarded(state, caps)
    held = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        def unchanged(path):
            original, named = os.fstat(held), path.lstat()
            private(original, True); private(named, True)
            require((original.st_dev, original.st_ino, original.st_mode, original.st_uid)
                    == (named.st_dev, named.st_ino, named.st_mode, named.st_uid),
                    'pilot_prepared_directory_rebound')
        unchanged(staging)
        if caps.policy.qualification_kind in PUBLIC32_KINDS:
            require(caps.public32_owner_current() is True, 'public32_owner_not_current')
        os.rename(staging, target)
        fd = os.open(runs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        unchanged(target)
        if guarded:
            from .resume_parent_guard import record_prepared
            from .pilot_parent_caps import canonical
            record = record_prepared(state, caps, preparation, held_staging_fd=held)
            unchanged(target)
            require(_history_guarded(state, caps) is True, 'pilot_history_binding_changed')
            # RPC-only evidence: changing provision.json would form a hash cycle.
            receipt['prepared_identity_sha256'] = hashlib.sha256(canonical(record)).hexdigest()
    finally:
        os.close(held)
    return receipt
