# Current private MeshCore activation path

Source-only checkpoint, September 8. No device actions, enrollment, keys,
provider enablement or RF acceptance. This supersedes the early activation
list in UNIT_A_SECURE_MESSAGING_20260906.md, not its unknown-work limitations.

## Already wired

- `scripts/lantern/unit-a-voice-loop.py` supplies `MeshVoiceFacade` to the real
  voice loop. It does not create a second serial owner.
- `mesh_daemon.HardwareRuntime.provider` constructs the provider under the
  root supervisor's storage lifecycle, reconciles journals and exclusively
  opens the pinned radio. `resume_background` honors root configured messaging
  and dispatch consent; both are distinct from permission to initially open USB.
- `mesh_runtime._dispatch_pending` dispatches durable queued messages and
  authorized receipts. Unknown dispatch stays uncertain, never automatically
  resubmitted. Firmware ACK is not application delivery.
- `mesh_read_root.RootReadReceipts` is installed by the daemon. Read receipts
  require the session-bound render challenge plus explicit share-read-receipt
  consent, then authorized background dispatch. Merely opening/fetching does
  not authorize a read assertion. The older facade docstring saying all read
  receipts remain disabled describes its base policy without this root wrapper.

## Concrete missing wiring and narrow correction

The root runtime previously had no import route for private peer self-consent
cards. `MeshRuntime.put_contact` existed, but tests populated it through a
synthetic context before constructing the production provider. A configured
peer triple or firmware contact alone cannot populate `contacts/list`.

New `MeshDaemon.handle` operation `import-contact` is accepted only from the
kernel-authenticated root peer. `HardwareRuntime.import_contact` requires:

- exact current broker ID and nonempty, live unlocked access epoch;
- SHA256 of canonical installed config, and SHA256 of exact signed card bytes;
- valid fresh self-signature and an exact chain/agent/radio tuple in the
  independently approved installed peer policy;
- already initialized provider, active supervisor and unchanged configuration;
- a bounded volatile contact-update grant with epoch/lifetime checks on the
  serial worker and guarded encrypted workflow CAS persistence.

It does not initialize the provider, change config, enroll an actor, sign a card,
enable any worker, send a message or queue a receipt. Existing coordinator
inventory queries may run; this is not a promise of zero serial IO. Revision
and revocation checks remain in MeshContactRegistry. Signed false consent is
accepted as a revocation, not turned into true consent. An unavailable response
does not prove no write: retain evidence and explicitly reconcile the exact
card/registry state, never automatically retry an uncertain operation.

## Required integration order on each independently provisioned unit

1. Verify current ordinary local actor enrollment, private chain/genesis and
   signed encrypted storage availability. Independently verify the remote
   chain/agent/full radio public key and owner permission before adding its
   tuple to root policy. A self-signed card is not enrollment evidence.
2. Review both units' radio inventory, firmware/protocol, existing full-key
   firmware contact and compatible RF settings. A prefix alone is insufficient;
   runtime resolution still requires an unambiguous inventory match. No
   firmware-contact mutation or peer enrollment helper is supplied here.
3. Use coordinated maintenance to install matched runtime/config/startup and
   audio bindings. Do not copy a module into a running venv. Retain existing
   guards until the established bootstrap/completion gates pass. Review
   `messaging_enabled` and `dispatch_enabled` together: dispatch scheduling
   needs messaging enabled. Leave both while-locked flags false unless separately
   approved. Changing config invalidates lifetime/policy bindings; no live edit.
4. Each recipient owner explicitly signs its own card with the existing ordinary
   identity via `mesh_contacts.sign_contact_card`, exact chain/full radio key,
   monotonically reviewed revision, expiry and consent. No new identity and no
   off-device seed export. Cards expire within at most 86400 seconds (24 h, the
   owner's message-approval window; the issuing owner still chooses the TTL):
   renewal is explicit and remains an operational prerequisite, not automatic
   discovery.
5. After verified bootstrap and owner unlock, initialize the existing provider
   through normal authenticated status. Import each peer's signed card through
   the root socket route below. An empty peer list, absent card, expired consent
   or missing firmware match must remain unavailable, not synthesize a contact.
6. Use normal authenticated `contacts/list`, explicit-consent `messages/send`,
   inbox fetch and actual rendered acknowledgement on the other unit. Observe
   correlated `peer_read_asserted` only from an authentic return envelope.
   Keep unknown work and storage-epoch mismatch fail-closed.

The new request schema is exact (no extra fields):

```json
{
  "op": "import-contact",
  "broker_id": "CURRENT_BROKER_ID",
  "epoch": "CURRENT_UNLOCKED_32_HEX_EPOCH",
  "config_sha256": "SHA256_OF_CANONICAL_CONFIG",
  "card_sha256": "SHA256_OF_EXACT_UTF8_SIGNED_CARD",
  "card": "EXACT_SIGNED_CARD_JSON_STRING"
}
```

Config canonicalization is `json.dumps(config, sort_keys=True,
separators=(",", ":")).encode()` (same as receive-consent revision), not the
pretty-printed file hash. Keep the installed file pin separately in the operator
plan. Use the existing `mesh_voice_ipc.SOCKET`, `send` and `receive` framing from
a root-owned reviewed local caller; response contains only card digest, revision
and consent flags. There is deliberately no browser import method and no generic
root-file/key-loading CLI in this patch.

## Remaining explicit gaps

- No owner-facing private card issuance/exchange/renewal orchestration or
  firmware contact installation is supplied. Parent must obtain the verified
  public identities and consent before invoking these source capabilities.
- Follow-on delivery source now supplies a separately consented
  queue-after-durable-inbox policy; see MESH_DELIVERY_RECEIPTS_20260908.md.
  Root configuration forwarding is now wired, default off; matched installation
  and explicit owner opt-in remain required. Do not equate an RF ACK with application delivery or a delivery assertion
  with reading.
- Public proposer/gateway enrollment is a separate feature, not a prerequisite
  for private A–B encrypted MeshCore messages. Same-node canonical sends are not
  the cross-private-chain transport.
- This source does not prove A or B has these pins/cards/settings installed or
  that RF delivery works. Deployment remains with the existing live owners.
