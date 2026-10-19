"""The C-F session profile of the guarded sleep controller, without a unit."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sage_lantern.suspend_session import SUPERVISOR, WAYLAND_PROFILE, X11_PROFILE, WaylandBinding


SPEC = importlib.util.spec_from_file_location(
    "idle_runner_cf", Path(__file__).resolve().parents[1] / "lantern/lantern-idle-suspend.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def binding():
    return WaylandBinding("unit-c", "sage", 1000,
                          Path("/home/sage/.config/sage-lantern/unit-c/chromium"))


class ProfileSelectionTests(unittest.TestCase):
    def setUp(self):
        MODULE.SESSION = None
        self.addCleanup(setattr, MODULE, "SESSION", None)

    def test_installed_record_selects_the_session_backend(self):
        with patch.object(MODULE, "installed_profile", return_value=X11_PROFILE):
            self.assertIsInstance(MODULE.session(), MODULE.X11Session)
        with patch.object(MODULE, "installed_profile", return_value=WAYLAND_PROFILE), \
                patch.object(MODULE, "load_wayland_binding", return_value=binding()), \
                patch.object(MODULE, "MutterIdle") as idle, patch.object(MODULE, "GnomeShield") as display:
            self.assertIsInstance(MODULE.session(), MODULE.WaylandSession)
            idle.assert_called_once_with(binding())
            display.assert_called_once_with(binding())
            self.assertIs(MODULE.idle_seconds(), idle.return_value.seconds.return_value)

    def test_stop_set_follows_the_installed_profile(self):
        with patch.object(MODULE, "installed_profile", return_value=WAYLAND_PROFILE), \
                patch.object(MODULE, "SuspendSystem") as system:
            MODULE.suspend_system()
            system.assert_called_once_with(stop_services=MODULE.WAYLAND_STOP_SERVICES)
        with patch.object(MODULE, "installed_profile", return_value=X11_PROFILE), \
                patch.object(MODULE, "SuspendSystem") as system:
            MODULE.suspend_system()
            system.assert_called_once_with()

    def test_idle_and_display_dispatch_to_the_installed_session(self):
        installed = Mock()
        with patch.object(MODULE, "session", return_value=installed):
            self.assertIs(MODULE.idle_seconds(), installed.idle_seconds.return_value)
            MODULE.display_power("off")
            installed.display_power.assert_called_once_with("off")


class VoiceOptionalProfileTests(unittest.TestCase):
    """A shipped worker that was never enabled is not part of the profile."""

    def test_voice_lane_joins_only_when_the_unit_was_enabled(self):
        for units, expected in (((), ()), ((MODULE.VOICE,), (MODULE.VOICE,))):
            with self.subTest(units=units), patch.object(MODULE, "SuspendSystem") as system:
                system.return_value.voice_lane.return_value = SimpleNamespace(confirmed=True, units=units)
                self.assertEqual(MODULE.voice_lane(), expected)

    def test_unconfirmed_observation_is_never_a_lane(self):
        with patch.object(MODULE, "SuspendSystem") as system:
            system.return_value.voice_lane.return_value = SimpleNamespace(confirmed=False, units=(MODULE.VOICE,))
            self.assertEqual(MODULE.voice_lane(), ())

    def test_drain_hooks_do_not_require_a_voice_instance_without_the_lane(self):
        with patch.object(MODULE, "public_json", return_value={"ready": True}), \
                patch.object(MODULE, "service_identity", return_value=(22, "a" * 32)) as identity, \
                patch.object(MODULE, "read_json", return_value={"version": 1}), \
                patch.object(MODULE, "prior_auth_drained", return_value=True), \
                patch.object(MODULE, "voice_lane", return_value=()), \
                patch.object(MODULE.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=123)):
            self.assertIs(MODULE.drain_hooks_ready(), True)
            identity.assert_called_once_with(MODULE.AUTH)

    def test_inference_ready_needs_no_voice_guard_environment_without_the_lane(self):
        with patch.object(MODULE, "model_baseline_ready", return_value=True), \
                patch.object(MODULE, "pending_count", return_value=0), \
                patch.object(MODULE, "voice_lane", return_value=()), \
                patch.object(MODULE.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=123)), \
                patch.object(MODULE, "command") as command:
            self.assertIs(MODULE.inference_ready(), True)
            command.assert_not_called()

    def test_appliance_without_a_voice_lane_publishes_a_null_voice_identity(self):
        """The live C rehearsal caught this: no lane, no local identity to publish."""
        with patch.object(MODULE, "voice_lane", return_value=()), \
                patch.object(MODULE, "service_identity", return_value=(22, "a" * 32)), \
                patch.object(MODULE, "idle_seconds", return_value=600), \
                patch.object(MODULE, "publish") as publish, \
                patch.object(MODULE.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=123)):
            appliance = MODULE.Appliance(Mock(), dict(deep_sleep_seconds=300), "transition-123", Mock())
        self.assertFalse(appliance.voice)
        self.assertIsNone(appliance.voice_identity)
        self.assertIs(publish.call_args.args[1]["voice_identity"], None)
        self.assertEqual(publish.call_args.args[1]["transition_id"], "transition-123")


class WaylandSessionTests(unittest.TestCase):
    def setUp(self):
        self.idle = Mock()
        self.display = Mock()
        self.session = MODULE.WaylandSession(binding(), self.idle, self.display)

    def test_idle_seconds_come_from_the_compositor(self):
        self.idle.seconds.return_value = 604.5
        self.assertEqual(self.session.idle_seconds(), 604.5)

    def test_display_power_turns_the_panel_off_and_on_through_mutter(self):
        self.session.display_power("off")
        self.display.off.assert_called_once_with()
        self.session.display_power("on")
        self.display.on.assert_called_once_with()
        with self.assertRaisesRegex(ValueError, "invalid_display_action"):
            self.session.display_power("toggle")
        self.assertEqual(self.display.off.call_count, 1)

    def test_display_outcome_is_reported_for_the_cycle_record(self):
        self.display.off.return_value = "unconfirmed"
        self.display.outcome = "restored"
        self.session.display_power("off")
        self.assertEqual(self.session.display_outcome, "unconfirmed")
        self.session.display_power("on")
        self.assertEqual(self.session.display_outcome, "restored")

    def test_preflight_requires_a_panel_that_is_on_and_reachable(self):
        self.session.preflight()
        self.display.locks_kiosk.assert_called_once_with()
        self.display.active.assert_called_once_with()

    def test_fence_closes_and_refuses_an_unverified_console(self):
        with patch.object(MODULE, "ConsoleFence") as constructor:
            constructor.return_value.preflight.return_value = False
            with self.assertRaisesRegex(ValueError, "console_inventory_unverified"):
                self.session.fence()
            constructor.assert_called_once_with(binding=binding(), auth_locked=MODULE.locked)
            constructor.return_value.close.assert_called_once_with()
        with patch.object(MODULE, "ConsoleFence") as constructor, \
                patch.object(MODULE, "locked", return_value=True):
            constructor.return_value.preflight.return_value = True
            self.assertIs(self.session.fence(), constructor.return_value)

    def test_supervisor_path_is_the_installed_mode_supervisor(self):
        self.assertEqual(SUPERVISOR, "/usr/local/libexec/sage-lantern-mode-supervisor")
