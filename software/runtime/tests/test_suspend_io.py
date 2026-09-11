import os
from pathlib import Path
import tempfile
import unittest

from sage_lantern.suspend_io import model_idle, publish, read_json


class IOTests(unittest.TestCase):
    def test_exact_model_idle_metadata(self):
        self.assertTrue(model_idle([dict(id=0, is_processing=False)]))
        for value in [None, [], {}, [dict(id=0, is_processing=0)], [dict(id=True, is_processing=False)],
                      [dict(id=0, is_processing=True)], [dict(id=0)],
                      [dict(id=0, is_processing=False)] * 2]:
            self.assertFalse(model_idle(value))

    def test_atomic_private_metadata_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "status"
            publish(path, {"version": 1}, owner_uid=os.geteuid())
            self.assertEqual(read_json(path, owner_uid=os.geteuid()), {"version": 1})
            other = path.with_name("link")
            os.link(path, other)
            with self.assertRaises(ValueError):
                publish(path, {"version": 2}, owner_uid=os.geteuid())
            other.unlink()
            path.chmod(0o666)
            with self.assertRaises(ValueError):
                read_json(path, owner_uid=os.geteuid())

    def test_json_duplicates_and_oversized_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "status"
            for text in ['{"ok":true,"ok":false}', 'x' * 4097, '{"number":NaN}']:
                path.write_text(text)
                path.chmod(0o600)
                with self.assertRaises(ValueError):
                    read_json(path, owner_uid=os.geteuid())
