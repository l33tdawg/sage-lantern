import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location('kiosk_session',
                                              ROOT / 'scripts/lantern/configure-kiosk-session-settings.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeGsettings:
    """A session bus stand-in: one dict of current values, plus a write log."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []

    def __call__(self, user, arguments):
        if arguments[0] == 'get':
            value = self.values.get(f'{arguments[1]} {arguments[2]}')
            return (0, value.encode()) if value is not None else (1, b'')
        self.writes.append((user, arguments[1], arguments[2], arguments[3]))
        self.values[f'{arguments[1]} {arguments[2]}'] = arguments[3]
        return 0, b''


REVIEWED = {
    'org.gnome.desktop.lockdown disable-lock-screen': 'false',
    'org.gnome.settings-daemon.plugins.media-keys screensaver': '@as []',
    'org.gnome.desktop.screensaver lock-enabled': 'true',
    'org.gnome.desktop.session idle-delay': 'uint32 0',
    'org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type': "'nothing'",
    'org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout': 'uint32 0',
    'org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type': "'nothing'",
    'org.gnome.settings-daemon.plugins.power sleep-inactive-battery-timeout': 'uint32 0',
}


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'session_environment', lambda uid: {'XDG_RUNTIME_DIR': str(tmp_path)})
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda name: type('P', (), {'pw_uid': 1000})())
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    return FakeGsettings()


def test_check_passes_for_a_d022_compatible_kiosk_session(session, monkeypatch, capsys):
    """Locking must be possible (D-022) and never accidental (the Sep 30 trap).

    gsettings prints typed values; the check must compare meaning, not text.
    """
    session.values.update(REVIEWED)
    assert MODULE.main(['--account', 'sage'], run=session) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['ok'] is True and report['wrong'] == [] and report['changed'] == []


def test_automatic_suspend_from_the_session_is_a_named_refusal(session, monkeypatch, capsys):
    """The lane's controller is the only reviewed suspend source.

    The shipped sleep drop-in allows systemd suspend so the controller can
    suspend; GNOME's default fifteen-minute idle suspend would reach systemd
    unmanaged, so the check must name it instead of silently accepting it.
    """
    session.values.update(REVIEWED)
    session.values['org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type'] = "'suspend'"
    session.values['org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout'] = 'uint32 900'
    assert MODULE.main(['--account', 'sage'], run=session) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['wrong'] == ['org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type',
                               'org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout']
    assert MODULE.main(['--account', 'sage', '--apply'], run=session) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['changed'] == ['org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type',
                                 'org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout']
    assert report['ok'] is True


def test_normalize_understands_gsettings_type_annotations():
    assert MODULE.normalize('@as []') == '[]'
    assert MODULE.normalize('uint32 0') == '0'
    assert MODULE.normalize('true') == 'true'
    assert MODULE.normalize('false') == 'false'
    assert MODULE.normalize('@a{sv} {}') == '{}'
    assert MODULE.normalize(None) is None


def test_apply_writes_only_what_differs_and_verifies_by_read_back(session, monkeypatch, capsys):
    session.values.update(REVIEWED)
    session.values['org.gnome.desktop.lockdown disable-lock-screen'] = 'true'
    session.values['org.gnome.settings-daemon.plugins.media-keys screensaver'] = "['<Super>l']"
    assert MODULE.main(['--account', 'sage', '--apply'], run=session) == 0
    report = json.loads(capsys.readouterr().out)
    assert sorted(report['changed']) == ['org.gnome.desktop.lockdown disable-lock-screen',
                                         'org.gnome.settings-daemon.plugins.media-keys screensaver']
    assert report['ok'] is True
    assert len(session.writes) == 2


def test_check_fails_when_the_gate_could_not_lock(session, monkeypatch, capsys):
    """Both halves of the old availability workaround are named as wrong now.

    disable-lock-screen=true makes the lock request a no-op and lock-enabled=false
    makes logind ignore it, so a D-022 desktop entry could not be gated at all.
    """
    session.values.update(REVIEWED)
    session.values['org.gnome.desktop.lockdown disable-lock-screen'] = 'true'
    session.values['org.gnome.desktop.screensaver lock-enabled'] = 'false'
    session.values['org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type'] = "'suspend'"
    session.values['org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout'] = 'uint32 900'
    assert MODULE.main(['--account', 'sage'], run=session) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['ok'] is False
    assert 'org.gnome.desktop.lockdown disable-lock-screen' in report['wrong']
    assert 'org.gnome.desktop.screensaver lock-enabled' in report['wrong']
    assert 'org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type' in report['wrong']


def test_missing_operator_session_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda name: type('P', (), {'pw_uid': 1000})())
    monkeypatch.setattr(MODULE, 'session_environment', lambda uid: (_ for _ in ()).throw(SystemExit('operator_session_unavailable')))
    with pytest.raises(SystemExit) as refusal:
        MODULE.main(['--account', 'sage'])
    assert 'operator_session_unavailable' in str(refusal.value)
    with pytest.raises(SystemExit) as unknown:
        monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda name: (_ for _ in ()).throw(KeyError(name)))
        MODULE.main(['--account', 'nobody-here'])
    assert 'operator_account_unknown' in str(unknown.value)
