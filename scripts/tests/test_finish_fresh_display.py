import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('display_finish',
    Path(__file__).resolve().parents[1] / 'lantern/finish-fresh-display.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('fault', [None, 'check', 'release', 'start'])
def test_only_display_mutations_and_failures_retained(fault):
    events = []
    def action(name):
        events.append(name)
        if fault == name:
            raise ValueError
    def release(service, record):
        assert service == MODULE.DISPLAY
        action('release')
    def start(backend, service):
        assert service == MODULE.DISPLAY
        action('start')
    backend = SimpleNamespace(current=lambda: action('check'), release=release)
    finish = SimpleNamespace(bounded_start=start)
    if fault:
        with pytest.raises(ValueError):
            MODULE.transition(backend, finish, events.append)
        assert events[-1] == fault and 'start_display_done' not in events
    else:
        result = MODULE.transition(backend, finish, events.append)
        assert events.count('start') == 1 and events.index('release') < events.index('start')
        assert result['existing_services_restarted'] is False
        assert result['cryptographic_lock_verified'] is False


def test_error_has_frames_but_no_message_or_locals(capsys):
    try:
        private_value = 'do-not-print'
        raise ValueError(private_value)
    except ValueError as error:
        MODULE.failure(error)
    raw = capsys.readouterr().out
    assert 'do-not-print' not in raw and 'private_value' not in raw
    value = json.loads(raw)
    assert value['error_type'] == 'ValueError' and value['automatic_retry'] is False
    assert all(set(frame) == {'file', 'line', 'function'} for frame in value['frames'])


def test_duplicate_input_rejected():
    with pytest.raises(ValueError):
        MODULE.decode(b'{"camera_checkpoint":1,"camera_checkpoint":2}')
