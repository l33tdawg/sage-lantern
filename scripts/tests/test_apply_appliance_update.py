import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software/runtime/src'))
SPEC = importlib.util.spec_from_file_location('update_apply', ROOT / 'scripts/lantern/apply-appliance-update.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
STAGE = MODULE.STAGE


@dataclass
class SyntheticEvidence:
    candidate_sha256: str
    rollback_sha256: str
    transition_id: str = '11111111-1111-4111-8111-111111111111'
    boot_id: str = '22222222-2222-4222-8222-222222222222'
    broker_id: str = 'a' * 32
    vault_epoch: str = 'b' * 64 + ':1'


class SyntheticAuthority:
    def __init__(self, hashes):
        self.hashes, self.checks, self.closed = hashes, 0, False
        self.fail_at = None

    def prepare(self):
        return SyntheticEvidence(**self.hashes)

    def assert_current(self, **hashes):
        if hashes != self.hashes:
            raise ValueError('synthetic binding mismatch')
        self.checks += 1
        if self.checks == self.fail_at:
            raise RuntimeError('synthetic revoked authority')
        return True

    def run(self, mutation):
        self.assert_current(**self.hashes)
        result = mutation(self)
        self.assert_current(**self.hashes)
        return result

    def close(self):
        self.closed = True


class ApplyUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.staging = self.root / 'staging'
        self.staging.mkdir(mode=0o700)
        self.candidate = self.fixture('new', b'new UI fixture')
        self.rollback = self.fixture('old', b'old UI fixture')
        stage_args = argparse.Namespace(output=str(self.candidate[0]), manifest_sha256=hashlib.sha256(self.candidate[2]).hexdigest(),
            rollback_output=str(self.rollback[0]), rollback_manifest_sha256=hashlib.sha256(self.rollback[2]).hexdigest(),
            unit_id='unit-a', stage_root=str(self.staging))
        with patch.object(STAGE, 'checked_output', side_effect=lambda output, digest, current: self.candidate if current else self.rollback):
            staged = STAGE.stage_update(stage_args)
        self.args = argparse.Namespace(stage=staged['stage'], unit_id='unit-a',
            candidate_manifest_sha256=stage_args.manifest_sha256, rollback_manifest_sha256=stage_args.rollback_manifest_sha256)
        self.destination = self.root / 'installed-ui'
        self.destination.mkdir()
        for entry in self.rollback[1]['files']:
            if entry['role'] == 'ui':
                target = self.destination / entry['path'].removeprefix(MODULE.APP)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((self.rollback[0] / 'payload' / entry['path']).read_bytes())
                target.chmod(entry['mode'])
        self.target = self.destination / 'dist/server/index.js'
        self.plan = MODULE.preflight(self.args, destination=self.destination)
        self.authority = SyntheticAuthority(dict(candidate_sha256=self.args.candidate_manifest_sha256,
            rollback_sha256=self.args.rollback_manifest_sha256))

    def fixture(self, name, changed):
        directory = self.root / name
        files = []
        for relative, data, role in ((MODULE.APP + 'dist/server/index.js', changed, 'ui'),
                (MODULE.APP + 'package.json', b'{}', 'ui'),
                ('scripts/lantern/lantern-idle-suspend.py', b'unchanged runner fixture', 'root_helper')):
            target = directory / 'payload' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
            files.append(dict(path=relative, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=0o644, role=role))
        manifest = dict(files=files, selection={'fixture': True})
        return directory, manifest, STAGE.canonical(manifest), STAGE.canonical(manifest['selection'])

    def synthetic_copy(self, *, journal=None):
        import sage_lantern.deployment_drain as drain
        plan = dict(self.plan, blockers=[])
        if journal is None:
            journal = MODULE.ApplyJournal(Path(self.args.stage) / 'apply-journal', dict(plan['binding'], stage_sha256=plan['stage_sha256']))
            journal.append('apply_started', plan)
        def acquire(runner, **kwargs):
            self.assertIs(kwargs['verify_broker'], drain.verify_root_broker)
            self.assertIs(kwargs['verify_binding'](**self.authority.hashes), True)
            return self.authority
        with patch.object(MODULE, 'load_installed_runner', return_value=object()), \
                patch.object(drain.DeploymentDrain, 'acquire', side_effect=acquire):
            return MODULE._copy_admitted(self.args, plan, journal, destination=self.destination)

    def status(self):
        return MODULE.inspect_apply(Path(self.args.stage) / 'apply-journal')

    def test_inspection_rehashes_but_never_authorizes(self):
        self.assertTrue(self.plan['installed_ui_baseline_verified'])
        self.assertFalse(self.plan['installed_baseline_verified'])
        self.assertEqual(self.plan['queue_status'], 'not_inspected')
        self.assertEqual(len(self.plan['blockers']), 2)
        self.assertFalse(self.plan['activation_authorized'])
        self.assertFalse((Path(self.args.stage) / 'apply-journal').exists())

    def test_production_apply_denies_unknown_before_lock_or_native_calls(self):
        with patch.object(MODULE.os, 'geteuid', return_value=0), patch.object(MODULE.sys, 'platform', 'linux'), \
                patch.object(MODULE, 'preflight', return_value=self.plan), patch.object(MODULE, '_copy_admitted') as copy:
            with self.assertRaisesRegex(ValueError, 'all_backend_queue_authority_unavailable'):
                MODULE.apply_update(self.args)
            copy.assert_not_called()
        self.assertFalse((Path(self.args.stage) / 'apply.lock').exists())

    def test_engine_also_rejects_production_blockers(self):
        with self.assertRaisesRegex(ValueError, 'production admission blockers'):
            MODULE._copy_admitted(self.args, self.plan, None, destination=self.destination)

    def test_reviewed_hash_and_unit_are_required(self):
        for field, value in (('candidate_manifest_sha256', '0' * 64), ('rollback_manifest_sha256', '0' * 64), ('unit_id', 'unit-b')):
            with self.subTest(field=field), patch.object(self.args, field, value), self.assertRaisesRegex(ValueError, 'binding mismatch'):
                MODULE.preflight(self.args, destination=self.destination)

    def test_baseline_tamper_and_untracked_files_are_rejected(self):
        self.target.write_bytes(b'not the reviewed rollback')
        with self.assertRaises(ValueError):
            MODULE.preflight(self.args, destination=self.destination)
        self.target.write_bytes(b'old UI fixture')
        (self.destination / 'untracked').write_bytes(b'preserve')
        with self.assertRaisesRegex(ValueError, 'untracked'):
            MODULE.preflight(self.args, destination=self.destination)

    def test_symlink_hardlink_and_writable_parent_are_rejected(self):
        saved = self.target.read_bytes()
        self.target.unlink()
        self.target.symlink_to(self.destination / 'package.json')
        with self.assertRaises((ValueError, OSError)):
            MODULE.preflight(self.args, destination=self.destination)
        self.target.unlink()
        self.target.write_bytes(saved)
        link = self.root / 'alias'
        os.link(self.target, link)
        with self.assertRaises(ValueError):
            MODULE.preflight(self.args, destination=self.destination)
        link.unlink()
        self.target.parent.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'untrusted'):
            MODULE.preflight(self.args, destination=self.destination)

    def test_non_ui_changes_and_added_artifacts_are_denied(self):
        inspected = MODULE.inspect_binding(self.args)
        changed = dict(inspected[3])
        name = 'scripts/lantern/lantern-idle-suspend.py'
        changed[name] = dict(changed[name], sha256='c' * 64)
        with patch.object(MODULE, 'inspect_binding', return_value=(*inspected[:3], changed, inspected[4])), \
                self.assertRaisesRegex(ValueError, 'non-UI'):
            MODULE.preflight(self.args, destination=self.destination)
        changed['added'] = changed[name]
        with patch.object(MODULE, 'inspect_binding', return_value=(*inspected[:3], changed, inspected[4])), \
                self.assertRaisesRegex(ValueError, 'additions/removals'):
            MODULE.preflight(self.args, destination=self.destination)

    def test_synthetic_copy_uses_real_files_and_journal_not_native_evidence(self):
        result = self.synthetic_copy()
        self.assertEqual(self.target.read_bytes(), b'new UI fixture')
        self.assertFalse(result['activation_authorized'])
        self.assertTrue(self.authority.closed)
        self.assertEqual(self.status()['last_phase'], 'artifacts_copied_fenced')
        self.assertTrue(self.status()['reconciliation_required'])
        records = [json.loads(path.read_bytes()) for path in sorted((Path(self.args.stage) / 'apply-journal').iterdir())]
        self.assertEqual([record['phase'] for record in records],
            ['apply_started', 'drain_prepared', 'copy_intent', 'copy_done', 'artifacts_copied_fenced'])
        self.assertGreaterEqual(self.authority.checks, 7)
        self.assertFalse(list(self.destination.rglob('.sage-update-*')))

    def test_baseline_change_after_prepare_denies_before_intent(self):
        self.target.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.synthetic_copy()
        self.assertEqual(self.status()['last_phase'], 'drain_prepared')
        self.assertTrue(self.authority.closed)

    def test_intent_durability_failure_prevents_copy(self):
        original = MODULE.ApplyJournal.append
        def append(journal, phase, details):
            if phase == 'copy_intent':
                raise OSError('synthetic intent fsync failure')
            original(journal, phase, details)
        with patch.object(MODULE.ApplyJournal, 'append', append), self.assertRaises(OSError):
            self.synthetic_copy()
        self.assertEqual(self.target.read_bytes(), b'old UI fixture')
        self.assertFalse(list(self.destination.rglob('.sage-update-*')))

    def test_temp_copy_failure_retains_intent_and_old_target(self):
        original = STAGE.write_new
        def write(path, raw, mode=0o600):
            original(path, raw, mode)
            if path.name.startswith('.sage-update-'):
                raise OSError('synthetic temporary fsync failure')
        with patch.object(STAGE, 'write_new', side_effect=write), self.assertRaises(OSError):
            self.synthetic_copy()
        self.assertEqual(self.target.read_bytes(), b'old UI fixture')
        self.assertIsNotNone(self.status()['pending_intent'])
        self.assertEqual(len(list(self.destination.rglob('.sage-update-*'))), 1)

    def test_rename_failure_preserves_temp_and_old_target(self):
        with patch.object(MODULE.os, 'replace', side_effect=OSError('synthetic rename failure')), self.assertRaises(OSError):
            self.synthetic_copy()
        self.assertEqual(self.target.read_bytes(), b'old UI fixture')
        self.assertEqual(self.status()['last_phase'], 'copy_intent')
        self.assertTrue(self.authority.closed)

    def test_revocation_immediately_before_rename_denies_switch(self):
        self.authority.fail_at = 5
        with self.assertRaises(RuntimeError):
            self.synthetic_copy()
        self.assertEqual(self.target.read_bytes(), b'old UI fixture')
        self.assertEqual(self.status()['last_phase'], 'copy_intent')

    def test_done_failure_preserves_new_bytes_but_reports_uncertainty(self):
        original = MODULE.ApplyJournal.append
        def append(journal, phase, details):
            if phase == 'copy_done':
                raise OSError('synthetic done fsync failure')
            original(journal, phase, details)
        with patch.object(MODULE.ApplyJournal, 'append', append), self.assertRaises(OSError):
            self.synthetic_copy()
        self.assertEqual(self.target.read_bytes(), b'new UI fixture')
        self.assertIsNotNone(self.status()['pending_intent'])
        self.assertTrue(self.authority.closed)

    def test_directory_fsync_failure_after_replace_is_not_done(self):
        original = STAGE.sync_directory
        calls = []
        def sync(path):
            original(path)
            if path == self.target.parent:
                calls.append(path)
                if len(calls) == 2:
                    raise OSError('synthetic directory fsync failure after rename')
        with patch.object(STAGE, 'sync_directory', side_effect=sync), self.assertRaises(OSError):
            self.synthetic_copy()
        self.assertEqual(self.target.read_bytes(), b'new UI fixture')
        self.assertEqual(self.status()['last_phase'], 'copy_intent')
        self.assertTrue(self.authority.closed)

    def test_revocation_after_replace_leaves_uncertain_new_target(self):
        self.authority.fail_at = 6
        with self.assertRaises(RuntimeError):
            self.synthetic_copy()
        self.assertEqual(self.target.read_bytes(), b'new UI fixture')
        self.assertEqual(self.status()['last_phase'], 'copy_intent')
        self.assertTrue(self.authority.closed)

    def test_seal_tamper_is_denied_by_concrete_binding_callback(self):
        import sage_lantern.deployment_drain as drain
        plan = dict(self.plan, blockers=[])
        journal = MODULE.ApplyJournal(Path(self.args.stage) / 'apply-journal', dict(plan['binding'], stage_sha256=plan['stage_sha256']))
        journal.append('apply_started', plan)
        def acquire(runner, **kwargs):
            artifact = Path(self.args.stage) / 'candidate/payload' / MODULE.APP / 'dist/server/index.js'
            artifact.chmod(0o644)
            artifact.write_bytes(b'bad staged UI')
            artifact.chmod(0o444)
            kwargs['verify_binding'](**self.authority.hashes)
            self.fail('tampered stage must not acquire admission')
        with patch.object(MODULE, 'load_installed_runner', return_value=object()), \
                patch.object(drain.DeploymentDrain, 'acquire', side_effect=acquire), self.assertRaises(ValueError):
            MODULE._copy_admitted(self.args, plan, journal, destination=self.destination)
        self.assertEqual(self.target.read_bytes(), b'old UI fixture')
        self.assertEqual(self.status()['last_phase'], 'apply_started')

    def test_old_daemon_denial_is_not_replaced_with_synthetic_receipt(self):
        import sage_lantern.deployment_drain as drain
        plan = dict(self.plan, blockers=[])
        journal = MODULE.ApplyJournal(Path(self.args.stage) / 'apply-journal', dict(plan['binding'], stage_sha256=plan['stage_sha256']))
        journal.append('apply_started', plan)
        with patch.object(MODULE, 'load_installed_runner', return_value=object()), \
                patch.object(drain.DeploymentDrain, 'acquire', side_effect=RuntimeError('unsupported installed broker')), \
                self.assertRaisesRegex(RuntimeError, 'unsupported installed broker'):
            MODULE._copy_admitted(self.args, plan, journal, destination=self.destination)
        self.assertEqual(self.target.read_bytes(), b'old UI fixture')
        self.assertEqual(self.status()['last_phase'], 'apply_started')
        self.assertFalse(list(self.destination.rglob('.sage-update-*')))

    def test_apply_journal_tampering_and_reuse_are_denied(self):
        self.synthetic_copy()
        directory = Path(self.args.stage) / 'apply-journal'
        with self.assertRaises(FileExistsError):
            MODULE.ApplyJournal(directory, self.plan['binding'])
        path = directory / '000004.json'
        value = json.loads(path.read_bytes())
        value['previous_sha256'] = '0' * 64
        path.write_bytes(STAGE.canonical(value))
        with self.assertRaisesRegex(ValueError, 'chain mismatch'):
            MODULE.inspect_apply(directory)

    def test_cli_has_no_root_prefix_or_restart_override(self):
        for option in ('--root', '--restart', '--force', '--queues-empty', '--activate'):
            with self.subTest(option=option), self.assertRaises(SystemExit):
                MODULE.main(['--apply', option])


if __name__ == '__main__':
    unittest.main()
