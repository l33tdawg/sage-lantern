# Offline question correction: source integrated, deployment pending

The owner requests a predictive touch keyboard, not swipe typing. Corrections
must submit the edited question to inference rather than merely changing its
display. Keep normal voice submission immediate; do not force every question
through an extra confirmation screen.

## Required flow

- Offer Edit question from the current question and saved question detail.
- Show the existing text, a large touch keyboard and local word suggestions.
  Preserve ordinary cursor movement, selection, backspace, punctuation and
  physical keyboard input. Suggestions are explicit choices, not silent edits.
- Keep any attached photo and show its thumbnail during editing and resubmission.
- Cancel editing restores the unmodified question. Ask again starts a new session
  with the edited text and photo; never relabel the old answer as its result.
- Permit drafting while inference runs, but do not imply cancellation or submit
  concurrent inference. A future cancel-and-replace flow needs verified worker
  termination and cleanup before admitting replacement work.
- Keep predictions and drafts offline. Clear drafts and suggestions on lock,
  session loss and unmount. Do not persist typing or send it to a keyboard service.

## Original constraints and retained requirements

`scripts/lantern/unit-a-voice-loop.py` VoiceControl rejects ordinary control
commands once processing starts. The original worker captured audio before
calling process, and VoiceOrchestrator.run_audio invoked STT directly. The
integrated explicit text-input path described below avoids synthetic audio while
preserving the normal admission and completion controls.

The implementation must reuse authenticated admission, single-session ownership,
drain handling, request replay protection, image validation, private history and
completion semantics. Text must not be passed in process arguments or logs.
It must not synthesize a WAV or bypass these controls with a direct model call.

## Acceptance evidence needed

- Empty/oversized/malformed text, stale authentication and duplicate submissions
  are rejected safely; retries do not create duplicate inference.
- Corrected text reaches retrieval and inference exactly; STT is skipped only for
  explicit text input and its timing is represented honestly.
- Photo remains attached, including saved-photo reload failures being explicit.
- Lock during editing or inference clears private UI and stops unauthorized work.
- Tests cover cursor/selection editing and suggestion replacement without text loss.
- Physical acceptance on the small display verifies readable suggestions and
  adequately sized keys; deployed package versions are recorded separately.

## Source integration update

### Durable reconciliation follow-up

The reviewed durable correction backend is now merged in commons. Its matching
native/SDK delta remains isolated pending combination with contact issuance.
Canonical tests against that SDK candidate pass 208 tests and 32 subtests.
The UI now reconciles, obtains a session-bound confirmation, and requires a
distinct owner action before a new execution. UI tests pass 153 tests and the
production build passes; mounted review and device acceptance remain pending.
Earlier wheel hashes below describe the previous correction release, not this
new contract. Never deploy the newer UI with the older backend.

**Release blocker:** cross-boot `settled_inventory` currently accepts only
conversation controls, idle inbound state and conversation sessions. Correction
attempts and other messaging/public workflow kinds need explicit schema-aware
maintenance handling before deployment. Do not classify unresolved records as
settled merely to pass startup, or erase them. Historical uncertainty must remain
distinct from verified current producer containment and storage authorization.

### Latest host validation and integration boundary

The explicit reconciliation/confirmation UI passes 153 tests, TypeScript and
production build. Mounted tests verify first-click reconciliation only, separate
execution consent, retained photo context, expiry, lost responses, remount and
token rotation. Clicking a confirmation after token rotation now dismisses it
with a session-changed message rather than silently leaving an unusable button.
This remains host validation, not deployment or physical touchscreen acceptance.

The isolated matched contact/correction candidate at
`/private/tmp/lantern-contact-correction-release-20260908-v1` passed parent-run
104 maintenance tests and both real local SDK/HTTP/encrypted-vault interop tests.
Independent review accepted its documented cold-recovery scope only. Its updated
`signed_inventory()` requires `backend.inspect_inventory()`; existing maintenance
collectors supplying only `SimpleNamespace(current=...)` require a reviewed
adapter before integration. Unsupported messaging/public record variants still
refuse admission. Neither a helper repin nor these passing tests make the
candidate eligible for the current A/B update. Frozen device release inputs
remain unchanged.

### Previous source and wheel checkpoint

The reviewed V2 backend is now integrated: explicit `source_kind` distinguishes
current retained input from protected history lookup, including when both refer
to the same latest photo. The `/correct` route and `voice --question-stdin` share
the normal authenticated worker, retrieval, inference and completion paths.
Canonical backend tests pass: 344 tests plus 130 subtests. This does not prove
deployed service integration or real model acceptance.

Current-question and History editors are capability-gated in the UI. An English
17,306-word offline vocabulary supplies prefix and one-edit suggestions; this is
not a next-word language model or multilingual IME. Attribution ships in About.
The full UI test suite passes 151 tests; type-check and production build passed
again after typed contact filtering and generalized keyboard labels. Independent
mounted keyboard tests cover caret/suffix preservation, selected replacement,
shift, deletion, explicit submission and busy state. These are host tests, not
physical touchscreen acceptance. Unknown submissions are not automatically retried.

The matched correction runtime wheel has SHA-256
`68c2333174334ebc090ad707c72ea7fb0e927a53c178b6f9565ab9a9c72c69db`;
143 tests and 8 subtests passed against its imported modules. It is not part of
the older frozen B update release and has not been deployed.

Pending: matched runtime/UI release packaging, deployment, physical touch
acceptance and end-to-end corrected-photo/current/history/lock tests. Re-entry
protection currently lasts only for the mounted unlocked DeviceScreen; reload
and lock/remount are not durable exactly-once protection. Backend request-ID
replay checks do not make a newly generated request ID the same operation.
Resolve or explicitly constrain this recovery flow before claiming robust retry
acceptance. Other Unit A/B gates remain open.
