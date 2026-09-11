# Bounded prompt replay for active text conversations

Status: rejected for deployment after the first on-device screen. Prototype and tests are preserved under `work/model-trial-20260910/cpu-latency`; active source no longer contains replay.

The current model request includes retrieved context in the user's current
message. Active history later reconstructs that message as the bare question.
This changes the reusable prefix and can discard recalled facts when the model
only acknowledged them. The proposal retains the exact text user message for
successful active turns. It does not persist model KV state or retrieved context.

The runtime opts into a private child result field via `--prompt-replay --events`.
The field is text-only, at most 2,048 UTF-8 bytes, and must preserve the current
question and existing context wrapper. Images and oversized inputs have no replay
field. Failed, cancelled, truncated or unconfirmed requests cannot publish it.
The parent removes it before history storage and passes it separately from UI
state values. It never enters the public-sharing source or saved conversation
record; reopening saved history uses ordinary question/answer turns.

The existing owner, session and revision checks govern admission. Lock, reset,
owner changes and stale results retain their existing rejection/clear behavior.
Extra prompt text is held only for the last four exchanges. The model's existing
2,048-byte conversation budget remains fixed: first choose the same contiguous
raw-history suffix as before, then substitute replay text only while it fits.
Never remove a correction or another raw turn to create room for a cached prompt.
Earlier photos keep the existing unavailable-photo note.

Old recall remains an untrusted historical claim. Fresh retrieval is always
included, and the existing policy still requires uncertainty/conflict handling
and application of the latest correction. Benchmark changed-recall, injection,
reset, and owner-change cases before activation. Full cache reuse is not promised:
history eviction, tokenization, output whitespace, changing context and hybrid
model checkpoint availability can reduce it.

The only new inference measurement request is `stream_options.include_usage`.
Existing numeric-only projections record cache counts without prompt text.
The separate shorter-system-prompt experiment passed its repeated gate without replay and is installed on A. It does not activate replay or change checkpoint settings.

Source changes require a matched runtime/voice-helper installation, with normal
maintenance ownership and updated startup source bindings. A runs the compact text policy with ordinary history. The replay prototype
remains uninstalled.

Subsequent conversation-recall compaction is qualified separately and installed:
see [results](../../work/model-trial-20260910/context-latency/CONVERSATION-ONLY-RESULTS.md).
It only changes the prompt view of already-selected conversation recall. It does
not retain old request wrappers or activate this replay/checkpoint proposal.

## First device result

Preserving old recall fixed the Willow Clinic destination in one correction
follow-up, but made first text slower (6.33 s versus 4.85 s). Both variants reused
only the 226-token system prefix. The record-injection follow-up regressed from
correct D4 with current history to an unknown drawer with replay. The same replay
failure appeared with caching disabled, so this is not solely a cache-state issue.
Fresh checkpoints are a separate diagnostic; speed alone cannot qualify replay.
