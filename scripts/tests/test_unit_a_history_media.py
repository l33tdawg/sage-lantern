import base64
import builtins
from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import test_unit_a_voice_history as history_tests
from test_unit_a_voice_history import HistoryFixture, MODULE, SCRIPT
from test_unit_a_voice_photo import jpeg


class HistoryMediaIntegrationTests(HistoryFixture, unittest.TestCase):
    request = history_tests.HistoryHttpTests.request

    def add_photo(self):
        session_id = str(uuid.uuid4())
        self.history.record(self.history.consent(), session_id, self.result, original=jpeg(1920, 1080))
        return session_id

    def test_fresh_defaults_and_v1_explicit_false_migration(self):
        response = self.history.request("list", {})
        self.assertTrue(response["enabled"])
        self.assertTrue(response["photos_enabled"])
        self.assertTrue(response["photo_saving_supported"])
        session_id = self.add()
        value = json.loads(self.history.path.read_text())
        value.update(version=1, enabled=False)
        value.pop("photos_enabled")
        for item in value["items"]:
            item.pop("has_photo")
        old_generation = value["generation"]
        self.history.save(value)
        restarted = MODULE.LocalHistory(self.directory)
        response = restarted.request("list", {})
        self.assertFalse(response["enabled"])
        self.assertTrue(response["photos_enabled"])
        self.assertEqual(response["items"][0]["session_id"], session_id)
        self.assertFalse(response["items"][0]["has_photo"])
        self.assertIsNone(restarted.consent())
        migrated = json.loads(self.history.path.read_text())
        self.assertEqual(migrated["version"], 2)
        self.assertNotEqual(migrated["generation"], old_generation)
        self.assertEqual(restarted.request("list", {}), response)

    def test_photo_association_and_no_bulk_images_or_inference_fallback(self):
        session_id = self.add_photo()
        text_id = self.add(photo="untrusted-path", image_base64="untrusted-image")
        status, response = self.request()
        self.assertEqual(status, 200)
        self.assertNotIn("image_base64", json.dumps(response))
        items = {item["session_id"]: item for item in response["items"]}
        self.assertTrue(items[session_id]["has_photo"])
        self.assertFalse(items[text_id]["has_photo"])
        self.assertEqual(self.request("photo", {"session_id": session_id}),
                         (200, {"image_base64": base64.b64encode(jpeg(1920, 1080)).decode()}))
        self.assertEqual(self.request("photo", {"session_id": text_id})[0], 404)
        self.assertEqual(self.request("photo", {"session_id": str(uuid.uuid4())})[0], 404)
        self.history.request("settings", {"enabled": False, "photos_enabled": False})
        self.assertEqual(self.request("photo", {"session_id": session_id})[0], 200)

    def test_photo_route_security_and_payload_bounds(self):
        session_id = self.add_photo()
        for changes in ({"Origin": None}, {"Origin": "null"}, {"Host": "other:3197"},
                        {"X-Lantern-Voice": None}, {"Content-Type": "text/plain"},
                        {"Content-Length": "513"}, {"Transfer-Encoding": "chunked"}):
            self.assertIn(self.request("photo", {"session_id": session_id}, changes=changes)[0], (400, 403))
        for duplicate in ("Origin", "Host", "Content-Length"):
            self.assertIn(self.request("photo", {"session_id": session_id}, duplicate=duplicate)[0], (400, 403))
        for payload in ({"session_id": "../private"}, {"session_id": session_id, "path": "/private"},
                        {"session_id": session_id.upper()}, {}):
            self.assertEqual(self.request("photo", payload)[0], 400)
        self.assertEqual(self.request("photo", method="GET", changes={"Content-Length": "0"})[0], 404)
        self.assertEqual(self.request("photo", method="OPTIONS", changes={
            "Content-Length": "0", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-lantern-voice",
        })[0], 200)

    def test_preferences_rotate_consent_and_disable_preserves_media(self):
        session_id = self.add_photo()
        consent = self.history.consent()
        response = self.history.request("settings", {"photos_enabled": False})
        self.assertFalse(response["photos_enabled"])
        self.assertNotEqual(consent, self.history.consent())
        self.history.record(consent, str(uuid.uuid4()), self.result, original=jpeg())
        text_id = str(uuid.uuid4())
        self.history.record(self.history.consent(), text_id, self.result, original=jpeg())
        items = self.history.request("list", {})["items"]
        self.assertEqual(len(items), 2)
        self.assertFalse(items[0]["has_photo"])
        self.history.request("settings", {"enabled": False})
        self.assertIsNone(self.history.consent())
        self.assertEqual(self.request("photo", {"session_id": session_id})[0], 200)
        with patch.object(MODULE.HistoryMediaStore, "put", side_effect=AssertionError("unexpected save")):
            self.history.record(None, str(uuid.uuid4()), self.result, original=jpeg())
        restarted = MODULE.LocalHistory(self.directory).request("list", {})
        self.assertFalse(restarted["enabled"])
        self.assertFalse(restarted["photos_enabled"])
        for payload in ({"photos_enabled": 1}, {"photos_enabled": None}, {"enabled": True, "extra": False}):
            self.assertEqual(self.request("settings", payload)[0], 400)

    def test_delete_clear_and_missing_delete_invalidate_inflight_consent(self):
        for action in ("delete", "clear", "missing-delete"):
            session_id = self.add_photo()
            consent = self.history.consent()
            target = str(uuid.uuid4()) if action == "missing-delete" else session_id
            payload = {} if action == "clear" else {"session_id": target}
            self.history.request("clear" if action == "clear" else "delete", payload)
            self.assertNotEqual(consent, self.history.consent())
            pending_id = str(uuid.uuid4())
            self.history.record(consent, pending_id, self.result, original=jpeg())
            self.assertNotIn(pending_id, [item["session_id"] for item in self.history.request("list", {})["items"]])
            if action != "missing-delete":
                self.assertEqual(self.request("photo", {"session_id": session_id})[0], 404)
                self.assertFalse((self.history.media_directory / (session_id + ".media")).exists())
            self.history.request("clear", {})

    def test_restart_prunes_orphans_pending_and_expired_associations(self):
        retained = self.add_photo()
        media = self.history.media()
        orphan = str(uuid.uuid4())
        media.put(orphan, jpeg())
        pending = self.history.media_directory / (".pending-" + str(uuid.uuid4()))
        pending.write_bytes(b"synthetic interrupted write")
        pending.chmod(0o600)
        restarted = MODULE.LocalHistory(self.directory)
        restarted.request("list", {})
        self.assertEqual({path.name for path in self.history.media_directory.iterdir()}, {retained + ".media"})
        with patch.object(MODULE.time, "time", return_value=(datetime.now(UTC) + timedelta(days=31)).timestamp()):
            self.assertEqual(restarted.request("list", {})["items"], [])
        self.assertEqual(list(self.history.media_directory.iterdir()), [])

    def test_metadata_failure_before_and_after_commit_never_deletes_linked_bytes(self):
        retained = self.add_photo()
        save = self.history.save
        for committed in (False, True):
            session_id = str(uuid.uuid4())
            consent = self.history.consent()

            def fail(value):
                if committed:
                    save(value)
                raise OSError("synthetic metadata failure")

            with patch.object(self.history, "save", side_effect=fail):
                with self.assertRaises(MODULE.ControlError):
                    self.history.record(consent, session_id, self.result, original=jpeg())
            path = self.history.media_directory / (session_id + ".media")
            self.assertTrue(path.exists())
            self.assertTrue((self.history.media_directory / (retained + ".media")).exists())
            restarted = MODULE.LocalHistory(self.directory)
            response = restarted.request("list", {})
            self.assertEqual(path.exists(), committed)
            self.assertEqual(any(item["session_id"] == session_id for item in response["items"]), committed)

    def test_clear_failure_does_not_prune_before_metadata_and_retries_cleanup(self):
        session_id = self.add_photo()
        path = self.history.media_directory / (session_id + ".media")
        with patch.object(self.history, "save", side_effect=OSError("before commit")):
            with self.assertRaises(MODULE.ControlError):
                self.history.request("clear", {})
        self.assertTrue(path.exists())
        original = self.history.prune_media

        def fail_after_commit(value):
            if not value["items"]:
                raise MODULE.HistoryMediaError("media_unavailable")
            original(value)

        with patch.object(self.history, "prune_media", side_effect=fail_after_commit):
            with self.assertRaises(MODULE.ControlError):
                self.history.request("clear", {})
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(self.history.path.read_text())["items"], [])
        self.assertEqual(MODULE.LocalHistory(self.directory).request("list", {})["items"], [])
        self.assertFalse(path.exists())

    def test_media_put_failure_does_not_claim_photo_or_record(self):
        self.history.request("list", {})
        before = self.history.path.read_bytes()
        with patch.object(MODULE.HistoryMediaStore, "put", side_effect=MODULE.HistoryMediaError("insufficient_space")):
            with self.assertRaises(MODULE.ControlError):
                self.add_photo()
        self.assertEqual(self.history.path.read_bytes(), before)

    def test_missing_media_dependency_blocks_defaults_and_original_upload(self):
        with patch.object(MODULE, "HistoryMediaStore", None):
            self.assertEqual(self.request(), (503, {"error": "history_media_unavailable"}))
            self.assertFalse(self.directory.exists())
            control = MODULE.VoiceControl(30, photo_ready=True, photo_dir=self.directory)
            with self.assertRaises(MODULE.ControlError) as caught:
                control.start(str(uuid.uuid4()), jpeg(), jpeg(1920, 1080))
            self.assertEqual(caught.exception.code, "history_media_unavailable")
            self.assertFalse(control.busy())
        original_import = builtins.__import__

        def unavailable(name, *args, **kwargs):
            if name == "sage_lantern.history_media":
                raise ImportError("private interpreter path")
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location("voice_without_media", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        with patch("builtins.__import__", side_effect=unavailable):
            spec.loader.exec_module(module)
        with self.assertRaises(module.ControlError) as caught:
            module.LocalHistory(self.directory).request("list", {})
        self.assertEqual((caught.exception.status, caught.exception.code), (503, "history_media_unavailable"))
        self.assertFalse(self.directory.exists())


if __name__ == "__main__":
    unittest.main()
