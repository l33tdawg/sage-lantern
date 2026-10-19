import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/lantern' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def output_mode(monkeypatch):
    """cf-output-mode.py needs Mutter's Gio/GLib; stub them for the pure helper."""
    gi = SimpleNamespace(require_version=lambda *args, **kwargs: None)
    repository = SimpleNamespace(
        Gio=SimpleNamespace(BusType=SimpleNamespace(SESSION=1),
                            DBusCallFlags=SimpleNamespace(NONE=0)),
        GLib=SimpleNamespace(Variant=lambda *args, **kwargs: None),
    )
    monkeypatch.setitem(sys.modules, 'gi', gi)
    monkeypatch.setitem(sys.modules, 'gi.repository', repository)
    return load('cf_output_mode', 'cf-output-mode.py')


def test_logical_monitor_tuple_preserves_everything_but_transform_and_scale(output_mode):
    state = dict(serial=3, x=0, y=0, scale=1.6666666, transform=0, primary=True,
                 connector='HDMI-2', mode_id='720x1560@59.987', monitors=[], )
    assert output_mode.monitors_tuple(state, transform=0, scale=1.0) == [
        (0, 0, 1.0, 0, True, [('HDMI-2', '720x1560@59.987', {})])]
    assert output_mode.monitors_tuple(state, transform=1, scale=1.5) == [
        (0, 0, 1.5, 1, True, [('HDMI-2', '720x1560@59.987', {})])]


def test_scale_outside_the_reviewed_range_is_refused(output_mode, capsys, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['cf-output-mode.py', 'normal', '--scale', '9'])
    with pytest.raises(SystemExit) as error:
        output_mode.main()
    assert error.value.code == 2
    assert 'between 0.5 and 4.0' in capsys.readouterr().err


def test_transform_map_is_the_mutter_enum():
    text = (ROOT / 'scripts/lantern/cf-output-mode.py').read_text()
    assert "TRANSFORMS = {'normal': 0, 'rotate-90': 1, 'rotate-180': 2, 'rotate-270': 3}" in text
    # The applied configuration always goes through the reviewed tuple helper.
    assert 'logical_monitors = monitors_tuple(state, transform=wanted, scale=scale)' in text
    assert "'--scale'" in text


@pytest.fixture
def supervisor():
    return load('cf_mode_supervisor', 'cf-mode-supervisor.py')


def test_supervisor_defaults_to_panel_scale_one(supervisor):
    assert supervisor.DEFAULTS['panel_scale'] == '1.0'
    assert supervisor.panel_scale({'panel_scale': '1.0'}) == 1.0
    assert supervisor.panel_scale({}) == 1.0
    for bad in ('fractional', '0.1', '9', 'None'):
        assert supervisor.panel_scale({'panel_scale': bad}) == 1.0


def test_supervisor_config_accepts_a_numeric_scale_and_keeps_transforms(supervisor, tmp_path, monkeypatch):
    config = tmp_path / 'display-mode.json'
    config.write_text(json.dumps({'kiosk_transform': 'rotate-90', 'desktop_transform': 'rotate-270',
                                  'panel_scale': 1.0}))
    monkeypatch.setattr(supervisor, 'CONFIG', config)
    values = supervisor.config()
    assert values == {'kiosk_transform': 'rotate-90', 'desktop_transform': 'rotate-270',
                      'panel_scale': '1.0'}
    assert supervisor.panel_scale(values) == 1.0


def test_apply_transform_passes_the_reviewed_scale(supervisor, monkeypatch):
    calls = []
    def run(argv, **options):
        calls.append((argv, options))
        return SimpleNamespace(returncode=0, stdout='applied normal scale=1 on HDMI-2', stderr='')
    monkeypatch.setattr(supervisor.subprocess, 'run', run)
    supervisor.apply_transform('normal', 1.0)
    assert calls[0][0] == [supervisor.OUTPUT_MODE, 'normal', '--scale', '1']
    assert calls[0][1]['timeout'] == 20


def test_native_guard_transition_requires_positive_concealment_ack(supervisor, monkeypatch):
    calls = []
    def run(argv, **options):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='(true,)')
    monkeypatch.setattr(supervisor.subprocess, 'run', run)
    assert supervisor.native_guard_mode('desktop', change=True)
    assert calls[-1][-2:] == ['org.sage.Lantern.DesktopGuard1.SetMode', 'desktop']
    assert supervisor.native_guard_mode('kiosk', change=False)
    assert calls[-1][-2:] == ['org.sage.Lantern.DesktopGuard1.KeepMode', 'kiosk']
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **k:
        SimpleNamespace(returncode=0, stdout='(false,)'))
    assert not supervisor.native_guard_mode('desktop', change=True)
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **k:
        SimpleNamespace(returncode=1, stdout='(true,)'))
    assert not supervisor.native_guard_mode('desktop', change=True)
