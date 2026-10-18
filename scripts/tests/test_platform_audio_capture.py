import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'audio_capture', ROOT / 'scripts/lantern/set-platform-audio-capture.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

UNIT = ROOT / 'software/systemd/sage-lantern-audio-capture.service'
INSTALLER = ROOT / 'scripts/lantern/install-platform-audio-capture-local.sh'

ENUMS = {'Left PGA Mux', 'Right PGA Mux', 'ADC Data Select', 'Differential Mux'}
SWITCHES = {'Main Mic', 'Headset Mic', 'Capture Mute'}
STEREO = {'Capture Digital'}

# What the board boots with on C: a differential line input at 0 dB gain.
BOOT_DEFAULT = {
    'Left PGA Mux': 'DifferentialL',
    'Right PGA Mux': 'DifferentialR',
    'Differential Mux': 'Line 2',
    'ADC Data Select': 'Left Right',
    'Main Mic': 'on',
    'Headset Mic': 'off',
    'Capture Digital': '192',
    'Left Channel': '0',
    'Right Channel': '0',
    'Capture Mute': 'off',
}
REVIEWED = dict(MODULE.SETTINGS)


def render(control, value):
    if control in ENUMS:
        return "  Item0: '%s'\n" % value
    if control in SWITCHES:
        return '  Mono: Playback [%s]\n' % value
    if control in STEREO:
        return ('  Limits: 0 - 192\n  Front Left: %s [100%%] [0.00dB]\n'
                '  Front Right: %s [100%%] [0.00dB]\n' % (value, value))
    return '  Limits: 0 - 8\n  Mono: Capture %s [100%%] [%0.2fdB]\n' % (value, int(value) * 3.0)


class FakeAmixer:
    def __init__(self, state=None):
        self.state = dict(BOOT_DEFAULT if state is None else state)
        self.calls = []

    def __call__(self, argv, **options):
        self.calls.append((list(argv), options))
        assert argv[0] == MODULE.AMIXER
        assert argv[1:3] == ['-c', 'rockchipes8388']
        if argv[3] == 'info':
            return SimpleResult(0, 'Card rockchipes8388\n')
        action, control = argv[3], argv[4]
        if action == 'sget':
            return SimpleResult(0, render(control, self.state[control]))
        assert action == 'sset'
        self.state[control] = argv[5]
        return SimpleResult(0, '')

    @property
    def sset(self):
        return [call[0][4] for call in self.calls if call[0][3] == 'sset']


class SimpleResult:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def test_settings_are_the_reviewed_capture_path_only():
    assert MODULE.AMIXER == '/usr/bin/amixer'
    assert MODULE.CARD == 'rockchipes8388'
    assert MODULE.SETTINGS == (
        ('Left PGA Mux', 'Line 2L'),
        ('Right PGA Mux', 'Line 2R'),
        ('ADC Data Select', 'Left Right'),
        ('Main Mic', 'on'),
        ('Headset Mic', 'off'),
        ('Capture Digital', '192'),
        ('Left Channel', '8'),
        ('Right Channel', '8'),
        ('Capture Mute', 'off'),
    )
    for control, _ in MODULE.SETTINGS:
        assert control not in {'Speaker', 'Headphone', 'PCM', 'hp switch', 'spk switch'}


def test_apply_corrects_the_boot_default_in_reviewed_order():
    fake = FakeAmixer()
    observed = MODULE.apply(fake, 'rockchipes8388')
    assert {control: fake.state[control] for control, _ in MODULE.SETTINGS} == REVIEWED
    for control, expected in MODULE.SETTINGS:
        values = observed[control]
        values = values if isinstance(values, list) else [values]
        assert all(value == expected for value in values), control
    assert fake.sset == [control for control, _ in MODULE.SETTINGS]
    assert fake.calls[-1][0][4] == 'Capture Mute'
    assert all(options.get('check') is False for _, options in fake.calls)


def test_verify_rejects_any_wrong_channel_or_item():
    def wrong_value(control, expected):
        if control in SWITCHES:
            return 'off' if expected == 'on' else 'on'
        if control in STEREO:
            return '96'
        if control.endswith('Channel'):
            return '4'
        return 'Line 1L' if control == 'Left PGA Mux' else 'Line 1R'

    for control, expected in MODULE.SETTINGS:
        state = dict(REVIEWED)
        state[control] = wrong_value(control, expected)
        fake = FakeAmixer(state)
        with pytest.raises(MODULE.CapturePathError, match='capture_path_mismatch:' + control):
            MODULE.verify(fake, 'rockchipes8388')
        assert fake.sset == []


def test_parse_values_reads_every_amixer_value_shape():
    assert MODULE.parse_values(
        "Simple mixer control 'Left PGA Mux',0\n"
        '  Capabilities: enum\n'
        "  Items: 'Line 1L' 'Line 2L' 'DifferentialL'\n"
        "  Item0: 'Line 2L'\n") == ['Line 2L']
    assert MODULE.parse_values('  Mono: Playback [off]\n') == ['off']
    assert MODULE.parse_values('Simple mixer control \'Capture Mute\',0\n'
                               '  Capabilities: pswitch pswitch-joined\n'
                               '  Mono: Playback [on]\n') == ['on']
    assert MODULE.parse_values('Simple mixer control \'Left Channel\',0\n'
                               '  Limits: Capture 0 - 8\n'
                               '  Mono: Capture 8 [100%] [24.00dB]\n') == ['8']
    assert MODULE.parse_values("Simple mixer control 'Capture Digital',0\n"
                               '  Limits: 0 - 192\n'
                               '  Front Left: 192 [100%] [0.00dB]\n'
                               '  Front Right: 192 [100%] [0.00dB]\n') == ['192', '192']
    with pytest.raises(MODULE.CapturePathError):
        MODULE.parse_values('  weird output with no value\n')


def test_amixer_failure_is_not_verified_or_applied():
    def failing(argv, **options):
        return SimpleResult(1, '')
    with pytest.raises(MODULE.CapturePathError, match='amixer_failed'):
        MODULE.verify(failing, 'rockchipes8388')
    with pytest.raises(MODULE.CapturePathError, match='amixer_failed'):
        MODULE.apply(failing, 'rockchipes8388')


def test_wait_for_card_is_bounded_and_retries():
    attempts = []
    def missing(argv, **options):
        attempts.append(argv[3])
        return SimpleResult(1, '') if len(attempts) < 3 else SimpleResult(0, 'Card\n')
    MODULE.wait_for_card(missing, 'rockchipes8388', 5, clock=lambda: 0.0, sleep=lambda seconds: None)
    assert len(attempts) == 3

    def never(argv, **options):
        return SimpleResult(1, '')
    now = [0.0]
    def clock():
        now[0] += 1.0
        return now[0]
    with pytest.raises(MODULE.CapturePathError, match='sound_card_unavailable'):
        MODULE.wait_for_card(never, 'rockchipes8388', 2, clock=clock, sleep=lambda seconds: None)


def test_check_mode_never_sets_a_control(monkeypatch, capsys):
    fake = FakeAmixer(REVIEWED)
    monkeypatch.setattr(MODULE.subprocess, 'run', fake)
    assert MODULE.main(['--check']) == 0
    assert fake.sset == []
    assert '"action": "verified"' in capsys.readouterr().out

    wrong = FakeAmixer({**REVIEWED, 'Right PGA Mux': 'Line 1R'})
    monkeypatch.setattr(MODULE.subprocess, 'run', wrong)
    assert MODULE.main(['--check']) == 1
    assert wrong.sset == []
    assert '"action": "refused"' in capsys.readouterr().out


def test_apply_mode_corrects_a_wrong_path(monkeypatch):
    fake = FakeAmixer()
    monkeypatch.setattr(MODULE.subprocess, 'run', fake)
    assert MODULE.main(['--card', 'rockchipes8388', '--wait-seconds', '0']) == 0
    assert {control: fake.state[control] for control, _ in MODULE.SETTINGS} == REVIEWED
    assert fake.sset == [control for control, _ in MODULE.SETTINGS]


def test_unit_is_a_verified_oneshot_with_the_reviewed_values():
    text = UNIT.read_text()
    for directive in (
        'Type=oneshot',
        'RemainAfterExit=yes',
        'ConditionPathExists=/dev/snd/by-path/platform-es8388-sound',
        'ExecStart=/opt/sage-lantern/venv/bin/python -I -B '
        '/usr/local/libexec/sage-lantern-set-platform-audio-capture '
        '--card rockchipes8388 --wait-seconds 20',
        'WantedBy=multi-user.target',
    ):
        assert directive in text
    for playback in ('Speaker', 'Headphone', 'PCM', 'aplay'):
        assert playback not in text


def test_installer_installs_enables_and_verifies_without_guessing():
    text = INSTALLER.read_text()
    assert '/dev/snd/by-path/platform-es8388-sound' in text
    assert 'systemd-analyze verify' in text
    assert 'systemctl enable "$service"' in text
    assert 'systemctl start "$service"' in text
    assert '--check' in text
    assert 'set-platform-audio-capture.py' in text
    for playback in ('aplay', 'Speaker', 'Headphone'):
        assert playback not in text
