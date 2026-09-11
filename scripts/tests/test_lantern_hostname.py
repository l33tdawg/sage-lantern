import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/configure-unit-a-hostname.py"
SPEC = importlib.util.spec_from_file_location("lantern_hostname", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HostnameTests(unittest.TestCase):
    def test_unit_names_are_distinct_and_dns_safe(self):
        for unit in ("a", "b", "c", "d", "village-2"):
            self.assertEqual(MODULE.hostname_for_unit("unit-" + unit), "sage-lantern-" + unit)
        for unit in ("", "unit-", "unit-A", "unit-b_", "unit-b\n", "unit-../b", "unit--b", "unit-b-", "unit-" + "b" * 52):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                MODULE.hostname_for_unit(unit)

    def test_hosts_update_preserves_other_entries(self):
        original = "127.0.0.1 localhost\n127.0.1.1 old-name\n::1 localhost\n"
        result = MODULE.update_hosts(original, "sage-lantern-b")
        self.assertEqual(result, "127.0.0.1 localhost\n127.0.1.1\tsage-lantern-b\n::1 localhost\n")
        self.assertEqual(MODULE.update_hosts(result, "sage-lantern-b"), result)

    def test_missing_hosts_entry_is_added(self):
        self.assertEqual(MODULE.update_hosts("127.0.0.1 localhost\n", "sage-lantern-c"),
                         "127.0.0.1 localhost\n127.0.1.1\tsage-lantern-c\n")

    def test_dry_run_requires_no_privilege_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "unit.env"
            metadata.write_text("UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n")
            before = metadata.read_bytes()
            output = io.StringIO()
            with patch.object(MODULE.os, "geteuid", side_effect=AssertionError("privilege check")), \
                    patch.object(MODULE, "atomic_write", side_effect=AssertionError("write")), \
                    patch.object(MODULE.subprocess, "run", side_effect=AssertionError("command")), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(MODULE.main(["--dry-run", "--metadata-file", str(metadata)]), 0)
            self.assertEqual(json.loads(output.getvalue())["hostname"], "sage-lantern-b")
            self.assertEqual(metadata.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [metadata])

    def test_metadata_mismatch_and_invalid_schema_fail_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "unit.env"
            contents = (
                "UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n",
                "UNIT_ID=unit-b\nUNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n",
                "UNIT_ID=unit-b\nPROVISIONING_SCHEMA=bad\n",
                "UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\nEXTRA=value\n",
                "x" * 257,
            )
            with patch.object(MODULE, "atomic_write", side_effect=AssertionError("write")):
                for content in contents:
                    metadata.write_text(content)
                    with self.subTest(content=content), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        MODULE.main(["--dry-run", "--unit-id", "unit-b", "--metadata-file", str(metadata)])

    def test_alternate_metadata_cannot_authorize_live_changes(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            MODULE.main(["--metadata-file", "/tmp/not-live-unit.env"])


if __name__ == "__main__":
    unittest.main()
