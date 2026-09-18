"""Explicit reconciliation of one interrupted receive; no replay, no claim.

An interrupted capture is a firmware dequeue whose frame never reached durable
storage: the radio may have consumed a message this unit can never read again.
Replaying is impossible, deleting would erase the only evidence, and replacing
the configured slot UUID is forbidden (MESH_ROOT_DAEMON.md). This module records
an audit row that preserves the exact interrupted operation and payload digest,
then CAS-resets the *same* fixed slot to idle so the receiver can start again.

Nothing here opens the radio, transmits, delivers or acknowledges anything. The
audit row is written before the slot changes, so an unknown write outcome leaves
evidence rather than a silent reset. Only a root operator naming the exact
interrupted operation may run this; the CLI refuses otherwise.
"""

import hashlib
import json
import math
import re
import time
import uuid

from .mesh_receiver import _payload, _validate
from .workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore


SCHEMA = "sage.lantern.mesh-receive-audit.v1"
KIND = "mesh_inbound"
NAMESPACE = uuid.UUID("f6c1d3d2-0b26-4c58-9de1-2a7b0f4c5d10")
MAX_REASON = 512
OUTCOME = "interrupted_receive_abandoned"


class ReceiveRecoveryError(RuntimeError):
    def __init__(self, code="receive_recovery_unavailable"):
        self.code = code
        super().__init__(code)


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        raise ReceiveRecoveryError("invalid_identifier")
    parsed = uuid.UUID(value)
    if not parsed.int or str(parsed) != value:
        raise ReceiveRecoveryError("invalid_identifier")
    return value


def _reason(value):
    if (type(value) is not str or not value.strip() or "\x00" in value
            or len(value.encode("utf-8")) > MAX_REASON):
        raise ReceiveRecoveryError("invalid_reason")
    return value


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def audit_identifier(slot_id, operation_id):
    return str(uuid.uuid5(NAMESPACE, "receive-recovery:" + slot_id + ":" + operation_id))


def _bound(audit, slot_id, operation_id):
    return (type(audit) is dict and audit.get("schema") == SCHEMA and audit.get("slot_id") == slot_id
            and audit.get("operation_id") == operation_id and audit.get("outcome") == OUTCOME
            and audit.get("replayed") is False and audit.get("delivery_claim") is False)


class ReceiveRecovery:
    """Root-operator reconciliation for one fixed receive slot."""

    def __init__(self, store, *, slot_id, access_check):
        if type(store) is not WorkflowStore or not callable(access_check):
            raise ReceiveRecoveryError("invalid_recovery_configuration")
        self._store = store
        self._slot = _uuid(slot_id)
        self._access = access_check

    def _read(self):
        try:
            record = self._store.get(self._slot, kind=KIND, access_check=self._access)
        except WorkflowNotFound:
            raise ReceiveRecoveryError("receive_slot_missing") from None
        try:
            payload = record["payload"]
            _validate(payload)
        except Exception:
            raise ReceiveRecoveryError("invalid_receive_slot") from None
        if type(record.get("revision")) is not int or record["revision"] < 1:
            raise ReceiveRecoveryError("invalid_receive_slot")
        return record

    def state(self):
        """Read-only view of the slot; never mutates and never exposes frames."""
        record = self._read()
        payload = record["payload"]
        return {"slot_id": self._slot, "state": payload["state"],
                "operation_id": payload["operation_id"], "revision": record["revision"],
                "recoveries": self._recoveries()}

    def _recoveries(self, maximum=8):
        """Bounded, read-only audit history for this slot; newest first."""
        rows = self._store.scan(limit=50, max_records=256, access_check=self._access)
        found = []
        for row in rows:
            payload = row.get("payload")
            if (type(payload) is dict and payload.get("schema") == SCHEMA
                    and payload.get("slot_id") == self._slot and _bound(payload, self._slot, payload.get("operation_id"))):
                found.append({"audit_id": row["record_id"], "operation_id": payload["operation_id"],
                              "recovered_at": payload.get("recovered_at"), "reason": payload.get("reason"),
                              "original_sha256": payload.get("original_sha256")})
        found.sort(key=lambda item: (item["recovered_at"] or 0, item["audit_id"]), reverse=True)
        return found[:maximum]

    def _audit(self, operation_id):
        try:
            return self._store.get(audit_identifier(self._slot, operation_id), kind=KIND,
                                   access_check=self._access)
        except WorkflowNotFound:
            return None

    def recover(self, *, operation_id, reason, clock=time.time):
        """Preserve one interrupted capture as an audit row, then free the slot."""
        operation_id = _uuid(operation_id)
        reason = _reason(reason)
        record = self._read()
        payload = record["payload"]
        if payload["state"] == "idle":
            # A repeated run must prove the earlier audit instead of writing again.
            existing = self._audit(operation_id)
            if existing is None or not _bound(existing["payload"], self._slot, operation_id):
                raise ReceiveRecoveryError("receive_not_interrupted")
            return {"slot_id": self._slot, "state": "idle", "operation_id": operation_id,
                    "audit_id": existing["record_id"],
                    "audit_sha256": hashlib.sha256(canonical(existing["payload"])).hexdigest(),
                    "already_recovered": True, "replayed": False, "delivery_claim": False}
        if payload["state"] != "interrupted" or payload["operation_id"] != operation_id:
            raise ReceiveRecoveryError("receive_not_interrupted")
        digest = hashlib.sha256(canonical(payload)).hexdigest()
        identifier = audit_identifier(self._slot, operation_id)
        audit = {"schema": SCHEMA, "slot_id": self._slot, "operation_id": operation_id,
                 "original_revision": record["revision"], "original_sha256": digest,
                 "recovered_at": int(clock()), "reason": reason, "outcome": OUTCOME,
                 "replayed": False, "delivery_claim": False}
        existing = self._audit(operation_id)
        if existing is None:
            try:
                self._store.put(identifier, KIND, 0, audit, access_check=self._access)
            except WorkflowOutcomeUnknown:
                raise ReceiveRecoveryError("audit_outcome_unknown") from None
            except Exception:
                # A concurrent identical recovery may have created it first.
                existing = self._audit(operation_id)
                if existing is None:
                    raise ReceiveRecoveryError("audit_rejected") from None
        if existing is not None and existing["payload"] != audit:
            bound = existing["payload"]
            if (not _bound(bound, self._slot, operation_id)
                    or bound.get("original_revision") != record["revision"]
                    or bound.get("original_sha256") != digest):
                raise ReceiveRecoveryError("audit_conflict")
        try:
            saved = self._store.put(self._slot, KIND, record["revision"], _payload("idle"),
                                    access_check=self._access)
        except WorkflowOutcomeUnknown:
            raise ReceiveRecoveryError("slot_outcome_unknown") from None
        except Exception:
            raise ReceiveRecoveryError("slot_rejected") from None
        if saved["payload"] != _payload("idle"):
            raise ReceiveRecoveryError("slot_rejected")
        return {"slot_id": self._slot, "state": saved["payload"]["state"], "operation_id": operation_id,
                "audit_id": identifier, "audit_sha256": hashlib.sha256(canonical(audit)).hexdigest(),
                "already_recovered": False, "replayed": False, "delivery_claim": False}


def main(argv=None):
    """Root-only operator entry point on the appliance itself."""
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(description="Inspect or reconcile the fixed mesh receive slot")
    parser.add_argument("--state", action="store_true", help="read-only slot view; performs no write")
    parser.add_argument("--apply", action="store_true", help="preserve one interrupted receive and free the slot")
    parser.add_argument("--acknowledge-operation-id", help="exact interrupted operation id to abandon")
    parser.add_argument("--reason", default="operator acknowledged an interrupted receive",
                        help="bounded audit reason recorded with the reconciliation")
    arguments = parser.parse_args(argv)
    if arguments.state == arguments.apply:
        parser.error("choose exactly one of --state or --apply")
    if arguments.apply and not arguments.acknowledge_operation_id:
        parser.error("--apply requires --acknowledge-operation-id")
    if os.geteuid() != 0:
        parser.error("receive recovery requires root on the appliance")

    from sage_sdk import SageClient

    from .message_contacts import RecipientID
    from .message_storage import MessageStorageProbe
    from .mesh_daemon import load_config, load_identity

    config = load_config()
    identity, descriptor = load_identity(config)
    try:
        with SageClient(config["endpoint"], identity, timeout=5, trust_env=False) as client:
            probe = MessageStorageProbe(client, actor=RecipientID(chain_id=config["chain_id"],
                                                                  agent_id=config["agent_id"]),
                                        endpoint=config["endpoint"])
            store = WorkflowStore(client, storage=probe)
            recovery = ReceiveRecovery(store, slot_id=config["receiver_slot"], access_check=lambda: True)
            if arguments.state:
                result = recovery.state()
            else:
                result = recovery.recover(operation_id=arguments.acknowledge_operation_id,
                                          reason=arguments.reason)
    finally:
        os.close(descriptor)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
