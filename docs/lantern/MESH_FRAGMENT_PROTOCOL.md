# Lantern mesh fragments, version 1

Source-only codec; not a running mesh service or proof of delivery.

Each companion direct-text payload is ASCII `SL1:` followed by canonical padded
base64. Decoded bytes contain a 40-byte network-byte-order header and 1–48 data
bytes: SHA-256 of the entire opaque envelope (32 bytes), zero-based fragment
index (uint16), fragment count (uint16), total envelope length (uint32).
The count is exactly ceiling(length / 48). All but the last fragment contain
48 bytes; the last contains the exact remainder. Maximum envelope size is
4,096 bytes, hence at most 86 fragments. Every encoded frame fits within the
driver's 128-byte text cap. No photographs or plaintext private questions belong
in these frames. Larger content requires a different transport, not truncation.

The reassembler accepts a previously resolved full 32-byte radio sender key;
six-byte contact prefixes must not be treated as unique identities. Assemblies
are keyed by that sender and full envelope digest. Out-of-order frames and exact
duplicates are supported. Conflicting headers/data or a final hash mismatch
reject that assembly. The digest detects corruption; it does not authenticate
the sender or content. The assembled object still requires signature,
recipient, consent, expiry and durable UUID replay verification.

Resource limits are eight active assemblies, two active per radio sender,
eight active/terminal per sender and 64 total active/terminal entries. A bounded
32-peer ingress table grants each sender a 172-frame burst, refilling that burst
over 60 seconds; invalid frames and duplicates also consume allowance. Resolved
contact admission upstream remains necessary against many-key flooding.
An assembly expires 900 seconds after its first frame;
duplicates do not extend its lifetime. Completed/conflicting identities remain
suppressed until that same expiry. Saturation rejects new work without silently
evicting a live assembly. Backwards/nonfinite clocks and explicit close disable
the instance. A mutex serializes completion and state changes.

Incoming frames must be durably persisted by the protected receive layer before
calling this memory-only codec. A restart loses partial assembly state and
short-lived duplicate suppression; durable queue replay and application-level
idempotency remain required. Neither assembly nor a radio ACK generates a
delivery/read receipt. Send scheduling, legal airtime limits, retransmission,
relay policy, lock/suspend admission and real A/B RF tests are separate work.
