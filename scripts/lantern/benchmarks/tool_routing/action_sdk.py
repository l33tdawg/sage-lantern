"""Disabled private-note adapter using the real SAGE signing/model SDK.

No credentials are loaded, no client is constructed, no runtime imports this.
Trusted construction supplies the SDK client, supervised transport and an online
node-policy verifier. This is online canonical observation, not an offline proof.
The real reviewed-submit issuer and lock/sleep/drain authority remain external.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import threading

from action_session import ActionRefused, VerifiedReceipt, fingerprint
from action_transport import SupervisedLoopbackTransport
from contract import validate_plan, unique


SCHEMA = "sage.lantern.private-note.v1"
PROVIDER = "sage-lantern-private-note-v1"
CANONICAL = "app-v25-exact-envelope-status-v1"


def _map(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if type(value) is not dict:
        raise ActionRefused("invalid_sdk_response")
    return value


def _identifier(value):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ActionRefused("invalid_sdk_identifier")
    return value


@dataclass(frozen=True)
class OnlinePrivateBinding:
    """Returned ONLY by a trusted live config/node-policy verifier.

    The verifier must bind exact client/transport, chain, canonical disclosure,
    owned domain and private encryption/export/grant policy. Echoing HTTP fields
    or inventing these references is not evidence. No default verifier exists.
    """
    client: object
    transport: object
    endpoint: str
    chain: str
    identity: str
    domain: str
    endpoint_profile: str
    node_revision: str
    private_policy_sha256: str
    canonical_disclosure: str


class PrivateSageSDKBackend:
    def __init__(self, client, transport, *, binding_verifier, classification,
                 enabled=False):
        from sage_sdk import SageClient
        if (type(client) is not SageClient or type(transport) is not SupervisedLoopbackTransport
                or type(enabled) is not bool
                or type(classification) is not int or not 1 <= classification <= 4
                or not callable(binding_verifier)):
            raise ActionRefused("trusted_sdk_construction_required")
        self.client, self.transport = client, transport
        self.binding_verifier = binding_verifier
        self.classification, self.enabled = classification, enabled
        self._evidence = {}
        self._started = set()
        self._lock = threading.Lock()
        self._registered = self._binding()

    def _binding(self):
        try:
            b = self.binding_verifier(self.client, self.transport)
        except Exception:
            raise ActionRefused("private_node_trust_unavailable") from None
        if (type(b) is not OnlinePrivateBinding or b.client is not self.client
                or b.transport is not self.transport or b.canonical_disclosure != CANONICAL
                or self.client._identity.agent_id != b.identity
                or self.client._base_url != b.endpoint
                or str(self.client._client.base_url).rstrip("/") != b.endpoint
                or self.client._client._transport is not self.transport
                or self.client._client.follow_redirects is not False
                or self.client._client.trust_env is not False
                or self.transport.endpoint != b.endpoint
                or type(b.identity) is not str or not re.fullmatch(r"[0-9a-f]{64}", b.identity)
                or type(b.private_policy_sha256) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", b.private_policy_sha256)
                or any(type(s) is not str or not 1 <= len(s) <= 256
                       for s in (b.chain, b.domain, b.endpoint_profile, b.node_revision))
                or b.domain in {"general", "self", "meta"}
                or b.domain.startswith(("sage-", "scope:"))):
            raise ActionRefused("private_node_trust_unavailable")
        return b

    def profile_sha256(self):
        from sage_sdk import client as sdk_module
        # Bind the actual loaded SDK source, not just its claimed version.
        files = sorted(Path(sdk_module.__file__).parent.glob("*.py"))
        sources = {"sdk/" + p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        for name in ("action_sdk.py", "action_transport.py", "action_journal.py"):
            sources[name] = hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        b = self._binding()
        public = {k: v for k, v in vars(b).items() if k not in {"client", "transport"}}
        return fingerprint({"sources": sources, "node": public, "classification": self.classification})

    def _check(self, scope, access_check):
        if access_check() is not True or self._binding() != self._registered:
            raise ActionRefused("private_scope_or_policy_changed")
        b = self._registered
        if (scope.sage_identity != b.identity or scope.sage_chain != b.chain
                or scope.private_domain != b.domain or scope.sage_endpoint_profile != b.endpoint_profile):
            raise ActionRefused("private_scope_mismatch")

    def _call(self, method, arguments, scope, access_check):
        self._check(scope, access_check)
        value = getattr(self.client, method)(**arguments)
        self._check(scope, access_check)
        return value

    def _authorize(self, scope, access_check, write):
        p = _map(self._call("get_profile", {}, scope, access_check))
        c = p.get("clearance")
        if (p.get("agent_id") != scope.sage_identity or p.get("home_domain") != scope.private_domain
                or p.get("role") not in {"member", "manager"}
                or p.get("enrollment_status") != "active" or p.get("registration_status") != "active"
                or p.get("approval_required") is not False or p.get("can_read") is not True
                or (write and p.get("can_write") is not True)
                or type(c) is not int or not self.classification <= c <= 4):
            raise ActionRefused("private_profile_denied")
        grants = _map(self._call("domain_access_sample", {}, scope, access_check))
        for name in ("owned_domains", "readable_domains") + (("writable_domains",) if write else ()):
            if type(grants.get(name)) is not list or scope.private_domain not in grants[name]:
                raise ActionRefused("private_grant_denied")

    def _record(self, raw, scope):
        r = _map(raw)
        content = r.get("content")
        if (type(content) is not str or len(content.encode()) > 16384
                or r.get("submitting_agent") != scope.sage_identity
                or r.get("domain_tag") != scope.private_domain
                or r.get("memory_type") != "observation" or r.get("status") != "committed"
                or type(r.get("classification")) is not int or r["classification"] != self.classification
                or r.get("content_hash") != hashlib.sha256(content.encode()).hexdigest()):
            raise ActionRefused("canonical_private_record_mismatch")
        _identifier(r.get("memory_id"))
        try:
            note = json.loads(content, object_pairs_hook=unique)
            if (type(note) is not dict or set(note) != {"schema", "operation_id", "title", "text"}
                    or note["schema"] != SCHEMA
                    or type(note["operation_id"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", note["operation_id"])):
                raise ValueError()
            validate_plan([{"name": "save_private_note", "arguments": {k: note[k] for k in ("title", "text")}}])
        except Exception:
            raise ActionRefused("private_note_schema_mismatch") from None
        return r, note

    def _embedding(self, text, scope, access_check):
        e = self._call("embed", {"text": text}, scope, access_check)
        if (type(e) is not list or not 1 <= len(e) <= 4096
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in e)
                or not any(n != 0 for n in e)):
            raise ActionRefused("invalid_embedding")
        return e

    def invoke(self, calls, *, scope, operation_id, deadline, access_check):
        if self.enabled is not True:
            raise ActionRefused("private_sdk_disabled")
        validate_plan(calls)
        # Reject the ENTIRE plan before network activity. A device-status source
        # and its attestation are a different adapter and are not invented here.
        if any(c["name"] not in {"search_memory", "save_private_note"} for c in calls):
            raise ActionRefused("unsupported_sdk_plan")
        if type(operation_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", operation_id):
            raise ActionRefused("invalid_operation_id")
        with self._lock:
            if operation_id in self._started or len(self._started) >= 1024:
                raise ActionRefused("sdk_operation_replayed_or_capacity_exhausted")
            self._started.add(operation_id)

        def work():
            results = []
            for call in calls:
                write = call["name"] == "save_private_note"
                self._authorize(scope, access_check, write)
                if write:
                    content = json.dumps({"schema": SCHEMA, "operation_id": operation_id,
                                          **call["arguments"]}, ensure_ascii=False,
                                         sort_keys=True, separators=(",", ":"))
                    response = _map(self._call("propose", dict(content=content, memory_type="observation",
                        domain_tag=scope.private_domain, confidence=0.8, classification=self.classification,
                        provider=PROVIDER, embedding=self._embedding(content, scope, access_check)), scope, access_check))
                    memory_id = _identifier(response.get("memory_id"))
                    # A successful proposal/tx hash never supplies completion.
                    r, note = self._record(self._call("get_memory", {"memory_id": memory_id}, scope, access_check), scope)
                    if r["memory_id"] != memory_id or r["content"] != content:
                        raise ActionRefused("saved_note_content_conflict")
                    result = {"memory_id": memory_id, "note": note, "content_hash": r["content_hash"]}
                else:
                    query = call["arguments"]["query"]
                    response = _map(self._call("hybrid", dict(query=query,
                        embedding=self._embedding(query, scope, access_check), domain_tag=scope.private_domain,
                        provider=PROVIDER, top_k=5, status_filter="committed"), scope, access_check))
                    rows = response.get("results")
                    if type(rows) is not list or len(rows) > 5:
                        raise ActionRefused("invalid_recall_page")
                    result, seen = {"records": []}, set()
                    for row in rows:
                        candidate, note = self._record(row, scope)
                        memory_id = candidate["memory_id"]
                        if memory_id in seen:
                            raise ActionRefused("duplicate_recall_record")
                        seen.add(memory_id)
                        canonical, _ = self._record(self._call("get_memory", {"memory_id": memory_id}, scope, access_check), scope)
                        if any(candidate[k] != canonical[k] for k in ("memory_id", "content", "content_hash")):
                            raise ActionRefused("recall_detail_conflict")
                        result["records"].append({"memory_id": memory_id, "note": note,
                                                  "content_hash": canonical["content_hash"]})
                self._authorize(scope, access_check, write)
                results.append({"name": call["name"], "result": result})
            self._check(scope, access_check)
            return {"results": results}

        try:
            raw = self.transport.run(work, deadline=deadline, access_check=access_check)
            self._check(scope, access_check)
            # Only this adapter's independently observed result can be receipted.
            with self._lock:
                if operation_id in self._evidence:
                    raise ActionRefused("sdk_operation_evidence_exists")
                self._evidence[operation_id] = (fingerprint(raw), fingerprint(scope.binding()), fingerprint(calls))
            return raw
        except ActionRefused:
            raise
        except Exception:
            raise ActionRefused("sdk_operation_unknown") from None

    def verify(self, raw, *, scope, calls, operation_id, request_sha256):
        # Broker performs live guarded policy/scope checks around this method.
        with self._lock:
            evidence = self._evidence.pop(operation_id, None)
        expected = (fingerprint(raw), fingerprint(scope.binding()), fingerprint(calls))
        if evidence != expected or self._binding() != self._registered:
            raise ActionRefused("sdk_canonical_evidence_unavailable")
        return VerifiedReceipt(operation_id, request_sha256, expected[1], expected[2], expected[0],
            "committed" if calls[0]["name"] == "save_private_note" else "read_complete",
            "online-canonical:" + operation_id, "pinned_online_canonical_observation")
