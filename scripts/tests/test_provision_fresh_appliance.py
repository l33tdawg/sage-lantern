import argparse
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('phase_runner', ROOT / 'scripts/lantern/provision-fresh-appliance.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PhaseRunnerTests(unittest.TestCase):
    def test_identity_plan_requires_consent_and_no_secret_arguments(self):
        args = argparse.Namespace(unit_id='unit-b', phase='identity', app_options=[], allow_native_build=False,
            install_os_packages=False, owner_approved_companion_bootstrap=True, identity_config_sha256='b' * 64)
        steps = MODULE.commands(args, Path('/synthetic/payload'))
        self.assertEqual(len(steps), 1)
        self.assertIn('init-lantern-private', steps[0])
        self.assertEqual(steps[0][-1], 'unit-b')
        self.assertIn('SAGE_LANTERN_PRIVATE_LISTENERS=1', steps[0])
        self.assertNotIn('serve', steps[0])
        args.owner_approved_companion_bootstrap = False
        with self.assertRaises(ValueError):
            MODULE.commands(args, Path('/synthetic/payload'))

    def test_identity_config_orders_validation_init_and_install(self):
        from types import SimpleNamespace
        args = argparse.Namespace(unit_id='unit-b', phase='identity-config', app_options=[], allow_native_build=False,
            install_os_packages=False, owner_approved_companion_bootstrap=True, identity_config_sha256='b' * 64,
            commission_plan='/synthetic/plan.json', commission_plan_sha256='c' * 64, execute=True)
        actions = []
        prepared = object()
        def run(*unused, **options):
            self.assertTrue(options['capture_output'])
            self.assertEqual(options['timeout'], 120)
            actions.append('initialize')
            return SimpleNamespace(stdout=b'actual delegate output')
        with patch.object(MODULE, 'verify_release', return_value=Path('/synthetic/payload')), \
             patch.object(MODULE.os, 'geteuid', return_value=0), patch.object(MODULE.sys, 'platform', 'linux'), \
             patch.object(MODULE.platform, 'machine', return_value='aarch64'), \
             patch.object(MODULE, 'identity_preflight', side_effect=lambda *args: actions.append('identity-check')), \
             patch.object(MODULE, 'commission_preflight', side_effect=lambda *args: actions.append('config-check') or prepared), \
             patch.object(MODULE, 'commission_begin', side_effect=lambda *args: actions.append('durable-fence')), \
             patch.object(MODULE, 'commission_install', side_effect=lambda args, raw, state: actions.append(('configure', raw, state))):
            result = MODULE.execute(args, run=run)
        self.assertEqual(actions, ['identity-check', 'config-check', 'durable-fence', 'initialize', ('configure', b'actual delegate output', prepared)])
        self.assertTrue(result['configuration_applied'])
        self.assertFalse(result['ready'])
        self.assertFalse(result['provisioned'])

    def setUp(self):
        self.args = argparse.Namespace(output='/synthetic/output', manifest_sha256='a' * 64,
            unit_id='unit-b', phase='base', execute=False, allow_native_build=True,
            install_os_packages=False, app_options=[])
        self.payload = Path('/synthetic/output/payload')

    def test_base_actual_command_sequence_no_verify_or_activation(self):
        steps = MODULE.commands(self.args, self.payload)
        self.assertEqual([Path(step[1]).name for step in steps], ['bootstrap-lantern-os.sh', 'install-lantern-unit.sh', 'provision-lantern-unit.sh'])
        self.assertEqual(steps[0][-1], '--check')
        self.assertEqual(steps[2][-3:], ['prepare', '--unit-id', 'unit-b'])
        self.assertNotIn('verify', str(steps))
        self.assertNotIn('systemctl', str(steps))
        self.assertNotIn('LOCAL_VOICE_READY', str(steps))

    def test_no_execution_by_default(self):
        run = Mock()
        with patch.object(MODULE, 'verify_release', return_value=self.payload):
            result = MODULE.execute(self.args, run=run)
        run.assert_not_called()
        self.assertFalse(result['executed'])
        self.assertFalse(result['ready'])
        self.assertFalse(result['provisioned'])

    def test_explicit_native_build_and_apt_permission(self):
        self.args.allow_native_build = False
        with self.assertRaises(ValueError):
            MODULE.commands(self.args, self.payload)
        self.args.allow_native_build = True
        self.args.install_os_packages = True
        self.assertEqual(MODULE.commands(self.args, self.payload)[0][-1], '--install')

    def test_real_executor_order_reverify_and_fail_stop(self):
        self.args.execute = True
        run = Mock(side_effect=[None, subprocess.CalledProcessError(1, 'base')])
        with patch.object(MODULE, 'node_preflight'), patch.object(MODULE, 'verify_release', return_value=self.payload) as verify, patch.object(MODULE.os, 'geteuid', return_value=0), patch.object(MODULE.sys, 'platform', 'linux'), patch.object(MODULE.platform, 'machine', return_value='aarch64'), patch.object(MODULE.os.path, 'lexists', return_value=False):
            with self.assertRaises(subprocess.CalledProcessError):
                MODULE.execute(self.args, run=run)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(verify.call_count, 3)
        self.assertTrue(all(call.kwargs['check'] for call in run.call_args_list))
        self.assertTrue(all('shell' not in call.kwargs for call in run.call_args_list))

    def test_existing_base_blocks_before_apt(self):
        self.args.execute = self.args.install_os_packages = True
        run = Mock()
        with patch.object(MODULE, 'verify_release', return_value=self.payload), patch.object(MODULE.os, 'geteuid', return_value=0), patch.object(MODULE.sys, 'platform', 'linux'), patch.object(MODULE.platform, 'machine', return_value='aarch64'), patch.object(MODULE.os.path, 'lexists', return_value=True):
            with self.assertRaises(ValueError):
                MODULE.execute(self.args, run=run)
        run.assert_not_called()

    def test_missing_node_blocks_before_any_base_mutation(self):
        self.args.execute = self.args.install_os_packages = True
        run = Mock()
        with patch.object(MODULE, 'verify_release', return_value=self.payload), \
             patch.object(MODULE.os, 'geteuid', return_value=0), \
             patch.object(MODULE.sys, 'platform', 'linux'), \
             patch.object(MODULE.platform, 'machine', return_value='aarch64'), \
             patch.object(MODULE.os.path, 'lexists', return_value=False), \
             patch.object(MODULE, 'node_preflight', side_effect=ValueError('missing Node')):
            with self.assertRaisesRegex(ValueError, 'missing Node'):
                MODULE.execute(self.args, run=run)
        run.assert_not_called()

    def test_app_phase_checks_then_invokes_actual_installer(self):
        self.args.phase, self.args.allow_native_build = 'app', False
        self.args.app_options = ['--', '--account', 'operatorb', '--alsa-device', 'hw:CARD=AudioB,DEV=0', '--allow-owner-vault-setup']
        steps = MODULE.commands(self.args, self.payload)
        self.assertEqual(len(steps), 2)
        self.assertNotIn('--install', steps[0])
        self.assertEqual(steps[1], steps[0] + ['--install'])
        self.assertIn('/etc/sage-lantern/unit.env', steps[0])
        self.assertIn(str(self.payload / MODULE.APP), steps[0])
        self.assertIn('unit-b', steps[0])
        self.assertNotIn('unit-a', steps[0])

    def test_passthrough_cannot_override_runner_or_invoke_keys(self):
        for options in (['--unit-id', 'unit-a'], ['--install'], ['--manifest', '/other'],
                        ['--operator-key', '/secret'], ['--account'], ['--account', 'one', '--account', 'two']):
            with self.subTest(options=options), self.assertRaises(ValueError):
                MODULE.app_options(options)

    def test_bad_reviewed_hash_prevents_verifier_or_commands(self):
        self.args.manifest_sha256 = 'placeholder'
        with patch.object(MODULE, 'helper') as helper:
            with self.assertRaises(ValueError):
                MODULE.verify_release(self.args)
            helper.assert_not_called()


if __name__ == '__main__':
    unittest.main()
