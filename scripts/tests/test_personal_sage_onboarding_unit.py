import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = 'software/systemd/sage-lantern-personal-sage.service'
SPEC = importlib.util.spec_from_file_location('onboarding_installer', ROOT / 'scripts/lantern/install-fleet-appliance-local.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OnboardingUnitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.release = self.root / 'release'
        source = self.release / SOURCE
        source.parent.mkdir(parents=True)
        self.data = (ROOT / SOURCE).read_bytes()
        source.write_bytes(self.data)
        self.manifest = {'files': [dict(path=SOURCE, bytes=len(self.data), sha256=hashlib.sha256(self.data).hexdigest())]}
        self.installed = self.root / 'etc/systemd/system/sage-lantern-personal-sage.service'
        self.installed.parent.mkdir(parents=True)
        self.installed.write_bytes(self.data)

    def test_start_prerequisites_not_voice_or_encryption_attestations(self):
        text = self.data.decode()
        self.assertNotIn('LOCAL_VOICE_READY', text)
        for required in ('/etc/sage-lantern-device-lock.enabled', '/etc/sage-lantern/appliance-installed.json',
                         'config.yaml', 'agent.key', 'genesis.json', 'priv_validator_key.json', 'node_key.json', 'priv_validator_state.json'):
            self.assertIn(required, text)
        self.assertIn('User=sage-lantern\n', text)
        self.assertIn('ExecStart=/opt/sage-lantern/bin/sage-gui serve\n', text)
        self.assertNotIn('vault-setup.enabled', text)
        self.assertNotIn('unlock', text.lower())
        MODULE.onboarding_service_check(self.release, self.manifest, root=self.root)

    def test_voice_keeps_its_separate_ready_gate(self):
        voice = (ROOT / 'software/systemd/sage-lantern-voice-loop.service').read_text()
        self.assertIn('ConditionPathExists=/var/lib/sage-lantern/LOCAL_VOICE_READY', voice)
        auth = (ROOT / 'software/systemd/sage-lantern-device-auth.service').read_text()
        self.assertIn('BindsTo=sage-lantern-personal-sage.service', auth)

    def test_listener_overrides_are_explicit(self):
        for setting in ('REST_ADDR=127.0.0.1:8080', 'SAGE_CMT_RPC_ADDR=tcp://127.0.0.1:26657',
                        'SAGE_CMT_P2P_ADDR=tcp://127.0.0.1:26656', 'SAGE_TLS_ADDR=127.0.0.1:8443',
                        'SAGE_LANTERN_PRIVATE_LISTENERS=1'):
            self.assertIn('Environment=' + setting + '\n', self.data.decode())
        self.assertIn('ExecStartPre=/opt/sage-lantern/bin/sage-gui check-lantern-private-config\n', self.data.decode())

    def test_release_without_rest_override_rejected_even_when_installed_matches(self):
        data = self.data.replace(b'Environment=REST_ADDR=127.0.0.1:8080\n', b'')
        (self.release / SOURCE).write_bytes(data)
        self.installed.write_bytes(data)
        self.manifest['files'][0].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        with self.assertRaisesRegex(ValueError, 'loopback override missing'):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root)

    def test_old_installed_base_is_rejected_not_overwritten(self):
        self.installed.write_text('ConditionPathExists=/var/lib/sage-lantern/LOCAL_VOICE_READY\n')
        original = self.installed.read_bytes()
        with self.assertRaises(ValueError):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root)
        self.assertEqual(self.installed.read_bytes(), original)

    def test_effective_policy_overrides_rejected(self):
        dropin = self.installed.with_name(self.installed.name + '.d')
        dropin.mkdir()
        (dropin / 'override.conf').write_text('[Unit]\nConditionPathExists=\n')
        with self.assertRaises(ValueError):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root)

    def test_missing_initialized_files_cannot_satisfy_unit_conditions(self):
        conditions = [line.split('=', 1)[1] for line in self.data.decode().splitlines() if line.startswith('ConditionFileNotEmpty=')]
        self.assertEqual(len(conditions), 6)
        for condition in conditions:
            self.assertFalse((self.root / condition.lstrip('/')).exists())
        self.assertFalse((self.root / 'var/lib/sage-lantern/LOCAL_VOICE_READY').exists())


if __name__ == '__main__':
    unittest.main()
