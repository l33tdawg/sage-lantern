# Owner contact production route candidate

No device actions, service/socket permission changes or voice edits. Following
the explicit Lovelace handoff, the exact-base route patch is now integrated in
current source. At that integration all eight touched files matched the frozen
candidate in `/private/tmp/lantern-owner-contact-route-review-20260908-v1`
byte-for-byte. The subsequent future-source auth-fanout revision is documented in
`OWNER_CONTACT_ROUTE_PERFORMANCE_20260908.md`; the original closure is unchanged.

Daemon base: `1be13159c359dba19a15554d000d9e5be670349d221236f4acf4759bd97f13c8`.
Auth base: `e2788517113b2e01a57e2f6d5efba6ee350962daf047a39b8c72c6f2ed446e20`.
The parent explicitly released this daemon base; all existing-file base hashes
matched before applying only the route hunks. No underlying inbox/uncertainty
fix was replaced. Future changes still require exact rebase review, not blind
repinning or entire-tree replacement. Reviewed native+SDK/runtime
`contact_issuance` admission and accepted reconciliation remain prerequisites.
Canonical `workflow_store.py` still lacks that kind; route validation uses the
accepted matched-schema overlay, not a claim that the canonical schema release
or any device activation is complete.

Integrated daemon SHA256:
`ee7fa9293a5dc5f984cf426a12d968675ebc0e50eb65b0f70836d7c9d522c857`.
Current future-release auth helper SHA256 (performance revision):
`a276b1a322bdeb1b3e9543232207a0b9d11cbdd44a5be511fb12bfb8bd7b1260`.
Parent/Curie must coordinate this auth helper and its new runtime imports with
A-vision future activation/deployment pins. Existing live or staged activation
pins were not changed: installing source is not evidence that a device ran it.

## Actual path

Browser owner session → existing `unit-a-device-auth.py` AuthHandler and DeviceLock
→ `OwnerContactForwarder` → fixed root Unix socket → MeshDaemon explicit branch
→ `OwnerContactRoute` → ordinary signer, real WorkflowStore and reviewed issuance/
reconciliation handlers. No voice proxy, provider initialization or radio calls.

POST routes (all retain existing Host/Origin/X-Lantern-Auth controls, exact JSON,
512-byte body bound, token authentication and no-store responses):

| Route | Fields in addition to `token` |
| --- | --- |
| `/contacts/preview` | `ttl_seconds`, `findable`, `messaging_allowed` |
| `/contacts/confirm` | `challenge`, `plan_sha256`, `consent:true` |
| `/contacts/cancel` | none |
| `/contacts/state` | none |
| `/contacts/reconcile/preview` | `action`: abandon/export/acknowledge |
| `/contacts/reconcile/confirm` | `challenge`, `plan_sha256`, `consent:true` |
| `/contacts/reconcile/cancel` | none |

Caller-supplied root operations, broker IDs, paths, config, keys, profiles and epochs
are not accepted as HTTP fields. The auth broker supplies its current epoch; root
derives the full broker/config/owner/radio binding. Confirm keeps the reviewed exact
plan/challenge semantics. Cancellation clears both pending owner windows without
rolling back committed/uncertain work. Cancellation racing an in-flight serialized
operation can return unavailable; it is not a promise that an already dispatched
write was undone. Explicit reconciliation remains necessary after response loss.

## Authority and lifetime

- Auth validates the actual DeviceLock capability before forwarding, on every
  guard exchange and before returning data. No PIN or token extraction occurs.
- Root socket peer must be the positive configured service UID (`voice_uid`, the
  current auth service's shared `sage-lantern` UID), not UID0. This is NOT a claim
  of auth-process PID attestation. UID alone is insufficient: root independently
  rechecks the exact token, current epoch and profile through the helper-only
  `/owner-contact-snapshot` after each guard acknowledgement, with the enabled
  lock marker checked on both sides of that request.
- The root endpoint of the fixed socket is kernel-UID0 verified by the forwarding
  connector. No socket mode changes are introduced. Root then invokes its internal
  handlers as root only after the above owner checks; naked root is not consent.
- Pending windows bind the token fingerprint, epoch, broker and current resource/
  provider lifetime. Another valid session cannot confirm an earlier session's
  preview. Raw tokens remain only request-local, not persisted in ledger, approval,
  instance fields or responses. Tokens traverse only local auth HTTP/Unix IPC.
- The existing provider must already be initialized and active. This route never
  calls ready, resumes uncertain work, bypasses first commissioning or enables
  messaging/receipt workers. It holds the existing resource mutex and rechecks
  active state, config, owner profile, provider identity and epoch throughout.
- Forwarding is single-flight per auth process; root adapter is single-flight.
  Socket reads are bounded, guards are capped, and both sides have a 45-second
  total window with five-second socket waits. Failures clear pending challenges;
  no automatic retries or successful empty fallback exist. After a dispatched
  failure, HTTP reports `contact_unavailable_reconcile_if_uncertain`, never an
  assertion that no write happened.

## Profile compatibility

Production inspection found both the persisted DeviceProfile reader and auth
response validator required `public_discoverable is False`. The held patch changes
only those validations to exact bool so already-public owners can privately pair.
No preference setter/default changes; initial profiles remain private and rename
preserves existing preference. Public-profile revision/value remain approval-bound.
This is a separately visible reviewed delta, not an automatic visibility change.

## Tests and remaining gates

The closure exercises the actual patched AuthHandler, real DeviceLock sessions,
framed socketpair forwarding, actual patched MeshDaemon dispatch and real SDK/
WorkflowStore handlers with a synthetic HTTP storage transport. A separate test
uses an actual ephemeral HTTP auth server for root owner-snapshot checks (only
endpoint and lock-marker observation are injected). Tests cover CSRF/Host/Origin,
exact fields, root/wrong UID, wrong/new session, lock before/after reservation,
epoch/config/broker changes, cancel, public/private profiles, uninitialized provider,
unknown-reservation reconciliation and no card leakage or radio initialization.

Socketpair tests inject the kernel UID argument; this macOS run is NOT Linux
SO_PEERCRED/systemd/sandbox acceptance. Native encrypted HTTP interop remains the
separately accepted schema/reconciliation evidence, not this mock-storage route
test. Owner UI, Linux matched installation/permissions, real latency under repeated
guards, daemon handoff and physical pairing/adoption remain explicit next gates.
