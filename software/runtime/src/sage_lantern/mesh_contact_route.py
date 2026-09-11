"""Production root adapter: live owner capability plus per-request guard exchange.

No provider initialization, radio calls, enrollment or root-UID consent bypass.
Only token fingerprints survive a request; pending previews are session-bound.
"""

import hashlib
import http.client
import json
import re
import secrets
import threading
import time

from .device_access import MARKER, _marker_state
from .device_profile import DeviceProfile
from .mesh_contact_forwarding import OP, ROUTES, validate_payload
from .mesh_contact_issuance import canonical, preview, require
from .mesh_contact_owner import ContactIssuanceLedger, OwnerContactHandlers
from .mesh_contact_reconciliation import OwnerContactReconciliation
from .mesh_voice_ipc import receive, send
from .suspend_io import unique_object
from .workflow_store import WorkflowStore


def owner_snapshot(token):
    require(_marker_state(MARKER) is True)
    connection = http.client.HTTPConnection('127.0.0.1', 3196, timeout=1)
    try:
        connection.request('POST', '/owner-contact-snapshot', json.dumps({'token': token}),
            {'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(1025)
        require(response.status == 200 and len(raw) <= 1024)
        value = json.loads(raw, object_pairs_hook=unique_object)
        require(type(value) is dict and set(value) == {'schema', 'epoch', 'profile'}
            and value['schema'] == 'sage.lantern.owner-contact-snapshot.v1'
            and type(value['epoch']) is str and re.fullmatch(r'[0-9a-f]{32}', value['epoch']) is not None)
        epoch, value = value['epoch'], value['profile']
        require(type(value) is dict and set(value) == {'version', 'configured', 'display_name',
            'public_discoverable', 'revision', 'setup_required'} and value['setup_required'] is False)
        profile = DeviceProfile(**{key: item for key, item in value.items() if key != 'setup_required'})
    finally:
        connection.close()
    require(_marker_state(MARKER) is True)
    return epoch, profile


class OwnerContactRoute:
    def __init__(self, *, snapshot=owner_snapshot, clock=time.time):
        self.snapshot, self.clock = snapshot, clock
        self.mutex = threading.Lock()
        self.scope = self.issuance = self.reconciliation = self.context = None

    def _active(self):
        return self.context is not None and self.context['guard']() is True

    def _profile(self):
        require(self.context is not None and self.context['local']() is True)
        return self.context['profile']

    def _binding(self):
        require(self.context is not None and self.context['local']() is True)
        return self.context['binding']

    def invalidate(self):
        if self.issuance is not None:
            self.issuance.pending = None
            self.reconciliation.pending = None
        self.scope = None

    def handle(self, connection, request, *, resource, supervisor, uid):
        require(self.mutex.acquire(blocking=False))
        try:
            require(type(uid) is int and uid > 0 and uid == resource.config['voice_uid'])
            require(type(request) is dict and set(request) == {'op', 'token', 'epoch', 'request'} and request['op'] == OP)
            inner = request['request']
            require(type(inner) is dict and 'op' in inner)
            paths = [path for path, contract in ROUTES.items() if contract[0] == inner['op']]
            require(len(paths) == 1)
            validate_payload(paths[0], dict(token=request['token'], **{key: value for key, value in inner.items() if key != 'op'}))
            epoch, profile = self.snapshot(request['token'])
            provider = resource._provider
            require(epoch == request['epoch'] and resource.active() is True and provider is not None)
            binding = dict(broker_id=supervisor.broker_id, epoch=epoch,
                config_sha256=hashlib.sha256(json.dumps(resource.config, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                chain_id=resource.config['chain_id'], agent_id=resource.identity.agent_id,
                radio_public_key=resource.config['radio']['public_key'])
            preview(profile, binding, previous_revision=0, now=int(self.clock()), ttl_seconds=1,
                findable=False, messaging_allowed=False)
            deadline = time.monotonic() + 45
            failed = False
            def local():
                return (not failed and time.monotonic() < deadline and resource.active() is True
                    and resource._provider is provider and supervisor.broker_id == binding['broker_id']
                    and hashlib.sha256(json.dumps(resource.config, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == binding['config_sha256'])
            def guard():
                nonlocal failed
                try:
                    require(local())
                    nonce = secrets.token_hex(16)
                    send(connection, {'guard': nonce})
                    answer = receive(connection, 256)
                    require(answer == {'guard': nonce, 'allowed': True})
                    require(self.snapshot(request['token']) == (epoch, profile) and local())
                    return True
                except Exception:
                    failed = True
                    return False
            require(resource._mutex.acquire(blocking=False))
            try:
                self.context = dict(guard=guard, local=local, profile=profile, binding=binding)
                scope = (id(resource), id(provider), supervisor.broker_id, epoch, hashlib.sha256(request['token'].encode()).hexdigest())
                if self.scope != scope:
                    self.invalidate()
                    ledger = ContactIssuanceLedger(WorkflowStore(resource.probe.client, storage=resource.probe),
                        resource.identity, access_check=self._active, clock=self.clock)
                    self.issuance = OwnerContactHandlers(ledger, read_profile=self._profile,
                        read_binding=self._binding, access_check=self._active, clock=self.clock)
                    self.reconciliation = OwnerContactReconciliation(ledger, read_profile=self._profile,
                        read_binding=self._binding, access_check=self._active, clock=self.clock)
                    self.scope = scope
                require(guard())
                operation = dict(inner, broker_id=binding['broker_id'], epoch=epoch)
                handler = self.reconciliation if inner['op'].startswith('owner-contact-reconcile-') else self.issuance
                result = handler.handle(operation, peer_uid=0)
                if inner['op'].endswith('-cancel'):
                    self.invalidate()
                require(guard())
                send(connection, {'result': result})
            finally:
                self.context = None
                resource._mutex.release()
        except BaseException:
            self.invalidate()
            raise
        finally:
            self.mutex.release()
