from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / 'scripts/lantern/install-camera-local.sh'


class CameraInstallPlanTests(unittest.TestCase):
    def run_plan(self, *arguments):
        return subprocess.run(['bash', str(INSTALLER), *arguments], capture_output=True, text=True, timeout=5)

    def test_distinct_units_receive_explicit_camera_bindings(self):
        for serial in ('A_5E4BEE10', 'B_01234567'):
            device = f'/dev/v4l/by-id/usb-046d_0825_{serial}-video-index0'
            result = self.run_plan('--device', device)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'--device={device}\n', result.stdout)
            self.assertIn('Plan only', result.stderr)
            self.assertIn('DynamicUser=yes', result.stdout)
            self.assertIn('IPAddressDeny=any', result.stdout)
            self.assertIn('InaccessiblePaths=-/etc/sage-lantern -/var/lib/sage-lantern', result.stdout)
            self.assertEqual(result.stdout.count('ExecStart='), 1)

    def test_missing_ambiguous_or_unsafe_device_never_reaches_install(self):
        cases = [(), ('--install',), ('--device',), ('--unknown',),
                 ('--device', '/dev/video0'), ('--device', '/tmp/camera'),
                 ('--device', '/dev/v4l/by-id/../../video0'),
                 ('--device', '/dev/v4l/by-id/camera%u-video-index0'),
                 ('--device', '/dev/v4l/by-id/camera\nExecStart=other-video-index0'),
                 ('--device', '/dev/v4l/by-id/camera name-video-index0'),
                 ('--device', '/dev/v4l/by-id/camera-video-index0', '--device', '/dev/v4l/by-id/other-video-index0')]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.run_plan(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')

    def test_post_start_checks_new_runtime_identity_and_health_without_capture(self):
        source = INSTALLER.read_text()
        self.assertLess(source.index('from sage_lantern.camera_identity import verify_camera_identity'),
                        source.index('install -d -o root'))
        self.assertLess(source.index('systemctl restart'), source.index('-m sage_lantern.camera_identity'))
        self.assertIn('--device "$camera_device" --unit-sha256 "$unit_sha256" --helper-sha256 "$helper_sha256"', source)
        self.assertIn('sha256sum "$temporary_unit"', source)
        self.assertIn('sha256sum "${repo_root}/scripts/lantern/unit-a-camera-server.py"', source)
        self.assertNotIn('http://127.0.0.1:3198/health', source)
        self.assertNotIn('/capture', source)
        self.assertNotIn('/preview', source)


if __name__ == '__main__':
    unittest.main()
