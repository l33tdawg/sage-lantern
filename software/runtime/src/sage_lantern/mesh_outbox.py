"""Durable outbound workflows with verified, persisted peer receipts.

Every untrusted journal revision is signed by the existing sender identity.
No keys are loaded/generated/saved. No automatic loop or uncertain-send retry.
The trusted live guard and contact verifier are required on every dispatch.
Peer delivery/read are signed recipient assertions, not proof of human cognition.
close() fences admission and joins this instance only, not other processes.
"""

from dataclasses import dataclass
from functools import wraps
import hashlib
import json
import re
import struct
import threading
import time
import uuid

from nacl.signing import SigningKey, VerifyKey

from .mesh_contacts import VerifiedMeshContact
from .mesh_envelope import Peer, seal_message, open_envelope, correlate_receipt
from .mesh_fragments import split_envelope
from .meshcore_transport import MeshCoreClient, SendReceipt
from .message_contacts import RecipientID
from .workflow_store import MAX_REVISION, WorkflowNotFound, WorkflowStore, WorkflowOutcomeUnknown


SCHEMA = 'sage.lantern.mesh-outbound.v1'
METHOD = 'LANTERN-OUTBOX'
PATH = '/sage-lantern/mesh-outbound/v1'
MAX_ATTEMPTS = 256
MAX_RECEIPTS = 2
REPLAY_NAMESPACE = uuid.UUID('f1d66cf1-379c-48bb-a90e-d3a75f208e61')


class MeshOutboxError(RuntimeError):
    def __init__(self):
        super().__init__('mesh_outbox_unavailable')


@dataclass(frozen=True, repr=False)
class OutboxAction:
    operation: str
    message_id: str
    recipient: Peer
    radio_public_key: bytes
    approval_binding: str
    consent_binding: str
    content_digest: str
    wire_hash: str
    fragment_index: int
    decision_binding: str = ''


@dataclass(frozen=True)
class OutboxProgress:
    message_id: str
    revision: int
    state: str
    next_fragment: int
    fragment_count: int


@dataclass(frozen=True, repr=False)
class SentMessage:
    progress: OutboxProgress
    recipient: Peer
    content: str
    issued_at: int


def _admitted(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._condition:
            if self._closing or self._active:
                raise MeshOutboxError()
            self._active = True
        try:
            original = kwargs['access_check']
            if not callable(original):
                raise MeshOutboxError()
            kwargs['access_check'] = lambda: self._open() and original() is True
            return method(self, *args, **kwargs)
        except BaseException as error:
            if not isinstance(error, Exception):
                self._uncertain = True
                raise
            raise MeshOutboxError() from None
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()
    return call


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        raise MeshOutboxError()


def _binding(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', value) is None:
        raise MeshOutboxError()


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise MeshOutboxError()


class MeshOutbox:
    def __init__(self, store, identity, *, sender, guard, verify_contact, clock=lambda: int(time.time())):
        try:
            if (type(store) is not WorkflowStore or type(sender) is not Peer
                    or not isinstance(identity._signing_key, SigningKey)
                    or bytes(identity._signing_key.verify_key) != sender.public_key
                    or identity.agent_id != sender.public_key.hex()
                    or store.storage.actor != RecipientID(sender.chain_id, identity.agent_id)
                    or not callable(guard) or not callable(verify_contact) or not callable(clock)):
                raise MeshOutboxError()
        except Exception:
            raise MeshOutboxError() from None
        self.store = store
        self.identity = identity
        self.sender = sender
        self.guard = guard
        self.verify_contact = verify_contact
        self.clock = clock
        self._mutex = threading.Lock()
        self._condition = threading.Condition()
        self._active = False
        self._closing = False
        self._uncertain = False

    def _open(self):
        with self._condition:
            return not self._closing

    def close(self, timeout=0):
        """Latch closed, cancel future admission, bounded join; unknown stays false."""
        if type(timeout) not in (int, float) or not 0 <= timeout <= 30:
            raise MeshOutboxError()
        with self._condition:
            self._closing = True
            joined = self._condition.wait_for(lambda: not self._active, timeout)
            return joined and not self._uncertain

    def _now(self):
        now = self.clock()
        _integer(now, 1, (1 << 53) - 1)
        return now

    def _action(self, body, operation, decision=''):
        approved = body['approved']
        return OutboxAction(operation, body['message_id'],
                            Peer(approved['recipient']['chain_id'], bytes.fromhex(approved['recipient']['public_key'])),
                            bytes.fromhex(approved['radio_public_key']), approved['approval_binding'],
                            approved['consent_binding'], approved['content_digest'], approved['wire_hash'],
                            body['next_fragment'], decision or body['retry_binding'])

    def _check(self, action):
        if not self._open() or self.guard(action) is not True:
            raise MeshOutboxError()
        return True

    def _contact(self, action):
        self._check(action)
        contact = self.verify_contact(action)
        now = self._now()
        if (type(contact) is not VerifiedMeshContact
                or contact.recipient != RecipientID(action.recipient.chain_id, action.recipient.public_key.hex())
                or contact.radio_public_key != action.radio_public_key.hex()
                or contact.findable is not True or contact.messaging_allowed is not True
                or not contact.issued_at <= now < contact.expires_at):
            raise MeshOutboxError()
        return self._check(action)

    def _validate(self, body, message_id, revision):
        if (type(body) is not dict or set(body) != {'schema', 'message_id', 'revision', 'approved',
                'state', 'next_fragment', 'dispatch_timestamp', 'attempts', 'retry_binding',
                'approval_proof', 'receipts'}
                or body['schema'] != SCHEMA or body['message_id'] != message_id or body['revision'] != revision):
            raise MeshOutboxError()
        _uuid(message_id)
        _integer(body['revision'], 1, MAX_REVISION)
        approved = body['approved']
        if (type(approved) is not dict or set(approved) != {'sender', 'recipient', 'radio_public_key',
                'wire', 'wire_hash', 'content_digest', 'approval_binding', 'consent_binding',
                'issued_at', 'expires_at', 'content'} or approved['sender'] != self.sender.to_dict()):
            raise MeshOutboxError()
        for peer in (approved['sender'], approved['recipient']):
            if type(peer) is not dict or set(peer) != {'chain_id', 'public_key'}:
                raise MeshOutboxError()
            Peer(peer['chain_id'], bytes.fromhex(peer['public_key']))
        _binding(approved['approval_binding'])
        _binding(approved['consent_binding'])
        for field in ('wire_hash', 'content_digest', 'radio_public_key'):
            if type(approved[field]) is not str or re.fullmatch('[0-9a-f]{64}', approved[field]) is None:
                raise MeshOutboxError()
        _integer(approved['issued_at'], 1, (1 << 53) - 1)
        _integer(approved['expires_at'], approved['issued_at'] + 1, approved['issued_at'] + 3600)
        content = approved['content']
        if (type(content) is not str or not content.strip() or not 1 <= len(content.encode('utf-8')) <= 512
                or any(ord(char) < 32 and char not in '\n\t' for char in content)):
            raise MeshOutboxError()
        original = dict(schema='sage.lantern.mesh-peer.v1', sender=approved['sender'], recipient=approved['recipient'],
                        message_id=message_id, kind='message', issued_at=approved['issued_at'],
                        expires_at=approved['expires_at'], content=content, original_id=None, original_digest=None)
        digest = hashlib.sha256(json.dumps(original, sort_keys=True, ensure_ascii=False,
                                          separators=(',', ':')).encode('utf-8')).hexdigest()
        if digest != approved['content_digest']:
            raise MeshOutboxError()
        self._verify_proof(body['approval_proof'], method='LANTERN-OUTBOX-APPROVAL',
                           expected=dict(message_id=message_id, approved=approved))
        if type(approved['wire']) is not str or not approved['wire'].startswith('LME1.'):
            raise MeshOutboxError()
        wire = approved['wire'].encode('ascii')
        fragments = split_envelope(wire)
        if hashlib.sha256(wire).hexdigest() != approved['wire_hash']:
            raise MeshOutboxError()
        _integer(body['next_fragment'], 0, len(fragments))
        _integer(body['attempts'], 0, MAX_ATTEMPTS)
        if body['retry_binding'] != '':
            _binding(body['retry_binding'])
        if body['dispatch_timestamp'] is not None:
            _integer(body['dispatch_timestamp'], 1, 0xffffffff)
        if body['state'] not in ('queued', 'dispatching', 'awaiting_receipt', 'delivered', 'read'):
            raise MeshOutboxError()
        if body['state'] == 'awaiting_receipt':
            if body['next_fragment'] != len(fragments) or body['dispatch_timestamp'] is not None:
                raise MeshOutboxError()
        elif body['state'] not in ('delivered', 'read') and body['next_fragment'] >= len(fragments):
            raise MeshOutboxError()
        if body['state'] == 'dispatching' and (body['dispatch_timestamp'] is None or body['attempts'] == 0):
            raise MeshOutboxError()
        receipts = body['receipts']
        if type(receipts) is not list or len(receipts) > MAX_RECEIPTS:
            raise MeshOutboxError()
        seen = set()
        highest = None
        for item in receipts:
            if type(item) is not dict or set(item) != {'wire', 'verified_at'}:
                raise MeshOutboxError()
            _integer(item['verified_at'], 1, (1 << 53) - 1)
            receipt = self._receipt(body, item['wire'], item['verified_at'])
            if receipt.message_id in seen:
                raise MeshOutboxError()
            seen.add(receipt.message_id)
            if receipt.kind == 'read' or highest is None:
                highest = receipt.kind
        expected_state = 'read' if highest == 'read' else 'delivered' if highest else None
        if ((expected_state is not None and body['state'] != expected_state)
                or (expected_state is None and body['state'] in ('delivered', 'read'))):
            raise MeshOutboxError()
        return fragments

    def _proof(self, body, method=METHOD):
        headers = self.identity.sign_request(method, PATH, _canonical(body), timestamp=self._now())
        return dict(body=body, timestamp=headers['X-Timestamp'], nonce=headers['X-Nonce'],
                    signature=headers['X-Signature'])

    def _verify_proof(self, payload, *, method=METHOD, expected=None):
        if type(payload) is not dict or set(payload) != {'body', 'timestamp', 'nonce', 'signature'}:
            raise MeshOutboxError()
        for field, size in (('nonce', 16), ('signature', 128)):
            if type(payload[field]) is not str or re.fullmatch('[0-9a-f]{' + str(size) + '}', payload[field]) is None:
                raise MeshOutboxError()
        stamp = payload['timestamp']
        if type(stamp) is not str or re.fullmatch('[1-9][0-9]{0,15}', stamp) is None:
            raise MeshOutboxError()
        _integer(int(stamp), 1, (1 << 53) - 1)
        canonical = method.encode() + b' ' + PATH.encode() + b'\n' + _canonical(payload['body'])
        message = hashlib.sha256(canonical).digest() + struct.pack('>q', int(stamp)) + bytes.fromhex(payload['nonce'])
        VerifyKey(self.sender.public_key).verify(message, bytes.fromhex(payload['signature']))
        if expected is not None and payload['body'] != expected:
            raise MeshOutboxError()
        return payload['body']

    def _verified(self, record):
        payload = record['payload']
        self._verify_proof(payload)
        self._validate(payload['body'], record['record_id'], record['revision'])
        return payload['body']

    def _load(self, message_id, access_check):
        return self._verified(self.store.get(message_id, kind='mesh_outbound', access_check=access_check))

    def _put(self, body, expected_revision, access_check):
        body = {**body, 'revision': expected_revision + 1}
        self._validate(body, body['message_id'], body['revision'])
        if access_check() is not True:
            raise MeshOutboxError()
        proof = self._proof(body)
        self._verified(dict(payload=proof, record_id=body['message_id'], revision=body['revision']))
        try:
            result = self.store.put(body['message_id'], 'mesh_outbound', expected_revision, proof,
                                    access_check=access_check)
        except WorkflowOutcomeUnknown:
            self._uncertain = True
            raise
        return self._verified(result)

    def _progress(self, body):
        return OutboxProgress(body['message_id'], body['revision'], body['state'], body['next_fragment'],
                              len(split_envelope(body['approved']['wire'].encode('ascii'))))

    @_admitted
    def queue(self, *, message_id, recipient, radio_public_key, content, approval_binding,
              consent_binding, issued_at, expires_at, access_check):
        """Seal once, self-sign approval and revision, then CAS-create; never RF."""
        try:
            _uuid(message_id)
            _binding(approval_binding)
            _binding(consent_binding)
            now = self._now()
            if not issued_at <= now < expires_at or access_check() is not True:
                raise MeshOutboxError()
            try:
                self.store.get(message_id, kind='mesh_outbound', access_check=access_check)
            except WorkflowNotFound:
                pass
            else:
                raise MeshOutboxError()
            sealed = seal_message(self.identity._signing_key, sender=self.sender, recipient=recipient,
                                  message_id=message_id, content=content, issued_at=issued_at, expires_at=expires_at)
            body = dict(schema=SCHEMA, message_id=message_id, revision=1,
                        approved=dict(sender=self.sender.to_dict(), recipient=recipient.to_dict(),
                                      radio_public_key=radio_public_key.hex(), wire=sealed.wire,
                                      wire_hash=hashlib.sha256(sealed.wire.encode('ascii')).hexdigest(),
                                      content_digest=sealed.digest, content=content, approval_binding=approval_binding,
                                      consent_binding=consent_binding, issued_at=issued_at, expires_at=expires_at),
                        state='queued', next_fragment=0, dispatch_timestamp=None, attempts=0, retry_binding='',
                        receipts=[])
            action = self._action(body, 'queue')
            self._contact(action)
            body['approval_proof'] = self._proof(dict(message_id=message_id, approved=body['approved']),
                                                  method='LANTERN-OUTBOX-APPROVAL')
            return self._progress(self._put(body, 0, lambda: access_check() is True and self._check(action)))
        except Exception:
            raise MeshOutboxError() from None

    @_admitted
    def inspect(self, message_id, *, access_check):
        try:
            return self._progress(self._load(message_id, access_check))
        except Exception:
            raise MeshOutboxError() from None

    @_admitted
    def dispatch_one(self, message_id, *, device, radio_timestamp, access_check):
        """One CAS-admitted RF fragment. Any failure requires inspect, not retry."""
        if not self._mutex.acquire(blocking=False):
            raise MeshOutboxError()
        rf_started = False
        try:
            if type(device) is not MeshCoreClient:
                raise MeshOutboxError()
            _integer(radio_timestamp, 1, 0xffffffff)
            body = self._load(message_id, access_check)
            if body['state'] != 'queued' or body['attempts'] >= MAX_ATTEMPTS:
                raise MeshOutboxError()
            if not body['approved']['issued_at'] <= self._now() < body['approved']['expires_at']:
                raise MeshOutboxError()
            action = self._action(body, 'dispatch')
            def check():
                return (access_check() is True
                        and body['approved']['issued_at'] <= self._now() < body['approved']['expires_at']
                        and self._contact(action))
            if check() is not True:
                raise MeshOutboxError()
            inventory = device.get_contacts(authorize=check)
            prefix = inventory.unique_prefix(action.radio_public_key)
            stamp = body['dispatch_timestamp'] or radio_timestamp
            body = self._put({**body, 'state': 'dispatching', 'dispatch_timestamp': stamp,
                              'attempts': body['attempts'] + 1}, body['revision'], check)
            if check() is not True:
                raise MeshOutboxError()
            fragments = split_envelope(body['approved']['wire'].encode('ascii'))
            rf_started = True
            receipt = device.send_text(recipient_prefix=prefix, text=fragments[body['next_fragment']],
                                       timestamp=stamp, authorize=check)
            if (type(receipt) is not SendReceipt or receipt.serial_accepted is not True
                    or receipt.delivered is not False or receipt.read is not False or check() is not True):
                raise MeshOutboxError()
            index = body['next_fragment'] + 1
            body = self._put({**body, 'next_fragment': index, 'dispatch_timestamp': None,
                              'state': 'awaiting_receipt' if index == len(fragments) else 'queued',
                              'retry_binding': ''}, body['revision'], check)
            return self._progress(body)
        except Exception:
            if rf_started:
                self._uncertain = True
            raise MeshOutboxError() from None
        finally:
            self._mutex.release()

    @_admitted
    def retry_decision(self, message_id, *, expected_revision, decision_binding, access_check):
        """Explicit guarded retry; guard MUST fence the original dispatch worker.

        A CAS cannot stop a different process already admitted to radio IO.
        Same wire/UUID/index/timestamp survive; consent must still be current.
        """
        try:
            _binding(decision_binding)
            _integer(expected_revision, 1, MAX_REVISION - 1)
            body = self._load(message_id, access_check)
            if body['state'] != 'dispatching' or body['revision'] != expected_revision:
                raise MeshOutboxError()
            if not body['approved']['issued_at'] <= self._now() < body['approved']['expires_at']:
                raise MeshOutboxError()
            action = self._action(body, 'retry', decision_binding)
            check = lambda: access_check() is True and self._contact(action)
            if check() is not True:
                raise MeshOutboxError()
            return self._progress(self._put({**body, 'state': 'queued', 'retry_binding': decision_binding},
                                           expected_revision, check))
        except Exception:
            raise MeshOutboxError() from None

    def _receipt(self, body, wire, now):
        approved = body['approved']
        peer = Peer(approved['recipient']['chain_id'], bytes.fromhex(approved['recipient']['public_key']))
        receipt = open_envelope(wire, self.identity._signing_key, expected_sender=peer,
                                expected_recipient=self.sender, now=now)
        correlate_receipt(receipt, original_sender=self.sender, original_recipient=peer,
                          original_id=body['message_id'], original_digest=approved['content_digest'],
                          original_issued_at=approved['issued_at'])
        return receipt

    def _receipt_binding(self, record_id, expected, method, access_check, *, create):
        try:
            record = self.store.get(record_id, kind='mesh_outbound', access_check=access_check)
        except WorkflowNotFound:
            if not create:
                return
            proof = self._proof(expected, method=method)
            try:
                record = self.store.put(record_id, 'mesh_outbound', 0, proof, access_check=access_check)
            except WorkflowOutcomeUnknown:
                self._uncertain = True
                raise
        if record['revision'] != 1:
            raise MeshOutboxError()
        self._verify_proof(record['payload'], method=method, expected=expected)

    def _reserve_receipt(self, body, receipt, access_check, *, create=True):
        scope = dict(sender=receipt.sender.to_dict(), receipt_id=receipt.message_id)
        record_id = str(uuid.uuid5(REPLAY_NAMESPACE, _canonical(scope).decode('ascii')))
        expected = dict(schema='sage.lantern.mesh-receipt-replay.v1', receiver=self.sender.to_dict(),
                        scope=scope, receipt_digest=receipt.digest, original_id=body['message_id'],
                        original_digest=body['approved']['content_digest'])
        method = 'LANTERN-OUTBOX-RECEIPT-REPLAY'
        if create:
            slot_scope = dict(schema='sage.lantern.mesh-receipt-slot.v1', receiver=self.sender.to_dict(),
                              original_id=body['message_id'], kind=receipt.kind)
            slot_id = str(uuid.uuid5(REPLAY_NAMESPACE, _canonical(slot_scope).decode('ascii')))
            slot = {**expected, **slot_scope}
            self._receipt_binding(slot_id, slot, 'LANTERN-OUTBOX-RECEIPT-SLOT', access_check, create=True)
        self._receipt_binding(record_id, expected, method, access_check, create=create)

    @_admitted
    def ingest_receipt(self, message_id, *, wire, access_check):
        """Reserve genuine receipt replay binding, then CAS-promote; no RF or UUID generation.

        Failure requires retry of the SAME original/receipt, never a replacement.
        Fixed-slot reservation, UUID reservation and promotion are three commits;
        each is reconciled by a subsequent explicit call. Redundant assertions
        allocate nothing. No new peer assertion is returned before promotion.
        """
        body = self._load(message_id, access_check)
        action = self._action(body, 'receipt')
        check = lambda: access_check() is True and self._check(action)
        if check() is not True:
            raise MeshOutboxError()
        now = self._now()
        for item in body['receipts']:
            if item['wire'] == wire:
                now = item['verified_at']
                break
        receipt = self._receipt(body, wire, now)
        ranks = {'queued': 0, 'dispatching': 0, 'awaiting_receipt': 0, 'delivered': 1, 'read': 2}
        target = 'delivered' if receipt.kind == 'delivery' else 'read'
        if ranks[body['state']] >= ranks[target]:
            self._reserve_receipt(body, receipt, check, create=False)
            if check() is not True:
                raise MeshOutboxError()
            return self._progress(body)
        self._reserve_receipt(body, receipt, check)
        promoted = {**body, 'state': target,
                    'receipts': body['receipts'] + [dict(wire=wire, verified_at=now)]}
        return self._progress(self._put(promoted, body['revision'], check))

    @_admitted
    def read_sent(self, message_id, *, access_check):
        """Return original text only after both original approval and progress proofs verify."""
        body = self._load(message_id, access_check)
        action = self._action(body, 'read_sent')
        if access_check() is not True or self._check(action) is not True:
            raise MeshOutboxError()
        return SentMessage(self._progress(body), action.recipient, body['approved']['content'],
                           body['approved']['issued_at'])
