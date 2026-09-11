"""Fixed-loopback REST bridge with protected CAS intent before public dispatch.

Never creates/loads identities, grants, validators, files, or acceptance proofs.
An ordinary public signer stays on the Lantern. Unknown dispatches never retry.
The operator supplies a real endpoint/chain check and a storage lifecycle lease
valid until workers join, before vault lock, so in-flight receipts can persist.
HTTP, journal and radio acknowledgments are distinct. No background worker starts.
No HTTP timestamp/auth packet is persisted. Pin the same gateway/journal for an
operation's lifetime: automatic gateway failover after uncertainty is unsafe.
An exception after admission may mean a dispatched write; it is not a retry grant.
"""

from dataclasses import asdict
import hashlib
import re
import time
import uuid

import httpx
from nacl.signing import SigningKey

from .community_public import PublicProposal, PublicReceipt
from .public_mesh_protocol import (PublicMeshError, canonical, identifier, open_request, parse, proposal_data,
                                   public_key, require, result_data, seal_result, submit_body, MAX_INTEGER)
from .workflow_store import WorkflowNotFound, WorkflowStore


SCHEMA = "sage.lantern.public-gateway-journal.v1"
NAMESPACE = uuid.UUID("8bfc7f05-f5e0-46a3-af2b-53a0e83b808b")
STATUSES = {"proposed", "validated", "committed", "challenged", "deprecated"}
MAX_RESPONSE = 65536


class LocalPublicREST:
    def __init__(self, endpoint, *, transport=None):
        require(type(endpoint) is str and re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", endpoint) is not None
                and 1 <= int(endpoint.rsplit(":", 1)[1]) <= 65535)
        self.endpoint = endpoint
        self._client = httpx.Client(base_url=endpoint, timeout=3, trust_env=False, follow_redirects=False, transport=transport)
        self._transport = self._client

    def request(self, method, path, headers, body=b""):
        require((method, path) in {("GET", "/v1/agent/me"), ("POST", "/v1/memory/submit")}
                or method == "GET" and path.startswith("/v1/memory/") and identifier(path.removeprefix("/v1/memory/")))
        require(str(self._client.base_url).rstrip("/") == self.endpoint
                and self._client is self._transport and self._client.trust_env is False and self._client.follow_redirects is False)
        deadline = time.monotonic() + 15
        with self._client.stream(method, path, headers={**headers, "Content-Type": "application/json", "Accept-Encoding": "identity"}, content=body) as response:
            require(response.status_code in {200, 201, 202} and response.headers.get("content-encoding", "identity") == "identity"
                    and time.monotonic() < deadline)
            data = bytearray()
            for chunk in response.iter_bytes():
                require(len(data) + len(chunk) <= MAX_RESPONSE and time.monotonic() < deadline)
                data.extend(chunk)
            value = parse(bytes(data))
            require(type(value) is dict)
            return value

    def close(self):
        self._client.close()


def receipt_data(value, proposal):
    require(type(value) is dict and set(value) == {"operation_id", "content_hash", "intended_chain_id", "memory_id", "tx_hash", "committed_height"})
    receipt = PublicReceipt(**value)
    require(receipt.operation_id == proposal.operation_id and receipt.content_hash == proposal.content_hash
            and receipt.intended_chain_id == proposal.chain_id)
    identifier(receipt.memory_id)
    public_key(receipt.tx_hash)
    require(receipt.committed_height is None or type(receipt.committed_height) is int and 0 < receipt.committed_height <= MAX_INTEGER)
    return value


class PublicGatewayBridge:
    def __init__(self, workflow, rest, signing_key, *, chain_id, domain, authors, clock, access_check, storage_check, backend_check):
        require(isinstance(workflow, WorkflowStore) and type(rest) is LocalPublicREST and isinstance(signing_key, SigningKey))
        require(type(authors) is frozenset and 1 <= len(authors) <= 64)
        for author in authors:
            public_key(author)
        require(all(callable(check) for check in (clock, access_check, storage_check, backend_check)))
        require(type(chain_id) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", chain_id) is not None
                and type(domain) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", domain) is not None)
        self._workflow, self._rest, self._key = workflow, rest, signing_key
        self._chain, self._domain, self._authors = chain_id, domain, authors
        self._clock, self._access, self._storage, self._backend = clock, access_check, storage_check, backend_check
        self._endpoint = rest.endpoint
        self._binding = dict(gateway=bytes(signing_key.verify_key).hex(), chain_id=chain_id, domain=domain,
                             endpoint=rest.endpoint, storage_actor=workflow.storage.actor.agent_id,
                             storage_chain=workflow.storage.actor.chain_id)

    def _check(self, request):
        require(self._access() is True and self._rest.endpoint == self._endpoint)
        now = self._clock()
        require(type(now) is int and request["issued_at"] <= now < request["expires_at"], "public_mesh_expired")
        require(self._backend(self._endpoint, self._chain) is True)
        after = self._clock()
        require(type(after) is int and now <= after < request["expires_at"] and self._access() is True)

    def _id(self, proposal):
        return str(uuid.uuid5(NAMESPACE, canonical([self._binding["storage_chain"], self._binding["storage_actor"],
                                                   self._chain, proposal.author, proposal.operation_id]).decode()))

    def _validate(self, row, proposal):
        payload = row["payload"]
        require(type(payload) is dict and set(payload) == {"schema", "binding", "proposal", "state", "receipt", "dispatch_digest"}
                and payload["schema"] == SCHEMA and payload["binding"] == self._binding
                and payload["proposal"] == proposal_data(proposal)
                and payload["state"] in {"prepared", "dispatching", "receipt"})
        if payload["state"] == "prepared":
            require(payload["receipt"] is None and payload["dispatch_digest"] is None)
        else:
            public_key(payload["dispatch_digest"])
            require((payload["state"] == "receipt") == (payload["receipt"] is not None))
            if payload["receipt"] is not None:
                receipt_data(payload["receipt"], proposal)
        return row

    def _get(self, proposal):
        try:
            return self._validate(self._workflow.get(self._id(proposal), kind="public_proposal", access_check=self._storage), proposal)
        except WorkflowNotFound:
            return None

    def _put(self, proposal, revision, payload):
        row = self._workflow.put(self._id(proposal), "public_proposal", revision, payload, access_check=self._storage)
        return self._validate(row, proposal)

    def _profile(self, request):
        self._check(request)
        profile = self._rest.request("GET", "/v1/agent/me", request["profile_auth"])
        self._check(request)
        require(profile.get("agent_id") == request["sender"] and profile.get("role") == "member"
                and profile.get("enrollment_status") == "active" and profile.get("registration_status") == "active"
                and profile.get("approval_required") is False and profile.get("can_read") is True
                and profile.get("can_write") is True and type(profile.get("clearance")) is int
                and profile["clearance"] == 0 and profile.get("home_domain") == self._domain)

    def _submit(self, request, proposal, row):
        if row is not None and row["payload"]["state"] != "prepared":
            receipt = row["payload"]["receipt"]
            return result_data("unknown") if receipt is None else result_data("receipt", receipt=receipt)
        if row is None:
            self._check(request)
            row = self._put(proposal, 0, dict(schema=SCHEMA, binding=self._binding, proposal=proposal_data(proposal),
                                            state="prepared", receipt=None, dispatch_digest=None))
        self._profile(request)
        self._check(request)
        payload = dict(row["payload"], state="dispatching", dispatch_digest=hashlib.sha256(canonical(request)).hexdigest())
        row = self._put(proposal, row["revision"], payload)
        self._check(request)
        try:
            response = self._rest.request("POST", "/v1/memory/submit", request["auth"], submit_body(proposal))
            require(response.get("status") in STATUSES and type(response.get("tx_hash")) is str)
            receipt = receipt_data(asdict(PublicReceipt(proposal.operation_id, proposal.content_hash, proposal.chain_id,
                                   response.get("memory_id"), response["tx_hash"].lower(), response.get("committed_height"))), proposal)
            self._put(proposal, row["revision"], dict(payload, state="receipt", receipt=receipt))
            return result_data("receipt", receipt=receipt)
        except Exception:
            return result_data("unknown")

    def _poll(self, request, proposal, row):
        if row is None or row["payload"]["receipt"] is None:
            return result_data("unknown")
        receipt = row["payload"]["receipt"]
        require(request["record_id"] == receipt["memory_id"])
        self._profile(request)
        self._check(request)
        value = self._rest.request("GET", "/v1/memory/" + receipt["memory_id"], request["auth"])
        self._check(request)
        require(value.get("memory_id") == receipt["memory_id"] and value.get("submitting_agent") == proposal.author
                and value.get("domain_tag") == proposal.domain and value.get("memory_type") == "inference"
                and type(value.get("classification")) is int and value["classification"] == 0
                and value.get("content") == proposal.content and value.get("content_hash") == proposal.content_hash
                and value.get("status") in STATUSES)
        return result_data("observed", receipt=receipt, observed_status=value["status"])

    def handle(self, wire, *, author):
        """Caller must authenticate/pin the radio-peer to this ordinary public author.

        Unknown requires external exact reconciliation; this implementation never
        lists, guesses a record ID, re-signs, or resubmits an uncertain operation.
        Duplicate polls are safe reads, not durable consensus-status assertions.
        """
        try:
            require(author in self._authors and self._access() is True)
            request = open_request(wire, self._key, author=author, chain_id=self._chain, domain=self._domain, now=self._clock())
            self._check(request)
            proposal = PublicProposal(**request["proposal"])
            row = self._get(proposal)
            self._check(request)
            result = self._submit(request, proposal, row) if request["kind"] == "submit" else self._poll(request, proposal, row)
            self._check(request)
            return seal_result(request, result, self._key, now=self._clock())
        except PublicMeshError:
            raise
        except Exception:
            raise PublicMeshError() from None
