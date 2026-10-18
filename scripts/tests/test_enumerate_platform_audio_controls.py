"""The read-only control inventory for wiring a codec output path.

The sibling capture script can hard-code its nine control names because they were
read off real hardware first. Nothing has read the C–F codec's *output* controls,
so this enumerator prints what the card has instead of assuming. These tests use a
fake `amixer`, so no sound card is needed and no control is ever set.
"""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'audio_controls', ROOT / 'scripts/lantern/enumerate-platform-audio-controls.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SimpleResult:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


# A plausible card: the real input controls the capture script knows, plus an
# output side that nothing in this tree has ever configured.
CONTROLS = {
    'Left PGA Mux': "  Item0: 'Line 2L'\n",
    'Right PGA Mux': "  Item0: 'Line 2R'\n",
    'Main Mic': '  Mono: Playback [on]\n',
    'Capture Mute': '  Mono: Playback [off]\n',
    'Left Channel': '  Limits: 0 - 8\n  Mono: Capture 8 [100%] [24.00dB]\n',
    'Playback Digital': '  Limits: 0 - 192\n  Front Left: 160 [83%] [0.00dB]\n  Front Right: 160 [83%] [0.00dB]\n',
    'Headphone': '  Limits: 0 - 3\n  Mono: Playback 0 [0%] [-45.00dB] [off]\n',
    'Speaker': '  Mono: Playback [off]\n',
    'DAC Mux': "  Item0: 'IF1 DAC'\n",
    'Output Mixer': '  Mono: Playback [off]\n',
}


class FakeAmixer:
    """Answers scontrols/sget from CONTROLS and records every call."""

    def __init__(self, overrides=None, fail_on=None):
        self.overrides = dict(overrides or {})
        self.fail_on = set(fail_on or ())
        self.calls = []

    def __call__(self, argv, **options):
        self.calls.append(list(argv))
        if argv[0] != MODULE.AMIXER:
            raise AssertionError(f'unexpected binary {argv[0]}')
        action = argv[3]
        if action == 'scontrols':
            body = ''.join(f"simple control '{name}'\n" for name in CONTROLS)
            return SimpleResult(0, body)
        assert action == 'sget', f'this tool must never set a control (saw {action})'
        control = argv[4]
        if control in self.fail_on:
            return SimpleResult(1, 'amixer: Control default element write error\n')
        if control in self.overrides:
            return SimpleResult(0, self.overrides[control])
        return SimpleResult(0, CONTROLS[control])

    @property
    def set_calls(self):
        return [call for call in self.calls if call[3] == 'sset']


def names(rows):
    return [row['control'] for row in rows]


# --- the inventory ------------------------------------------------------

def test_it_reads_every_control_the_card_reports():
    rows = MODULE.inventory(FakeAmixer(), 'rockchipes8388')
    assert names(rows) == list(CONTROLS)


def test_operational_controls_are_named_output_and_microphone_controls_are_not():
    rows = {row['control']: row['kind'] for row in MODULE.inventory(FakeAmixer(), 'rockchipes8388')}
    for control in ('Playback Digital', 'Headphone', 'Speaker', 'DAC Mux', 'Output Mixer'):
        assert rows[control] == 'output', control
    for control in ('Left PGA Mux', 'Right PGA Mux', 'Main Mic', 'Capture Mute'):
        assert rows[control] == 'input', control
    # 'Left Channel' matches neither list, and saying so is more honest than
    # guessing which side of the codec it belongs to.
    assert rows['Left Channel'] == 'unknown'


def test_the_current_value_of_each_control_is_reported():
    rows = {row['control']: row['values'] for row in MODULE.inventory(FakeAmixer(), 'rockchipes8388')}
    assert rows['DAC Mux'] == ['IF1 DAC']
    assert rows['Playback Digital'] == ['160', '160']
    assert rows['Speaker'] == ['off']
    assert rows['Main Mic'] == ['on']


def test_an_unreadable_control_is_reported_rather_than_ending_the_inventory():
    rows = MODULE.inventory(FakeAmixer(fail_on={'Speaker'}), 'rockchipes8388')
    by_name = {row['control']: row for row in rows}
    assert by_name['Speaker']['state'] == 'unreadable'
    assert by_name['Speaker']['values'] is None
    # Everything after it still got read: one bad control is not a dead tool.
    assert by_name['Output Mixer']['state'] == 'read'


def test_a_card_that_reports_no_controls_is_refused():
    class Empty(FakeAmixer):
        def __call__(self, argv, **options):
            self.calls.append(list(argv))
            return SimpleResult(0, '')
    with pytest.raises(MODULE.EnumerateError, match='no_controls_reported'):
        MODULE.inventory(Empty(), 'rockchipes8388')


# --- it is read-only ----------------------------------------------------

def test_it_never_sets_a_control():
    fake = FakeAmixer()
    MODULE.inventory(fake, 'rockchipes8388')
    assert fake.set_calls == []


def test_the_only_amixer_verbs_used_are_scontrols_and_sget():
    fake = FakeAmixer()
    MODULE.inventory(fake, 'rockchipes8388')
    verbs = {call[3] for call in fake.calls}
    assert verbs == {'scontrols', 'sget'}


# --- command line -------------------------------------------------------

def test_json_output_is_parseable_and_counts_every_control(monkeypatch, capsys):
    monkeypatch.setattr(MODULE.subprocess, 'run', FakeAmixer())
    assert MODULE.main(['--card', 'rockchipes8388', '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['action'] == 'read'
    assert payload['total'] == len(CONTROLS)
    assert payload['card'] == 'rockchipes8388'


def test_only_output_filters_the_rows(monkeypatch, capsys):
    monkeypatch.setattr(MODULE.subprocess, 'run', FakeAmixer())
    assert MODULE.main(['--only', 'output', '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['controls'], 'the filter must not empty the list'
    assert all(row['kind'] == 'output' for row in payload['controls'])


def test_a_failure_is_reported_as_refused_with_a_exit_code(monkeypatch, capsys):
    class Broken(FakeAmixer):
        def __call__(self, argv, **options):
            return SimpleResult(1, '')
    monkeypatch.setattr(MODULE.subprocess, 'run', Broken())
    assert MODULE.main(['--card', 'rockchipes8388']) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload['action'] == 'refused'
    assert payload['reason'] == 'amixer_failed'


def test_it_warns_when_a_card_shows_no_output_controls(monkeypatch, capsys):
    """A card with no output side must not be given a blind output-path script."""
    class InputOnly(FakeAmixer):
        def __call__(self, argv, **options):
            self.calls.append(list(argv))
            if argv[3] == 'scontrols':
                return SimpleResult(0, "simple control 'Main Mic'\n")
            return SimpleResult(0, '  Mono: Playback [on]\n')
    monkeypatch.setattr(MODULE.subprocess, 'run', InputOnly())
    assert MODULE.main([]) == 0
    out = capsys.readouterr().out
    assert 'No output controls were found' in out
    assert 'configured blind' in out
