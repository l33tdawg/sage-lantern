# Unit A Messages and lock-delay rollout

Deployed September 6, 2026, approximately 10:42 MYT. Physical owner acceptance
remains separate from automated checks. No personal keys, questions, photos or
messages were read or copied during this rollout.

## Lock correction

The installed auth command still specified a fixed 120-second idle timeout.
The owner's saved 300-second setting controlled screen blanking **after lock**,
not the auth expiry. A separate authenticated **Lock after** preference now
controls server-enforced idle expiry: 1, 2, 5, 10 or 15 minutes; default 5.

The separate `lock.json` policy shares protected atomic storage mechanics with
power settings, but has its own revision. Existing screen-off/deep-sleep policy
is unchanged: revision 2, 300/600 seconds. Those delays start after locking.
Saved lock policy overrides the legacy service argument on startup. Changing
the timeout preserves last activity, does not renew presence, and cannot revive
expired tickets/sessions. An uncertain save locks and closes the broker until
restart rather than admitting unlocks against an uncertain live policy.

## Delivered and withheld

- Messages envelope, Inbox/Outbox availability states and memory-only dictation
  with paragraph review/re-recording are deployed.
- Answer-bottom sharing preview/consent UI and Shared knowledge entry are
  deployed. No configured public provider means submission stays unavailable.
- Real send/receive, discovery, encrypted durable drafts, LoRa delivery,
  delivery/read receipts and public acceptance/cache reuse remain release gates.
  No fabricated contacts, delivery results or green acceptance status are used.
- Voice and root suspend helper shipped together: new positive voice-drain
  receipts require all eight checks, including `community_drained`.

## Evidence

- Native installed-runtime overlay: 785 Python tests, 454 subtests pass.
- Native UI: 94 tests, ESLint and production build pass.
- 29 installed source-file SHA-256 values match the frozen deployment manifest.
- Auth, voice, camera, UI, display, sleep controller and model active, zero
  reported restarts at verification. Anonymous community status returns 403;
  invalid-session lock settings returns 401. UI returned locked.
- Boot remains `7fc2a334-26e4-48e8-b585-42e1072c4346`; warm model PID 797
  retained. No system suspend/reboot was requested during deployment.
- Production sleep unit SHA-256 remains
  `58a9b4379a18f537d45dc59ef37bfec77f50dd806beb12d18633d53956de517c`.
  Its `sleep:handle-power-key` inhibitor remains active, preserving the prior
  wake/power-key correction.

Manifest SHA-256:
`243240e891ff654a9f4595708d07c804f22a74a2b8217795da55649b2c2e3557`.
Native UI archive SHA-256:
`271249d9fa33cf499829d5a7de21dc83a4a2cca23dbd8f069446493ca2148e6c`.

## Deployment recovery

Root backup/payload/result directory:
`/var/lib/sage-lantern-community-deploy-20260906`.
One-off reviewed deployer:
`/var/lib/sage-lantern-community-deployer-20260906.py`, SHA-256
`0195752644fda8d6e6a7c071dcda81ebbe293ffadc25f57eff7fc250389abc67`.
Do not rerun this initial-rollout script: it requires absent staging/guard files.

Old auth/voice workers were positively drained under the runtime fence before
replacement; the kiosk launcher was pinned and resumed through its existing
verified lifecycle helper. The model was not restarted.

The five consumer services and idle controller now have
`91-deployment-recovery.conf` with a root-owned deployment guard. A durable
`/var/lib/sage-lantern-deployment.pending` marker blocks startup unless an exact
volatile `/run/sage-lantern-deployment.allowed` permit exists. Reboot drops that
permit. Completed deployment removes both; interrupted deployment must retain
containment and be reviewed, not simply delete markers. These guards and their
recovery process must be carried into the reproducible installer. Actual
power-cut fault injection has not been performed.

## Owner checks next

1. Unlock, open Settings, verify **Lock after: 5 minutes**, and leave untouched.
   It should no longer lock at two minutes. Recheck a changed delay after reboot.
2. Messages → New message: dictate, finish, review and re-record a paragraph.
   Drafts intentionally clear on lock; sending remains disabled.
3. Repeat sleep/wake, then microphone and camera; waking must not enter PIN digits.
4. Continue protected storage/real message and public-provider integration,
   branded boot, complete fresh-install orchestration and recovery acceptance.
   A↔B delivery/relay/replay/sync require both independently provisioned units.
