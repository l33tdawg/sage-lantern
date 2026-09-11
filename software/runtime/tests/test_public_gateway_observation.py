"""Synthetic gateway keys and replay doubles only; not production source/proof tests."""

from dataclasses import FrozenInstanceError, replace
import json
import unittest
from uuid import uuid4

from nacl.signing import SigningKey

from sage_lantern.community_public import CommunityPublic, PublicObservation, PublicReceipt
from sage_lantern.public_gateway_observation import (
    CanonicalGatewayEvidence, CanonicalObservationSource, GatewayExporter, GatewayObservationError,
    GatewayPolicy, GatewayReplayStore, GatewayRequest, GatewayVerifier, SIGN_DOMAIN,
)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


class SyntheticSource(CanonicalObservationSource):
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = 0

    def fetch(self, request, *, access_check):
        self.calls += 1
        if access_check() is not True:
            raise RuntimeError("synthetic denied")
        return self.evidence


class ReplayDouble(GatewayReplayStore):
    def __init__(self):
        self.nonces = set()
        self.records = {}
        self.available = True

    def consume(self, checkpoint):
        if not self.available:
            raise RuntimeError("synthetic unavailable")
        nonce = (checkpoint.gateway_key, checkpoint.recipient, checkpoint.request_id)
        key = (checkpoint.gateway_key, checkpoint.chain_id, checkpoint.record_id)
        previous = self.records.get(key)
        if nonce in self.nonces:
            return False
        if previous is not None and (
            checkpoint.binding_digest != previous.binding_digest
            or checkpoint.status_revision < previous.status_revision
            or checkpoint.observed_height < previous.observed_height
            or checkpoint.observed_at < previous.observed_at
            or checkpoint.status_revision == previous.status_revision and checkpoint.status != previous.status
        ):
            return False
        self.nonces.add(nonce)
        self.records[key] = checkpoint
        return True


class GatewayCodecTests(unittest.TestCase):
    def setUp(self):
        self.key = SigningKey(bytes(range(32)))
        self.now = 100
        self.policy = GatewayPolicy(frozenset({self.key.verify_key.encode().hex()}), "public-chain",
                                    frozenset({"commons"}), frozenset({"generated-unverified"}), 60)
        self.proposal = CommunityPublic(None, None, chain_id="public-chain", domain="commons", author="a" * 64).prepare(
            operation_id=str(uuid4()), question="Why blue?", answer="Scattered light.")
        self.receipt = PublicReceipt(self.proposal.operation_id, self.proposal.content_hash, "public-chain",
                                     str(uuid4()), "b" * 64, 10)
        self.observation = PublicObservation(state="accepted", reason="online_node_observation", observed_status="committed",
            receipt=self.receipt, content_bound=True, chain_verified=True, acceptance_available=True, trust_basis="trusted_node_online")
        self.source = SyntheticSource(CanonicalGatewayEvidence(self.proposal, self.observation, 7, 20, 100))
        self.request = GatewayRequest("public-chain", self.receipt.memory_id, self.proposal.content_hash, "a" * 64,
                                      "commons", "generated-unverified", str(uuid4()), "c" * 64)
        self.replay = ReplayDouble()
        self.exporter = GatewayExporter(self.key, self.source, policy=self.policy, clock=lambda: self.now, ttl_seconds=30)
        self.verifier = GatewayVerifier(policy=self.policy, clock=lambda: self.now, replay_store=self.replay)

    def bundle(self, request=None):
        return self.exporter.export(self.request if request is None else request, access_check=lambda: True)

    def signed_changes(self, bundle, **changes):
        envelope = json.loads(bundle)
        envelope["body"].update(changes)
        envelope["signature"] = self.key.sign(SIGN_DOMAIN + canonical(envelope["body"])).signature.hex()
        return canonical(envelope)

    def test_roundtrip_is_gateway_metadata_never_consensus_truth_or_cache(self):
        bundle = self.bundle()
        result = self.verifier.verify(bundle, expected_request=self.request)
        self.assertEqual(result.trust_basis, "pinned_gateway")
        self.assertEqual(result.status, "committed")
        self.assertTrue(result.fresh)
        for field in ("offline_proof", "consensus_verified", "acceptance_available", "factual_verified", "cache_eligible"):
            self.assertIs(getattr(result, field), False)
        with self.assertRaises(FrozenInstanceError):
            result.cache_eligible = True
        with self.assertRaises(ValueError):
            replace(result, consensus_verified=True)
        self.assertNotIn(b"Scattered light", bundle)
        self.assertNotIn(b"Why blue", bundle)
        self.assertNotIn(b"proof", bundle)
        self.assertEqual(self.source.calls, 1)

    def test_raw_observation_json_or_unverified_shape_cannot_be_exported(self):
        evidence = self.source.evidence
        for value in ({"observation": "arbitrary"}, replace(evidence, observation=PublicObservation(receipt=self.receipt)),
                      replace(evidence, status_revision=None), replace(evidence, status_revision=True),
                      replace(evidence, observed_height=1), replace(evidence, observed_at=99)):
            self.source.evidence = value
            with self.subTest(value=type(value).__name__), self.assertRaises(GatewayObservationError):
                self.bundle()
        with self.assertRaises(GatewayObservationError):
            GatewayExporter(self.key, lambda request: evidence, policy=self.policy, clock=lambda: self.now, ttl_seconds=30)

    def test_export_requires_exact_validated_canonical_observation_flags(self):
        original = self.source.evidence
        for field, value in (("content_bound", False), ("chain_verified", False), ("acceptance_available", False),
                             ("trust_basis", "unverified"), ("reason", "submitted_unverified"),
                             ("offline_proof", True), ("cache_eligible", True), ("state", "pending")):
            self.source.evidence = replace(original, observation=replace(self.observation, **{field: value}))
            with self.subTest(field=field), self.assertRaises(GatewayObservationError):
                self.bundle()

    def test_request_nonce_recipient_and_all_content_bindings_mandatory(self):
        bundle = self.bundle()
        for field, value in (("request_id", str(uuid4())), ("recipient", "d" * 64), ("record_id", str(uuid4())),
                             ("content_digest", "e" * 64), ("author", "f" * 64), ("domain", "other"),
                             ("source_class", "human-verified"), ("chain_id", "other")):
            with self.subTest(field=field), self.assertRaises(GatewayObservationError):
                self.verifier.verify(bundle, expected_request=replace(self.request, **{field: value}))
        self.assertEqual(self.replay.nonces, set())
        with self.assertRaises(GatewayObservationError):
            self.bundle(replace(self.request, request_id="00000000-0000-1000-8000-000000000001"))

    def test_all_lifecycle_statuses_remain_non_consensus_and_non_cache(self):
        for status, state in (("proposed", "pending"), ("validated", "pending"), ("committed", "accepted"),
                              ("challenged", "challenged"), ("deprecated", "deprecated")):
            self.setUp()
            self.source.evidence = replace(self.source.evidence, observation=replace(self.observation,
                observed_status=status, state=state))
            with self.subTest(status=status):
                result = self.verifier.verify(self.bundle(), expected_request=self.request)
                self.assertEqual(result.status, status)
                self.assertFalse(result.acceptance_available)
                self.assertFalse(result.consensus_verified)
                self.assertFalse(result.cache_eligible)

    def test_pinning_revocation_and_missing_replay_store_fail_closed(self):
        other = SigningKey(bytes(reversed(range(32)))).verify_key.encode().hex()
        verifier = GatewayVerifier(policy=replace(self.policy, gateway_keys=frozenset({other})), clock=lambda: self.now, replay_store=self.replay)
        with self.assertRaises(GatewayObservationError):
            verifier.verify(self.bundle(), expected_request=self.request)
        for policy in (replace(self.policy, gateway_keys=frozenset()), replace(self.policy, domains=frozenset()),
                       replace(self.policy, source_classes=frozenset()), replace(self.policy, max_ttl_seconds=301)):
            with self.assertRaises(GatewayObservationError):
                GatewayVerifier(policy=policy, clock=lambda: self.now, replay_store=self.replay)
        with self.assertRaises(GatewayObservationError):
            GatewayVerifier(policy=self.policy, clock=lambda: self.now, replay_store=None)

    def test_signature_tampering_and_signed_invalid_shapes_rejected(self):
        bundle = self.bundle()
        tampered = json.loads(bundle)
        tampered["body"]["status"] = "deprecated"
        with self.assertRaises(GatewayObservationError):
            self.verifier.verify(canonical(tampered), expected_request=self.request)
        for changes in ({"classification": 1}, {"classification": False}, {"status_revision": True},
                        {"observed_height": 0}, {"status": "accepted"}, {"extra": "unreviewed"},
                        {"valid_until": 100}, {"valid_until": 161}, {"observed_at": 101}):
            with self.subTest(changes=changes), self.assertRaises(GatewayObservationError):
                self.verifier.verify(self.signed_changes(bundle, **changes), expected_request=self.request)
        self.assertEqual(self.replay.nonces, set())

    def test_noncanonical_duplicate_oversize_and_wrong_signature_domain_rejected(self):
        bundle = self.bundle()
        parsed = json.loads(bundle)
        wrong_domain = {**parsed, "signature": self.key.sign(canonical(parsed["body"])).signature.hex()}
        duplicate = bundle.replace(b'"classification":0', b'"classification":0,"classification":0')
        for invalid in (bundle + b"\n", json.dumps(parsed, indent=2).encode(), duplicate, b"x" * 4097, canonical(wrong_domain)):
            with self.subTest(length=len(invalid)), self.assertRaises(GatewayObservationError):
                self.verifier.verify(invalid, expected_request=self.request)

    def test_expired_is_rejected_or_explicit_historical_never_current(self):
        bundle = self.bundle()
        self.now = 130
        with self.assertRaises(GatewayObservationError):
            self.verifier.verify(bundle, expected_request=self.request)
        self.assertEqual(self.replay.nonces, set())
        result = self.verifier.verify(bundle, expected_request=self.request, allow_expired_history=True)
        self.assertFalse(result.fresh)
        self.assertFalse(result.current_lifecycle_available)
        self.assertFalse(result.cache_eligible)
        self.assertFalse(result.acceptance_available)

    def test_replay_survives_verifier_recreation_and_new_nonce_cannot_rollback(self):
        bundle = self.bundle()
        self.verifier.verify(bundle, expected_request=self.request)
        reopened = GatewayVerifier(policy=self.policy, clock=lambda: self.now, replay_store=self.replay)
        with self.assertRaises(GatewayObservationError):
            reopened.verify(bundle, expected_request=self.request)
        new = replace(self.request, request_id=str(uuid4()))
        changed = self.signed_changes(self.bundle(new), status="challenged", status_revision=8)
        result = reopened.verify(changed, expected_request=new)
        self.assertEqual(result.status, "challenged")
        new = replace(new, request_id=str(uuid4()), recipient="d" * 64)
        with self.assertRaises(GatewayObservationError):
            reopened.verify(self.bundle(new), expected_request=new)

    def test_same_revision_conflict_and_height_time_rollback_rejected(self):
        self.verifier.verify(self.bundle(), expected_request=self.request)
        for changes in ({"status": "challenged"}, {"observed_height": 19}, {"observed_at": 99}):
            new = replace(self.request, request_id=str(uuid4()))
            with self.subTest(changes=changes), self.assertRaises(GatewayObservationError):
                self.verifier.verify(self.signed_changes(self.bundle(new), **changes), expected_request=new)

    def test_guard_revocation_and_replay_storage_unavailable(self):
        checks = iter([True, False])
        with self.assertRaises(GatewayObservationError):
            self.exporter.export(self.request, access_check=lambda: next(checks))
        self.replay.available = False
        with self.assertRaises(GatewayObservationError):
            self.verifier.verify(self.bundle(), expected_request=self.request)

    def test_expiry_during_replay_commit_never_returns_current_evidence(self):
        bundle = self.bundle()
        original = self.replay.consume
        def consume(checkpoint):
            result = original(checkpoint)
            self.now = 130
            return result
        self.replay.consume = consume
        with self.assertRaises(GatewayObservationError):
            self.verifier.verify(bundle, expected_request=self.request)
        self.assertEqual(len(self.replay.nonces), 1)


if __name__ == "__main__":
    unittest.main()
