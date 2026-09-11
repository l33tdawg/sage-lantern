import tempfile
import unittest
from pathlib import Path

from sage_lantern.dedupe import DuplicateStore


class DuplicateStoreTests(unittest.TestCase):
    def test_source_and_message_pair_is_recorded_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DuplicateStore(Path(tmp) / "seen.db")
            self.assertTrue(store.check_and_record("a" * 64, "msg-1"))
            self.assertFalse(store.check_and_record("a" * 64, "msg-1"))
            self.assertTrue(store.check_and_record("b" * 64, "msg-1"))
            store.close()


if __name__ == "__main__":
    unittest.main()

