# Existing MeshCore contacts: bounded read-only foundation

No live ports, RF transmissions, adoption, add/import/update command or deployment.
The parent-approved driver hook is now implemented in source.
This inventories contacts already in companion memory;
it does not discover an unknown nearby radio or enroll a SAGE recipient.

## Pinned primary protocol

Firmware revision: `d92964352441e53b93e8667b802e04f6e072b39e`.
[Command and response implementation](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L1103):
command `04` without a `since` argument; response `02` plus LE32 total count;
zero or more `03` contacts; terminator `04` plus LE32 maximum emitted lastmod.
The iterator filters `lastmod > 0` even for a full request. It is not a
transactional snapshot. Count is total, not filtered count.

[Contact response layout](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L155)
is exactly 148 bytes: code, public key32, type1, flags1, path descriptor1,
path64, name32, advert timestamp4, latitude4, longitude4, lastmod4.
[Size constants](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/src/MeshCore.h#L7)
pin the 32-byte key and 64-byte path. Only key/type/flags/lastmod are retained;
names, coordinates and paths are discarded. Device timestamps are observations,
not trusted freshness or consent evidence.

## Implemented driver API

`MeshCoreClient.get_contacts(*, authorize) -> ContactInventory` in
`software/runtime/src/sage_lantern/meshcore_transport.py` now follows this contract:

1. Enter the existing client's `_run` mutex/poison/deadline wrapper.
2. Require that client's successful pinned handshake; check live authorization.
3. Delegate to `meshcore_contacts.get_contacts(exchange=self._exchange,
   read_frame=self._frame, deadline=deadline, authorize=authorize)`.
4. Let every failure escape into `_run` so unread/stale replies poison the
   connection. Never open a second serial reader or silently retry.

The callback helper and public driver method are implemented in source, not deployed.
Callers must not invoke private methods concurrently or use the helper as a new
standalone serial client. Tests call the actual public driver method against a
synthetic partial-I/O stream, including handshake, busy and poisoned-state gates.
One inherited deadline covers the transaction; callbacks must enforce it. No
timeout refresh per row. Authorization is checked before command, before each
subsequent frame, and before returning. Interrupted scans yield no partial data.

`ContactInventoryDecoder.feed(frame)` accepts decoded payloads and returns true
only on valid termination. `finish()` returns the complete observed inventory.
Malformed order/size, duplicate keys, count mismatch, zero lastmod, oversized
inventory (>256), incorrect watermark or contact-change pushes invalidate it.
Up to 32 unrelated push frames may be discarded after the start response; the
driver's existing exchange imposes its separate pre-start push bound. No message
dequeue command is sent. Any abandoned stream must be fenced before reuse.

`inventory.unique_prefix(full_public_key)` requires exact full-key membership
and a unique six-byte prefix among all observed contacts. `resolve_prefix(bytes6)`
returns a unique observed radio contact or fails. It is not a SAGE identity
resolver. Collisions are never broken by name, path, list order or signal level.
Full keys must be valid Ed25519 points; no short address can stand in for one.

## A/B preparation and remaining gates

- An absent peer remains unavailable. The optional add-contact path is omitted:
  explicit authenticated binding, authorization, local confirmation and uncertain
  mutation handling need agreement before any command is implemented.
- Existing contact presence is not findable/messaging consent, authenticated
  ownership of a radio or SAGE enrollment. Independently verify the signed card,
  current actor policy and radio possession. Unknown names remain untrusted.
- Scan completion/count agreement cannot prove atomic firmware state: radio
  updates can race, including changes with unchanged count. Detected contact
  pushes fail closed; absence of a push is not an immutable inventory lease.
  Do not advertise this snapshot as collision-free authority for a later send.
  The integration must fence competing host operations; even one host mutex does
  not stop firmware radio updates. Exact-recipient E2E envelopes and genuine
  recipient-signed, exactly message-bound application receipts are the separate
  confidentiality/delivery boundaries: a misroute must not disclose plaintext to
  another radio owner or become false application delivery. A radio ACK alone
  cannot satisfy those boundaries. No guaranteed radio-address lease is claimed.
- Default scan cap is a host safety bound, not a claim of firmware capacity.
  Tables above 256 or filtered/incomplete rows are refused, not truncated into a
  falsely unique prefix map. No fallback prefix send or automatic adoption.
- No roster persistence, discovery UI, add-contact or first A/B RF exchange is
  activated by this module. Root service ownership and protected contact handling
  remain integration responsibilities.
