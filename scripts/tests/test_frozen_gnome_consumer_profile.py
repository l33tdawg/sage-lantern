"""Real credential/interpreter refusals in the disposable Linux replay."""
from pathlib import Path
import importlib.util
import sys
import unittest


if not (sys.platform == "linux" and Path("/input/consumer.py").is_file()
        and __name__ == "__main__"):
    raise unittest.SkipTest("Run explicit container profile-refusal commands")

spec = importlib.util.spec_from_file_location("profile_consumer", "/input/consumer.py")
consumer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = consumer
spec.loader.exec_module(consumer)

def identity_must_not_be_read(_held):
    raise AssertionError("Rejected runtime attempted identity/capture IO")

consumer._identity = identity_must_not_be_read
expected = sys.argv[1]
result = consumer.review_capture("/nonexistent-policy.json")
assert not result.evidence_consumable, result
assert not result.runtime_authorized, result
assert result.reasons == (expected,), result
print("Real runtime refusal verified:", expected)
