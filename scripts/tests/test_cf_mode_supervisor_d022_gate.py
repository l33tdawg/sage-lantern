"""D-022 gate: the session lock is confirmed before the desktop is exposed.

The five findings this covers came from an independent review of the deployed
supervisor, which locked *after* rotating and starting the desktop browser, never
read a confirmation, ignored a failed request, and fired a second attempt from a
detached timer. Everything here is inert: no unit, no session, no D-Bus.

The later review of the repaired source added the lifetime fence (leader and its
own start time), the property-option contract, the intent re-check and the
operator-binding validation; those cases live below with the same inert ports.
"""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    'mode_supervisor', ROOT / 'scripts/lantern/cf-mode-supervisor.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def session_properties(sid, uid='1000', seat='seat0', kind='user', stype='wayland',
                       remote='no', active='yes', locked='no'):
    return (f'Active={active}\nSeat={seat}\nClass={kind}\nType={stype}\nRemote={remote}\n'
            f'User={uid}\nLeader=900\nLockedHint={locked}\n')


def binding(lifetime='987654321'):
    """The session-3 binding exactly as the supervisor derives it.

    The lifetime is an explicit value, not a read of this host: off the unit
    there is no /proc/<pid>/stat to read, and the repaired supervisor refuses a
    fence it cannot time rather than admitting an empty one. A case that wants
    that refusal supplies an empty or unreadable lifetime deliberately.
    """
    return MODULE.SessionBinding('3', '1000', 'seat0', 'user', 'wayland', 'no', 'yes', '900',
                                 lifetime)


@pytest.fixture
def inert(monkeypatch):
    """Replace the session bus; the caller supplies each command's answer.

    The leader lifetime is pinned to a readable value as well: /proc/<pid>/stat
    only exists on the unit, and a fence it cannot time is now refused instead of
    admitting an empty string. A case that wants that refusal overrides this.
    """
    monkeypatch.setattr(MODULE, 'log', lambda message: None)
    monkeypatch.setattr(MODULE.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(MODULE, 'leader_lifetime', lambda leader: '987654321')


def test_the_bound_session_is_the_operator_on_the_expected_seat(inert, monkeypatch):
    listing = '7 1001 other seat9 tty7\n3 1000 sage seat0 tty2\n'

    def run(command, **_kwargs):
        if command[1] == 'list-sessions':
            return type('R', (), {'returncode': 0, 'stdout': listing, 'stderr': ''})()
        sid = command[2]
        other = session_properties(sid, uid='1001', seat='seat9') if sid == '7' \
            else session_properties(sid)
        return type('R', (), {'returncode': 0, 'stdout': other, 'stderr': ''})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    binding, reason = MODULE.graphical_session(dict(MODULE.BINDING_DEFAULTS))
    assert reason == 'bound' and binding.session_id == '3' and binding.uid == '1000'


def test_keyboard_alone_triggers_desktop_and_mouse_is_optional(monkeypatch):
    monkeypatch.setattr(MODULE, 'override_setting', lambda: '')
    assert MODULE.wanted_mode({}, True, False) == ('desktop', 'auto')
    assert MODULE.wanted_mode({}, True, True) == ('desktop', 'auto')
    assert MODULE.wanted_mode({}, False, True) == ('kiosk', 'auto')


def test_a_session_for_another_account_or_seat_is_not_bound(inert, monkeypatch):
    def run(command, **_kwargs):
        if command[1] == 'list-sessions':
            return type('R', (), {'returncode': 0, 'stdout': '7 1001 other seat9 tty7\n',
                                  'stderr': ''})()
        return type('R', (), {'returncode': 0,
                              'stdout': session_properties(command[2], uid='1001', seat='seat9'),
                              'stderr': ''})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    assert MODULE.graphical_session(dict(MODULE.BINDING_DEFAULTS)) == (None, 'no_bound_session')


def test_two_matching_sessions_are_refused_rather_than_picked(inert, monkeypatch):
    def run(command, **_kwargs):
        if command[1] == 'list-sessions':
            return type('R', (), {'returncode': 0, 'stdout': '3 1000 sage seat0 tty2\n'
                                                           '4 1000 sage seat0 tty3\n',
                                  'stderr': ''})()
        return type('R', (), {'returncode': 0, 'stdout': session_properties(command[2]),
                              'stderr': ''})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    assert MODULE.graphical_session(dict(MODULE.BINDING_DEFAULTS)) == (None, 'ambiguous_bound_session')


def test_exit_zero_without_a_locked_session_is_not_confirmation(inert, monkeypatch):
    """On C the request returned 0 while doing nothing, so 0 cannot be the gate."""
    gate = binding()
    requests = []

    def run(command, **_kwargs):
        if command[1] == 'lock-session':
            requests.append(command)
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        if command[1] == 'show-session':
            return type('R', (), {'returncode': 0,
                                  'stdout': session_properties('3', locked='no'), 'stderr': ''})()
        return type('R', (), {'returncode': 1, 'stdout': '', 'stderr': 'no bus'})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    result = MODULE.lock_and_confirm(gate, polls=3, intent=lambda: (True, True))
    assert result['confirmed'] is False and result['reason'] == 'locked_hint_not_set'
    assert result['locked_hint'] == 'no' and requests


def test_a_confirmed_lock_settles_the_gate_on_the_observed_hint(inert, monkeypatch):
    gate = binding()

    def run(command, **_kwargs):
        if command[1] == 'lock-session':
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        if command[1] == 'show-session':
            return type('R', (), {'returncode': 0,
                                  'stdout': session_properties('3', locked='yes'), 'stderr': ''})()
        return type('R', (), {'returncode': 0, 'stdout': 'b true\n', 'stderr': ''})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    result = MODULE.lock_and_confirm(gate, polls=3)
    assert result['confirmed'] is True and result['locked_hint'] == 'yes'


def test_a_session_that_changes_under_the_request_refuses_the_gate(inert, monkeypatch):
    gate = binding()

    def run(command, **_kwargs):
        if command[1] == 'lock-session':
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        if command[1] == 'show-session':
            return type('R', (), {'returncode': 0,
                                  'stdout': session_properties('3', locked='yes', seat='seat1'),
                                  'stderr': ''})()
        return type('R', (), {'returncode': 0, 'stdout': 'b true\n', 'stderr': ''})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    result = MODULE.lock_and_confirm(gate, polls=3)
    assert result['confirmed'] is False and result['reason'] == 'session_changed'


def test_every_requested_property_gets_its_own_option(inert, monkeypatch):
    """loginctl reads one argument per -p; the rest are session IDs, not names."""
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return type('R', (), {'returncode': 1, 'stdout': '', 'stderr': ''})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    MODULE.session_properties('3')
    expected = ['loginctl', 'show-session', '3']
    for name in MODULE.SESSION_PROPERTIES:
        expected += ['-p', name]
    assert commands == [expected]


def test_a_session_that_stops_being_active_refuses_the_gate(inert, monkeypatch):
    """Selection requires an active session; confirmation must keep requiring it."""
    gate = binding()

    def run(command, **_kwargs):
        if command[1] == 'lock-session':
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        if command[1] == 'show-session':
            return type('R', (), {'returncode': 0,
                                  'stdout': session_properties('3', active='no', locked='yes'),
                                  'stderr': ''})()
        return type('R', (), {'returncode': 0, 'stdout': 'b true\n', 'stderr': ''})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    result = MODULE.lock_and_confirm(gate, polls=3)
    assert result['confirmed'] is False and result['reason'] == 'session_changed'


def test_a_recycled_leader_pid_is_not_the_same_lifetime(inert, monkeypatch):
    """Same session id, same leader number, different process: not the same gate."""
    reads = []

    def lifetime(leader):
        reads.append(leader)
        return '555' if len(reads) == 1 else '999'

    monkeypatch.setattr(MODULE, 'leader_lifetime', lifetime)
    gate = binding()

    def run(command, **_kwargs):
        if command[1] == 'lock-session':
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        if command[1] == 'show-session':
            return type('R', (), {'returncode': 0,
                                  'stdout': session_properties('3', locked='yes'), 'stderr': ''})()
        return type('R', (), {'returncode': 0, 'stdout': 'b true\n', 'stderr': ''})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    result = MODULE.lock_and_confirm(gate, polls=3)
    assert result['confirmed'] is False and result['reason'] == 'session_changed'


def test_an_unverifiable_lifetime_is_not_an_identity(monkeypatch):
    """Rook's boundary finding: an empty lifetime compares equal to another one.

    The real parser runs against virtual /proc bytes: an unreadable stat, a
    truncated one and a missing start time all have to report *no* lifetime
    rather than an empty string a replacement process can match.
    """
    def proc(answer):
        class ProcPath:
            def __truediv__(self, part):
                return self

            def read_text(self):
                if isinstance(answer, Exception):
                    raise answer
                return answer

        return ProcPath()

    def stat(start):
        # After the comm field: state, ppid, ... , starttime at index 19.
        return '900 (m) ' + ' '.join(['S'] + ['1'] * 18 + [start])

    usable = stat('987654321')
    cases = [PermissionError('modeled /proc read unavailable'), '900 (modeled leader) S 1',
             stat(''), stat('abc'), stat('0')]
    for answer in cases:
        monkeypatch.setattr(MODULE, 'Path', lambda *a, value=answer: proc(value))
        assert MODULE.leader_lifetime('900') is None, answer
    monkeypatch.setattr(MODULE, 'Path', lambda *a: proc(usable))
    assert MODULE.leader_lifetime('900') == '987654321'
    assert MODULE.leader_lifetime('') is None
    assert MODULE.leader_lifetime('not-a-pid') is None


def test_a_session_with_an_unverifiable_lifetime_cannot_be_admitted(inert, monkeypatch):
    """Admission refuses rather than fencing on a value this host cannot read."""
    def run(command, **_kwargs):
        if command[1] == 'list-sessions':
            return type('R', (), {'returncode': 0, 'stdout': '3 1000 sage seat0 tty2\n',
                                  'stderr': ''})()
        return type('R', (), {'returncode': 0, 'stdout': session_properties('3'), 'stderr': ''})()

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    monkeypatch.setattr(MODULE, 'leader_lifetime', lambda leader: None)
    assert MODULE.graphical_session(dict(MODULE.BINDING_DEFAULTS)) == (
        None, 'session_lifetime_unverified')
    # The same loss during confirmation: the fence cannot be rebuilt, so the gate
    # that was standing on the readable lifetime must not report itself confirmed.
    inflight = MODULE.lock_and_confirm(
        MODULE.SessionBinding('3', '1000', 'seat0', 'user', 'wayland', 'no', 'yes', '900',
                              '987654321'), polls=2)
    assert inflight['confirmed'] is False


def test_a_kiosk_override_cancels_a_pending_lock(inert, monkeypatch):
    """A pending desktop entry must not outlive the operator's newer intent."""
    gate = binding()
    answers = iter(['auto', 'kiosk'])

    class Override:
        def read_text(self):
            return next(answers)

    def run(command, **_kwargs):
        if command[1] == 'lock-session':
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        if command[1] == 'show-session':
            return type('R', (), {'returncode': 0,
                                  'stdout': session_properties('3', locked='no'), 'stderr': ''})()
        return type('R', (), {'returncode': 0, 'stdout': 'b true\n', 'stderr': ''})()

    monkeypatch.setattr(MODULE, 'OVERRIDE', Override())
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    # The attached keyboard and mouse still want the desktop; only the written
    # override changes, so the cancellation can only come from re-reading it.
    # It arrives as the resolved mode, because that is the whole question a
    # retry has to answer.
    result = MODULE.lock_and_confirm(gate, polls=4, intent=lambda: (True, True))
    assert result['confirmed'] is False and result['reason'] == 'intent_changed'


def test_a_blank_binding_setting_is_not_a_wildcard(monkeypatch, tmp_path):
    """An unusable operator_uid/operator_seat keeps the reviewed default."""
    config = tmp_path / 'display-mode.json'

    def written(values):
        config.write_text(json.dumps(values))
        monkeypatch.setattr(MODULE, 'CONFIG', config)
        return MODULE.binding_values()

    assert written({'operator_uid': ' ', 'operator_seat': ''}) == dict(MODULE.BINDING_DEFAULTS)
    assert written({'operator_uid': ''}) == dict(MODULE.BINDING_DEFAULTS)
    assert written({'operator_seat': '   '}) == dict(MODULE.BINDING_DEFAULTS)
    assert written({'operator_uid': 'nobody'}) == dict(MODULE.BINDING_DEFAULTS)
    assert written({'operator_uid': '0'}) == dict(MODULE.BINDING_DEFAULTS)
    assert written({'operator_seat': '-'}) == dict(MODULE.BINDING_DEFAULTS)
    assert written({'operator_uid': '1001', 'operator_seat': 'seat9'}) == {
        'operator_uid': '1001', 'operator_seat': 'seat9'}
    assert written({'operator_uid': ' 1001 ', 'operator_seat': ' seat9 '}) == {
        'operator_uid': '1001', 'operator_seat': 'seat9'}


def test_an_invalid_binding_expectation_refuses_without_listing_sessions(inert, monkeypatch):
    """Even a caller that skips the config loader cannot reach a wildcard."""
    def run(command, **_kwargs):
        raise AssertionError(f'no session command may run: {command!r}')

    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    for values in ({'operator_uid': '', 'operator_seat': ''},
                   {'operator_uid': '1000', 'operator_seat': ' '},
                   {'operator_uid': '0', 'operator_seat': 'seat0'}):
        assert MODULE.graphical_session(values) == (None, 'binding_invalid')


def test_the_lock_is_confirmed_before_anything_is_exposed(monkeypatch):
    """The ordering finding: no rotation, no kiosk stop, no desktop browser first."""
    events = []
    passes = [0]

    class Browser:
        def __init__(self):
            self.process = type('P', (), {'pid': 101})()
            self.mode = None
            self.failures = 0

        def running(self):
            return True

        def stop(self):
            events.append('browser.stop')

        def start(self, mode, transform):
            events.append(f'browser.start:{mode}')

        def note_exit(self):
            return 2

    class R:
        def __init__(self, returncode=0, stdout='', stderr=''):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def run(command, **_kwargs):
        events.append('lock-session' if command[1] == 'lock-session' else command[1])
        if command[1] == 'list-sessions':
            return R(stdout='3 1000 sage seat0 tty2\n')
        if command[1] == 'show-session':
            return R(stdout=session_properties('3', locked='yes'))
        return R(stdout='b true\n')

    reads = [0]

    def inputs():
        # The repaired supervisor re-reads the operator's intent inside a pass,
        # so this counts reads, not passes. The mode below still steps once per
        # pass: a re-read inside a pass must resolve the same intent as the read
        # that began it, and only a new pass may change what is wanted.
        reads[0] += 1
        if reads[0] > 6:
            raise KeyboardInterrupt
        return True, True

    def wanted(*_args):
        passes[0] += 1
        return ('kiosk' if passes[0] == 1 else 'desktop', 'inert')

    monkeypatch.setattr(MODULE, 'subprocess', type('S', (), {
        'run': staticmethod(run), 'SubprocessError': subprocess.SubprocessError})())
    monkeypatch.setattr(MODULE, 'leader_lifetime', lambda leader: '987654321')
    monkeypatch.setattr(MODULE, 'Browser', Browser)
    monkeypatch.setattr(MODULE, 'log', lambda message: None)
    monkeypatch.setattr(MODULE, 'write_state', lambda state: None)
    monkeypatch.setattr(MODULE, 'apply_transform', lambda *a: events.append('transform'))
    monkeypatch.setattr(MODULE, 'config', lambda: dict(MODULE.DEFAULTS))
    monkeypatch.setattr(MODULE, 'inputs', inputs)
    monkeypatch.setattr(MODULE, 'wanted_mode', wanted)
    monkeypatch.setattr(MODULE, 'RUNTIME', type('F', (), {
        '__truediv__': lambda self, name: self, 'open': lambda self, mode: object()})())
    monkeypatch.setattr(MODULE, 'fcntl', type('F', (), {'flock': staticmethod(lambda *a: None),
                                                       'LOCK_EX': 1, 'LOCK_NB': 2})())
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(MODULE.time, 'sleep', lambda seconds: None)
    with pytest.raises(KeyboardInterrupt):
        MODULE.main([])

    lock = events.index('lock-session')
    kiosk_running = events.index('browser.start:kiosk')
    assert kiosk_running < lock, 'the portrait kiosk ran first'
    assert lock < events.index('transform', 1), 'the desktop rotation follows the confirmed lock'
    assert lock < events.index('browser.stop', kiosk_running + 1), (
        'the kiosk stops only after the lock')
    assert lock < events.index('browser.start:desktop'), 'the desktop browser starts after the lock'


def test_a_request_withdrawn_during_the_kiosk_stop_is_not_admitted(monkeypatch):
    """The last check before exposure runs after the stop, not only before it.

    The rotation check and the check after it both run while the kiosk is still
    up. Browser.stop() then waits up to eight seconds for that kiosk to go, and
    that is long enough for the operator to detach. Nothing may be admitted on
    the intent that preceded a blocking port, so the desktop must not start and
    the console has to come back on the appliance.
    """
    events = []
    reads = [0]
    locks = [0]
    state = {'withdrawn': False}

    class Browser:
        def __init__(self):
            self.process = None
            self.mode = None
            self.failures = 0

        def running(self):
            return self.process is not None

        def stop(self):
            events.append('browser.stop')
            self.process, self.mode = None, None
            if locks[0] and not state['withdrawn']:
                # The modeled world changes while this port is blocking.
                state['withdrawn'] = True
                events.append('inputs.withdrawn')

        def start(self, mode, transform):
            self.process = type('P', (), {'pid': 101, 'poll': lambda: None})()
            self.mode = mode
            events.append(f'browser.start:{mode}')

        def note_exit(self):
            return 2

    class R:
        def __init__(self, returncode=0, stdout='', stderr=''):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def run(command, **_kwargs):
        if command[1] == 'lock-session':
            locks[0] += 1
            return R()
        if command[1] == 'list-sessions':
            return R(stdout='3 1000 sage seat0 tty2\n')
        if command[1] == 'show-session':
            return R(stdout=session_properties('3', locked='yes'))
        return R(stdout='b true\n')

    def inputs():
        # The first pass is the appliance alone. The operator attaches for the
        # second, then detaches again while that entry's kiosk stop blocks.
        reads[0] += 1
        if reads[0] > 8:
            raise KeyboardInterrupt
        if state['withdrawn'] or reads[0] == 1:
            return False, False
        return True, True

    monkeypatch.setattr(MODULE, 'subprocess', type('S', (), {
        'run': staticmethod(run), 'SubprocessError': subprocess.SubprocessError})())
    monkeypatch.setattr(MODULE, 'leader_lifetime', lambda leader: '987654321')
    monkeypatch.setattr(MODULE, 'Browser', Browser)
    monkeypatch.setattr(MODULE, 'log', events.append)
    monkeypatch.setattr(MODULE, 'write_state', lambda state: None)
    monkeypatch.setattr(MODULE, 'apply_transform', lambda *a: events.append('transform'))
    monkeypatch.setattr(MODULE, 'config', lambda: dict(MODULE.DEFAULTS))
    monkeypatch.setattr(MODULE, 'inputs', inputs)
    monkeypatch.setattr(MODULE, 'OVERRIDE', type('O', (), {
        'read_text': staticmethod(lambda: 'auto')})())
    monkeypatch.setattr(MODULE, 'RUNTIME', type('F', (), {
        '__truediv__': lambda self, name: self, 'open': lambda self, mode: object()})())
    monkeypatch.setattr(MODULE, 'fcntl', type('F', (), {'flock': staticmethod(lambda *a: None),
                                                       'LOCK_EX': 1, 'LOCK_NB': 2})())
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(MODULE.time, 'sleep', lambda seconds: None)
    with pytest.raises(KeyboardInterrupt):
        MODULE.main([])

    withdrawal = events.index('inputs.withdrawn')
    assert locks[0] == 1, 'the gate was requested once for this entry'
    assert 'browser.start:desktop' not in events, (
        'a request withdrawn during the kiosk stop must not expose a desktop')
    assert any(event.startswith('desktop refused (intent_changed)') for event in events), (
        'the refusal names the withdrawn intent rather than the session')
    assert 'browser.start:kiosk' in events[withdrawal:], (
        'the console comes back on the appliance instead of being left blank')
