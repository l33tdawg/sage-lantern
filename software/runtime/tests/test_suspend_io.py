import os
from pathlib import Path
import tempfile
import unittest

from sage_lantern.suspend_io import PUBLIC_ENDPOINTS, model_idle, publish, read_json, public_json


class IOTests(unittest.TestCase):
    def test_exact_model_idle_metadata(self):
        self.assertTrue(model_idle([dict(id=0, is_processing=False)]))
        for value in [None, [], {}, [dict(id=0, is_processing=0)], [dict(id=True, is_processing=False)],
                      [dict(id=0, is_processing=True)], [dict(id=0)],
                      [dict(id=0, is_processing=False)] * 2]:
            self.assertFalse(model_idle(value))

    def test_public_read_allowlist_covers_both_model_servers_only(self):
        self.assertEqual(PUBLIC_ENDPOINTS, {(3196, "/state"), (3196, "/power-policy"),
                                            (3196, "/sleep-ready"), (8081, "/slots"), (8082, "/slots")})
        for port, path in ((8082, "/health"), (8081, "/props"), (8080, "/slots"), (3196, "/slots")):
            with self.subTest(port=port, path=path), self.assertRaises(ValueError):
                public_json(port, path)

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
