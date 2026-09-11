import os
from pathlib import Path
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from uuid import uuid4

from nacl.secret import SecretBox
from nacl.utils import random

from sage_lantern.knowledge_sharing import DATABASE, KnowledgeSharingError, KnowledgeSharingStore


class Crypto:
    def __init__(self):
        self.box = SecretBox(random(SecretBox.KEY_SIZE))

    def encrypt(self, value):
        return bytes(self.box.encrypt(value))

    def decrypt(self, value):
        return self.box.decrypt(value)


class KnowledgeSharingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.crypto = Crypto()
        self.store = KnowledgeSharingStore(directory=self.directory, crypto=self.crypto)
        self.addCleanup(self.store.close)
        self.source_id = str(uuid4())

    def create(self, **overrides):
        arguments = dict(source_id=self.source_id, question="Private question canary سؤال",
                         answer="Unverified private answer canary 答案", request_id=str(uuid4()))
        arguments.update(overrides)
        return self.store.create_draft(**arguments)

    def error(self, code, callback):
        with self.assertRaises(KnowledgeSharingError) as raised:
            callback()
        self.assertEqual(str(raised.exception), code)

    def test_encryption_required_before_any_storage(self):
        target = self.directory / "new"
        self.error("crypto_required", lambda: KnowledgeSharingStore(directory=target))
        self.assertFalse(target.exists())

    def test_restart_roundtrip_and_no_plaintext_payload_or_source_id(self):
        draft = self.create()
        self.store.close()
        for path in self.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            data = path.read_bytes()
            for secret in ("Private question canary", "Unverified private answer canary", self.source_id):
                self.assertNotIn(secret.encode(), data)
        reopened = KnowledgeSharingStore(directory=self.directory, crypto=self.crypto)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get(draft["draft_id"]), draft)

    def test_create_idempotent_and_conflicting_reuse_denied(self):
        request_id = str(uuid4())
        first = self.create(request_id=request_id)
        self.assertEqual(first, self.create(request_id=request_id))
        self.error("idempotency_conflict", lambda: self.create(request_id=request_id, answer="changed"))

    def test_review_consent_and_truthful_local_save(self):
        draft = self.create()
        draft_id = draft["draft_id"]
        self.error("review_required", lambda: self.store.save_reviewed(draft_id, expected_revision=1, consent=True, request_id=str(uuid4())))
        reviewed = self.store.review_draft(draft_id, expected_revision=1, request_id=str(uuid4()))
        self.assertTrue(reviewed["reviewed"])
        self.assertEqual(reviewed["revision"], 2)
        for consent in (False, None, 1, "yes"):
            self.error("consent_required", lambda: self.store.save_reviewed(draft_id, expected_revision=2, consent=consent, request_id=str(uuid4())))
        saved = self.store.save_reviewed(draft_id, expected_revision=2, consent=True, request_id=str(uuid4()))
        self.assertTrue(saved["consented"])
        self.assertEqual(saved["state"], "DRAFT")
        self.assertEqual(saved["transport"], "unavailable")
        self.assertEqual(saved["publication"], "not_submitted")
        self.assertFalse(saved["signed"])
        self.error("transport_unavailable", lambda: self.store.queue_reviewed(draft_id))
        self.assertFalse(hasattr(self.store, "update_state"))

    def test_edit_resets_review_and_keeps_source_copy_immutable(self):
        draft = self.create()
        draft_id = draft["draft_id"]
        original = self.store._db.execute("SELECT source_id, original_json FROM drafts").fetchone()
        reviewed = self.store.review_draft(draft_id, expected_revision=1, request_id=str(uuid4()))
        updated = self.store.update_draft(draft_id, expected_revision=reviewed["revision"], question="Selected public question", answer="Redacted candidate", request_id=str(uuid4()))
        self.assertFalse(updated["reviewed"])
        self.assertFalse(updated["consented"])
        self.assertEqual(updated["source_id"], self.source_id)
        self.assertEqual(tuple(original), tuple(self.store._db.execute("SELECT source_id, original_json FROM drafts").fetchone()))
        self.error("revision_conflict", lambda: self.store.review_draft(draft_id, expected_revision=1, request_id=str(uuid4())))

    def test_save_retries_return_original_response(self):
        draft = self.create()
        reviewed = self.store.review_draft(draft["draft_id"], expected_revision=1, request_id=str(uuid4()))
        arguments = dict(expected_revision=reviewed["revision"], consent=True, request_id=str(uuid4()))
        saved = self.store.save_reviewed(draft["draft_id"], **arguments)
        self.assertEqual(saved, self.store.save_reviewed(draft["draft_id"], **arguments))

    def test_photos_metadata_only_never_attachments(self):
        photo = dict(width=1280, height=720, byte_count=10000)
        draft = self.create(photo_metadata=photo)
        self.assertEqual(draft["photo_metadata"], photo)
        self.assertEqual(draft["photo_status"], "preview_only")
        self.assertFalse(draft["public_photo_supported"])
        self.error("photo_attachment_unsupported", lambda: self.store.save_reviewed(draft["draft_id"], expected_revision=1, consent=True, request_id=str(uuid4()), attach_photo=True))
        for photo in ({"path": "/private/photo.jpg"}, {"image_base64": "data"}, dict(width=True, height=1, byte_count=1)):
            self.error("invalid_photo_metadata", lambda: self.create(photo_metadata=photo))

    def test_unicode_and_byte_limits(self):
        draft = self.create(question="س" * 2048, answer="界" * 5000)
        self.assertEqual(len(draft["question"]), 2048)
        for value in ("", " ", "س" * 2049, "\ud800", b"bytes"):
            self.error("invalid_text", lambda: self.create(question=value))
        self.error("invalid_text", lambda: self.create(answer="a" * 16385))

    def test_ids_revisions_and_no_source_history_access(self):
        sentinel = self.directory / "private-history"
        sentinel.write_text("original frozen history")
        before = sentinel.read_bytes()
        for source_id in ("../history", str(uuid4()).upper(), None):
            self.error("invalid_id", lambda: self.create(source_id=source_id))
        draft = self.create()
        for revision in (True, 0, -1, 2**53):
            self.error("invalid_revision", lambda: self.store.review_draft(draft["draft_id"], expected_revision=revision, request_id=str(uuid4())))
        self.assertEqual(sentinel.read_bytes(), before)

    def test_concurrent_cas_only_one_winner(self):
        draft = self.create()

        def review(_):
            try:
                self.store.review_draft(draft["draft_id"], expected_revision=1, request_id=str(uuid4()))
                return "ok"
            except KnowledgeSharingError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(review, range(2)))
        self.assertCountEqual(results, ["ok", "revision_conflict"])

    def test_crypto_failure_rolls_back_draft_and_receipt(self):
        with patch.object(self.crypto, "encrypt", side_effect=RuntimeError("private canary")):
            self.error("crypto_unavailable", self.create)
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM drafts").fetchone()[0], 0)
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM requests").fetchone()[0], 0)

    def test_locked_crypto_and_ciphertext_or_metadata_tamper_fail_closed(self):
        draft = self.create()
        with patch.object(self.crypto, "decrypt", side_effect=RuntimeError("locked")):
            self.error("crypto_unavailable", lambda: self.store.get(draft["draft_id"]))
        self.store._db.execute("UPDATE drafts SET consented=1")
        self.error("crypto_unavailable", lambda: self.store.get(draft["draft_id"]))

    def test_wrong_key_and_payload_swap_rejected(self):
        first, second = self.create(), self.create()
        payload = self.store._db.execute("SELECT payload FROM drafts WHERE draft_id=?", (first["draft_id"],)).fetchone()[0]
        self.store._db.execute("UPDATE drafts SET payload=? WHERE draft_id=?", (payload, second["draft_id"]))
        self.error("crypto_unavailable", lambda: self.store.get(second["draft_id"]))
        self.store.close()
        self.error("store_unavailable", lambda: KnowledgeSharingStore(directory=self.directory, crypto=Crypto()))

    def test_capacity_and_no_idempotency_eviction(self):
        request_id = str(uuid4())
        draft = self.create(request_id=request_id)
        with patch("sage_lantern.knowledge_sharing.MAX_REQUESTS", 1):
            self.error("capacity_exceeded", self.create)
            self.assertEqual(self.create(request_id=request_id), draft)
        with patch("sage_lantern.knowledge_sharing.MAX_DRAFTS", 1):
            self.error("capacity_exceeded", self.create)

    def test_unsafe_modes_links_and_second_owner(self):
        self.error("store_unavailable", lambda: KnowledgeSharingStore(directory=self.directory, crypto=self.crypto))
        self.store.close()
        database = self.directory / DATABASE
        database.chmod(0o644)
        self.error("store_unavailable", lambda: KnowledgeSharingStore(directory=self.directory, crypto=self.crypto))
        database.chmod(0o600)
        os.link(database, self.directory / "hardlink")
        self.error("store_unavailable", lambda: KnowledgeSharingStore(directory=self.directory, crypto=self.crypto))
        (self.directory / "hardlink").unlink()
        database.rename(self.directory / "real")
        database.symlink_to(self.directory / "real")
        self.error("store_unavailable", lambda: KnowledgeSharingStore(directory=self.directory, crypto=self.crypto))

    def test_uncertain_fsync_retry_is_idempotent(self):
        request_id = str(uuid4())
        with patch("sage_lantern.knowledge_sharing.os.fsync", side_effect=OSError("disk")):
            self.error("store_unavailable", lambda: self.create(request_id=request_id))
        draft = self.create(request_id=request_id)
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)
        self.assertEqual(self.create(request_id=request_id), draft)


if __name__ == "__main__":
    unittest.main()
