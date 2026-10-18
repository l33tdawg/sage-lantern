# Streaming speech — Ember, disabled candidate

**Status: DISABLED CANDIDATE. Not wired into the orchestrator, no audio device is
opened, no runtime module is imported, nothing is installed or deployed.**

Revision 1, 3 October 2026. Owner: Ember (`dsh/sage-commons`). Delegated by Rook;
Ember task `e2524271-539e-4af5-9b57-fe8f467048bf`, program `7342ee23-bc0c-4bce-b7b3-267ef08c66f0`.

Normative reading accompanies this code: `work/ember-streaming-speech-20261002/`.

## Why it exists

The runtime speaks only after the whole answer exists and the verifier has run
(`orchestrator.py:136-152`): `tts.synthesize(response, ...)` is called once, on the
complete text. That is a safe order and a slow one — nothing is audible until the
last token and the fact check are both done. This package is the interface
proposal for speaking approved sentences as they complete, with executable
semantics so the trade-off can be argued about concretely.

## The rule everything else follows from

**With verification enabled, a sentence may not be spoken while a later verdict
could still replace it.**

`AnswerVerifier.verify(question, answer)` only accepts a *complete* answer and
returns one of `ok`, `corrected`, `unsure`, `unavailable`. A `corrected` verdict
replaces the text. So under this rule, streaming and enabled verification are
mutually exclusive at this layer, and the controller enforces that instead of
pretending otherwise: while `policy.verify` is true nothing is released, the
draft is buffered under a byte bound, and the verdict decides what may be spoken.
`policy.VERDICT_RELEASE` records the mapping in one place; it mirrors the
runtime's existing rule that only `corrected` replaces the draft.

That is a deliberate, falsifiable claim and the obvious thing to disagree with.
If a future verifier can approve an increment, this restriction should be
replaced by that approval rather than kept alongside it.

## What is here

| File | Purpose |
| --- | --- |
| `policy.py` | Bounds, the verdict→release mapping, and their validation. Pure data. |
| `segmenter.py` | Incremental sentence segmentation. Pure text; decides where a sentence ends, and more importantly where it does not. |
| `controller.py` | The state machine: generated text, approved text, playback, fences. |

Three states are kept apart on purpose. **Generated** is what the model produced.
**Approved** is what verification cleared — identical to generated when
verification is off, frozen at the verdict when it is on. **Playback** is what the
sink actually started. A release is not playback, and a queue is not playback: a
cancellation that empties a queue while the sink keeps talking has cancelled
nothing, so `stop()` is always called on the sink when anything is in flight.

## Bounds

| Bound | Default | What it stops |
| --- | --- | --- |
| `queue_max_items` / `queue_max_bytes` | 4 / 1024 | unbounded backlog in front of the sink |
| `held_max_bytes` | 4096 | a delta that cannot be buffered is refused, never dropped |
| `segment_max_bytes` | 512 | one sentence growing without limit |
| `draft_max_bytes` | 16384 | the whole-answer buffer while a verdict is pending |
| `deadline_seconds` | 30.0 | a session that outlives its budget |

Backpressure is a returned status, not an exception: the refused delta is *not*
consumed, so the caller retries the same text after the sink drains. That is what
makes "nothing is lost or reordered" checkable rather than asserted.

## Running the checks

```sh
PYTHONPATH=.:software/runtime/src python3 -m pytest scripts/tests/test_ember_streaming_speech.py -q
```

63 checks, synthetic input and an injected mock sink, no device and no network.
Run beside the model-router suite (232 checks together) to confirm the two
synthetic package registrations coexist.

## What is deliberately not here

- **No audio.** No device, no ALSA/Pulse, no synthesis, no file is written.
- **No orchestrator wiring.** Nothing imports this package from the runtime, and
  nothing here imports the runtime.
- **No runtime activation.** No feature flag, no config key, no default change.
- **No promotion evidence.** Time-to-first-audible-word, audio latency, CPU
  contention, memory and power on the target are unmeasured; the benchmark in the
  suite is a labelled simulation on an injected clock, not a latency result.

## What is not proven

Session validation is the caller's: these are facts, not credentials, and nothing
here issues, fetches or verifies a session against the runtime. The fence
comparisons are only as good as the facts handed in. The tests describe a
synchronous mock sink; a real device sink has its own completion, error and
backpressure semantics that this cannot qualify. Segmentation is heuristic and
tuned to hold text back when unsure, so it will sometimes speak late rather than
wrong.

## Review round 1 — interface decisions and source repairs (5 October 2026)

Independently reviewed and **held** on seven findings
(`work/rook-streaming-speech-review-20261005/DECISION.json`). The reviewer's own
seventeen paired controls went 9 GREEN / 8 RED / 0 ERROR against the reviewed
revision and 16 GREEN / 1 RED / 0 ERROR against this one. Three of the seven are
interface decisions rather than defects, and they are recorded here because a
later reader will otherwise read them as regressions.

### The approval decision

`VERDICT_RELEASE` previously mapped `unsure` and `unavailable` onto `"draft"`,
mirroring the runtime's rule that only a correction replaces text. That rule
answers *which text is current*; it does not answer *whether the text was
approved*, and releasing on it reads "we could not check this" as "this passed".
An inconclusive verdict now releases nothing: the draft still survives as the
current text, its bytes are accounted in `unapproved_bytes` rather than dropped
silently, the outcome is `unapproved`, and the session still freezes so no later
delta can escape. `unavailable` is deliberately not softer than `unsure` — being
unable to ask is not an answer. The authored test that pinned the old behaviour
was rewritten with this reasoning attached rather than silently inverted.

### The delivery-identity decision

A raw delta that merely repeated the previous text was suppressed as a retry,
which silently dropped a legitimate repeated token: two `"Echo. "` deltas
produced one. Text equality cannot establish delivery, so it is no longer used.
Retry suppression now requires `emission_id`, the upstream's own stable identity
for the emission: the same id twice is a retry, two ids are two emissions however
identical their text. `mode='snapshot'` keeps its old behaviour and is the
supported interface for a caller that can only send whole answers. This is an
API decision, so it changes `feed`'s signature and is not retrofittable to a
caller that has no identity to send.

### The end-of-answer event

There was no way to finish a final sentence that ends without whitespace: a
streamed boundary needs a terminator plus evidence that the sentence ended, and
end-of-input is not that evidence, so `"Final sentence."` waited forever. A
guarded `finish()` now flushes the remainder through the ordinary admission and
bounding path, and is refused when verification is enabled because a verdict is
already the end of the answer. `feed` still does not guess: inferring "the answer
is over" from the absence of more input is exactly how a streaming path starts
speaking half-written sentences.

### The four source repairs

| Finding | Repair |
| --- | --- |
| SPEECH-F02 | The verdict-to-playback transition is bounded like every other release. `_release` is all-or-nothing: a release that cannot fit the queue or the held buffer is rolled back whole and refused as `release_exceeds_bounds` with the segments retained in `last_released`, instead of appending past `held_max_bytes`. |
| SPEECH-F03 | A corrected answer's UTF-8 size is validated against `draft_max_bytes` **before** the draft is replaced or anything plays, and refuses as `corrected_budget_exceeded`. The injected caller is not the party enforcing this budget. |
| SPEECH-F04 | A natural sentence boundary must itself fit the ceiling. A boundary moves the cut; it does not license the size. The 202-byte segment under a 32-byte ceiling is cut like any other over-long text, and reconstruction is preserved. |
| SPEECH-F05 | A refused sink stop is no longer reported as a completed drain. The state is `STOP_UNCERTAIN`, the obligation is retained in `stop_unresolved`, and `inflight` keeps the item that may still be playing. `stopped` and `drained` mean playback positively ended; being unable to stop is a different fact. |

`SpeechItem` became a `str` subclass so a retained obligation stays serialisable
for a reviewer's report; `item_id` and `text` remain its interface.

### Results on this revision

| Evidence | Result |
| --- | --- |
| The reviewer's seventeen paired controls, as delivered | **9 GREEN / 8 RED / 0 ERROR** |
| The same controls on this revision | **16 GREEN / 1 RED / 0 ERROR** |
| The authored suite | **54 passed** (was 45) |

The one remaining RED is `terminal_text_space_False`, and it is a harness
limitation rather than a source defect: that scenario feeds `"Final sentence."`,
drains, and asserts the text played, but never invokes `finish()` — so it
exercises no end-of-answer event at all. The control does report
`has_finish_event: true`. The behavioural evidence for the repair is the authored
test `test_a_final_sentence_without_trailing_whitespace_is_released_by_finish`,
which calls the event and observes the sentence spoken intact.

## Review round 2 — six findings (5 October 2026)

Independent review of revision 1 returned **HELD** with six findings
(`work/rook-streaming-speech-r1-review-20261005/DECISION.json`), on top of a
separate 23-case interface control set. Five of the six are the same mistake in
different places: a record was written at the moment a caller asked, rather than
at the moment the work was actually admitted, released or settled.

| Finding | Repair |
| --- | --- |
| SPEECH-R1-F01 | The emission identity of a refused delta was committed before admission, so retrying the same `emission_id` after a backpressure refusal came back `duplicate_delta` and the text was lost. `_reconcile` now only *reads* the identity; `_commit_emission` writes it after the text is admitted. Accepted adjacent-id suppression and equal-text-distinct-id behaviour are unchanged. |
| SPEECH-R1-F02 | An approved answer too wide for its bounds was refused as `release_exceeds_bounds` with no way forward: the retained segments had no public re-entry, and a second `verdict` raised `answer_frozen`. The verdict is still all-or-nothing — nothing plays unless the whole answer fits — but the retained segments are now released by a cursor that public events advance: `completed` continues the release as playback frees room, and a repeated `verdict` resumes it. `unapproved_bytes` follows the cursor, so bytes that reached playback are no longer counted as unspoken. |
| SPEECH-R1-F03 | A failed stop retained its obligation honestly but could never be settled: neither an exact completion for the retained item nor a later successful stop resolved it. Both now settle it, and only the retained item does — a foreign session or a completion for a different item settles nothing. Settlement clears playback and nothing else: admission stays permanently fenced. |
| SPEECH-R1-F04 | An empty queue bypassed its byte ceiling, so a 64-byte segment sat in a queue declared to hold 16. The byte ceiling now binds on the first waiting item too. Because `segment_max_bytes` may legitimately exceed `queue_max_bytes`, a segment that can never fit the queue is handed straight to an idle sink (bounded by its own size, nothing stored twice) and held otherwise. |
| SPEECH-R1-F05 | The forced cut walked *forward* over trailing attachments, emitting 18-byte segments under a 16-byte ceiling. The cut now moves *back* instead, so an attachment travels with its base and the segment can only shrink. The ceiling binds; a grapheme cluster may straddle a boundary, which is the honest outcome. |
| SPEECH-R1-F06 | `SpeechItem`'s fields were publicly mutable, so `item_id` could be changed from 1 to 999 and the real completion for item 1 was then ignored while the obligation stayed open. `__setattr__`/`__delattr__` now refuse reassignment and deletion. |

On SPEECH-R1-F06 the reviewer's note was that serialisation belongs in the
reporter or an explicit value adapter, not in the controller's playback identity.
That is what this does: `SpeechItem` **stays a `str` subclass** and gains
immutability rather than losing its `str` base to a frozen dataclass. A plain
dataclass was tried first and broke a reviewer harness that passes the record
straight to `json.dumps` — so the `str` base is load-bearing for exactly the
serialisation reason it was introduced in round 1, and only the mutability was
the defect.

### Results on this revision

| Evidence | Result |
| --- | --- |
| The reviewer's seventeen original paired controls, on the held revision 1 | **16 GREEN / 1 RED / 0 ERROR** |
| The same controls on this revision | **16 GREEN / 1 RED / 0 ERROR** — identical, case for case |
| The reviewer's 23-case interface controls, on the held revision 1 | **14 GREEN / 9 RED / 0 ERROR**, reproduced before editing a byte |
| The same controls on this revision | **23 GREEN / 0 RED / 0 ERROR** |
| Authored suite | **63 passed** (was 54) |
| The nine regression checks added this round, against the held revision 1 | **7 failed** — they discriminate |
| The same checks on this revision | **all pass** |

The single remaining RED in the original seventeen is `terminal_text_space_False`,
unchanged from round 1 and unchanged in outcome: it is the documented
no-finish-event instrument gap, not a source defect. Both positive controls in the
interface set (`accepted_emission_retry_*`, `stop_resolution_positive_stop`) are
still green.

Nothing about this round's scope changes: the candidate is still disabled, still
unwired, still opens no audio device, and no device, audio, model, runtime or
deployment evidence is claimed. Rook's independent review remains the acceptance
gate.

## Review round 3 — four adjacent findings (5 October 2026)

Rook's independent review of round 2 held the round at the frozen source/mock-sink
boundary on three blocking findings and one interface reconciliation. All four are
addressed here, and one of them was a regression this writer introduced in round 2.

### What was wrong, and what changed

| Finding | What was wrong | What the repair does |
| --- | --- | --- |
| SPEECH-R2-F01 (P1, **new r2 regression**) | `_start` caught a raising sink, called `_close(SINK_FAILED)` and returned nothing, so `_place` still reported success and `_release` still called `_pump()`. A failing direct handover therefore started the *next* segment and played it into a fenced session. A sink that stops cleanly makes it worse: `_close` leaves no obligation inflight, so the pump takes the next item and speaks it. | `_start` returns whether the sink took the item. `_place` refuses outright once fenced, `_release` and `_release_progress` stop the walk, and `_pump` refuses when fenced. The failing segment stays the single inflight obligation and `stop_uncertain`/`fenced` remain the honest state. |
| SPEECH-R2-F02 (P1, inherited) | The queue and the held buffer were drained as two ordered lanes rather than one ordered stream. An oversized first segment leaves through a direct handover, so a *newer* segment can be queued while an *older* one waits in held; draining the queue first then played them out of order. | `SpeechItem` carries an admission ordinal, and `_take_oldest` takes whichever container holds the earliest-admitted item. Playback order is the caller's emission order across both destinations, on every event. |
| SPEECH-R2-F03 (P2, inherited) | The backward attachment walk consumed the entire byte-valid prefix, `_forced_cut` returned `None`, and `_split_remainder` read `None` as "cannot split" and emitted the whole buffer — 21 bytes under a 16-byte ceiling. | When the attachment walk collapses, the cut falls back to the prefix that decodes and fits. The ceiling wins; a grapheme cluster may straddle the boundary, which is what this file already documents as the honest outcome. |
| SPEECH-R2-I01 (P2, interface) | A repeated verdict with no drain and no capacity change released a prefix that the first verdict had refused, so the same public event read two different ways depending on how many times it arrived. | The contract is now stated once and enforced in one place. See the decision below. |

### The release decision (SPEECH-R2-I01)

Rook asked for consistent bounded progressive or terminal release semantics across
the first verdict and a retry, with the source, the documentation and the controls
aligned. The two published instruments disagreed about the same code, so the
semantics are stated here explicitly.

**A first verdict is all-or-nothing; a retry resumes progressively.** On the first
verdict the whole approved answer must fit the placement bounds at that moment or
nothing plays — no partial playback of an answer, and the segments stay retained
and named in `last_released`. After that verdict the answer is approved and
retained, and the documented recovery from round 1 applies unchanged: the release
cursor advances as room frees, `completed` continues the walk, and a repeated
`verdict` resumes it. Every individual release stays bounded — `_release_progress`
places a prefix, rolls a placement back if it does not fit, and never exceeds
`queue_max_bytes` or `held_max_bytes` — and `approved_unplaced_bytes` follows the
cursor, so bytes that reached playback stop being counted as unplaced. Truly
unapproved text is a separate count: `unapproved_bytes` reports text the verifier
never cleared, so an accepted answer is never described as a rejected one.

That reading is the one the source and this file already documented, and it is the
only reading under which an answer wider than the queue and held buffers together
becomes speakable at all. The consequence is recorded honestly rather than
smoothed: `identical_verdict_retry_release_contract` in Rook's adjacent set stays
**RED**. It asserts that a retry plays *nothing* when capacity is unchanged, which
is the opposite of the resumability the same reviewer required in round 1 and which
`approved_release_progress_200` in the earlier interface set asserts must happen.
Those two instruments cannot both pass on one implementation; the discriminating
case added below pins the behaviour this revision actually implements.

### Results on this revision

| Evidence | Held r2 | This revision |
| --- | --- | --- |
| Rook's adjacent controls, `work/rook-streaming-speech-r2-review-20261005/ADJACENT_CONTROLS.py` | 6 GREEN / 5 RED / 0 ERROR | **10 GREEN / 1 RED / 0 ERROR** |
| Rook's interface controls, `work/rook-streaming-speech-r1-review-20261005/INTERFACE_CONTROLS.py` | 23 GREEN / 0 RED / 0 ERROR | **23 GREEN / 0 RED / 0 ERROR** — unchanged |
| Rook's original seventeen, `…/ORIGINAL_CONTROLS.py` | 16 GREEN / 1 RED / 0 ERROR | **16 GREEN / 1 RED / 0 ERROR** — unchanged case-for-case |
| Authored suite | 63 passed | **68 passed** |
| The five cases added this round, against the exact held r2 source | — | **4 failed, 1 passed** — they discriminate |
| The same five cases on this revision | — | **all pass** |

The single RED in the adjacent set is the I01 contract disagreement described
above. The single RED in the original seventeen is `terminal_text_space_False`,
unchanged from round 1 and unchanged in outcome: the documented no-finish-event
instrument gap, not a source defect.

The five added cases are `test_a_failed_direct_handover_does_not_start_the_next_segment`,
`test_a_failed_speak_stops_the_walk_before_the_next_segment` (both F01),
`test_a_queued_segment_cannot_overtake_an_older_held_one` (F02),
`test_a_single_attachment_run_obeys_the_declared_ceiling` (F03) and
`test_a_repeated_verdict_resumes_a_refused_release_progressively` (I01, the pinned
semantics — it passes on both revisions, because the resumability it asserts is the
behaviour the held revision already had). Raw output is in
`work/ember-streaming-speech-r3-20261005/controls/held-r2-discrimination/`.

Nothing about the scope changes. The candidate is still disabled, still unwired,
still opens no audio device, and Rook's independent review remains the acceptance
gate for every claim above.
