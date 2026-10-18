"""The profile's own lock decides whether the singleton names are cleared.

`Browser.start()` reaches `clear_stale_singleton()` whenever *this* Browser
object holds no Popen handle, and `running()` only covers that one handle. An
initial or recreated Browser therefore starts with process=None while a live
chromium may still own the profile, and clearing the three singleton names
underneath it admits a second browser onto a profile the first is still using.
Rook reproduced that case against the frozen source; the case below is its
regression.

Only the chromium launch is stubbed. The real `start()` and the real file
cleanup run; no chromium is started and no device is touched.
"""
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
NAMES = ('SingletonLock', 'SingletonSocket', 'SingletonCookie')


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/lantern' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBrowser:
    """Stands in for the chromium Popen; poll() reports 'still alive'."""
    pid = 123456

    def poll(self):
        return None


@pytest.fixture
def supervisor(tmp_path, monkeypatch):
    """A real supervisor pointed at a scratch profile on a synthetic proc tree.

    /proc exists only on the unit, so the process-name branch is exercised
    through `PROC` rather than skipped. `NODENAME` is injected for the same
    reason: simulating another machine's lock must not patch `os.uname()`, which
    would reach every other caller of os in the process.
    """
    module = load('cf_mode_supervisor_owner', 'cf-mode-supervisor.py')
    profile = tmp_path / 'chromium'
    profile.mkdir()
    (profile / 'Preferences').write_bytes(b'profile-content-must-stay')
    monkeypatch.setattr(module, 'PROFILE', str(profile))
    monkeypatch.setattr(module, 'PROC', str(tmp_path / 'proc'))
    (tmp_path / 'proc').mkdir()
    return module


def own_name(module, pid, program='chromium'):
    """Give that pid a name in the synthetic process table."""
    entry = Path(module.PROC) / str(pid)
    entry.mkdir(parents=True, exist_ok=True)
    (entry / 'comm').write_text(f'{program}\n')


def write_locks(module, lock_target, other='fixture'):
    for name in NAMES:
        path = Path(module.PROFILE) / name
        if os.path.lexists(path):
            path.unlink()
        path.symlink_to(lock_target if name == 'SingletonLock' else other)


def remaining(module):
    return [name for name in NAMES if os.path.lexists(Path(module.PROFILE) / name)]


def start(module):
    with patch.object(module.subprocess, 'Popen', return_value=FakeBrowser()):
        module.Browser().start('kiosk', 'normal')
    return (Path(module.PROFILE) / 'Preferences').read_bytes()


def test_a_live_owner_keeps_its_locks_even_though_this_browser_is_untracked(supervisor):
    # The owner is this test process: alive, and named as the browser would be.
    # This is Rook's case — the supervisor has no Popen handle for it.
    supervisor.NODENAME = 'lantern-c'
    own_name(supervisor, os.getpid(), 'chromium')
    write_locks(supervisor, f'lantern-c-{os.getpid()}')
    assert start(supervisor) == b'profile-content-must-stay'
    assert sorted(remaining(supervisor)) == sorted(NAMES), \
        'nothing may be removed while a live browser holds the profile'


def test_a_dead_owner_is_still_repaired(supervisor):
    # The original repair must survive: this is the greeter it exists to prevent.
    pid = 999999
    supervisor.NODENAME = 'lantern-c'
    write_locks(supervisor, f'lantern-c-{pid}')
    (Path(supervisor.PROC) / str(pid)).mkdir(parents=True, exist_ok=True)
    start(supervisor)
    assert remaining(supervisor) == []


def test_a_recycled_pid_belonging_to_another_program_does_not_hold_the_profile(supervisor):
    # Pids are recycled, and a live stranger wearing the lock's number would hold
    # the profile for good if liveness alone were trusted.
    supervisor.NODENAME = 'lantern-c'
    own_name(supervisor, os.getpid(), 'python3')
    write_locks(supervisor, f'lantern-c-{os.getpid()}')
    start(supervisor)
    assert remaining(supervisor) == []


def test_a_hostname_containing_hyphens_is_still_recognised(supervisor):
    # Appliance hostnames look like `lantern-c`. Splitting on the first hyphen
    # would read the short prefix as the host and discard a genuine lock.
    supervisor.NODENAME = 'lantern-c'
    own_name(supervisor, os.getpid(), 'chromium')
    write_locks(supervisor, f'lantern-c-{os.getpid()}')
    start(supervisor)
    assert sorted(remaining(supervisor)) == sorted(NAMES)


def test_a_lock_this_machine_never_issued_is_stale(supervisor):
    supervisor.NODENAME = 'lantern-c'
    for target in ('some-other-host-4242', 'not-a-host-pid-pair', 'lantern-c-notanumber', 'lantern-c-0'):
        write_locks(supervisor, target)
        start(supervisor)
        assert remaining(supervisor) == [], target


def test_leftovers_without_a_lock_still_clear(supervisor):
    # SingletonLock is the only name carrying an owner; without it there is
    # nothing to establish and the leftovers must not accumulate.
    write_locks(supervisor, 'lantern-c-4242')
    (Path(supervisor.PROFILE) / 'SingletonLock').unlink()
    start(supervisor)
    assert remaining(supervisor) == []


def test_a_clean_profile_is_left_untouched(supervisor):
    assert start(supervisor) == b'profile-content-must-stay'
    assert remaining(supervisor) == []
