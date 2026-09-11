import importlib.util
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import struct
from types import SimpleNamespace
from unittest.mock import patch

import pytest


SPEC = importlib.util.spec_from_file_location('commission_b_radio', Path(__file__).parents[1] / 'lantern/commission-b-radio.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def info(repeat=1):
    response = bytearray(82)
    response[:2] = bytes((13, 13))
    response[4:8] = b'PIN!'
    response[60:60 + len(MODULE.INVENTORY.FIRMWARE)] = MODULE.INVENTORY.FIRMWARE.encode()
    response[80] = repeat
    return bytes(response)


def self_info(name=b'B-factory', power=20):
    response = bytearray(58)
    response[:4] = bytes((5, 1, power, 22))
    response[4:36] = MODULE.PUBLIC_KEY
    response[48:58] = struct.pack('<IIBB', 915000, 250000, 10, 5)
    return bytes(response) + name


class Radio:
    def __init__(self, repeat=1):
        self.info = info(repeat)
        self.self_info = self_info()
        self.calls = []
        self.failure = None

    def __call__(self, command):
        self.calls.append(command)
        if command == MODULE.QUERY:
            return self.info
        if command == MODULE.APP_START:
            return self.self_info
        if self.failure:
            raise self.failure
        if command == MODULE.POWER:
            self.self_info = self.self_info[:2] + b'\x0a' + self.self_info[3:]
        elif command == MODULE.NAME:
            self.self_info = self.self_info[:58] + command[1:]
        elif command in (MODULE.RADIO + b'\x00', MODULE.RADIO + b'\x01'):
            self.self_info = self.self_info[:48] + command[1:11] + self.self_info[58:]
            assert command[11] == self.info[80]
        else:
            raise AssertionError('unexpected command')
        return b'\x00'


@pytest.mark.parametrize('repeat', [0, 1])
def test_transaction_pins_bytes_preserves_repeat_and_redacts(repeat):
    radio = Radio(repeat)
    events = []

    def exchange(command):
        if command not in MODULE.INVENTORY.COMMANDS:
            assert events[-1] == dict(event='setter_intent', opcode=command[0], outcome='unknown_until_verified')
        return radio(command)

    result = MODULE.commission(exchange, events.append)
    assert all(result[key] == value for key, value in MODULE.TARGET.items())
    assert result['repeat'] == repeat
    assert [command for command in radio.calls if command not in MODULE.INVENTORY.COMMANDS] == [
        b'\x0c\x0a', b'\x08SAGE-LANTERN-B', bytes.fromhex('0b7fff0d0024f400000805') + bytes((repeat,))]
    assert len([event for event in events if event['event'] == 'setter_verified']) == 3
    assert 'PIN!' not in json.dumps(events)
    assert 'PIN!' not in json.dumps(result)


def test_matching_profile_has_no_setters():
    radio = Radio()
    MODULE.commission(radio, lambda event: None)
    radio.calls.clear()
    MODULE.commission(radio, lambda event: None)
    assert all(command in MODULE.INVENTORY.COMMANDS for command in radio.calls)


@pytest.mark.parametrize('offset,value', [(0, 12), (1, 12), (60, 88), (80, 2), (81, 3)])
def test_info_mismatch_stops_before_identity(offset, value):
    radio = Radio()
    changed = bytearray(radio.info)
    changed[offset] = value
    radio.info = bytes(changed)
    with pytest.raises(MODULE.INVENTORY.InventoryRefused):
        MODULE.commission(radio, lambda event: None)
    assert radio.calls == [MODULE.QUERY]


@pytest.mark.parametrize('size', [0, 1, 80, 81, 83, 176])
def test_info_length(size):
    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='length'):
        MODULE.parse_info((info() + bytes(176))[:size])


@pytest.mark.parametrize('response', [self_info()[:57], self_info(b'x' * 32), self_info(b'bad\0name'),
                                      self_info(b'bad\nname'), self_info(b'\xff'),
                                      self_info()[:4] + bytes(32) + self_info()[36:],
                                      b'\x05\x02' + self_info()[2:],
                                      self_info()[:48] + bytes(10) + b'B'])
def test_self_info_rejections(response):
    with pytest.raises(MODULE.INVENTORY.InventoryRefused):
        MODULE.parse_self(response)


def test_self_info_length_boundaries():
    assert MODULE.parse_self(self_info(b''))['name'] == ''
    assert len(MODULE.parse_self(self_info(b'x' * 31))['name']) == 31


def test_wrong_key_never_sets():
    radio = Radio()
    radio.self_info = radio.self_info[:4] + bytes(32) + radio.self_info[36:]
    events = []
    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='public key'):
        MODULE.commission(radio, events.append)
    assert radio.calls == [MODULE.QUERY, MODULE.APP_START]
    assert events == []


def test_unknown_setter_stops_no_retry():
    radio = Radio()
    radio.failure = TimeoutError('secret')
    events = []
    with pytest.raises(TimeoutError):
        MODULE.commission(radio, events.append)
    assert radio.calls == [MODULE.QUERY, MODULE.APP_START, MODULE.POWER]
    assert events[-1]['outcome'] == 'unknown_until_verified'


def test_readback_mismatch_stops_before_next_setter():
    radio = Radio()
    events = []

    def exchange(command):
        return b'\x00' if command == MODULE.POWER else radio(command)

    with pytest.raises(MODULE.INVENTORY.InventoryRefused, match='readback'):
        MODULE.commission(exchange, events.append)
    assert MODULE.NAME not in radio.calls
    assert events[-1]['event'] == 'setter_intent'


def test_journal_failure_prevents_setter():
    radio = Radio()

    def append(event):
        if event['event'] == 'setter_intent':
            raise OSError('disk full')

    with pytest.raises(OSError):
        MODULE.commission(radio, append)
    assert radio.calls == [MODULE.QUERY, MODULE.APP_START]


@pytest.mark.parametrize('command', [b'\x07', b'\x02', b'\x17', b'\x18', b'\x13', MODULE.RADIO, MODULE.RADIO + b'\x02'])
def test_transport_allowlist_before_write(command):
    exchange = MODULE.Exchange(42, lambda: None)
    with patch.object(MODULE.os, 'write') as write, pytest.raises(MODULE.INVENTORY.InventoryRefused):
        exchange(command)
    write.assert_not_called()


@pytest.mark.parametrize('response', [b'\x00\x00', b'\x01\x06', b'\x80'])
def test_transport_nonexact_ok_poisoned(response):
    exchange = MODULE.Exchange(42, lambda: None)
    with patch.object(MODULE.select, 'select', side_effect=[([], [], []), ([], [42], [])]), \
         patch.object(MODULE.os, 'write', return_value=5) as write, \
         patch.object(MODULE.INVENTORY, 'read_exact', side_effect=[b'>' + len(response).to_bytes(2, 'little'), response]):
        with pytest.raises(MODULE.INVENTORY.InventoryRefused):
            exchange(MODULE.POWER)
        assert exchange.poisoned
        with pytest.raises(MODULE.INVENTORY.InventoryRefused):
            exchange(MODULE.POWER)
        assert write.call_count == 1


def test_transport_partial_write_is_continuation_not_resend():
    exchange = MODULE.Exchange(42, lambda: None)
    with patch.object(MODULE.select, 'select', side_effect=[([], [], []), ([], [42], []), ([], [42], [])]), \
         patch.object(MODULE.os, 'write', side_effect=[2, 3]) as write, \
         patch.object(MODULE.INVENTORY, 'read_exact', side_effect=[b'>\x01\x00', b'\x00']):
        assert exchange(MODULE.POWER) == b'\x00'
    assert [call.args[1] for call in write.call_args_list] == [b'<\x02\x00\x0c\x0a', b'\x00\x0c\x0a']


def test_default_never_opens_serial(capsys):
    with patch.object(MODULE.sys, 'platform', 'linux'), patch.object(MODULE.os, 'geteuid', return_value=0), \
         patch.object(MODULE.INVENTORY, 'inspect_usb', return_value=(Path('/dev/ttyACM0'), (1, 2, 3))), \
         patch.object(MODULE.INVENTORY, 'no_other_openers'), patch.object(MODULE, 'connection') as connection:
        MODULE.main([])
    connection.assert_not_called()
    assert json.loads(capsys.readouterr().out)['result']['queried'] is False


@pytest.mark.parametrize('arguments', [['--apply'], ['--query'], ['--apply', '--consent', MODULE.CONSENT],
                                     ['--apply', '--operator-reviewed-open-close-and-stopped-producers', '--consent', 'wrong']])
def test_consent_before_usb(arguments):
    with patch.object(MODULE.sys, 'platform', 'linux'), patch.object(MODULE.os, 'geteuid', return_value=0), \
         patch.object(MODULE.INVENTORY, 'inspect_usb') as inspect, pytest.raises(MODULE.INVENTORY.InventoryRefused):
        MODULE.main(arguments)
    inspect.assert_not_called()


def test_competing_owner_prevents_open():
    with patch.object(MODULE.INVENTORY, 'inspect_usb', return_value=(Path('/dev/ttyACM0'), (1, 2, 3))), \
         patch.object(MODULE.INVENTORY, 'no_other_openers', side_effect=RuntimeError('busy')), \
         patch.object(MODULE.os, 'open') as opener, pytest.raises(RuntimeError):
        with MODULE.connection((1, 2, 3)):
            pass
    opener.assert_not_called()


def test_usb_change_prevents_open():
    with patch.object(MODULE.INVENTORY, 'inspect_usb', return_value=(Path('/dev/ttyACM0'), (1, 2, 4))), \
         patch.object(MODULE.os, 'open') as opener, pytest.raises(MODULE.INVENTORY.InventoryRefused):
        with MODULE.connection((1, 2, 3)):
            pass
    opener.assert_not_called()


@pytest.fixture
def trusted_test_journal(tmp_path, monkeypatch):
    directory = tmp_path.resolve() / 'journal'
    directory.mkdir(mode=0o700)
    original = os.fstat

    def metadata(descriptor):
        original(descriptor)
        return SimpleNamespace(st_uid=os.geteuid(), st_mode=stat.S_IFDIR | 0o700)

    monkeypatch.setattr(MODULE.os, 'fstat', metadata)
    return directory / 'attempt.jsonl'


def test_exclusive_journal_survives_failure_and_blocks_rerun(trusted_test_journal):
    path = trusted_test_journal
    with pytest.raises(TimeoutError):
        with MODULE.journal(path) as append:
            append(dict(event='setter_intent', outcome='unknown_until_verified'))
            raise TimeoutError()
    original = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        with MODULE.journal(path):
            pytest.fail('existing journal accepted')
    assert path.read_bytes() == original


def test_journal_fsync_before_admission(trusted_test_journal):
    with patch.object(MODULE.os, 'fsync', wraps=os.fsync) as sync:
        with MODULE.journal(trusted_test_journal) as append:
            assert sync.call_count == 2
            append(dict(event='prepared'))
            assert sync.call_count == 3


def test_journal_symlink_refused(trusted_test_journal):
    target = trusted_test_journal.with_name('other')
    target.write_text('preserved')
    trusted_test_journal.symlink_to(target)
    with pytest.raises(FileExistsError):
        with MODULE.journal(trusted_test_journal):
            pytest.fail('symlink accepted')
    assert target.read_text() == 'preserved'


def test_unsafe_journal_ancestor_refused(tmp_path):
    with patch.object(MODULE.os, 'fstat', return_value=SimpleNamespace(st_uid=os.geteuid(), st_mode=0o40777)), \
         pytest.raises(MODULE.INVENTORY.InventoryRefused, match='ancestor'):
        with MODULE.journal(tmp_path.resolve() / 'attempt.jsonl'):
            pass


@pytest.mark.parametrize('error', [RuntimeError('PIN!'), OSError(13, 'PIN!')])
def test_redacted_error(error, capsys):
    with patch.object(MODULE, 'main', side_effect=error):
        assert MODULE.entrypoint() == 1
    result = capsys.readouterr()
    assert result.out == '' and 'PIN!' not in result.err
    assert 'preserve_journal_no_automatic_retry' in result.err


def test_query_no_journal_no_setters(capsys):
    radio = Radio()

    @contextmanager
    def connection(expected):
        yield radio

    with patch.object(MODULE.sys, 'platform', 'linux'), patch.object(MODULE.os, 'geteuid', return_value=0), \
         patch.object(MODULE.INVENTORY, 'inspect_usb', return_value=(Path('/dev/ttyACM0'), (1, 2, 3))), \
         patch.object(MODULE.INVENTORY, 'no_other_openers'), patch.object(MODULE, 'connection', connection), \
         patch.object(MODULE, 'journal') as journal:
        MODULE.main(['--query', '--operator-reviewed-open-close-and-stopped-producers'])
    journal.assert_not_called()
    assert radio.calls == [MODULE.QUERY, MODULE.APP_START]
    captured = capsys.readouterr().out
    assert 'PIN!' not in captured
    assert json.loads(captured)['result']['repeat'] == 1


def test_apply_journal_precedes_connection_completion_follows_close(capsys):
    events = []
    radio = Radio()

    @contextmanager
    def journal():
        events.append('journal_exclusive_durable')
        yield events.append

    @contextmanager
    def connection(expected):
        assert events == ['journal_exclusive_durable']
        yield radio
        events.append('connection_closed_verified')

    with patch.object(MODULE.sys, 'platform', 'linux'), patch.object(MODULE.os, 'geteuid', return_value=0), \
         patch.object(MODULE.INVENTORY, 'inspect_usb', return_value=(Path('/dev/ttyACM0'), (1, 2, 3))), \
         patch.object(MODULE.INVENTORY, 'no_other_openers'), patch.object(MODULE, 'connection', connection), \
         patch.object(MODULE, 'journal', journal):
        MODULE.main(['--apply', '--operator-reviewed-open-close-and-stopped-producers', '--consent', MODULE.CONSENT])
    assert events[-2] == 'connection_closed_verified'
    assert events[-1]['event'] == 'complete'
    assert json.loads(capsys.readouterr().out)['radio_silence_verified'] is False


def test_pending_journal_blocks_apply_before_serial():
    with patch.object(MODULE.sys, 'platform', 'linux'), patch.object(MODULE.os, 'geteuid', return_value=0), \
         patch.object(MODULE.INVENTORY, 'inspect_usb', return_value=(Path('/dev/ttyACM0'), (1, 2, 3))), \
         patch.object(MODULE.INVENTORY, 'no_other_openers'), patch.object(MODULE, 'connection') as connection, \
         patch.object(MODULE, 'journal', side_effect=FileExistsError), pytest.raises(FileExistsError):
        MODULE.main(['--apply', '--operator-reviewed-open-close-and-stopped-producers', '--consent', MODULE.CONSENT])
    connection.assert_not_called()


@pytest.mark.parametrize('header', [b'<\x01\x00', b'>\x00\x00', b'>\xb1\x00'])
def test_bad_frame_poisoned(header):
    exchange = MODULE.Exchange(42, lambda: None)
    with patch.object(MODULE.select, 'select', side_effect=[([], [], []), ([], [42], [])]), \
         patch.object(MODULE.os, 'write', return_value=5), \
         patch.object(MODULE.INVENTORY, 'read_exact', return_value=header), pytest.raises(MODULE.INVENTORY.InventoryRefused):
        exchange(MODULE.POWER)
    assert exchange.poisoned


def test_timeout_poisoned_without_retry():
    exchange = MODULE.Exchange(42, lambda: None)
    with patch.object(MODULE.select, 'select', side_effect=[([], [], []), ([], [42], [])]), \
         patch.object(MODULE.os, 'write', return_value=5) as write, \
         patch.object(MODULE.INVENTORY, 'read_exact', side_effect=TimeoutError), pytest.raises(TimeoutError):
        exchange(MODULE.POWER)
    assert exchange.poisoned and write.call_count == 1


def test_unsolicited_data_refused_without_flush_or_write():
    exchange = MODULE.Exchange(42, lambda: None)
    with patch.object(MODULE.select, 'select', return_value=([42], [], [])), \
         patch.object(MODULE.os, 'write') as write, pytest.raises(MODULE.INVENTORY.InventoryRefused):
        exchange(MODULE.QUERY)
    assert exchange.poisoned
    write.assert_not_called()
