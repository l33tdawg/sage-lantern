# Verified shared answers: zero-LLM integration gate

**Status:** Proposed handoff contract; not implemented or enabled.  
**Review:** Repository source only, 2026-09-05. No live queries, keys, photos,
benchmarks, deployments or external research. Only this document is added.

Beauvoir owns `shared_answers.py` and its pure verification/eligibility contract.
That file was not available at this review. Names and schemas below are proposals
to reconcile with that owner before changing runtime code. Other agents' current
conversation, access, controller and UI changes must be preserved.

## 1. What exists and what must change

| Source anchor | Observed behavior | Integration consequence |
|---|---|---|
| `software/runtime/src/sage_lantern/adapters.py:20` | `ContextSource` exposes only `recall(query) -> list[str]`. | Add an optional typed shared-answer lookup capability, not a special string in ordinary recall. |
| `software/runtime/src/sage_lantern/adapters.py:296` | `SageSDKContext` constructs its SDK identity/client eagerly and recall embeds then performs committed-memory retrieval. | To claim no private-context access on a hit, construct the private delegate lazily after a miss; never derive shared eligibility from private recall. |
| `software/runtime/src/sage_lantern/adapters.py:321` | Conversation context uses the fixed local `/history/context` endpoint and forwards the captured device token. | A shared lookup must stay local and separate from conversation retrieval. Do not send private query text to an external shared-cache service. |
| `software/runtime/src/sage_lantern/orchestrator.py:39` | `run_audio` always transcribes, recalls and calls the LLM. Photo support is checked before transcription. | Lookup belongs after successful STT and before private recall/LLM. A verified hit must not require LLM readiness. Photo validation must not be bypassed. |
| `software/runtime/src/sage_lantern/orchestrator.py:14` | `VoiceResult` has no answer-source/provenance fields. | Add backward-compatible typed fields; text alone cannot establish a verified hit. |
| `software/runtime/src/sage_lantern/cli.py:19` | Context selection is static fixture, conversation service, or SDK; the builder eagerly constructs dependencies. | Compose an optional shared-first context with lazy private fallback. Static fixtures must not silently acquire production shared-cache access. |
| `software/runtime/src/sage_lantern/cli.py:154` | CLI constructs its result payload explicitly. | Dataclass changes alone will not reach JSON/JSONL; update both output modes deliberately. |
| `scripts/lantern/unit-a-voice-loop.py:58` | Progress requires recalling then photo analysis or generation before answering. | A cache hit needs a new real stage/transition, not a fabricated generation event. |
| `scripts/lantern/unit-a-voice-loop.py:1066` | Final result validation handles text and timing fields; complete-state publication copies selected fields. | Validate and forward bounded provenance explicitly; extra runtime fields currently do not establish UI attribution. |
| `scripts/lantern/unit-a-voice-loop.py:367` | History persists selected answer fields; conversation storage is separately owned. | Preserve source attribution through authorized history integration; do not promote a cached answer into a new verified personal fact. |
| `scripts/lantern/unit-a-voice-loop.py:853` | Admission-token checks gate ongoing work and publication. | Preserve these checks on both cache-hit and fallback branches, including history saving and TTS. |

Anchors describe this inspected checkout; concurrent changes may move lines.

## 2. Proposed context boundary

```python
context.lookup_shared_answer(
    query: str,
    *,
    language: str | None = None,
    photo_digest: str | None = None,
) -> VerifiedSharedAnswer | None
```

- `None` is a miss or ineligible candidate, not an empty successful answer.
- Only Beauvoir's typed verified result may select the hit branch. Never trust
  a raw mapping, `verified: true` supplied by a caller, or text containing a
  source marker. Pure verification still needs an independently trusted policy,
  verification key/checkpoint and current time supplied by the owning boundary.
- The context adapter owns bounded local storage access and authentication;
  the pure cache owns canonical matching and eligibility. No network refresh,
  model invocation, embedding, private recall, or cache write on this lookup.
- Query matching must be deterministic and conservative. Proposed first slice:
  exact query under a versioned normalization rule, explicit language and
  preapproved public scope. No semantic similarity shortcut for sensitive or
  ambiguous requests. Missing language matches only an explicitly compatible
  entry under the agreed policy; do not assume English or translate a hit.
- Language, domain/locality, validity and evidence requirements must be bound
  to the match. A query about a particular person/place/time cannot reuse an
  answer with missing or different scope merely because words match.
- Personal-context-dependent questions are ineligible for public shared reuse.
  If eligibility cannot be determined without reading private context, miss.
- First release should be text-only unless Beauvoir supplies an explicit photo
  eligibility contract. With an attached photo, never fall back to a text-only
  shared answer for a generic question such as "what is this?".
- If photo hits are approved: validate and hash the exact bounded inference
  JPEG, tag the digest algorithm/input representation, and require an exact
  digest match. This API does not currently receive the original retained JPEG;
  never claim its digest is the original's. Digest matching is not evidence of
  capture authenticity, same scene semantics, or sufficient OCR detail.
- No raw photo, private history, recalled context, owner identifier, device
  token, transcript log or private-source digest enters shared storage/telemetry.
  The query itself may be private: keep local matching ephemeral and unlogged.

### Eligibility and failure taxonomy

The result must bind the exact answer bytes/digest to public source identity,
verification evidence, canonical state and policy version. Require committed,
permitted, current material with appropriate expiry/checkpoint freshness and no
known challenge, deprecation, revocation or conflict. A signature verifies origin
and integrity, not factual truth. A local history answer or intake approval is
not an eligible verified public answer.

Use the public-cache boundaries in
`docs/lantern/DUAL_HOME_AND_PUBLICATION_CONTRACT.md:290`. Offline freshness means
"verified as of this checkpoint under this policy", not knowledge of all later
revocations. Uncertain clock/freshness fails eligibility.

- Ordinary miss/expired/wrong-scope material: fall through to the existing
  authorized pipeline, without promoting rejected content into private recall.
- Corrupt/unsupported cache: bounded unavailable result; policy may permit
  ordinary fallback, but never label corruption as a verified hit.
- Cancellation, revoked device token or lost authorization: abort. These MUST
  NOT become cache misses followed by private recall or an LLM call.
- No cache mutation, automatic publication, corroboration or federation activation.

## 3. Execution, output and provenance

1. Preserve caller session and captured admission token. Recheck authorization.
2. Transcribe normally; no-speech still returns the existing safe error.
3. Emit `recalling`; validate applicable media and perform bounded shared lookup.
4. Recheck cancellation, token and eligibility before publishing a hit.
5. Hit: emit proposed `shared_answer`, then `answering`, and deliver the verified
   answer unchanged in one bounded text callback, not fake token streaming.
   Skip private recall, SDK identity loading, embedding and both LLM methods.
6. Miss: continue existing private recall and normal photo/generation behavior.
7. Optional TTS uses the selected answer with the existing cancellation boundary.
8. Recheck before history persistence and final publication. Completion follows
   history outcome as today; a save warning does not turn a hit into a new fact.

The proposed `shared_answer` stage means local verification completed, not a model
generated anything. Controller transition: `recalling -> shared_answer -> answering
-> speaking?`; retain all existing branches and whitespace/backward-event defenses.
Parent must approve the status sanitizer/UI allowlist before enabling this stage.
If that coordinated change is unavailable, keep the hit feature disabled rather
than emitting `generating` or hiding provenance until after text appears.

Proposed result additions, subject to Beauvoir's canonical schema:

```json
{
  "answer_source": "shared_verified",
  "llm_called": false,
  "provenance": {
    "public_memory_id": "bounded-public-id",
    "chain_id": "bounded-public-chain-id",
    "answer_sha256": "64-lowercase-hex",
    "checkpoint_ref": "bounded-verification-reference",
    "verified_at": "RFC3339",
    "expires_at": "RFC3339",
    "policy_version": "bounded-version"
  }
}
```

Generated results use `answer_source: "generated"`, `llm_called: true` and no
shared-hit provenance. These describe execution, not certainty. Preserve default
fields for existing `VoiceResult` construction and legacy JSON consumers; absent
provenance never means verified. Map a hit to the existing `public-cached` UI
source vocabulary, not `public-live` or personal consensus approval.

CLI/controller must agree how source metadata precedes the first answer text
(a dedicated bounded source event is proposed), survives the final result, and
matches the answer digest. Do not overload the exact two-field progress event.
Reject source changes after visible output, duplicate/conflicting source events,
wrong-session events, oversized fields, arbitrary URLs/paths and unsanitized proof
blobs. Final-result-only attribution is insufficient if streamed text is shown
without its source. Parent owns presentation; conversation owner owns history
schema propagation. If a view cannot preserve provenance, do not label its cached
text as verified or independently corroborated.

## 4. Honest timing semantics

- Keep `stt` as measured speech recognition time.
- Add `shared_lookup` for media-digest work, lookup and eligibility validation.
- On hit: `sage_context = 0`, `llm = 0`, `llm_called = false`; omit
  `llm_first_token` entirely. Zero is measured absence of those operations, not
  a claimed zero-cost response. Keep booleans/source metadata outside timings.
- Add `shared_emit` for hit delivery/callback duration; on fallback it is zero.
- `first_text` remains time from processing start to first non-whitespace text
  emission, not browser paint or a fabricated model-token measurement.
- `total` sums disjoint `stt + shared_lookup + shared_emit + sage_context + llm
  + tts`; do not add first-text/TTFT or nested callback timings again. Existing
  `delta_callback` remains nested in LLM duration on generated responses.
- TTS remains separately measured on either branch. Capture, controller/history
  fsync and display latency are outside runtime total; never advertise it as
  microphone-to-screen wall time. No timers may manufacture progress or savings.

## 5. Required implementation and tests after contract freeze

| Owner/slice | Bounded change | Acceptance cases |
|---|---|---|
| Beauvoir | Freeze verified result, matching, trust/freshness and miss/error semantics. | Tampered answer/proof, wrong query/language/scope/photo, challenged/expired/revoked/conflicting entry, clock uncertainty all ineligible. |
| Runtime context/CLI builder | Optional shared lookup adapter and lazy private delegate; fixed local authority boundary. | Hit performs zero private identity reads, embed/recall calls or external requests. Static fixtures remain isolated. Private query canaries never enter cache/logs. |
| Orchestrator | Early verified-hit branch, shared result metadata, real stages and timing buckets. | Raise-on-call LLM/private-context fakes remain untouched on hit; exact answer preserved; miss invokes normal path once; unsupported-photo hit cannot bypass image policy. |
| CLI | Explicit source metadata in JSON and JSONL, bounded source event and final consistency. | Flush order, hit versus generated payloads, legacy construction, no speech, malformed verifier result, no synthetic TTFT, callback exception has no final success. |
| Controller | Source-event validation/propagation, shared-answer transition, admission-token checks. | Revocation during lookup, before delta, during TTS and before history/final cancels old work; new unlock cannot revive it. No stale source survives a new session. |
| Parent UI/status + conversation owner | Sanitized source display and retained-history provenance. | Attribution precedes visible text; cached is not live/true; saving/replaying is not independent corroboration; absence of provenance never receives a verified badge. |

Extend existing suites only after ownership approval:
`software/runtime/tests/test_orchestrator.py`,
`software/runtime/tests/test_config_cli.py`, relevant adapter tests,
`scripts/tests/test_unit_a_voice_loop.py`, and
`scripts/tests/test_unit_a_voice_lock.py`. Use synthetic public entries, fake
clock/checkpoints, raise-on-call model/SDK objects and fake auth/cancellation.
No real cache, private keys/photos, network or native benchmark is needed for
the implementation gate.

**Go/no-go:** Wait for Beauvoir's contract and the parent-approved public
eligibility/provenance schema, language/photo policy, local lookup wiring and
source-event/UI agreement. Until then, this document authorizes no runtime edits
or feature activation. Offline tests can establish the zero-LLM branch; actual
two-unit freshness, revocation and user-visible provenance require a later,
separately authorized acceptance session.
