import argparse
import hashlib
import os
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('identity_phase', ROOT / 'scripts/lantern/provision-fresh-appliance.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IdentityPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.payload = self.root / 'payload'
        self.config = self.root / 'var/lib/sage-lantern/sage-personal/config.yaml'
        self.metadata = self.root / 'etc/sage-lantern/unit.env'
        self.profile = self.root / 'etc/sage/provisioning-profile.yaml'
        self.binary = self.root / 'opt/sage-lantern/bin/sage-gui'
        files = {self.config: b'quorum: {enabled: false}\nfederation: {enabled: false}\n',
                 self.metadata: b'UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n',
                 self.profile: (ROOT / MODULE.PROVISIONING_PROFILE).read_bytes(),
                 self.binary: b'synthetic nonexecutable',
                 self.payload / MODULE.PROVISIONING_PROFILE: (ROOT / MODULE.PROVISIONING_PROFILE).read_bytes(),
                 self.payload / 'software/bundle/linux-arm64/bin/sage-gui': b'synthetic nonexecutable'}
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o755 if path.name == 'sage-gui' else 0o600)
        self.args = argparse.Namespace(unit_id='unit-b', identity_config_sha256=hashlib.sha256(files[self.config]).hexdigest())

    def checked(self):
        original = Path.stat
        def synthetic_owner(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path in (self.metadata, self.profile, self.binary):
                class Owned:
                    st_uid = 0
                    def __getattr__(self, name):
                        return getattr(info, name)
                return Owned()
            return info
        from types import SimpleNamespace
        with patch.object(Path, 'stat', synthetic_owner), patch.object(MODULE.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())):
            MODULE.identity_preflight(self.args, self.payload, root=self.root)

    def test_matched_inputs_no_commands_or_keys(self):
        self.checked()
        self.assertEqual(list(self.root.rglob('*.key')), [])

    def test_binding_config_and_binary_mismatches(self):
        for path in (self.config, self.metadata, self.profile, self.binary):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(b'mismatch')
                with self.assertRaises(ValueError):
                    self.checked()
                path.write_bytes(original)

    def test_symlink_and_writable_config_rejected(self):
        self.config.chmod(0o666)
        with self.assertRaises(ValueError):
            self.checked()
        self.config.unlink()
        self.config.symlink_to(self.metadata)
        with self.assertRaises(ValueError):
            self.checked()


if __name__ == '__main__':
    unittest.main()
