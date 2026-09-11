import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / "lantern"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = load_module("verify-local-voice")
APPROVE = load_module("approve-local-voice")


class VoiceReadinessTests(unittest.TestCase):
    def test_requires_explicit_loopback(self):
        for url in ["http://127.0.0.1:8080", "http://[::1]:8080"]:
            VERIFY.require_loopback(url)
        for url in ["https://example.com", "http://192.168.50.103:8080", "http://localhost:8080",
                    "http://user@127.0.0.1", "http://127.0.0.1/elsewhere", "http://127.0.0.1?query=1"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                VERIFY.require_loopback(url)

    def test_capture_and_signed_recall_are_required(self):
        for scenario in ["pass", "denied", "capture_failed", "static"]:
            with self.subTest(scenario=scenario):
                config = argparse.Namespace(
                    sage_base_url="http://127.0.0.1:8080", llama_base_url="http://127.0.0.1:8081",
                    static_context=["bypass"] if scenario == "static" else [],
                    sage_identity_path=Path("voice.key"), sage_domain="local-field-knowledge",
                )
                source = Mock()
                if scenario == "denied":
                    source.recall.side_effect = RuntimeError("HTTP 403")
                loop = Mock()
                loop.capture.return_value = scenario != "capture_failed"
                adapters = types.ModuleType("sage_lantern.adapters")
                adapters.SageSDKContext = Mock(return_value=source)
                configuration = types.ModuleType("sage_lantern.config")
                configuration.RuntimeConfig = Mock()
                configuration.RuntimeConfig.load.return_value = config
                with patch.dict(sys.modules, {"sage_lantern.adapters": adapters,
                                              "sage_lantern.config": configuration}), patch.object(
                    VERIFY.runpy, "run_path", return_value={"VoiceLoop": Mock(return_value=loop)},
                ):
                    if scenario == "pass":
                        result = VERIFY.verify(Path("config"), Path("loop"), "hw:CARD=Audio,DEV=0")
                        self.assertEqual(result["signed_context"], "passed")
                        self.assertEqual(result["physical_ptt_end_to_end"], "requires_operator_test")
                        self.assertFalse(loop.capture.call_args.args[1].parent.exists())
                    else:
                        with self.assertRaises((ValueError, RuntimeError)):
                            VERIFY.verify(Path("config"), Path("loop"), "hw:CARD=Audio,DEV=0")
                    if scenario in {"denied", "static"}:
                        loop.capture.assert_not_called()

    def test_identity_seed_restrictions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.key"
            path.write_bytes(b"a" * 32)
            path.chmod(0o600)
            self.assertEqual(APPROVE.seed_bytes(path), b"a" * 32)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                APPROVE.seed_bytes(path)
            path.chmod(0o600)
            path.write_bytes(b"short")
            with self.assertRaises(ValueError):
                APPROVE.seed_bytes(path)
            link = Path(directory) / "link.key"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                APPROVE.seed_bytes(link)


if __name__ == "__main__":
    unittest.main()
