import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[2]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/lantern' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ASSEMBLER = module('generated_assembler', 'assemble-protected-config.py')
RUNNER = module('generated_runner', 'provision-fresh-appliance.py')


class GeneratedConfigTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(schema='sage.lantern.initialized.v1', unit_id='unit-b', chain_id='sage-personal-generated-fixture',
                             root_id='11' * 32, private_agent_id='22' * 32, public_agent_id='33' * 32,
                             local_enrollment_committed=False, public_enrolled=False, vault_initialized=False, ready=False, provisioned=False)
        self.sage = b'federation: {enabled: false}\nquorum: {enabled: false}\nvendored_agent_bootstrap:\n  agent_key_file: /var/lib/sage-lantern/identity/lantern-agent.key\n  home_domain: private-test\nunknown: [keep, this]\n'
        self.runtime = b'[sage]\nbase_url = "http://127.0.0.1:8080"\nidentity_path = "/var/lib/sage-lantern/identity/lantern-agent.key"\ndomain = "private-test"\n[extra]\nkeep = true\n'
        self.quotas = dict(actor_bytes=1024, node_bytes=2048, actor_objects=10, node_objects=20, min_free_bytes=4096)

    def build(self):
        return ASSEMBLER.generated_config(json.dumps(self.identity).encode(), 'unit-b', self.sage, self.runtime, self.quotas)

    def test_actual_output_chain_and_split_identity_paths(self):
        result = self.build()
        runtime = tomllib.loads(result['lantern.toml'].decode())
        self.assertEqual(runtime['sage']['chain_id'], self.identity['chain_id'])
        self.assertTrue(runtime['sage']['protected_storage'])
        self.assertEqual(runtime['sage']['conversation_dir'], '/var/lib/sage-lantern/history/conversations-v1')
        self.assertEqual(runtime['extra'], {'keep': True})
        self.assertEqual(runtime['sage']['identity_path'], '/var/lib/sage-lantern/identity/lantern-agent.key')
        self.assertNotIn('public', runtime['sage'])
        sage = yaml.safe_load(result['sage.yaml'])
        self.assertEqual(sage['unknown'], ['keep', 'this'])
        self.assertEqual(sage['private_media'], {'enabled': True, **self.quotas})
        binding = json.loads(result['bindings.json'])
        self.assertEqual(binding['public_identity_path'], '/var/lib/sage-lantern/identity/lantern-public.key')
        self.assertFalse(binding['public_enrolled'])
        self.assertFalse(binding['provisioned'])

    def test_conflicting_conversation_directory_rejected(self):
        for value in ('relative', '/other/legacy', ''):
            with self.subTest(value=value):
                original = self.runtime
                self.runtime = original.replace(b'[sage]', ('[sage]\nconversation_dir=' + json.dumps(value)).encode())
                with self.assertRaises(ValueError):
                    self.build()
                self.runtime = original

    def test_untrusted_identity_output_rejected(self):
        for key, value in (('unit_id', 'unit-a'), ('ready', True), ('public_agent_id', self.identity['root_id']),
                           ('chain_id', 'public-chain'), ('root_id', 'bad'), ('extra', 'forbidden')):
            with self.subTest(key=key):
                original = dict(self.identity)
                self.identity[key] = value
                with self.assertRaises(ValueError):
                    self.build()
                self.identity = original
        with self.assertRaises(ValueError):
            ASSEMBLER.generated_config(b'{"schema":1,"schema":2}', 'unit-b', self.sage, self.runtime, self.quotas)

    def test_wrong_private_runtime_path_and_existing_chain_rejected(self):
        for raw in (self.runtime.replace(b'lantern-agent.key', b'lantern-public.key'), self.runtime.replace(b'[sage]', b'[sage]\nchain_id="copied-a"')):
            with self.assertRaises(ValueError):
                ASSEMBLER.generated_config(json.dumps(self.identity).encode(), 'unit-b', self.sage, raw, self.quotas)

    def test_fresh_config_install_preserves_backups_no_key_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sage = root / 'var/lib/sage-lantern/sage-personal/config.yaml'
            runtime = root / 'etc/sage-lantern/lantern.toml'
            for path, data in ((sage, self.sage), (runtime, self.runtime)):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o600)
            args = argparse.Namespace(unit_id='unit-b', manifest_sha256='a' * 64, commission_plan_sha256='b' * 64, identity_config_sha256=hashlib.sha256(self.sage).hexdigest())
            prepared = (ASSEMBLER, {'quotas': self.quotas}, self.sage, self.runtime)
            RUNNER.commission_begin(args, prepared, root=root)
            RUNNER.commission_install(args, json.dumps(self.identity).encode(), prepared, root=root)
            self.assertEqual(sage.with_name('config.yaml.before-commissioning').read_bytes(), self.sage)
            self.assertEqual(runtime.with_name('lantern.toml.before-commissioning').read_bytes(), self.runtime)
            self.assertTrue(tomllib.loads(runtime.read_text())['sage']['protected_storage'])
            self.assertFalse(list(root.rglob('*.key')))
            self.assertFalse(list(root.rglob('PROVISIONED')))
            self.assertFalse(list(root.rglob('LOCAL_VOICE_READY')))
            with self.assertRaises((ValueError, FileNotFoundError)):
                RUNNER.commission_install(args, json.dumps(self.identity).encode(), prepared, root=root)

    def commissioning(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        for name, data in (('sage.yaml', self.sage), ('lantern.toml', self.runtime)):
            path = root / RUNNER.COMMISSION_FILES[name]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o600)
        args = argparse.Namespace(unit_id='unit-b', manifest_sha256='a' * 64, commission_plan_sha256='b' * 64,
                                  identity_config_sha256=hashlib.sha256(self.sage).hexdigest())
        return root, args, (ASSEMBLER, {'quotas': self.quotas}, self.sage, self.runtime)

    def test_completion_binds_review_and_files_and_rejects_changes(self):
        root, args, prepared = self.commissioning()
        with self.assertRaises(FileNotFoundError):
            RUNNER.commission_completion_check(args.unit_id, args.manifest_sha256, root=root, owner_uid=os.getuid())
        pending = RUNNER.commission_begin(args, prepared, root=root)
        RUNNER.commission_install(args, json.dumps(self.identity).encode(), prepared, root=root)
        receipt = RUNNER.commission_completion_check(args.unit_id, args.manifest_sha256, root=root, owner_uid=os.getuid())
        self.assertEqual(receipt['transaction_id'], pending['transaction_id'])
        self.assertEqual(receipt['plan_sha256'], args.commission_plan_sha256)
        self.assertEqual(receipt['initial_config_sha256'], args.identity_config_sha256)
        self.assertFalse((root / RUNNER.PENDING).exists())
        with self.assertRaises(ValueError):
            RUNNER.commission_completion_check('unit-a', args.manifest_sha256, root=root, owner_uid=os.getuid())
        with self.assertRaises(ValueError):
            RUNNER.commission_completion_check(args.unit_id, 'd' * 64, root=root, owner_uid=os.getuid())
        for name in RUNNER.COMMISSION_FILES.values():
            path = root / name
            original = path.read_bytes()
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                RUNNER.commission_completion_check(args.unit_id, args.manifest_sha256, root=root, owner_uid=os.getuid())
            path.write_bytes(original)

    def test_preinit_mismatch_and_backup_collision_do_not_claim(self):
        for target in ('runtime-mismatch', 'sage.yaml', 'lantern.toml'):
            with self.subTest(target=target):
                root, args, prepared = self.commissioning()
                if target == 'runtime-mismatch':
                    (root / RUNNER.COMMISSION_FILES['lantern.toml']).write_bytes(b'changed')
                else:
                    path = root / (RUNNER.COMMISSION_FILES[target] + '.before-commissioning')
                    path.write_bytes(b'existing backup')
                with self.assertRaises(ValueError):
                    RUNNER.commission_begin(args, prepared, root=root)
                self.assertFalse((root / RUNNER.PENDING).exists())

    def test_failure_after_each_config_replace_keeps_fence(self):
        for target in ('sage.yaml', 'lantern.toml'):
            with self.subTest(target=target):
                root, args, prepared = self.commissioning()
                RUNNER.commission_begin(args, prepared, root=root)
                replace = RUNNER.os.replace
                def fail_after(source, destination):
                    replace(source, destination)
                    if Path(destination) == root / RUNNER.COMMISSION_FILES[target]:
                        raise OSError('injected after replacement')
                with patch.object(RUNNER.os, 'replace', side_effect=fail_after), self.assertRaises(OSError):
                    RUNNER.commission_install(args, json.dumps(self.identity).encode(), prepared, root=root)
                self.assertTrue((root / RUNNER.PENDING).exists())
                self.assertFalse((root / RUNNER.COMPLETE).exists())
                with self.assertRaises(ValueError):
                    RUNNER.commission_completion_check(args.unit_id, args.manifest_sha256, root=root, owner_uid=os.getuid())

    def test_directory_sync_failures_retain_fence(self):
        for point in ('configs', 'completion', 'clear'):
            with self.subTest(point=point):
                root, args, prepared = self.commissioning()
                RUNNER.commission_begin(args, prepared, root=root)
                sync = RUNNER.sync_directory
                failed = False
                def fail_sync(path):
                    nonlocal failed
                    complete = (root / RUNNER.COMPLETE).exists()
                    pending = (root / RUNNER.PENDING).exists()
                    configs = (root / RUNNER.COMMISSION_FILES['sage.yaml']).read_bytes() != self.sage
                    should_fail = ((point == 'configs' and configs and not complete) or
                                   (point == 'completion' and complete and pending) or
                                   (point == 'clear' and complete and not pending))
                    if should_fail and not failed:
                        failed = True
                        raise OSError('injected directory fsync failure')
                    sync(path)
                with patch.object(RUNNER, 'sync_directory', side_effect=fail_sync), self.assertRaises(OSError):
                    RUNNER.commission_install(args, json.dumps(self.identity).encode(), prepared, root=root)
                self.assertTrue(failed)
                self.assertTrue((root / RUNNER.PENDING).exists())
                with self.assertRaises(ValueError):
                    RUNNER.commission_completion_check(args.unit_id, args.manifest_sha256, root=root, owner_uid=os.getuid())

    def test_postinit_backup_collision_retains_fence(self):
        root, args, prepared = self.commissioning()
        RUNNER.commission_begin(args, prepared, root=root)
        (root / (RUNNER.COMMISSION_FILES['lantern.toml'] + '.before-commissioning')).write_bytes(b'race')
        with self.assertRaises(ValueError):
            RUNNER.commission_install(args, json.dumps(self.identity).encode(), prepared, root=root)
        self.assertTrue((root / RUNNER.PENDING).exists())

    def test_fence_fsync_failure_keeps_pending_and_blocks_installer(self):
        root, args, prepared = self.commissioning()
        with patch.object(RUNNER, 'sync_directory', side_effect=OSError('injected initial fence sync')), self.assertRaises(OSError):
            RUNNER.commission_begin(args, prepared, root=root)
        self.assertTrue((root / RUNNER.PENDING).exists())
        installer = module('commission_installer', 'install-fleet-appliance-local.py')
        with self.assertRaisesRegex(ValueError, 'commissioning pending'):
            installer.fresh(root, {})

    def test_missing_pending_cannot_publish_completion(self):
        root, args, prepared = self.commissioning()
        with self.assertRaises(FileNotFoundError):
            RUNNER.commission_install(args, json.dumps(self.identity).encode(), prepared, root=root)
        self.assertFalse((root / RUNNER.COMPLETE).exists())


if __name__ == '__main__':
    unittest.main()
