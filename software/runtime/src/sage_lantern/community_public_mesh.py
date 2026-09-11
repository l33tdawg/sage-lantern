"""Strict public-share IPC facade for unprivileged voice; no SDK client or USB.

The existing MeshVoiceFacade pins the root kernel peer, supervised broker/actor
and current device epoch. Root owns encrypted idempotency, signed private source
binding and actual transport admission. All callbacks remain request-bound.
Gateway observations cannot enter the direct-node acceptance path.
"""

from dataclasses import asdict
from datetime import datetime
import json
import re
import uuid

from .community_public import CommunityPublic, PublicProposal, PublicReceipt
from .mesh_voice_ipc import MeshVoiceFacade, MeshIPCError, require
from .public_mesh_protocol import proposal_data


REASONS = frozenset({"ready", "not_ready", "encrypted_storage_unavailable", "transport_unavailable",
                    "public_identity_unavailable", "acceptance_proof_unavailable"})
STATES = frozenset({"queued", "pending", "unknown"})


def _uuid(value):
    require(type(value) is str and len(value) == 36 and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0)
    return value


def _date(value):
    require(type(value) is str and 1 <= len(value) <= 40)
    require(datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None)


def _text(value, maximum):
    require(type(value) is str and 0 < len(value.encode("utf-8")) <= maximum and "\x00" not in value)


def _receipt(value, *, question=False):
    require(type(value) is dict and set(value) == ({"id", "state", "created_at", "question"} if question else
                                                {"id", "state", "created_at"}))
    require(type(value["id"]) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value["id"]) is not None)
    require(type(value["state"]) is str and value["state"] in STATES)
    _date(value["created_at"])
    if question:
        _text(value["question"], 4096)
    return value


class CommunityPublicMeshFacade:
    def __init__(self, transport):
        require(type(transport) is MeshVoiceFacade)
        self.transport = transport
        self._binding = None

    def readiness(self, *, access_check):
        result = self.transport._call("public_ready", {}, access_check)
        require(type(result) is dict and set(result) == {"available", "reason"}
                and type(result["available"]) is bool and type(result["reason"]) is str
                and result["reason"] in REASONS and result["available"] == (result["reason"] == "ready"))
        return result["reason"]

    def prepare(self, *, operation_id, question, answer, access_check):
        _uuid(operation_id)
        _text(question, 4096)
        _text(answer, 8192)
        value = self.transport._call("public_prepare", dict(operation_id=operation_id, question=question, answer=answer), access_check)
        require(type(value) is dict and set(value) == {"operation_id", "content", "content_hash", "chain_id", "domain", "author"})
        proposal = PublicProposal(**value)
        proposal_data(proposal)
        require(self.transport._actor is not None and proposal.author != self.transport._actor.agent_id)
        canonical = CommunityPublic(None, None, chain_id=proposal.chain_id, domain=proposal.domain,
                                    author=proposal.author).prepare(operation_id=operation_id, question=question, answer=answer)
        require(proposal == canonical)
        binding = (proposal.author, proposal.chain_id, proposal.domain)
        if self._binding is None:
            self._binding = binding
        require(self._binding == binding)
        return proposal

    def queue(self, proposal, *, source_id, created_at, request_id, approval_binding, access_check, on_submit):
        proposal_data(proposal)
        require(self._binding == (proposal.author, proposal.chain_id, proposal.domain))
        _uuid(source_id)
        _uuid(request_id)
        _date(created_at)
        require(type(approval_binding) is str and re.fullmatch(r"[0-9a-f]{64}", approval_binding) is not None)
        result = self.transport._call("public_queue", dict(proposal=asdict(proposal), source_id=source_id,
            created_at=created_at, request_id=request_id, approval_binding=approval_binding, consent=True),
            access_check, on_submit)
        return _receipt(result)

    def _entries(self, *, access_check):
        result = self.transport._call("public_list", {}, access_check)
        require(type(result) is dict and set(result) == {"items", "more"} and type(result["items"]) is list
                and len(result["items"]) <= 20 and type(result["more"]) is bool)
        seen = set()
        for value in result["items"]:
            require(type(value) is dict and set(value) == {"id", "state", "created_at", "question", "proposal"})
            _receipt({key: item for key, item in value.items() if key != "proposal"}, question=True)
            require(type(value["proposal"]) is dict)
            proposal = PublicProposal(**value["proposal"])
            proposal_data(proposal)
            require(json.loads(proposal.content)["question"] == value["question"])
            require(self.transport._actor is not None and proposal.author != self.transport._actor.agent_id)
            require(value["id"].startswith("mesh-public:"))
            _uuid(value["id"][len("mesh-public:"):])
            require(value["id"] not in seen)
            seen.add(value["id"])
        return result

    def list(self, *, access_check):
        result = self._entries(access_check=access_check)
        return dict(items=[{key: item for key, item in value.items() if key != "proposal"}
                           for value in result["items"]], more=result["more"])

    def resolve(self, identifier, *, access_check):
        for value in self._entries(access_check=access_check)["items"]:
            if value["id"] == identifier:
                return PublicProposal(**value["proposal"]), value["id"][len("mesh-public:"):], value["created_at"]
        raise MeshIPCError("public share unavailable")

    def inspect(self, proposal, *, request_id, access_check):
        proposal_data(proposal)
        _uuid(request_id)
        progress, result = self.transport.public_inspect(request_id, access_check=access_check)
        require(self.transport._actor is not None and proposal.author != self.transport._actor.agent_id)
        if result is not None and result["receipt"] is not None:
            receipt = PublicReceipt(**result["receipt"])
            require(receipt.operation_id == proposal.operation_id and receipt.content_hash == proposal.content_hash
                    and receipt.intended_chain_id == proposal.chain_id)
        return progress, result

    def refresh(self, proposal, *, submission_request_id, request_id, access_check, on_submit):
        """Explicit signed status GET; never substitute a new submission for uncertainty."""
        _uuid(request_id)
        require(request_id != submission_request_id and callable(on_submit))
        progress, result = self.inspect(proposal, request_id=submission_request_id, access_check=access_check)
        require(progress.state == "consumed" and result is not None
                and result["state"] in {"receipt", "observed"} and result["receipt"] is not None)
        receipt = PublicReceipt(**result["receipt"])
        return self.transport.public_begin(proposal, request_id=request_id, receipt=receipt,
            access_check=access_check, on_submit=on_submit)

    def sources(self, source_ids, *, access_check):
        require(type(source_ids) is list and len(source_ids) <= 20)
        for source in source_ids:
            _uuid(source)
        require(len(set(source_ids)) == len(source_ids))
        result = self.transport._call("public_sources", dict(source_ids=source_ids), access_check)
        require(type(result) is dict and set(result) == {"items"} and type(result["items"]) is list
                and len(result["items"]) == len(source_ids))
        seen = set()
        for item in result["items"]:
            require(type(item) is dict and set(item) == {"source_id", "state"})
            source = _uuid(item["source_id"])
            require(source in source_ids and source not in seen and type(item["state"]) is str
                    and item["state"] in STATES | {"not_shared"})
            seen.add(source)
        return result
