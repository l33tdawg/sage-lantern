# Consolidated SAGE tasks — 10 September 2026

**Historical checkpoint.** See [the 11 September consolidation](SAGE_TASKS_20260911.md)
for current task IDs, deployed UX work, unqualified prototypes and verified counts.

The initial consolidation verified **10 open tasks: five in progress and five planned**.
The owner's subsequent request for clearer Reply recording and live transcription
adds one linked task. Its question-review implementation is now in progress:
**11 open tasks, six in progress and five planned**.
Two stale checkpoints were replaced and linked to their successors. They are
marked `dropped` because they were superseded; their unfinished work was carried
forward. No whole open task was marked done on the strength of a partial milestone.
An in-progress program does not mean a benchmark or training process is running.

## Current task ownership

| Task | SAGE ID | Status and next boundary |
|---|---|---|
| Responsiveness and model quality | `00b1dbf4-ea56-4f7f-9960-780a0ad5f6f4` | In progress; measure normal Ask through first useful screen text, then qualify the next latency change. Owns runtime, model/training experiments and upstream PR follow-up. |
| Recording feedback and live transcription | `900c154d-7641-48f8-a912-41d73342e827` | In progress; question review before Submit, on-screen editing/Record again, prominent recording cue and three-second rotating fade/breathing footer implemented/tested. Owner reports better review UX. Continuous conversation flow, keyboard bottom sheet and direct tap-to-edit question bubble and automatic bottom scroll on follow-up Submit now installed on A (165 frontend tests; UI-only restart). Physical acceptance of the smoother flow and offline rolling ASR remain pending. See [installed question review](../../work/question-review-20260910/README.md) and [continuous flow](../../work/question-flow-20260910/README.md) and [tap-to-edit](../../work/question-tap-edit-20260910/README.md) and [follow-up scrolling](../../work/question-scroll-20260910/README.md) and [wait animation](../../work/wait-animation-20260910/README.md). |
| Initial-question factual grounding | `41822122-f5df-470f-82aa-b25536454a14` | Planned; fix unnecessary refusal/disclaimers and add useful context with optional relevant follow-ups while honoring brief/strict requests. Seven local policy variants, a fresh v2 screen and two fuller-default development screens are retained; none qualified. See [answer experience](../../work/model-trial-20260910/answer-experience/README.md). |
| A/B delivery acceptance | `324b2365-32b6-4e41-bb17-8d991abe0691` | In progress; umbrella for hardware and delivery evidence. Linked to the specialist tasks below; performance execution belongs to the first row. |
| Messages, directory and public sharing | `f66a2503-8da6-48a7-ad50-ebe017e05a45` | In progress; local mailbox admission/recovery, matched backend/UI deployment, approved signed-card refresh, real A/B delivery and receipts, then public-sharing acceptance. |
| B SSH enrollment | `a1221b10-a1ee-4af8-ac40-9be4b94cb44f` | Planned, waiting for B's existing login credential. Shared key, aliases, host pins and A enrollment are complete. |
| Private media and conversation encryption | `e28f5711-dc60-4beb-9a0a-b99fd3b80e1f` | In progress; vault-backed at-rest protection, migration and recovery evidence remain release gates. |
| Power recovery and base-image acceptance | `7a3837c7-19ea-4cb9-b2e1-75b5985dc534` | In progress; physical sleep/wake/reboot acceptance and image provenance remain open. |
| White Pages discovery | `7fed7db3-f0a5-4608-b0b0-dc05b12ffe5f` | Planned; wider person/household/locality discovery after basic Messages/People delivery. |
| Fleet production hardening | `d8cca61b-aa40-41ee-a90e-410933afd8af` | Planned; later production operations, certificates, backups, roles and monitoring. |
| Optional online research | `008da7ad-a6b6-4df3-a2e5-4dd03d020b0f` | Planned design proposal; scope/default decision still pending. Offline behavior remains independent. |

The delivery umbrella retains the January 2027 demo/foundation roadmap and all
unmet radio, audio/PTT, display/touch/video/thermal, onboarding, correction/keyboard,
privacy/recovery and public-proposer requirements in its original record and
[delivery backlog](DELIVERY_BACKLOG.md). Linking those requirements to execution
tasks does not close them. No other agent's tasks were changed.

## Completed milestones and retained limits

- **A SSH management and normal remote unlock:** verified; B enrollment remains.
  See [SSH management](SSH_MANAGEMENT.md).
- **Compact text policy and conversation-only recall compaction:** installed on A.
  The latter was qualified through 100 synthetic requests; paired median cached
  follow-up first-model-text savings were 5.106 and 5.202 seconds, with all matched
  answers identical. This excludes STT, retrieval, publication and browser time.
  See [installed results](../../work/model-trial-20260910/context-latency/CONVERSATION-ONLY-RESULTS.md).
- **NPU experiment and upstream fixes:** the tested full-model configuration was
  rejected and CPU4 retained. The [iwagumi PR](https://github.com/fukumori/iwagumi/pull/1)
  was verified OPEN during this consolidation at head
  `bf277ecc2f04441d9094b158e7c3864eff3a760f`; it is not merged.
- **Training and conversion experiments:** completed in the separate training lab;
  current adapters and derivatives remain ineligible after quality regressions.
  Export success is not device performance or production readiness. New tuning
  requires fresh independent acceptance data.
- **Earlier first-text publication:** local prototype and mocked tests completed;
  no installation or measured board/browser gain. Full knowledge-table formatting
  and the earlier prompt-replay candidate remain rejected for answer regressions.

## Recommended next sequence

Owner's follow-up UX requests are now explicit: deploy the qualified recording cue,
show and approve completed transcription before generation with Edit/Record again/Submit, investigate live partial captions,
and remove unnecessary generic recall disclaimers while retaining meaningful
uncertainty. The latest request also replaces the waiting counter with human
progress copy and asks for fuller answers with helpful follow-ups. The footer, recording cue and transcript-review/keyboard release are now installed
on A; physical acceptance remains. Answer-policy trials remain held after
brevity and retrieved-instruction failures. The prior service's unconfirmed
history-saving outcome also needs investigation; maintenance did not establish
that every prior answer was saved.
The new recording/transcription task and existing initial-question
quality task carry these requirements. Measure their actual boundaries as below.

1. **Measure the normal Ask experience on A.** Use controlled typed questions and
   separately spoken questions. Record end-of-speech/transcript, retrieval, model
   first text, first state publication and first browser-rendered useful text.
   Separate model restart, an already-warm system prefix and conversation follow-ups.
   Report per-run results and use enough repetitions before making p50/p95 claims.
2. **Qualify the change that addresses the measured wait.** Compare fixed system
   prefix warm-up for the first question after restart, and earlier initial text
   publication for display delay, independently. Measure warm-up startup cost and
   state-write/status-poll cost; preserve lock/session behavior. Faster busy-only
   polling follows evidence that browser polling is a material delay.
3. **Fix the initial-question quality failure.** A supplied delivery plan currently
   triggers unnecessary verification/photo requests. Preserve stated facts and
   uncertainty about actual execution; freeze broader correction, photo, refusal,
   general-answer and preference tests before changing policy or training again.
4. **Compare models after the quality gate is credible.** Keep stock/current board
   controls, conversion checks and real device latency. Smaller models, deterministic
   routing and further training remain candidates rather than promised speedups.

B SSH enrollment can resume when its existing credential is available. Messages,
private-storage protection and power acceptance remain delivery work; none was
silently closed or implemented during this consolidation.

## Retired checkpoints

| Previous task | Replacement | Reason |
|---|---|---|
| `28237b40-abdc-4e88-8a01-97a4505927ee` | `00b1dbf4-ea56-4f7f-9960-780a0ad5f6f4` | Old pilot-in-progress description superseded by completed experiment results and one current performance/quality program. Full original roadmap retained through links. |
| `1e2520b3-6937-4396-8015-36ef64ad0991` | `a1221b10-a1ee-4af8-ac40-9be4b94cb44f` | Old key-creation-pending description superseded by A-done/B-pending enrollment scope. |

Both replacements were committed before retiring their predecessors. The initial
consolidation reread verified ten canonical open IDs; after the later owner-requested
recording/transcription task, readback verified eleven.
