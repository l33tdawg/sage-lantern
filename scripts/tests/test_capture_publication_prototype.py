"""Synthetic protocol qualification. No filesystem backend or real child exists.

The shared in-memory anchor survives Coordinator replacement, not power loss.
The deliberately rollbackable-anchor control states that remaining limitation.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "software/security/desktop_loader_candidate/capture_publication_prototype.py"
SPEC = importlib.util.spec_from_file_location("capture_publication_prototype_tested", SOURCE)
p = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = p
SPEC.loader.exec_module(p)

MACHINE = "a" * 32
OTHER_MACHINE = "b" * 32
BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_BOOT = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
CAPTURE = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
OTHER_CAPTURE = "dddddddd-eeee-ffff-aaaa-bbbbbbbbbbbb"
LAUNCH = "eeeeeeee-ffff-aaaa-bbbb-cccccccccccc"
OTHER_LAUNCH = "ffffffff-aaaa-bbbb-cccc-dddddddddddd"
IDENTITY = p.Identity(MACHINE, BOOT)


def current(generation=1, **changes):
    value = dict(schema=p.CURRENT_SCHEMA, state="active", machine_id=MACHINE,
                 boot_id=BOOT, capture_id=CAPTURE, generation=generation,
                 policy_path="/protected/capture/policy.json", policy_sha256="1" * 64)
    value.update(changes)
    return p.canonical(value)


class SimulatedCrash(BaseException):
    """Execution stops without returning a protocol outcome."""


@dataclass(frozen=True)
class Fault:
    method: str
    edge: str
    occurrence: int
    kind: type


class SyntheticBackend:
    """Explicitly provisioned, truthful test ports with one shared thread gate.

    This is not a storage/authentication adapter. Test code can corrupt any
    state. That capability supplies counterexamples, not production recovery.
    """
    def __init__(self):
        self.platform = IDENTITY
        self.anchor = p.Anchor(MACHINE, 0, 0, "closed")
        self.current_bytes = None
        self.staged = {}
        self.releases = []
        self.counts = Counter()
        self.events = []
        self.fault = None
        self.overrides = {}
        self.hooks = {}
        self.gate = threading.Lock()
        self.owner = None
        self.waiting_event = None

    @contextmanager
    def transaction(self):
        tid = threading.get_ident()
        if self.owner == tid:
            raise p.ProtocolRefused("nonreentrant_authority")
        if self.waiting_event is not None:
            self.waiting_event.set()
        if not self.gate.acquire(timeout=5):
            raise p.BackendUnavailable("synthetic_gate_timeout")
        self.owner = tid
        try:
            yield
        finally:
            self.owner = None
            self.gate.release()

    def arm(self, method, edge, kind, occurrence=1):
        self.counts.clear()
        self.events.clear()
        self.fault = Fault(method, edge, occurrence, kind)

    def point(self, method, edge):
        if self.owner != threading.get_ident():
            raise AssertionError("port escaped the shared authority transaction")
        phase = self.anchor.phase if type(self.anchor) is p.Anchor else None
        self.events.append((method, edge, phase))
        hook = self.hooks.get((method, edge))
        if hook is not None:
            hook()
        fault = self.fault
        if (fault is not None and (method, edge, self.counts[method]) ==
                (fault.method, fault.edge, fault.occurrence)):
            self.fault = None
            raise fault.kind(f"injected:{method}:{edge}:{fault.occurrence}")

    def call(self, method, action):
        self.counts[method] += 1
        self.point(method, "before")
        value = action()
        override = self.overrides.get(method)
        if override is not None:
            value = override(value)
        self.point(method, "after")
        return value

    def identity(self):
        return self.call("identity", lambda: self.platform)

    def read_anchor(self):
        return self.call("read_anchor", lambda: self.anchor)

    def compare_exchange(self, before, after):
        def action():
            if self.anchor != before:
                return None
            self.anchor = after
            return after
        return self.call("compare_exchange", action)

    def stage_durable(self, record):
        def action():
            self.staged[record.sha256] = record
            return p.StageReceipt(record.sha256, record.policy_sha256,
                                  record.capture_id, record.generation)
        return self.call("stage_durable", action)

    def publish_current_durable(self, data):
        def action():
            self.current_bytes = data
            return hashlib.sha256(data).hexdigest()
        return self.call("publish_current_durable", action)

    def read_current(self):
        return self.call("read_current", lambda: self.current_bytes)

    def withdraw_current_durable(self):
        def action():
            self.current_bytes = None
        return self.call("withdraw_current_durable", action)

    def verify_release_ready(self, selection, launch_id):
        return self.call("verify_release_ready", lambda: p.ChildReceipt(launch_id, selection))

    def release_stopped_child(self, child):
        def action():
            # Independent event witness: the attempt marker precedes the callback
            # and the same gate still owns it. There is no actual child/PID here.
            if (self.anchor.phase != "release_attempted"
                    or self.anchor.launch_id != child.launch_id):
                raise AssertionError("release happened before the durable attempt marker")
            self.releases.append(child.launch_id)
            return p.ReleaseReceipt(child.launch_id, True)
        return self.call("release_stopped_child", action)


class CapturePublicationTests(unittest.TestCase):
    def active(self, generation=1):
        backend = SyntheticBackend()
        coordinator = p.Coordinator(backend)
        # Larger generations are reached by monotonic publications, never seeded
        # by modifying the closed anchor.
        for epoch in range(1, generation + 1):
            self.assertEqual(coordinator.publish(current(epoch)).status, "published")
        selection = coordinator.observe().selection
        self.assertIsNotNone(selection)
        return backend, coordinator, selection

    def closed(self, observation):
        self.assertIsNone(observation.selection)
        self.assertIs(observation.runtime_authorized, False)

    def interrupted(self, operation, kind):
        if kind is SimulatedCrash:
            with self.assertRaises(SimulatedCrash):
                operation()
        else:
            result = operation()
            self.assertEqual(result.status, "uncertain")
            self.assertIs(result.runtime_authorized, False)

    def test_default_has_no_backend_for_any_operation(self):
        coordinator = p.Coordinator()
        self.closed(coordinator.observe())
        for result in (coordinator.publish(current()), coordinator.revoke(),
                       coordinator.attempt_release(None, LAUNCH)):
            self.assertEqual((result.status, result.reason),
                             ("refused", "no_qualified_backend"))
            self.assertIs(result.runtime_authorized, False)

    def test_transaction_exit_refusal_cannot_mask_a_completed_operation(self):
        for operation in ("publish", "revoke", "release", "observe"):
            for error in (p.ProtocolRefused, p.BackendUnavailable):
                with self.subTest(operation=operation, error=error.__name__):
                    backend, coordinator, selection = self.active()
                    transaction = backend.transaction
                    exited = []

                    @contextmanager
                    def failed_exit():
                        try:
                            with transaction():
                                yield
                        finally:
                            exited.append(True)
                            raise error("injected_transaction_exit")

                    backend.transaction = failed_exit
                    if operation == "publish":
                        result = coordinator.publish(current(2))
                    elif operation == "revoke":
                        result = coordinator.revoke()
                    elif operation == "release":
                        result = coordinator.attempt_release(selection, LAUNCH)
                    else:
                        result = coordinator.observe()
                    if operation == "observe":
                        self.closed(result)
                        self.assertEqual(result.reason, "transaction_exit_unconfirmed")
                    else:
                        self.assertEqual((result.status, result.reason),
                                         ("uncertain", "transaction_exit_unconfirmed"))
                        self.assertIsNone(result.selection)
                        self.assertIs(result.runtime_authorized, False)
                    self.assertEqual(exited, [True])
                    self.assertIsNone(backend.owner)
                    backend.transaction = transaction
                    fresh = p.Coordinator(backend)
                    if operation == "publish":
                        digest = hashlib.sha256(current(2)).hexdigest()
                        self.assertEqual(backend.anchor, p.Anchor(MACHINE, 4, 2, "active", digest))
                        self.assertEqual(backend.current_bytes, current(2))
                        self.assertEqual(fresh.observe().selection, p.Selection(IDENTITY, 4, 2, digest))
                        self.assertEqual(fresh.publish(current(2)).status, "refused")
                        self.assertEqual(fresh.attempt_release(selection, LAUNCH).status, "refused")
                    elif operation == "revoke":
                        self.assertEqual(backend.anchor, p.Anchor(MACHINE, 3, 2, "revoked"))
                        self.assertIsNone(backend.current_bytes)
                        self.closed(fresh.observe())
                        self.assertEqual(fresh.attempt_release(selection, LAUNCH).status, "refused")
                    elif operation == "release":
                        obligation = p.ReleaseObligation(selection, LAUNCH)
                        self.assertEqual(backend.anchor,
                                         p.Anchor(MACHINE, 3, 1, "release_attempted",
                                                  selection.current_sha256, LAUNCH, obligation))
                        self.assertEqual(backend.current_bytes, current())
                        self.closed(fresh.observe())
                        for launch in (LAUNCH, OTHER_LAUNCH):
                            retry = fresh.attempt_release(selection, launch)
                            self.assertEqual((retry.status, retry.reason),
                                             ("refused", "prior_release_unresolved"))
                    else:
                        self.assertEqual(fresh.observe().selection, selection)
                    self.assertEqual(backend.releases, [LAUNCH] if operation == "release" else [])

    def test_transaction_exit_refusal_cannot_mask_unknown_release_completion(self):
        for inner_error in (p.OutcomeUnknown, OSError):
            for exit_error in (p.ProtocolRefused, p.BackendUnavailable):
                with self.subTest(inner=inner_error.__name__, exit=exit_error.__name__):
                    backend, coordinator, selection = self.active()
                    transaction = backend.transaction
                    seen = []

                    @contextmanager
                    def failed_exit():
                        try:
                            with transaction():
                                try:
                                    yield
                                except inner_error as error:
                                    seen.append(type(error))
                                    raise
                        finally:
                            raise exit_error("injected_transaction_exit")

                    backend.transaction = failed_exit
                    backend.arm("release_stopped_child", "after", inner_error)
                    result = coordinator.attempt_release(selection, LAUNCH)
                    self.assertEqual((result.status, result.reason),
                                     ("uncertain", "transaction_exit_unconfirmed"))
                    self.assertEqual(seen, [inner_error])
                    self.assertIsNone(result.selection)
                    self.assertIs(result.runtime_authorized, False)
                    obligation = p.ReleaseObligation(selection, LAUNCH)
                    self.assertEqual(backend.anchor,
                                     p.Anchor(MACHINE, 3, 1, "release_attempted",
                                              selection.current_sha256, LAUNCH, obligation))
                    self.assertEqual(backend.releases, [LAUNCH])
                    self.assertEqual(backend.counts["release_stopped_child"], 1)
                    backend.transaction = transaction
                    fresh = p.Coordinator(backend)
                    self.closed(fresh.observe())
                    for launch in (LAUNCH, OTHER_LAUNCH):
                        retry = fresh.attempt_release(selection, launch)
                        self.assertEqual((retry.status, retry.reason),
                                         ("refused", "prior_release_unresolved"))
                    self.assertEqual(fresh.revoke().status, "revoked")
                    self.assertEqual(fresh.publish(current(3)).status, "published")
                    self.assertEqual(backend.anchor.outstanding, obligation)
                    self.assertEqual(fresh.attempt_release(fresh.observe().selection, OTHER_LAUNCH).status,
                                     "refused")
                    self.assertEqual(backend.releases, [LAUNCH])
                    self.assertEqual(backend.counts["release_stopped_child"], 1)

    def test_initial_transaction_entry_refusal_still_performs_no_ports_or_writes(self):
        for error in (p.ProtocolRefused, p.BackendUnavailable):
            for operation in ("publish", "revoke", "release", "observe"):
                with self.subTest(error=error.__name__, operation=operation):
                    backend = SyntheticBackend()
                    initial = backend.anchor

                    @contextmanager
                    def failed_entry():
                        raise error("injected_transaction_entry")
                        yield  # pragma: no cover -- required contextmanager shape

                    backend.transaction = failed_entry
                    coordinator = p.Coordinator(backend)
                    if operation == "publish":
                        result = coordinator.publish(current())
                    elif operation == "revoke":
                        result = coordinator.revoke()
                    elif operation == "release":
                        result = coordinator.attempt_release(None, LAUNCH)
                    else:
                        result = coordinator.observe()
                    if operation == "observe":
                        self.closed(result)
                        self.assertEqual(result.reason, "injected_transaction_entry")
                    else:
                        self.assertEqual((result.status, result.reason),
                                         ("refused", "injected_transaction_entry"))
                    self.assertEqual(backend.anchor, initial)
                    self.assertEqual(backend.counts, {})
                    self.assertIsNone(backend.current_bytes)
                    self.assertEqual(backend.staged, {})
                    self.assertEqual(backend.releases, [])

    def test_suppressed_release_exception_is_uncertain_and_cannot_release_again(self):
        # Suppression violates the adapter contract. The coordinator must still
        # return a closed result without losing an already consumed attempt.
        for error in (p.OutcomeUnknown, OSError, p.ProtocolRefused, p.BackendUnavailable):
            with self.subTest(error=error.__name__):
                backend, coordinator, selection = self.active()
                transaction = backend.transaction
                suppressed = []

                @contextmanager
                def suppress_error():
                    with transaction():
                        try:
                            yield
                        except error as caught:
                            suppressed.append(type(caught))

                backend.transaction = suppress_error
                backend.arm("release_stopped_child", "after", error)
                result = coordinator.attempt_release(selection, LAUNCH)
                self.assertEqual((result.status, result.reason),
                                 ("uncertain", "transaction_suppressed_error"))
                self.assertIsNone(result.selection)
                self.assertIs(result.runtime_authorized, False)
                self.assertEqual(suppressed, [error])
                obligation = p.ReleaseObligation(selection, LAUNCH)
                self.assertEqual(backend.anchor,
                                 p.Anchor(MACHINE, 3, 1, "release_attempted",
                                          selection.current_sha256, LAUNCH, obligation))
                self.assertEqual(backend.current_bytes, current())
                backend.transaction = transaction
                fresh = p.Coordinator(backend)
                self.closed(fresh.observe())
                for launch in (LAUNCH, OTHER_LAUNCH):
                    retry = fresh.attempt_release(selection, launch)
                    self.assertEqual((retry.status, retry.reason),
                                     ("refused", "prior_release_unresolved"))
                self.assertEqual(backend.anchor.outstanding, obligation)
                self.assertEqual(backend.releases, [LAUNCH])
                self.assertEqual(backend.counts["release_stopped_child"], 1)

    def test_publish_revoke_and_new_generation_preserve_exact_bindings(self):
        backend, coordinator, first = self.active()
        self.assertEqual((first.identity, first.anchor_revision, first.generation),
                         (IDENTITY, 2, 1))
        self.assertEqual(first.current_sha256, hashlib.sha256(current()).hexdigest())
        self.assertIs(coordinator.observe().runtime_authorized, False)
        result = coordinator.revoke()
        self.assertEqual((result.status, result.generation), ("revoked", 2))
        self.closed(p.Coordinator(backend).observe())
        self.assertIsNone(backend.current_bytes)
        stale = coordinator.attempt_release(first, LAUNCH)
        self.assertEqual(stale.status, "refused")
        self.assertEqual(backend.releases, [])
        result = coordinator.publish(current(3, capture_id=OTHER_CAPTURE))
        self.assertEqual((result.status, result.generation), ("published", 3))
        self.assertEqual(coordinator.observe().selection.anchor_revision, 5)
        self.assertIs(result.runtime_authorized, False)

    def test_missing_or_malformed_anchor_never_bootstraps(self):
        invalid = [None, {}, p.Anchor(MACHINE, True, 0, "closed"),
                   p.Anchor(MACHINE, 0, False, "closed"),
                   p.Anchor(MACHINE, 0, 0, []), p.Anchor(MACHINE, 0, 0, None),
                   p.Anchor(MACHINE, 0, 0, "active"), p.Anchor(MACHINE, 1, 2, "revoked"),
                   p.Anchor(OTHER_MACHINE, 0, 0, "closed"),
                   p.Anchor(MACHINE, 1, 1, "revoked", "1" * 64),
                   p.Anchor(MACHINE, 2, 1, "active", "1" * 64, LAUNCH),
                   p.Anchor(MACHINE, p.MAX_COUNTER + 1, 1, "revoked")]
        for value in invalid:
            for method in ("publish", "revoke", "attempt_release"):
                with self.subTest(anchor=value, method=method):
                    backend = SyntheticBackend()
                    backend.anchor = value
                    coordinator = p.Coordinator(backend)
                    args = {"publish": (current(),), "revoke": (),
                            "attempt_release": (None, LAUNCH)}[method]
                    self.assertEqual(getattr(coordinator, method)(*args).status, "refused")
                    self.assertEqual(set(backend.counts), {"identity", "read_anchor"})
                    self.assertIs(backend.anchor, value)

    def test_unavailable_anchor_and_identity_refuse_before_writes(self):
        for port in ("identity", "read_anchor"):
            with self.subTest(port=port):
                backend = SyntheticBackend()
                def unavailable(_value):
                    raise p.BackendUnavailable("explicit_unavailable")
                backend.overrides[port] = unavailable
                result = p.Coordinator(backend).publish(current())
                self.assertEqual((result.status, result.reason),
                                 ("refused", "explicit_unavailable"))
                self.assertEqual(backend.anchor.generation, 0)
                self.assertNotIn("compare_exchange", backend.counts)

    def test_malformed_identity_and_changed_boot_are_not_selection(self):
        for value in (None, {}, p.Identity(MACHINE, "bad"), p.Identity(True, BOOT)):
            with self.subTest(value=value):
                backend = SyntheticBackend()
                backend.platform = value
                self.assertEqual(p.Coordinator(backend).publish(current()).status, "refused")
                self.assertNotIn("compare_exchange", backend.counts)
        backend, coordinator, selection = self.active()
        backend.platform = p.Identity(MACHINE, OTHER_BOOT)
        self.closed(coordinator.observe())
        self.assertEqual(coordinator.attempt_release(selection, LAUNCH).status, "refused")
        self.assertEqual(backend.releases, [])

    def test_bounded_exact_parser_refuses_mutated_records_before_reservation(self):
        decoded = json.loads(current())
        mutations = [current(generation=True), current(generation=0),
                     current(generation=p.MAX_COUNTER + 1), current(state=False),
                     current(state="reserved"), current(schema="unknown"),
                     current(machine_id=MACHINE.upper()), current(boot_id="bad"),
                     current(capture_id="bad"), current(policy_sha256="G" * 64),
                     current(policy_path="relative"), current(policy_path="/"),
                     current(policy_path="/a//b"), current(policy_path="/a/../b"),
                     current(policy_path="/a/./b"), current(policy_path="/a/b/"),
                     current(policy_path="/a/\x00b"), current(policy_path="/" + "a" * 1024),
                     current(policy_path="/" + "é" * 512),
                     p.canonical({**decoded, "extra": 1}),
                     p.canonical({k: v for k, v in decoded.items() if k != "boot_id"}),
                     current() + b"\n", b"{\"state\":\"active\",\"state\":\"active\"}",
                     b"{\"generation\":NaN}", b"{\"generation\":Infinity}",
                     b"[" * 1500 + b"]" * 1500, b"\xff", b"", b"x" * 4097,
                     current().decode(), bytearray(current()), None]
        for data in mutations:
            with self.subTest(data=repr(data)[:90]):
                backend = SyntheticBackend()
                result = p.Coordinator(backend).publish(data)
                self.assertEqual(result.status, "refused")
                self.assertEqual(backend.anchor.generation, 0)
                self.assertNotIn("compare_exchange", backend.counts)
        # Exact UTF-8 path byte limit positive, separate from canonical JSON size.
        self.assertEqual(p.parse_current(current(policy_path="/" + "a" * 1023)).generation, 1)
        self.assertEqual(p.parse_current(current(policy_path="/" + "é" * 511)).generation, 1)

    def test_non_next_generation_and_wrong_identity_do_not_change_active(self):
        backend, coordinator, selection = self.active()
        before = backend.anchor
        for data in (current(1), current(3), current(2, state="revoked"),
                     current(2, machine_id=OTHER_MACHINE), current(2, boot_id=OTHER_BOOT)):
            with self.subTest(data=data):
                self.assertEqual(coordinator.publish(data).status, "refused")
                self.assertEqual(backend.anchor, before)
                self.assertEqual(coordinator.observe().selection, selection)

    def test_counter_exhaustion_never_wraps_or_writes(self):
        cases = [(p.MAX_COUNTER, p.MAX_COUNTER),
                 (p.MAX_COUNTER, 1), (p.MAX_COUNTER - 1, 1)]
        for revision, generation in cases:
            with self.subTest(revision=revision, generation=generation):
                backend = SyntheticBackend()
                backend.anchor = p.Anchor(MACHINE, revision, generation, "active",
                                          hashlib.sha256(current(generation)).hexdigest())
                backend.current_bytes = current(generation)
                coordinator = p.Coordinator(backend)
                if generation < p.MAX_COUNTER:
                    result = coordinator.publish(current(generation + 1))
                    self.assertEqual((result.status, result.reason), ("refused", "anchor_exhausted"))
                if revision == p.MAX_COUNTER:
                    result = coordinator.revoke()
                    self.assertEqual((result.status, result.reason), ("refused", "anchor_exhausted"))
                    selection = coordinator.observe().selection
                    result = coordinator.attempt_release(selection, LAUNCH)
                    self.assertEqual((result.status, result.reason), ("refused", "anchor_exhausted"))
                self.assertNotIn("compare_exchange", backend.counts)
                self.assertEqual(backend.releases, [])

    def test_publish_interruptions_recover_only_from_fresh_anchor(self):
        # 32 cases: 2 predecessors x 4 writes x 2 edges x 2 interruption kinds.
        boundaries = [("compare_exchange", 1), ("stage_durable", 1),
                      ("publish_current_durable", 1), ("compare_exchange", 2)]
        for prior in (0, 1):
            for method, occurrence in boundaries:
                for edge in ("before", "after"):
                    for kind in (p.OutcomeUnknown, SimulatedCrash):
                        with self.subTest(prior=prior, method=method, occurrence=occurrence,
                                          edge=edge, kind=kind.__name__):
                            if prior:
                                backend, coordinator, old = self.active()
                            else:
                                backend = SyntheticBackend()
                                coordinator, old = p.Coordinator(backend), None
                            backend.arm(method, edge, kind, occurrence)
                            self.interrupted(lambda: coordinator.publish(current(prior + 1)), kind)
                            fresh = p.Coordinator(backend)
                            observation = fresh.observe()
                            untouched = method == "compare_exchange" and occurrence == 1 and edge == "before"
                            committed = method == "compare_exchange" and occurrence == 2 and edge == "after"
                            if untouched and old is not None:
                                self.assertEqual(observation.selection, old)
                            elif committed:
                                self.assertEqual(observation.selection.generation, prior + 1)
                            else:
                                self.closed(observation)
                            self.assertEqual(backend.anchor.generation, prior if untouched else prior + 1)
                            self.assertEqual(backend.releases, [])
                            if not untouched and not committed:
                                # Interrupted reservation cannot be replayed; recovery
                                # requires a newly advanced generation.
                                self.assertEqual(fresh.publish(current(prior + 1)).status, "refused")
                                self.assertEqual(fresh.publish(current(prior + 2)).status, "published")
                                self.assertEqual(fresh.observe().selection.generation, prior + 2)

    def test_exact_staging_receipt_is_required_after_fencing(self):
        mutations = [lambda r: None, lambda r: replace(r, generation=True),
                     lambda r: replace(r, generation=2),
                     lambda r: replace(r, capture_id=OTHER_CAPTURE),
                     lambda r: replace(r, policy_sha256="2" * 64),
                     lambda r: replace(r, current_sha256="2" * 64)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                backend = SyntheticBackend()
                backend.overrides["stage_durable"] = mutation
                result = p.Coordinator(backend).publish(current())
                self.assertEqual((result.status, result.reason),
                                 ("refused", "staged_bundle_receipt_mismatch"))
                self.assertEqual((backend.anchor.phase, backend.anchor.generation), ("reserved", 1))
                self.assertNotIn("publish_current_durable", backend.counts)
                self.closed(p.Coordinator(backend).observe())

    def test_cas_conflict_wrong_receipt_and_readback_do_not_publish(self):
        for receipt in (None, {}, p.Anchor(MACHINE, True, 1, "reserved", "1" * 64)):
            with self.subTest(receipt=receipt):
                backend = SyntheticBackend()
                backend.overrides["compare_exchange"] = lambda _value: receipt
                result = p.Coordinator(backend).publish(current())
                self.assertEqual(result.status, "refused" if receipt is None else "uncertain")
                self.assertNotIn("stage_durable", backend.counts)
                self.closed(p.Coordinator(backend).observe())
        backend = SyntheticBackend()
        def invalidate_after_cas():
            backend.anchor = p.Anchor(MACHINE, 2, 2, "revoked")
        backend.hooks[("compare_exchange", "after")] = invalidate_after_cas
        result = p.Coordinator(backend).publish(current())
        self.assertEqual((result.status, result.reason), ("uncertain", "anchor_readback_mismatch"))
        self.assertNotIn("stage_durable", backend.counts)

    def test_unconfirmed_cas_readback_is_uncertain_and_recovers_exact_state(self):
        stages = {"reservation": (1, "reserved", 2, 3),
                  "activation": (2, "active", 2, 4),
                  "revocation": (1, "revoked", 2, 3),
                  "attempt": (1, "release_attempted", 1, 3)}
        for stage, (occurrence, phase, generation, revision) in stages.items():
            for fault in ("missing", "malformed", "unavailable", "refused"):
                with self.subTest(stage=stage, fault=fault):
                    backend, coordinator, selection = self.active()
                    backend.counts.clear()

                    def failed_readback(value):
                        if fault == "missing":
                            return None
                        if fault == "malformed":
                            return replace(value, revision=True)
                        error = (p.BackendUnavailable if fault == "unavailable"
                                 else p.ProtocolRefused)
                        raise error("injected_anchor_readback")

                    def lose_confirmation():
                        if backend.counts["compare_exchange"] == occurrence:
                            backend.overrides["read_anchor"] = failed_readback

                    backend.hooks[("compare_exchange", "after")] = lose_confirmation
                    if stage in ("reservation", "activation"):
                        result = coordinator.publish(current(2))
                    elif stage == "revocation":
                        result = coordinator.revoke()
                    else:
                        result = coordinator.attempt_release(selection, LAUNCH)
                    self.assertEqual((result.status, result.reason),
                                     ("uncertain", "anchor_readback_unconfirmed"))
                    self.assertIsNone(result.selection)
                    self.assertIs(result.runtime_authorized, False)
                    self.assertEqual((backend.anchor.phase, backend.anchor.generation,
                                      backend.anchor.revision), (phase, generation, revision))
                    self.assertEqual(backend.counts["compare_exchange"], occurrence)
                    self.assertEqual(backend.releases, [])
                    if stage == "attempt":
                        self.assertEqual(backend.anchor.outstanding,
                                         p.ReleaseObligation(selection, LAUNCH))

                    # Remove only the read fault. The persisted transition is
                    # recovered by a fresh client, never reset or retried.
                    backend.hooks.clear()
                    backend.overrides.clear()
                    fresh = p.Coordinator(backend)
                    observation = fresh.observe()
                    if stage == "activation":
                        self.assertEqual(observation.selection.generation, 2)
                    else:
                        self.closed(observation)
                    if stage == "attempt":
                        original = backend.anchor.outstanding
                        for launch in (LAUNCH, OTHER_LAUNCH):
                            retry = fresh.attempt_release(selection, launch)
                            self.assertEqual((retry.status, retry.reason),
                                             ("refused", "prior_release_unresolved"))
                        self.assertEqual(fresh.revoke().status, "revoked")
                        self.assertEqual(fresh.publish(current(3)).status, "published")
                        retry = fresh.attempt_release(fresh.observe().selection, OTHER_LAUNCH)
                        self.assertEqual((retry.status, retry.reason),
                                         ("refused", "prior_release_unresolved"))
                        self.assertEqual(backend.anchor.outstanding, original)
                        self.assertEqual(backend.releases, [])

    def test_initial_invalid_or_unavailable_anchor_still_refuses_without_writes(self):
        for fault in ("missing", "malformed", "unavailable"):
            for operation in ("publish", "revoke", "attempt"):
                with self.subTest(fault=fault, operation=operation):
                    backend = SyntheticBackend()
                    initial = backend.anchor

                    def failed_read(value):
                        if fault == "missing":
                            return None
                        if fault == "malformed":
                            return replace(value, revision=True)
                        raise p.BackendUnavailable("injected_initial_read")

                    backend.overrides["read_anchor"] = failed_read
                    coordinator = p.Coordinator(backend)
                    if operation == "publish":
                        result = coordinator.publish(current())
                    elif operation == "revoke":
                        result = coordinator.revoke()
                    else:
                        result = coordinator.attempt_release(None, LAUNCH)
                    self.assertEqual(result.status, "refused")
                    self.assertIs(result.runtime_authorized, False)
                    self.assertEqual(backend.anchor, initial)
                    self.assertNotIn("compare_exchange", backend.counts)
                    self.assertEqual(backend.releases, [])

    def test_bad_publication_receipt_and_torn_current_leave_reserved(self):
        for port, value in (("publish_current_durable", "2" * 64),
                            ("publish_current_durable", None),
                            ("read_current", None), ("read_current", b"{torn"),
                            ("read_current", current(1, capture_id=OTHER_CAPTURE)),
                            ("read_current", current(1, policy_sha256="2" * 64))):
            with self.subTest(port=port, value=value):
                backend = SyntheticBackend()
                backend.overrides[port] = lambda _value: value
                result = p.Coordinator(backend).publish(current())
                self.assertIn(result.status, ("refused", "uncertain"))
                self.assertEqual((backend.anchor.phase, backend.anchor.generation), ("reserved", 1))
                self.assertEqual(backend.counts["compare_exchange"], 1)
                self.closed(p.Coordinator(backend).observe())

    def test_committed_publication_with_unknown_final_read_is_recovered_fresh(self):
        for edge in ("before", "after"):
            with self.subTest(edge=edge):
                backend, coordinator, old = self.active()
                backend.arm("read_current", edge, OSError, occurrence=2)
                result = coordinator.publish(current(2))
                self.assertEqual(result.status, "uncertain")
                self.assertIsNone(result.selection)
                self.assertEqual((backend.anchor.phase, backend.anchor.generation), ("active", 2))
                fresh = p.Coordinator(backend)
                self.assertEqual(fresh.observe().selection.generation, 2)
                self.assertEqual(fresh.attempt_release(old, LAUNCH).status, "refused")
                self.assertEqual(backend.releases, [])

    def test_committed_publication_terminal_reads_preserve_exact_state_and_recovery(self):
        # Final observation follows the activation CAS and its own readback.
        # Cover both anchor reads, the copied current record and platform identity.
        positions = [("read_anchor", 6), ("read_current", 2),
                     ("identity", 3), ("read_anchor", 7)]
        for port, occurrence in positions:
            for fault in ("missing", "malformed", "unavailable", "refused",
                          "mismatch", "positive"):
                with self.subTest(port=port, occurrence=occurrence, fault=fault):
                    backend, coordinator, old = self.active()
                    backend.counts.clear()
                    witnessed = []

                    def final_read(value):
                        if backend.counts[port] != occurrence:
                            return value
                        witnessed.append((port, occurrence))
                        if fault == "positive":
                            return value
                        if fault == "missing":
                            return None
                        if fault in ("unavailable", "refused"):
                            error = (p.BackendUnavailable if fault == "unavailable"
                                     else p.ProtocolRefused)
                            raise error("injected_publication_terminal_read")
                        if port == "read_anchor":
                            return replace(value, revision=(True if fault == "malformed"
                                                            else value.revision + 1))
                        if port == "identity":
                            return p.Identity(MACHINE, "bad" if fault == "malformed"
                                              else OTHER_BOOT)
                        return b"{torn" if fault == "malformed" else current(2, capture_id=OTHER_CAPTURE)

                    backend.overrides[port] = final_read
                    result = coordinator.publish(current(2))
                    if fault == "positive":
                        self.assertEqual((result.status, result.reason), ("published", ""))
                    else:
                        self.assertEqual((result.status, result.reason),
                                         ("uncertain", "publication_readback_unconfirmed"))
                        self.assertIsNone(result.selection)
                    self.assertIs(result.runtime_authorized, False)
                    self.assertEqual(witnessed, [(port, occurrence)])
                    self.assertEqual(backend.counts["compare_exchange"], 2)
                    digest = hashlib.sha256(current(2)).hexdigest()
                    committed = p.Anchor(MACHINE, 4, 2, "active", digest)
                    selected = p.Selection(IDENTITY, 4, 2, digest)
                    self.assertEqual(backend.anchor, committed)
                    self.assertEqual(backend.current_bytes, current(2))
                    self.assertEqual(backend.staged[digest].data, current(2))
                    if fault == "positive":
                        self.assertEqual(result.selection, selected)
                    self.assertEqual(backend.releases, [])

                    # Restore only the read port. No state repair or rollback is
                    # needed to recover the already committed publication.
                    backend.overrides.clear()
                    fresh = p.Coordinator(backend)
                    self.assertEqual(fresh.observe().selection, selected)
                    before_retry = backend.counts["compare_exchange"]
                    retry = fresh.publish(current(2))
                    self.assertEqual((retry.status, retry.reason),
                                     ("refused", "publication_generation_not_next"))
                    self.assertEqual(backend.counts["compare_exchange"], before_retry)
                    self.assertEqual(fresh.attempt_release(old, LAUNCH).status, "refused")
                    self.assertEqual(backend.anchor, committed)
                    self.assertEqual(fresh.publish(current(3)).status, "published")
                    self.assertEqual(fresh.observe().selection.generation, 3)
                    self.assertEqual(backend.releases, [])

    def test_active_depot_substitution_is_not_authority(self):
        replacements = [None, b"{torn", current(2), current(state="revoked"),
                        current(boot_id=OTHER_BOOT), current(machine_id=OTHER_MACHINE),
                        current(capture_id=OTHER_CAPTURE), current(policy_path="/another/policy"),
                        current(policy_sha256="2" * 64)]
        for data in replacements:
            with self.subTest(data=data):
                backend, coordinator, selection = self.active()
                backend.current_bytes = data
                self.closed(coordinator.observe())
                self.assertEqual(coordinator.attempt_release(selection, LAUNCH).status, "refused")
                self.assertEqual(backend.releases, [])

    def test_depot_only_rollback_refuses_but_rollbackable_anchor_is_open(self):
        backend, coordinator, old_selection = self.active()
        old_anchor, old_bytes = backend.anchor, backend.current_bytes
        self.assertEqual(coordinator.publish(current(2)).status, "published")
        new_anchor = backend.anchor
        backend.current_bytes = old_bytes
        self.closed(p.Coordinator(backend).observe())
        self.assertEqual(coordinator.attempt_release(old_selection, LAUNCH).status, "refused")
        self.assertEqual(backend.anchor, new_anchor)
        self.assertEqual(backend.releases, [])
        # Deliberately violate the independent monotonic backend contract.
        # Restoring BOTH stores fools this simulator. This is an open production
        # prerequisite, not a claim that a local high-water file prevents rollback.
        backend.anchor = old_anchor
        unsafe = p.Coordinator(backend).observe()
        self.assertEqual(unsafe.selection, old_selection)
        self.assertIs(unsafe.runtime_authorized, False)

    def test_revocation_interruptions_fence_before_store_withdrawal(self):
        # 8 cases: authority CAS / withdrawal x 2 edges x 2 interruption kinds.
        for method in ("compare_exchange", "withdraw_current_durable"):
            for edge in ("before", "after"):
                for kind in (p.OutcomeUnknown, SimulatedCrash):
                    with self.subTest(method=method, edge=edge, kind=kind.__name__):
                        backend, coordinator, selection = self.active()
                        old_bytes = backend.current_bytes
                        backend.arm(method, edge, kind)
                        self.interrupted(coordinator.revoke, kind)
                        untouched = method == "compare_exchange" and edge == "before"
                        fresh = p.Coordinator(backend)
                        if untouched:
                            self.assertEqual(fresh.observe().selection, selection)
                        else:
                            self.assertEqual((backend.anchor.phase, backend.anchor.generation), ("revoked", 2))
                            backend.current_bytes = old_bytes  # even a failed/undone withdrawal
                            self.closed(fresh.observe())
                            self.assertEqual(fresh.attempt_release(selection, LAUNCH).status, "refused")
                            self.assertEqual(backend.releases, [])
                            self.assertEqual(fresh.publish(current(3)).status, "published")

    def test_withdrawal_false_success_is_uncertain_and_stays_fenced(self):
        backend, coordinator, selection = self.active()
        old_bytes = backend.current_bytes
        backend.hooks[("withdraw_current_durable", "after")] = lambda: setattr(backend, "current_bytes", old_bytes)
        result = coordinator.revoke()
        self.assertEqual((result.status, result.reason), ("uncertain", "withdrawal_not_confirmed"))
        self.closed(p.Coordinator(backend).observe())
        self.assertEqual(coordinator.attempt_release(selection, LAUNCH).status, "refused")
        self.assertEqual(backend.releases, [])

    def test_withdrawn_revocation_terminal_reads_preserve_fence_and_fresh_recovery(self):
        positions = [("read_current", 1), ("read_anchor", 3), ("identity", 2)]
        for port, occurrence in positions:
            for fault in ("missing", "malformed", "unavailable", "refused",
                          "mismatch", "positive"):
                with self.subTest(port=port, fault=fault):
                    backend, coordinator, old = self.active()
                    backend.counts.clear()
                    witnessed = []

                    def final_read(value):
                        if backend.counts[port] != occurrence:
                            return value
                        witnessed.append((port, occurrence))
                        if fault == "positive":
                            return value
                        if fault == "missing":
                            return None
                        if fault in ("unavailable", "refused"):
                            error = (p.BackendUnavailable if fault == "unavailable"
                                     else p.ProtocolRefused)
                            raise error("injected_revocation_terminal_read")
                        if port == "read_anchor":
                            return replace(value, revision=(True if fault == "malformed"
                                                            else value.revision + 1))
                        if port == "identity":
                            return p.Identity(MACHINE, "bad" if fault == "malformed"
                                              else OTHER_BOOT)
                        return b"{torn" if fault == "malformed" else current()

                    backend.overrides[port] = final_read
                    result = coordinator.revoke()
                    if fault == "positive" or (port == "read_current" and fault == "missing"):
                        expected = ("revoked", "")
                    elif port == "read_current" and fault in ("malformed", "mismatch"):
                        expected = ("uncertain", "withdrawal_not_confirmed")
                    elif fault == "mismatch":
                        expected = ("uncertain", "revocation_readback_mismatch")
                    else:
                        expected = ("uncertain", "revocation_readback_unconfirmed")
                    self.assertEqual((result.status, result.reason), expected)
                    self.assertIsNone(result.selection)
                    self.assertIs(result.runtime_authorized, False)
                    self.assertEqual(witnessed, [(port, occurrence)])
                    self.assertEqual(backend.counts["compare_exchange"], 1)
                    self.assertEqual(backend.counts["withdraw_current_durable"], 1)
                    committed = p.Anchor(MACHINE, 3, 2, "revoked")
                    self.assertEqual(backend.anchor, committed)
                    self.assertIsNone(backend.current_bytes)
                    self.assertEqual(backend.releases, [])

                    backend.overrides.clear()
                    fresh = p.Coordinator(backend)
                    self.closed(fresh.observe())
                    self.assertEqual(fresh.attempt_release(old, LAUNCH).status, "refused")
                    self.assertEqual(backend.anchor, committed)
                    # Retrying revocation consumes another epoch. It cannot
                    # restore the withdrawn publication or reuse its generation.
                    retry = fresh.revoke()
                    self.assertEqual((retry.status, retry.generation), ("revoked", 3))
                    self.assertEqual(backend.anchor, p.Anchor(MACHINE, 4, 3, "revoked"))
                    self.assertIsNone(backend.current_bytes)
                    self.assertEqual(fresh.publish(current(3)).status, "refused")
                    self.assertEqual(fresh.publish(current(4)).status, "published")
                    self.assertEqual(fresh.observe().selection.generation, 4)
                    self.assertEqual(backend.releases, [])

    def test_repeated_revocation_never_reuses_an_epoch(self):
        backend, coordinator, _ = self.active()
        self.assertEqual(coordinator.revoke().generation, 2)
        self.assertEqual(p.Coordinator(backend).revoke().generation, 3)
        self.assertEqual(coordinator.publish(current(3)).status, "refused")
        self.assertEqual(coordinator.publish(current(4)).status, "published")

    def test_one_release_attempt_is_persisted_and_never_runtime_authority(self):
        backend, coordinator, selection = self.active()
        result = coordinator.attempt_release(selection, LAUNCH)
        self.assertEqual((result.status, result.generation), ("release_observed", 1))
        self.assertIs(result.runtime_authorized, False)
        self.assertIsNone(result.selection)
        self.assertEqual((backend.anchor.phase, backend.anchor.launch_id), ("release_attempted", LAUNCH))
        self.assertEqual(backend.releases, [LAUNCH])
        for launch in (LAUNCH, OTHER_LAUNCH):
            self.assertEqual(p.Coordinator(backend).attempt_release(selection, launch).status, "refused")
        self.closed(p.Coordinator(backend).observe())
        self.assertEqual(backend.releases, [LAUNCH])

    def test_stale_or_malformed_selector_and_exact_child_mismatch_do_not_release(self):
        backend, coordinator, selection = self.active()
        selectors = [None, replace(selection, generation=True),
                     replace(selection, anchor_revision=True),
                     replace(selection, identity=None),
                     replace(selection, current_sha256="2" * 64),
                     replace(selection, anchor_revision=3)]
        for stale in selectors:
            with self.subTest(selector=stale):
                self.assertEqual(coordinator.attempt_release(stale, LAUNCH).status, "refused")
                self.assertEqual(backend.anchor.phase, "active")
        for mutation in (lambda r: None, lambda r: replace(r, launch_id=OTHER_LAUNCH),
                         lambda r: replace(r, selection=replace(selection, generation=True)),
                         lambda r: replace(r, selection=replace(selection, current_sha256="2" * 64))):
            backend.overrides["verify_release_ready"] = mutation
            self.assertEqual(coordinator.attempt_release(selection, LAUNCH).status, "refused")
            self.assertEqual(backend.anchor.phase, "active")
        self.assertEqual(backend.releases, [])

    def test_release_interruptions_block_retry_after_durable_attempt(self):
        # 8 cases: attempt-marker CAS / release callback x 2 edges x 2 kinds.
        for method in ("compare_exchange", "release_stopped_child"):
            for edge in ("before", "after"):
                for kind in (p.OutcomeUnknown, SimulatedCrash):
                    with self.subTest(method=method, edge=edge, kind=kind.__name__):
                        backend, coordinator, selection = self.active()
                        backend.arm(method, edge, kind)
                        self.interrupted(lambda: coordinator.attempt_release(selection, LAUNCH), kind)
                        count = 1 if method == "release_stopped_child" and edge == "after" else 0
                        self.assertEqual(len(backend.releases), count)
                        untouched = method == "compare_exchange" and edge == "before"
                        fresh = p.Coordinator(backend)
                        if untouched:
                            self.assertEqual(backend.anchor.phase, "active")
                            self.assertEqual(fresh.attempt_release(selection, LAUNCH).status, "release_observed")
                            self.assertEqual(backend.releases, [LAUNCH])
                        else:
                            self.assertEqual(backend.anchor.phase, "release_attempted")
                            self.closed(fresh.observe())
                            self.assertEqual(fresh.attempt_release(selection, LAUNCH).status, "refused")
                            self.assertEqual(fresh.attempt_release(selection, OTHER_LAUNCH).status, "refused")
                            self.assertEqual(len(backend.releases), count)
                            self.assertEqual(fresh.revoke().status, "revoked")
                            # No test port exposes kill/unpin; uncertain release
                            # does not authorize cleanup of a potentially running child.
                            self.assertEqual(len(backend.releases), count)

    def test_unconfirmed_release_receipts_are_uncertain_and_cannot_retry(self):
        for receipt in (None, p.ReleaseReceipt(LAUNCH, False), p.ReleaseReceipt(LAUNCH, 1),
                        p.ReleaseReceipt(OTHER_LAUNCH, True)):
            with self.subTest(receipt=receipt):
                backend, coordinator, selection = self.active()
                backend.overrides["release_stopped_child"] = lambda _value: receipt
                result = coordinator.attempt_release(selection, LAUNCH)
                self.assertEqual((result.status, result.reason), ("uncertain", "release_not_confirmed"))
                self.assertEqual(backend.releases, [LAUNCH])
                self.assertEqual(p.Coordinator(backend).attempt_release(selection, LAUNCH).status, "refused")
                self.assertEqual(backend.releases, [LAUNCH])

    def test_last_release_read_failure_or_substitution_stays_terminal(self):
        for edge in ("before", "after"):
            with self.subTest(edge=edge):
                backend, coordinator, selection = self.active()
                backend.arm("read_current", edge, OSError, occurrence=3)
                self.assertEqual(coordinator.attempt_release(selection, LAUNCH).status, "uncertain")
                self.assertEqual(backend.anchor.phase, "release_attempted")
                self.assertEqual(backend.releases, [])
                self.assertEqual(p.Coordinator(backend).attempt_release(selection, LAUNCH).status, "refused")
        backend, coordinator, selection = self.active()
        backend.counts.clear()
        def substitute_last_read():
            if backend.counts["read_current"] == 3:
                backend.current_bytes = current(2)
        backend.hooks[("read_current", "before")] = substitute_last_read
        self.assertEqual(coordinator.attempt_release(selection, LAUNCH).status, "refused")
        self.assertEqual(backend.anchor.phase, "release_attempted")
        self.assertEqual(backend.releases, [])
        backend.current_bytes = current()  # restoring bytes cannot remove attempt marker
        self.closed(p.Coordinator(backend).observe())
        self.assertEqual(p.Coordinator(backend).attempt_release(selection, LAUNCH).status, "refused")

    def test_true_compare_conflict_preserves_the_other_monotonic_transition(self):
        backend = SyntheticBackend()
        competing = p.Anchor(MACHINE, 1, 1, "revoked")
        backend.hooks[("compare_exchange", "before")] = lambda: setattr(backend, "anchor", competing)
        result = p.Coordinator(backend).publish(current())
        self.assertEqual((result.status, result.reason), ("refused", "anchor_compare_conflict"))
        self.assertEqual(backend.anchor, competing)
        self.assertNotIn("stage_durable", backend.counts)
        self.closed(p.Coordinator(backend).observe())

    def test_changes_during_observation_and_before_release_are_rechecked(self):
        for operation in ("observe", "release"):
            for target in ("identity", "anchor", "current"):
                with self.subTest(operation=operation, target=target):
                    backend, coordinator, selection = self.active()
                    if target == "identity":
                        mutation = lambda: setattr(backend, "platform", p.Identity(MACHINE, OTHER_BOOT))
                    elif target == "anchor":
                        mutation = lambda: setattr(backend, "anchor", p.Anchor(MACHINE, 3, 2, "revoked"))
                    else:
                        mutation = lambda: setattr(backend, "current_bytes", current(2))
                    port = "read_current" if operation == "observe" else "verify_release_ready"
                    backend.hooks[(port, "after")] = mutation
                    if operation == "observe" and target == "current":
                        # Bytes were already returned and the last anchor is unchanged;
                        # a broken depot port cannot revoke this copied observation.
                        # The release path independently re-reads and refuses it.
                        observed = coordinator.observe()
                        self.assertEqual(observed.selection, selection)
                        self.assertEqual(coordinator.attempt_release(observed.selection, LAUNCH).status, "refused")
                    elif operation == "observe":
                        self.closed(coordinator.observe())
                    else:
                        self.assertEqual(coordinator.attempt_release(selection, LAUNCH).status, "refused")
                    self.assertEqual(backend.releases, [])

    def test_os_errors_are_uncertain_and_reservation_still_fences(self):
        backend, coordinator, selection = self.active()
        backend.arm("publish_current_durable", "after", OSError)
        result = coordinator.publish(current(2))
        self.assertEqual(result.status, "uncertain")
        self.closed(p.Coordinator(backend).observe())
        self.assertEqual(coordinator.attempt_release(selection, LAUNCH).status, "refused")
        self.assertEqual(backend.releases, [])

    def test_same_thread_reentrancy_cannot_revoke_during_release(self):
        backend, coordinator, selection = self.active()
        nested = []
        backend.hooks[("release_stopped_child", "before")] = lambda: nested.append(p.Coordinator(backend).revoke())
        result = coordinator.attempt_release(selection, LAUNCH)
        self.assertEqual(result.status, "release_observed")
        self.assertEqual((nested[0].status, nested[0].reason), ("refused", "nonreentrant_authority"))
        self.assertEqual(backend.anchor.phase, "release_attempted")

    def hold(self, backend, method):
        reached, resume = threading.Event(), threading.Event()
        def pause():
            reached.set()
            if not resume.wait(3):
                raise AssertionError("bounded synthetic barrier expired")
        backend.hooks[(method, "before")] = pause
        return reached, resume

    def test_other_client_release_waits_for_publication_then_refuses_old_selector(self):
        backend, coordinator, old = self.active()
        reached, resume = self.hold(backend, "publish_current_durable")
        with ThreadPoolExecutor(max_workers=2) as pool:
            update = pool.submit(coordinator.publish, current(2))
            try:
                self.assertTrue(reached.wait(2))
                self.assertEqual((backend.anchor.phase, backend.anchor.generation), ("reserved", 2))
                waiting = backend.waiting_event = threading.Event()
                release = pool.submit(p.Coordinator(backend).attempt_release, old, LAUNCH)
                self.assertTrue(waiting.wait(2))
                self.assertFalse(release.done())
                self.assertEqual(backend.releases, [])
            finally:
                resume.set()
            self.assertEqual(update.result(timeout=3).status, "published")
            self.assertEqual(release.result(timeout=3).status, "refused")
        self.assertEqual(backend.releases, [])

    def test_other_client_release_waits_for_revocation_then_refuses(self):
        backend, coordinator, old = self.active()
        reached, resume = self.hold(backend, "withdraw_current_durable")
        with ThreadPoolExecutor(max_workers=2) as pool:
            revocation = pool.submit(coordinator.revoke)
            try:
                self.assertTrue(reached.wait(2))
                self.assertEqual((backend.anchor.phase, backend.anchor.generation), ("revoked", 2))
                waiting = backend.waiting_event = threading.Event()
                release = pool.submit(p.Coordinator(backend).attempt_release, old, LAUNCH)
                self.assertTrue(waiting.wait(2))
                self.assertFalse(release.done())
            finally:
                resume.set()
            self.assertEqual(revocation.result(timeout=3).status, "revoked")
            self.assertEqual(release.result(timeout=3).status, "refused")
        self.assertEqual(backend.releases, [])

    def test_other_client_revocation_waits_through_exact_release_callback(self):
        backend, coordinator, selection = self.active()
        reached, resume = self.hold(backend, "release_stopped_child")
        with ThreadPoolExecutor(max_workers=2) as pool:
            release = pool.submit(coordinator.attempt_release, selection, LAUNCH)
            try:
                self.assertTrue(reached.wait(2))
                attempted = backend.anchor
                self.assertEqual(attempted.phase, "release_attempted")
                waiting = backend.waiting_event = threading.Event()
                revocation = pool.submit(p.Coordinator(backend).revoke)
                self.assertTrue(waiting.wait(2))
                self.assertFalse(revocation.done())
                self.assertEqual(backend.anchor, attempted)
            finally:
                resume.set()
            self.assertEqual(release.result(timeout=3).status, "release_observed")
            self.assertEqual(revocation.result(timeout=3).status, "revoked")
        self.assertEqual(backend.releases, [LAUNCH])
        self.assertEqual((backend.anchor.phase, backend.anchor.generation), ("revoked", 2))
        release_after = next(i for i, e in enumerate(backend.events) if e[:2] == ("release_stopped_child", "after"))
        withdraw_before = next(i for i, e in enumerate(backend.events) if e[:2] == ("withdraw_current_durable", "before"))
        self.assertLess(release_after, withdraw_before)
        self.closed(p.Coordinator(backend).observe())

    def test_cached_observation_defect_control_is_discriminating(self):
        backend, coordinator, cached = self.active()
        self.assertEqual(coordinator.revoke().status, "revoked")
        # The defective old design treats a once-positive copy as authority.
        # Record its unsafe intent without a child or backend callback.
        defective_release_intents = [LAUNCH] if cached is not None else []
        self.assertEqual(defective_release_intents, [LAUNCH])
        self.assertEqual(p.Coordinator(backend).attempt_release(cached, LAUNCH).status, "refused")
        self.assertEqual(backend.releases, [])


if __name__ == "__main__":
    unittest.main()
