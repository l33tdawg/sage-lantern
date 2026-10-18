"""Tunable policy constants for the disabled model-router candidate.

Every number here is a *proposal*, not a measurement. Each cites the source that
bounds it or is marked as unqualified. Nothing in this file may be treated as an
accepted device parameter.
"""

# --- Request budget -------------------------------------------------------

#: `adapters.TEXT_MAX_TOKENS` in the shipped runtime.
TEXT_MAX_TOKENS = 512

#: `adapters.LlamaCppHTTP._request` pins 128 max_tokens for the image path.
IMAGE_MAX_TOKENS = 128

#: Escalation lanes are allowed a larger answer because the user asked for the
#: expensive path explicitly. UNQUALIFIED proposal; no escalation lane exists.
ESCALATION_MAX_TOKENS = 512

#: `adapters.TEXT_MAX_TOKENS` comment: the server caps at 128 when the field is
#: absent; complete answers need roughly 150-250 tokens.
SERVER_DEFAULT_MAX_TOKENS = 128

#: `software/npu/vlm_server.py` uses 192 when `max_tokens` is absent.
VLM_SERVER_DEFAULT_MAX_TOKENS = 192

# --- Context ---------------------------------------------------------------

#: `sage-lantern-llm.service` runs `--context 2048` for the resident text model.
TEXT_CONTEXT_TOKENS = 2048

#: `sage-lantern-vlm.service` runs `--context 4096` for the vision lane.
VISION_CONTEXT_TOKENS = 4096

#: `config.RuntimeConfig` -> `SageSDKContext(top_k=4)`. Four recalled records.
RECALL_TOP_K = 4

# --- Input token accounting ------------------------------------------------
#
# CONTRACT, and the fix for review reproduction 5.
#
# `RequestFacts.prompt_tokens` is the **complete** input cost: prompt text, plus
# policy, plus every recalled item. It does NOT include the output budget, and
# the router does not add policy or recall reserves on top of it.
#
# The constants below are therefore *not* used in the router's arithmetic. They
# are kept as the documented way for a **caller** to estimate the input it is
# about to declare, so that estimate stays reviewable in one place. The first
# version added them inside the router on top of a caller count that already
# contained them, double counting the same tokens twice.
#
# The output side is now reserved at exactly the budget actually selected
# (`_budget_tokens`), replacing the old fixed `ANSWER_TOKEN_RESERVE` of 256 that
# sat under a 512-token selection and admitted requests which could not fit.

#: Conservative per-record reservation for a caller estimating recall cost.
#: Deliberately pessimistic and NOT a measurement of real recall size.
RECALL_ITEM_TOKEN_RESERVE = 160

#: Policy text is long and fixed (`adapters.TEXT_POLICY` is ~140 words). A
#: caller estimating its input cost starts here. Not added by the router.
POLICY_TOKEN_RESERVE = 320

#: SUPERSEDED. The old fixed answer reserve. The router now reserves the real
#: selected output budget instead. Retained because the review probe imports it
#: and because removing a constant silently would hide the correction.
ANSWER_TOKEN_RESERVE = 256

#: A caller estimating input cost may use this to build `prompt_tokens`.
def estimated_input_tokens(prompt_tokens: int, recall_items: int = RECALL_TOP_K) -> int:
    """Reference estimator for `RequestFacts.prompt_tokens`.

    Provided so callers and reviewers share one definition. The router never
    calls this; it trusts the declared total and validates only that it is a
    non-negative integer.
    """
    return prompt_tokens + POLICY_TOKEN_RESERVE + RECALL_ITEM_TOKEN_RESERVE * recall_items

# --- Deadlines -------------------------------------------------------------

#: `config.RuntimeConfig.verifier_timeout_seconds` default.
VERIFIER_TIMEOUT_SECONDS = 60.0

#: `adapters.LlamaCppHTTP` construction default.
LLM_TIMEOUT_SECONDS = 120.0

#: Voice-loop patience: a spoken answer arriving after this is not an answer.
#: UNQUALIFIED proposal. The training roadmap's stated ambition is first useful
#: speech within ~2 s warm; this is the *ceiling* for refusing to start, not a
#: target.
RESIDENT_DEADLINE_SECONDS = 30.0

#: Least time worth admitting a request for. Below this the answer cannot reach
#: the user, so admitting it spends a lane slot to produce something that is
#: already too late. Applied to every path, including the deterministic bypass:
#: reviewer probing showed a bypass returning a decision with a *negative*
#: remaining budget, because the bypass computed its own deadline and skipped
#: the check.
MIN_DEADLINE_SECONDS = 1.0

#: An escalation is allowed to be slower than the resident path because it is
#: explicitly requested. UNQUALIFIED proposal.
ESCALATION_DEADLINE_SECONDS = 120.0

# --- Escalation ------------------------------------------------------------

#: Number of distinct failed attempts on a request before escalation becomes
#: eligible. One, so a second try must justify itself.
ESCALATION_MIN_ATTEMPTS = 1

#: Escalation is refused outright unless the resident lane finished and the
#: caller flagged the request as escalation-approved. There is no automatic
#: quality-triggered escalation in this candidate: the verifier's own verdict
#: vocabulary ("ok"/"corrected"/"unsure"/"unavailable") is not a trustworthy
#: routing signal, and treating "unsure" as "escalate" would let a second model
#: spend NPU time on the strength of a first model's self-report.
#:
#: Reviewed as MR-005 and approved as candidate policy: keep automatic
#: escalation disabled. Enforced in `fallback_after_failure`, which refuses
#: rather than promoting when this is False.
ALLOW_AUTOMATIC_ESCALATION = False

# --- Lifecycle -------------------------------------------------------------

#: Refuse a request when no `LifecycleFacts` were supplied.
#:
#: "We did not check cancellation or sleep" must not read the same as "we
#: checked and it was fine". With this True, an absent lifecycle is a
#: `LIFECYCLE_UNVERIFIED` refusal rather than an assumed-clear admission.
REQUIRE_LIFECYCLE_FACTS = True

# --- Deterministic bypass --------------------------------------------------

#: Maximum age of a status observation before the bypass must not use it.
#: UNQUALIFIED proposal: it is a plausibility bound on how stale a battery or
#: signal reading may be before it is re-read or routed to the model, not a
#: measured refresh interval. A stale reading answered confidently is worse than
#: a slow answer.
STATUS_MAX_AGE_SECONDS = 30.0

# --- Image handling --------------------------------------------------------

#: `adapters.LlamaCppHTTP._jpeg_data_uri` bounds. The client is far stricter
#: than either server, and the client bound is the one a device actually meets.
IMAGE_MAX_BYTES = 512 * 1024
IMAGE_MAX_DIMENSION = 640
IMAGE_MAX_PIXELS = 307200

# --- Known lane identifiers ------------------------------------------------

#: Fixed by `adapters.LlamaCppHTTP._inference_admission` while the guard is set.
GUARDED_TEXT_ENDPOINT = "http://127.0.0.1:8081"

#: Contended. See the module docstring of `router.py`.
CONTENDED_VISION_ENDPOINT = "http://127.0.0.1:8082"
