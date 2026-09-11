"""Schema-specific, read-only producer history inspection; never resume work."""

import base64
import hashlib
import math
import re
import uuid

from .community_public import PublicProposal
from .gateway_contact import GatewayContactProbe, _open as open_contact
from .mesh_envelope import Peer
from .mesh_fragments import CHUNK_BYTES, MAX_MESSAGE_BYTES, decode_fragment
from .mesh_inbox import MeshInbox, ASSEMBLY_SCHEMA, INBOX_SCHEMA, REJECTION_SCHEMA, REASONS, _id
from .mesh_read_root import verify_consent_row
from .mesh_receipt_sender import MeshReceiptSender
from .message_contacts import RecipientID
from .public_mesh_protocol import canonical, identifier, proposal_data, public_key, timestamp
from .public_mesh_provider import PublicMeshProvider, PublicMeshPins
from .workflow_store import WorkflowUnavailable


SCHEMAS = frozenset({ASSEMBLY_SCHEMA, INBOX_SCHEMA, REJECTION_SCHEMA,
    'sage.lantern.mesh-receipt-sender.v1', 'sage.lantern.mesh-read-consent.v1',
    'sage.lantern.public-mesh-local.v1', 'sage.lantern.gateway-contact.v1',
    'sage.lantern.public-gateway-runtime.v2', 'sage.lantern.public-gateway-journal.v1'})


def require(value):
    if not value:
        raise WorkflowUnavailable()


def schema(row):
    payload = row['payload']
    return payload.get('schema') or payload.get('body', {}).get('schema')


def fragments(digest, count, size, parts):
    public_key(digest)
    require(type(size) is int and 1 <= size <= MAX_MESSAGE_BYTES and type(count) is int
        and count == math.ceil(size / CHUNK_BYTES) and type(parts) is dict and 0 < len(parts) <= count)
    decoded = {}
    for index, encoded in parts.items():
        require(type(index) is str and index.isascii() and index.isdecimal()
            and str(int(index)) == index and 0 <= int(index) < count and type(encoded) is str)
        value = base64.b64decode(encoded, validate=True)
        require(base64.b64encode(value).decode() == encoded
            and len(value) == min(CHUNK_BYTES, size - int(index) * CHUNK_BYTES))
        decoded[int(index)] = value
    if len(parts) == count:
        wire = b''.join(decoded[index] for index in range(count))
        require(len(wire) == size and hashlib.sha256(wire).hexdigest() == digest)
        return wire
    return None


class ProducerLedgerInspection:
    def __init__(self, owner, config, identities):
        self.owner, self.config, self.identities = owner, config, identities
        self.recipient = Peer(owner.actor['chain_id'], bytes.fromhex(owner.actor['agent_id']))
        self.inbox = MeshInbox(owner.store, recipient=self.recipient, recipient_signing_key=owner.identity._signing_key,
            resolve_peer=lambda message: None, access_check=owner.containment, storage_check=owner.containment)
        self.gateway_checked = False

    def historical(self, local_id):
        require(local_id in self.owner.rows)
        row = self.owner.rows[local_id]
        require(row['kind'] == 'mesh_inbound' and schema(row) == INBOX_SCHEMA)
        return self.inbox._historical(local_id, row)

    def private(self, row):
        selected = schema(row)
        require(row['kind'] == ('mesh_inbound' if selected in {INBOX_SCHEMA, ASSEMBLY_SCHEMA, REJECTION_SCHEMA}
            else 'mesh_outbound'))
        payload = row['payload']
        if selected == INBOX_SCHEMA:
            self.historical(row['record_id'])
            return 'retained_terminal'
        if selected == ASSEMBLY_SCHEMA:
            require(row['record_id'] in self.inbox._slots)
            value = self.inbox._assembly(row, payload['created_at'])
            parts = {index: decode_fragment(text) for index, text in value['parts'].items()}
            first = next(iter(parts.values()))
            wire = fragments(value['digest'], first.count, first.size,
                {index: base64.b64encode(part.payload).decode() for index, part in parts.items()})
            if value['state'] == 'complete':
                require(wire is not None)
                matches = [candidate for candidate in self.owner.rows.values()
                    if candidate['kind'] == 'mesh_inbound' and schema(candidate) == INBOX_SCHEMA
                    and candidate['payload']['wire'] == wire.decode('ascii')
                    and candidate['payload']['sender'] == value['sender']]
                require(len(matches) == 1)
                self.historical(matches[0]['record_id'])
            return 'retained_unknown'
        if selected == REJECTION_SCHEMA:
            require(set(payload) == {'schema', 'operation_id', 'frame_digest', 'frame_bytes', 'reason'}
                and payload['reason'] in REASONS and type(payload['frame_bytes']) is int
                and 1 <= payload['frame_bytes'] <= 176)
            public_key(payload['frame_digest'])
            identifier(payload['operation_id'])
            require(row['record_id'] == _id(REJECTION_SCHEMA,
                [self.recipient.to_dict(), uuid.UUID(payload['operation_id']).int % 16]))
            return 'retained_terminal'
        if selected == 'sage.lantern.mesh-read-consent.v1':
            self.historical(payload['body']['local_id'])
            require(verify_consent_row(row, identity=self.owner.identity,
                owner=RecipientID(**self.owner.actor), inbox=self.inbox) is True)
            return 'retained_unknown'
        sender = MeshReceiptSender(self.owner.store, self.owner.identity, inbox=self.inbox, sender=self.recipient,
            guard=lambda action: False, policy=lambda action: None, verify_contact=lambda action: False)
        body, approved = sender._verified(row)
        require(body['state'] == 'serial_accepted')
        original = self.historical(approved['local_id'])
        require(original.kind == 'message' and approved['recipient'] == original.sender.to_dict()
            and approved['original_id'] == original.message_id and approved['original_digest'] == original.digest
            and approved['issued_at'] >= original.issued_at)
        return 'retained_terminal'

    def public_client(self, row):
        require(row['kind'] == 'public_proposal' and self.config is not None
            and 'public_mesh' in self.config and 'public_mesh' in self.identities)
        settings = self.config['public_mesh']
        identity = self.identities['public_mesh']
        require(identity.agent_id == settings['author'])
        pins = PublicMeshPins(settings['gateway_key'], bytes.fromhex(settings['radio_key']),
            settings['chain_id'], settings['domain'])
        provider = PublicMeshProvider(self.owner.store, identity, pins,
            approval_check=lambda proposal: False, contact_check=lambda pins: False)
        if schema(row) == 'sage.lantern.gateway-contact.v1':
            probe = GatewayContactProbe(self.owner.store, identity, pins, active=lambda: False)
            require(row['record_id'] == probe._slot and probe._get(self.owner.containment) == row)
            data = row['payload']['body']['data']
            require(data['state'] != 'dispatching')
            proof_size = len(canonical(dict(body=data['probe'], signature=''))) + 128
            request_fragments = math.ceil((4 + 4 * math.ceil(proof_size / 3)) / CHUNK_BYTES)
            require(data['next'] < request_fragments if data['state'] == 'queued'
                else data['next'] == request_fragments)
            if data['response_digest'] is None:
                require(data['parts'] == {} and data['count'] is None and data['size'] is None
                    and data['state'] != 'verified')
            else:
                wire = fragments(data['response_digest'], data['count'], data['size'], data['parts'])
                require((wire is not None) == (data['state'] == 'verified'))
                if wire is not None:
                    require(open_contact(wire, pins.gateway_key) == dict(data['probe'], kind='reply'))
            return 'retained_unknown'
        body = row['payload']['body']
        require(provider._get(row['record_id'], body['kind'], self.owner.containment) == body)
        data = body['data']
        if body['kind'] == 'request':
            provider._request(body)
            require(data['state'] != 'dispatching')
        elif body['kind'] == 'submit':
            require(type(data) is dict and set(data) == {'request_id', 'proposal'} and row['revision'] == 1)
            proposal = PublicProposal(**data['proposal'])
            require(proposal_data(proposal) == data['proposal'] and proposal.author == identity.agent_id
                and proposal.chain_id == pins.chain_id and proposal.domain == pins.domain
                and row['record_id'] == provider._id('submit', proposal.operation_id))
            identifier(data['request_id'])
            if data['request_id'] in self.owner.rows:
                request_row = self.owner.rows[data['request_id']]
                require(schema(request_row) == 'sage.lantern.public-mesh-local.v1'
                    and request_row['payload']['body']['kind'] == 'request'
                    and request_row['payload']['body']['data']['proposal'] == data['proposal'])
        else:
            require(body['kind'] == 'assembly' and row['record_id'] in {provider._id('assembly', index) for index in range(8)}
                and type(data) is dict and set(data) == {'digest', 'count', 'size', 'expires_at', 'parts'})
            timestamp(data['expires_at'])
            fragments(data['digest'], data['count'], data['size'], data['parts'])
        return 'retained_unknown'

    def gateway(self, row):
        require(row['kind'] == 'public_proposal' and self.config is not None
            and 'public_gateway' in self.config and 'public_gateway' in self.identities)
        if schema(row) == 'sage.lantern.public-gateway-runtime.v2':
            require(row['payload']['body']['data']['state'] in {'receiving', 'sent'})
        else:
            require(row['payload']['state'] in {'prepared', 'receipt'})
        if not self.gateway_checked:
            from .mesh_gateway_root import verify_gateway_inventory
            require(verify_gateway_inventory(list(self.owner.rows.values()), self.owner.store, self.identities['public_gateway'],
                self.config['public_gateway'], access_check=self.owner.containment) is True)
            self.gateway_checked = True
        return 'retained_unknown'

    def inspect(self, row):
        selected = schema(row)
        require(selected in SCHEMAS)
        payload = row['payload']
        if 'body' in payload:
            require(set(payload) == {'body', 'signature'} and type(payload['body']) is dict
                and type(payload['signature']) is str and re.fullmatch('[0-9a-f]{128}', payload['signature']) is not None)
            if 'revision' in payload['body']:
                require(type(payload['body']['revision']) is int and payload['body']['revision'] == row['revision'])
        if selected in {INBOX_SCHEMA, ASSEMBLY_SCHEMA, REJECTION_SCHEMA,
                'sage.lantern.mesh-receipt-sender.v1', 'sage.lantern.mesh-read-consent.v1'}:
            return self.private(row)
        if selected in {'sage.lantern.public-mesh-local.v1', 'sage.lantern.gateway-contact.v1'}:
            return self.public_client(row)
        return self.gateway(row)
