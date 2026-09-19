import importlib.util
import json
import os
from pathlib import Path
import struct
from unittest.mock import patch

import pytest


SPEC = importlib.util.spec_from_file_location('set_radio_repeat', Path(__file__).parents[1] / 'lantern/set-radio-repeat.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
KEY = bytes(range(32))
BASE = 'v1.17.1-d929643'
R5 = BASE + '-r5'
R3 = BASE + '-r3'


def info(repeat=0, version=R5):
    response = bytearray(82)
    response[:2] = bytes((13, 13))
    response[4:8] = b'PIN!'
    response[60:60 + len(version)] = version.encode()
    response[80] = repeat
    return bytes(response)


def self_info(name=b'LANTERN-B', power=10, maximum=22, freq=917375, bw=62500, sf=8, cr=5):
    response = bytearray(58)
    response[:4] = bytes((5, 1)) + struct.pack('b', power) + bytes((maximum,))
    response[4:36] = KEY
    response[48:58] = struct.pack('<IIBB', freq, bw, sf, cr)
    return bytes(response) + name


class Radio:
    """Just enough of the companion to answer QUERY/APP_START and one setter."""

    def __init__(self, repeat=0, version=R5, applies=True, response=b'\x00', **params):
        self.info = info(repeat, version)
        self.self_info = self_info(**params)
        self.applies = applies
        self.response = response
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        if command == MODULE.QUERY:
            return self.info
        if command == MODULE.APP_START:
            return self.self_info
        assert command[:1] == b'\x0b' and len(command) == 12
        if self.applies:
            self.self_info = self.self_info[:48] + command[1:11] + self.self_info[58:]
            self.info = self.info[:80] + bytes((command[11],)) + self.info[81:]
        return self.response


class Transport:
    """The Exchange contract without the framing: allowlist, poison, exact OK."""

    def __init__(self, radio, allowed):
        self.radio = radio
        self.allowed = set(allowed)
        self.poisoned = False

    def __call__(self, command):
        assert not self.poisoned and command in self.allowed
        response = self.radio(command)
        if command not in (MODULE.QUERY, MODULE.APP_START) and response != b'\x00':
            self.poisoned = True
        return response


def transport(radio):
    return Transport(radio, {MODULE.QUERY, MODULE.APP_START})


@pytest.mark.parametrize('repeat,desired', [(0, 1), (1, 0)])
def test_write_carries_only_observed_parameters(repeat, desired):
    radio = Radio(repeat=repeat)
    exchange = transport(radio)
    events = []
    result = MODULE.configure(exchange, desired, events.append, KEY.hex())
    assert result['repeat'] == desired
    assert result['freq_khz'] == 917375 and result['bandwidth_hz'] == 62500
    assert result['sf'] == 8 and result['cr'] == 5 and result['name'] == 'LANTERN-B'
    assert result['tx_setting_dbm'] == 10 and result['public_key'] == KEY.hex()
    setter = [command for command in radio.calls if command not in (MODULE.QUERY, MODULE.APP_START)]
    assert setter == [b'\x0b' + struct.pack('<IIBB', 917375, 62500, 8, 5) + bytes((desired,))]
    assert [event['event'] for event in events] == ['prepared', 'setter_intent', 'setter_verified']
    assert events[-1]['observed']['repeat'] == desired


def test_matching_state_writes_nothing():
    radio = Radio(repeat=1)
    events = []
    result = MODULE.configure(transport(radio), 1, events.append, KEY.hex())
    assert result['repeat'] == 1
    assert radio.calls == [MODULE.QUERY, MODULE.APP_START]
    assert [event['event'] for event in events] == ['prepared', 'already_set']


@pytest.mark.parametrize('version', [BASE, R3])
def test_enable_refused_without_repeat_allowance(version):
    radio = Radio(version=version)
    events = []
    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='repeat allowance'):
        MODULE.configure(transport(radio), 1, events.append)
    assert radio.calls == [MODULE.QUERY, MODULE.APP_START]
    assert [event['event'] for event in events] == ['prepared']


def test_enable_refused_off_the_commissioned_frequency():
    radio = Radio(freq=915000)
    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='frequency mismatch'):
        MODULE.configure(transport(radio), 1, lambda event: None)
    assert radio.calls == [MODULE.QUERY, MODULE.APP_START]


def test_disable_is_allowed_on_any_admitted_radio():
    radio = Radio(repeat=1, version=R3)
    assert MODULE.configure(transport(radio), 0, lambda event: None)['repeat'] == 0


def test_readback_mismatch_refuses():
    radio = Radio(applies=False)
    events = []
    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='readback mismatch'):
        MODULE.configure(transport(radio), 1, events.append)
    assert [event['event'] for event in events] == ['prepared', 'setter_intent']


def test_unknown_setter_outcome_poisons():
    radio = Radio(response=b'\x01\x06')
    exchange = transport(radio)
    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='setter result unverified'):
        MODULE.configure(exchange, 1, lambda event: None)
    assert exchange.poisoned


def test_unexpected_command_is_refused_before_the_wire():
    exchange = MODULE.Exchange(42, lambda: None, {MODULE.QUERY, MODULE.APP_START})
    with patch.object(MODULE.os, 'write') as write, pytest.raises(MODULE.INVENTORY.InventoryRefused, match='forbidden'):
        exchange(MODULE.radio_command(dict(freq_khz=917375, bandwidth_hz=62500, sf=8, cr=5), 1))
    write.assert_not_called()


def test_malformed_frame_poisons_the_exchange():
    exchange = MODULE.Exchange(42, lambda: None, {MODULE.QUERY, MODULE.APP_START})
    command = MODULE.radio_command(dict(freq_khz=917375, bandwidth_hz=62500, sf=8, cr=5), 1)
    exchange.allowed.add(command)
    with patch.object(MODULE.select, 'select', side_effect=[([], [], []), ([], [42], [])]), \
         patch.object(MODULE.os, 'write', return_value=len(command) + 3), \
         patch.object(MODULE.INVENTORY, 'read_exact', side_effect=[b'x\x01\x00', b'\x00']):
        with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='frame invalid'):
            exchange(command)
        assert exchange.poisoned


def test_drain_discards_buffered_bytes_and_is_bounded():
    exchange = MODULE.Exchange(42, lambda: None, {MODULE.QUERY})
    with patch.object(MODULE.select, 'select', side_effect=[([42], [], []), ([42], [], []), ([], [], [])]), \
         patch.object(MODULE.os, 'read', side_effect=[b'abc', b'de']):
        assert exchange.drain() == 5
    exchange = MODULE.Exchange(42, lambda: None, {MODULE.QUERY})
    with patch.object(MODULE.select, 'select', return_value=([42], [], [])), \
         patch.object(MODULE.os, 'read', side_effect=[b'aaaa', b'bbbb']):
        with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='drain limit exceeded'):
            exchange.drain(limit=4)


def test_drain_pending_consumes_unsolicited_frames_before_a_command():
    exchange = MODULE.Exchange(42, lambda: None, {MODULE.QUERY}, drain_pending=True)
    with patch.object(MODULE.select, 'select', side_effect=[([42], [], []), ([], [], []), ([], [42], [])]), \
         patch.object(MODULE.os, 'read', side_effect=[b'\x8a\x01\x02']), \
         patch.object(MODULE.os, 'write', return_value=6), \
         patch.object(MODULE.INVENTORY, 'read_exact', side_effect=[b'>\x01\x00', b'\x00']):
        assert exchange(MODULE.QUERY) == b'\x00'
    assert exchange.drained_bytes == 3


def test_partial_write_is_continuation_not_resend():
    exchange = MODULE.Exchange(42, lambda: None, {MODULE.QUERY, MODULE.APP_START})
    command = MODULE.radio_command(dict(freq_khz=917375, bandwidth_hz=62500, sf=8, cr=5), 1)
    exchange.allowed.add(command)
    with patch.object(MODULE.select, 'select', side_effect=[([], [], []), ([], [42], []), ([], [42], [])]), \
         patch.object(MODULE.os, 'write', side_effect=[2, len(command) + 1]) as write, \
         patch.object(MODULE.INVENTORY, 'read_exact', side_effect=[b'>\x01\x00', b'\x00']):
        assert exchange(command) == b'\x00'
    assert [call.args[1] for call in write.call_args_list] == [b'<\x0c\x00' + command, b'\x00' + command]


@pytest.mark.parametrize('repeat,flags', [(2, 0), (1, 3)])
def test_info_flag_validation(repeat, flags):
    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='flags'):
        MODULE.parse_info(info(repeat)[:81] + bytes((flags,)))


@pytest.mark.parametrize('version', ['v1.17.1-d929643-r0', 'v1.17.1-d929643-x1', 'v1.16.1-d929643', 'r5'])
def test_info_version_validation(version):
    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='version'):
        MODULE.parse_info(info(0, version))


def test_expected_key_mismatch_stops_before_write():
    radio = Radio()
    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='public key'):
        MODULE.configure(transport(radio), 1, lambda event: None, (b'\xff' * 32).hex())
    assert radio.calls == [MODULE.QUERY, MODULE.APP_START]


def test_self_info_rejections():
    for response in (self_info()[:57], self_info(b'x' * 32), self_info(b'bad\0name'),
                     self_info(power=-10), self_info(maximum=9), self_info(sf=13), self_info(cr=9)):
        with pytest.raises(MODULE.INVENTORY.InventoryRefused):
            MODULE.parse_self(response, KEY.hex())


@pytest.mark.skipif(os.geteuid() != 0, reason='the journal is root-owned by design; runs in the unit root context')
def test_journal_writes_an_append_only_file(tmp_path):
    root = tmp_path / 'radio-repeat'
    with MODULE.journal('20260919-070426', '44:1B:F6:62:56:54', root=root) as append:
        append({'event': 'prepared'})
        append({'event': 'setter_intent'})
    directory = root / '20260919-070426-441BF6625654'
    assert stat_mode(directory) == 0o700
    events = [json.loads(line) for line in (directory / 'attempt.jsonl').read_text().splitlines()]
    assert [event['event'] for event in events] == ['prepared', 'setter_intent']
    with pytest.raises(FileExistsError):
        with MODULE.journal('20260919-070426', '44:1B:F6:62:56:54', root=root):
            raise AssertionError('unreachable')


def stat_mode(path):
    import stat
    return stat.S_IMODE(os.stat(path).st_mode)
