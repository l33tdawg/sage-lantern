import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest


ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "crash_privacy", ROOT / "scripts/lantern/configure-crash-privacy.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Systemd:
    def __init__(self):
        self.calls = []
        self.active = {"apport.service", "whoopsie.path", "apport-forward@42.service"}
        self.masked = False
        self.fail_stop = False

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "/usr/lib/systemd/systemd-sysctl":
            return 0, "kernel.core_uses_pid = 0\nfs.suid_dumpable = 0\nkernel.core_pattern =\n"
        if args[1] == "list-unit-files":
            return 0, "sage-lantern-new-worker.service enabled enabled"
        if args[1] == "list-units":
            return 0, "\n".join(f"{u} loaded active running purpose" for u in sorted(self.active))
        if args[1] == "stop":
            if self.fail_stop:
                return 1, ""
            self.active.remove(args[2])
            return 0, ""
        if args[1] == "daemon-reload":
            self.masked = True
            return 0, ""
        if args[1] == "is-enabled":
            return (1, "masked") if self.masked else (0, "static")
        if args[1] == "show":
            unit = args[2]
            if unit in MODULE.COLLECTORS:
                return 0, ("LoadState=masked\nActiveState=inactive\nUnitFileState=masked" if self.masked
                           else "LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled")
            return 0, "LoadState=loaded\nActiveState=active\nLimitCORE=0\nLimitCORESoft=0"
        raise AssertionError(args)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    # Synthetic root. Production safety is tested separately below; this
    # fixture skips ownership of the laptop's world-writable temp ancestors.
    def safe(path):
        for component in (path, *path.parents):
            if component == tmp_path.parent:
                break
            if component.is_symlink():
                raise ValueError("unsafe_config")
        if not path.exists():
            return None
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022:
            raise ValueError("unsafe_config")
        return info
    monkeypatch.setattr(MODULE, "safe_path", safe)
    for name, value in MODULE.SYSCTLS.items():
        path = tmp_path / "proc/sys" / name.replace(".", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("|/collector %p\n" if name == "kernel.core_pattern" else "2\n")
    defaults = tmp_path / "etc/default/apport"
    defaults.parent.mkdir(parents=True)
    defaults.write_text("# existing operator comment\nenabled=1\nother_option=yes\n")
    (tmp_path / "etc/systemd/system").mkdir(parents=True)
    (tmp_path / "var/backups").mkdir(parents=True)
    for pid, limits in (("123", "0 unlimited"), ("124", "0 0")):
        path = tmp_path / "proc" / pid
        path.mkdir()
        (path / "status").write_text(f"Uid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n")
        (path / "limits").write_text(f"Max core file size         {limits}       bytes\n")
    crash = tmp_path / "var/crash/private.crash"
    crash.parent.mkdir(parents=True)
    crash.write_bytes(b"DO_NOT_READ_OWNER_MEMORY")
    return tmp_path, Systemd()


def test_apply_blocks_collectors_preserves_memory_and_reports_pending_sessions(machine, monkeypatch):
    root, run = machine
    crash = root / "var/crash/private.crash"
    before = crash.lstat()
    real_open = Path.open
    real_read = Path.read_text
    def no_crash_open(path, *args, **kwargs):
        assert path != crash, "crash payload must never be opened"
        return real_open(path, *args, **kwargs)
    def no_crash_read(path, *args, **kwargs):
        assert path != crash
        return real_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", no_crash_open)
    monkeypatch.setattr(Path, "read_text", no_crash_read)
    receipt_path = MODULE.apply(root, root / "var/backups/policy", run)
    report = MODULE.check(root, run, {"desktop": os.getuid()})
    assert report["configuration_ok"]
    assert report["session_restart_pending"] == ["desktop"]
    assert report["acceptance_complete"] is False
    assert report["processes"]["desktop"]["pending"] == 1
    assert report["processes"]["desktop"]["zero_soft_and_hard"] == 1
    assert crash.lstat() == before
    assert report["existing_artifacts"]["/var/crash"]["files"] == 1
    assert "DO_NOT_READ_OWNER_MEMORY" not in json.dumps(report)
    assert "private.crash" not in json.dumps(report)
    for unit in MODULE.COLLECTORS:
        assert os.readlink(root / "etc/systemd/system" / unit) == "/dev/null"
    assert all(args[1] not in {"restart", "start", "enable", "daemon-reexec"} for args in run.calls)
    assert "apport-forward@42.service" not in run.active
    data = (root / "etc/default/apport").read_text()
    assert "enabled=0" in data and "other_option=yes" in data
    receipt = json.loads(receipt_path.read_text())
    apport = next(row for row in receipt["files"] if row["path"] == "/etc/default/apport")
    assert (receipt_path.parent / apport["backup"]).read_text().endswith("other_option=yes\n")
    assert (root / "etc/systemd/system/sage-lantern-new-worker.service.d/90-sage-lantern-crash-privacy.conf").exists()


def test_check_is_read_only_and_detects_handler_reenabled_after_apply(machine):
    root, run = machine
    MODULE.apply(root, root / "var/backups/policy", run)
    pattern = root / "proc/sys/kernel/core_pattern"
    pattern.write_text("|/collector %p\n")
    run.calls.clear()
    report = MODULE.check(root, run, {"desktop": os.getuid()})
    assert not report["configuration_ok"]
    assert "kernel.core_pattern" in report["failures"]
    assert pattern.read_text() == "|/collector %p\n"
    assert all(args[1] in {"show", "is-enabled", "list-units", "list-unit-files", "--cat-config"} for args in run.calls)


@pytest.mark.parametrize("case", ["policy_conflict", "local_collector", "linked_parent", "reused_backup"])
def test_preflight_refuses_without_stopping_collectors(machine, case):
    root, run = machine
    backup = root / "var/backups/policy"
    if case == "policy_conflict":
        path = root / "etc/systemd/user.conf.d/90-sage-lantern-crash-privacy.conf"
        path.parent.mkdir()
        path.write_text("[Manager]\nDefaultLimitCORE=infinity\n")
    elif case == "local_collector":
        (root / "etc/systemd/system/apport.service").write_text("local unit")
    elif case == "linked_parent":
        target = root / "elsewhere"
        target.mkdir()
        (root / "etc/sysctl.d").symlink_to(target)
    else:
        backup.mkdir()
    with pytest.raises(ValueError):
        MODULE.apply(root, backup, run)
    assert not any(args[1] == "stop" for args in run.calls)
    assert run.active


def test_failed_stop_leaves_a_prepared_receipt_and_does_not_claim_success(machine):
    root, run = machine
    run.fail_stop = True
    with pytest.raises(ValueError, match="policy_command_failed"):
        MODULE.apply(root, root / "var/backups/policy", run)
    receipt = json.loads((root / "var/backups/policy/receipt.json").read_text())
    assert receipt["status"] == "prepared"
    assert not (root / "etc/systemd/system/apport.service").is_symlink()


def test_ambiguous_apport_defaults_refuse():
    with pytest.raises(ValueError, match="ambiguous_apport_config"):
        MODULE.apport_disabled(b"enabled=1\nenabled=0\n")


def test_plan_is_portable_and_never_touches_systemd(monkeypatch, capsys):
    monkeypatch.setattr(MODULE, "command", lambda args: pytest.fail("plan used systemd"))
    assert MODULE.main(["--plan"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "plan"
    assert report["files"]["/etc/systemd/coredump.conf.d/90-sage-lantern-crash-privacy.conf"] == (
        "[Coredump]\nStorage=none\nProcessSizeMax=0\n")


@pytest.mark.parametrize("case", ["writable", "linked", "hardlinked"])
def test_real_safe_path_rejects_linked_and_writable_parents(tmp_path, monkeypatch, case):
    # Root ownership is simulated; preserve actual mode/type/inode metadata.
    original = Path.lstat
    class Metadata:
        def __init__(self, info):
            self.__dict__.update({name: getattr(info, name) for name in
                                 ("st_mode", "st_nlink", "st_size", "st_dev", "st_ino", "st_mtime_ns")})
            self.st_uid = 0
    monkeypatch.setattr(Path, "lstat", lambda path: Metadata(original(path)))
    parent = tmp_path / "parent"
    parent.mkdir()
    path = parent / "policy.conf"
    path.write_text("non-secret config")
    if case == "writable":
        parent.chmod(0o777)
    elif case == "linked":
        link = tmp_path / "link"
        link.symlink_to(parent)
        path = link / "policy.conf"
    else:
        os.link(path, parent / "other.conf")
    with pytest.raises(ValueError, match="unsafe_(parent|config)"):
        MODULE.safe_path(path)


def test_later_boot_sysctl_override_is_a_failure(machine):
    root, run = machine
    MODULE.apply(root, root / "var/backups/policy", run)
    def overridden(args):
        rc, text = run(args)
        if args[0] == "/usr/lib/systemd/systemd-sysctl":
            text += "kernel.core_pattern = |/unexpected-collector %p\n"
        return rc, text
    report = MODULE.check(root, overridden, {"desktop": os.getuid()})
    assert "kernel.core_pattern:boot_config" in report["failures"]


def test_instance_inventory_and_unavailable_process_limits_fail_visibly(machine):
    root, run = machine
    MODULE.apply(root, root / "var/backups/policy", run)
    (root / "proc/123/limits").write_text("unexpected metadata")
    run.active.add("apport-coredump-hook@42.service")
    report = MODULE.check(root, run, {"desktop": os.getuid()})
    assert not report["configuration_ok"]
    assert "apport-coredump-hook@42.service" in report["failures"]
    assert report["processes"]["desktop"]["unavailable"] == 1
