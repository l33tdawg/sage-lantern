import argparse
import hashlib
import importlib.util
import json
import os
from types import SimpleNamespace
from pathlib import Path
import stat
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('fresh_app', ROOT / 'scripts/lantern/install-fleet-appliance-local.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONFIG = MODULE.helper('test_unit_config', MODULE.HELPERS[0])


class FreshAppTests(unittest.TestCase):
    def test_node_preflight_rejects_missing_canonical_path(self):
        with patch.object(MODULE.Path, 'is_file', return_value=False), \
             patch.object(MODULE.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, '/usr/bin/node'):
                MODULE.node_preflight()
        run.assert_not_called()

    def test_node_preflight_validates_version_and_clears_environment(self):
        for version, accepted in (([22, 13, 0], True), ([22, 22, 0], True),
                                  ([23, 0, 0], True), ([22, 12, 9], False),
                                  ([20, 19, 0], False), ([True, 99, 0], False),
                                  ([22, 13], False), ('22.22.0', False)):
            with self.subTest(version=version), \
                 patch.object(MODULE.Path, 'is_file', return_value=True), \
                 patch.object(MODULE.os, 'access', return_value=True), \
                 patch.object(MODULE.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(version))) as run:
                if accepted:
                    MODULE.node_preflight()
                else:
                    with self.assertRaises(ValueError):
                        MODULE.node_preflight()
                self.assertEqual(run.call_args.args[0][0], '/usr/bin/node')
                self.assertEqual(run.call_args.kwargs['env'], {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent'})

    def setUp(self):
        self.start_listxattr = patch.object(MODULE.os, 'listxattr', return_value=[], create=True)
        self.start_listxattr.start()
        self.addCleanup(self.start_listxattr.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / 'target'
        self.root.mkdir()
        self.release = self.directory / 'release'
        self.release.mkdir()
        self.entries = []
        self.add_source(MODULE.AUTH, b'print("synthetic auth fixture")\n', 'root_helper')
        self.add_source(MODULE.AUTH_UNIT, (ROOT / MODULE.AUTH_UNIT).read_bytes(), 'systemd')
        for name in MODULE.APPLIANCE_FILES:
            self.add_source(name, (ROOT / name).read_bytes(), 'support')
        for name in ('dist/server/index.js', 'package.json', 'package-lock.json', 'node_modules/vinext/dist/cli.js'):
            self.add_source(MODULE.APP + name, b'synthetic app fixture\n', 'ui')
        for name in (MODULE.INSTALLER, *MODULE.HELPERS):
            self.add_source(name, (ROOT / name).read_bytes(), 'installer' if name == MODULE.INSTALLER else 'support')
        self.manifest = dict(release_id='synthetic-test', files=self.entries)
        self.ui = {entry['path']: entry for entry in self.entries if entry['role'] == 'ui'}
        self.plan = dict(schema='sage.lantern.unit-plan.v1', unit_id='unit-b', operator_account='fixtureoperator', radio=dict(usb_serial='B-serial', vendor_id='1234', product_id='5678', stable_path='/dev/serial/by-id/usb-B-serial', public_key='12' * 32, public_fingerprint='sha256:' + hashlib.sha256(bytes.fromhex('12' * 32)).hexdigest()), audio=dict(usb_serial='B-AUDIO', vendor_id='1234', product_id='5678', stable_path='/dev/snd/by-id/usb-B-AUDIO', alsa_device='hw:CARD=FixtureB,DEV=0'))
        self.rules = CONFIG.audio_udev_rules(self.plan)

    def add_source(self, name, data, role):
        path = self.release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644)
        self.entries.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=0o644, role=role))

    def install(self, approval=False):
        with patch.object(MODULE.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid() or 1001)), patch.object(MODULE.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=os.getgid() or 1001)):
            return MODULE.install_files(self.plan, self.rules, self.release, self.manifest, self.ui, allow_owner_setup=approval, root=self.root)

    def test_radio_mutation_even_with_matching_manifest_refuses_before_writes(self):
        name = next(iter(MODULE.RADIO_PINS))
        data = (self.release / name).read_bytes() + b'\nReadWritePaths=/\n'
        self.add_source(name, data, 'support')
        with self.assertRaisesRegex(ValueError, 'unreviewed radio commissioning artifact'):
            self.install()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_radio_artifact_pins_match_stager(self):
        stage = MODULE.helper('radio_stage_pin_test', 'scripts/stage-appliance-release.py')
        self.assertEqual(MODULE.RADIO_PINS, stage.RADIO_PINS)

    def test_retained_radio_journal_denies_fresh_install(self):
        for name in ('sage-lantern-radio-commission', 'sage-lantern-radio-operator'):
            with self.subTest(name=name):
                retained = self.root / 'var/lib' / name
                retained.mkdir(parents=True)
                evidence = retained / 'unknown.json'
                evidence.write_bytes(b'retained synthetic unknown')
                with self.assertRaises(ValueError):
                    self.install()
                self.assertEqual(evidence.read_bytes(), b'retained synthetic unknown')
                self.assertFalse((self.root / 'etc/sage-lantern-device-lock.enabled').exists())
                evidence.unlink()
                retained.rmdir()

    def test_b_installs_locked_offline_and_keeps_setup_explicit(self):
        with patch.object(MODULE.subprocess, 'run') as command:
            result = self.install()
            command.assert_not_called()
        self.assertFalse(result['ready'])
        self.assertFalse(result['provisioned'])
        self.assertFalse(result['services_started'])
        self.assertFalse(result['services_enabled'])
        self.assertTrue(result['lock_required'])
        self.assertFalse(result['owner_setup_complete'])
        for name, digest in MODULE.RADIO_PINS.items():
            destination = MODULE.APPLIANCE_FILES[name]
            installed = self.root / destination.lstrip('/')
            self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(), digest)
            self.assertEqual(stat.S_IMODE(installed.stat().st_mode),
                             0o755 if destination.startswith('/usr/local/libexec/') else 0o644)
        self.assertFalse((self.root / 'var/lib/sage-lantern-radio-commission').exists())
        self.assertFalse((self.root / 'var/lib/sage-lantern-radio-operator').exists())
        self.assertFalse((self.root / 'etc/sage-lantern-vault-setup.enabled').exists())
        unit = (self.root / 'etc/systemd/system/sage-lantern-fleet-admin.service').read_text()
        self.assertIn('LANTERN_DEVICE_LOCK=1', unit)
        self.assertIn('unit-b operator', unit)
        self.assertNotIn('/home/radxa', unit)
        self.assertNotIn('Unit A', unit)
        self.assertIn('B-AUDIO', (self.root / 'etc/udev/rules.d/71-sage-lantern-audio.rules').read_text())
        radio = json.loads((self.root / 'usr/local/share/sage-lantern/meshcore-radio-public.json').read_text())
        self.assertEqual(radio['unit_id'], 'unit-b')
        self.assertEqual(radio['usb']['serial'], 'B-serial')
        drain = self.root / MODULE.AUTH_DRAIN.lstrip('/')
        self.assertTrue(drain.is_dir())
        self.assertEqual(list(drain.iterdir()), [])
        for path in (drain, drain.parent):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertEqual(path.stat().st_uid, os.getuid() or 1001)
            self.assertEqual(path.stat().st_gid, os.getgid() or 1001)
        auth_unit = (self.root / 'etc/systemd/system/sage-lantern-device-auth.service').read_text()
        self.assertIn('--drain-report-dir ' + MODULE.AUTH_DRAIN, auth_unit)
        self.assertEqual(auth_unit.count('--drain-report-dir'), 1)
        voice = (self.root / 'etc/systemd/system/sage-lantern-voice-loop.service.d/30-unit-audio.conf').read_text()
        self.assertIn('--alsa-device hw:CARD=FixtureB,DEV=0', voice)
        broker = (self.root / 'etc/systemd/system/sage-lantern-voice-loop.service.d/40-mesh-broker.conf').read_text()
        self.assertIn('Restart=no', broker)
        self.assertIn('ConditionPathExists=/run/sage-lantern-mesh-control/voice-start-authorized.json', broker)
        self.assertFalse((self.root / 'run').exists())
        session = (self.root / 'usr/local/bin/sage-lantern-session').read_text()
        self.assertIn('/home/fixtureoperator/.config/sage-lantern/unit-b/chromium', session)
        self.assertNotIn('/home/radxa', session)

    def test_a_and_explicit_owner_pin_permission(self):
        self.plan['unit_id'] = 'unit-a'
        result = self.install(True)
        self.assertEqual((self.root / 'etc/sage-lantern-vault-setup.enabled').read_text(), 'allow-vault-initialize\n')
        self.assertTrue(result['owner_setup_permitted'])
        self.assertFalse(result['owner_setup_complete'])
        self.assertIn('unit-a operator', (self.root / 'etc/systemd/system/sage-lantern-fleet-admin.service').read_text())

    def test_reinstall_rejected_no_changes(self):
        self.install()
        original = (self.root / 'etc/sage-lantern/appliance-installed.json').read_bytes()
        with self.assertRaises(ValueError):
            self.install(True)
        self.assertEqual((self.root / 'etc/sage-lantern/appliance-installed.json').read_bytes(), original)
        self.assertFalse((self.root / 'etc/sage-lantern-vault-setup.enabled').exists())

    def test_prior_state_rejected_before_policy(self):
        for name in ('var/lib/sage-lantern/PROVISIONED', 'var/lib/sage-lantern/device-lock', 'etc/systemd/system/sage-lantern-fleet-admin.service.d'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.mkdir()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.install()
            self.assertFalse((self.root / 'etc/sage-lantern-device-lock.enabled').exists())
            path.rmdir()

    def test_symlink_target_rejected(self):
        (self.root / 'etc').symlink_to(self.directory)
        with self.assertRaises(ValueError):
            self.install()

    def test_payload_drift_leaves_lock_and_no_completion(self):
        (self.release / (MODULE.APP + 'dist/server/index.js')).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.install()
        self.assertTrue((self.root / 'etc/sage-lantern-device-lock.enabled').exists())
        self.assertFalse((self.root / 'etc/sage-lantern/appliance-installed.json').exists())

    def test_auth_drift_rejected_before_changes(self):
        (self.release / MODULE.AUTH).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_root_policy_and_executable_permissions(self):
        self.install()
        self.assertEqual(stat.S_IMODE((self.root / 'etc/sage-lantern-device-lock.enabled').stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((self.root / 'usr/local/libexec/sage-lantern-device-auth').stat().st_mode), 0o755)

    def test_prepare_rejects_general_manifest_not_canonical_stage(self):
        for entry in self.entries:
            if entry['path'] in (MODULE.AUTH, MODULE.AUTH_UNIT):
                data = (ROOT / entry['path']).read_bytes()
                (self.release / entry['path']).write_bytes(data)
                entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        config, verifier = Mock(), Mock()
        config.build_plan.return_value = self.plan
        config.audio_udev_rules.return_value = self.rules
        verifier.verify.return_value = self.manifest
        args = argparse.Namespace(unit_id='unit-b', format='plan', identity_inventory=None, activation_staging_root=None, operator_approved_release=True, release_root=str(self.release), manifest='synthetic-manifest', alsa_device='hw:CARD=FixtureB,DEV=0')
        with patch.object(MODULE, 'helper', side_effect=lambda name, path: config if name == 'unit_config' else verifier):
            with self.assertRaises(ValueError):
                MODULE.prepare(args)
            config.build_plan.assert_called_once_with(args)
            verifier.verify.assert_not_called()
            self.entries[:] = [entry for entry in self.entries if entry['path'] != MODULE.INSTALLER]
            with self.assertRaises(ValueError):
                MODULE.prepare(args)

    def test_native_gate_rejects_host_without_commands(self):
        with patch.object(MODULE.os, 'geteuid', return_value=1000), patch.object(MODULE.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                MODULE.native_preflight(argparse.Namespace())
            run.assert_not_called()

    def test_existing_drain_receipt_preserved_and_install_rejected(self):
        receipt = self.root / MODULE.AUTH_DRAIN.lstrip('/') / 'existing.json'
        receipt.parent.mkdir(parents=True)
        receipt.write_bytes(b'preserve synthetic receipt')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(receipt.read_bytes(), b'preserve synthetic receipt')
        self.assertFalse((self.root / 'etc/sage-lantern-device-lock.enabled').exists())

    def test_protected_owner_directory_is_private_and_empty(self):
        self.install()
        directory = self.root / 'var/lib/sage-lantern/history/conversations-v1'
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(directory.stat().st_uid, os.getuid())
        self.assertEqual(directory.stat().st_gid, os.getgid())
        self.assertEqual(list(directory.iterdir()), [])
        self.assertEqual(stat.S_IMODE(directory.parent.stat().st_mode), 0o700)
        self.assertEqual(directory.parent.stat().st_uid, os.getuid())

    def test_protected_owner_rejects_inherited_acl(self):
        with patch.object(MODULE.os, 'listxattr', return_value=['system.posix_acl_default']):
            with self.assertRaisesRegex(ValueError, 'ACL'):
                self.install()
        self.assertFalse((self.root / 'var/lib/sage-lantern/history').exists())
        self.assertFalse((self.root / 'etc/sage-lantern/appliance-installed.json').exists())

    def test_fresh_history_preferences_match_reader_schema(self):
        self.install()
        preferences = self.root / 'var/lib/sage-lantern/history/history.json'
        value = json.loads(preferences.read_bytes())
        self.assertEqual(value, dict(version=2, enabled=True, photos_enabled=True,
                                    generation=str(uuid.UUID(value['generation'])), items=[]))
        info = preferences.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink),
                         (os.getuid(), os.getgid(), 0o600, 1))

    def test_existing_preferences_are_preserved_without_install(self):
        preferences = self.root / 'var/lib/sage-lantern/history/history.json'
        preferences.parent.mkdir(parents=True)
        raw = b'{"enabled":false,"preserve":"existing preferences"}'
        preferences.write_bytes(raw)
        before = preferences.stat()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(preferences.read_bytes(), raw)
        self.assertEqual(preferences.stat(), before)
        self.assertFalse((self.root / 'etc/sage-lantern/appliance-installed.json').exists())

    def test_preferences_failure_never_records_install_completion(self):
        real_open = MODULE.os.open
        def fail_preferences(name, *args, **kwargs):
            if name == 'history.json':
                raise OSError('synthetic preferences creation failure')
            return real_open(name, *args, **kwargs)
        with patch.object(MODULE.os, 'open', side_effect=fail_preferences):
            with self.assertRaises(OSError):
                self.install()
        self.assertFalse((self.root / 'etc/sage-lantern/appliance-installed.json').exists())
        self.assertTrue((self.root / 'etc/sage-lantern-device-lock.enabled').exists())

    def test_preferences_exclusive_create_preserves_racing_file(self):
        real_open = MODULE.os.open
        def race_preferences(name, flags, *args, **kwargs):
            if name == 'history.json':
                self.assertTrue(flags & os.O_EXCL and flags & os.O_NOFOLLOW)
                descriptor = real_open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                       0o600, dir_fd=kwargs['dir_fd'])
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(b'preserve raced preferences')
            return real_open(name, flags, *args, **kwargs)
        with patch.object(MODULE.os, 'open', side_effect=race_preferences):
            with self.assertRaises(FileExistsError):
                self.install()
        self.assertEqual((self.root / 'var/lib/sage-lantern/history/history.json').read_bytes(),
                         b'preserve raced preferences')
        self.assertFalse((self.root / 'etc/sage-lantern/appliance-installed.json').exists())

    def test_existing_conversation_data_is_not_adopted(self):
        directory = self.root / 'var/lib/sage-lantern/history/conversations-v1'
        directory.mkdir(parents=True)
        marker = directory / 'owner.lock'
        marker.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(marker.read_bytes(), b'preserve')

    def test_direct_drain_provision_rejects_existing_state_and_links(self):
        state = self.root / 'var/lib/sage-lantern/device-lock'
        state.parent.mkdir(parents=True)
        state.symlink_to(self.directory)
        with self.assertRaises(FileExistsError):
            MODULE.provision_auth_drain(self.root, os.getuid() or 1001, os.getgid() or 1001)
        self.assertTrue(state.is_symlink())

    def test_drain_failure_retains_lock_without_completion(self):
        with patch.object(MODULE, 'provision_auth_drain', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                self.install()
        self.assertTrue((self.root / 'etc/sage-lantern-device-lock.enabled').exists())
        self.assertFalse((self.root / 'etc/sage-lantern/appliance-installed.json').exists())


class CanonicalProfileTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('canonical_fixture', Path(__file__).with_name('test_stage_appliance_release.py'))
        fixture_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture_module)
        self.stage = fixture_module.STAGE
        self.fixture = fixture_module.StageApplianceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.manifest = self.fixture.build()
        self.release = self.fixture.output / 'payload'
        self.destinations_path = self.release / 'release/destinations.json'
        self.destinations = json.loads(self.destinations_path.read_text())

    def validate(self):
        return MODULE.canonical_profile(self.release, self.manifest, self.stage)

    def rewrite_destinations(self):
        self.destinations_path.write_text(json.dumps(self.destinations))

    def test_real_stage_profile_accepted(self):
        rows = self.validate()
        self.assertEqual(rows['software/systemd/sage-lantern-mesh-runtime.service']['role'], 'systemd')
        self.assertEqual(rows['software/systemd/sage-lantern-voice-loop.service.d/40-mesh-broker.conf']['role'], 'dropin')
        self.assertEqual(rows[MODULE.AUTH_UNIT]['operation'], 'installer_transform')
        self.assertEqual(rows['scripts/lantern/unit-a-status-server.py']['operation'], 'reviewed_copy')
        self.assertEqual(rows['scripts/lantern/unit-a-voice-loop.py']['operation'], 'reviewed_copy')

    def test_profile_uses_shared_dependency_validator(self):
        with patch.object(self.stage, 'dependency_inventory', wraps=self.stage.dependency_inventory) as validator:
            self.validate()
        validator.assert_called_once()
        self.assertIn('node_modules/vinext', validator.call_args.args[0])
        self.assertIn('node_modules/vinext/dist/cli.js', validator.call_args.args[1])
        with patch.object(self.stage, 'dependency_inventory', side_effect=ValueError('dependency rejected')):
            with self.assertRaisesRegex(ValueError, 'dependency rejected'):
                self.validate()

    def test_prepare_accepts_canonical_output_and_matching_source_closure(self):
        config = Mock()
        config.build_plan.return_value = {'unit_id': 'unit-b', 'audio': {}}
        config.audio_udev_rules.return_value = 'synthetic rules'
        modules = {'unit_config': config, 'appliance_manifest': self.stage.MANIFEST, 'canonical_stage': self.stage}
        args = argparse.Namespace(unit_id='unit-b', format='plan', identity_inventory=None, activation_staging_root=None,
                                  operator_approved_release=True, release_root=str(self.release), manifest=str(self.fixture.output / 'manifest.json'), alsa_device='hw:CARD=FixtureB,DEV=0')
        with patch.object(MODULE, 'ROOT', self.fixture.source), patch.object(MODULE, 'helper', side_effect=lambda name, path: modules[name]):
            result = MODULE.prepare(args)
            self.assertEqual(result[0]['unit_id'], 'unit-b')
            self.assertEqual(result[3], self.manifest)
            self.fixture.write(self.fixture.source / 'software/runtime/src/sage_lantern/new_module.py', b'new = True\n')
            with self.assertRaises(ValueError):
                MODULE.prepare(args)

    def test_role_filler_cannot_replace_missing_canonical_source(self):
        for entry in self.manifest['files']:
            if entry['path'] == 'scripts/lantern/unit-a-voice-loop.py':
                entry['path'] = 'scripts/filler.py'
        with self.assertRaises(ValueError):
            self.validate()

    def test_redirect_mode_and_operation_rejected(self):
        row = next(row for row in self.destinations['files'] if row['path'] == MODULE.AUTH)
        for key, value in (('destination', '/etc/cron.d/unsafe'), ('operation', 'copy'), ('destination_mode', 0o777)):
            original = row[key]
            row[key] = value
            self.rewrite_destinations()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate()
            row[key] = original

    def test_canonical_readiness_claim_rejected(self):
        self.destinations['activation_authorized'] = True
        self.rewrite_destinations()
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_app_dependency_rejected(self):
        (self.release / MODULE.APP / 'node_modules/vinext/package.json').unlink()
        with self.assertRaises((ValueError, OSError)):
            self.validate()

    def test_dependency_version_and_advertised_closure_rejected(self):
        self.destinations['ui_dependencies']['node_modules/vinext'] = 'wrong'
        self.rewrite_destinations()
        with self.assertRaises(ValueError):
            self.validate()

    def test_stack_hash_mismatch_rejected(self):
        path = self.release / self.stage.LOCK
        document = json.loads(path.read_text())
        document['files'][0]['sha256'] = '0' * 64
        path.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            self.validate()

    def test_extra_role_complete_payload_rejected(self):
        self.manifest['files'].append(dict(path='extra.py', role='support'))
        with self.assertRaises(ValueError):
            self.validate()

    def test_installed_source_closure_missing_extra_and_modified(self):
        purelib = self.fixture.root / 'site-packages'
        runtime_data, sdk_data = b'runtime fixture\n', b'sdk fixture\n'
        for name, data in (('sage_lantern', runtime_data), ('sage_sdk', sdk_data)):
            self.fixture.write(purelib / name / '__init__.py', data)
        manifest = dict(files=[dict(path='software/runtime/src/sage_lantern/__init__.py', role='runtime_source', sha256=hashlib.sha256(runtime_data).hexdigest())])
        builder = Mock()
        builder.staged_payloads.return_value = {'src/sage_sdk/__init__.py': sdk_data}
        with patch.object(MODULE, 'helper', return_value=builder):
            MODULE.installed_source_closure(self.release, manifest, purelib)
            extra = purelib / 'sage_lantern/extra.py'
            extra.write_text('unexpected')
            with self.assertRaises(ValueError):
                MODULE.installed_source_closure(self.release, manifest, purelib)
            extra.unlink()
            installed = purelib / 'sage_sdk/__init__.py'
            installed.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                MODULE.installed_source_closure(self.release, manifest, purelib)
            installed.unlink()
            with self.assertRaises(ValueError):
                MODULE.installed_source_closure(self.release, manifest, purelib)


class KioskAutologinTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.gdm = self.root / 'etc/gdm3/daemon.conf'
        self.gdm.parent.mkdir(parents=True)
        self.original = b'# reviewed fresh GDM\n[daemon]\nAutomaticLoginEnable=false\n[security]\nDisallowTCP=true\n'
        self.gdm.write_bytes(self.original)
        self.gdm.chmod(0o644)
        self.digest = hashlib.sha256(self.original).hexdigest()
        config = self.root / 'etc/sage-lantern'
        config.mkdir()
        (self.root / 'etc/sage-lantern-device-lock.enabled').write_bytes(b'1\n')
        (config / 'appliance-installed.json').write_text(json.dumps(dict(lock_required=True, ready=False)))

    def plan(self):
        return MODULE.kiosk_plan('operatorb', self.digest, root=self.root)

    def test_opt_in_gdm_preserves_unrelated_settings(self):
        candidate = self.plan()['candidate'].decode()
        self.assertIn('AutomaticLogin=operatorb', candidate)
        self.assertIn('AutomaticLoginEnable=true', candidate)
        self.assertIn('WaylandEnable=false', candidate)
        self.assertIn('[security]\nDisallowTCP=true', candidate)
        self.assertIn('# reviewed fresh GDM', candidate)
        self.assertEqual(candidate.count('AutomaticLoginEnable='), 1)
        self.assertEqual(self.gdm.read_bytes(), self.original)

    def test_existing_autologin_and_duplicate_sections_rejected(self):
        for content in (b'[daemon]\nAutomaticLoginEnable=true\n', b'[daemon]\nAutomaticLogin=other\n',
                        b'[daemon]\nDefaultSession=plasma\n', b'[daemon]\n[daemon]\n'):
            with self.subTest(content=content), self.assertRaises((ValueError, MODULE.configparser.Error)):
                MODULE.kiosk_gdm_candidate(content, 'operatorb')

    def test_existing_display_getty_and_account_state_rejected(self):
        for name in ('etc/sddm.conf', 'etc/systemd/system/getty@tty1.service.d', 'var/lib/AccountsService/users/operatorb', 'home/operatorb/.dmrc'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('prior state')
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.plan()
            path.unlink()

    def test_hash_drift_and_symlink_rejected(self):
        self.gdm.write_bytes(b'[daemon]\n')
        with self.assertRaises(ValueError):
            self.plan()
        self.gdm.unlink()
        self.gdm.symlink_to(self.root / 'missing')
        with self.assertRaises(ValueError):
            self.plan()

    def test_boot_enablement_never_starts_or_unlocks(self):
        run = Mock()
        plan = self.plan()
        result = MODULE.configure_kiosk_boot(plan, root=self.root, run=run)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['systemctl', 'daemon-reload'], ['systemctl', 'enable', 'sage-lantern-device-auth.service', 'sage-lantern-status.service', 'sage-lantern-fleet-admin.service']])
        self.assertFalse(result['private_sage_unlocked'])
        self.assertFalse(result['ready'])
        self.assertFalse(result['services_started'])
        self.assertEqual(self.gdm.read_bytes(), plan['candidate'])
        self.assertFalse((self.root / 'run').exists())
        self.assertEqual((self.root / 'var/lib/sage-lantern/recovery/kiosk/gdm3-daemon.conf.before-kiosk').read_bytes(), self.original)
        self.assertIn('XSession=sage-lantern', (self.root / 'var/lib/AccountsService/users/operatorb').read_text())

    def test_enable_failure_preserves_gdm_no_success_receipt(self):
        run = Mock(side_effect=[None, MODULE.subprocess.CalledProcessError(1, 'systemctl')])
        with self.assertRaises(MODULE.subprocess.SubprocessError):
            MODULE.configure_kiosk_boot(self.plan(), root=self.root, run=run)
        self.assertEqual(self.gdm.read_bytes(), self.original)
        self.assertFalse((self.root / 'etc/sage-lantern/kiosk-boot.json').exists())
        self.assertTrue((self.root / 'etc/sage-lantern-device-lock.enabled').exists())

    def test_no_lock_policy_no_autologin_changes(self):
        (self.root / 'etc/sage-lantern-device-lock.enabled').unlink()
        run = Mock()
        with self.assertRaises(OSError):
            MODULE.configure_kiosk_boot(self.plan(), root=self.root, run=run)
        run.assert_not_called()
        self.assertEqual(self.gdm.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
