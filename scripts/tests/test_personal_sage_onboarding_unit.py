import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


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
        self.profile = self.root / 'etc/sage/provisioning-profile.yaml'
        self.profile.parent.mkdir(parents=True)
        profile_source = 'software/config/sage-provisioning-profile.example.yaml'
        profile_data = (ROOT / profile_source).read_bytes()
        profile_payload = self.release / profile_source
        profile_payload.parent.mkdir(parents=True)
        profile_payload.write_bytes(profile_data)
        self.manifest['files'].append(dict(path=profile_source, bytes=len(profile_data),
                                          sha256=hashlib.sha256(profile_data).hexdigest()))
        self.profile.write_bytes(profile_data)
        self.profile.chmod(0o600)
        self.root_owned_paths = {self.profile}
        original_stat = Path.stat
        def root_owned(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path in self.root_owned_paths:
                class Owned:
                    st_uid = 0
                    def __getattr__(self, name):
                        return getattr(info, name)
                return Owned()
            return info
        self.stat_patch = patch.object(Path, 'stat', root_owned)
        self.stat_patch.start()
        self.addCleanup(self.stat_patch.stop)

    def test_start_prerequisites_not_voice_or_encryption_attestations(self):
        text = self.data.decode()
        self.assertNotIn('LOCAL_VOICE_READY', text)
        for required in ('/etc/sage-lantern-device-lock.enabled', '/etc/sage-lantern/appliance-installed.json',
                         '/etc/sage/provisioning-profile.yaml',
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
                        'SAGE_CMT_P2P_ADDR=tcp://127.0.0.1:26656', 'SAGE_TLS_ADDR=127.0.0.1:8443'):
            self.assertIn('Environment=' + setting + '\n', self.data.decode())
        self.assertIn('ExecStartPre=/opt/sage-lantern/bin/sage-gui check-private-config\n', self.data.decode())
        self.assertIn('CapabilityBoundingSet=CAP_DAC_READ_SEARCH\n', self.data.decode())
        self.assertIn('AmbientCapabilities=CAP_DAC_READ_SEARCH\n', self.data.decode())
        self.assertNotIn('SAGE_LANTERN_PRIVATE_LISTENERS', self.data.decode())
        self.assertNotIn('check-lantern-private-config', self.data.decode())

    def test_release_without_rest_override_rejected_even_when_installed_matches(self):
        data = self.data.replace(b'Environment=REST_ADDR=127.0.0.1:8080\n', b'')
        (self.release / SOURCE).write_bytes(data)
        self.installed.write_bytes(data)
        self.manifest['files'][0].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        with self.assertRaisesRegex(ValueError, 'loopback override missing'):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root)

    def test_release_without_provisioning_profile_gate_rejected(self):
        data = self.data.replace(b'ConditionPathExists=/etc/sage/provisioning-profile.yaml\n', b'')
        (self.release / SOURCE).write_bytes(data)
        self.installed.write_bytes(data)
        self.manifest['files'][0].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        with self.assertRaisesRegex(ValueError, 'provisioning profile gate missing'):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root)

    def test_installed_provisioning_profile_must_match_release(self):
        self.profile.write_text(self.profile.read_text().replace('private_listeners: true',
                                                                 'private_listeners: false'))
        with self.assertRaisesRegex(ValueError, 'differs from reviewed release'):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root)

    def test_old_installed_base_is_rejected_not_overwritten(self):
        self.installed.write_text('ConditionPathExists=/var/lib/sage-lantern/LOCAL_VOICE_READY\n')
        original = self.installed.read_bytes()
        with self.assertRaises(ValueError):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root)
        self.assertEqual(self.installed.read_bytes(), original)

    def test_successor_may_replace_the_predecessor_personal_sage_unit(self):
        old = b'[Unit]\nDescription=old r17 personal SAGE unit\n'
        self.installed.write_bytes(old)
        predecessor = dict(root='/synthetic-r17', manifest={})
        with patch.object(MODULE, 'predecessor_targets', return_value={
                '/etc/systemd/system/sage-lantern-personal-sage.service':
                    hashlib.sha256(old).hexdigest()}):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root,
                                            predecessor=predecessor)

    def test_effective_policy_overrides_rejected(self):
        dropin = self.installed.with_name(self.installed.name + '.d')
        dropin.mkdir()
        (dropin / 'override.conf').write_text('[Unit]\nConditionPathExists=\n')
        with self.assertRaises(ValueError):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root)

    def test_successor_preserves_only_the_reviewed_crash_privacy_dropin(self):
        old = b'[Unit]\nDescription=old r17 personal SAGE unit\n'
        self.installed.write_bytes(old)
        dropin = self.installed.with_name(self.installed.name + '.d')
        dropin.mkdir()
        override = dropin / '90-sage-lantern-crash-privacy.conf'
        override.write_bytes(b'[Service]\nLimitCORE=0:0\n')
        override.chmod(0o644)
        self.root_owned_paths.add(override)
        predecessor = dict(root='/synthetic-r17', manifest={})
        with patch.object(MODULE, 'predecessor_targets', return_value={
                '/etc/systemd/system/sage-lantern-personal-sage.service':
                    hashlib.sha256(old).hexdigest()}):
            MODULE.onboarding_service_check(self.release, self.manifest, root=self.root,
                                            predecessor=predecessor)
        (dropin / '99-extra.conf').write_text('[Service]\nLimitCORE=1:1\n')
        with patch.object(MODULE, 'predecessor_targets', return_value={
                '/etc/systemd/system/sage-lantern-personal-sage.service':
                    hashlib.sha256(old).hexdigest()}):
            with self.assertRaisesRegex(ValueError, 'overrides require explicit review'):
                MODULE.onboarding_service_check(self.release, self.manifest, root=self.root,
                                                predecessor=predecessor)

    def test_missing_initialized_files_cannot_satisfy_unit_conditions(self):
        conditions = [line.split('=', 1)[1] for line in self.data.decode().splitlines() if line.startswith('ConditionFileNotEmpty=')]
        self.assertEqual(len(conditions), 6)
        for condition in conditions:
            self.assertFalse((self.root / condition.lstrip('/')).exists())
        self.assertFalse((self.root / 'var/lib/sage-lantern/LOCAL_VOICE_READY').exists())


if __name__ == '__main__':
    unittest.main()
