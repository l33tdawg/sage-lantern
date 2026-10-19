"""Actual FD/root-filesystem replay in a disposable, capless Linux container.

Run with Python -I -S. No pytest dependency. /review and /etc are disposable
container tmpfs; /input is a read-only frozen test snapshot. Synthetic identity
and captures only. No native loader, host namespace, device or network action.
"""
from pathlib import Path
import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest


if not (sys.platform == "linux" and os.geteuid() == 0 and sys.flags.isolated
        and sys.flags.no_site and Path("/input/consumer.py").is_file()):
    raise unittest.SkipTest("Use the frozen, isolated Linux root-consumer replay")

INPUT = Path("/input")
CODE = Path("/review/consumer.py")
CODE.write_bytes((INPUT / "consumer.py").read_bytes())
CODE.chmod(0o600)
spec = importlib.util.spec_from_file_location("root_consumer_under_test", CODE)
consumer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = consumer
spec.loader.exec_module(consumer)
MACHINE = "0123456789abcdef0123456789abcdef"
Path("/etc/machine-id").write_text(MACHINE + "\n")
Path("/etc/machine-id").chmod(0o644)
BOOT = Path("/proc/sys/kernel/random/boot_id").read_text().strip()


class RootCaptureReplay(unittest.TestCase):
    def setUp(self):
        current = Path(consumer.CURRENT_RECORD)
        current.parent.chmod(0o700)
        if current.is_dir():
            current.rmdir()
        else:
            current.unlink(missing_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="capture-", dir="/review")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "code"
        self.source.mkdir(mode=0o700)
        for name in consumer.SOURCE_PINS:
            shutil.copyfile(INPUT / "sources" / name, self.source / name)
            (self.source / name).chmod(0o600)
        self.payload = self.base / "capture"
        shutil.copytree(INPUT / "closed", self.payload)
        self.payload.chmod(0o700)
        for file in self.payload.iterdir():
            file.chmod(0o600)
        self.authority = self.base / "authority.json"
        self.policy_file = self.base / "policy.json"
        self.policy = {
            "schema": consumer.SCHEMA, "contract_version": 3,
            "machine_id": MACHINE, "boot_id": BOOT,
            "capture_id": "12345678-1234-1234-1234-123456789abc", "generation": 1,
            "authority_file": str(self.authority), "source_directory": str(self.source),
            "capture_directory": str(self.payload),
            "consumer_sha256": consumer.digest(CODE.read_bytes()),
            "python_sha256": consumer.digest(Path(sys.executable).read_bytes()),
            "payload_digests": {p.stem: consumer.digest(p.read_bytes())
                                for p in self.payload.iterdir()},
        }
        self.write_policy()

    def write_policy(self):
        data = consumer.canonical(self.policy)
        self.policy_file.write_bytes(data)
        self.policy_file.chmod(0o600)
        authority = {name: self.policy[name] for name in
                     ("machine_id", "boot_id", "capture_id", "generation")}
        authority.update(schema=consumer.AUTHORITY_SCHEMA, policy_sha256=consumer.digest(data))
        self.authority.write_bytes(consumer.canonical(authority))
        self.authority.chmod(0o600)
        self.write_current()

    def write_current(self, *, state="active"):
        record = {name: self.policy[name] for name in
                  ("machine_id", "boot_id", "capture_id", "generation")}
        record.update(schema=consumer.CURRENT_SCHEMA, state=state,
                      policy_path=str(self.policy_file),
                      policy_sha256=consumer.digest(self.policy_file.read_bytes()))
        path = Path(consumer.CURRENT_RECORD)
        path.write_bytes(consumer.canonical(record))
        path.chmod(0o600)

    def change_current(self, **changes):
        path = Path(consumer.CURRENT_RECORD)
        record = json.loads(path.read_bytes())
        record.update(changes)
        path.write_bytes(consumer.canonical(record))
        path.chmod(0o600)

    def review(self):
        return consumer.review_capture(str(self.policy_file))

    def denied(self, reason=None):
        result = self.review()
        self.assertFalse(result.evidence_consumable, result)
        self.assertIsNone(result.unit_path)
        self.assertFalse(result.runtime_authorized)
        if reason:
            self.assertIn(reason, result.reasons)
        return result

    @contextlib.contextmanager
    def at_compile(self, change):
        original = consumer._load_contract
        def wrapped(sources):
            result = original(sources)
            change()
            return result
        consumer._load_contract = wrapped
        try:
            yield
        finally:
            consumer._load_contract = original

    def test_complete_held_capture_positive_and_repeat_cleanup(self):
        for _ in range(2):
            result = self.review()
            self.assertTrue(result.evidence_consumable, result)
            self.assertEqual(result.policy_sha256, consumer.digest(self.policy_file.read_bytes()))
            self.assertEqual(result.capture_id, self.policy["capture_id"])
            self.assertEqual(result.generation, 1)
            self.assertEqual(result.unit_path, json.loads((self.payload / "verified_unit_path.json").read_bytes()))
            self.assertFalse(result.runtime_authorized)
            self.assertFalse(any(n.startswith(consumer._PREFIX) for n in sys.modules))

    def test_valid_unobserved_capture_is_honest_and_refused(self):
        shutil.rmtree(self.payload)
        shutil.copytree(INPUT / "open", self.payload)
        for file in self.payload.iterdir():
            file.chmod(0o600)
        self.policy["payload_digests"] = {p.stem: consumer.digest(p.read_bytes())
                                         for p in self.payload.iterdir()}
        self.write_policy()
        self.denied("analysis_not_closed")

    def test_policy_leaf_is_not_authority_under_writable_parent(self):
        self.base.chmod(0o777)
        self.denied("untrusted_file_ancestry")

    def test_writable_policy_leaf_refused(self):
        self.policy_file.chmod(0o666)
        self.denied("untrusted_file_ancestry")

    def test_source_directory_symlink_refused(self):
        alias = self.base / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        self.policy["source_directory"] = str(alias)
        self.write_policy()
        self.denied()

    def test_payload_leaf_symlink_refused(self):
        file = self.payload / "paths.json"
        other = self.base / "other.json"
        file.rename(other)
        file.symlink_to(other)
        self.denied()

    def test_hardlinked_policy_refused(self):
        os.link(self.policy_file, self.base / "duplicate-policy.json")
        self.denied("untrusted_file_ancestry")

    def test_fifo_policy_refused_without_blocking(self):
        self.policy_file.unlink()
        os.mkfifo(self.policy_file, 0o600)
        self.denied("untrusted_file_ancestry")

    def test_directory_in_place_of_policy_refused(self):
        self.policy_file.unlink()
        self.policy_file.mkdir()
        self.denied("untrusted_file_ancestry")

    def test_wrong_owner_metadata_refused(self):
        real = os.stat(self.policy_file)
        info = types.SimpleNamespace(st_uid=1000, st_mode=real.st_mode, st_nlink=real.st_nlink)
        with self.assertRaises(consumer.CaptureRefusal):
            consumer._trusted(info)

    def test_contract_code_digest_is_fixed_not_caller_selected(self):
        with (self.source / "evidence_contract.py").open("ab") as file:
            file.write(b"\n# changed\n")
        self.denied("analysis_code_mismatch")

    def test_schema_module_digest_is_also_pinned(self):
        (self.source / "analysis.py").write_text("raise RuntimeError('must not execute')\n")
        self.denied("analysis_code_mismatch")

    def test_consumer_digest_mismatch_refused(self):
        self.policy["consumer_sha256"] = "0" * 64
        self.write_policy()
        self.denied("consumer_code_mismatch")

    def test_interpreter_digest_mismatch_refused(self):
        self.policy["python_sha256"] = "0" * 64
        self.write_policy()
        self.denied("interpreter_code_mismatch")

    def test_machine_binding_refuses_another_device(self):
        self.policy["machine_id"] = "f" * 32
        self.write_policy()
        self.denied("platform_identity_mismatch")

    def test_boot_binding_refuses_previous_boot(self):
        self.policy["boot_id"] = "00000000-0000-0000-0000-000000000000"
        self.write_policy()
        self.denied("platform_identity_mismatch")

    def test_stale_generation_refused(self):
        current = json.loads(self.authority.read_bytes())
        current["generation"] = 2
        self.authority.write_bytes(consumer.canonical(current))
        self.denied("authority_epoch_or_policy_mismatch")

    def test_authority_policy_binding_refused(self):
        current = json.loads(self.authority.read_bytes())
        current["policy_sha256"] = "0" * 64
        self.authority.write_bytes(consumer.canonical(current))
        self.denied("authority_epoch_or_policy_mismatch")

    def test_authority_generation_bool_is_not_an_integer(self):
        current = json.loads(self.authority.read_bytes())
        current["generation"] = True
        self.authority.write_bytes(consumer.canonical(current))
        self.denied("authority_epoch_or_policy_mismatch")

    def test_policy_boolean_version_refused(self):
        self.policy["contract_version"] = True
        self.write_policy()
        self.denied("policy_version_invalid")

    def test_unknown_policy_authority_flag_refused(self):
        self.policy["allow_launch"] = True
        self.write_policy()
        self.denied("policy_schema_invalid")

    def test_missing_section_manifest_refused(self):
        del self.policy["payload_digests"]["paths"]
        self.write_policy()
        self.denied("capture_section_set_invalid")

    def test_changed_payload_digest_refused_before_decode(self):
        with (self.payload / "paths.json").open("ab") as file:
            file.write(b" ")
        self.denied("capture_digest_mismatch")

    def test_rehashed_invalid_typed_evidence_cannot_be_authority(self):
        file = self.payload / "paths.json"
        file.write_bytes(b"false")
        self.policy["payload_digests"]["paths"] = consumer.digest(file.read_bytes())
        self.write_policy()
        self.denied("invalid_section_schema")

    def test_rehashed_admitted_path_must_come_from_decoded_proposal(self):
        file = self.payload / "verified_unit_path.json"
        file.write_bytes(consumer.canonical("/home/sage/.config/systemd/user"))
        self.policy["payload_digests"]["verified_unit_path"] = consumer.digest(file.read_bytes())
        self.write_policy()
        self.denied("path_not_derived_from_proposal")

    def test_duplicate_json_key_refused_even_when_rehashed(self):
        file = self.payload / "manager_mutations.json"
        file.write_bytes(b'{"state":"unobserved","state":"observed_empty","reachable":[]}')
        self.policy["payload_digests"]["manager_mutations"] = consumer.digest(file.read_bytes())
        self.write_policy()
        self.denied("duplicate_json_key")

    def test_noncanonical_policy_is_not_repaired(self):
        self.policy_file.write_bytes(json.dumps(self.policy, indent=2).encode())
        self.denied("noncanonical_json")

    def test_noncanonical_payload_is_not_substituted(self):
        file = self.payload / "manager_mutations.json"
        file.write_bytes(json.dumps(json.loads(file.read_bytes()), indent=2).encode())
        self.policy["payload_digests"]["manager_mutations"] = consumer.digest(file.read_bytes())
        self.write_policy()
        self.denied("value_not_bound")

    def test_stale_module_namespace_cannot_replace_pinned_code(self):
        name = consumer._PREFIX + ".analysis"
        sys.modules[name] = types.ModuleType(name)
        try:
            self.denied("module_namespace_not_clean")
            self.assertIn(name, sys.modules)
        finally:
            del sys.modules[name]

    def test_authority_change_during_analysis_invalidates_snapshot(self):
        def change():
            current = json.loads(self.authority.read_bytes())
            current["generation"] += 1
            self.authority.write_bytes(consumer.canonical(current))
        with self.at_compile(change):
            self.denied("held_file_changed")

    def test_identical_payload_replacement_is_an_identity_change(self):
        def change():
            file = self.payload / "paths.json"
            replacement = self.base / "replacement.json"
            replacement.write_bytes(file.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(file)
        with self.at_compile(change):
            self.denied()

    def test_source_change_after_hold_refuses_before_result(self):
        def change():
            with (self.source / "analysis.py").open("ab") as file:
                file.write(b"\n# changed after compilation from held bytes\n")
        with self.at_compile(change):
            self.denied("held_file_changed")

    def test_policy_change_after_hold_refuses_before_result(self):
        def change():
            with self.policy_file.open("ab") as file:
                file.write(b" ")
        with self.at_compile(change):
            self.denied("held_file_changed")

    def test_descriptor_cleanup_on_success_and_repeated_refusal(self):
        before = len(list(Path("/proc/self/fd").iterdir()))
        self.assertTrue(self.review().evidence_consumable)
        self.policy_file.chmod(0o666)
        for _ in range(10):
            self.denied()
        self.assertEqual(len(list(Path("/proc/self/fd").iterdir())), before)

    def test_current_generation_controls_old_and_new_pairs(self):
        older_policy = self.base / "older-policy.json"
        older_authority = self.base / "older-authority.json"
        old = dict(self.policy, authority_file=str(older_authority))
        data = consumer.canonical(old)
        older_policy.write_bytes(data)
        older_policy.chmod(0o600)
        authority = {name: old[name] for name in
                     ("machine_id", "boot_id", "capture_id", "generation")}
        authority.update(schema=consumer.AUTHORITY_SCHEMA, policy_sha256=consumer.digest(data))
        older_authority.write_bytes(consumer.canonical(authority))
        older_authority.chmod(0o600)
        self.policy["generation"] = 2
        self.write_policy()
        current = self.review()
        self.assertTrue(current.evidence_consumable, current)
        self.assertEqual(current.generation, 2)
        old_result = consumer.review_capture(str(older_policy))
        self.assertFalse(old_result.evidence_consumable, old_result)
        self.assertIn("capture_is_not_current", old_result.reasons)
        self.assertFalse(old_result.runtime_authorized)

    def test_same_bytes_at_alternate_policy_path_are_not_current(self):
        alternate = self.base / "alternate-policy.json"
        alternate.write_bytes(self.policy_file.read_bytes())
        alternate.chmod(0o600)
        result = consumer.review_capture(str(alternate))
        self.assertFalse(result.evidence_consumable, result)
        self.assertIn("capture_is_not_current", result.reasons)

    def test_rollback_of_whole_protected_store_remains_a_producer_limit(self):
        paths = (self.policy_file, self.authority, Path(consumer.CURRENT_RECORD))
        older = {path: path.read_bytes() for path in paths}
        self.policy["generation"] = 2
        self.write_policy()
        self.assertTrue(self.review().evidence_consumable)
        for path, data in older.items():
            path.write_bytes(data)
        result = self.review()
        # Explicit limitation control: this reader cannot attest that the trusted
        # store itself was rolled back by its root producer or restored storage.
        self.assertTrue(result.evidence_consumable, result)
        self.assertEqual(result.generation, 1)
        self.assertFalse(result.runtime_authorized)

    def test_same_generation_policy_substitution_is_not_current(self):
        original = Path(consumer.CURRENT_RECORD).read_bytes()
        self.policy["capture_id"] = "87654321-4321-4321-4321-cba987654321"
        self.write_policy()
        Path(consumer.CURRENT_RECORD).write_bytes(original)
        self.denied("capture_is_not_current")

    def test_current_record_fields_cannot_claim_a_different_capture(self):
        original = Path(consumer.CURRENT_RECORD).read_bytes()
        changes = ({"generation": 2}, {"capture_id": "87654321-4321-4321-4321-cba987654321"},
                   {"machine_id": "f" * 32}, {"boot_id": "00000000-0000-0000-0000-000000000000"},
                   {"policy_sha256": "0" * 64}, {"policy_path": str(self.base / "other.json")})
        for change in changes:
            with self.subTest(change=change):
                Path(consumer.CURRENT_RECORD).write_bytes(original)
                self.change_current(**change)
                self.denied("capture_is_not_current")

    def test_current_record_invalid_shape_and_values_refused(self):
        original = Path(consumer.CURRENT_RECORD).read_bytes()
        changes = ({"generation": True}, {"generation": 0}, {"generation": 2**64},
                   {"schema": "future"}, {"state": True}, {"state": "unknown"},
                   {"capture_id": "bad"}, {"policy_sha256": "bad"},
                   {"policy_path": "relative"}, {"allow_launch": True})
        for change in changes:
            with self.subTest(change=change):
                Path(consumer.CURRENT_RECORD).write_bytes(original)
                self.change_current(**change)
                self.denied()

    def test_missing_current_record_refuses_even_consistent_pair(self):
        Path(consumer.CURRENT_RECORD).unlink()
        self.denied()

    def test_revoked_current_record_refuses_even_consistent_pair(self):
        self.write_current(state="revoked")
        self.denied("current_capture_revoked")

    def test_torn_and_duplicate_current_record_refused(self):
        path = Path(consumer.CURRENT_RECORD)
        for data in (b'{"schema":', b'{"state":"active","state":"active"}'):
            with self.subTest(data=data):
                path.write_bytes(data)
                self.denied()

    def test_oversized_current_record_refused(self):
        Path(consumer.CURRENT_RECORD).write_bytes(b" " * 4097)
        self.denied("payload_size_exceeded")

    def test_current_record_symlink_refused(self):
        path = Path(consumer.CURRENT_RECORD)
        target = self.base / "unanchored-current.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        self.denied()

    def test_current_record_hardlink_refused(self):
        target = Path(consumer.CURRENT_RECORD).with_name("duplicate-current.json")
        os.link(consumer.CURRENT_RECORD, target)
        self.addCleanup(target.unlink)
        self.denied("untrusted_file_ancestry")

    def test_current_record_writable_parent_refused(self):
        path = Path(consumer.CURRENT_RECORD)
        path.parent.chmod(0o770)
        self.addCleanup(path.parent.chmod, 0o700)
        self.denied("untrusted_file_ancestry")

    def test_current_record_special_file_refused(self):
        path = Path(consumer.CURRENT_RECORD)
        path.unlink()
        os.mkfifo(path, 0o600)
        self.denied()

    def test_advancement_during_analysis_invalidates_held_capture(self):
        with self.at_compile(lambda: self.change_current(generation=2)):
            self.denied("held_file_changed")

    def test_revocation_during_analysis_invalidates_held_capture(self):
        with self.at_compile(lambda: self.write_current(state="revoked")):
            self.denied("held_file_changed")

    def test_withdrawal_during_analysis_invalidates_held_capture(self):
        with self.at_compile(lambda: Path(consumer.CURRENT_RECORD).unlink()):
            self.denied()

    def test_identical_current_record_replacement_invalidates_identity(self):
        def replace():
            path = Path(consumer.CURRENT_RECORD)
            alternate = path.parent / "replace.json"
            alternate.write_bytes(path.read_bytes())
            alternate.chmod(0o600)
            alternate.replace(path)
        with self.at_compile(replace):
            self.denied()

    def test_current_record_is_rechecked_after_other_files(self):
        original = consumer._HeldFiles.recheck
        def recheck(held, only_path=None):
            result = original(held, only_path)
            if only_path is None:
                self.change_current(generation=2)
            return result
        consumer._HeldFiles.recheck = recheck
        try:
            self.denied("held_file_changed")
        finally:
            consumer._HeldFiles.recheck = original

    def test_repeated_anchor_refusals_clean_descriptors_and_modules(self):
        before = len(list(Path("/proc/self/fd").iterdir()))
        for _ in range(10):
            self.write_current()
            with self.at_compile(lambda: self.write_current(state="revoked")):
                self.denied()
            self.assertFalse(any(name.startswith(consumer._PREFIX) for name in sys.modules))
        self.assertEqual(len(list(Path("/proc/self/fd").iterdir())), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
