"""Read-only owner-ledger inspection under an externally held containment lease.

Historical uncertainty is retained, never converted into completion or replay
authority. The caller must keep producer locks and recheck live containment,
access and storage epochs around two identical signed scans.
"""

import uuid

from .correction_attempts import SCHEMA, STATES, identifier
from .mesh_contact_owner import ContactIssuanceLedger
from .mesh_contact_reconciliation import (
    AUDIT_SCHEMA, COMPLETION_SCHEMA, read_audit, read_completion, verify_abandoned,
)
from .mesh_contacts import verify_contact_card_signature
from .mesh_envelope import Peer
from .mesh_outbox import MeshOutbox, REPLAY_NAMESPACE, SCHEMA as OUTBOX_SCHEMA, _canonical
from .message_contacts import RecipientID
from .public_workflow_store import PublicWorkflowStore, SCHEMA as PUBLIC_SCHEMA
from .workflow_store import WorkflowUnavailable


def require(value):
    if not value:
        raise WorkflowUnavailable()


def envelope(row, actor):
    require(type(row) is dict and set(row) == {
        'schema', 'agent_id', 'record_id', 'revision', 'kind', 'payload', 'trust'})
    require(row['schema'] == 'sage.workflow-journal.v1' and row['agent_id'] == actor['agent_id']
        and row['trust'] == 'untrusted_auxiliary' and type(row['revision']) is int
        and 1 <= row['revision'] <= 2**53 - 1 and type(row['payload']) is dict)
    identifier(row['record_id'])


def correction(row, actor):
    envelope(row, actor)
    require(row['kind'] == 'correction_attempt')
    value = row['payload']
    basic = {'schema', 'actor', 'source_id', 'source_kind', 'attempt_id', 'session_id', 'state'}
    require(set(value) in (basic, basic | {'previous', 'authorization'})
        and value['schema'] == SCHEMA and value['actor'] == actor
        and value['source_kind'] in ('current', 'history')
        and type(value['state']) is str and value['state'] in STATES)
    source = identifier(value['source_id'])
    identifier(value['attempt_id'])
    expected = str(uuid.uuid5(uuid.NAMESPACE_URL,
        SCHEMA + ':' + actor['chain_id'] + ':' + actor['agent_id'] + ':' + source))
    require(row['record_id'] == expected)
    if value['session_id'] is not None:
        identifier(value['session_id'])
    require((value['state'] in {'reserved', 'admitting'}) == (value['session_id'] is None))
    if 'authorization' in value:
        authority = value['authorization']
        require(type(authority) is dict and set(authority) == {
            'expected_revision', 'acknowledge_unknown', 'new_execution'}
            and type(authority['expected_revision']) is int
            and 0 <= authority['expected_revision'] < row['revision']
            and authority['acknowledge_unknown'] is True and authority['new_execution'] is True)
        if value['previous'] is not None:
            identifier(value['previous'])
        require((authority['expected_revision'] == 0) == (value['previous'] is None))
    return value


class OwnerLedgerInspection:
    def __init__(self, rows, actor, store, identity, *, containment, producer_config=None, producer_identities=None):
        require(callable(containment) and containment() is True)
        self.actor, self.containment = actor, containment
        self.rows = {}
        for row in rows:
            envelope(row, actor)
            require(row['record_id'] not in self.rows)
            self.rows[row['record_id']] = row
        self.ledger = ContactIssuanceLedger(store, identity, access_check=containment)
        require(self.ledger.owner == actor)
        self.store, self.identity = store, identity
        from .maintenance_producer_ledgers import ProducerLedgerInspection
        self.producers = ProducerLedgerInspection(self, producer_config, producer_identities or {})

    def finish(self):
        require(self.containment() is True)
        return True

    def linked_correction(self, row):
        visited = set()
        while True:
            value = correction(row, self.actor)
            require(value['attempt_id'] not in visited)
            visited.add(value['attempt_id'])
            previous = value.get('previous')
            if previous is None:
                return
            require(previous in self.rows)
            archive = self.rows[previous]
            retained = self.archive(archive)
            require(retained['record_id'] == row['record_id']
                and retained['revision'] == value['authorization']['expected_revision']
                and retained['revision'] < row['revision'])
            row = retained

    def archive(self, row):
        envelope(row, self.actor)
        require(row['kind'] == 'correction_attempt' and row['revision'] == 1
            and set(row['payload']) == {'schema', 'record'}
            and row['payload']['schema'] == 'sage.lantern.correction-history.v1')
        retained = row['payload']['record']
        value = correction(retained, self.actor)
        require(row['record_id'] == str(uuid.uuid5(uuid.UUID(retained['record_id']), value['attempt_id'])))
        return retained

    def terminal_outbox(self, outbox, row):
        require(row['kind'] == 'mesh_outbound')
        body = outbox._verified(row)
        require(body['state'] in {'delivered', 'read'})
        for item in body['receipts']:
            receipt = outbox._receipt(body, item['wire'], item['verified_at'])
            scope = dict(sender=receipt.sender.to_dict(), receipt_id=receipt.message_id)
            expected = dict(schema='sage.lantern.mesh-receipt-replay.v1', receiver=outbox.sender.to_dict(),
                scope=scope, receipt_digest=receipt.digest, original_id=body['message_id'],
                original_digest=body['approved']['content_digest'])
            slot_scope = dict(schema='sage.lantern.mesh-receipt-slot.v1', receiver=outbox.sender.to_dict(),
                original_id=body['message_id'], kind=receipt.kind)
            for binding, key_scope, method in (
                (expected, scope, 'LANTERN-OUTBOX-RECEIPT-REPLAY'),
                ({**expected, **slot_scope}, slot_scope, 'LANTERN-OUTBOX-RECEIPT-SLOT')):
                key = str(uuid.uuid5(REPLAY_NAMESPACE, _canonical(key_scope).decode('ascii')))
                require(key in self.rows)
                linked = self.rows[key]
                require(linked['kind'] == 'mesh_outbound' and linked['revision'] == 1)
                outbox._verify_proof(linked['payload'], method=method, expected=binding)
        return body

    def abandoned(self, row):
        envelope(row, self.actor)
        body = self.ledger._verify(row)
        require(body['state'] == 'abandoned' and body['audit_id'] in self.rows)
        audit = self.rows[body['audit_id']]
        original = self.ledger._signed_body(audit['payload'])['original']
        envelope(original, self.actor)
        require(read_audit(self.ledger, original) == audit)
        verify_abandoned(self.ledger, row)

    def inspect(self, row):
        require(self.rows.get(row['record_id']) == row)
        from .maintenance_producer_ledgers import SCHEMAS, schema
        from .mesh_contact_registry import MeshContactRegistry, INDEX_SCHEMA, CONTACT_SCHEMA
        if schema(row) in (INDEX_SCHEMA, CONTACT_SCHEMA):
            require(self.containment() is True)
            registry = MeshContactRegistry(self.store, self.identity, owner=RecipientID(**self.actor))
            require(registry.index_id in self.rows)
            index = registry._verify(self.rows[registry.index_id], registry.index_id, INDEX_SCHEMA)
            members = {identifier for identifier, entry in self.rows.items() if schema(entry) == CONTACT_SCHEMA}
            require(set(index['members']) == members)
            for identifier in members:
                registry._verify(self.rows[identifier], identifier, CONTACT_SCHEMA)
            require(self.containment() is True)
            return 'retained_issued'
        if schema(row) in SCHEMAS:
            return self.producers.inspect(row)
        if row['kind'] == 'correction_attempt':
            retained = self.archive(row) if row['payload'].get('schema') == 'sage.lantern.correction-history.v1' else row
            self.linked_correction(retained)
            outcome = 'retained_unknown'
        elif row['kind'] == 'contact_issuance':
            payload = row['payload']
            if payload.get('schema') == COMPLETION_SCHEMA:
                require(set(payload) == {'schema', 'abandoned'})
                self.abandoned(payload['abandoned'])
                require(read_completion(self.ledger, payload['abandoned']) == row)
                outcome = 'retained_abandoned'
            else:
                body = self.ledger._signed_body(payload)
                if body.get('schema') == AUDIT_SCHEMA:
                    envelope(body['original'], self.actor)
                    require(read_audit(self.ledger, body['original']) == row)
                    outcome = 'retained_unknown'
                else:
                    body = self.ledger._verify(row)
                    if body['state'] == 'abandoned':
                        self.abandoned(row)
                        outcome = 'retained_abandoned'
                    elif body['state'] == 'issued':
                        card = verify_contact_card_signature(body['card'].encode(), known_actor=RecipientID(**self.actor))
                        require(card.revision == body['card_revision'])
                        outcome = 'retained_issued'
                    else:
                        outcome = 'retained_unknown'
        elif row['kind'] == 'mesh_outbound':
            outbox = MeshOutbox(self.store, self.identity,
                sender=Peer(self.actor['chain_id'], bytes.fromhex(self.actor['agent_id'])),
                guard=lambda action: False, verify_contact=lambda action: False)
            body = row['payload'].get('body', {})
            schema = body.get('schema')
            if schema == OUTBOX_SCHEMA:
                self.terminal_outbox(outbox, row)
            else:
                methods = {'sage.lantern.mesh-receipt-replay.v1': 'LANTERN-OUTBOX-RECEIPT-REPLAY',
                    'sage.lantern.mesh-receipt-slot.v1': 'LANTERN-OUTBOX-RECEIPT-SLOT'}
                require(schema in methods and row['revision'] == 1)
                outbox._verify_proof(row['payload'], method=methods[schema])
                require(body['original_id'] in self.rows)
                original = self.terminal_outbox(outbox, self.rows[body['original_id']])
                require(body['original_digest'] == original['approved']['content_digest'])
                found = False
                for item in original['receipts']:
                    receipt = outbox._receipt(original, item['wire'], item['verified_at'])
                    scope = dict(sender=receipt.sender.to_dict(), receipt_id=receipt.message_id)
                    if body.get('scope') == scope and body.get('receipt_digest') == receipt.digest:
                        key_scope = scope if schema.endswith('replay.v1') else dict(schema=schema,
                            receiver=outbox.sender.to_dict(), original_id=original['message_id'], kind=receipt.kind)
                        require(row['record_id'] == str(uuid.uuid5(REPLAY_NAMESPACE, _canonical(key_scope).decode('ascii'))))
                        found = True
                require(found)
            outcome = 'retained_terminal'
        elif row['kind'] == 'public_proposal':
            payload = row['payload']
            require(payload.get('schema') == PUBLIC_SCHEMA)
            binding = payload['binding']
            require(type(binding) is dict and set(binding) == {
                'storage_chain', 'storage_actor', 'chain_id', 'domain', 'author'}
                and binding['storage_chain'] == self.actor['chain_id']
                and binding['storage_actor'] == self.actor['agent_id']
                and payload['namespace'] in {'sdk', 'http'})
            public = PublicWorkflowStore(self.store, chain_id=binding['chain_id'], domain=binding['domain'],
                author=binding['author'], access_check=self.containment, drain_check=lambda: False,
                identity=self.identity)
            public._validate(row, payload['namespace'], payload['operation_id'])
            require(payload['result'] == {'cancelled': True}
                or (payload['namespace'] == 'sdk' and payload['result'] is not None))
            outcome = 'retained_terminal'
        else:
            raise WorkflowUnavailable()
        return outcome
