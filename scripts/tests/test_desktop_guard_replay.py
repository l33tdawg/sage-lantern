"""Replay actual BPF hook bodies; this does not load or qualify kernel policy."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DesktopGuardReplayTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler required')
    def test_attack_and_identity_replay(self):
        with tempfile.TemporaryDirectory(prefix='desktop-guard-replay-') as directory:
            binary = Path(directory) / 'replay'
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', '-I' + str(ROOT / 'software/security'),
                '-I' + str(ROOT / 'scripts/tests/native'),
                str(ROOT / 'scripts/tests/native/desktop_guard_replay.c'),
                '-o', str(binary)], capture_output=True, check=True, timeout=30)
            result = subprocess.run([str(binary)], capture_output=True, check=True, timeout=5)
            self.assertRegex(result.stdout.decode(), r'\d+ hook decision checks passed')
            self.assertIn('kernel_enforcement_qualified=false', result.stdout.decode())


if __name__ == '__main__':
    unittest.main()
