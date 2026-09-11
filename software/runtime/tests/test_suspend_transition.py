import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from sage_lantern.suspend_transition import (
    Acknowledgement, Gate, Outcome, Phase, Request, Snapshot,
    SuspendTransition, SuspendTransitionError,
)


class ProcessDeath(BaseException):
    pass


class Adapter:
    def __init__(self):
        self.calls = []
        self.saved = None
        self.fenced = False
        self.auth_running = True
        self.sleep_submitted = False
        self.resumed = False
        self.failure_at = None
        self.failure = None
        self.die_after = None

    def __call__(self, request):
        self.calls.append(request)
        if len(self.calls) == self.failure_at:
            if isinstance(self.failure, BaseException):
                raise self.failure
            return self.failure(request)
        gate = request.gate
        if gate is Gate.RETAIN_FENCE:
            self.fenced = True
        elif gate is Gate.CHECKPOINT:
            self.saved = request.snapshot
        elif gate is Gate.STOP_AUTH:
            self.auth_running = False
        elif gate is Gate.AUTH_INACTIVE:
            assert not self.auth_running
        elif gate is Gate.SUBMIT_SLEEP:
            assert self.fenced and not self.auth_running
            assert self.saved.phase is Phase.SLEEP_REQUESTED
            assert not self.sleep_submitted
            self.sleep_submitted = True
        elif gate is Gate.VERIFY_RESUMED:
            if not self.resumed:
                return Acknowledgement(request, Outcome.UNKNOWN)
        elif gate is Gate.VERIFY_ABORTED:
            if self.sleep_submitted and not self.resumed:
                return Acknowledgement(request, Outcome.UNKNOWN)
        elif gate is Gate.RELEASE_FENCE:
            assert self.saved.phase in (Phase.RESUMED, Phase.ABORTED_VERIFIED)
            self.fenced = False
        elif gate is Gate.START_AUTH_LOCKED:
            assert not self.fenced
            self.auth_running = True
        if len(self.calls) == self.die_after:
            raise ProcessDeath
        return Acknowledgement(request, Outcome.CONFIRMED)

    @property
    def gates(self):
        return [request.gate for request in self.calls]


class SuspendTransitionTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Adapter()
        self.coordinator = SuspendTransition("transition-0001", self.adapter)

    def error(self, code, operation):
        with self.assertRaises(SuspendTransitionError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    def requested(self):
        self.coordinator.prepare()
        self.coordinator.request_sleep()

    def test_constructor_has_no_effects(self):
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.coordinator.snapshot, Snapshot("transition-0001"))

    def test_prepare_exact_gate_order_and_durable_ready(self):
        snapshot = self.coordinator.prepare()
        self.assertEqual(self.adapter.gates, [Gate.RETAIN_FENCE, Gate.CHECKPOINT, Gate.STOP_AUTH,
                         Gate.AUTH_INACTIVE, Gate.QUIESCE, Gate.IDLE_ELIGIBLE, Gate.CHECKPOINT])
        self.assertEqual(snapshot, self.adapter.saved)
        self.assertTrue(snapshot.prepared)
        self.assertFalse(snapshot.completed)
        self.assertTrue(self.adapter.fenced)

    def test_submit_is_not_resume_and_cannot_repeat(self):
        self.requested()
        self.assertEqual(self.adapter.gates[-6:], [Gate.RETAIN_FENCE, Gate.AUTH_INACTIVE,
                         Gate.QUIESCE, Gate.IDLE_ELIGIBLE, Gate.CHECKPOINT, Gate.SUBMIT_SLEEP])
        self.assertEqual(self.coordinator.snapshot.phase, Phase.SLEEP_REQUESTED)
        self.assertNotIn(Gate.RELEASE_FENCE, self.adapter.gates)
        self.assertFalse(self.adapter.auth_running)
        self.error("invalid_transition", self.coordinator.request_sleep)
        self.assertEqual(self.adapter.gates.count(Gate.SUBMIT_SLEEP), 1)

    def test_resume_proof_precedes_durable_terminal_then_release(self):
        self.requested()
        self.adapter.resumed = True
        snapshot = self.coordinator.resume()
        self.assertEqual(self.adapter.gates[-8:], [Gate.RETAIN_FENCE, Gate.STOP_AUTH,
                         Gate.AUTH_INACTIVE, Gate.QUIESCE, Gate.VERIFY_RESUMED,
                         Gate.CHECKPOINT, Gate.RELEASE_FENCE, Gate.START_AUTH_LOCKED])
        self.assertEqual(snapshot.phase, Phase.RESUMED)
        self.assertTrue(snapshot.completed)
        self.assertFalse(self.adapter.fenced)
        self.assertTrue(self.adapter.auth_running)
        for operation in (self.coordinator.prepare, self.coordinator.request_sleep,
                          self.coordinator.resume, self.coordinator.abort):
            self.error("invalid_transition", operation)

    def test_abort_requires_positive_no_pending_sleep_proof(self):
        self.coordinator.prepare()
        snapshot = self.coordinator.abort()
        self.assertEqual(snapshot.phase, Phase.ABORTED_VERIFIED)
        self.assertTrue(snapshot.completed)
        self.assertLess(self.adapter.gates.index(Gate.VERIFY_ABORTED),
                        self.adapter.gates.index(Gate.RELEASE_FENCE))
        self.assertNotIn(Gate.SUBMIT_SLEEP, self.adapter.gates)

    def test_pending_sleep_prevents_both_abort_and_unproven_resume(self):
        self.requested()
        self.error("recovery_required", self.coordinator.abort)
        self.error("recovery_required", self.coordinator.resume)
        self.assertTrue(self.adapter.fenced)
        self.assertFalse(self.adapter.auth_running)
        self.assertNotIn(Gate.RELEASE_FENCE, self.adapter.gates)
        self.assertEqual(self.coordinator.snapshot.phase, Phase.RECOVERY_REQUIRED)

    def test_missing_cancel_ack_cannot_submit(self):
        self.adapter.failure_at = 5
        self.adapter.failure = lambda request: Acknowledgement(request, Outcome.UNKNOWN)
        self.error("recovery_required", self.coordinator.prepare)
        self.error("invalid_transition", self.coordinator.request_sleep)
        self.assertTrue(self.adapter.fenced)
        self.assertNotIn(Gate.SUBMIT_SLEEP, self.adapter.gates)

    def test_submit_timeout_retains_fence_and_never_retries(self):
        self.coordinator.prepare()
        self.adapter.failure_at = len(self.adapter.calls) + 6
        self.adapter.failure = TimeoutError("private upstream details")
        self.error("recovery_required", self.coordinator.request_sleep)
        self.assertEqual(self.adapter.saved.phase, Phase.RECOVERY_REQUIRED)
        self.assertTrue(self.adapter.fenced)
        self.error("invalid_transition", self.coordinator.request_sleep)
        self.assertEqual(self.adapter.gates.count(Gate.SUBMIT_SLEEP), 1)

    def test_timeout_after_real_submission_requires_correlated_terminal_proof(self):
        def uncertain(request):
            acknowledgement = self.adapter(request)
            if request.gate is Gate.SUBMIT_SLEEP:
                raise TimeoutError("submission response lost")
            return acknowledgement

        coordinator = SuspendTransition("transition-timeout", uncertain)
        coordinator.prepare()
        self.error("recovery_required", coordinator.request_sleep)
        self.assertTrue(self.adapter.sleep_submitted)
        self.error("recovery_required", coordinator.abort)
        self.error("invalid_transition", coordinator.request_sleep)
        self.assertNotIn(Gate.RELEASE_FENCE, self.adapter.gates)
        self.adapter.resumed = True
        self.assertTrue(coordinator.resume().completed)
        self.assertEqual(self.adapter.gates.count(Gate.SUBMIT_SLEEP), 1)

    def test_lost_release_or_start_ack_reasserts_fence_and_stops_auth(self):
        for uncertain_gate in (Gate.RELEASE_FENCE, Gate.START_AUTH_LOCKED):
            with self.subTest(gate=uncertain_gate):
                adapter = Adapter()

                def uncertain(request):
                    acknowledgement = adapter(request)
                    if request.gate is uncertain_gate:
                        raise TimeoutError("side effect completed but reply lost")
                    return acknowledgement

                coordinator = SuspendTransition("transition-release", uncertain)
                coordinator.prepare()
                self.error("recovery_required", coordinator.abort)
                self.assertTrue(adapter.fenced)
                self.assertFalse(adapter.auth_running)
                self.assertEqual(adapter.saved.phase, Phase.RECOVERY_REQUIRED)

    def test_truthy_or_unbound_acknowledgements_rejected(self):
        for bad in (lambda request: True, lambda request: {"ok": True},
                    lambda request: Acknowledgement(request, True),
                    lambda request: Acknowledgement(request, "confirmed"),
                    lambda request: Acknowledgement(replace(request), Outcome.CONFIRMED),
                    lambda request: Acknowledgement(request, Outcome.REJECTED),
                    lambda request: None):
            with self.subTest(bad=bad):
                adapter = Adapter()
                adapter.failure_at = 5
                adapter.failure = bad
                coordinator = SuspendTransition("transition-0002", adapter)
                self.error("recovery_required", coordinator.prepare)
                self.assertNotIn(Gate.SUBMIT_SLEEP, adapter.gates)

    def test_failure_at_each_gate_never_leaves_success_or_released_fence(self):
        for position in range(1, 22):
            with self.subTest(position=position):
                adapter = Adapter()
                adapter.failure_at = position
                adapter.failure = TimeoutError()
                adapter.resumed = True
                coordinator = SuspendTransition("transition-0003", adapter)

                def run():
                    coordinator.prepare()
                    coordinator.request_sleep()
                    coordinator.resume()

                self.error("recovery_required", run)
                self.assertEqual(coordinator.snapshot.phase, Phase.RECOVERY_REQUIRED)
                self.assertFalse(coordinator.snapshot.completed)
                self.assertTrue(adapter.fenced)
                self.assertFalse(adapter.auth_running)

    def test_process_death_after_every_gate_requires_fresh_recovery_proof(self):
        for position in range(1, 22):
            with self.subTest(position=position):
                adapter = Adapter()
                adapter.die_after = position
                adapter.resumed = True
                coordinator = SuspendTransition("transition-0004", adapter)
                with self.assertRaises(ProcessDeath):
                    coordinator.prepare()
                    coordinator.request_sleep()
                    coordinator.resume()
                snapshot = adapter.saved or Snapshot("transition-0004")
                restored = SuspendTransition.restore(snapshot, adapter)
                self.assertEqual(restored.snapshot.phase, Phase.RECOVERY_REQUIRED)
                self.error("invalid_transition", restored.prepare)
                self.error("invalid_transition", restored.request_sleep)
                before = len(adapter.calls)
                result = restored.abort()
                self.assertTrue(result.completed)
                self.assertNotIn(Gate.SUBMIT_SLEEP, adapter.gates[before:])
                self.assertIn(Gate.VERIFY_ABORTED, adapter.gates[before:])

    def test_death_after_submission_does_not_assume_job_finished(self):
        self.adapter.die_after = 13
        with self.assertRaises(ProcessDeath):
            self.requested()
        restored = SuspendTransition.restore(self.adapter.saved, self.adapter)
        self.error("recovery_required", restored.abort)
        self.assertTrue(self.adapter.fenced)
        self.adapter.resumed = True
        self.assertTrue(restored.resume().completed)
        self.assertEqual(self.adapter.gates.count(Gate.SUBMIT_SLEEP), 1)

    def test_all_adapter_failures_remain_recovery_required(self):
        def broken(request):
            raise TimeoutError("private")

        coordinator = SuspendTransition("transition-0005", broken)
        self.error("recovery_required", coordinator.prepare)
        self.assertEqual(coordinator.snapshot.phase, Phase.RECOVERY_REQUIRED)
        self.error("invalid_transition", coordinator.request_sleep)

    def test_concurrent_calls_reject_busy_without_mutating_phase(self):
        entered = threading.Event()
        release = threading.Event()

        def blocked(request):
            if request.gate is Gate.QUIESCE:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError()
            return self.adapter(request)

        coordinator = SuspendTransition("transition-0006", blocked)
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(coordinator.prepare)
            self.assertTrue(entered.wait(1))
            try:
                self.error("busy", coordinator.abort)
                self.error("busy", coordinator.request_sleep)
            finally:
                release.set()
            self.assertTrue(future.result(timeout=1).prepared)

    def test_invalid_initial_sequence_has_no_effects(self):
        self.error("invalid_transition", self.coordinator.request_sleep)
        self.error("invalid_transition", self.coordinator.resume)
        self.assertEqual(self.adapter.calls, [])

    def test_invalid_configuration_and_snapshot(self):
        for identifier in ("short", "transition\n", 123, "x" * 129):
            self.error("invalid_configuration", lambda: SuspendTransition(identifier, self.adapter))
        for snapshot in (True, {"phase": "resumed"}, Snapshot("transition-1", phase="resumed"),
                         Snapshot("transition-1", revision=True), Snapshot("transition-1", revision=-1),
                         Snapshot("transition-1", completed=True),
                         Snapshot("transition-1", phase=Phase.SLEEP_REQUESTED, prepared=True)):
            self.error("invalid_snapshot", lambda: SuspendTransition.restore(snapshot, self.adapter))


if __name__ == "__main__":
    unittest.main()
