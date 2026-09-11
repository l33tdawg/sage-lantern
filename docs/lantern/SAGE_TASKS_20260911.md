# Consolidated SAGE tasks — 11 September 2026

Verified against the assigned SAGE backlog: **12 open tasks — eight in progress and four planned.** An in-progress program does not imply a running training, benchmark or deployment process.

This consolidation replaces two stale task descriptions, records the completed A UI deployment as a separate done milestone, and links the latest evidence to the existing responsiveness, factual-grounding and hardware programs. No unmet requirement was marked complete. This was a backlog/documentation update; no device changes or new experiments were run.

## Completed work

**A conversation UI milestone — DONE:** `14c0b470-dccb-4666-a9a4-350ac859eb3e`.

- Clear confirmed-recording feedback; transcription review before answering; local large-key keyboard and word suggestions; tap the question to edit; Cancel, Record again and deliberate Submit.
- Continuous conversation flow and automatic return to the bottom on follow-up submission, while preserving deliberate scrolling up.
- Removal of redundant New chat drawer controls; shared session-scoped conversation cache; recovery from transient saved-history refresh failures.
- Balanced footer buttons, Follow up label, raised review action frame and toolbar slide out during preparation/back in when answer text starts.
- Three-second rotating human status and the smaller breathing Lantern waiting card.

All are installed on **A**. The latest styling archive is `d765b2653894654f3d12d5d40e676855a98c5dba572c5101cb892a02f33f9e3c`: 114 files and 17 served assets verified, 181 frontend tests passing. The styling install preserved all seven backend process identities. Owner feedback reports improved feel; that is not complete physical voice/touch acceptance on A or the final display.

Evidence: [question review](../../work/question-review-20260910/README.md), [continuous flow](../../work/question-flow-20260910/README.md), [tap editing](../../work/question-tap-edit-20260910/README.md), [scrolling](../../work/question-scroll-20260910/README.md), [conversation cache](../../work/conversation-cache-20260910/README.md), [refresh recovery](../../work/history-refresh-fix-20260910/README.md), [floating controls](../../work/floating-review-panel-20260910/README.md), [compact waiting card](../../work/compact-working-panel-20260910/README.md).

Earlier completed milestones remain credited: A shared-key SSH enrollment and normal owner-authorized remote unlock; compact text policy and conversation-only recall compaction installed on A; the bounded NPU investigation and submission of actual upstream harness fixes. B SSH enrollment, PR follow-up and broader performance qualification remain open. No fresh external PR-status check was performed for this consolidation.

## Built or investigated, still unqualified

**Richer answers and follow-up bubbles:** the local prototype supports up to two bubbles, editable prefill and explicit Submit, with Ask something else for recording. It has bounded metadata separation, an authenticated source/owner-bound typed follow-up route, immutable retry requests and explicit assistant excerpts for longer history. The feature checks passed: 357 runtime/control tests plus 76 subtests, 181 frontend tests, build and changed-file lint. This does not claim repository-wide lint or the broad maintenance/mesh suite is green.

The combined answer/suggestion policies failed semantic checks over 218 completed local development requests. They are not installed, and `llm.experimental_answer_suggestions` stays off by default. Qualify answer depth independently before adding reliable suggestions. Background preparation remains a later experiment. [Prototype and evidence](../../work/complete-answer-suggestions-20260910/README.md).

**Hoya/correction and saving:** exact missing follow-up text was unavailable; the synthetic correction screen did not reproduce the reported anchoring. Missing saved turns remain unexplained. The deployed browser cache/retry improves list usability but does not resolve repeated backend inventory scans, consent/save contention or historical uncertain outcomes. Keep these under the responsiveness and factual-grounding gates. [Investigation](../../work/hoya-followup-20260910/README.md).

**Training studies:** the separate training lab now records compact-memory, curriculum, replay, MiniCPM comparison, evidence-contrast, retained-anchor, evidence-specialist and evidence-selector studies as completed but unqualified for promotion. A selector-format gain is not proof of better complete answers, and Mac timing is not device latency. The existing program ID remains canonical. [Current training roadmap](/Users/l33tdawg/ai-workspaces/lantern-training/reports/ROADMAP.md).

## Current open task map

| Work | SAGE task | Status / remaining boundary |
| --- | --- | --- |
| Responsiveness and model quality | `00b1dbf4-ea56-4f7f-9960-780a0ad5f6f4` | In progress. Owns measured end-to-end Ask latency, runtime/model/training experiments, history-performance investigation and upstream PR follow-up. |
| Complete answers and suggested bubbles | `140f07f5-5fc6-42d2-9e61-6e5796b1aee1` | In progress. Local prototype exists; qualify complete answers, then suggestions, before matched backend/UI rollout. |
| Live transcription and physical voice/touch acceptance | `8b5c213f-9af1-429a-b6d5-8bdc3e436eaf` | In progress. Recording/review UI is deployed. True offline provisional captions, measured ASR quality/latency and current/final-screen acceptance remain. |
| Initial-turn facts and correction grounding | `41822122-f5df-470f-82aa-b25536454a14` | In progress. Preserve user-supplied facts and latest corrections without claiming verified actions; unnecessary disclaimers and Hoya drift remain quality issues. |
| A/B hardware and delivery acceptance | `324b2365-32b6-4e41-bb17-8d991abe0691` | In progress. Four 6.5-inch **portrait** screens are ordered, expected next week per owner. Exact module specifications, enclosure integration and physical acceptance remain. |
| Messages, directory and public sharing | `f66a2503-8da6-48a7-ad50-ebe017e05a45` | In progress. Backend/readiness recovery, compatible UI, approved signed-card refresh, real bidirectional LoRa delivery/read receipts and public-sharing acceptance. |
| Private media/history security | `e28f5711-dc60-4beb-9a0a-b99fd3b80e1f` | In progress. Remaining at-rest protection, migration/recovery and lock/sleep/reboot evidence; not closed by history UI changes. |
| Power recovery and base-image gates | `7a3837c7-19ea-4cb9-b2e1-75b5985dc534` | In progress. Physical sleep/wake/reboot, retained uncertainty recovery and browser/image provenance. |
| B shared-key SSH enrollment | `a1221b10-a1ee-4af8-ac40-9be4b94cb44f` | Planned. A and shared management key/aliases are ready; B needs its existing valid login access. |
| White Pages discovery | `7fed7db3-f0a5-4608-b0b0-dc05b12ffe5f` | Planned. Wider person/household/locality discovery after basic Messages/People delivery. |
| Fleet production hardening | `d8cca61b-aa40-41ee-a90e-410933afd8af` | Planned. Operations, certificates, roles, backups, monitoring and separately reviewed remote commands. |
| Optional online research | `008da7ad-a6b6-4df3-a2e5-4dd03d020b0f` | Planned proposal. Explicit scope/consent and transport design remain; offline use stays independent. |

The hardware parent retains all original unmet audio/PTT, RF/range/relay, display/touch/video/thermal, onboarding, privacy, ordinary public-proposer and January 2027 demo/foundation requirements. The ordered portrait-screen direction supersedes older documents calling orientation undecided. Ordered does not mean received; no enclosure dimensions have been invented.

## Next work within the current UX/performance stream

1. Qualify complete first answers separately from suggestion formatting, with factual, correction, recall, photo and exact-format checks; then use new acceptance cases after candidate selection.
2. Diagnose history read/save contention and reproduce a real correction progression without discarding uncertain records or inferring missing live context from missing saved turns.
3. Measure the normal spoken/typed Ask path through first useful screen text; use those stage timings to select warming/publication/ASR work. Preserve the single canonical performance program.
4. Qualify and deploy suggested bubbles, then assess speculative preparation only if it cannot delay the user's submitted question.
5. Validate voice, editing and layout on the ordered portrait hardware when available. B enrollment and the separate Messages/security/power work remain in their existing streams.

## Superseded tasks and audit

| Old task | Current task | Reason |
| --- | --- | --- |
| `900c154d-7641-48f8-a912-41d73342e827` | `8b5c213f-9af1-429a-b6d5-8bdc3e436eaf` | Old description said recording UI was local only. Deployed milestones are now done; live ASR and acceptance remain open. |
| `d8562ad8-fd9c-4fd0-99a0-90734a5dc489` | `140f07f5-5fc6-42d2-9e61-6e5796b1aee1` | Old description said discussion only and one suggestion. The local two-bubble prototype, failed qualification and remaining rollout gates are now explicit. |

Replacements were committed before the old tasks were marked **dropped as superseded**. Their detailed requirements remain linked. The main program, grounding gate and delivery umbrella keep their existing IDs and are linked to both successors, the done milestone and checkpoint `9c66c7a8-fb0d-440b-b505-255611d31f6b`. The hardware parent also links the owner's confirmed portrait-screen order.

Final SAGE readback verified the two old IDs and the done milestone are absent from the 12 open tasks. Inbox was clear. Only this agent's assigned tasks were reconciled; no messages were sent to other agents or people.
