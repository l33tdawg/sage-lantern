"""Encrypted, signed, bounded contact registry; no discovery or radio authority.

One CAS index reserves capacity before contact creation. Missing reserved rows
remain charged and fail closed until explicitly reconciled with the same card.
Raw self-signed cards are reverified on reads. Owner signatures bind actor, kind,
UUID and row revision, not whole-database rollback resistance. Independent live
inventory/binding policy is mandatory for resolution; factory/FD ownership is
external. No keys/files, auto-retries, advertisements, eviction or transmission.
"""

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
import re
import struct
import threading
import time
import uuid

from nacl.signing import VerifyKey

from .mesh_contacts import verify_contact_card, verify_contact_card_signature
from .meshcore_contacts import ContactInventory
from .message_contacts import RecipientID
from .workflow_store import MAX_REVISION, WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore


MAX_CONTACTS = 256
INDEX_SCHEMA = 'sage.lantern.mesh-contact-index.v1'
CONTACT_SCHEMA = 'sage.lantern.mesh-contact-record.v1'
KIND = 'mesh_inbound'
NAMESPACE = uuid.UUID('8967f8dc-e1d8-4fa1-bb5d-3a4a4cde77ce')
METHOD = 'LANTERN-CONTACT-REGISTRY'
PATH = '/sage-lantern/mesh-contact-registry/v1'


class RegistryError(RuntimeError):
    def __init__(self):
        super().__init__('mesh_contact_registry_unavailable')


class RegistryOutcomeUnknown(RegistryError):
    def __init__(self):
        RuntimeError.__init__(self, 'mesh_contact_registry_outcome_unknown')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def _actor(value):
    if type(value) is not RecipientID:
        raise RegistryError()
    return asdict(value)


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        raise RegistryError()


def registry_index_id(owner):
    return str(uuid.uuid5(NAMESPACE, _canonical(dict(schema=INDEX_SCHEMA, owner=_actor(owner), kind=KIND)).decode()))


def contact_record_id(owner, actor):
    return str(uuid.uuid5(NAMESPACE, _canonical(dict(schema=CONTACT_SCHEMA, owner=_actor(owner),
                                                    actor=_actor(actor), kind=KIND)).decode()))


class MeshContactRegistry:
    def __init__(self, store, identity, *, owner, inventory=None, verify_binding=None, clock=time.time):
        try:
            if (type(store) is not WorkflowStore or type(owner) is not RecipientID
                    or store.storage.actor != owner or identity.agent_id != owner.agent_id
                    or not callable(identity.sign_request) or not callable(clock)
                    or inventory is not None and not callable(inventory)
                    or verify_binding is not None and not callable(verify_binding)):
                raise RegistryError()
        except Exception:
            raise RegistryError() from None
        self.store, self.identity, self.owner = store, identity, owner
        self.inventory, self.verify_binding, self.clock = inventory, verify_binding, clock
        self.index_id = registry_index_id(owner)
        self._mutex = threading.Lock()
        self._last = None
        self._clock_failed = False

    def _now(self):
        now = self.clock()
        if (self._clock_failed or type(now) not in (int, float) or not math.isfinite(now)
                or not 1 <= now <= (1 << 53) - 1 or self._last is not None and now < self._last):
            self._clock_failed = True
            raise RegistryError()
        self._last = now
        return now

    def _access(self, check):
        if (not callable(check) or check() is not True or self.store.storage.actor != self.owner
                or self.identity.agent_id != self.owner.agent_id):
            raise RegistryError()
        return True

    @contextmanager
    def _operation(self, check):
        if not self._mutex.acquire(blocking=False):
            raise RegistryError()
        try:
            self._access(check)
            self._now()
            yield lambda: self._access(check)
            self._access(check)
        except WorkflowOutcomeUnknown:
            raise RegistryOutcomeUnknown() from None
        except Exception:
            raise RegistryError() from None
        finally:
            self._mutex.release()

    def _read(self, identifier, schema, check):
        try:
            record = self.store.get(identifier, kind=KIND, access_check=check)
        except WorkflowNotFound:
            return None
        return self._verify(record, identifier, schema)

    def _verify(self, record, identifier, schema):
        proof = record['payload']
        if type(proof) is not dict or set(proof) != {'body', 'timestamp', 'nonce', 'signature'}:
            raise RegistryError()
        body = proof['body']
        fields = {'schema', 'owner', 'kind', 'record_id', 'revision', 'members' if schema == INDEX_SCHEMA else 'card'}
        if (type(body) is not dict or set(body) != fields or body['schema'] != schema
                or body['owner'] != _actor(self.owner) or body['kind'] != KIND
                or body['record_id'] != identifier or record['record_id'] != identifier
                or record['kind'] != KIND or record['agent_id'] != self.owner.agent_id
                or type(body['revision']) is not int or body['revision'] != record['revision']
                or not 1 <= body['revision'] <= MAX_REVISION):
            raise RegistryError()
        for field, size in (('nonce', 16), ('signature', 128)):
            if type(proof[field]) is not str or re.fullmatch('[0-9a-f]{' + str(size) + '}', proof[field]) is None:
                raise RegistryError()
        stamp = proof['timestamp']
        if (type(stamp) is not str or re.fullmatch('[1-9][0-9]{0,15}', stamp) is None
                or int(stamp) > (1 << 53) - 1):
            raise RegistryError()
        canonical = METHOD.encode() + b' ' + PATH.encode() + b'\n' + _canonical(body)
        message = hashlib.sha256(canonical).digest() + struct.pack('>q', int(stamp)) + bytes.fromhex(proof['nonce'])
        VerifyKey(bytes.fromhex(self.owner.agent_id)).verify(message, bytes.fromhex(proof['signature']))
        if schema == INDEX_SCHEMA:
            members = body['members']
            if type(members) is not list or len(members) > MAX_CONTACTS:
                raise RegistryError()
            for member in members:
                _uuid(member)
            if members != sorted(set(members)) or identifier != self.index_id:
                raise RegistryError()
        else:
            if type(body['card']) is not str:
                raise RegistryError()
            previous = verify_contact_card_signature(body['card'].encode('utf-8'))
            if identifier != contact_record_id(self.owner, previous.recipient):
                raise RegistryError()
        return body

    def _write(self, identifier, schema, expected_revision, value, check):
        body = dict(schema=schema, owner=_actor(self.owner), kind=KIND, record_id=identifier,
                    revision=expected_revision + 1, **value)
        headers = self.identity.sign_request(METHOD, PATH, _canonical(body), timestamp=int(self._now()))
        if headers.get('X-Agent-ID') != self.owner.agent_id:
            raise RegistryError()
        proof = dict(body=body, timestamp=headers['X-Timestamp'], nonce=headers['X-Nonce'], signature=headers['X-Signature'])
        self._verify(dict(payload=proof, record_id=identifier, revision=expected_revision + 1,
                          kind=KIND, agent_id=self.owner.agent_id), identifier, schema)
        return self._verify(self.store.put(identifier, KIND, expected_revision, proof, access_check=check), identifier, schema)

    def put(self, card, *, known_actor, access_check):
        """Explicit current signed update. Re-submit identical card/UUID after unknown."""
        with self._operation(access_check) as check:
            _actor(known_actor)
            incoming = verify_contact_card(card, now=self._now(), known_actor=known_actor)
            identifier = contact_record_id(self.owner, known_actor)
            index = self._read(self.index_id, INDEX_SCHEMA, check)
            members = index['members'] if index else []
            previous = self._read(identifier, CONTACT_SCHEMA, check)
            if identifier not in members:
                if previous is not None or len(members) >= MAX_CONTACTS:
                    raise RegistryError()
                self._write(self.index_id, INDEX_SCHEMA, index['revision'] if index else 0,
                            dict(members=sorted([*members, identifier])), check)
            if previous is not None:
                historical = verify_contact_card_signature(previous['card'].encode('utf-8'), known_actor=known_actor)
                incoming = verify_contact_card(card, now=self._now(), known_actor=known_actor, previous=historical)
                if asdict(incoming) == asdict(historical):
                    return incoming
            self._write(identifier, CONTACT_SCHEMA, previous['revision'] if previous else 0,
                        dict(card=card.decode('utf-8')), check)
            return verify_contact_card(card, now=self._now(), known_actor=known_actor)

    def _cards(self, check):
        index = self._read(self.index_id, INDEX_SCHEMA, check)
        if index is None:
            return []
        cards = []
        for identifier in index['members']:
            row = self._read(identifier, CONTACT_SCHEMA, check)
            if row is None:
                raise RegistryError()
            cards.append(row['card'].encode('utf-8'))
        latest = self._read(self.index_id, INDEX_SCHEMA, check)
        if latest != index:
            raise RegistryError()
        return cards

    def historical(self, actor, *, access_check):
        with self._operation(access_check) as check:
            identifier = contact_record_id(self.owner, actor)
            index = self._read(self.index_id, INDEX_SCHEMA, check)
            if index is None or identifier not in index['members']:
                raise RegistryError()
            row = self._read(identifier, CONTACT_SCHEMA, check)
            if row is None:
                raise RegistryError()
            return verify_contact_card_signature(row['card'].encode('utf-8'), known_actor=actor)

    def list_current(self, *, access_check):
        """Current self-consenting cards only; not an enrolled/trusted directory."""
        with self._operation(access_check) as check:
            cards = self._cards(check)
            result = []
            now = self._now()
            for raw in cards:
                previous = verify_contact_card_signature(raw)
                if previous.issued_at <= now < previous.expires_at and previous.findable and previous.messaging_allowed:
                    result.append(verify_contact_card(raw, now=now))
            return tuple(result)

    def resolve(self, full_radio_key, *, access_check):
        """No prefix input. Trusted policy must verify possession/enrollment as required."""
        with self._operation(access_check) as check:
            if (type(full_radio_key) is not bytes or len(full_radio_key) != 32
                    or self.inventory is None or self.verify_binding is None):
                raise RegistryError()
            matches = []
            for raw in self._cards(check):
                previous = verify_contact_card_signature(raw)
                if previous.radio_public_key == full_radio_key.hex():
                    matches.append(raw)
            if len(matches) != 1:
                raise RegistryError()
            card = verify_contact_card(matches[0], now=self._now())
            if card.findable is not True or card.messaging_allowed is not True:
                raise RegistryError()
            inventory = self.inventory()
            if type(inventory) is not ContactInventory:
                raise RegistryError()
            inventory.unique_prefix(full_radio_key)
            if self.verify_binding(self.owner, card, full_radio_key) is not True:
                raise RegistryError()
            check()
            latest = self._read(contact_record_id(self.owner, card.recipient), CONTACT_SCHEMA, check)
            if latest is None or latest['card'].encode('utf-8') != matches[0]:
                raise RegistryError()
            return verify_contact_card(matches[0], now=self._now(), known_actor=card.recipient)
