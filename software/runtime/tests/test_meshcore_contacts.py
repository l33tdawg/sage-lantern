import hashlib

from nacl.bindings import crypto_core_ed25519_is_valid_point
from nacl.signing import SigningKey
import pytest

from sage_lantern.meshcore_contacts import (
    ContactInventoryDecoder, MeshCoreContactsError, get_contacts,
)
from sage_lantern.meshcore_transport import MeshCoreError, encode_frame
from test_meshcore_transport import client, frame, handshake_bytes


KEY = SigningKey(b'c' * 32).verify_key.encode()


def start(count):
    return b'\x02' + count.to_bytes(4, 'little')


def end(watermark):
    return b'\x04' + watermark.to_bytes(4, 'little')


def contact(key=KEY, modified=123):
    payload = bytearray(148)
    payload[0] = 3
    payload[1:33] = key
    payload[33:36] = b'\x01\x00\xff'
    payload[100:113] = b'private label'
    payload[132:136] = (120).to_bytes(4, 'little')
    payload[136:144] = b'GPS-DATA'
    payload[144:148] = modified.to_bytes(4, 'little')
    return bytes(payload)


def decode(frames):
    decoder = ContactInventoryDecoder()
    for payload in frames:
        decoder.feed(payload)
    return decoder.finish()


@pytest.mark.parametrize('chunk', [1, 7, 179])
def test_real_client_exclusive_delegate_exact_command(chunk):
    device, stream = client(handshake_bytes() + frame(start(1)) + frame(contact()) + frame(end(123)), chunk)
    device.handshake()
    before = bytes(stream.output)
    inventory = device.get_contacts(authorize=lambda: True)
    assert bytes(stream.output) == before + encode_frame(b'\x04')
    assert inventory.unique_prefix(KEY) == KEY[:6]
    assert inventory.resolve_prefix(KEY[:6]).public_key == KEY
    assert inventory.watermark == 123
    assert inventory.contacts[0].last_modified == 123
    assert not hasattr(inventory.contacts[0], 'name')
    assert not hasattr(inventory.contacts[0], 'gps')
    assert 'private label' not in repr(inventory)
    assert 'GPS-DATA' not in repr(inventory)


def test_empty_and_maximum_full_inventory():
    assert decode([start(0), end(0)]).contacts == ()
    keys = [SigningKey(hashlib.sha256(str(index).encode()).digest()).verify_key.encode()
            for index in range(256)]
    inventory = decode([start(256)] + [contact(key) for key in keys] + [end(123)])
    assert len(inventory.contacts) == 256
    assert inventory.unique_prefix(keys[-1]) == keys[-1][:6]


@pytest.mark.parametrize('frames', [
    [], [start(1)], [start(1), contact()], [start(257)], [contact()], [end(0)],
    [start(1), end(0)], [start(0), contact()], [start(2), contact(), contact(), end(123)],
    [start(1), contact(modified=0), end(0)], [start(1), contact(), end(124)],
    [start(0), end(1)], [start(0), start(0)], [start(0), b'\x01\x04'],
    [start(1), contact(bytes(32))], [start(1), contact()[:-1]],
    [start(1), contact() + b'\0'], [b'\x02'], [start(0), b'\x04'],
    [start(0), end(0), end(0)], [start(0), b''], [start(0), b'\x82' * 177],
])
def test_malformed_partial_and_duplicate_inventory_never_returns(frames):
    with pytest.raises(MeshCoreContactsError, match='^meshcore_contacts_unavailable$'):
        decode(frames)


def test_decoder_failure_is_sticky():
    decoder = ContactInventoryDecoder()
    with pytest.raises(MeshCoreContactsError):
        decoder.feed(b'bad')
    with pytest.raises(MeshCoreContactsError):
        decoder.feed(start(0))
    with pytest.raises(MeshCoreContactsError):
        decoder.finish()


@pytest.mark.parametrize('code', [0x80, 0x81, 0x8a, 0x8f, 0x90])
def test_contact_mutation_push_invalidates_scan(code):
    with pytest.raises(MeshCoreContactsError):
        decode([start(0), bytes([code]), end(0)])


def test_other_pushes_have_total_bound():
    assert decode([start(0)] + [b'\x83'] * 32 + [end(0)]).contacts == ()
    with pytest.raises(MeshCoreContactsError):
        decode([start(0)] + [b'\x83'] * 33 + [end(0)])


def test_six_byte_collision_and_missing_full_key_fail_closed():
    matches = []
    for index in range(256):
        candidate = KEY[:6] + hashlib.sha256(str(index).encode()).digest()[:26]
        if crypto_core_ed25519_is_valid_point(candidate):
            matches.append(candidate)
        if len(matches) == 2:
            break
    assert len(matches) == 2
    inventory = decode([start(2), contact(matches[0]), contact(matches[1]), end(123)])
    with pytest.raises(MeshCoreContactsError):
        inventory.unique_prefix(matches[0])
    with pytest.raises(MeshCoreContactsError):
        inventory.resolve_prefix(KEY[:6])
    single = decode([start(1), contact(matches[0]), end(123)])
    with pytest.raises(MeshCoreContactsError):
        single.unique_prefix(matches[1])
    for prefix in (KEY, KEY[:5], b'absent', 'abcdef'):
        with pytest.raises(MeshCoreContactsError):
            single.resolve_prefix(prefix)


@pytest.mark.parametrize('incoming', [b'', frame(start(1)), frame(start(257)),
                                      frame(start(1)) + frame(contact()) + frame(end(0))])
def test_real_client_failure_poison_no_retry(incoming):
    device, stream = client(handshake_bytes() + incoming)
    device.handshake()
    with pytest.raises(MeshCoreError, match='serial_unavailable'):
        device.get_contacts(authorize=lambda: True)
    before = bytes(stream.output)
    with pytest.raises(MeshCoreError, match='reconciliation_required'):
        device.get_contacts(authorize=lambda: True)
    assert bytes(stream.output) == before


def test_real_client_busy_and_unhandshaken_emit_nothing():
    device, stream = client(b'')
    device._mutex.acquire()
    try:
        with pytest.raises(MeshCoreError, match='busy'):
            device.get_contacts(authorize=lambda: True)
    finally:
        device._mutex.release()
    with pytest.raises(MeshCoreError):
        device.get_contacts(authorize=lambda: True)
    assert bytes(stream.output) == b''


def test_authorization_before_during_and_after_scan():
    for decisions in ([False], [True, False], [True, True, False]):
        responses = iter(decisions)
        calls = []
        def exchange(command, expected, deadline):
            calls.append((command, expected, deadline))
            return start(0)
        with pytest.raises(MeshCoreContactsError):
            get_contacts(exchange=exchange, read_frame=lambda deadline: end(0), deadline=5,
                         authorize=lambda: next(responses))
        assert len(calls) == (0 if decisions == [False] else 1)


@pytest.mark.parametrize('decisions', [[False], [True, False], [True, True, False]])
def test_public_method_authorization_failure_poison(decisions):
    device, stream = client(handshake_bytes() + frame(start(0)) + frame(end(0)))
    device.handshake()
    before = bytes(stream.output)
    responses = iter(decisions)
    with pytest.raises(MeshCoreError, match='serial_unavailable'):
        device.get_contacts(authorize=lambda: next(responses))
    expected = before if decisions == [False] else before + encode_frame(b'\x04')
    assert bytes(stream.output) == expected
    with pytest.raises(MeshCoreError, match='reconciliation_required'):
        device.get_contacts(authorize=lambda: True)
    assert bytes(stream.output) == expected


def test_shared_deadline_and_sanitized_transport_failure():
    seen = []
    def exchange(command, expected, deadline):
        seen.append(deadline)
        return start(1)
    def read(deadline):
        seen.append(deadline)
        raise TimeoutError('private diagnostic')
    with pytest.raises(MeshCoreContactsError, match='^meshcore_contacts_unavailable$'):
        get_contacts(exchange=exchange, read_frame=read, deadline=123.5, authorize=lambda: True)
    assert seen == [123.5, 123.5]
