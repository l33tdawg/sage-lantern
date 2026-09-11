# Private conversation memory lifecycle

Implementation checkpoint: 2026-09-05. This covers the private conversation lane,
not public federation, directory messaging, onboarding, or a secure device lock.

## Lifecycle

- Startup loads local configuration and persisted saving preferences, opens the
  private durable outbox and checks the ordinary agent's identity, owned-domain
  grants and clearance. Missing established preferences fail closed.
- Before an answer, the runtime requests bounded conversation context from the
  local controller. It retrieves relevant and recent committed private records,
  preserves dates and speaker attribution, and excludes suppressed records.
- After a completed answer, when saving remains enabled under the captured
  consent generation, the controller durably queues an interaction and a
  conservative reflection observation. Prior model output is not a verified fact.
- A background worker embeds and submits both through the signed SAGE SDK,
  using explicit Internal classification 1 in the owned personal domain. It
  records receipts and polls semantic commitment separately from transaction
  inclusion. Answer display does not wait for this background work.
- Restart resumes queued work and reconciles uncertain outcomes. Ordinary
  observations do not support task idempotency keys. Missing search results
  never authorize resubmission after an ambiguous network outcome.

These are application lifecycle hooks over real SAGE SDK primitives, not
nonexistent SDK `inception` or `reflect` methods. Reflections currently record
attributed interaction observations and lack of verification; automatic learned
preferences, generated lessons and canonical factual corrections remain separate.

## Storage and privacy

SAGE holds the interaction and reflection records. The local SQLite database is
the durable outbox, receipt journal, history projection and suppression index.
Explicitly captured photos remain local files linked by session ID; their bytes
are not placed into SAGE records. The legacy JSON retains preferences only after
cutover. There is no automatic backfill of legacy history.

Stopping saving preserves existing history but prevents further unsent writes.
Removing a conversation suppresses it immediately from Lantern history and
recall, removes linked local photo media, and asynchronously requests SAGE
deprecation. It does not erase ledger records. Suppression survives restart and
applies even when deprecation is unavailable or an earlier submission is uncertain.
Changing photo preferences or removing one item must not cancel unrelated
durable conversation writes.

The first history page is bounded to the latest 100 items. The local projection
currently has a 10,000-item/64 MiB payload safety bound; it does not silently expire
SAGE conversations after 30 days. Capacity failures are surfaced, not discarded
silently. Pagination and user-facing storage management remain follow-up work.

The outbox and photos are protected by file permissions, **not encrypted by the
SAGE vault**. This release does not provide a secure screen lock or protection
against someone with filesystem access. Do not claim whole-device encryption.
Restoration must preserve preferences, outbox/journal, suppression index, photo
links and the corresponding personal SAGE identity/state together.

## Activation and acceptance

Enable with an absolute `[sage].conversation_dir` only after private identity
approval, local-service checks and initialized history preferences. A new device
must explicitly initialize its preferences; a missing file must not reset an
existing opt-out. Do not auto-unlock a vault or substitute a Root key.

Unit A's operator approved clearing existing local test history at cutover.
Preserve settings, keys and unrelated SAGE records. Keep matched code/config
backups; never restore an older history implementation against the new outbox.

Run runtime/script regression tests, native conversation tests and
`scripts/lantern/verify-conversation-memory.py` with a fresh isolated test
directory. The native verifier uses synthetic records dated three days earlier,
checks store reopening and model use of recalled content, then deprecates its own
test records. This is not a three-day soak or a physical power-loss test.

Physical follow-ups: voice question then subject resumption; photo save/reopen;
stop saving; removal; service restart; real power recovery; delayed commitment;
and no personal disclosure after the future lock implementation.
