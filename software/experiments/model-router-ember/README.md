# Model router — Ember, disabled decision-only prototype

**Status: DISABLED CANDIDATE. Not wired into the runtime, not installed, not deployed.**

**Revision 2**, 2 October 2026. Addresses all six defects from Rook's first review
(`work/ember-router-review-20261002/REVIEW.md`) plus seven contract mismatches his
independent probe found in this revision afterwards
(`work/pocket-ui-start-review-20261002/router-probe-results.json`). 169 checks pass.
Still not accepted, not integrated, and not device-qualified.

This package makes routing *decisions* and nothing else. It performs no inference,
opens no socket, imports no runtime module and mutates no state. Every lane it
reasons about is described by data the caller supplies. It is an interface
proposal with executable semantics, so the selection policy can be argued about
concretely instead of in prose.

Owner: Ember (`dsh/sage-commons`). Delegated by Rook, 2 October 2026.
Rook tracking milestone `eaff003b-564d-4ccd-a277-c244d94e2d51`.
Ember task `1f991165-2620-4e55-8ed9-573da6dd7432`.

Normative reading accompanies this code: `work/ember-model-router-20261002/`.

## What revision 2 changed

| # | Review finding | Fix |
| --- | --- | --- |
| 1 | A deterministic request with a stale owner or epoch was accepted, because session validation sat inside the lane gate after the deterministic early return. | Identity and lifecycle are validated **first**, before any path, including the bypass. |
| 2 | An image on a resident text request or approved escalation selected a text-only lane, silently discarding the photo. | Required capabilities are **derived from the payload**, not from the caller's `request_class`. A mislabelled image request is refused, never downgraded. |
| 3 | `battery chemistry`, `storage of insulin` and `signal processing` were answered as device status by a prefix match. | Prefix matching is gone. Intents are a **closed enum with whole-string phrasings**; anything else routes to the model. |
| 4 | `qualified="false"`, `session_epoch=True`, a NaN deadline and negative token counts were accepted. | **Strict validation**: exact `bool`/`int`/`enum` types and finite bounds. `InvalidFacts` is deliberately a different type from `RouteRefused`, so a caller bug cannot read as a routing outcome. |
| 5 | The context check reserved 256 answer tokens while selecting a 512-token budget, and the caller's count already included policy and recall. | `prompt_tokens` is now **the complete input by contract**; the router adds nothing. The reserve equals **the budget actually selected**. |
| 6 | No cancellation or sleep admission; fallback restarted the relative deadline. | `LifecycleFacts` with cancelled/fenced/elapsed, **one shared total budget**, and `REQUIRE_LIFECYCLE_FACTS` refusing an absent lifecycle rather than assuming it clear. |

The deterministic bypass is no longer reachable from a caller-supplied label, no
longer claims to have answered from device state (it names the handler it defers
to), and no longer returns a decision on a budget it does not have.

## What is here

| File | Purpose |
| --- | --- |
| `router.py` | Request classification, lane gating, selection and fallback decision. Pure. |
| `policy.py` | The tunable constants: budgets, deadlines, escalation thresholds. |

## What is deliberately not here

- No HTTP client. The prototype cannot call a model even by accident.
- No import of `sage_lantern`. The runtime is audited, not modified.
- No model download, conversion, training or evaluation.
- An escalation lane, even when it is *configured* as available, never wins a
  decision unless a request is explicitly marked `escalation_approved`. That
  models the "stay cheap, escalate on purpose" policy and keeps the default path
  resident-only.

## Relation to the runtime today

The runtime has no router. `VoiceOrchestrator` takes exactly one `LanguageModel`
plus an optional `AnswerVerifier`, and the only routing-influenced branch in the
whole path is `image_path is None` in `adapters.LlamaCppHTTP`. See
`work/ember-model-router-20261002/PROPOSAL.md` for the audit that establishes
that, and `DECISION_MATRIX.md` for the request-class matrix.

The intended integration point is narrow by design: a single object that
implements the existing `LanguageModel` protocol and delegates to the currently
selected lane. It must not require changes to `VoiceOrchestrator`, because the
orchestrator's timing, cancellation and state-machine behaviour is already
accepted and load-bearing.

## Hard constraints inherited from the existing code

1. **Guarded inference is bound to `http://127.0.0.1:8081`.**
   `adapters.LlamaCppHTTP._inference_admission` rejects any other base URL when
   `SAGE_LANTERN_INFERENCE_GUARD` is set. The router therefore treats the text
   lane endpoint as fixed while the guard is active, not as a free choice.
2. **Endpoint identity is the port, not the service.** `:8082` is drafted by
   `sage-lantern-vlm.service` *and* `sage-lantern-llm-cpu.service`, which the
   units themselves record as mutually exclusive. The router models `:8082` as
   conditionally claimed.
3. **The engine serialises.** Both NPU servers hold one lock and serve one
   inference at a time. Parallel lanes do not increase throughput; they queue.
4. **Budgets differ per lane and are enforced at both ends.** The client caps
   images at 512 KiB and 640 px; both server families advertise far larger
   limits. The tighter client bound wins.
5. **`enable_thinking` is disabled unconditionally** on the text path because
   Qwen 3.5 can otherwise spend the whole budget on hidden reasoning and return
   empty visible content.

## Running the tests

From the repository root:

```
PYTHONPATH=.:software/runtime/src python3 -m pytest scripts/tests/test_ember_model_router.py -q
```

169 checks, 0.2 s. The test file registers the hyphenated candidate directory as
a synthetic package itself, so no `__init__.py` outside the reserved paths is
needed. Those two markers (`software/__init__.py`, `software/experiments/__init__.py`)
were removed at review request and nothing else in the tree imported that path.

These tests are self-contained. They do **not** import `sage_lantern` and
therefore do **not** need the pinned `sage-agent-sdk` wheel. This matters,
because the shared development environment currently has the wrong SDK version
and a bare `pytest scripts/tests` fails to collect for unrelated, pre-existing
reasons described in `work/ember-model-router-20261002/ENVIRONMENT_FINDINGS.md`.

## What this candidate still does not have

Stated so a passing suite is not mistaken for qualification:

- **No measured tokenization.** `prompt_tokens` is a caller declaration. Real
  tokenizer and image occupancy remain unqualified.
- **No claim about live NPU contention.** `LaneFacts.contended` records that an
  endpoint has more than one service *definition*. Per-engine locks prove
  serialisation within an engine, not exclusion between separately running
  engines. Only post-gate measurement can settle that.
- **No cancellation implementation.** The router fences a cancelled request; it
  does not cancel anything. Cancellation of real work belongs to the adapter.
- **No device, model, service or network contact of any kind**, by design.


