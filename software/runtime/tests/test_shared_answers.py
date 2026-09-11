import copy
import hashlib
import unittest
from dataclasses import replace

from nacl.signing import SigningKey

from sage_lantern.shared_answers import (
    SCHEMA,
    PinnedReviewTrust,
    SharedAnswerCache,
    SharedAnswerError,
    VerifiedSharedAnswer,
    canonical_attestation,
)


class FakeReplayGuard:
    def __init__(self):
        self.watermarks = {}
        self.revoked = set()

    def advance(self, identity, revision, digest):
        if identity in self.revoked or revision <= self.watermarks.get(identity, (0, ""))[0]:
            return False
        self.watermarks[identity] = (revision, digest)
        return True

    def current(self, identity, revision, digest):
        return identity not in self.revoked and self.watermarks.get(identity) == (revision, digest)

    def revoke(self, identity):
        self.revoked.add(identity)


class SharedAnswerTests(unittest.TestCase):
    def setUp(self):
        self.signer = SigningKey(bytes(range(32)))
        self.trust = PinnedReviewTrust(
            reviewer_id="test-reviewer", public_key=bytes(self.signer.verify_key),
            chain_id="test-commons", domains=frozenset({"public-answers"}),
            review_policy="review-v1", licenses=frozenset({"CC0-1.0"}), max_age_seconds=300,
        )
        self.guard = FakeReplayGuard()
        self.now = 1000
        self.cache = self.make_cache()
        self.value = {
            "schema": SCHEMA, "chain_id": "test-commons", "record_id": "answer-1",
            "revision": 1, "domain": "public-answers", "classification": 0,
            "status": "committed", "committed_height": 42, "verified_at": 990,
            "expires_at": 1100, "question": "What is café?", "answer": "A café is a coffeehouse.",
            "language": "en-GB", "scope": "context-free-public", "modality": "text",
            "challenged": False, "superseded": False, "license": "CC0-1.0",
            "provenance": {"reviewer_id": "test-reviewer", "review_policy": "review-v1",
                           "decision": "verified-reusable",
                           "sources": [{"uri": "https://example.org/reference",
                                        "sha256": hashlib.sha256(b"synthetic source").hexdigest()}]},
        }

    def make_cache(self, **kwargs):
        return SharedAnswerCache(trust=self.trust, replay_guard=self.guard,
                                 clock=lambda: self.now, **kwargs)

    def ingest(self, value=None, cache=None):
        payload = canonical_attestation(self.value if value is None else value)
        return (self.cache if cache is None else cache).ingest_verified(
            payload, self.signer.sign(payload).signature)

    def lookup(self, query="What is café?", language="en-GB", **kwargs):
        return self.cache.lookup(query, language, standalone=True, personal=False, **kwargs)

    def test_disabled_without_both_trust_and_guard(self):
        for cache in (SharedAnswerCache(), SharedAnswerCache(trust=self.trust),
                      SharedAnswerCache(replay_guard=self.guard)):
            with self.subTest(cache=cache):
                self.assertFalse(cache.enabled)
                self.assertFalse(cache.ingest_verified(b"untrusted", b""))
                self.assertIsNone(cache.lookup("question", "en", standalone=True, personal=False))

    def test_verified_result_has_public_ui_provenance(self):
        self.assertTrue(self.ingest())
        answer = self.lookup()
        self.assertIsInstance(answer, VerifiedSharedAnswer)
        self.assertEqual(answer.source, "public-cached")
        self.assertEqual(answer.chain_id, self.trust.chain_id)
        self.assertEqual(answer.committed_height, 42)
        self.assertEqual(answer.reviewer_id, self.trust.reviewer_id)
        self.assertEqual(answer.review_policy, "review-v1")
        self.assertEqual(answer.sources[0][0], "https://example.org/reference")
        self.assertEqual(answer.expires_at, 1100)

    def test_exact_normalization_only(self):
        self.ingest()
        self.assertIsNotNone(self.lookup("  What  is cafe\u0301?\n", "EN-gb"))
        for query, language in (("what is café?", "en-GB"), ("What is café", "en-GB"),
                                ("What is café?", "en"), ("What is tea?", "en-GB"),
                                ("What is café?", "fr"), ("What is café 2?", "en-GB")):
            with self.subTest(query=query, language=language):
                self.assertIsNone(self.lookup(query, language))

    def test_context_admission_is_explicit_and_never_cross_photo(self):
        self.ingest()
        self.assertIsNone(self.cache.lookup("What is café?", "en-GB"))
        for standalone, personal in ((False, False), (True, True), (1, False), (True, 0)):
            self.assertIsNone(self.cache.lookup("What is café?", "en-GB",
                                               standalone=standalone, personal=personal))
        for photo in ("digest-a", "digest-b", "", False):
            self.assertIsNone(self.lookup(photo_digest=photo))

    def test_wrong_signature_tamper_and_outbox_are_not_proof(self):
        payload = canonical_attestation(self.value)
        signature = self.signer.sign(payload).signature
        other = SigningKey(bytes(reversed(range(32))))
        for data, proof in ((payload, other.sign(payload).signature),
                            (payload.replace(b"coffeehouse", b"privatehome"), signature),
                            (payload, b"x" * 64), (self.value, signature), (payload, b"")):
            with self.subTest(data_type=type(data), proof_length=len(proof)):
                with self.assertRaises(SharedAnswerError):
                    self.cache.ingest_verified(data, proof)
        self.assertEqual(self.guard.watermarks, {})

    def test_signed_noncanonical_duplicate_or_nonobject_rejected(self):
        for payload in (b'{"schema":"a","schema":"b"}', b'{ "status": "committed" }', b'[]'):
            with self.assertRaises(SharedAnswerError):
                self.cache.ingest_verified(payload, self.signer.sign(payload).signature)
        self.assertEqual(self.guard.watermarks, {})

    def test_ineligible_new_revision_invalidates_old(self):
        changes = [("classification", 1), ("classification", False), ("status", "accepted"),
                   ("status", "proposed"), ("status", "deprecated"), ("status", "challenged"),
                   ("scope", "private-personal"), ("modality", "photo"), ("challenged", True),
                   ("superseded", True), ("committed_height", 0), ("expires_at", 1000),
                   ("expires_at", 2000), ("provenance", {}), ("license", "unknown"),
                   ("question", ""), ("answer", "x" * 8193), ("language", ""),
                   ("classification", None)]
        for field, value in changes:
            with self.subTest(field=field, value_type=type(value)):
                self.guard = FakeReplayGuard()
                self.cache = self.make_cache()
                self.ingest()
                revised = dict(self.value, revision=2)
                revised[field] = value
                self.assertFalse(self.ingest(revised))
                self.assertIsNone(self.lookup())
                self.assertFalse(self.ingest())

    def test_incomplete_or_unreviewed_provenance_misses(self):
        for field, value in (("decision", "accepted"), ("reviewer_id", "publisher"),
                             ("review_policy", "shape-only"), ("sources", []),
                             ("sources", [{"uri": "https://example.org/?private=secret", "sha256": "a" * 64}]),
                             ("sources", [{"uri": "file:///private/history", "sha256": "a" * 64}]),
                             ("sources", [{"uri": "https://example.org", "sha256": "missing"}])):
            with self.subTest(field=field):
                self.guard = FakeReplayGuard()
                revised = copy.deepcopy(self.value)
                revised["provenance"][field] = value
                self.assertFalse(self.ingest(revised, self.make_cache()))
                self.assertEqual(self.guard.watermarks[("test-commons", "answer-1")][0], 1)
        self.assertIsNone(self.lookup())

    def test_missing_fields_and_ambient_metadata_never_qualify(self):
        for field in self.value:
            with self.subTest(field=field):
                self.guard = FakeReplayGuard()
                cache = self.make_cache()
                incomplete = dict(self.value)
                del incomplete[field]
                try:
                    self.assertFalse(self.ingest(incomplete, cache))
                except SharedAnswerError:
                    pass
                self.assertIsNone(cache.lookup("What is café?", "en-GB",
                                               standalone=True, personal=False))
        self.guard = FakeReplayGuard()
        self.assertFalse(self.ingest(dict(self.value, private_context_hash="forbidden"), self.make_cache()))

    def test_payload_budget_bounds_cache(self):
        self.cache = self.make_cache(max_bytes=16384)
        self.ingest(dict(self.value, answer="a" * 8000))
        self.ingest(dict(self.value, record_id="answer-2", answer="b" * 8000))
        self.assertEqual(len(self.cache._entries), 1)
        self.assertFalse(self.ingest(dict(self.value, answer="a" * 8000)))

    def test_nonboolean_replay_guard_fails_closed(self):
        self.guard.advance = lambda *arguments: "true"
        with self.assertRaises(SharedAnswerError):
            self.ingest()
        self.assertFalse(self.cache.enabled)

    def test_scope_mismatch_cannot_mutate_watermarks(self):
        for field, value in (("chain_id", "private-chain"), ("domain", "personal"),
                             ("schema", "outbox-v3"), ("verified_at", 1001),
                             ("revision", True), ("record_id", "")):
            with self.subTest(field=field):
                with self.assertRaises(SharedAnswerError):
                    self.ingest(dict(self.value, **{field: value}))
        self.assertEqual(self.guard.watermarks, {})

    def test_expiry_and_clock_rollback_fail_closed(self):
        self.ingest()
        self.now = 1100
        self.assertIsNone(self.lookup())
        self.now = 1000
        self.assertIsNone(self.lookup())
        self.assertFalse(self.cache.enabled)

    def test_replay_conflict_and_restart_cannot_resurrect(self):
        self.ingest()
        self.assertFalse(self.ingest())
        self.assertFalse(self.ingest(dict(self.value, answer="conflicting answer")))
        self.assertFalse(self.ingest(cache=self.make_cache()))
        self.assertTrue(self.ingest(dict(self.value, revision=2)))
        self.assertFalse(self.ingest())
        self.cache.invalidate("answer-1")
        self.assertIsNone(self.lookup())
        self.assertFalse(self.ingest(dict(self.value, revision=3), self.make_cache()))

    def test_invalidation_before_ingestion_is_permanent(self):
        self.cache.invalidate("answer-1")
        self.assertFalse(self.ingest())

    def test_eviction_preserves_watermarks(self):
        self.cache = self.make_cache(max_entries=1)
        self.ingest()
        self.ingest(dict(self.value, record_id="answer-2", question="Other question?"))
        self.assertIsNone(self.lookup())
        self.assertFalse(self.ingest())
        self.assertEqual(len(self.cache._entries), 1)

    def test_ambiguous_answers_miss(self):
        self.ingest()
        self.ingest(dict(self.value, record_id="answer-2"))
        self.assertIsNone(self.lookup())

    def test_other_cache_revision_invalidates_local_entry(self):
        self.ingest()
        self.ingest(dict(self.value, revision=2), self.make_cache())
        self.assertIsNone(self.lookup())

    def test_storage_failure_disables_reuse(self):
        self.ingest()
        def fail(*arguments):
            raise OSError("synthetic storage failure")
        self.guard.current = fail
        self.assertIsNone(self.lookup())
        self.assertFalse(self.cache.enabled)

    def test_cache_bytes_and_identity_are_reverified(self):
        self.ingest()
        identity = ("test-commons", "answer-1")
        original = self.cache._entries[identity]
        self.cache._entries[identity] = replace(original, signature=b"x" * 64)
        self.assertIsNone(self.lookup())
        other = canonical_attestation(dict(self.value, record_id="answer-2"))
        self.cache._entries[identity] = replace(original, payload=other,
                                                signature=self.signer.sign(other).signature)
        self.assertIsNone(self.lookup())

    def test_invalid_bounds_and_trust_rejected(self):
        for kwargs in ({"max_entries": 0}, {"max_entries": True}, {"max_bytes": 1}):
            with self.assertRaises(SharedAnswerError):
                self.make_cache(**kwargs)
        with self.assertRaises(SharedAnswerError):
            SharedAnswerCache(trust=replace(self.trust, public_key=b""), replay_guard=self.guard)


if __name__ == "__main__":
    unittest.main()
