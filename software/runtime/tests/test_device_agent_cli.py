import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from sage_lantern.device_agent_cli import main


class DeviceAgentCliTests(unittest.TestCase):
    def test_identity_and_signed_observation_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity_root = root / "management-identity"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["init-identity", "--identity-root", str(identity_root)]), 0)
            identity = json.loads(stdout.getvalue())

            state_path = root / "state.json"
            state_path.write_text('{"services":{"runtime":"ready"}}', encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(
                    [
                        "observe",
                        "--identity-root",
                        str(identity_root),
                        "--device-id",
                        "lantern-a",
                        "--sequence",
                        "7",
                        "--state",
                        str(state_path),
                        "--freshness-seconds",
                        "120",
                    ]
                )
            self.assertEqual(result, 0)
            signed = json.loads(stdout.getvalue())
            self.assertEqual(signed["observation"]["source_fingerprint"], identity["fingerprint"])
            self.assertEqual(signed["observation"]["sequence"], 7)
            self.assertTrue(signed["signature"])


if __name__ == "__main__":
    unittest.main()
