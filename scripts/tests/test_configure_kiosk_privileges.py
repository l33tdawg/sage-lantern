import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest


ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "kiosk_privileges", ROOT / "scripts/lantern/configure-kiosk-privileges.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    membership = {"sage", "sudo", "disk", "docker", "systemd-journal", "audio", "video", "input"}
    monkeypatch.setattr(MODULE, "groups", lambda: sorted(membership))
    def safe(path):
        for part in (path, *path.parents):
            if part == tmp_path.parent:
                break
            if part.is_symlink():
                raise ValueError("unsafe_file")
        if not path.exists():
            return None
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022:
            raise ValueError("unsafe_file")
        return info
    monkeypatch.setattr(MODULE, "safe", safe)
    (tmp_path / "etc/sudoers.d").mkdir(parents=True)
    (tmp_path / "etc/sudoers").write_text("@includedir /etc/sudoers.d\n")
    (tmp_path / MODULE.OLD_RULE.relative_to("/")).write_text(MODULE.OLD_RULE_TEXT + "\n")
    (tmp_path / "var/backups").mkdir(parents=True)
    (tmp_path / "usr/bin").mkdir(parents=True)
    (tmp_path / "proc/123").mkdir(parents=True)
    (tmp_path / "proc/123/status").write_text("Uid:\t1000\t1000\t1000\t1000\nGroups:\t6 27 999\n")
    gid_map = {"disk": 6, "sudo": 27, "docker": 988, "systemd-journal": 999, "adm": 4}
    monkeypatch.setattr(MODULE.grp, "getgrnam", lambda name: type("G", (), {"gr_gid": gid_map[name]})())
    calls = []
    def run(args):
        calls.append(args)
        if args[0] == "/usr/bin/gpasswd":
            membership.remove(args[-1])
        if args[0] == "/usr/bin/sudo":
            return 1
        return 0
    return tmp_path, membership, calls, run


def test_apply_preserves_hardware_groups_root_lane_and_existing_sessions(machine):
    root, membership, calls, run = machine
    status = (root / "proc/123/status").read_bytes()
    receipt = MODULE.apply(root, root / "var/backups/policy", run)
    report = MODULE.check(root, run)
    assert report["configuration_ok"]
    assert membership == {"sage", "audio", "video", "input"}
    assert report["fresh_session_required"]
    assert report["desktop_authentication_accepted"] is False
    assert report["running_sessions"]["pending"] == 1
    assert (root / "proc/123/status").read_bytes() == status
    assert (root / MODULE.RULE.relative_to("/")).read_bytes() == MODULE.RULE_BYTES
    assert not (root / MODULE.OLD_RULE.relative_to("/")).exists()
    saved = json.loads(receipt.read_text())
    assert saved["root_management_untouched"] and saved["passwords_and_keys_untouched"]
    assert (receipt.parent / "legacy-psd.before").read_text().strip() == MODULE.OLD_RULE_TEXT
    assert set(saved["groups_removed"]) == {"disk", "sudo", "docker", "systemd-journal"}
    assert all(args[0] in {"/usr/sbin/visudo", "/usr/bin/gpasswd", "/usr/bin/sudo"} for args in calls)
    assert all("/usr/bin/true" not in args or "-l" in args for args in calls)


@pytest.mark.parametrize("case", ["managed_rule", "different_psd", "live_helper", "symlink_helper", "symlink_rule", "backup_exists"])
def test_refusal_changes_no_membership_or_policy(machine, case):
    root, membership, calls, run = machine
    before = set(membership)
    backup = root / "var/backups/policy"
    old = root / MODULE.OLD_RULE.relative_to("/")
    if case == "managed_rule":
        (root / MODULE.RULE.relative_to("/")).write_text("other operator policy")
    elif case == "different_psd":
        old.write_text("sage ALL=(ALL) NOPASSWD: ALL\n")
    elif case == "live_helper":
        (root / "usr/bin/psd-overlay-helper").write_text("existing helper")
    elif case == "symlink_helper":
        (root / "usr/bin/psd-overlay-helper").symlink_to(root / "not-present")
    elif case == "symlink_rule":
        old.unlink()
        old.symlink_to(root / "not-present")
    else:
        backup.mkdir()
    with pytest.raises(ValueError):
        MODULE.apply(root, backup, run)
    assert membership == before
    assert not any(args[0] == "/usr/bin/gpasswd" for args in calls)


def test_invalid_sudoers_stops_before_account_mutation(machine):
    root, membership, calls, run = machine
    old = (root / MODULE.OLD_RULE.relative_to("/")).read_bytes()
    with pytest.raises(ValueError, match="privilege_command_failed"):
        MODULE.apply(root, root / "var/backups/policy", lambda args: 1)
    assert "sudo" in membership
    assert (root / MODULE.OLD_RULE.relative_to("/")).read_bytes() == old
    assert not (root / "var/backups/policy").exists()


def test_failed_group_removal_leaves_rollback_receipt(machine):
    root, membership, calls, run = machine
    def fail(args):
        if args[0] == "/usr/bin/gpasswd":
            return 1
        return run(args)
    with pytest.raises(ValueError, match="privilege_command_failed"):
        MODULE.apply(root, root / "var/backups/policy", fail)
    saved = json.loads((root / "var/backups/policy/receipt.json").read_text())
    assert saved["status"] == "prepared"
    assert saved["old_rule_present"] is True
    assert "sudo" in membership


def test_effective_sudo_admission_or_missing_process_metadata_is_not_acceptance(machine):
    root, membership, calls, run = machine
    MODULE.apply(root, root / "var/backups/policy", run)
    (root / "proc/123/status").write_text("Uid: invalid\n")
    def admitted(args):
        return 0 if args[0] == "/usr/bin/sudo" else run(args)
    report = MODULE.check(root, admitted)
    assert "sudo_probe_admitted" in report["failures"]
    assert report["running_sessions"]["unavailable"] == 1
    assert report["fresh_session_required"]


def test_portable_plan_has_no_commands_or_credentials(monkeypatch, capsys):
    monkeypatch.setattr(MODULE, "run", lambda args: pytest.fail("plan ran a command"))
    assert MODULE.main(["--plan"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["session_restart"] is False
    assert result["root_management"] == "unchanged"
    assert "." not in MODULE.RULE.name  # sudoers ignores files containing dots


def test_apply_requires_separate_direct_root_management(monkeypatch, capsys):
    monkeypatch.setattr(MODULE.platform, "system", lambda: "Linux")
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.os, "getuid", lambda: 0)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    assert MODULE.main(["--apply"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "direct_root_ssh_required"
