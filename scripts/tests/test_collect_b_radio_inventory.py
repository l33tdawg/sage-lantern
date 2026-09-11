import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


SPEC = importlib.util.spec_from_file_location('radio_inventory', Path(__file__).parents[1] / 'lantern/collect-b-radio-inventory.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def responses():
    info = bytearray(82)
    info[:2] = bytes((13, 13))
    info[60:60 + len(MODULE.FIRMWARE)] = MODULE.FIRMWARE.encode()
    identity = bytearray(58)
    identity[0] = 5
    identity[4:36] = bytes(range(32))
    return bytes(info), bytes(identity)


def test_only_two_public_queries():
    with patch.object(MODULE, 'exchange', side_effect=responses()) as exchange:
        result = MODULE.query(42)
    assert [call.args[1] for call in exchange.call_args_list] == [bytes((22, 13)), b'\x01' + bytes(7) + b'Lantern']
    assert result['unit_id'] == 'unit-b'
    assert result['radio_identity']['public_key'] == bytes(range(32)).hex()
    assert result['usb']['serial'] == '44:1B:F6:62:56:54'


@pytest.mark.parametrize('command', [b'\x02', b'\x03', b'\x14', b'\x17', b''])
def test_forbidden_commands_never_write(command):
    with patch.object(MODULE.os, 'write') as write:
        with pytest.raises(RuntimeError, match='forbidden'):
            MODULE.exchange(42, command, 5, 0)
        write.assert_not_called()


def test_unreviewed_firmware_stops_before_app_start():
    info, _ = responses()
    with patch.object(MODULE, 'exchange', return_value=info[:60] + bytes(22)) as exchange:
        with pytest.raises(RuntimeError, match='firmware'):
            MODULE.query(42)
        assert exchange.call_count == 1


def test_empty_public_key_refused():
    info, identity = responses()
    identity = identity[:4] + bytes(32) + identity[36:]
    with patch.object(MODULE, 'exchange', side_effect=[info, identity]):
        with pytest.raises(RuntimeError, match='public identity'):
            MODULE.query(42)


def test_default_never_opens_serial():
    with patch.object(MODULE.sys, 'argv', ['collector']), patch.object(MODULE.sys, 'platform', 'linux'), \
         patch.object(MODULE.os, 'geteuid', return_value=0), \
         patch.object(MODULE, 'inspect_usb', return_value=(Path('/dev/ttyACM0'), (1, 2, 3))), \
         patch.object(MODULE, 'no_other_openers'), patch.object(MODULE, 'collect') as collect:
        MODULE.main()
        collect.assert_not_called()


def test_busy_owner_prevents_open():
    with patch.object(MODULE, 'inspect_usb', return_value=(Path('/dev/ttyACM0'), (1, 2, 3))), \
         patch.object(MODULE, 'no_other_openers', side_effect=RuntimeError('competing serial opener')), \
         patch.object(MODULE.os, 'open') as opener:
        with pytest.raises(RuntimeError, match='competing'):
            MODULE.collect((1, 2, 3))
        opener.assert_not_called()


@pytest.mark.parametrize('error,reason', [
    (PermissionError(13, 'private-path-secret'), 'os_error_13'),
    (RuntimeError('secret-bytes'), 'internal_error'),
    (MODULE.InventoryRefused('preexisting serial data; no flush/retry'), 'preexisting_serial_data_no_flush_retry'),
])
def test_safe_refusal(error, reason, capsys):
    with patch.object(MODULE, 'main', side_effect=error):
        assert MODULE.entrypoint() == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert reason in captured.err
    assert 'secret' not in captured.err


@pytest.mark.parametrize('change,reason', [('length', 'response length'), ('protocol', 'protocol'), ('version', 'version')])
def test_distinct_firmware_refusals(change, reason):
    info, _ = responses()
    if change == 'length':
        info = info[:-1]
    elif change == 'protocol':
        info = info[:1] + b'\x0c' + info[2:]
    else:
        info = info[:60] + bytes(22)
    with patch.object(MODULE, 'exchange', return_value=info):
        with pytest.raises(MODULE.InventoryRefused, match=reason):
            MODULE.query(42)
