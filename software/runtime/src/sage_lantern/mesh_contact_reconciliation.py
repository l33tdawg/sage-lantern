"""Explicit reconciliation; no daemon wiring, card signing or automatic retry.

One create-only signed authorization archives the exact reserved row before CAS.
Only the signed abandoned ledger row proves completion, not the archive alone.
"""

import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import asdict

from .mesh_contact_issuance import approval_digest, canonical, preview, require
from .mesh_contact_owner import ContactIssuanceLedger, KIND, NAMESPACE
from .mesh_contacts import verify_contact_card
from .workflow_store import WorkflowNotFound


SCHEMA = 'sage.lantern.owner-contact-reconciliation.v1'
AUDIT_SCHEMA = 'sage.lantern.owner-contact-reconciliation-audit.v1'
COMPLETION_SCHEMA = 'sage.lantern.owner-contact-reconciliation-completion.v1'


def audit_identifier(ledger, original):
    return str(uuid.uuid5(NAMESPACE, 'reconcile:' + ledger.identifier + ':' + str(original['revision'])))


def read_audit(ledger, original):
    try:
        row = ledger.store.get(audit_identifier(ledger, original), kind=KIND, access_check=ledger.access)
    except WorkflowNotFound:
        return None
    body = ledger._signed_body(row['payload'])
    require(row['revision'] == 1 and type(body) is dict and set(body) ==
        {'schema', 'owner', 'ledger_id', 'original', 'approval', 'decision'}
        and body['schema'] == AUDIT_SCHEMA and body['owner'] == ledger.owner
        and body['ledger_id'] == ledger.identifier and body['original'] == original
        and body['decision'] == 'abandon_authorized')
    validate_plan(ledger, body['approval'], original, 'abandon')
    require(ledger._verify(original)['state'] == 'reserved')
    return row


def validate_plan(ledger, plan, original, action):
    require(type(plan) is dict and set(plan) == {'schema', 'action', 'ledger_id', 'record_revision',
        'card_revision', 'original_sha256', 'original_plan_sha256', 'binding', 'profile', 'created_at', 'audit_sha256'}
        and plan['schema'] == SCHEMA and plan['action'] == action and plan['ledger_id'] == ledger.identifier
        and plan['record_revision'] == original['revision']
        and plan['card_revision'] == original['payload']['body']['card_revision']
        and plan['original_sha256'] == approval_digest(original)
        and plan['original_plan_sha256'] == original['payload']['body']['plan_sha256']
        and plan['binding']['agent_id'] == ledger.owner['agent_id']
        and plan['binding']['chain_id'] == ledger.owner['chain_id'])


def verify_abandoned(ledger, record):
    body = ledger._verify(record)
    require(body['state'] == 'abandoned')
    audit = ledger.store.get(body['audit_id'], kind=KIND, access_check=ledger.access)
    original = ledger._signed_body(audit['payload'])['original']
    require(audit == read_audit(ledger, original) and body['audit_id'] == audit_identifier(ledger, original)
        and body['audit_sha256'] == approval_digest(audit))
    validate_plan(ledger, body['reconciliation_plan'], original, 'abandon')
    require(body == dict(original['payload']['body'], state='abandoned',
        record_revision=original['revision'] + 1, audit_id=audit['record_id'],
        audit_sha256=approval_digest(audit), reconciliation_plan=body['reconciliation_plan']))


def completion_identifier(ledger, record):
    return str(uuid.uuid5(NAMESPACE, 'reconcile-complete:' + ledger.identifier + ':' + str(record['revision'])))


def read_completion(ledger, record):
    try:
        completed = ledger.store.get(completion_identifier(ledger, record), kind=KIND, access_check=ledger.access)
    except WorkflowNotFound:
        return None
    require(completed['revision'] == 1 and completed['payload'] == dict(schema=COMPLETION_SCHEMA, abandoned=record))
    verify_abandoned(ledger, record)
    return completed


def require_completion(ledger, body):
    record = ledger.store.get(ledger.identifier, kind=KIND, access_check=ledger.access)
    require(ledger._verify(record) == body and read_completion(ledger, record) is not None)


class OwnerContactReconciliation:
    def __init__(self, ledger, *, read_profile, read_binding, access_check, clock=time.time):
        require(type(ledger) is ContactIssuanceLedger)
        self.ledger, self.profile, self.binding = ledger, read_profile, read_binding
        self.access, self.clock = access_check, clock
        self.pending = None
        self.mutex = threading.Lock()

    def _current(self):
        row = self.ledger.store.get(self.ledger.identifier, kind=KIND, access_check=self.ledger.access)
        body = self.ledger._verify(row)
        if body['state'] == 'abandoned':
            verify_abandoned(self.ledger, row)
        return row

    def _live(self, plan):
        require(self.access() is True and self.ledger.access() is True
            and self.binding() == plan['binding'] and asdict(self.profile()) == plan['profile']
            and 0 <= self.clock() - plan['created_at'] <= 60)

    def handle(self, request, *, peer_uid):
        require(type(peer_uid) is int and peer_uid == 0 and type(request) is dict)
        require(self.mutex.acquire(blocking=False))
        try:
            require(self.access() is True and self.ledger.access() is True)
            binding = self.binding()
            require(request.get('broker_id') == binding['broker_id'] and request.get('epoch') == binding['epoch'])
            require(binding['agent_id'] == self.ledger.owner['agent_id']
                and binding['chain_id'] == self.ledger.owner['chain_id'])
            common = {'op', 'broker_id', 'epoch'}
            if request.get('op') == 'owner-contact-reconcile-cancel':
                require(set(request) == common)
                self.pending = None
                return {'cancelled': True}
            if request.get('op') == 'owner-contact-reconcile-preview':
                require(set(request) == common | {'action'})
                self.pending = None
                original = self._current()
                state = original['payload']['body']['state']
                require(request['action'] == {'reserved': 'abandon', 'issued': 'export', 'abandoned': 'acknowledge'}[state])
                profile = self.profile()
                now = int(self.clock())
                preview(profile, binding, previous_revision=0, now=now,
                    ttl_seconds=1, findable=False, messaging_allowed=False)
                audit = read_audit(self.ledger, original) if state == 'reserved' else None
                plan = dict(schema=SCHEMA, action=request['action'], ledger_id=self.ledger.identifier,
                    record_revision=original['revision'], card_revision=original['payload']['body']['card_revision'],
                    original_sha256=approval_digest(original), original_plan_sha256=original['payload']['body']['plan_sha256'],
                    binding=binding, profile=asdict(profile), created_at=now,
                    audit_sha256=approval_digest(audit) if audit else None)
                self._live(plan)
                challenge = secrets.token_hex(32)
                self.pending = json.loads(canonical(dict(plan=plan, original=original, challenge=challenge, audit=audit)))
                return json.loads(canonical(dict(plan=plan, plan_sha256=approval_digest(plan), challenge=challenge)))
            require(request.get('op') == 'owner-contact-reconcile-confirm' and set(request) ==
                common | {'challenge', 'plan_sha256', 'consent'})
            pending, self.pending = self.pending, None
            require(pending is not None and type(request['challenge']) is str
                and secrets.compare_digest(request['challenge'], pending['challenge'])
                and request['consent'] is True and request['plan_sha256'] == approval_digest(pending['plan']))
            plan, original = pending['plan'], pending['original']
            self._live(plan)
            require(self._current() == original)
            if plan['action'] == 'abandon':
                return self._abandon(plan, original, pending['audit'])
            if plan['action'] == 'acknowledge':
                self._complete(plan, original)
                self._live(plan)
                return dict(state='abandoned', revision=plan['card_revision'], record_revision=plan['record_revision'])
            require(plan['action'] == 'export')
            card = original['payload']['body']['card'].encode()
            verified = verify_contact_card(card, now=self.clock())
            require(verified.agent_id == self.ledger.owner['agent_id']
                and verified.chain_id == self.ledger.owner['chain_id']
                and verified.radio_public_key == binding['radio_public_key']
                and verified.revision == plan['card_revision'])
            require(self._current() == original)
            self._live(plan)
            return dict(card=card.decode(), card_sha256=hashlib.sha256(card).hexdigest(), revision=verified.revision)
        except BaseException:
            self.pending = None
            raise
        finally:
            self.mutex.release()

    def _abandon(self, plan, original, observed_audit):
        def guard():
            self._live(plan)
            return True
        require(read_audit(self.ledger, original) == observed_audit)
        guard()
        audit = observed_audit
        if audit is None:
            body = dict(schema=AUDIT_SCHEMA, owner=self.ledger.owner, ledger_id=self.ledger.identifier,
                original=original, approval=plan, decision='abandon_authorized')
            audit = self.ledger.store.put(audit_identifier(self.ledger, original), KIND, 0,
                self.ledger._proof(body), access_check=guard)
        require(audit == read_audit(self.ledger, original))
        require(self._current() == original)
        guard()
        body = dict(original['payload']['body'], state='abandoned', record_revision=original['revision'] + 1,
            audit_id=audit['record_id'], audit_sha256=approval_digest(audit), reconciliation_plan=plan)
        result = self.ledger.store.put(self.ledger.identifier, KIND, original['revision'],
            self.ledger._proof(body), access_check=guard)
        verify_abandoned(self.ledger, result)
        self._complete(plan, result)
        require(self._current() == result)
        guard()
        return dict(state='abandoned', revision=body['card_revision'], record_revision=body['record_revision'],
            audit_id=body['audit_id'], audit_sha256=body['audit_sha256'])

    def _complete(self, plan, record):
        def guard():
            self._live(plan)
            return True
        require(self._current() == record)
        guard()
        if read_completion(self.ledger, record) is None:
            self.ledger.store.put(completion_identifier(self.ledger, record), KIND, 0,
                dict(schema=COMPLETION_SCHEMA, abandoned=record), access_check=guard)
        require(read_completion(self.ledger, record) is not None and self._current() == record)
        guard()
