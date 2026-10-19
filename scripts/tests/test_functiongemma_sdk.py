"""Real SDK/signatures/HTTP/fsync; fake node policy and reviewed-submit authority.

This loopback fixture is NOT a SAGE consensus node or human/device acceptance.
"""
from dataclasses import replace
import hashlib
import importlib
import json
import multiprocessing
from pathlib import Path
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from nacl.signing import VerifyKey
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lantern/benchmarks/tool_routing"))
from action_journal import DurableActionJournal
from action_authority import PRIVATE_ACTIONS, ReviewedActionAuthority
from action_session import (ActionRefused, AuthenticatedScope, QualifiedActionBroker,
                            QualifiedPolicy, SubmittedInstruction, VerifiedReceipt, boundary_digest, fingerprint)
from action_sdk import CANONICAL, SCHEMA, OnlinePrivateBinding, PrivateSageSDKBackend
from action_transport import SupervisedLoopbackTransport
import action_transport


SAVE = "Save a private note titled 'Keys' with exact text 'Use shelf 4.'"
SEARCH = "Find my saved note about the keys."


@pytest.fixture
def sdk(monkeypatch):
    source = ROOT.parent / "sage/sdk/python/src"
    monkeypatch.syspath_prepend(str(source))
    saved = {k: v for k, v in sys.modules.items() if k == "sage_sdk" or k.startswith("sage_sdk.")}
    for name in saved:
        del sys.modules[name]
    module = importlib.import_module("sage_sdk")
    assert Path(module.__file__).resolve().is_relative_to(source.resolve())
    yield module
    for name in list(sys.modules):
        if name == "sage_sdk" or name.startswith("sage_sdk."):
            del sys.modules[name]
    sys.modules.update(saved)


class FakeAuthority:
    def __init__(self, scope):
        self.scope, self.texts, self.active = scope, {}, threading.Event()
        self.gate = threading.RLock()
        self.active.set()

    def resolve(self, instruction):
        return self.scope

    def check(self, scope, instruction):
        return self.active.is_set() and self.scope == scope and self.texts.get(instruction.request_id) == instruction.text

    def run_guarded(self, scope, instruction, callback):
        if not self.check(scope, instruction):
            raise ActionRefused("fake_authority_revoked")
        return callback()

    def dispatch_step(self, scope, instruction, callback):
        with self.gate:
            if not self.check(scope, instruction):
                raise ActionRefused("fake_authority_revoked")
            return callback()


@pytest.fixture
def rig(sdk, tmp_path):
    identity = sdk.AgentIdentity.from_seed(bytes(range(32)))
    state = {"requests": [], "notes": {}, "mode": "ok", "accepted": threading.Event(),
             "release": threading.Event(), "binding_change": False, "verified_signatures": 0}

    def record(memory_id, content):
        return dict(memory_id=memory_id, submitting_agent=identity.agent_id, content=content,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(), memory_type="observation",
                    domain_tag="owner-test", confidence_score=0.8, classification=3,
                    status="committed", created_at="2026-10-02T00:00:00Z")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            assert self.headers["X-Agent-ID"] == identity.agent_id
            nonce = bytes.fromhex(self.headers["X-Nonce"])
            canonical = self.command.encode() + b" " + self.path.encode() + b"\n" + body
            signed = hashlib.sha256(canonical).digest() + struct.pack(">q", int(self.headers["X-Timestamp"])) + nonce
            VerifyKey(bytes.fromhex(identity.agent_id)).verify(signed, bytes.fromhex(self.headers["X-Signature"]))
            state["verified_signatures"] += 1
            state["requests"].append((self.command, self.path, json.loads(body) if body else None))
            path, mode = self.path, state["mode"]
            if path == "/v1/agent/me":
                value = dict(agent_id=identity.agent_id, poe_weight=1, vote_count=0, role="member",
                             home_domain="owner-test", enrollment_status="active", registration_status="active",
                             approval_required=False, clearance=3, can_read=True, can_write=mode != "no_write")
                if mode == "coerced_boolean":
                    value["can_write"] = "true"
            elif path == "/v1/agent/me/domains":
                value = dict(domains=["owner-test"], owned_domains=["owner-test"],
                             readable_domains=["owner-test"], writable_domains=[] if mode == "no_grant" else ["owner-test"],
                             truncated=False, scope="caller")
            elif path == "/v1/embed":
                value = {"embedding": [1.0, 0.25]}
            elif path == "/v1/memory/submit":
                content = json.loads(body)["content"]
                state["notes"]["note-1"] = record("note-1", content)
                state["accepted"].set()
                if mode == "blocked_write":
                    state["release"].wait(2)
                value = dict(memory_id="note-1", tx_hash="fake-test-tx", status="committed",
                             committed=True, committed_height=12, projection_confirmed=True, retryable=False)
            elif path == "/v1/memory/hybrid":
                rows = list(state["notes"].values())
                if mode == "duplicate":
                    rows += rows
                value = {"results": rows, "total_count": len(rows)}
            elif path == "/v1/memory/note-1":
                value = dict(state["notes"]["note-1"])
                changes = {"wrong_owner": {"submitting_agent": "b" * 64},
                           "wrong_domain": {"domain_tag": "other-owner"},
                           "public": {"classification": 0}, "pending": {"status": "proposed"},
                           "hash": {"content_hash": "a" * 64}, "wrong_id": {"memory_id": "note-2"}}
                value.update(changes.get(mode, {}))
                if mode == "policy_change":
                    state["binding_change"] = True
                if mode == "changed_bytes":
                    content = json.loads(value["content"])
                    content["text"] = "Changed by fake server"
                    value["content"] = json.dumps(content)
                    value["content_hash"] = hashlib.sha256(value["content"].encode()).hexdigest()
                if mode == "coerced_classification":
                    value["classification"] = "3"
                if mode == "duplicate_note_field":
                    note = json.loads(value["content"])
                    value["content"] = value["content"][:-1] + ',"text":' + json.dumps(note["text"]) + '}'
                    value["content_hash"] = hashlib.sha256(value["content"].encode()).hexdigest()
            else:
                self.send_error(404)
                return
            raw = json.dumps(value, separators=(",", ":")).encode()
            if mode == "duplicate_json":
                raw = b'{"embedding":[1.0],"embedding":[2.0]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if mode == "blocked_body" and path == "/v1/memory/submit":
                state["release"].wait(2)
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint = "http://127.0.0.1:" + str(server.server_port)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    transport = SupervisedLoopbackTransport(endpoint)
    client = sdk.SageClient(endpoint, identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, transport=transport, trust_env=False, follow_redirects=False)

    def binding(client, transport):
        return OnlinePrivateBinding(client, transport, endpoint, "fake-chain", identity.agent_id,
            "owner-test", "fake-profile", "fake-app-v25", ("f" if state["binding_change"] else "e") * 64, CANONICAL)

    backend = PrivateSageSDKBackend(client, transport, binding_verifier=binding, classification=3, enabled=True)
    scope = AuthenticatedScope("fake-owner", "fake-session", "fake-epoch", "owner-test", identity.agent_id,
        "fake-chain", "fake-profile", "fake-device", "fake-consent", time.monotonic() + 30)
    authority = FakeAuthority(scope)
    directory = tmp_path.resolve() / "journal"
    directory.mkdir(mode=0o700)
    journal = DurableActionJournal(directory)

    class Qualification:
        def current(self):
            return QualifiedPolicy(boundary_digest(), backend.profile_sha256(), "a" * 64, "exact_grammar", True)

    broker = QualifiedActionBroker(authority=authority, backend=backend, journal=journal, verifier=backend,
        qualification=Qualification(), backend_profile_sha256=backend.profile_sha256(), enabled=True)

    def bind(text=SAVE, request_id="request-1", timeout=3):
        instruction = SubmittedInstruction(request_id, text)
        authority.texts[request_id] = text
        return broker.bind(instruction, deadline=time.monotonic() + timeout)

    yield dict(state=state, backend=backend, broker=broker, authority=authority, bind=bind,
               journal=journal, directory=directory, client=client, transport=transport)
    state["release"].set()
    client._client.close()
    journal.close()
    server.shutdown()
    server.server_close()
    thread.join(1)


def test_real_sdk_signed_save_then_exact_canonical_search(rig):
    result = rig["broker"].execute(rig["bind"]())
    assert result["receipt"].state == "committed"
    assert result["completion_text"] == "Your private note was saved."
    result = rig["broker"].execute(rig["bind"](SEARCH, "read-1"))
    assert result["receipt"].state == "read_complete"
    assert result["result"]["results"][0]["result"]["records"][0]["note"]["text"] == "Use shelf 4."
    s = rig["state"]
    assert len(s["requests"]) == s["verified_signatures"]
    proposal = next(body for _, path, body in s["requests"] if path == "/v1/memory/submit")
    assert proposal["classification"] == 3 and proposal["domain_tag"] == "owner-test"
    assert "idempotency_key" not in proposal  # observation is not a task
    query = next(body for _, path, body in s["requests"] if path == "/v1/memory/hybrid")
    assert query["status_filter"] == "committed" and query["top_k"] == 5
    assert len(list(rig["directory"].glob("*.reserved"))) == 2
    assert len(list(rig["directory"].glob("*.receipt"))) == 2
    assert all(b"Use shelf" not in p.read_bytes() for p in rig["directory"].iterdir())


@pytest.mark.parametrize("mode", ["wrong_owner", "wrong_domain", "public", "pending", "hash", "wrong_id",
                                  "changed_bytes", "no_write", "no_grant", "policy_change", "duplicate_json",
                                  "coerced_boolean", "coerced_classification", "duplicate_note_field"])
def test_forged_pending_denied_or_changed_responses_never_complete(rig, mode):
    handle = rig["bind"]()
    rig["state"]["mode"] = mode
    with pytest.raises(ActionRefused):
        rig["broker"].execute(handle)
    assert list(rig["directory"].glob("*.receipt")) == []
    assert len(list(rig["directory"].glob("*.reserved"))) == 1
    if mode in {"no_write", "no_grant", "duplicate_json", "coerced_boolean"}:
        assert not any(path == "/v1/memory/submit" for _, path, _ in rig["state"]["requests"])


def test_disabled_sdk_and_whole_mixed_plan_refuse_before_network(rig):
    rig["backend"].enabled = False
    with pytest.raises(ActionRefused, match="private_sdk_disabled"):
        rig["broker"].execute(rig["bind"]())
    assert not rig["state"]["requests"]
    rig["backend"].enabled = True
    with pytest.raises(ActionRefused, match="unsupported_sdk_plan"):
        rig["broker"].execute(rig["bind"]("Find my saved keys note and check this Lantern's battery level.", "parallel-1"))
    assert not rig["state"]["requests"]


@pytest.mark.parametrize("cause", ["timeout", "cancel", "epoch"])
@pytest.mark.parametrize("mode", ["blocked_write", "blocked_body"])
def test_interrupted_write_keeps_durable_reservation_and_cannot_retry_after_restart(rig, cause, mode):
    rig["state"]["mode"] = mode
    handle = rig["bind"](timeout=0.4 if cause == "timeout" else 3)
    errors = []

    def execute():
        try:
            rig["broker"].execute(handle)
        except ActionRefused as error:
            errors.append(str(error))

    worker = threading.Thread(target=execute)
    worker.start()
    assert rig["state"]["accepted"].wait(1)
    started = time.monotonic()
    if cause == "cancel":
        rig["broker"].cancel("request-1")
    elif cause == "epoch":
        rig["authority"].active.clear()
    worker.join(1)
    assert not worker.is_alive() and errors
    assert time.monotonic() - started < 1
    assert len(list(rig["directory"].glob("*.reserved"))) == 1
    assert not list(rig["directory"].glob("*.receipt"))
    assert not any(t.name == "disabled-action-transport" and t.is_alive() for t in threading.enumerate())
    rig["journal"].close()
    restarted = DurableActionJournal(rig["directory"])
    try:
        marker = next(rig["directory"].glob("*.reserved"))
        assert restarted.reserve(marker.stem, marker.read_text().strip()) is False
    finally:
        restarted.close()
    assert sum(path == "/v1/memory/submit" for _, path, _ in rig["state"]["requests"]) == 1


def test_fabricated_raw_result_has_no_internal_canonical_evidence(rig):
    with pytest.raises(ActionRefused, match="sdk_canonical_evidence_unavailable"):
        rig["backend"].verify({"ok": True}, scope=rig["authority"].scope,
            calls=[{"name": "save_private_note", "arguments": {"title": "Keys", "text": "x"}}],
            operation_id="b" * 64, request_sha256="a" * 64)
    assert not rig["state"]["requests"]


def test_sdk_candidate_defaults_disabled_and_endpoint_change_refuses(rig):
    backend = PrivateSageSDKBackend(rig["client"], rig["transport"],
        binding_verifier=rig["backend"].binding_verifier, classification=3)
    assert backend.enabled is False
    with pytest.raises(ActionRefused, match="private_sdk_disabled"):
        backend.invoke([{"name": "search_memory", "arguments": {"query": "keys"}}],
            scope=rig["authority"].scope, operation_id="b" * 64, deadline=time.monotonic() + 1,
            access_check=lambda: True)
    handle = rig["bind"]()
    rig["client"]._base_url = "http://127.0.0.1:1"
    with pytest.raises(ActionRefused, match="private_node_trust_unavailable"):
        rig["broker"].execute(handle)
    assert not rig["state"]["requests"]


@pytest.mark.parametrize("endpoint", ["https://example.com", "http://localhost:9000", "http://127.0.0.1:9000/path"])
def test_transport_refuses_unpinned_hosts(endpoint):
    with pytest.raises(ActionRefused):
        SupervisedLoopbackTransport(endpoint)


def test_transport_refuses_calls_outside_guard_and_nonfinite_deadline(rig):
    with pytest.raises(ActionRefused, match="transport_outside_guard"):
        rig["client"].get_profile()
    for deadline in (float("nan"), float("inf"), time.monotonic() - 1):
        with pytest.raises(ActionRefused, match="bounded_transport_deadline_required"):
            rig["transport"].run(lambda: None, deadline=deadline, access_check=lambda: True)
    assert not rig["state"]["requests"]


def test_duplicate_or_changed_recall_is_refused(rig):
    rig["broker"].execute(rig["bind"]())
    rig["state"]["mode"] = "duplicate"
    with pytest.raises(ActionRefused, match="duplicate_recall_record"):
        rig["broker"].execute(rig["bind"](SEARCH, "read-duplicate"))


@pytest.mark.parametrize("mode", ["wrong_owner", "wrong_domain", "public", "pending", "hash", "wrong_id", "changed_bytes"])
def test_search_requires_current_exact_canonical_detail(rig, mode):
    rig["broker"].execute(rig["bind"]())
    rig["state"]["mode"] = mode
    with pytest.raises(ActionRefused):
        rig["broker"].execute(rig["bind"](SEARCH, "read-bad-detail"))
    assert len(list(rig["directory"].glob("*.receipt"))) == 1


def test_literal_unicode_note_bytes_are_preserved(rig):
    text = "Save a private note titled 'Café' with exact text 'Route B: 4 → 6.'"
    result = rig["broker"].execute(rig["bind"](text))
    note = result["result"]["results"][0]["result"]["note"]
    assert note["title"] == "Café" and note["text"] == "Route B: 4 → 6."


def lifecycle_bind(rig, text=SAVE, timeout=3):
    if not isinstance(rig["broker"].authority, ReviewedActionAuthority):
        authority = ReviewedActionAuthority(enabled=True)
        authority.activate(rig["authority"].scope, allowed_actions=PRIVATE_ACTIONS)
        rig["broker"].authority = authority
    authority = rig["broker"].authority
    review = authority.prepare_review(text, source="typed")
    instruction = authority.submit_review(review, reviewed_text=text,
                                          approve_private_write=text == SAVE)
    return instruction, rig["broker"].bind(instruction, deadline=time.monotonic() + timeout)


def test_sdk_with_lifecycle_review_and_receipt_fenced_local_publication(rig):
    published = []
    instruction, handle = lifecycle_bind(rig)
    result = rig["broker"].execute(handle, publish=published.append)
    assert published == [result] and result["receipt"].state == "committed"
    assert not rig["broker"].authority.check(rig["authority"].scope, instruction)
    _, handle = lifecycle_bind(rig, SEARCH)
    assert rig["broker"].execute(handle)["receipt"].state == "read_complete"


@pytest.mark.parametrize("cause", ["lock", "sleep", "owner", "consent", "cancel"])
def test_lifecycle_revocation_interrupts_real_sdk_http_and_suppresses_publication(rig, cause):
    rig["state"]["mode"] = "blocked_body"
    instruction, handle = lifecycle_bind(rig)
    errors, published = [], []

    def execute():
        try:
            rig["broker"].execute(handle, publish=published.append)
        except ActionRefused as error:
            errors.append(str(error))

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert rig["state"]["accepted"].wait(1)
        if cause == "cancel":
            rig["broker"].cancel(instruction.request_id)
        else:
            # These labels represent the future trusted hook's common revoke
            # path. They do not exercise actual hardware/session events.
            rig["broker"].authority.revoke()
        worker.join(1)
        assert not worker.is_alive() and errors and not published
        if cause != "cancel":
            assert rig["broker"].authority.drain(0.1)
        assert len(list(rig["directory"].glob("*.reserved"))) == 1
        assert not list(rig["directory"].glob("*.receipt"))
    finally:
        rig["state"]["release"].set()
        worker.join(2)


def test_revocation_after_canonical_verification_fences_publication(rig, monkeypatch):
    _, handle = lifecycle_bind(rig)
    original = rig["journal"].complete

    def complete(key, receipt):
        original(key, receipt)
        rig["broker"].authority.revoke()

    monkeypatch.setattr(rig["journal"], "complete", complete)
    published = []
    with pytest.raises(ActionRefused):
        rig["broker"].execute(handle, publish=published.append)
    assert published == []
    assert len(list(rig["directory"].glob("*.receipt"))) == 1


def test_transport_refuses_check_then_dispatch_callback(rig):
    with pytest.raises(ActionRefused, match="serialized_dispatch_authority_required"):
        rig["transport"].run(lambda: rig["client"].get_profile(),
                             deadline=time.monotonic() + 1, access_check=lambda: True)
    assert rig["state"]["requests"] == []


def test_revocation_wins_before_first_sdk_byte_is_sent(rig, monkeypatch):
    _, handle = lifecycle_bind(rig)
    authority = rig["broker"].authority
    original = authority.dispatch_step
    arrived, release = threading.Event(), threading.Event()
    errors = []

    def delayed_step(scope, instruction, callback):
        arrived.set()
        assert release.wait(2)
        return original(scope, instruction, callback)

    monkeypatch.setattr(authority, "dispatch_step", delayed_step)

    def execute():
        try:
            rig["broker"].execute(handle)
        except ActionRefused as error:
            errors.append(str(error))

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert arrived.wait(1)
        authority.revoke()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and errors
    assert rig["state"]["requests"] == []
    assert authority.drain(0.1)


def test_socket_backpressure_wait_does_not_hold_revocation_gate(rig, monkeypatch):
    _, handle = lifecycle_bind(rig)
    original_send, original_select = socket.socket.send, action_transport.select.select
    arrived, release = threading.Event(), threading.Event()
    errors, blocked = [], []

    def send(sock, data, *args, **kwargs):
        if not blocked and bytes(data).startswith(b"GET /v1/"):
            blocked.append(True)
            raise BlockingIOError()
        return original_send(sock, data, *args, **kwargs)

    def wait_readiness(*args, **kwargs):
        arrived.set()
        assert release.wait(2)
        return original_select(*args, **kwargs)

    monkeypatch.setattr(socket.socket, "send", send)
    monkeypatch.setattr(action_transport.select, "select", wait_readiness)

    def execute():
        try:
            rig["broker"].execute(handle)
        except ActionRefused as error:
            errors.append(str(error))

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert arrived.wait(1)
        rig["broker"].authority.revoke()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and errors and blocked
    assert rig["state"]["requests"] == []
    assert rig["broker"].authority.drain(0.1)


def _reserve_process(directory, queue):
    journal = DurableActionJournal(directory)
    try:
        queue.put(journal.reserve("b" * 64, "c" * 64))
    finally:
        journal.close()


def test_durable_reservation_is_atomic_between_processes(tmp_path):
    directory = tmp_path.resolve() / "private-journal"
    directory.mkdir(mode=0o700)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [context.Process(target=_reserve_process, args=(str(directory), queue)) for _ in range(2)]
    for worker in workers:
        worker.start()
    outcomes = [queue.get(timeout=10) for _ in workers]
    for worker in workers:
        worker.join(5)
        assert worker.exitcode == 0
    assert sorted(outcomes) == [False, True]


def test_journal_refuses_symlinks_permissions_and_truncated_reservations(tmp_path):
    directory = tmp_path.resolve() / "private-journal"
    directory.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ActionRefused):
        DurableActionJournal(alias)
    directory.chmod(0o755)
    with pytest.raises(ActionRefused):
        DurableActionJournal(directory)
    directory.chmod(0o700)
    journal = DurableActionJournal(directory)
    try:
        assert journal.reserve("b" * 64, "c" * 64)
        (directory / ("b" * 64 + ".reserved")).write_bytes(b"partial")
        with pytest.raises(ActionRefused):
            journal.reserve("b" * 64, "c" * 64)
    finally:
        journal.close()


def test_journal_binds_completion_to_original_reservation(tmp_path):
    directory = tmp_path.resolve() / "private-journal"
    directory.mkdir(mode=0o700)
    journal = DurableActionJournal(directory)
    receipt = VerifiedReceipt("b" * 64, "c" * 64, "d" * 64, "e" * 64, "f" * 64,
        "committed", "fake-test-evidence", "pinned_online_canonical_observation")
    original = fingerprint({"operation_id": receipt.operation_id, "scope": receipt.scope_sha256,
                            "calls": receipt.calls_sha256, "request": receipt.request_sha256})
    try:
        assert journal.reserve("a" * 64, original)
        with pytest.raises(ActionRefused, match="journal_completion_uncertain"):
            journal.complete("a" * 64, replace(receipt, scope_sha256="1" * 64))
        assert not list(directory.glob("*.receipt"))
        journal.complete("a" * 64, receipt)
        journal.complete("a" * 64, receipt)
        assert len(list(directory.glob("*.receipt"))) == 1
    finally:
        journal.close()
