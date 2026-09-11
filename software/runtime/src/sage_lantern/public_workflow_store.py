"""Protected auxiliary public history; never public acceptance or send authority.

Access guards are trusted generation-bound callbacks. The separate drain guard
must fence producers and remain valid only until workers join, before vault lock.
No retries, disk persistence, provider activation or canonical SDK calls occur.
Scans bound all actor workflows before filtering; pages are not an atomic snapshot.
Only a producer-fenced drain lease can turn a pending scan into drain evidence.
Source approvals require the existing storage-actor identity injected by trusted
startup. No keys are loaded or generated here. A domain-separated Ed25519 proof
binds the private source UUID, proposal operation/hash, complete actor/chain/domain
binding and approval timestamp. Only a newly authorized preparation is signed;
existing unsigned rows are never upgraded. Missing identity denies source status.
"""

from dataclasses import asdict
from datetime import datetime, timezone
import json
import re
import uuid

from nacl.signing import SigningKey, VerifyKey

from .community_public import CommunityPublic, PublicProposal, PublicReceipt
from .workflow_store import WorkflowNotFound, WorkflowStore, WorkflowUnavailable, _identifier, _payload


SCHEMA = "sage.lantern.public-workflow.v1"
NAMESPACE = uuid.UUID("662c90b2-e814-4e5d-8ddf-1e73faaf879b")
SOURCE_DOMAIN = b"SAGE-LANTERN-PUBLIC-SOURCE-APPROVAL-v1\x00"


def _check(condition):
    if not condition:
        raise WorkflowUnavailable()


def _digest(value):
    _check(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    return value


def _date(value):
    try:
        _check(type(value) is str and len(value) <= 40)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _check(parsed.tzinfo is not None)
        return parsed.astimezone(timezone.utc)
    except Exception:
        raise WorkflowUnavailable() from None


def _cancelled(value):
    return type(value) is dict and set(value) == {"cancelled"} and value["cancelled"] is True


def _http_result(value):
    if _cancelled(value):
        return
    _check(type(value) is dict and set(value) == {"id", "state", "created_at"})
    _check(type(value["id"]) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value["id"]) is not None)
    _check(type(value["state"]) is str and value["state"] in {"pending", "sent"})
    _date(value["created_at"])


class PublicWorkflowStore:
    def __init__(self, workflow, *, chain_id, domain, author, access_check, drain_check, max_records=4096,
                 identity=None):
        _check(isinstance(workflow, WorkflowStore) and callable(access_check) and callable(drain_check))
        _check(type(max_records) is int and 1 <= max_records <= 4096)
        self._workflow = workflow
        self._access = access_check
        self._drain = drain_check
        self._maximum = max_records
        self._public = CommunityPublic(None, None, chain_id=chain_id, domain=domain, author=author)
        actor = workflow.storage.actor
        self._binding = dict(storage_chain=actor.chain_id, storage_actor=actor.agent_id,
                             chain_id=chain_id, domain=domain, author=author)
        self._identity = identity
        if identity is not None:
            try:
                _check(isinstance(identity._signing_key, SigningKey)
                       and identity.agent_id == actor.agent_id
                       and bytes(identity._signing_key.verify_key).hex() == actor.agent_id)
            except Exception:
                raise WorkflowUnavailable() from None
        self.sdk_journal = _SDKJournal(self)

    def _source_bytes(self, payload):
        body = dict(schema="sage.lantern.public-source-approval.v1", binding=self._binding,
                    source_id=_identifier(payload["source_id"]), operation_id=payload["operation_id"],
                    content_hash=payload["fingerprint"], created_at=payload["created_at"])
        return SOURCE_DOMAIN + json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=True, allow_nan=False).encode("utf-8")

    def _verify_source(self, payload):
        try:
            signature = payload["source_proof"]
            _check(type(signature) is str and re.fullmatch(r"[0-9a-f]{128}", signature) is not None)
            VerifyKey(bytes.fromhex(self._binding["storage_actor"])).verify(
                self._source_bytes(payload), bytes.fromhex(signature))
        except Exception:
            raise WorkflowUnavailable() from None

    def _id(self, scope, operation):
        _identifier(operation)
        value = json.dumps([self._binding, scope, operation], sort_keys=True, separators=(",", ":"))
        return str(uuid.uuid5(NAMESPACE, value))

    def _proposal(self, proposal):
        try:
            self._public._proposal(proposal)
            _identifier(proposal.operation_id)
            return asdict(proposal)
        except Exception:
            raise WorkflowUnavailable() from None

    def _receipt(self, receipt, proposal):
        _check(type(receipt) is PublicReceipt)
        _check(receipt.operation_id == proposal.operation_id and receipt.content_hash == proposal.content_hash
               and receipt.intended_chain_id == proposal.chain_id)
        _identifier(receipt.memory_id)
        _digest(receipt.tx_hash)
        _check(receipt.committed_height is None or type(receipt.committed_height) is int
               and 1 <= receipt.committed_height <= 2 ** 53 - 1)
        return asdict(receipt)

    def _validate(self, row, scope, operation):
        try:
            payload = row["payload"]
            keys = {"schema", "binding", "namespace", "operation_id", "fingerprint", "result"}
            if scope == "sdk":
                keys |= {"proposal", "created_at", "reserved"}
                if "source_id" in payload:
                    _identifier(payload["source_id"])
                    keys.add("source_id")
                if "source_proof" in payload:
                    self._verify_source(payload)
                    keys.add("source_proof")
            _check(type(payload) is dict and set(payload) == keys and payload["schema"] == SCHEMA
                   and payload["binding"] == self._binding and payload["namespace"] == scope
                   and payload["operation_id"] == operation and row["record_id"] == self._id(scope, operation)
                   and row["kind"] == "public_proposal")
            _digest(payload["fingerprint"])
            result = payload["result"]
            _check(result is None or type(result) is dict)
            if scope == "sdk":
                proposal = PublicProposal(**payload["proposal"])
                _check(self._proposal(proposal) == payload["proposal"] and proposal.operation_id == operation
                       and proposal.content_hash == payload["fingerprint"] and type(payload["reserved"]) is bool)
                _date(payload["created_at"])
                if result is not None:
                    if not _cancelled(result):
                        _check(payload["reserved"] is True)
                        self._receipt(PublicReceipt(**result), proposal)
            elif result is not None:
                _http_result(result)
            _payload(payload)
            return row
        except Exception:
            raise WorkflowUnavailable() from None

    def _get(self, scope, operation, guard=None):
        row = self._workflow.get(self._id(scope, operation), kind="public_proposal",
                                 access_check=self._access if guard is None else guard)
        return self._validate(row, scope, operation)

    def _put(self, scope, operation, revision, payload):
        _payload(payload)
        row = self._workflow.put(self._id(scope, operation), "public_proposal", revision, payload,
                                 access_check=self._access)
        return self._validate(row, scope, operation)

    def _base(self, scope, operation, fingerprint):
        self._id(scope, operation)
        return dict(schema=SCHEMA, binding=dict(self._binding), namespace=scope,
                    operation_id=operation, fingerprint=_digest(fingerprint), result=None)

    def available(self):
        self._workflow.list(limit=1, access_check=self._access)
        return True

    def source_approval_available(self):
        return self._identity is not None and self._access() is True

    def reserve(self, identifier, fingerprint):
        payload = self._base("http", identifier, fingerprint)
        try:
            existing = self._get("http", identifier)
        except WorkflowNotFound:
            self._put("http", identifier, 0, payload)
            return True
        _check(existing["payload"]["fingerprint"] == fingerprint)
        return False

    def get(self, identifier):
        payload = self._get("http", identifier)["payload"]
        return dict(fingerprint=payload["fingerprint"], result=payload["result"])

    def complete(self, identifier, result):
        _http_result(result)
        detached, _ = _payload(result)
        return self._complete("http", identifier, detached)

    def _complete(self, scope, identifier, result):
        row = self._get(scope, identifier)
        payload = row["payload"]
        if payload["result"] is not None:
            _check(payload["result"] == result)
            return True
        if scope == "sdk":
            _check(payload["reserved"] is True)
            if not _cancelled(result):
                self._receipt(PublicReceipt(**result), PublicProposal(**payload["proposal"]))
        self._put(scope, identifier, row["revision"], {**payload, "result": result})
        return True

    def prepare_public(self, proposal, created_at, *, source_id=None):
        encoded = self._proposal(proposal)
        _date(created_at)
        payload = {**self._base("sdk", proposal.operation_id, proposal.content_hash),
                   "proposal": encoded, "created_at": created_at, "reserved": False}
        if source_id is not None:
            _check(self._identity is not None)
            payload["source_id"] = _identifier(source_id)
        try:
            row = self._get("sdk", proposal.operation_id)
        except WorkflowNotFound:
            if source_id is not None:
                _check(self._access() is True)
                payload["source_proof"] = self._identity._signing_key.sign(self._source_bytes(payload)).signature.hex()
                _check(self._access() is True)
            self._put("sdk", proposal.operation_id, 0, payload)
            return True
        _check(row["payload"]["proposal"] == encoded and row["payload"]["created_at"] == created_at)
        _check(row["payload"].get("source_id") == source_id)
        if source_id is not None:
            self._verify_source(row["payload"])
        _check(row["payload"]["result"] != {"cancelled": True})
        return True

    def prepare_public_source(self, proposal, created_at, source_id):
        return self.prepare_public(proposal, created_at, source_id=_identifier(source_id))

    def public_sources(self, source_ids):
        _check(self._identity is not None)
        _check(type(source_ids) is list and len(source_ids) <= 20)
        _check(len(set(_identifier(value) for value in source_ids)) == len(source_ids))
        selected = {}
        for row in self._scan(self._access):
            payload = row["payload"]
            if payload["namespace"] != "sdk":
                continue
            _check("source_id" in payload)
            self._verify_source(payload)
            source = payload["source_id"]
            if source not in source_ids:
                continue
            previous = selected.get(source)
            if previous is None or (_date(payload["created_at"]), payload["operation_id"]) > (_date(previous["created_at"]), previous["operation_id"]):
                selected[source] = payload
        return {source: None if source not in selected else dict(
            proposal=PublicProposal(**selected[source]["proposal"]),
            receipt=PublicReceipt(**selected[source]["result"]) if selected[source]["result"] is not None and not _cancelled(selected[source]["result"]) else None,
            created_at=selected[source]["created_at"]) for source in source_ids}

    def record_public(self, proposal, receipt, created_at):
        encoded = self._proposal(proposal)
        result = self._receipt(receipt, proposal)
        row = self._get("sdk", proposal.operation_id)
        _check(row["payload"]["proposal"] == encoded and row["payload"]["created_at"] == created_at)
        return self._complete("sdk", proposal.operation_id, result)

    def cancel_prepared_public(self, proposal, created_at):
        """CAS-tombstone only exact preparation with no SDK reservation."""
        encoded = self._proposal(proposal)
        row = self._get("sdk", proposal.operation_id)
        payload = row["payload"]
        _check(payload["proposal"] == encoded and payload["created_at"] == created_at)
        if _cancelled(payload["result"]):
            return True
        _check(payload["reserved"] is False and payload["result"] is None)
        self._put("sdk", proposal.operation_id, row["revision"], {**payload, "result": {"cancelled": True}})
        return True

    def _scan(self, guard):
        rows = self._workflow.scan(limit=50, max_records=self._maximum, access_check=guard)
        selected = []
        for row in rows:
            if row["kind"] != "public_proposal":
                continue
            payload = row["payload"]
            _check(type(payload) is dict and payload.get("schema") == SCHEMA)
            binding = payload.get("binding")
            _check(type(binding) is dict and set(binding) == set(self._binding))
            _check(payload.get("namespace") in {"http", "sdk"})
            operation = _identifier(payload.get("operation_id"))
            value = json.dumps([binding, payload["namespace"], operation], sort_keys=True, separators=(",", ":"))
            _check(row["record_id"] == str(uuid.uuid5(NAMESPACE, value)))
            if payload.get("binding") != self._binding:
                continue
            selected.append(self._validate(row, payload["namespace"], payload["operation_id"]))
        _check(guard() is True)
        return selected

    def pending_count(self):
        return sum(row["payload"]["result"] is None for row in self._scan(self._drain))

    def list_public(self, *, limit=21):
        _check(type(limit) is int and 1 <= limit <= 21)
        entries = []
        for row in self._scan(self._access):
            payload = row["payload"]
            if payload["namespace"] != "sdk" or payload["result"] is None or payload["result"] == {"cancelled": True}:
                continue
            entries.append(dict(proposal=PublicProposal(**payload["proposal"]),
                                receipt=PublicReceipt(**payload["result"]), created_at=payload["created_at"]))
        _check(len({entry["receipt"].memory_id for entry in entries}) == len(entries))
        return sorted(entries, key=lambda entry: (_date(entry["created_at"]), entry["proposal"].operation_id), reverse=True)[:limit]

    def reconcile_public(self, operation_id):
        """Exact protected state only: never retries propose or proves acceptance."""
        payload = self._get("sdk", operation_id)["payload"]
        result = payload["result"]
        return dict(proposal=PublicProposal(**payload["proposal"]), created_at=payload["created_at"],
                    reserved=payload["reserved"], cancelled=result == {"cancelled": True},
                    receipt=PublicReceipt(**result) if result is not None and result != {"cancelled": True} else None)


class _SDKJournal:
    def __init__(self, store):
        self._store = store

    def reserve(self, identifier, fingerprint):
        _digest(fingerprint)
        row = self._store._get("sdk", identifier)
        payload = row["payload"]
        _check(payload["fingerprint"] == fingerprint)
        _check(not _cancelled(payload["result"]))
        if payload["reserved"]:
            return False
        self._store._put("sdk", identifier, row["revision"], {**payload, "reserved": True})
        return True

    def complete(self, identifier, receipt):
        if type(receipt) is PublicReceipt:
            _check(receipt.operation_id == identifier)
            result = asdict(receipt)
        else:
            _check(_cancelled(receipt))
            result = {"cancelled": True}
        return self._store._complete("sdk", identifier, result)
