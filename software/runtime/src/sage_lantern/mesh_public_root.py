"""Root-only public mesh composition; no contact evidence is invented here."""

from dataclasses import asdict, fields
from datetime import datetime
import hashlib
import json
import re
import uuid

from .community_public import PublicProposal, PublicReceipt
from .message_contacts import RecipientID
from .public_mesh_protocol import proposal_data, result_data
from .public_mesh_provider import PublicMeshPins, PublicMeshProvider, PublicMeshProgress, PublicMeshTiming
from .mesh_fragments import MAX_FRAGMENTS
from .public_workflow_store import PublicWorkflowStore
from .community_public import CommunityPublic
from .gateway_contact import GatewayContactProbe
from .workflow_store import WorkflowNotFound


ROOT_SCHEMA = "sage.lantern.public-mesh-intent.v1"
ROOT_DOMAIN = b"SAGE-LANTERN-PUBLIC-MESH-INTENT-v1\x00"
ROOT_NAMESPACE = uuid.UUID("72f5560a-289b-4ea5-93ea-4b52885db4ca")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def require(condition):
    if not condition:
        raise ValueError("public_mesh_unavailable")


def validate_settings(value, private):
    require(type(value) is dict and set(value) == {"identity_file", "author", "chain_id", "domain",
        "gateway_key", "radio_key", "storage_actor", "storage_chain", "timing"})
    require(value["storage_actor"] == private["agent_id"] and value["storage_chain"] == private["chain_id"])
    require(type(value["identity_file"]) is str and re.fullmatch(
        r"/var/lib/sage-lantern/identity/[A-Za-z0-9_.-]+", value["identity_file"]) is not None)
    require(value["identity_file"] != private["identity_file"])
    RecipientID(value["chain_id"], value["author"])
    require(value["chain_id"] != private["chain_id"])
    require(value["author"] != private["agent_id"] and value["author"] != "00" * 32)
    require(type(value["domain"]) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value["domain"]) is not None)
    for key in ("gateway_key", "radio_key"):
        require(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{64}", value[key]) is not None
                and value[key] != "00" * 32)
    require(value["gateway_key"] not in (value["author"], private["agent_id"]))
    require(value["radio_key"] != private["radio"]["public_key"])
    require(type(value["timing"]) is dict and set(value["timing"]) == {"frame_seconds", "gateway_seconds", "safety_seconds"})
    PublicMeshTiming(**value["timing"]).validate()


def progress(value):
    require(type(value) is dict and set(value) == {"request_id", "state", "next_fragment", "fragment_count"})
    from .public_mesh_protocol import identifier
    identifier(value["request_id"])
    require(value["state"] in {"queued", "dispatching", "awaiting_response", "consumed", "unknown"})
    require(type(value["next_fragment"]) is int and type(value["fragment_count"]) is int
            and 0 <= value["next_fragment"] <= value["fragment_count"] <= MAX_FRAGMENTS and value["fragment_count"] > 0)
    return PublicMeshProgress(**value)


def observation(value):
    if value is None:
        return None
    require(type(value) is dict)
    expected = result_data(value.get("state"), receipt=value.get("receipt"), observed_status=value.get("observed_status"))
    require(set(value) == set(expected) and value == expected)
    for key in ("acceptance_available", "consensus_verified", "cache_eligible"):
        require(value[key] is False)
    if value["receipt"] is not None:
        from .public_mesh_protocol import identifier
        receipt = value["receipt"]
        require(type(receipt) is dict and set(receipt) == {field.name for field in fields(PublicReceipt)})
        identifier(receipt["operation_id"])
        identifier(receipt["memory_id"])
        for key in ("content_hash", "tx_hash"):
            require(type(receipt[key]) is str and re.fullmatch(r"[0-9a-f]{64}", receipt[key]) is not None)
        require(type(receipt["intended_chain_id"]) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", receipt["intended_chain_id"]) is not None)
        require(receipt["committed_height"] is None or type(receipt["committed_height"]) is int
                and 1 <= receipt["committed_height"] <= 2 ** 53 - 1)
    return value


class RootPublicMesh:
    def __init__(self, runtime, authority, settings, public_identity, private_identity, *, active, contact_check=None):
        require(settings["chain_id"] != settings["storage_chain"])
        require(public_identity.agent_id == settings["author"]
                and private_identity.agent_id == settings["storage_actor"]
                and public_identity.agent_id != private_identity.agent_id)
        require(runtime.actor == RecipientID(settings["storage_chain"], settings["storage_actor"]))
        self.runtime, self.authority = runtime, authority
        self.settings, self.active = dict(settings), active
        self.identity = private_identity
        self.pins = PublicMeshPins(settings["gateway_key"], bytes.fromhex(settings["radio_key"]),
                                   settings["chain_id"], settings["domain"])
        self.journal = PublicWorkflowStore(runtime.store, chain_id=settings["chain_id"], domain=settings["domain"],
            author=settings["author"], identity=private_identity, access_check=runtime._access, drain_check=runtime._access)
        self.contact_check = contact_check
        self.probe = GatewayContactProbe(runtime.store, public_identity, self.pins, active=active,
                                        timing=PublicMeshTiming(**settings["timing"]))
        provider = PublicMeshProvider(runtime.store, public_identity, self.pins,
            approval_check=self.approved, contact_check=self.contact, timing=PublicMeshTiming(**settings["timing"]),
            contact_admission=self.probe)
        provider.contact_probe = self.probe
        self.provider = provider
        runtime.attach_public_mesh(provider)

    def contact(self, pins):
        return (self.active() is True and pins == self.pins
                and (self.contact_check(pins) is True if callable(self.contact_check) else self.probe.current(pins))
                and self.active() is True)

    def _id(self, operation_id):
        return str(uuid.uuid5(ROOT_NAMESPACE, encoded([self.journal._binding, operation_id]).decode()))

    def _verified(self, row):
        proof = row["payload"]
        require(type(proof) is dict and set(proof) == {"body", "signature"})
        body = proof["body"]
        require(type(proof["signature"]) is str and re.fullmatch(r"[0-9a-f]{128}", proof["signature"]) is not None)
        self.identity._signing_key.verify_key.verify(ROOT_DOMAIN + encoded(body), bytes.fromhex(proof["signature"]))
        require(type(body) is dict and set(body) == {"schema", "binding", "proposal", "source_id", "created_at", "request_id", "approval_binding"}
                and body["schema"] == ROOT_SCHEMA and body["binding"] == self.journal._binding
                and row["kind"] == "public_proposal" and row["revision"] == 1)
        proposal = PublicProposal(**body["proposal"])
        proposal_data(proposal)
        require((proposal.author, proposal.chain_id, proposal.domain) ==
                (self.settings["author"], self.pins.chain_id, self.pins.domain))
        require(row["record_id"] == self._id(proposal.operation_id))
        from .public_mesh_protocol import identifier
        identifier(body["source_id"])
        identifier(body["request_id"])
        require(type(body["created_at"]) is str and len(body["created_at"]) <= 40
                and datetime.fromisoformat(body["created_at"].replace("Z", "+00:00")).tzinfo is not None)
        require(type(body["approval_binding"]) is str and re.fullmatch(r"[0-9a-f]{64}", body["approval_binding"]) is not None)
        return body

    def _intent(self, operation_id):
        return self._verified(self.runtime.store.get(self._id(operation_id), kind="public_proposal", access_check=self.runtime._access))

    def approved(self, proposal):
        if self.active() is not True:
            return False
        try:
            expected = proposal_data(proposal)
            payload = self.journal._get("sdk", proposal.operation_id)["payload"]
            self.journal._verify_source(payload)
            require(payload["proposal"] == expected and payload["result"] != {"cancelled": True})
            return self.active() is True
        except Exception:
            return False

    def _entries(self):
        entries = []
        for row in self.runtime.store.scan(limit=50, max_records=4096, access_check=self.runtime._access):
            body = row["payload"].get("body", {})
            if body.get("schema") == ROOT_SCHEMA:
                entries.append(self._verified(row))
        return sorted(entries, key=lambda item: (datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")), item["request_id"]), reverse=True)

    def _known_receipt(self, proposal, receipt):
        expected = self.journal._receipt(receipt, proposal)
        payload = self.journal._get("sdk", proposal.operation_id)["payload"]
        self.journal._verify_source(payload)
        require(payload["proposal"] == proposal_data(proposal) and payload["result"] != {"cancelled": True})
        if payload["result"] == expected:
            return True
        from .public_mesh_provider import SCHEMA
        for row in self.runtime.store.scan(limit=50, max_records=4096, access_check=self.runtime._access):
            body = row["payload"].get("body", {})
            if body.get("schema") != SCHEMA or body.get("kind") != "request":
                continue
            verified = self.provider._get(row["record_id"], "request", self.runtime._access)
            self.provider._request(verified)
            data = verified["data"]
            if data["proposal"] == proposal_data(proposal) and data["result"] is not None and data["result"]["receipt"] == expected:
                return True
        return False

    def _summary(self, body, *, question=False):
        state = "unknown"
        try:
            saved = self.provider._get(body["request_id"], "request", self.runtime._access)
            if saved is None:
                result = dict(id="mesh-public:" + body["request_id"], state="unknown", created_at=body["created_at"])
                if question:
                    result["question"] = json.loads(body["proposal"]["content"])["question"]
                    result["proposal"] = body["proposal"]
                return result
            self.provider._request(saved)
            require(saved["data"]["proposal"] == body["proposal"])
            value, result = self.provider.inspect(body["request_id"], access_check=self.runtime._access)
            if value.state == "queued" and value.next_fragment == 0:
                state = "queued"
            elif value.state not in {"unknown", "dispatching"}:
                state = "unknown" if result is not None and result["state"] in {"unknown", "unavailable"} else "pending"
        except WorkflowNotFound:
            pass
        result = dict(id="mesh-public:" + body["request_id"], state=state, created_at=body["created_at"])
        if question:
            result["question"] = json.loads(body["proposal"]["content"])["question"]
            result["proposal"] = body["proposal"]
        return result

    def _facade(self, method, arguments, device):
        from .public_mesh_protocol import identifier
        if method == "public_ready":
            if not self.contact(self.pins):
                self.probe.begin(access_check=self.runtime._access)
            available = self.contact(self.pins)
            return dict(available=available, reason="ready" if available else "transport_unavailable")
        if method == "public_prepare":
            prepared = CommunityPublic(None, None, chain_id=self.pins.chain_id, domain=self.pins.domain,
                author=self.settings["author"]).prepare(**arguments)
            self.provider.preflight(prepared)
            return asdict(prepared)
        if method == "public_queue":
            require(arguments["consent"] is True)
            proposal = PublicProposal(**arguments["proposal"])
            self.provider.preflight(proposal)
            require(self.contact(self.pins))
            body = dict(schema=ROOT_SCHEMA, binding=self.journal._binding, proposal=proposal_data(proposal),
                source_id=identifier(arguments["source_id"]), created_at=arguments["created_at"],
                request_id=identifier(arguments["request_id"]), approval_binding=arguments["approval_binding"])
            proof = dict(body=body, signature=self.identity._signing_key.sign(ROOT_DOMAIN + encoded(body)).signature.hex())
            candidate = dict(payload=proof, record_id=self._id(proposal.operation_id), kind="public_proposal", revision=1)
            self._verified(candidate)
            try:
                existing = self._intent(proposal.operation_id)
            except WorkflowNotFound:
                existing = None
            if existing is not None:
                require(existing == body)
                return self._summary(existing)
            try:
                previous = self.journal.reconcile_public(proposal.operation_id)
                require(previous["receipt"] is None and previous["cancelled"] is False)
            except WorkflowNotFound:
                pass
            self.runtime.store.put(candidate["record_id"], "public_proposal", 0, proof, access_check=self.runtime._access)
            self.journal.prepare_public_source(proposal, body["created_at"], body["source_id"])
            self.provider.begin(proposal, request_id=body["request_id"], device=device, access_check=self.runtime._access)
            return self._summary(body)
        entries = self._entries()
        if method == "public_list":
            items = []
            for body in entries[:20]:
                item = self._summary(body, question=True)
                if len(encoded(dict(items=[*items, item], more=True))) > 48000:
                    require(bool(items))
                    break
                items.append(item)
            return dict(items=items, more=len(entries) > len(items))
        require(method == "public_sources")
        sources = arguments["source_ids"]
        require(type(sources) is list and len(sources) <= 20 and len(set(sources)) == len(sources))
        result = []
        for source in sources:
            identifier(source)
            selected = next((body for body in entries if body["source_id"] == source), None)
            result.append(dict(source_id=source, state="not_shared" if selected is None else self._summary(selected)["state"]))
        return dict(items=result)

    def invoke(self, method, arguments, access):
        require(self.active() is True and access() is True)
        with self.authority.grant(self.runtime.actor, access, {method}) as context:
            if method in {"public_ready", "public_prepare", "public_queue", "public_list", "public_sources"}:
                response = self.runtime._run(context, method, lambda device: self._facade(method, arguments, device))
            elif method == "public_begin":
                require(set(arguments) == {"proposal", "request_id", "receipt"})
                proposal = PublicProposal(**arguments["proposal"])
                proposal_data(proposal)
                receipt = None if arguments["receipt"] is None else PublicReceipt(**arguments["receipt"])
                if receipt is not None:
                    require(self.runtime._run(context, method, lambda device: self._known_receipt(proposal, receipt)) is True)
                result = self.runtime.public_begin(context, proposal, request_id=arguments["request_id"], receipt=receipt)
                response = asdict(result)
                progress(response)
            else:
                require(method == "public_inspect" and set(arguments) == {"request_id"})
                state, result = self.runtime.public_inspect(context, arguments["request_id"])
                response = {"progress": asdict(state), "result": observation(result)}
                progress(response["progress"])
            require(self.active() is True and access() is True)
            return response
