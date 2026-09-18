"""Explicit replacement of one expired stored peer card; evidence first, no radio.

A stored peer card can become permanently unmatchable when it was signed with a
revision that no later issuance from that peer can exceed (an artifact of ad-hoc
issuance, e.g. revision == issued_at). The registry's import fence then refuses
every fresh card from that peer as a rollback regression, and the registry has
no delete or retire route. This module performs the one narrow repair:

* the stored card must already be outside its validity window;
* the incoming card must currently be valid, self-signed, bound to the same
  chain, agent and full radio key, and announced by the installed peer policy;
* the incoming card's signed `issued_at` must be strictly later than the
  stored card's, so the repair can only ever move forward in issuance time and
  can never roll a revocation back to an older card;
* a create-only evidence record (exact stored and incoming bytes) is written
  before the record row is CAS-replaced at its current revision.

It never creates index members, opens the radio, sends, receives, or claims
delivery. Root operator only, and only with the exact incoming card digest
named on the command line. The CLI can also run as a plain script against the
installed package (`python .../mesh_contact_registry_recovery.py`) because it
uses absolute package imports.
"""

import hashlib
import json
import os
import re
import struct
import sys
import time
import uuid
from dataclasses import asdict

if __name__ == "__main__":  # direct appliance execution against the installed package
    sys.path.insert(0, "/opt/sage-lantern/venv/lib/python3.11/site-packages")

from sage_lantern.mesh_contact_registry import (
    CONTACT_SCHEMA, INDEX_SCHEMA, KIND, METHOD, PATH, MeshContactRegistry,
    contact_record_id, registry_index_id)
from sage_lantern.mesh_contacts import verify_contact_card, verify_contact_card_signature
from sage_lantern.workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore


EVIDENCE_SCHEMA = "sage.lantern.contact-record-recovery.v1"
EVIDENCE_ROOT = "/var/lib/sage-lantern-contact-recovery"
DECISION = "expired_artifact_superseded"
MAX_REASON = 512


class ContactRecoveryError(RuntimeError):
    def __init__(self, code="contact_recovery_unavailable"):
        self.code = code
        super().__init__(code)


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def _reason(value):
    if (type(value) is not str or not value.strip() or "\x00" in value
            or len(value.encode("utf-8")) > MAX_REASON):
        raise ContactRecoveryError("invalid_reason")
    return value


def _card_view(card):
    contact = verify_contact_card_signature(card)
    return {
        "sha256": hashlib.sha256(card).hexdigest(),
        "card": card.decode("utf-8"),
        "chain_id": contact.chain_id,
        "agent_id": contact.agent_id,
        "radio_public_key": contact.radio_public_key,
        "display_name": contact.display_name,
        "revision": contact.revision,
        "issued_at": contact.issued_at,
        "expires_at": contact.expires_at,
        "findable": contact.findable,
        "messaging_allowed": contact.messaging_allowed,
    }


class ContactRecordRecovery:
    """Plan (read-only) and apply (evidence-first CAS) for one stored peer card."""

    def __init__(self, store, identity, *, owner, peers, clock=time.time, access_check=lambda: True):
        if type(store) is not WorkflowStore or store.storage.actor != owner:
            raise ContactRecoveryError("store_binding")
        if identity.agent_id != owner.agent_id:
            raise ContactRecoveryError("identity_binding")
        self.store, self.identity, self.owner = store, identity, owner
        self.peers, self.clock, self.access = tuple(peers), clock, access_check
        self.index_id = registry_index_id(owner)

    def _row(self, identifier):
        try:
            return self.store.get(identifier, kind=KIND, access_check=self.access)
        except WorkflowNotFound:
            return None

    def plan(self, card):
        """Read-only verdict; returns a dict, never writes and never raises on refusal."""
        now = self.clock()
        try:
            incoming = verify_contact_card(card, now=now)
        except Exception:
            return {"verdict": "refused_invalid_card"}
        identifier = contact_record_id(self.owner, incoming.recipient)
        index = self._row(self.index_id)
        row = self._row(identifier)
        report = {"verdict": None, "record_id": identifier, "index_id": self.index_id, "now": now,
                  "incoming": _card_view(card)}
        if index is None or index["payload"]["body"]["schema"] != INDEX_SCHEMA:
            report["verdict"] = "refused_no_index"
            return report
        report["index_revision"] = index["revision"]
        if identifier not in index["payload"]["body"]["members"]:
            report["verdict"] = "refused_not_indexed"
            return report
        if row is None:
            report["verdict"] = "refused_record_missing"
            return report
        stored_card = row["payload"]["body"]["card"].encode("utf-8")
        try:
            stored = verify_contact_card_signature(stored_card)
        except Exception:
            report["verdict"] = "refused_stored_unverifiable"
            return report
        report["stored"] = _card_view(stored_card)
        report["record_revision"] = row["revision"]
        if incoming.recipient != stored.recipient:
            report["verdict"] = "refused_different_actor"
            return report
        if (incoming.chain_id != stored.chain_id or incoming.agent_id != stored.agent_id
                or incoming.radio_public_key != stored.radio_public_key):
            report["verdict"] = "refused_binding_changed"
            return report
        if incoming.revision == stored.revision:
            report["verdict"] = ("already_applied" if asdict(incoming) == asdict(stored) else "refused_same_revision")
            return report
        if now < stored.expires_at:
            report["verdict"] = "refused_stored_still_valid"
            return report
        if incoming.issued_at <= stored.issued_at:
            report["verdict"] = "refused_not_monotonic"
            return report
        if not any(incoming.chain_id == peer.get("chain_id") and incoming.agent_id == peer.get("agent_id")
                   and incoming.radio_public_key == peer.get("radio_public_key") for peer in self.peers):
            report["verdict"] = "refused_not_in_peer_policy"
            return report
        if incoming.messaging_allowed is not True:
            report["verdict"] = "refused_messaging_not_allowed"
            return report
        report["verdict"] = "ready"
        return report

    def recovery_id(self, report):
        return hashlib.sha256(canonical({
            "schema": EVIDENCE_SCHEMA, "record_id": report["record_id"],
            "stored_sha256": report["stored"]["sha256"],
            "incoming_sha256": report["incoming"]["sha256"]})).hexdigest()

    def _proof(self, record_id, revision, card):
        body = dict(schema=CONTACT_SCHEMA, owner=dict(chain_id=self.owner.chain_id, agent_id=self.owner.agent_id),
                    kind=KIND, record_id=record_id, revision=revision, card=card.decode("utf-8"))
        headers = self.identity.sign_request(METHOD, PATH, canonical(body), timestamp=int(self.clock()))
        if headers.get("X-Agent-ID") != self.owner.agent_id:
            raise ContactRecoveryError("sign_binding")
        return dict(body=body, timestamp=headers["X-Timestamp"], nonce=headers["X-Nonce"],
                    signature=headers["X-Signature"])

    def apply(self, card, *, acknowledged_sha256, reason, evidence_root=EVIDENCE_ROOT, context=None):
        """Write create-only evidence, then CAS the record row. Never rewrites evidence."""
        if not isinstance(acknowledged_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", acknowledged_sha256) is None:
            raise ContactRecoveryError("invalid_acknowledgement")
        report = self.plan(card)
        if report["verdict"] == "already_applied":
            return dict(report, applied=False, already_applied=True)
        if report["verdict"] != "ready":
            raise ContactRecoveryError(report["verdict"])
        if report["incoming"]["sha256"] != acknowledged_sha256:
            raise ContactRecoveryError("acknowledgement_mismatch")
        reason = _reason(reason)
        recovery_id = self.recovery_id(report)
        evidence = dict(schema=EVIDENCE_SCHEMA, recovery_id=recovery_id, unit=None,
                        record_id=report["record_id"], index_id=report["index_id"],
                        index_revision=report["index_revision"], record_revision=report["record_revision"],
                        stored=report["stored"], incoming=report["incoming"], decision=DECISION,
                        reason=reason, created_at=int(report["now"]), context=context or {})
        evidence_path = os.path.join(evidence_root, recovery_id, "evidence.json")
        os.makedirs(os.path.join(evidence_root, recovery_id), mode=0o700, exist_ok=True)
        os.chmod(evidence_root, 0o700)
        os.chmod(os.path.join(evidence_root, recovery_id), 0o700)
        if os.path.exists(evidence_path):
            with open(evidence_path, "rb") as handle:
                existing = json.loads(handle.read())
            if existing.get("recovery_id") != recovery_id or existing.get("incoming", {}).get("sha256") != acknowledged_sha256:
                raise ContactRecoveryError("evidence_conflict")
        else:
            descriptor = os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                handle.write(json.dumps(evidence, indent=2, sort_keys=True))
                handle.flush()
                os.fsync(handle.fileno())
            directory = os.open(os.path.dirname(evidence_path), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        try:
            saved = self.store.put(report["record_id"], KIND, report["record_revision"],
                                   self._proof(report["record_id"], report["record_revision"] + 1, card),
                                   access_check=self.access)
        except WorkflowOutcomeUnknown:
            raise ContactRecoveryError("record_outcome_unknown") from None
        except Exception:
            raise ContactRecoveryError("record_rejected") from None
        if saved["payload"]["body"]["card"].encode("utf-8") != card:
            raise ContactRecoveryError("record_rejected")
        registry = MeshContactRegistry(self.store, self.identity, owner=self.owner, clock=self.clock)
        current = registry.list_current(access_check=self.access)
        if verify_contact_card(card, now=self.clock()) not in current:
            raise ContactRecoveryError("registry_verification_failed")
        return dict(report, applied=True, already_applied=False, evidence=evidence_path,
                    evidence_sha256=hashlib.sha256(canonical(evidence)).hexdigest(),
                    record_revision=saved["revision"], registry_current=len(current))


def unresolved_attempt_ids(root="/var/lib/sage-lantern-radio-operator", limit=32):
    """Read-only list of root operator attempts with no confirmed response."""
    found = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return found
    for name in names:
        directory = os.path.join(root, name)
        if (os.path.isfile(os.path.join(directory, "intent.json"))
                and not os.path.isfile(os.path.join(directory, "response.json"))):
            found.append(name)
            if len(found) >= limit:
                break
    return found


def main(argv=None):
    """Root-only operator entry point on the appliance itself."""
    import argparse

    parser = argparse.ArgumentParser(description="Inspect or repair one expired stored peer card")
    parser.add_argument("--state", action="store_true", help="read-only verdict; performs no write")
    parser.add_argument("--apply", action="store_true", help="write evidence, then CAS the stored card")
    parser.add_argument("--card-file", required=True, help="file holding the exact incoming signed card")
    parser.add_argument("--acknowledge-card-sha256", help="exact incoming card sha256 to acknowledge")
    parser.add_argument("--reason", default="operator acknowledged an expired stored peer card",
                        help="bounded reason recorded with the repair")
    parser.add_argument("--evidence-root", default=EVIDENCE_ROOT)
    arguments = parser.parse_args(argv)
    if arguments.state == arguments.apply:
        parser.error("choose exactly one of --state or --apply")
    if arguments.apply and not arguments.acknowledge_card_sha256:
        parser.error("--apply requires --acknowledge-card-sha256")
    if os.geteuid() != 0:
        parser.error("contact record recovery requires root on the appliance")

    from sage_sdk import SageClient

    from sage_lantern.message_contacts import RecipientID
    from sage_lantern.message_storage import MessageStorageProbe
    from sage_lantern.mesh_daemon import load_config, load_identity

    config = load_config()
    identity, descriptor = load_identity(config)
    try:
        with open(arguments.card_file, "rb") as handle:
            card = handle.read().strip()
        with SageClient(config["endpoint"], identity, timeout=5, trust_env=False) as client:
            owner = RecipientID(chain_id=config["chain_id"], agent_id=config["agent_id"])
            probe = MessageStorageProbe(client, actor=owner, endpoint=config["endpoint"])
            store = WorkflowStore(client, storage=probe)
            recovery = ContactRecordRecovery(store, identity, owner=owner, peers=config["peers"])
            if arguments.state:
                result = recovery.plan(card)
            else:
                result = recovery.apply(card, acknowledged_sha256=arguments.acknowledge_card_sha256,
                                        reason=arguments.reason, evidence_root=arguments.evidence_root,
                                        context=dict(unit=config["unit_id"],
                                                     unresolved_operator_attempts=unresolved_attempt_ids()))
    finally:
        os.close(descriptor)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
