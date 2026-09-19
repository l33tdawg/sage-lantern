import hashlib
import struct
import threading

import pytest

from sage_lantern.meshcore_transport import (
    FIRMWARE, MAX_FRAME, PACKET_SIGNAL_WINDOW, MeshCoreClient, MeshCoreError, SerialFDStream, decode_incoming, encode_frame,
)


KEY = bytes(range(32))
PREFIX = bytes(range(6))
STAMP = 1234567
TEXT = "synthetic bench message"
ACK = hashlib.sha256(struct.pack("<I", STAMP) + b"\0" + TEXT.encode() + KEY).digest()[:4]


def frame(payload):
    return b">" + struct.pack("<H", len(payload)) + payload


def handshake_parts(key=KEY, version=FIRMWARE):
    info = bytearray(82)
    info[0:2] = bytes((13, 13))
    info[4:8] = b"PIN!"
    info[60:60 + len(version)] = version.encode()
    identity = bytearray(58)
    identity[0] = 5
    identity[4:36] = key
    return frame(bytes(info)), frame(bytes(identity))


def handshake_bytes(key=KEY, version=FIRMWARE):
    return b"".join(handshake_parts(key, version))


class SerialDouble:
    def __init__(self, incoming, chunk=1):
        self.incoming = bytearray(incoming)
        self.output = bytearray()
        self.chunk = chunk

    def read(self, size, timeout):
        assert 0 < timeout <= 3
        size = min(size, self.chunk)
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def write(self, data, timeout):
        assert 0 < timeout <= 3
        size = min(len(data), self.chunk)
        self.output.extend(data[:size])
        return size


class LateTailStream(SerialDouble):
    """Serves `head`, goes quiet for `quiet_reads` calls, then serves `tail`.

    This is the shape the stock firmware shows on unit A: a frame is declared,
    part of its body arrives, the rest does not arrive inside the read deadline,
    and then it arrives afterwards.
    """

    def __init__(self, head, tail, quiet_reads=1, chunk=1):
        super().__init__(head, chunk)
        self._tail = bytearray(tail)
        self._quiet_reads = quiet_reads
        self._quiet = 0

    def read(self, size, timeout):
        if not self.incoming and self._tail:
            if self._quiet < self._quiet_reads:
                self._quiet += 1
                return b""
            self.incoming = self._tail
            self._tail = bytearray()
        return super().read(size, timeout)


def truncated_handshake(tail_bytes=48):
    """handshake_bytes() with the identity frame cut short, plus its late tail."""
    full = handshake_bytes()
    split = full.index(b">", 3)
    body = bytearray(58)
    body[0] = 5
    body[4:36] = KEY
    head = full[:split] + b">" + struct.pack("<H", 58) + bytes(body[:58 - tail_bytes])
    return head, bytes(body[58 - tail_bytes:])


def client(incoming, chunk=1):
    stream = SerialDouble(incoming, chunk)
    return MeshCoreClient(stream, expected_public_key=KEY), stream


@pytest.mark.parametrize("version", ["v1.17.1-d929643-r1", "v1.17.1-d929643-r2", "v1.17.1-d929643-r3"])
def test_packet_rssi_candidate_reports_its_actual_version(version):
    device, stream = client(handshake_bytes(version=version))
    assert device.handshake().firmware == version


def test_health_reads_firmware_stats_and_retains_advertisement():
    incoming = handshake_bytes() + frame(bytes((0x80,)) + KEY)
    incoming += frame(bytes((24, 0)) + struct.pack("<HIHB", 4100, 300, 0, 2))
    incoming += frame(bytes((24, 1)) + struct.pack("<hbbII", -110, -75, 25, 3, 4))
    incoming += frame(bytes((24, 2)) + struct.pack("<IIIIIII", 12, 10, 6, 4, 7, 5, 1))
    device, stream = client(incoming)
    device.handshake()
    result = device.health(authorize=lambda: True)
    assert result["packets_received"] == 12
    assert result["last_snr_db"] == 6.25
    assert result["last_rssi_dbm"] == -75
    assert result["tx_queue"] == 2
    assert result["discovered_nodes"][0]["public_key"] == KEY.hex()
    assert result["discovered_nodes"][0]["direct_neighbor_verified"] is False
    assert bytes(stream.output).endswith(encode_frame(bytes((56, 2))))


def stats_frames(last_rssi, last_snr_quarter, packets=12):
    return (frame(bytes((24, 0)) + struct.pack("<HIHB", 4100, 300, 0, 0))
        + frame(bytes((24, 1)) + struct.pack("<hbbII", -110, last_rssi, last_snr_quarter, 3, 4))
        + frame(bytes((24, 2)) + struct.pack("<IIIIIII", packets, 10, 6, 4, 7, 5, 1)))


def test_health_reports_packet_signal_consumed_while_receiver_polls():
    """The main-screen meter reads health while the firmware pushes one RF log
    frame per received packet into normal receiver polling. A measured packet
    signal must survive that polling even though radio stats report zero RSSI."""
    packet = frame(bytes((0x88,)) + struct.pack("bb", 54, -38) + b"packet")
    device, stream = client(handshake_bytes() + packet + frame(bytes((10,))) + stats_frames(0, 0))
    device.handshake()
    device.set_receive_observer(lambda message: None)
    assert device.receive_next(authorize=lambda: True, persist=lambda message: True) is None
    result = device.health(authorize=lambda: True)
    assert result["packets_received"] == 12
    assert result["last_rssi_dbm"] == -38
    assert result["last_snr_db"] == 13.5
    assert bytes(stream.output).endswith(encode_frame(bytes((56, 2))))


def test_health_prefers_measured_packet_signal_over_radio_stats():
    stats = frame(bytes((24, 0)) + struct.pack("<HIHB", 4100, 300, 0, 0))
    stats += frame(bytes((24, 1)) + struct.pack("<hbbII", -110, -75, 25, 3, 4))
    counters = frame(bytes((24, 2)) + struct.pack("<IIIIIII", 12, 10, 6, 4, 7, 5, 1))
    packet = frame(bytes((0x88,)) + struct.pack("bb", 36, -42) + b"packet")
    device, stream = client(handshake_bytes() + stats + packet + counters + stats + counters)
    device.handshake()
    first = device.health(authorize=lambda: True)
    assert first["last_rssi_dbm"] == -42
    assert first["last_snr_db"] == 9
    second = device.health(authorize=lambda: True)
    assert second["last_rssi_dbm"] == -42
    assert second["last_snr_db"] == 9


def test_health_falls_back_to_radio_stats_once_packet_signal_ages_out():
    packet = frame(bytes((0x88,)) + struct.pack("bb", 54, -38) + b"packet")
    ticks = [10.0]
    stream = SerialDouble(handshake_bytes() + packet + stats_frames(-75, 25) + stats_frames(-75, 25))
    device = MeshCoreClient(stream, expected_public_key=KEY, clock=lambda: ticks[0])
    device.handshake()
    measured = device.health(authorize=lambda: True)
    assert measured["last_rssi_dbm"] == -38
    ticks[0] += PACKET_SIGNAL_WINDOW + 1
    aged = device.health(authorize=lambda: True)
    assert aged["last_rssi_dbm"] == -75
    assert aged["last_snr_db"] == 6.25


def test_health_caches_the_firmware_airtime_counters():
    device, stream = client(handshake_bytes() + stats_frames(-75, 25))
    device.handshake()
    assert device.airtime_sample is None
    device.health(authorize=lambda: True)
    sample = device.airtime_sample
    assert sample.tx_airtime_seconds == 3.0
    assert sample.rx_airtime_seconds == 4.0
    assert sample.monotonic >= 0


def test_messages_waiting_push_inside_exchange_is_captured_not_dropped():
    info, identity = handshake_parts()
    device, stream = client(info + frame(bytes((0x83,))) + identity)
    device.handshake()
    assert [event["name"] for event in device.push_events] == ["messages_waiting"]
    assert device.take_messages_waiting() is True
    assert device.take_messages_waiting() is False


def test_contact_table_pushes_mark_the_directory_dirty():
    info, identity = handshake_parts()
    device, stream = client(info + frame(bytes((0x8F,)) + KEY) + frame(bytes((0x90,))) + identity)
    device.handshake()
    assert device.take_contact_table_dirty() is True
    assert device.take_contact_table_dirty() is False
    assert [event["name"] for event in device.push_events] == ["contact_deleted", "contacts_full"]


def test_send_confirmation_push_is_a_radio_observation_not_delivery():
    info, identity = handshake_parts()
    ack = bytes.fromhex("aabbccdd")
    device, stream = client(info + frame(b"\x82" + ack + struct.pack("<I", 1234)) + identity)
    device.handshake()
    confirmation = device.send_confirmation
    assert confirmation["ack_crc"] == ack.hex()
    assert confirmation["trip_time_ms"] == 1234
    assert "delivered" not in confirmation and "read" not in confirmation


def test_push_events_are_bounded_and_payload_free():
    info, identity = handshake_parts()
    push = frame(b"\x8E" + b"PRIVATE-PAYLOAD")
    round_trip = info + push * 32 + identity
    device, stream = client(round_trip * 3)
    for _ in range(3):
        device.handshake()
    events = device.push_events
    assert len(events) == 64
    assert all(event["name"] == "control_data" for event in events)
    assert "PRIVATE" not in repr(events)


@pytest.mark.parametrize("location_policy", [0, 1])
def test_discovery_beacon_is_zero_hop_location_free_and_rate_limited(location_policy):
    identity = bytearray(58)
    identity[0] = 5
    identity[4:36] = KEY
    identity[45] = location_policy
    stats = frame(bytes((24, 0)) + struct.pack("<HIHB", 4100, 300, 0, 0))
    stats += frame(bytes((24, 1)) + struct.pack("<hbbII", -110, -75, 25, 3, 4))
    stats += frame(bytes((24, 2)) + struct.pack("<IIIIIII", 12, 10, 6, 4, 7, 5, 1))
    incoming = handshake_bytes() + frame(bytes(identity))
    if location_policy == 0:
        incoming += frame(b"\x00")
    device, stream = client(incoming + stats + stats)
    device.handshake()
    device.health(authorize=lambda: True, announce=True)
    device.health(authorize=lambda: True, announce=True)
    assert bytes(stream.output).count(encode_frame(b"\x07\x00")) == (1 if location_policy == 0 else 0)
    assert encode_frame(b"\x07\x01") not in bytes(stream.output)


def send(device, **changes):
    arguments = dict(recipient_prefix=PREFIX, text=TEXT, timestamp=STAMP, authorize=lambda: True)
    arguments.update(changes)
    return device.send_text(**arguments)


@pytest.mark.parametrize("chunk", [1, 2, 179])
def test_handshake_send_and_ack_exact_wire(chunk):
    sent = frame(b"\x06\x01" + ACK + struct.pack("<I", 900))
    ack = frame(b"\x82" + ACK + struct.pack("<I", 55))
    advertisement = frame(b"\x80" + KEY)
    device, stream = client(handshake_bytes() + sent + advertisement + ack, chunk)
    identity = device.handshake()
    assert identity.public_key == KEY and "PIN!" not in repr(identity)
    receipt = send(device)
    assert receipt.serial_accepted and receipt.route == "flood"
    assert not receipt.delivered and not receipt.read
    observation = device.observe_ack(receipt)
    assert observation.radio_ack_observed and observation.trip_time_ms == 55
    assert not observation.delivered and not observation.read
    assert device._discovery_events[KEY.hex()]["direct_neighbor_verified"] is False
    assert bytes(stream.output) == (encode_frame(bytes((22, 13)))
        + encode_frame(b"\x01" + bytes(7) + b"Lantern")
        + encode_frame(b"\x02\x00\x00" + struct.pack("<I", STAMP) + PREFIX + TEXT.encode()))


@pytest.mark.parametrize("payload", [b"", bytes(MAX_FRAME + 1), "text"])
def test_bad_outgoing_frame(payload):
    with pytest.raises(MeshCoreError):
        encode_frame(payload)


@pytest.mark.parametrize("incoming", [b"", b">", b">\x01", b">\x01\x00", b"<\x01\x00x",
    b">\x00\x00", b">\xff\xff", frame(b"\x00"), frame(b"\x01\x02"),
    handshake_bytes(key=b"z" * 32), handshake_bytes(version="unknown")])
def test_read_only_failure_does_not_poison_and_retry_is_allowed(incoming):
    """A failed read-only exchange leaves the radio usable.

    Nothing can have been sent or dequeued by a read-only command and the
    firmware re-sends a whole record for the next read, so a stall must not cost
    a process restart. The failure is still reported for that one call, and the
    command is never retried inside the call.
    """
    device, stream = client(incoming)
    with pytest.raises(MeshCoreError):
        device.handshake()
    assert device.poisoned is False and device.poisoned_by is None
    before = bytes(stream.output)
    with pytest.raises(MeshCoreError):
        device.handshake()
    assert bytes(stream.output) != before


def test_read_only_stall_discards_the_late_tail_and_stays_usable():
    head, tail = truncated_handshake(48)
    stream = LateTailStream(head, tail, quiet_reads=1)
    device = MeshCoreClient(stream, expected_public_key=KEY)
    with pytest.raises(MeshCoreError) as error:
        device.handshake()
    assert error.value.code == "serial_unavailable" and error.value.outcome_unknown is False
    assert device.poisoned is False
    # Both handshake commands (0x16 then 0x01) were written before the read
    # stalled; the failing call is still reported once, with no retry inside it.
    assert device.read_failure == {"command": "01", "sending": False, "receiving": False,
                                   "error": "serial_incomplete", "frames": 2}
    assert device.discarded_bytes == len(tail)
    written = bytes(stream.output)
    assert written == encode_frame(bytes((22, 13))) + encode_frame(b"\x01" + bytes(7) + b"Lantern")
    with pytest.raises(MeshCoreError) as retry:
        device.handshake()
    assert retry.value.code == "serial_unavailable"
    assert bytes(stream.output) != written


def test_read_only_stall_discard_is_bounded():
    head, _ = truncated_handshake(48)
    stream = LateTailStream(head, b"\x00" * 20000, quiet_reads=1)
    device = MeshCoreClient(stream, expected_public_key=KEY)
    with pytest.raises(MeshCoreError):
        device.handshake()
    assert device.poisoned is False
    assert device.discarded_bytes == 4096
    assert bytes(stream.output) == encode_frame(bytes((22, 13))) + encode_frame(b"\x01" + bytes(7) + b"Lantern")


def test_send_stall_still_poisons_and_records_the_cause():
    device, stream = client(handshake_bytes())
    device.handshake()
    with pytest.raises(MeshCoreError) as error:
        send(device)
    assert error.value.code == "send_unconfirmed" and error.value.outcome_unknown is True
    assert device.poisoned is True
    assert device.poisoned_by == {"command": "02", "sending": True, "receiving": False,
                                  "error": "serial_incomplete", "frames": 1}
    before = bytes(stream.output)
    with pytest.raises(MeshCoreError, match="reconciliation_required"):
        send(device)
    assert bytes(stream.output) == before


@pytest.mark.parametrize("change", [dict(text=""), dict(text="x" * 129), dict(text="é" * 65),
    dict(text="a\0b"), dict(text="\ud800"), dict(timestamp=True), dict(timestamp=0),
    dict(timestamp=2 ** 32), dict(recipient_prefix=b"short")])
def test_invalid_send_no_io(change):
    device, stream = client(b"")
    with pytest.raises(MeshCoreError):
        send(device, **change)
    assert not stream.output


def test_explicit_authorization_and_handshake_required():
    device, stream = client(b"")
    with pytest.raises(MeshCoreError):
        send(device)
    assert not stream.output
    device, stream = client(handshake_bytes())
    device.handshake()
    before = bytes(stream.output)
    with pytest.raises(MeshCoreError):
        send(device, authorize=lambda: False)
    assert bytes(stream.output) == before


@pytest.mark.parametrize("response", [b"", frame(b"\x06\x00" + b"fake" + bytes(4)),
                                      frame(b"\x01\x03"), frame(b"\x06")])
def test_unknown_send_is_never_retried(response):
    device, stream = client(handshake_bytes() + response)
    device.handshake()
    with pytest.raises(MeshCoreError) as error:
        send(device)
    assert error.value.outcome_unknown
    before = bytes(stream.output)
    with pytest.raises(MeshCoreError):
        send(device)
    assert bytes(stream.output) == before


def test_unsolicited_frames_bounded_and_not_returned():
    device, stream = client(frame(b"\x88PRIVATE") * 34 + handshake_bytes())
    with pytest.raises(MeshCoreError) as error:
        device.handshake()
    assert "PRIVATE" not in str(error.value)
    assert stream.incoming


def test_wrong_ack_never_claims_delivery():
    device, stream = client(handshake_bytes() + frame(b"\x06\x00" + ACK + bytes(4))
                            + frame(b"\x82nope" + bytes(4)))
    device.handshake()
    receipt = send(device)
    before = bytes(stream.output)
    with pytest.raises(MeshCoreError):
        device.observe_ack(receipt)
    assert bytes(stream.output) == before


def test_clock_deadline_and_backwards_fail_closed():
    for ticks in ([0, 4], [10, 9]):
        stream = SerialDouble(handshake_bytes())
        times = iter(ticks)
        device = MeshCoreClient(stream, expected_public_key=KEY, clock=lambda: next(times))
        with pytest.raises(MeshCoreError):
            device.handshake()
        assert not stream.output


def test_concurrent_operation_rejected_without_second_write():
    device, stream = client(handshake_bytes())
    entered = threading.Event()
    release = threading.Event()
    original = stream.write

    def blocked(data, timeout):
        entered.set()
        assert release.wait(1)
        return original(data, timeout)

    stream.write = blocked
    worker = threading.Thread(target=device.handshake)
    worker.start()
    assert entered.wait(1)
    try:
        with pytest.raises(MeshCoreError, match="busy"):
            device.handshake()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()


def test_fd_stream_rejects_non_device_without_opening_ports():
    with pytest.raises(MeshCoreError):
        SerialFDStream(-1)


def incoming(code=16, payload=b"untrusted\xff", text_type=0):
    header = bytes((code, 248, 0, 0)) if code in (16, 17) else bytes((code,))
    address = PREFIX if code in (7, 16) else b"\x02"
    return header + address + bytes((255, text_type)) + struct.pack("<I", STAMP) + payload


@pytest.mark.parametrize("code", [7, 8, 16, 17])
@pytest.mark.parametrize("chunk", [1, 179])
def test_receive_one_persisted_raw_untrusted_no_read_ack(code, chunk):
    raw = incoming(code)
    device, stream = client(handshake_bytes() + frame(b"\x83") + frame(raw) + frame(incoming()), chunk)
    device.handshake()
    before = bytes(stream.output)
    stored = []

    def persist(message):
        stored.append(message)
        return True

    result = device.receive_next(authorize=lambda: True, persist=persist)
    assert stored == [result] and result.raw_frame == raw
    assert result.payload == b"untrusted\xff"
    assert result.timestamp == STAMP and result.path_descriptor == 255
    assert result.snr_quarter_db == (-8 if code in (16, 17) else None)
    assert result.sender_prefix == (PREFIX if code in (7, 16) else None)
    assert result.channel_index == (2 if code in (8, 17) else None)
    assert result.trust == "untrusted_radio_payload"
    assert not result.application_authenticated and not result.read and not result.delivered
    assert "untrusted" not in repr(result)
    assert bytes(stream.output)[len(before):] == encode_frame(b"\x0a")
    assert stream.incoming


def test_empty_is_explicit_not_timeout():
    device, stream = client(handshake_bytes() + frame(b"\x0a"))
    device.handshake()
    assert device.receive_next(authorize=lambda: True, persist=lambda message: pytest.fail("not empty")) is None


@pytest.mark.parametrize("raw", [b"", b"\x10", incoming()[:15], b"\x0b", bytes(177),
                                    b"\x10\x00\x01\x00" + incoming()[4:]])
def test_malformed_incoming_not_decoded(raw):
    with pytest.raises(MeshCoreError):
        decode_incoming(raw)


@pytest.mark.parametrize("response", [b"", frame(b"\x0aextra"), frame(b"\x10"), frame(b"\x01\x04")])
def test_receive_unknown_poison_no_automatic_pop(response):
    device, stream = client(handshake_bytes() + response)
    device.handshake()
    with pytest.raises(MeshCoreError) as error:
        device.receive_next(authorize=lambda: True, persist=lambda message: True)
    assert error.value.outcome_unknown and error.value.code == "receive_unconfirmed"
    before = bytes(stream.output)
    with pytest.raises(MeshCoreError):
        device.receive_next(authorize=lambda: True, persist=lambda message: True)
    assert bytes(stream.output) == before


@pytest.mark.parametrize("fault", ["storage_false", "storage_error", "revoked", "death"])
def test_receive_storage_and_access_failure_no_content_escape(fault):
    device, stream = client(handshake_bytes() + frame(incoming()))
    device.handshake()
    checks = iter([True, False if fault == "revoked" else True])
    stored = []

    def persist(message):
        stored.append(message)
        if fault == "storage_error":
            raise RuntimeError("PRIVATE MESSAGE")
        if fault == "death":
            raise KeyboardInterrupt
        return fault != "storage_false"

    with pytest.raises((MeshCoreError, KeyboardInterrupt)) as error:
        device.receive_next(authorize=lambda: next(checks), persist=persist)
    assert len(stored) == 1
    assert "PRIVATE MESSAGE" not in str(error.value)
    with pytest.raises(MeshCoreError, match="reconciliation_required"):
        device.receive_next(authorize=lambda: True, persist=lambda message: True)


def test_receive_no_handshake_or_authorization_no_io():
    device, stream = client(b"")
    with pytest.raises(MeshCoreError):
        device.receive_next(authorize=lambda: True, persist=lambda message: True)
    assert not stream.output
    device, stream = client(handshake_bytes() + frame(b"\x0a"))
    device.handshake()
    before = bytes(stream.output)
    written = device.wire_frames
    with pytest.raises(MeshCoreError):
        device.receive_next(authorize=lambda: False, persist=lambda message: True)
    assert bytes(stream.output) == before and device.wire_frames == written
    # A refusal issued before the first write leaves firmware state untouched,
    # so the next authorized operation runs instead of a cold start.
    assert device.receive_next(authorize=lambda: True,
                               persist=lambda message: True) is None
    assert device.wire_frames == written + 1


def test_signed_subtype_payload_not_misrepresented_as_verified_text():
    raw = incoming(payload=b"signraw body", text_type=2)
    message = decode_incoming(raw)
    assert message.text_type == 2 and message.payload == b"signraw body"
    assert not message.application_authenticated


def binary_incoming(payload=b"\x00\xff", declared=None):
    return bytes((27, 248, 0, 0, 3, 255, 0x34, 0x12,
                  len(payload) if declared is None else declared)) + payload


@pytest.mark.parametrize("size", [0, 1, MAX_FRAME - 9])
def test_binary_receive_persists_zero_and_maximum_payload(size):
    raw = binary_incoming(bytes(range(size)))
    device, stream = client(handshake_bytes() + frame(raw) + frame(incoming()))
    device.handshake()
    stored = []

    def persist(message):
        stored.append(message)
        return True

    result = device.receive_next(authorize=lambda: True, persist=persist)
    assert stored == [result] and result.raw_frame == raw and result.payload == raw[9:]
    assert result.kind == "channel_binary" and result.data_type == 0x1234
    assert result.text_type is None and result.timestamp is None and result.sender_prefix is None
    assert result.channel_index == 3 and result.snr_quarter_db == -8 and result.path_descriptor == 255
    assert not result.application_authenticated and not result.delivered and not result.read
    assert stream.incoming


@pytest.mark.parametrize("raw", [binary_incoming(declared=0), binary_incoming(declared=3),
    binary_incoming(b"", declared=1), binary_incoming(b"x" * (MAX_FRAME - 8)),
    binary_incoming()[:8], b"\x1b\x00\x01\x00" + binary_incoming()[4:]])
def test_binary_declared_length_and_header_exact(raw):
    with pytest.raises(MeshCoreError):
        decode_incoming(raw)


@pytest.mark.parametrize("fault", ["persist_false", "persist_error", "lock_race"])
def test_binary_receive_persistence_failure_and_lock_race(fault):
    raw = binary_incoming()
    device, stream = client(handshake_bytes() + frame(raw))
    device.handshake()
    stored = []
    authorized = True

    def persist(message):
        nonlocal authorized
        stored.append(message)
        if fault == "persist_error":
            raise RuntimeError("SECRET BINARY")
        if fault == "lock_race":
            authorized = False
        return fault != "persist_false"

    with pytest.raises(MeshCoreError) as error:
        device.receive_next(authorize=lambda: authorized, persist=persist)
    assert error.value.outcome_unknown and "SECRET BINARY" not in str(error.value)
    assert len(stored) == 1 and stored[0].raw_frame == raw
    before = bytes(stream.output)
    with pytest.raises(MeshCoreError, match="reconciliation_required"):
        device.receive_next(authorize=lambda: True, persist=lambda message: True)
    assert bytes(stream.output) == before
