import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/install-boot-theme.py"
SPEC = importlib.util.spec_from_file_location("install_boot_theme", SCRIPT)
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)


class BootThemeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "target"
        self.source = self.base / "source"
        shutil.copytree(BOOT.SOURCE, self.source)
        self.kernel = "6.1.0-test"
        for directory in ("usr/share/plymouth/themes/old", "etc/plymouth", "boot", "var/lib",
                          "usr/lib/modules/6.1.0-test"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        files = ["usr/sbin/plymouth-set-default-theme", "usr/sbin/update-initramfs", "usr/sbin/plymouthd",
                 "usr/bin/unmkinitramfs", "proc/cmdline",
                 "usr/share/initramfs-tools/hooks/plymouth", "usr/lib/aarch64-linux-gnu/plymouth/script.so",
                 "usr/lib/aarch64-linux-gnu/plymouth/label.so", "usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "boot/initrd.img-6.1.0-test", "boot/vmlinuz-6.1.0-test",
                 "usr/share/plymouth/themes/old/old.plymouth", "etc/plymouth/plymouthd.conf"]
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic fixture\n")
            path.chmod(0o755 if "usr/sbin/" in name or name == 'usr/bin/unmkinitramfs' else 0o644)
        (self.root / 'proc/cmdline').write_text('root=UUID=fixture quiet splash')
        self.calls = []
        self.current = "old"

    def runner(self, command):
        self.calls.append(command)
        if command[0].endswith('unmkinitramfs'):
            destination = Path(command[2]) / 'main'
            for relative in ('usr/sbin/plymouthd', 'usr/lib/aarch64-linux-gnu/plymouth/script.so',
                             'etc/plymouth/plymouthd.conf',
                             'usr/lib/aarch64-linux-gnu/plymouth/label.so',
                             'usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                             'usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                             *(f'usr/share/plymouth/themes/sage-lantern/{name}' for name in BOOT.FILES)):
                path = destination / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / relative, path)
        if command[0].endswith("plymouth-set-default-theme"):
            if len(command) == 1:
                return self.current
            self.current = command[1]
            (self.root / 'etc/plymouth/plymouthd.conf').write_text('[Daemon]\nTheme=' + self.current + '\n')
        return ""

    def inspect(self, **kwargs):
        return BOOT.inspect(self.source, self.root, runner=self.runner, kernel=self.kernel,
                            system=kwargs.get("system", "Linux"), machine=kwargs.get("machine", "aarch64"))

    def snapshot(self):
        return {str(path.relative_to(self.base)): path.read_bytes()
                for path in self.base.rglob("*") if path.is_file()}

    def test_plan_is_readonly_and_query_only(self):
        before = self.snapshot()
        plan = self.inspect()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.calls, [[str(self.root / "usr/sbin/plymouth-set-default-theme")]])
        self.assertEqual(plan["mode"], "plan-only")
        self.assertFalse(plan["reboot"])
        self.assertFalse(plan["bootloader_or_cmdline_changes"])

    def test_apply_backs_up_before_any_command(self):
        plan = self.inspect()
        original_plan = json.dumps(plan, sort_keys=True)
        before_initrd = Path(plan["initrd"]).read_bytes()
        def runner(command):
            backup = Path(plan["backup"])
            self.assertEqual((backup / "initrd.previous").read_bytes(), before_initrd)
            self.assertTrue((backup / "recovery.json").is_file())
            self.assertTrue((backup / "plymouthd.conf.previous").is_file())
            return self.runner(command)
        BOOT.apply_plan(plan, self.source, runner=runner)
        self.assertEqual(json.dumps(plan, sort_keys=True), original_plan)
        self.assertTrue((Path(plan['backup']) / 'verified-initramfs.json').exists())
        self.assertTrue((Path(plan["backup"]) / "APPLIED").is_file())
        self.assertEqual(self.calls[1:3], plan["commands"])
        self.assertEqual(self.calls[2][-3:], ["-u", "-k", self.kernel])
        for name in BOOT.FILES:
            self.assertEqual((Path(plan["destination"]) / name).read_bytes(), (self.source / name).read_bytes())
        self.assertEqual(Path(plan["backup"]).stat().st_mode & 0o777, 0o700)

    def test_missing_prerequisites_fail_without_mutation(self):
        names = ["usr/bin/unmkinitramfs", "usr/sbin/update-initramfs", "usr/sbin/plymouthd", "usr/sbin/plymouth-set-default-theme",
                 "boot/initrd.img-6.1.0-test", "boot/vmlinuz-6.1.0-test",
                 "usr/lib/aarch64-linux-gnu/plymouth/script.so", "usr/lib/aarch64-linux-gnu/plymouth/label.so",
                 "usr/share/initramfs-tools/hooks/plymouth", "usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        for name in names:
            with self.subTest(name=name):
                path = self.root / name
                mode, data = path.stat().st_mode, path.read_bytes()
                path.unlink()
                before = self.snapshot()
                with self.assertRaises((OSError, BOOT.InstallError)):
                    self.inspect()
                self.assertEqual(before, self.snapshot())
                path.write_bytes(data)
                path.chmod(mode)

    def test_platform_kernel_and_space_gates(self):
        for kwargs in ({"system": "Darwin"}, {"machine": "x86_64"}):
            with self.assertRaises(BOOT.InstallError):
                self.inspect(**kwargs)
        self.kernel = "../bad"
        with self.assertRaises(BOOT.InstallError):
            self.inspect()
        self.kernel = "6.1.0-test"
        with patch.object(BOOT.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(1, 1, 0)):
            with self.assertRaises(BOOT.InstallError):
                self.inspect()

    def test_source_and_destination_symlink_hardlink_rejected(self):
        source = self.source / BOOT.FILES[0]
        data = source.read_bytes()
        source.unlink()
        source.symlink_to(self.source / BOOT.FILES[1])
        with self.assertRaises(BOOT.InstallError):
            self.inspect()
        source.unlink()
        source.write_bytes(data)
        os.link(source, self.base / "extra-link")
        with self.assertRaises(BOOT.InstallError):
            self.inspect()
        (self.base / "extra-link").unlink()
        destination = self.root / "usr/share/plymouth/themes/sage-lantern"
        destination.symlink_to(self.source)
        with self.assertRaises(BOOT.InstallError):
            self.inspect()

    def test_initrd_hardlink_and_config_symlink_rejected(self):
        initrd = self.root / "boot/initrd.img-6.1.0-test"
        os.link(initrd, self.base / "linked-initrd")
        with self.assertRaises(BOOT.InstallError):
            self.inspect()
        (self.base / "linked-initrd").unlink()
        config = self.root / "etc/plymouth/plymouthd.conf"
        config.unlink()
        config.symlink_to(initrd)
        with self.assertRaises(BOOT.InstallError):
            self.inspect()

    def test_existing_backup_and_theme_require_review(self):
        plan = self.inspect()
        for name in ("backup", "destination"):
            path = Path(plan[name])
            path.mkdir()
            with self.assertRaises(BOOT.InstallError):
                self.inspect()
            path.rmdir()

    def test_source_change_rejected_before_backup(self):
        plan = self.inspect()
        (self.source / BOOT.FILES[1]).write_text("changed")
        with self.assertRaises(BOOT.InstallError):
            BOOT.apply_plan(plan, self.source, runner=self.runner)
        self.assertFalse(Path(plan["backup"]).exists())

    def test_configuration_change_invalidates_review_before_mutation(self):
        plan = self.inspect()
        Path(plan["config"]).write_text("changed")
        with self.assertRaises(BOOT.InstallError):
            BOOT.apply_plan(plan, self.source, runner=self.runner)
        self.assertFalse(Path(plan["backup"]).exists())
        self.assertNotEqual(plan["plan_sha256"], self.inspect()["plan_sha256"])

    def test_absent_configuration_is_recorded_without_read(self):
        (self.root / "etc/plymouth/plymouthd.conf").unlink()
        plan = self.inspect()
        self.assertFalse(plan["config_existed"])
        BOOT.apply_plan(plan, self.source, runner=self.runner)
        self.assertFalse((Path(plan["backup"]) / "plymouthd.conf.previous").exists())

    def test_command_failure_retains_recovery_without_success(self):
        plan = self.inspect()
        def fail(command):
            raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(BOOT.InstallError):
            BOOT.apply_plan(plan, self.source, runner=fail)
        backup = Path(plan["backup"])
        self.assertTrue((backup / "initrd.previous").is_file())
        self.assertFalse((backup / "APPLIED").exists())
        self.assertEqual(json.loads((backup / "recovery.json").read_text())["previous_default"], "old")

    def test_cli_root_and_review_digest_required(self):
        with patch.object(BOOT.os, "geteuid", return_value=1000), patch.object(BOOT, "inspect") as inspect:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(BOOT.main(["--apply"]), 2)
            inspect.assert_not_called()
        plan = self.inspect()
        with patch.object(BOOT.os, "geteuid", return_value=0), patch.object(BOOT, "inspect", return_value=plan), \
                patch.object(BOOT, "apply_plan") as apply:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(BOOT.main(["--apply", "--expected-manifest", "wrong"]), 2)
            apply.assert_not_called()

    def test_prompt_diagnostics_and_native_script_contract(self):
        script = (self.source / "sage-lantern.script").read_text()
        for text in ("SAGE Lantern", "Tap. Ask. Learn.", "Esc: boot details",
                     "SetDisplayNormalFunction", "SetDisplayPasswordFunction", "SetDisplayQuestionFunction",
                     "SetDisplayMessageFunction", "SetHideMessageFunction", "Image.Text"):
            self.assertIn(text, script)
        self.assertNotIn("SetKeyboardInputFunction", script)
        self.assertNotIn(".png", script)

    def test_boot_request_missing_disabled_and_unknown_never_apply(self):
        for content, status in [('quiet', 'not_requested'), ('splash plymouth.enable=0', 'disabled'),
                                ('splash rd.plymouth=0', 'disabled'), ('splash nosplash', 'disabled'),
                                ('splash plymouth.enable=false', 'disabled'),
                                ('splash plymouth.enable=unexpected', 'unsupported')]:
            (self.root / 'proc/cmdline').write_text(content)
            plan = self.inspect()
            self.assertEqual(plan['boot_configuration']['current_kernel'], status)
            with self.assertRaises(BOOT.InstallError):
                BOOT.apply_plan(plan, self.source, runner=self.runner)
            self.assertFalse(Path(plan['backup']).exists())
        (self.root / 'proc/cmdline').unlink()
        self.assertEqual(self.inspect()['boot_configuration']['current_kernel'], 'unknown')

    def test_single_extraction_layout_and_directory_links(self):
        import hashlib
        members = {'etc/plymouth/plymouthd.conf': b'[Daemon]\nTheme=sage-lantern\n',
                   'usr/share/plymouth/themes/sage-lantern/sage-lantern.script': b'theme'}
        plan = self.inspect()
        plan['initramfs_files'] = {name: hashlib.sha256(raw).hexdigest() for name, raw in members.items()}
        for variant in ('root', 'main', 'split', 'root_link', 'main_link', 'intermediate_link'):
            with self.subTest(variant=variant):
                def extract(command):
                    destination = Path(command[2])
                    layout = destination if variant == 'root' else destination / 'main'
                    for relative, raw in members.items():
                        path = layout / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(raw)
                    if variant == 'split':
                        relative = 'usr/share/plymouth/themes/sage-lantern/sage-lantern.script'
                        path = destination / relative
                        path.parent.mkdir(parents=True)
                        (layout / relative).rename(path)
                    link = {'root_link': destination, 'main_link': layout,
                            'intermediate_link': layout / 'usr/share'}.get(variant)
                    if link is not None:
                        saved = link.with_name(link.name + '-saved')
                        link.rename(saved)
                        link.symlink_to(saved, target_is_directory=True)
                if variant in ('root', 'main'):
                    self.assertEqual(BOOT.verify_initramfs(plan, runner=extract), plan['initrd_sha256'])
                else:
                    with self.assertRaises((BOOT.InstallError, OSError)):
                        BOOT.verify_initramfs(plan, runner=extract)

    def test_rebuilt_member_missing_tampered_or_symlink_no_success(self):
        for fault in ('missing', 'tampered', 'symlink', 'duplicate', 'config_missing'):
            with self.subTest(fault=fault):
                plan = self.inspect()
                def runner(command):
                    result = self.runner(command)
                    if command[0].endswith('unmkinitramfs'):
                        path = Path(command[2]) / 'main/usr/share/plymouth/themes/sage-lantern/sage-lantern.script'
                        if fault == 'duplicate':
                            duplicate = Path(command[2]) / 'usr/share/plymouth/themes/sage-lantern/sage-lantern.script'
                            duplicate.parent.mkdir(parents=True)
                            shutil.copyfile(path, duplicate)
                        elif fault == 'config_missing':
                            (Path(command[2]) / 'main/etc/plymouth/plymouthd.conf').unlink()
                        else:
                            path.unlink()
                        if fault == 'tampered': path.write_text('wrong')
                        if fault == 'symlink': path.symlink_to(self.source / BOOT.FILES[1])
                    return result
                with self.assertRaises(BOOT.InstallError):
                    BOOT.apply_plan(plan, self.source, runner=runner)
                self.assertFalse((Path(plan['backup']) / 'APPLIED').exists())
                self.assertTrue((Path(plan['backup']) / 'initrd.previous').exists())
                shutil.rmtree(plan['backup'])
                shutil.rmtree(plan['destination'])
                self.current = 'old'


if __name__ == "__main__":
    unittest.main()
