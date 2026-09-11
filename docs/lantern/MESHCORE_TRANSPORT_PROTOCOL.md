# Pinned MeshCore host foundation

Staged only. No installation, radio IO, SAGE binding or encryption activation.

Unit A evidence pins `v1.17.1-d929643`, asset SHA-256
`67ef59c7687787255c23659e7525b81c1fc88bf679521f4183099a50580d0a3d`:
`evidence/unit-a-radio/meshcore-radio-public.json:13`.
The official source revision resolves to
`d92964352441e53b93e8667b802e04f6e072b39e`.

## Source and implemented wire contract

- [Serial implementation](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/src/helpers/ArduinoSerialInterface.cpp#L24): host frames start with `<`, device frames with `>`, then unsigned little-endian 16-bit payload length. No checksum/transaction ID. Reject malformed framing rather than silently resynchronizing.
- [Frame bound](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/src/helpers/BaseSerialInterface.h#L5): 176 bytes. The host rejects oversize instead of relying on firmware truncation.
- [USB initialization](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/main.cpp#L110): 115200 baud. The FD wrapper requires an already configured nonblocking raw 115200 8N1 descriptor; it never opens ports or toggles modem lines.
- [Commands](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L944): query `22,13`, response 13; app-start 1 with seven reserved bytes, response 5; text-send 2, plain type 0, attempt 0, timestamp LE32, six-byte contact prefix, UTF-8 bytes. Response 6 carries route flag, expected ACK and estimated timeout. Query also returns BLE PIN bytes: discarded, never exposed. Self-info location/name are likewise not exposed.
- [Message construction](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/src/helpers/BaseChatMesh.cpp#L420): expected ACK is the first four SHA-256 bytes of timestamp, attempt/type byte, text and sender public key. Host compares this before acknowledging serial acceptance. Initial host policy caps text at 128 UTF-8 bytes, rejects NUL, and never fragments.
- [ACK event](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L414): event `0x82`, four ACK bytes and LE32 trip time. A matching event is only `radio_ack_observed`, never application delivery/read evidence.

## API and integration obligations

`MeshCoreClient(stream, expected_public_key=bytes32, timeout=3, clock=...)`;
`handshake()` verifies protocol 13, exact firmware version and configured radio key.
`send_text(recipient_prefix=bytes6, text=..., timestamp=uint32, authorize=...)`
is explicit; `observe_ack(receipt)` passively waits once. Construction sends nothing.
`SerialFDStream(fd)` implements the stream interface with stdlib select/read/write.
Injected doubles must honor each remaining-deadline timeout. Partial IO and
unsolicited traffic are bounded; no background threads, log bodies or auto-retry.

The operator must establish fresh exclusive serial ownership with no stale
command replies. Protocol replies are not generally nonce-bound. Any uncertain
IO poisons the client; a new client does not authorize repeating uncertain sends.
The caller owns durable dispatch intent, recipient consent, timestamp uniqueness,
contact-prefix collision checks and lock/suspend lifecycle. Only a pre-existing
contact can be addressed; the driver never advertises, enrolls or modifies contacts.
Firmware may choose flooding when no direct path exists. No RF airtime/current
measurement, maximum reliable range, or power/sleep control is claimed.

Text is plaintext on host USB and inside companion firmware; firmware radio
cryptography is not SAGE recipient-only end-to-end encryption. Short ACK hashes
are not signed receipts. Unrelated push payloads are discarded within bounds,
and early ACKs may consequently be missed conservatively. The client is not a
complete A-to-B private inbox.
Synthetic tests prove framing/state behavior only, not physical firmware/RF
behavior. Public identity response matching is not firmware remote attestation.

## Explicit incoming retrieval

`receive_next(authorize=..., persist=...)` sends exactly one command 10, never a
drain loop. Response 10 alone means empty. Timeout is unknown, not empty.
It supports queued contact responses 7/16 and channel responses 8/17; old
layouts are necessary because frames already in firmware's queue retain their
original format. Version-3 layouts add signed quarter-dB SNR and two reserved
zero bytes. Contact metadata contains a six-byte sender prefix, path descriptor,
type and LE32 timestamp; channel metadata substitutes a one-byte channel index.
Payload remains opaque bytes, including any subtype-specific prefix. No UTF-8
decoding, command execution, SAGE identity assertion or signature validation.

Queued binary-channel response 27 is also preserved, not discarded. Its exact
nine-byte header is code, signed SNR, two zero reserved bytes, channel index,
path descriptor, LE16 data type, and one-byte payload length. Declared length
must equal actual remaining bytes (0 through 167). `kind="channel_binary"`
exposes `data_type`, opaque `payload` and `raw_frame`; `timestamp`, `text_type`
and `sender_prefix` are None because this layout contains none. The same
protected persistence and final authorization gates apply, including zero-length
payloads. Source: [binary receive builder](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L592).

Source: [receive layouts](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L432),
[channel layout](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L545),
[dequeue](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L243),
[command handler](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L1368).

IMPORTANT: retrieval removes the device queue item before its serial response;
firmware also updates its local unread display. Neither means the user read it.
No additional read/delivery ACK is emitted by this driver. Firmware radio ACKs
are independent of application reading. USB queue transfer is not crash-atomic:
host death or cable loss can lose a dequeued item. No exactly-once promise.

The trusted bounded `persist(IncomingMessage)->True` callback must protect and
durably retain admitted raw bytes before return, even after lock revocation.
Authorization is checked before dequeue and after persistence. Storage failure,
malformed replies and uncertain IO poison the client without another dequeue.
The caller must supply encrypted storage and admission journaling; neither a
radio prefix nor firmware encryption substitutes for a verified protected
application envelope. All results remain untrusted and repr hides payloads.
The firmware queue is finite and may drop messages before retrieval. The host
does not interpret its empty response as evidence of complete prior delivery.
