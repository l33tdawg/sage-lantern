# A–B radio commissioning gates

## September 8 read-only evidence

A's observed mesh configuration disables messaging and dispatch, has zero peers
and no public mesh/gateway. Its active broker had no radio file descriptor.
B's observed configuration likewise disables messaging, dispatch and delivery
receipts and has zero peers. B's broker held the configured radio serial device,
but an open descriptor is not proof of handshake, reception or delivery.

B's read-only audit completed with SSH exit 0 (handle 5343). Its historical
commissioning journal records MeshCore v1.17.1-d929643, protocol 13, 917.375 MHz,
62.5 kHz bandwidth, SF8, CR5 and 10 dBm. Those are historical readback values,
not a fresh RF profile verification. The deployed UI explicitly lacks live
neighbor telemetry: its dash must not be interpreted as zero measured neighbors.

Neither snapshot establishes current availability after a lock, reboot or update.

## Required commissioning sequence

- [ ] Revalidate both device/service lifetimes and owner authorization before
  changing configuration. Preserve independent device and radio identities.
- [ ] Verify current firmware and matching RF profiles through the existing
  exclusive lifecycle/radio owner. Never open a competing serial reader.
- [ ] Install authenticated peer bindings and explicitly enabled messaging and
  dispatch using a reviewed guarded commissioning path, not raw boolean toggles.
- [ ] Send one short private message A to B; prove intended-recipient decryption
  and application delivery. Repeat B to A.
- [ ] Prove delivery receipt and explicit read receipt as distinct states. An RF
  ACK alone is neither application delivery nor evidence the person read it.
- [ ] Test duplicate/replayed envelopes, wrong recipient, interruption and retry;
  verify private content remains inaccessible while locked according to policy.
- [ ] Verify reboot recovery and retained addressing without sharing private keys.
- [ ] Add broker-owned neighbor/last-heard observations with timestamps and
  provenance. Distinguish saved contacts, fresh reception and verified identity.

## Confirmed source gaps before RF testing

### September 8 evening source checkpoint

Broker-owned profile readback, explicit firmware contact provisioning, the root
operator wrapper and passive status route are now integrated into canonical
source after independent review. Deployment mappings and exact journal drop-in
validation are also integrated. Focused canonical suites passed 220 tests plus
34 subtests and 146 tests plus 127 subtests respectively. These are source
milestones, not completed commissioning or device acceptance checkboxes.

The local status UI labels observations as recently processed traffic, never
authenticated neighbors. Fifteen mounted mock cases cover lock/session changes,
visibility, expiry and polling cadence; the UI production build passes. Firmware
queue dequeue time is not RF reception time. Current devices remain on their
previous releases; no actual A–B delivery is established by this work.

A combined radio/owner-ledger runtime and its exact admitted wheel hash are being
prepared. Existing-config transition, guarded device deployment, firmware flash
persistence and every RF acceptance step below remain outstanding.

The September 8 source review found no complete guarded A–B commissioning
command. Two independent contact stores must be populated: the signed application
registry and the radio firmware contact table. Broker `import-contact` already
imports into the former but requires the peer in the configured peer set.
Outbound dispatch also requires the recipient radio key in firmware contacts
with an unambiguous six-byte prefix. Neither an imported application card nor a
messaging policy toggle satisfies both requirements.

- [ ] Implement broker-owned RF-profile verification and approved firmware
  contact provisioning/readback through its existing exclusive serial owner.
  Preserve uncertain outcomes rather than automatically retrying writes.
- [ ] Implement an existing-config transition with exact old/new pins, signed
  peer cards, producer containment and new commissioning/startup bindings.
  The fresh installer deliberately refuses existing config; do not weaken it.
- [ ] Add the operator wrapper for authenticated broker `import-contact`, bound
  to the current broker, access epoch, config digest and signed card digest.

The standalone B radio commissioning script opens serial itself and is not safe
to run alongside the broker. Keep locked receive/dispatch disabled unless
separately approved. These missing paths are implementation work, not evidence
that either device has exchanged a message.

## Separate acceptance evidence

Relay/range/bandwidth, disconnected store-and-forward, directory discovery and
public knowledge proposal/validation/sync each need their own evidence. A direct
two-device message does not establish those features or Byzantine consensus.
White-pages and visual discovery requirements are in `UNIFIED_DISCOVER.md`.
