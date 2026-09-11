from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_activate_fresh_display import DISPLAY, ROOT, backend, module


COLLECT = module('collect_display_tests', 'scripts/lantern/collect-fresh-display-inputs.py')


def arrange(runner, monkeypatch):
    def construct(expected):
        runner.expected = expected
        return runner
    monkeypatch.setattr(DISPLAY, 'Native', construct)
    monkeypatch.setattr(DISPLAY, 'parents', lambda path: None)
    monkeypatch.setattr(Path, 'read_text', lambda path: '00000000-0000-0000-0000-000000000001')
    runner.process = Mock(return_value={'pid': 101, 'start_ticks': 123,
        'invocation': 'invocation', 'image_sha256': 'hash'})
    return {'schema': 'sage.lantern.display-reviewed-inventory.v1', 'unit_id': 'unit-b',
        'files': {}, 'auth_dropins': {}}


def test_collector_uses_real_check_and_no_mutating_commands(backend, monkeypatch):
    runner, properties, current = backend
    inventory = arrange(runner, monkeypatch)
    result = COLLECT.collect(DISPLAY, inventory, '00000000-0000-0000-0000-000000000002')
    assert result['expected']['session'] == DISPLAY.session_public(current)
    assert result['expected']['unit_id'] == 'unit-b'
    assert result['expected']['auth_process']['pid'] == 101
    assert result['expected_sha256'] == DISPLAY.sha(DISPLAY.encoded(result['expected']))
    assert result['nonblanking_preflight'] is True
    assert result['activated'] is result['boot_enabled'] is result['physical_verified'] is False
    assert runner.native.check_x11.call_count == 2
    assert all(call.args[0] == 'show' for call in runner.command.call_args_list)


def test_collector_refuses_session_change_between_queries(backend, monkeypatch):
    runner, properties, current = backend
    inventory = arrange(runner, monkeypatch)
    runner.native.check_x11.side_effect = [current, replace(current, session_id='93')]
    with pytest.raises(ValueError):
        COLLECT.collect(DISPLAY, inventory, 'attempt')


def test_collector_does_not_claim_failed_setup(backend, monkeypatch):
    runner, properties, current = backend
    inventory = arrange(runner, monkeypatch)
    runner.setup.side_effect = ValueError()
    with pytest.raises(ValueError):
        COLLECT.collect(DISPLAY, inventory, 'attempt')


def test_activator_is_exact_reviewed_source(monkeypatch):
    source = (ROOT / 'scripts/lantern/activate-fresh-display.py').read_bytes()
    assert DISPLAY.sha(source) == COLLECT.ACTIVATOR_SHA256
    monkeypatch.setattr(COLLECT, 'trusted', lambda path: source + b'\n')
    with pytest.raises(ValueError):
        COLLECT.load_activator('/root/activator.py')


def test_inventory_cannot_request_extra_probe(backend, monkeypatch):
    runner, properties, current = backend
    inventory = arrange(runner, monkeypatch)
    inventory['command'] = 'arbitrary'
    with pytest.raises(ValueError):
        COLLECT.collect(DISPLAY, inventory, 'attempt')
