"""Fail-closed canonicalization and dispatch for Lantern memory actions.

The model proposes text; the runtime owns values, authorization and execution.
This module never executes a model-authored tool call and never invents content,
metadata, a destination or an owner.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

TOOL_CALL = re.compile(r"\s*<tool_call>\s*(\{.*\})\s*</tool_call>\s*", re.S)
MEMORY_TYPES = {"fact", "observation", "inference", "task"}
RECALL_SCOPES = {"local", "auto", "federated"}
COMPLETION_CLAIM = re.compile(
    r"(?:\b(?:i\s+have|i've|has\s+been|have\s+been|successfully|was)\s+"
    r"(?:saved|stored|written|committed|recorded|published|posted|shared|"
    r"remembered|memorized|memorised|noted)\b)"
    r"|(?:\b(?:i|it|is)\s+(?:saved|stored|written|committed|recorded|"
    r"published|posted|shared|remembered|memorized|memorised|noted)\b)"
    r"|(?:\b(?:save|write|lookup)\s+(?:is|was)\s+(?:complete|committed|recorded|done)\b)"
    r"|(?:^\s*(?:saved|stored|written|committed|recorded|published|posted|"
    r"remembered|memorized|memorised|noted)\b)",
    re.I | re.M,
)


@dataclass(frozen=True)
class GuardResult:
    kind: str
    text: str
    tool_call: dict | None = None
    reason: str = ""


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of one dispatcher invocation.

    ``uncertain`` means the executor raised or did not return a mapping. The
    caller must not retry automatically; SAGE-side reconciliation owns that
    decision.
    """

    kind: str
    text: str
    tool_call: dict | None = None
    receipt: dict | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "text": self.text,
            "tool_call": self.tool_call,
            "receipt": self.receipt,
            "reason": self.reason,
        }


class ActionNotAdmitted(RuntimeError):
    """Raised before any SAGE call when the runtime authority is unavailable."""


def _parse_tool_call(text: str) -> dict | None:
    match = TOOL_CALL.fullmatch(text)
    if not match:
        return None
    try:
        call = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(call, dict) or not isinstance(call.get("name"), str):
        return None
    arguments = call.get("arguments")
    if arguments is None:
        arguments = {
            key: call[key]
            for key in ("content", "query", "domain", "type", "tags", "confidence",
                        "scope", "top_k", "min_confidence")
            if key in call
        }
    if not isinstance(arguments, dict):
        return None
    return {"name": call["name"], "arguments": arguments}


def completion_claim(text: str) -> bool:
    normalized = (text or "").replace("’", "'").replace("‘", "'")
    return bool(COMPLETION_CLAIM.search(normalized))


def _canonical_arguments(request: dict, routing: dict) -> tuple[dict | None, str]:
    owner = request.get("memory_owner")
    if owner not in (None, "self"):
        grants = routing.get("private_grants", [])
        if not isinstance(grants, list) or owner not in grants:
            return None, "unauthorized_private_owner"

    selected_content = request.get("selected_content")
    plain_content = request.get("content")
    if (selected_content is not None and plain_content is not None
            and selected_content != plain_content):
        return None, "conflicting_content"
    content = selected_content if selected_content is not None else plain_content
    if not isinstance(content, str) or not content.strip():
        return None, "missing_content"

    memory_type = request.get("memory_type", "fact")
    if memory_type not in MEMORY_TYPES:
        return None, "invalid_memory_type"

    permitted = routing.get("permitted_public_domains", [])
    if not isinstance(permitted, list) or any(
            not isinstance(domain, str) or not domain for domain in permitted):
        return None, "invalid_routing"

    destination = request.get("selected_destination")
    public_selection = request.get("selected_public_domain")
    if destination is not None and public_selection is not None:
        if destination == "own_home" or destination != public_selection:
            return None, "conflicting_destination"
    domain = None
    if destination is not None:
        if destination == "own_home":
            domain = None
        elif isinstance(destination, str) and destination in permitted:
            domain = destination
        else:
            return None, "unpermitted_selected_destination"
    elif public_selection is not None:
        if isinstance(public_selection, str) and public_selection in permitted:
            domain = public_selection
        else:
            return None, "unpermitted_selected_public_domain"
    else:
        request_text = request.get("request", "")
        if isinstance(request_text, str) and re.search(
                r"\bpublic(?:ly)?\b", request_text, re.I):
            return None, "missing_public_selection"

    arguments = {"content": content, "type": memory_type}
    if domain is not None:
        arguments["domain"] = domain
    tags = request.get("tags")
    if isinstance(tags, list) and all(isinstance(tag, str) for tag in tags):
        arguments["tags"] = tags
    confidence = request.get("confidence")
    if (isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and 0 <= confidence <= 1):
        arguments["confidence"] = confidence
    return arguments, "ok"


def _parse_inputs(request_json: str, routing_json: str
                  ) -> tuple[dict | None, dict | None, GuardResult | None]:
    try:
        request = json.loads(request_json)
        routing = json.loads(routing_json)
    except (TypeError, json.JSONDecodeError):
        return None, None, GuardResult(
            "error", "The request could not be parsed; nothing was saved.",
            reason="invalid_request_json")
    if not isinstance(request, dict) or not isinstance(routing, dict):
        return None, None, GuardResult(
            "error", "The request could not be parsed; nothing was saved.",
            reason="invalid_request_shape")
    return request, routing, None


def _is_save(request: dict) -> bool:
    return request.get("action") == "remember" or request.get("authorize_save") is True


def canonical_save(request_json: str, routing_json: str) -> GuardResult:
    """Return the one canonical save derived only from trusted request fields.

    A complete authorized save is executable even when the model wrote prose or
    no tool call at all. An incomplete or unauthorized save never yields a call.
    """
    request, routing, error = _parse_inputs(request_json, routing_json)
    if error is not None:
        return error
    if not _is_save(request):
        return GuardResult("pass", "", reason="not_a_save_action")
    arguments, why = _canonical_arguments(request, routing)
    if arguments is None:
        messages = {
            "missing_content": "Which content do you mean? Nothing was saved.",
            "missing_public_selection":
                "Which permitted public domain do you select? Nothing was saved.",
            "unauthorized_private_owner":
                "Saving to another person's private memory requires a trusted "
                "grant; nothing was saved.",
        }
        return GuardResult(
            "clarification",
            messages.get(why, "That save is not authorized as written; nothing was saved."),
            reason=why,
        )
    return GuardResult(
        "tool_call", "",
        tool_call={"name": "sage_remember", "arguments": arguments},
        reason="canonicalized_request",
    )


def canonical_recall(request_json: str, routing_json: str) -> GuardResult:
    """Return the one lookup derived only from the trusted recall request."""
    request, routing, error = _parse_inputs(request_json, routing_json)
    if error is not None:
        return GuardResult(
            "error", "The request could not be parsed; no lookup was made.",
            reason=error.reason)
    if request.get("action") != "recall":
        return GuardResult("pass", "", reason="not_a_recall_action")
    query = request.get("query")
    if not isinstance(query, str) or not query.strip():
        return GuardResult(
            "clarification",
            "What should I look up? No query was supplied.",
            reason="missing_query")
    arguments = {"query": query}
    scope = request.get("scope")
    if scope is not None:
        if scope not in RECALL_SCOPES:
            return GuardResult(
                "clarification",
                "The supplied lookup scope is not valid; nothing was looked up.",
                reason="invalid_scope")
        arguments["scope"] = scope
    top_k = request.get("top_k")
    if top_k is not None:
        if (not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0):
            return GuardResult(
                "clarification",
                "The supplied lookup limit is not valid; nothing was looked up.",
                reason="invalid_top_k")
        arguments["top_k"] = top_k
    min_confidence = request.get("min_confidence")
    if min_confidence is not None:
        if (not isinstance(min_confidence, (int, float))
                or isinstance(min_confidence, bool)
                or not 0 <= min_confidence <= 1):
            return GuardResult(
                "clarification",
                "The supplied confidence limit is not valid; nothing was looked up.",
                reason="invalid_min_confidence")
        arguments["min_confidence"] = min_confidence

    permitted = routing.get("permitted_public_domains", [])
    if not isinstance(permitted, list) or any(
            not isinstance(domain, str) or not domain for domain in permitted):
        return GuardResult(
            "error", "The routing could not be read; no lookup was made.",
            reason="invalid_routing")
    destination = request.get("selected_destination")
    public_selection = request.get("selected_public_domain")
    if destination is not None and public_selection is not None:
        if destination == "own_home" or destination != public_selection:
            return GuardResult(
                "clarification",
                "The supplied destinations conflict; nothing was looked up.",
                reason="conflicting_destination")
    domain = None
    if destination == "own_home":
        domain = None
    elif public_selection is not None:
        if public_selection in permitted:
            domain = public_selection
        else:
            return GuardResult(
                "clarification",
                "The selected public domain is not permitted; nothing was looked up.",
                reason="unpermitted_selected_public_domain")
    elif destination is not None:
        if destination in permitted:
            domain = destination
        else:
            return GuardResult(
                "clarification",
                "The selected destination is not permitted; nothing was looked up.",
                reason="unpermitted_selected_destination")
    if domain is not None:
        arguments["domain"] = domain
    return GuardResult(
        "tool_call", "",
        tool_call={"name": "sage_recall", "arguments": arguments},
        reason="canonicalized_request")


def guard_save(request_json: str, model_text: str, routing_json: str) -> GuardResult:
    """Evaluate model output against a canonical save request.

    This helper is for review/response paths. Production execution uses
    :func:`canonical_save` through :class:`ActionDispatcher`; model-authored
    arguments are never the execution source.
    """
    canonical = canonical_save(request_json, routing_json)
    if canonical.kind != "tool_call":
        return canonical

    parsed = _parse_tool_call(model_text)
    claimed = completion_claim(model_text)
    if parsed is not None and parsed["name"] == "sage_remember":
        return GuardResult(
            "tool_call", "", tool_call=canonical.tool_call,
            reason="canonicalized_model_call")
    if parsed is not None:
        return GuardResult(
            "error", "That action is not the requested save; nothing was saved.",
            reason="wrong_tool")
    if claimed:
        return GuardResult(
            "tool_call", "", tool_call=canonical.tool_call,
            reason="replaced_unverified_completion_claim")
    return GuardResult(
        "pass", model_text, reason="model_did_not_claim_completion")


def guard_recall(request_json: str, model_text: str, routing_json: str) -> GuardResult:
    """Canonicalize a requested lookup even when the model asks for its query."""
    canonical = canonical_recall(request_json, routing_json)
    if canonical.kind != "tool_call":
        return canonical
    parsed = _parse_tool_call(model_text)
    if parsed is not None and parsed.get("name") != "sage_recall":
        return GuardResult(
            "error", "That action is not the requested lookup; nothing was looked up.",
            reason="wrong_tool")
    return GuardResult(
        "tool_call", "", tool_call=canonical.tool_call,
        reason="canonicalized_request")


def guard_action(request_json: str, model_text: str, routing_json: str) -> GuardResult:
    """Dispatch one trusted action request to the save or recall guard."""
    try:
        request = json.loads(request_json)
    except (TypeError, json.JSONDecodeError):
        return GuardResult(
            "error", "The request could not be parsed; nothing was performed.",
            reason="invalid_request_json")
    if not isinstance(request, dict):
        return GuardResult(
            "error", "The request could not be parsed; nothing was performed.",
            reason="invalid_request_shape")
    if _is_save(request):
        return guard_save(request_json, model_text, routing_json)
    if request.get("action") == "recall":
        return guard_recall(request_json, model_text, routing_json)
    return GuardResult("pass", model_text, reason="not_an_action_request")


def render(result: GuardResult) -> str:
    if result.kind == "tool_call" and result.tool_call is not None:
        return "<tool_call>" + json.dumps(
            result.tool_call, separators=(",", ":")) + "</tool_call>"
    if result.kind == "error":
        raise ValueError(result.reason)
    return result.text


class ActionDispatcher:
    """One-shot boundary around a trusted SAGE/OpenClaw executor.

    The executor receives only the canonical tool call. It owns the authenticated
    SAGE client and returns its durable receipt. Exceptions become ``uncertain``;
    this class never retries, substitutes a plan or turns an exception into a
    success message.
    """

    def __init__(self, execute: Callable[[dict], Mapping]):
        if not callable(execute):
            raise TypeError("execute must be callable")
        self._execute = execute
        self._closed = False

    def dispatch_save(self, request_json: str, routing_json: str) -> DispatchResult:
        if self._closed:
            return DispatchResult(
                "error", "The action dispatcher is closed; nothing was saved.",
                reason="dispatcher_closed")
        decision = canonical_save(request_json, routing_json)
        if decision.kind != "tool_call" or decision.tool_call is None:
            return DispatchResult(
                decision.kind, decision.text,
                tool_call=decision.tool_call, reason=decision.reason)
        parsed_request = json.loads(request_json)
        if parsed_request.get("memory_owner") not in (None, "self"):
            return DispatchResult(
                "clarification",
                "This dispatcher has no owner-bound executor for another person's "
                "private memory; nothing was saved.",
                tool_call=decision.tool_call,
                reason="owner_bound_executor_required",
            )
        try:
            receipt = self._execute(decision.tool_call)
        except ActionNotAdmitted as exc:
            return DispatchResult(
                "error",
                "The private action is not currently admitted; nothing was saved.",
                tool_call=decision.tool_call,
                reason=f"not_admitted:{exc}",
            )
        except Exception as exc:
            return DispatchResult(
                "uncertain",
                "The save outcome is unknown; do not retry automatically.",
                tool_call=decision.tool_call,
                reason=f"executor_exception:{type(exc).__name__}",
            )
        if not isinstance(receipt, Mapping):
            return DispatchResult(
                "uncertain",
                "The save outcome is unknown; do not retry automatically.",
                tool_call=decision.tool_call,
                reason="executor_invalid_receipt",
            )
        return DispatchResult(
            "result", "", tool_call=decision.tool_call,
            receipt=dict(receipt), reason="executed_once")

    def close(self) -> None:
        self._closed = True


class SageRememberExecutor:
    """Trusted executor for canonical ``sage_remember`` calls.

    The SAGE client, default private domain, classification and admission check
    are runtime-owned. The model cannot supply any of them. The returned mapping
    is a receipt, not a success claim: callers must read its status.
    """

    def __init__(self, client, *, default_domain: str, classification: int,
                 provider: str | None = None,
                 admission_check: Callable[[], bool] | None = None):
        if not callable(getattr(client, "propose", None)):
            raise TypeError("client must expose propose")
        if not isinstance(default_domain, str) or not default_domain:
            raise ValueError("default_domain must be a non-empty string")
        if type(classification) is not int or not 0 <= classification <= 4:
            raise ValueError("classification must be an integer from 0 to 4")
        if provider is not None and (not isinstance(provider, str) or not provider):
            raise ValueError("provider must be absent or a non-empty string")
        if admission_check is not None and not callable(admission_check):
            raise TypeError("admission_check must be callable")
        self._client = client
        self._default_domain = default_domain
        self._classification = classification
        self._provider = provider
        self._admission_check = admission_check

    def _admit(self) -> None:
        if self._admission_check is None:
            raise ActionNotAdmitted("admission_check_required")
        try:
            admitted = self._admission_check() is True
        except Exception:
            admitted = False
        if not admitted:
            raise ActionNotAdmitted("access_unavailable")

    def __call__(self, call: dict) -> dict:
        if (not isinstance(call, dict) or set(call) != {"name", "arguments"}
                or call.get("name") != "sage_remember"
                or not isinstance(call.get("arguments"), dict)):
            raise ValueError("canonical_save_call_required")
        arguments = call["arguments"]
        content = arguments.get("content")
        memory_type = arguments.get("type")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("canonical_content_required")
        if memory_type not in MEMORY_TYPES:
            raise ValueError("canonical_memory_type_required")

        self._admit()
        embed = getattr(self._client, "embed", None)
        embedding = embed(text=content) if callable(embed) else None
        propose_arguments = dict(
            content=content,
            memory_type=memory_type,
            domain_tag=arguments.get("domain") or self._default_domain,
            confidence=arguments.get("confidence", 0.8),
            embedding=embedding,
            classification=self._classification,
            provider=self._provider,
        )
        if arguments.get("tags") is not None:
            propose_arguments["tags"] = arguments["tags"]
        response = self._client.propose(**propose_arguments)
        fields = ("memory_id", "tx_hash", "status", "committed",
                  "committed_height", "projection_confirmed", "retryable")
        receipt = {}
        for name in fields:
            if isinstance(response, Mapping):
                value = response.get(name)
            else:
                value = getattr(response, name, None)
            receipt[name] = value
        if not isinstance(receipt["memory_id"], str) or not receipt["memory_id"]:
            raise ValueError("invalid_sage_receipt")
        return receipt
