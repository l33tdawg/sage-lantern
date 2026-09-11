"""Encrypted issuance ledger and root handlers; shared schema admission required.

No daemon registration, transport, profile mutation, renewal or implicit retry.
Only trusted root composition supplies the ordinary signer and live readers.
"""

import hashlib
import json
import re
import secrets
import struct
import threading
import time
import uuid

from nacl.signing import VerifyKey

from . import workflow_store
from .mesh_contact_issuance import approval_digest, canonical, issue, preview, require
from .mesh_contacts import verify_contact_card


KIND = 'contact_issuance'
SCHEMA = 'sage.lantern.owner-contact-ledger.v1'
METHOD = 'LANTERN-OWNER-CONTACT-LEDGER'
PATH = '/sage-lantern/owner-contact-ledger/v1'
NAMESPACE = uuid.UUID('3889cf08-c778-4a22-875f-ef1809a14d9c')


class ContactIssuanceLedger:
    def __init__(self, store, identity, *, access_check, clock=time.time):
        require(type(store) is workflow_store.WorkflowStore and KIND in workflow_store.KINDS)
        require(identity.agent_id == store.storage.actor.agent_id)
        self.store, self.identity, self.access, self.clock = store, identity, access_check, clock
        self.owner = dict(chain_id=store.storage.actor.chain_id, agent_id=identity.agent_id)
        self.identifier = str(uuid.uuid5(NAMESPACE, canonical(self.owner).decode()))

    def _proof(self, body):
        headers = self.identity.sign_request(METHOD, PATH, canonical(body), timestamp=int(self.clock()))
        require(headers['X-Agent-ID'] == self.identity.agent_id)
        return dict(body=body, timestamp=headers['X-Timestamp'], nonce=headers['X-Nonce'], signature=headers['X-Signature'])

    def _signed_body(self, proof):
        require(type(proof) is dict and set(proof) == {'body', 'timestamp', 'nonce', 'signature'})
        require(type(proof['timestamp']) is str and re.fullmatch('[1-9][0-9]{0,15}', proof['timestamp']) is not None)
        for name, length in (('nonce', 16), ('signature', 128)):
            require(type(proof[name]) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', proof[name]) is not None)
        body = proof['body']
        message = hashlib.sha256(METHOD.encode() + b' ' + PATH.encode() + b'\n' + canonical(body)).digest()
        message += struct.pack('>q', int(proof['timestamp'])) + bytes.fromhex(proof['nonce'])
        VerifyKey(bytes.fromhex(self.identity.agent_id)).verify(message, bytes.fromhex(proof['signature']))
        return body

    def _verify(self, record):
        require(record['record_id'] == self.identifier and record['kind'] == KIND
            and record['agent_id'] == self.identity.agent_id)
        body = self._signed_body(record['payload'])
        require(type(body) is dict)
        extras = {'audit_id', 'audit_sha256', 'reconciliation_plan'} if body.get('state') == 'abandoned' else set()
        require(set(body) - extras == {'schema', 'owner', 'record_revision', 'card_revision',
            'plan_sha256', 'state', 'card'} and body['schema'] == SCHEMA and body['owner'] == self.owner
            and type(body['record_revision']) is int and body['record_revision'] == record['revision']
            and type(body['card_revision']) is int and 0 < body['card_revision'] <= workflow_store.MAX_REVISION
            and type(body['plan_sha256']) is str and re.fullmatch('[0-9a-f]{64}', body['plan_sha256']) is not None
            and body['state'] in ('reserved', 'issued', 'abandoned')
            and (type(body['card']) is str if body['state'] == 'issued' else body['card'] is None)
            and extras <= set(body))
        return body

    def current(self):
        try:
            record = self.store.get(self.identifier, kind=KIND, access_check=self.access)
        except workflow_store.WorkflowNotFound:
            return None
        body = self._verify(record)
        if body['state'] == 'abandoned':
            from .mesh_contact_reconciliation import verify_abandoned
            verify_abandoned(self, record)
        return body

    def reserve(self, previous, following, digest):
        current = self.current()
        if current is not None and current['state'] == 'abandoned':
            from .mesh_contact_reconciliation import require_completion
            require_completion(self, current)
        require((current['card_revision'] if current else 0) == previous
            and (current is None or current['state'] in ('issued', 'abandoned')) and following == previous + 1)
        revision = current['record_revision'] if current else 0
        body = dict(schema=SCHEMA, owner=self.owner, record_revision=revision + 1,
            card_revision=following, plan_sha256=digest, state='reserved', card=None)
        result = self.store.put(self.identifier, KIND, revision, self._proof(body), access_check=self.access)
        require(self._verify(result) == body)
        return True

    def finish(self, plan, card):
        current = self.current()
        require(current is not None and current['state'] == 'reserved'
            and current['plan_sha256'] == approval_digest(plan) and current['card_revision'] == plan['revision'])
        verified = verify_contact_card(card, now=self.clock())
        require(verified.agent_id == self.identity.agent_id and verified.chain_id == self.owner['chain_id']
            and verified.revision == plan['revision'] and verified.radio_public_key == plan['binding']['radio_public_key']
            and verified.findable is plan['findable'] and verified.messaging_allowed is plan['messaging_allowed'])
        body = dict(current, record_revision=current['record_revision'] + 1, state='issued', card=card.decode())
        result = self.store.put(self.identifier, KIND, current['record_revision'], self._proof(body), access_check=self.access)
        require(self._verify(result) == body)
        return card


class OwnerContactHandlers:
    def __init__(self, ledger, *, read_profile, read_binding, access_check, clock=time.time):
        require(type(ledger) is ContactIssuanceLedger)
        self.ledger, self.profile, self.binding = ledger, read_profile, read_binding
        self.access, self.clock = access_check, clock
        self.pending = None
        self.mutex = threading.Lock()

    def handle(self, request, *, peer_uid):
        require(type(peer_uid) is int and peer_uid == 0 and type(request) is dict)
        require(self.mutex.acquire(blocking=False))
        try:
            require(self.access() is True)
            binding = self.binding()
            require(request.get('broker_id') == binding['broker_id'] and request.get('epoch') == binding['epoch'])
            require(binding['chain_id'] == self.ledger.owner['chain_id']
                and binding['agent_id'] == self.ledger.owner['agent_id'])
            operation = request.get('op')
            if operation == 'owner-contact-state':
                require(set(request) == {'op', 'broker_id', 'epoch'})
                current = self.ledger.current()
                require(self.access() is True and self.binding() == binding)
                if current is None:
                    return {'state': 'absent', 'revision': 0}
                return dict(state=current['state'], revision=current['card_revision'],
                    record_revision=current['record_revision'], plan_sha256=current['plan_sha256'],
                    card_sha256=hashlib.sha256(current['card'].encode()).hexdigest() if current['card'] else None)
            if operation == 'owner-contact-preview':
                require(set(request) == {'op', 'broker_id', 'epoch', 'ttl_seconds', 'findable', 'messaging_allowed'})
                self.pending = None
                current = self.ledger.current()
                require(current is None or current['state'] in ('issued', 'abandoned'))
                if current is not None and current['state'] == 'abandoned':
                    from .mesh_contact_reconciliation import require_completion
                    require_completion(self.ledger, current)
                plan = preview(self.profile(), binding, previous_revision=current['card_revision'] if current else 0,
                    now=int(self.clock()), ttl_seconds=request['ttl_seconds'], findable=request['findable'],
                    messaging_allowed=request['messaging_allowed'])
                require(self.access() is True and self.binding() == binding)
                challenge = secrets.token_hex(32)
                self.pending = dict(challenge=challenge, plan=plan)
                return json.loads(canonical(dict(challenge=challenge, plan=plan, plan_sha256=approval_digest(plan))))
            if operation == 'owner-contact-cancel':
                require(set(request) == {'op', 'broker_id', 'epoch'})
                self.pending = None
                return {'cancelled': True}
            require(operation == 'owner-contact-confirm' and set(request) ==
                {'op', 'broker_id', 'epoch', 'challenge', 'plan_sha256', 'consent'})
            pending, self.pending = self.pending, None
            require(pending is not None and type(request['challenge']) is str
                and secrets.compare_digest(request['challenge'], pending['challenge']))
            plan = pending['plan']
            card = issue(plan, dict(consent=request['consent'], plan_sha256=request['plan_sha256']),
                identity=self.ledger.identity, read_profile=self.profile, read_binding=self.binding,
                access_check=self.access, reserve_revision=self.ledger.reserve, clock=lambda: int(self.clock()))
            card = self.ledger.finish(plan, card)
            current_plan = preview(self.profile(), self.binding(), previous_revision=plan['previous_revision'],
                now=plan['issued_at'], ttl_seconds=plan['expires_at'] - plan['issued_at'],
                findable=plan['findable'], messaging_allowed=plan['messaging_allowed'])
            require(self.access() is True and self.binding() == binding and current_plan == plan)
            verify_contact_card(card, now=self.clock())
            require(self.clock() - plan['issued_at'] <= 60)
            return dict(card=card.decode(), card_sha256=hashlib.sha256(card).hexdigest(), revision=plan['revision'])
        except BaseException:
            self.pending = None
            raise
        finally:
            self.mutex.release()
