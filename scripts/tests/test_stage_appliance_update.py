import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('update_stage', ROOT / 'scripts/lantern/stage-appliance-update.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class UpdateStageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.staging = self.root / 'staging'
        self.staging.mkdir(mode=0o700)
        self.candidate = self.fixture('candidate-source', b'candidate fixture, not executable')
        self.rollback = self.fixture('rollback-source', b'old fixture, not executable')
        self.args = argparse.Namespace(unit_id='unit-a', output=str(self.candidate[0]), manifest_sha256=hashlib.sha256(self.candidate[2]).hexdigest(),
            stage_root=str(self.staging), rollback_output=None, rollback_manifest_sha256=None)

    def fixture(self, name, data):
        directory = self.root / name
        target = directory / 'payload/software/bundle/linux-arm64/bin/sage-gui'
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
        target.chmod(0o755)
        entry = dict(path='software/bundle/linux-arm64/bin/sage-gui', mode=0o755, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        manifest = dict(files=[entry], selection={'fixture': name})
        raw, selection = MODULE.canonical(manifest), MODULE.canonical(manifest['selection'])
        (directory / 'manifest.json').write_bytes(raw)
        (directory / 'selection.json').write_bytes(selection)
        return directory, manifest, raw, selection

    def stage(self):
        def verified(output, digest, *, current):
            self.assertEqual(current, output == self.args.output)
            return self.candidate if current else self.rollback
        with patch.object(MODULE, 'checked_output', side_effect=verified) as check:
            result = MODULE.stage_update(self.args)
        self.assertEqual(check.call_count, 4 if self.args.rollback_output else 2)
        return Path(result['stage'])

    def test_sealed_candidate_no_activation_or_fake_rollback(self):
        stage = self.stage()
        status = MODULE.inspect_stage(stage)
        self.assertTrue(status['complete'])
        self.assertFalse(status['activation_authorized'])
        self.assertFalse(status['rollback_available'])
        self.assertFalse(status['installed_baseline_verified'])
        payload = stage / 'candidate/payload/software/bundle/linux-arm64/bin/sage-gui'
        self.assertEqual(payload.stat().st_mode & 0o777, 0o555)
        self.assertNotEqual(payload.stat().st_ino, (self.candidate[0] / 'payload/software/bundle/linux-arm64/bin/sage-gui').stat().st_ino)
        self.assertEqual(len(list((stage / 'journal').iterdir())), 3)
        self.assertFalse(list(stage.rglob('*.key')))

    def test_fleet_staging_retains_unit_binding_without_granting_activation(self):
        for unit in ('unit-c', 'unit-d', 'unit-e', 'unit-f'):
            with self.subTest(unit=unit):
                self.args.unit_id = unit
                stage = self.stage()
                status = MODULE.inspect_stage(stage)
                self.assertTrue(status['complete'])
                self.assertFalse(status['activation_authorized'])
                self.assertEqual(json.loads((stage / 'stage.json').read_bytes())['binding']['unit_id'], unit)
        self.args.unit_id = 'unit-g'
        with self.assertRaises(ValueError):
            MODULE.stage_update(self.args)

    def test_selected_rollback_artifacts_not_installed_state(self):
        self.args.rollback_output = str(self.rollback[0])
        self.args.rollback_manifest_sha256 = hashlib.sha256(self.rollback[2]).hexdigest()
        stage = self.stage()
        self.assertTrue(MODULE.inspect_stage(stage)['rollback_available'])
        rollback = json.loads((stage / 'rollback-artifacts.json').read_bytes())
        self.assertFalse(rollback['restore_state'])
        self.assertFalse(rollback['automatic_rollback'])
        self.assertEqual(len(list((stage / 'journal').iterdir())), 4)

    def test_artifact_and_journal_tampering_rejected(self):
        stage = self.stage()
        artifact = stage / 'candidate/payload/software/bundle/linux-arm64/bin/sage-gui'
        prior = artifact.read_bytes()
        artifact.chmod(0o755)
        artifact.write_bytes(b'changed')
        artifact.chmod(0o555)
        with self.assertRaises(ValueError):
            MODULE.inspect_stage(stage)
        artifact.chmod(0o755)
        artifact.write_bytes(prior)
        artifact.chmod(0o555)
        journal = stage / 'journal/000002.json'
        value = json.loads(journal.read_bytes())
        value['previous_sha256'] = '0' * 64
        journal.write_bytes(MODULE.canonical(value))
        with self.assertRaises(ValueError):
            MODULE.inspect_stage(stage)

    def test_failed_copy_leaves_started_journal_not_ready(self):
        with patch.object(MODULE, 'checked_output', return_value=self.candidate), \
             patch.object(MODULE, 'copy_artifacts', side_effect=OSError('injected copy failure')):
            with self.assertRaises(OSError):
                MODULE.stage_update(self.args)
        stage, = self.staging.iterdir()
        status = MODULE.inspect_stage(stage)
        self.assertFalse(status['complete'])
        self.assertTrue(status['reconciliation_required'])
        self.assertFalse((stage / 'stage.json').exists())

    def test_journal_fsync_failure_never_copies(self):
        original = MODULE.write_new
        def fail(path, data, mode=0o600):
            original(path, data, mode)
            if path.name == '000001.json':
                raise OSError('injected journal durability failure')
        with patch.object(MODULE, 'checked_output', return_value=self.candidate), patch.object(MODULE, 'write_new', side_effect=fail), \
             patch.object(MODULE, 'copy_artifacts') as copy:
            with self.assertRaises(OSError):
                MODULE.stage_update(self.args)
            copy.assert_not_called()

    def test_source_changed_during_copy_rejected(self):
        (self.candidate[0] / 'payload/software/bundle/linux-arm64/bin/sage-gui').write_bytes(b'changed')
        with patch.object(MODULE, 'checked_output', return_value=self.candidate), self.assertRaises(ValueError):
            MODULE.stage_update(self.args)
        stage, = self.staging.iterdir()
        self.assertFalse(MODULE.inspect_stage(stage)['complete'])

    def test_uses_canonical_source_closure_gate(self):
        phase, verifier = Mock(), Mock()
        verifier.metadata_bytes.return_value = (self.candidate[2], None)
        phase.verify_release.side_effect = ValueError('unmatched source closure')
        def helper(name, filename):
            return phase if filename.endswith('provision-fresh-appliance.py') else verifier
        with patch.object(MODULE, 'helper', side_effect=helper), self.assertRaisesRegex(ValueError, 'unmatched source closure'):
            MODULE.checked_output(self.args.output, self.args.manifest_sha256, current=True)
        phase.verify_release.assert_called_once()
        verifier.verify.assert_not_called()

    def test_real_verifier_rejects_unmatched_fixture_without_staging(self):
        for current in (True, False):
            with self.subTest(current=current), self.assertRaises((ValueError, KeyError)):
                MODULE.checked_output(self.args.output, self.args.manifest_sha256, current=current)
        with self.assertRaisesRegex(ValueError, 'manifest hash mismatch'):
            MODULE.checked_output(self.args.output, '0' * 64, current=True)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_no_activation_cli_or_live_staging_paths(self):
        with self.assertRaises(SystemExit):
            MODULE.main(['--activate'])
        with self.assertRaises(ValueError):
            MODULE.safe_root('/etc/sage-lantern')
        self.args.rollback_output = str(self.rollback[0])
        with self.assertRaises(ValueError):
            MODULE.stage_update(self.args)

    def test_parent_traversal_rejected_before_filesystem_or_verifier(self):
        with patch.object(Path, 'is_symlink', side_effect=AssertionError('filesystem consulted')), \
             patch.object(Path, 'stat', side_effect=AssertionError('filesystem consulted')), \
             patch.object(MODULE, 'helper', side_effect=AssertionError('verifier consulted')):
            for value in ('/tmp/../var/lib/escape', '/tmp/safe/../../etc/escape', '../candidate'):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'parent traversal'):
                    MODULE.safe_root(value)
                with self.subTest(source=value), self.assertRaisesRegex(ValueError, 'parent traversal'):
                    MODULE.checked_output(value, 'a' * 64, current=True)
        for option in ('output', 'stage_root', 'rollback_output'):
            prior = getattr(self.args, option)
            setattr(self.args, option, '/tmp/source/../overlap')
            if option == 'rollback_output':
                self.args.rollback_manifest_sha256 = 'b' * 64
            with patch.object(MODULE, 'checked_output', side_effect=AssertionError('source read before lexical rejection')):
                with self.assertRaisesRegex(ValueError, 'parent traversal'):
                    MODULE.stage_update(self.args)
            setattr(self.args, option, prior)

    def test_manifest_replacement_between_pinning_and_verification(self):
        real_helper = MODULE.helper
        verifier = real_helper('race_verifier', 'scripts/appliance-release-manifest.py')
        manifest_path = self.candidate[0] / 'manifest.json'
        changed = json.loads(self.candidate[2])
        changed['files'][0]['sha256'] = '0' * 64
        for current in (True, False):
            with self.subTest(current=current):
                manifest_path.write_bytes(self.candidate[2])
                def replace_then_verify(*unused):
                    manifest_path.write_bytes(MODULE.canonical(changed))
                    return changed
                profile = Mock()
                def dependencies(name, filename):
                    if filename.endswith('appliance-release-manifest.py'):
                        return verifier
                    if filename.endswith('provision-fresh-appliance.py'):
                        return Mock()
                    return profile
                with patch.object(verifier, 'verify', side_effect=replace_then_verify), patch.object(MODULE, 'helper', side_effect=dependencies):
                    with self.assertRaisesRegex(ValueError, 'verified manifest differs'):
                        MODULE.checked_output(str(self.candidate[0]), self.args.manifest_sha256, current=current)
                profile.canonical_profile.assert_not_called()
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_manifest_replacement_during_profile_check_rejected(self):
        verifier = MODULE.helper('profile_race_verifier', 'scripts/appliance-release-manifest.py')
        profile = Mock()
        def replace_manifest(*unused):
            (self.candidate[0] / 'manifest.json').write_bytes(self.candidate[2] + b' ')
        profile.canonical_profile.side_effect = replace_manifest
        def dependencies(name, filename):
            return verifier if filename.endswith('appliance-release-manifest.py') else profile
        with patch.object(verifier, 'verify', return_value=self.candidate[1]), patch.object(MODULE, 'helper', side_effect=dependencies):
            with self.assertRaisesRegex(ValueError, 'manifest changed'):
                MODULE.checked_output(self.args.output, self.args.manifest_sha256, current=False)

    def test_copy_inventory_must_match_pinned_bytes(self):
        changed = json.loads(self.candidate[2])
        changed['files'][0]['sha256'] = '0' * 64
        output = self.staging / 'mismatched'
        with self.assertRaisesRegex(ValueError, 'copy inventory differs'):
            MODULE.copy_artifacts((self.candidate[0], changed, self.candidate[2], self.candidate[3]), output)
        self.assertFalse(output.exists())

    def test_strict_pinned_json_and_sealed_inventory_binding(self):
        for raw in (b'{"files":[],"files":[]}', b'{"value":NaN}', b'{"value":1e9999}'):
            with self.assertRaises(ValueError):
                MODULE.strict_json(raw)
        stage = self.stage()
        seal = json.loads((stage / 'stage.json').read_bytes())
        expected = seal['candidate']
        path = stage / 'candidate/manifest.json'
        changed = json.loads(path.read_bytes())
        changed['files'][0]['sha256'] = '0' * 64
        raw = MODULE.canonical(changed)
        path.chmod(0o644)
        path.write_bytes(raw)
        path.chmod(0o444)
        expected['manifest.json'].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ValueError, 'sealed inventory differs'):
            MODULE.verify_sealed(stage / 'candidate', expected)


if __name__ == '__main__':
    unittest.main()
