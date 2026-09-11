# Unit A community workflows — implementation checkpoint

Status: **Messages UI, supervised dictation and provider-disabled sharing deployed
to Unit A on September 6, approximately 10:42 MYT**. Real message/public sending
remains unavailable until the activation prerequisites below are implemented.
Deployment and lock-delay correction: `UNIT_A_COMMUNITY_LOCK_20260906.md`.
Do not equate these tests with physical dictation, LoRa delivery or public
publication acceptance.

## Touchscreen

- Envelope shortcut opens Messages, with Inbox/Outbox and a voice-first draft.
- Dictation records at most 30 seconds, then shows the transcript for explicit
  acceptance. Paragraph replacement preserves other paragraphs and supports
  undo. There is no keyboard requirement or LLM rewrite.
- Recipient selection uses exact verified identities. Spoken-name filtering is
  limited to loaded, operator-approved contacts; it is not a global directory
  search or proof that someone is online. Duplicate names require selection.
- Drafts live only in memory and clear on lock. Uncertain sends retain the draft
  and request identity while browsing Outbox; another send is not offered.
  Explicit discard does not claim to cancel a transmitted message.
- Completed answers have **Share this with others** at the bottom of the answer
  reader, without adding another crowded permanent footer row. Public preview
  shows only the selected question/answer, marks generated content, warns that
  public copies cannot be recalled, and requires an explicit privacy check and
  **Submit publicly** confirmation. No photo bytes or metadata are exported.
- Menu → Shared knowledge lists submission state through the protected archive
  provider. Queued/unsent is neutral; pending review is amber; verified online
  acceptance is green. Every state also has text. Challenged, deprecated,
  rejected and unknown are distinct; none imply private-record deletion.
- Menu icons and the stacked padlock / Device locked / Tap to wake presentation
  are included in the deployed bundle; owner touchscreen acceptance is pending.

## Backend boundary

`community_service.py` runs inside the existing voice HTTP service at port 3197;
there is no new detached producer or daemon. Eight `/community/` POST routes
provide status, message list/open/send, contacts, share preview/submit/list.
The frontend contract is `apps/lantern-fleet-admin/lib/community.ts`.

All routes require the original device session even in legacy unlocked mode,
exact allowlisted Host/Origin/control headers, strict bounded JSON, stable
session-hash and lock-epoch ownership, and authorization before and after work.
Bodies are limited to 32768 bytes, responses to 262144, lists to 20 items.
No request selects a backend URL, credential, encryption key or trust policy.

Message recording uses `/start` with `purpose: "message"`, existing leased
stop/cancel/keepalive controls, and `/message/status`. STT child processes remain
supervised and are drained with voice work. Transcripts are memory-only, expire
after 120 seconds, and do not enter answer/history/reflection/status-file paths.
Completed-Q/A sharing sources are bounded, authenticated in-memory snapshots,
not reads of ambient history. They clear on lock, clear and new capture.

The root suspend receipt now requires **community_drained** as an eighth check.
Deploy the voice helper, root sleep helper and dependencies together. Installing
only the voice change against the older exact receipt validator prevents safe
suspend completion. The staging builder now includes 33 files, adding the six
community/message dependencies and revisioned power/lock settings; it still
stages only, never deploys by itself.

## Real SDK adapters, not delivery claims

`community_messaging.py` calls actual SAGE passive pipe-history APIs and canonical
same-node `message_send`. Browsing never claims a message or marks it read.
Canonical sends appear in passive history through SAGE's pipeline projection.
Successful send means accepted by the node, **not** recipient delivery/read.
Request UUIDs are retained unchanged at the SDK boundary; protected binding
rejects altered or unresolved cross-session/restart retries.

`community_public.py` constructs a separate PUBLIC generated/unverified envelope
using a scoped, active ordinary public member—not private Root. It reserves
before dispatch, retains uncertain submissions, and performs an independent
exact-memory read for status. An operator-provided trusted-node verifier must
bind endpoint, chain, authenticated transport and reviewed canonical disclosure
implementation before online acceptance can be displayed. Content, hash,
classification, author and domain must match. This is **trusted-node online
observation**, not an offline quorum proof, factual verification or permission
to use an answer as a zero-LLM cache hit.

Public readiness additionally requires an encrypted durable archive exposing
`record_public` and `list_public(limit=21)`. Session-local previews are never
presented as complete publication history. Tests exercise lock/new-session/
restart listing through an injected protected archive, not a deployed codec.

## Still required before enabling sending

1. Bind actual ordinary device identities and authenticated home/public node
   endpoints to trusted configuration; establish participant findability consent
   and recipient policy. Active directory registration alone is not consent.
2. Verify vault encryption for the actual SAGE pipeline store and bind that
   evidence to the running node generation. No manually asserted boolean, file
   existence or Lantern unlock token establishes encrypted storage.
3. Implement/review the protected request journal and public archive, including
   bounded callbacks, uncertain-send reconciliation and restart recovery. The
   generic vault REST/SDK interface does not expose arbitrary encrypt/decrypt.
   Do not export and retain the recovery data key as a shortcut.
4. Supply trusted online public-node readiness and canonical-status verification;
   enroll a dedicated bounded public proposer only under provisioning gates.
5. Implement actual MeshCore/LoRa transport, relay, fragmentation budgets and
   SAGE federation backhaul. The current adapter supports same-node SAGE only.
   No internet fallback or full photo transmission is implemented.
6. Integrate genuine delivered/read receipts and user read-receipt privacy
   controls. Current UI does not emit read acknowledgements or infer Read from
   browsing, claiming, workflow completion or transport acknowledgement.
7. Implement private → locally verified shared knowledge → model retrieval,
   challenge/expiry invalidation and zero-LLM accepted-cache reuse separately.

Default construction supplies no messaging/public providers, so both send
capabilities fail closed. Dictation capability comes from the active supervised
voice loop. UI does not display false empty inboxes or false success when a
provider is unavailable. There is no shipped activation factory yet.

### Separate privacy finding

The existing conversation outbox uses a permission-restricted **plaintext**
SQLite payload store. SAGE ledger vault encryption does not encrypt this separate
Lantern store. Do not reuse it for protected message/public drafts or claim the
whole local data footprint is encrypted. Migration and recovery policy belong
to the private-device release gates; this change does not rewrite user history.

## Validation

- 764 Python tests and 443 subtests pass across community, voice, auth, access,
  lock, suspend, inference and staging regression suites. The two localhost HTTP
  tests required execution outside the port-binding sandbox; they then passed.
- 86 UI tests pass; full ESLint and local-mode production build pass.
- Standalone TypeScript checking reports existing errors in live enrollment and
  observation-contract files, not the changed community/device files. They were
  left untouched.
- Independent reviews covered session isolation, pre-dispatch reservation,
  cancellation, stale completion, uncertain-send retention, archive persistence
  contracts and paired suspend receipt changes. Provider-disabled defaults were
  reviewed; unconfigured storage/transport implementations were not certified.
- No live public record, private message, enrollment, new key or radio packet
  was created. No physical/browser interaction test is claimed.

SAGE task: `18011c4a-b476-4a3d-9be4-2e4659ee7443`, linked to the Unit A master.
Keep it in progress until genuine deployment/activation and A↔B acceptance.
