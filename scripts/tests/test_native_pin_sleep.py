"""Guarded sleep profile cannot fall back to a stock password shield."""
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
from sage_lantern.suspend_session import (ConsoleFence, NativePinShield, SUPERVISOR,
    GUARDED_WAYLAND_PROFILE, WaylandBinding, installed_profile)
from sage_lantern.suspend_kiosk import Process
from test_lantern_idle_suspend_cf import MODULE


def binding():
    return WaylandBinding('unit-c', 'sage', 1000,
                         Path('/home/sage/.config/sage-lantern/unit-c/chromium'))


def test_only_an_explicit_trusted_profile_selects_native_pin_sleep():
    with patch('sage_lantern.suspend_session.os.path.lexists', return_value=True), \
         patch('sage_lantern.suspend_session._read_binding_file', return_value=GUARDED_WAYLAND_PROFILE + '\n'):
        assert installed_profile() == GUARDED_WAYLAND_PROFILE
    MODULE.SESSION = None
    try:
        with patch.object(MODULE, 'installed_profile', return_value=GUARDED_WAYLAND_PROFILE), \
             patch.object(MODULE, 'load_wayland_binding', return_value=binding()), \
             patch.object(MODULE, 'MutterIdle'), patch.object(MODULE, 'NativePinShield') as display, \
             patch.object(MODULE, 'GnomeShield') as stock:
            value = MODULE.session()
            assert value.name == GUARDED_WAYLAND_PROFILE
            display.assert_called_once_with(binding()); stock.assert_not_called()
            with patch.object(MODULE, 'ConsoleFence') as fence:
                fence.return_value.preflight.return_value = True
                value.fence()
                assert fence.call_args.kwargs['require_native_pin'] is True
    finally:
        MODULE.SESSION = None


def test_native_sleep_and_wake_confirm_lock_without_public_unlock():
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return 'b true'
    shield = NativePinShield(binding(), run=run)
    assert shield.off() == 'concealed'
    assert shield.on() == 'restored_guarded'
    assert [argv[-1] for argv in calls] == ['PrepareSleep', 'GetLocked', 'Wake', 'GetLocked']
    assert all('org.sage.Lantern.DesktopGuard1' in argv for argv in calls)


@pytest.mark.parametrize('reply', ['b false', '', 'true', 'b maybe'])
@pytest.mark.parametrize('method', ['off', 'on'])
def test_missing_or_failed_guard_is_held_without_stock_fallback(reply, method):
    shield = NativePinShield(binding(), run=lambda *a, **k: reply)
    with pytest.raises(ValueError):
        getattr(shield, method)()


def test_wake_refuses_an_unlocked_state_even_after_positive_method_ack():
    replies = iter(['b true', 'b false'])
    shield = NativePinShield(binding(), run=lambda *a, **k: next(replies))
    with pytest.raises(ValueError, match='wake_unconfirmed'):
        shield.on()


def test_guarded_fence_refuses_a_supervisor_without_the_mandatory_flag():
    fence = ConsoleFence(binding=binding(), auth_locked=lambda: True, require_native_pin=True)
    process = Process(123, 100, 123, 1000, '/usr/bin/python3.11',
                      ('python3', SUPERVISOR), 'S', 456)
    with pytest.raises(ValueError, match='ambiguous'):
        fence._supervisor_of({123: process})
    guarded = Process(123, 100, 123, 1000, '/usr/bin/python3.11',
                      ('python3', SUPERVISOR, '--require-native-pin'), 'S', 456)
    with patch.object(fence, '_scope', return_value=True):
        assert fence._supervisor_of({123: guarded}) == guarded
    stock = ConsoleFence(binding=binding(), auth_locked=lambda: True)
    with pytest.raises(ValueError, match='ambiguous'):
        stock._supervisor_of({123: guarded})
