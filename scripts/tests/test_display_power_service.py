from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DisplayServiceTests(unittest.TestCase):
    def test_root_supervisor_selects_session_and_drops_child_privileges(self):
        service = (ROOT / "software/systemd/sage-lantern-display-power.service").read_text()
        for directive in (
            "User=root", "Group=root", "NoNewPrivileges=true",
            "ProtectSystem=strict", "PrivateTmp=false",
            "ConditionPathExists=/etc/sage-lantern-display-power.enabled",
            "ExecStart=/opt/sage-lantern/venv/bin/python -I -B /usr/local/libexec/sage-lantern-display-session --serve",
            "KillMode=control-group",
            "Restart=on-failure", "TimeoutStopSec=10",
        ):
            self.assertIn(directive, service)
        self.assertNotIn("sudo", service)
        self.assertNotIn("suspend.target", service)
        self.assertNotIn("force off", service)
        self.assertNotIn("Environment=DISPLAY", service)
        self.assertNotIn("Environment=XAUTHORITY", service)

    def test_installer_does_not_activate_or_guess_a_display(self):
        installer = (ROOT / "scripts/lantern/install-display-power-local.sh").read_text()
        self.assertNotIn("DISPLAY=:0", installer)
        self.assertNotIn('systemctl start "$service"', installer)
        self.assertNotIn('systemctl stop "$service"', installer)
        self.assertIn('lantern-display-session.py', installer)
        self.assertIn('--check --unit-id', installer)


if __name__ == "__main__":
    unittest.main()
