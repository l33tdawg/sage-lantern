import json
from types import SimpleNamespace

import pytest

from sage_lantern.action_guard import (
    ActionNotAdmitted,
    ActionDispatcher,
    SageRememberExecutor,
    canonical_save,
    guard_save,
)


ROUTING = {
    "owned_home": "self",
    "permitted_public_domains": ["community:tool-shed"],
    "private_grants": [],
}


def request(**changes):
    value = {
        "action": "remember",
        "content": "The spare gasket ring is in the lower drawer.",
        "memory_type": "fact",
    }
    value.update(changes)
    return json.dumps(value)


def saved(receipt=None):
    calls = []

    def execute(call):
        calls.append(call)
        return receipt if receipt is not None else {
            "status": "committed", "memory_id": "memory-1", "request_id": "write-1"}

    return calls, execute


def test_complete_private_save_is_canonical_without_a_model_call():
    result = canonical_save(request(tags=["drawer"], confidence=0.91), json.dumps(ROUTING))
    assert result.kind == "tool_call"
    assert result.tool_call == {
        "name": "sage_remember",
        "arguments": {
            "content": "The spare gasket ring is in the lower drawer.",
            "type": "fact",
            "tags": ["drawer"],
            "confidence": 0.91,
        },
    }


def test_exact_selected_public_destination_is_copied():
    result = canonical_save(request(
        selected_public_domain="community:tool-shed"), json.dumps(ROUTING))
    assert result.tool_call["arguments"]["domain"] == "community:tool-shed"


@pytest.mark.parametrize("changes, reason", [
    ({"selected_public_domain": "community:not-permitted"},
     "unpermitted_selected_public_domain"),
    ({"request": "Save this publicly."}, "missing_public_selection"),
    ({"selected_destination": "community:not-permitted"},
     "unpermitted_selected_destination"),
    ({"selected_destination": "own_home",
      "selected_public_domain": "community:tool-shed"},
     "conflicting_destination"),
    ({"selected_content": "A different sentence."}, "conflicting_content"),
    ({"content": " "}, "missing_content"),
    ({"memory_type": "secret"}, "invalid_memory_type"),
])
def test_incomplete_or_unselected_save_never_becomes_a_call(changes, reason):
    result = canonical_save(request(**changes), json.dumps(ROUTING))
    assert result.kind == "clarification"
    assert result.tool_call is None
    assert result.reason == reason


def test_cross_owner_private_save_without_grant_fails_closed():
    result = canonical_save(request(memory_owner="Talven"), json.dumps(ROUTING))
    assert result.kind == "clarification"
    assert result.reason == "unauthorized_private_owner"
    assert "nothing was saved" in result.text


def test_cross_owner_private_save_with_explicit_grant_can_be_canonical():
    routing = {**ROUTING, "private_grants": ["Talven"]}
    result = canonical_save(request(memory_owner="Talven"), json.dumps(routing))
    assert result.kind == "tool_call"
    assert "memory_owner" not in result.tool_call["arguments"]


def test_dispatcher_does_not_treat_a_grant_as_an_owner_bound_executor():
    routing = {**ROUTING, "private_grants": ["Talven"]}
    calls, execute = saved()
    result = ActionDispatcher(execute).dispatch_save(
        request(memory_owner="Talven"), json.dumps(routing))
    assert result.kind == "clarification"
    assert result.reason == "owner_bound_executor_required"
    assert calls == []


def test_model_prose_completion_claim_is_replaced_by_canonical_call():
    result = guard_save(
        request(), "I’ve remembered it.", json.dumps(ROUTING))
    assert result.kind == "tool_call"
    assert result.reason == "replaced_unverified_completion_claim"


def test_model_only_metadata_is_not_copied():
    result = guard_save(
        request(),
        '<tool_call>{"name":"sage_remember","arguments":'
        '{"content":"The spare gasket ring is in the lower drawer.",'
        '"type":"fact","tags":["invented"],"confidence":0.99}}</tool_call>',
        json.dumps(ROUTING),
    )
    assert result.kind == "tool_call"
    assert "tags" not in result.tool_call["arguments"]
    assert "confidence" not in result.tool_call["arguments"]


def test_dispatcher_executes_canonical_request_once_without_model_text():
    calls, execute = saved()
    result = ActionDispatcher(execute).dispatch_save(request(), json.dumps(ROUTING))
    assert result.kind == "result"
    assert result.receipt["status"] == "committed"
    assert calls == [result.tool_call]
    assert result.tool_call["arguments"]["content"] == (
        "The spare gasket ring is in the lower drawer.")


def test_dispatcher_does_not_execute_unauthorized_or_incomplete_saves():
    calls, execute = saved()
    dispatcher = ActionDispatcher(execute)
    owner = dispatcher.dispatch_save(
        request(memory_owner="Talven"), json.dumps(ROUTING))
    missing = dispatcher.dispatch_save(
        request(content=""), json.dumps(ROUTING))
    assert owner.kind == missing.kind == "clarification"
    assert calls == []


def test_dispatcher_converts_executor_failure_to_unknown_without_retry():
    calls = []

    def execute(call):
        calls.append(call)
        raise TimeoutError("uncertain write")

    result = ActionDispatcher(execute).dispatch_save(request(), json.dumps(ROUTING))
    assert result.kind == "uncertain"
    assert len(calls) == 1
    assert "do not retry automatically" in result.text
    assert result.receipt is None


def test_dispatcher_rejects_non_mapping_receipt():
    calls, execute = saved(receipt="not a receipt")
    result = ActionDispatcher(execute).dispatch_save(request(), json.dumps(ROUTING))
    assert result.kind == "uncertain"
    assert len(calls) == 1
    assert result.reason == "executor_invalid_receipt"


def test_closed_dispatcher_never_executes():
    calls, execute = saved()
    dispatcher = ActionDispatcher(execute)
    dispatcher.close()
    result = dispatcher.dispatch_save(request(), json.dumps(ROUTING))
    assert result.kind == "error"
    assert calls == []


class FakeSageClient:
    def __init__(self):
        self.calls = []

    def embed(self, *, text):
        self.calls.append(("embed", text))
        return [0.25, 0.75]

    def propose(self, **arguments):
        self.calls.append(("propose", arguments))
        return SimpleNamespace(
            memory_id="memory-1", tx_hash="tx-1", status="proposed",
            committed=True, committed_height=12, projection_confirmed=False,
            retryable=False,
        )


def test_sage_executor_uses_runtime_owned_boundary_and_returns_receipt():
    client = FakeSageClient()
    executor = SageRememberExecutor(
        client, default_domain="private:home", classification=1,
        provider="lantern", admission_check=lambda: True)
    result = ActionDispatcher(executor).dispatch_save(
        request(tags=["drawer"], confidence=0.91), json.dumps(ROUTING))
    assert result.kind == "result"
    assert result.receipt["memory_id"] == "memory-1"
    assert client.calls[0] == ("embed", "The spare gasket ring is in the lower drawer.")
    _, proposed = client.calls[1]
    assert proposed["domain_tag"] == "private:home"
    assert proposed["memory_type"] == "fact"
    assert proposed["tags"] == ["drawer"]
    assert proposed["confidence"] == 0.91
    assert proposed["classification"] == 1
    assert proposed["provider"] == "lantern"


def test_sage_executor_uses_exact_selected_public_domain():
    client = FakeSageClient()
    executor = SageRememberExecutor(
        client, default_domain="private:home", classification=1,
        admission_check=lambda: True)
    result = ActionDispatcher(executor).dispatch_save(
        request(selected_public_domain="community:tool-shed"), json.dumps(ROUTING))
    assert result.kind == "result"
    assert client.calls[1][1]["domain_tag"] == "community:tool-shed"


def test_sage_executor_refuses_before_propose_without_admission():
    client = FakeSageClient()
    executor = SageRememberExecutor(
        client, default_domain="private:home", classification=1,
        admission_check=lambda: False)
    with pytest.raises(ActionNotAdmitted, match="access_unavailable"):
        executor(canonical_save(request(), json.dumps(ROUTING)).tool_call)
    assert client.calls == []


def test_sage_executor_requires_an_admission_check():
    client = FakeSageClient()
    executor = SageRememberExecutor(
        client, default_domain="private:home", classification=1)
    with pytest.raises(ActionNotAdmitted, match="admission_check_required"):
        executor(canonical_save(request(), json.dumps(ROUTING)).tool_call)
    assert client.calls == []


def test_dispatcher_reports_admission_failure_as_known_refusal():
    client = FakeSageClient()
    executor = SageRememberExecutor(
        client, default_domain="private:home", classification=1,
        admission_check=lambda: False)
    result = ActionDispatcher(executor).dispatch_save(request(), json.dumps(ROUTING))
    assert result.kind == "error"
    assert result.reason.startswith("not_admitted:")
    assert result.receipt is None
    assert client.calls == []


@pytest.mark.parametrize("call", [
    None,
    {},
    {"name": "sage_recall", "arguments": {"query": "x"}},
    {"name": "sage_remember", "arguments": {}},
    {"name": "sage_remember", "arguments": {"content": "x", "type": "secret"}},
])
def test_sage_executor_rejects_noncanonical_calls(call):
    with pytest.raises((TypeError, ValueError)):
        SageRememberExecutor(
            FakeSageClient(), default_domain="private:home", classification=1,
            admission_check=lambda: True)(call)


@pytest.mark.parametrize("request_json, routing_json", [
    ("not json", json.dumps(ROUTING)),
    (json.dumps({"action": "remember"}), "not json"),
    (json.dumps([]), json.dumps(ROUTING)),
    (json.dumps({"action": "remember"}), json.dumps([])),
])
def test_malformed_boundary_inputs_never_execute(request_json, routing_json):
    calls, execute = saved()
    result = ActionDispatcher(execute).dispatch_save(request_json, routing_json)
    assert result.kind == "error"
    assert calls == []
