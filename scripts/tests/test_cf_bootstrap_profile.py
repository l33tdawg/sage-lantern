"""Run the actual shell platform gate without accessing or changing a host OS."""
from pathlib import Path
import subprocess
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'bootstrap-lantern-os.sh'
GATE = SCRIPT.read_text().split('validate_platform() {', 1)[1].split('\n}\nvalidate_platform', 1)[0]

class PlatformTests(unittest.TestCase):
    def gate(self, profile='orangepi5plus-ubuntu26.04', os_id='ubuntu', version='26.04',
             codename='resolute', board='Orange Pi 5 Plus', kernel='6.1.115-vendor-rk35xx'):
        script = 'die() { exit 2; }; uname() { echo "$kernel"; };\n' + GATE
        return subprocess.run(['/bin/bash', '-c', script], env={
            'profile': profile, 'os_id': os_id, 'os_version': version, 'os_codename': codename,
            'os_like': 'debian', 'board_model': board, 'kernel': kernel}, capture_output=True).returncode

    def test_reviewed_profile(self):
        self.assertEqual(self.gate(), 0)

    def test_drift_and_wrong_board_refused(self):
        for changes in ({'os_id':'debian'}, {'version':'24.04'}, {'codename':'noble'},
                        {'board':'Orange Pi 5'}, {'kernel':'6.12.0'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.gate(**changes), 2)

    def test_default_still_requires_debian12(self):
        self.assertEqual(self.gate(profile='debian12'), 2)
        self.assertEqual(self.gate(profile='debian12', os_id='debian', version='12', codename='bookworm'), 0)

if __name__ == '__main__':
    unittest.main()
