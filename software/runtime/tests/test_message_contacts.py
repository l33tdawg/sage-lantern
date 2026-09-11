import threading
import unittest
from dataclasses import FrozenInstanceError, replace

from sage_lantern.message_contacts import (
    MAX_EVIDENCE_BYTES, ContactClaim, ContactError, RecipientCache, RecipientID,
)


class RecipientCacheTests(unittest.TestCase):
    def setUp(self):
        self.caller = RecipientID("personal-a", "a" * 64)
        self.recipient = RecipientID("personal-b", "b" * 64)
        self.now = 100
        self.claim = ContactClaim(self.caller, self.recipient, "Recipient", "peer-policy",
                                  "generation-1", 1, 90, 200, True, True)

    def cache(self, **overrides):
        options = dict(caller=self.caller, issuer="peer-policy", policy_binding="generation-1",
                       clock=lambda: self.now, verifier=lambda evidence: self.claim)
        options.update(overrides)
        return RecipientCache(**options)

    def test_verified_round_trip_is_immutable_and_has_no_route(self):
        cache = self.cache()
        self.assertEqual(cache.put(b"verified by injected adapter"), self.claim)
        self.assertEqual(cache.get(self.recipient), self.claim)
        with self.assertRaises(FrozenInstanceError):
            self.claim.display_name = "new"
        self.assertFalse(hasattr(self.claim, "route"))
        self.assertFalse(hasattr(self.claim, "radio_address"))

    def test_absent_or_shape_only_verifier_denies(self):
        for verifier in (None, lambda evidence: True,
                         lambda evidence: {"signature": "looks-signed"}, lambda evidence: None):
            with self.subTest(verifier=verifier), self.assertRaises(ContactError):
                self.cache(verifier=verifier).put(b"proof")

    def test_errors_do_not_leak_evidence(self):
        def broken(evidence):
            raise ValueError(evidence.decode())
        with self.assertRaises(ContactError) as result:
            self.cache(verifier=broken).put(b"private recipient proof")
        self.assertEqual(str(result.exception), "recipient_unavailable")
        self.assertTrue(result.exception.__suppress_context__)

    def test_strict_identity(self):
        for chain, agent in (("", "a" * 64), ("bad/chain", "a" * 64),
                             ("chain", "A" * 64), ("chain", "a" * 63),
                             (True, "a" * 64), ("chain", 123)):
            with self.subTest(chain=chain, agent=agent), self.assertRaises(ContactError):
                RecipientID(chain, agent)

    def test_names_never_select_identity_and_chains_are_distinct(self):
        cache = self.cache()
        cache.put(b"proof")
        with self.assertRaises(ContactError):
            cache.get("Recipient")
        other = RecipientID("other-chain", self.recipient.agent_id)
        with self.assertRaises(ContactError):
            cache.get(other)
        self.claim = replace(self.claim, recipient=other)
        cache.put(b"other proof")
        self.assertEqual(cache.get(other).display_name, cache.get(self.recipient).display_name)

    def test_wrong_caller_issuer_or_policy_denied(self):
        for changes in ({"caller": self.recipient}, {"issuer": "another-peer"},
                        {"policy_binding": "old-generation"}):
            with self.subTest(changes=changes):
                claim = replace(self.claim, **changes)
                with self.assertRaises(ContactError):
                    self.cache(verifier=lambda evidence: claim).put(b"proof")

    def test_expiry_and_age_rechecked_on_every_read(self):
        for deadline in (110, 200):
            self.now = 100
            self.claim = replace(self.claim, expires_at=deadline)
            cache = self.cache(max_age_seconds=20)
            cache.put(b"proof")
            self.now = 110
            with self.assertRaises(ContactError):
                cache.get(self.recipient)

    def test_future_and_stale_evidence_denied(self):
        for issued, expiry in ((101, 200), (1, 100), (1, 200)):
            claim = replace(self.claim, issued_at=issued, expires_at=expiry)
            with self.assertRaises(ContactError):
                self.cache(verifier=lambda evidence: claim).put(b"proof")

    def test_consent_denial_invalidates_previous_entry(self):
        for field in ("findable", "messaging_allowed"):
            self.setUp()
            cache = self.cache()
            cache.put(b"proof")
            self.claim = replace(self.claim, revision=2, **{field: False})
            with self.assertRaises(ContactError):
                cache.put(b"verified denial")
            with self.assertRaises(ContactError):
                cache.get(self.recipient)
            self.claim = replace(self.claim, revision=3, findable=True, messaging_allowed=True)
            with self.assertRaises(ContactError):
                cache.put(b"cannot silently undo revocation")

    def test_revision_rollback_and_equivocation_denied(self):
        cache = self.cache()
        cache.put(b"proof")
        self.assertEqual(cache.put(b"same authenticated claim"), self.claim)
        for changes in ({"revision": 0}, {"display_name": "different name"}):
            original = self.claim
            self.claim = replace(self.claim, **changes)
            with self.assertRaises(ContactError):
                cache.put(b"proof")
            self.assertEqual(cache.get(self.recipient), original)
            self.claim = original
        self.claim = replace(self.claim, revision=2, display_name="Renamed")
        cache.put(b"new authenticated revision")
        self.assertEqual(cache.get(self.recipient).display_name, "Renamed")

    def test_revocation_during_verification_cannot_repopulate(self):
        entered = threading.Event()
        release = threading.Event()
        results = []

        def verifier(evidence):
            entered.set()
            if not release.wait(2):
                raise ValueError
            return self.claim

        cache = self.cache(verifier=verifier)

        def insert():
            try:
                cache.put(b"proof")
            except ContactError:
                results.append("denied")

        worker = threading.Thread(target=insert)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            cache.revoke(self.recipient)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, ["denied"])

    def test_clock_failure_latches_closed(self):
        for value in (99, float("nan"), float("inf"), True):
            self.now = 100
            cache = self.cache()
            cache.put(b"proof")
            self.now = value
            with self.assertRaises(ContactError):
                cache.get(self.recipient)
            self.now = 101
            with self.assertRaises(ContactError):
                cache.put(b"proof")

    def test_clear_hides_and_preserves_revocations(self):
        cache = self.cache()
        cache.put(b"proof")
        cache.clear()
        with self.assertRaises(ContactError):
            cache.get(self.recipient)
        cache.put(b"fresh verification")
        cache.revoke(self.recipient)
        cache.clear()
        with self.assertRaises(ContactError):
            cache.put(b"proof")

    def test_capacity_includes_revocations(self):
        cache = self.cache(capacity=1)
        cache.revoke(self.recipient)
        self.claim = replace(self.claim, recipient=RecipientID("other", "c" * 64))
        with self.assertRaises(ContactError):
            cache.put(b"proof")
        with self.assertRaises(ContactError):
            cache.revoke(self.claim.recipient)

    def test_bounds_and_strict_boolean_revisions(self):
        for evidence in (b"", b"x" * (MAX_EVIDENCE_BYTES + 1), "proof", {}):
            with self.assertRaises(ContactError):
                self.cache().put(evidence)
        for changes in ({"revision": True}, {"issued_at": True}, {"findable": 1},
                        {"messaging_allowed": "true"}, {"display_name": "bad\nname"}):
            with self.assertRaises(ContactError):
                replace(self.claim, **changes)
        for changes in ({"capacity": True}, {"capacity": 257}, {"max_age_seconds": 0}):
            with self.assertRaises(ContactError):
                self.cache(**changes)

    def test_restart_has_no_contacts(self):
        cache = self.cache()
        cache.put(b"proof")
        with self.assertRaises(ContactError):
            self.cache().get(self.recipient)


if __name__ == "__main__":
    unittest.main()
