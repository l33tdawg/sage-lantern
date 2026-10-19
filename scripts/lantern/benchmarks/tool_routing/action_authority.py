"""Disabled Lantern lifecycle coordinator, with no session authentication route.

The trusted application must authenticate and resolve the complete scope before
activate(), and feed every lock, sleep, owner and consent revocation through this
same instance. An HTTP token/epoch snapshot alone cannot supply those guarantees.
No production service imports this candidate. Review/submit methods are trusted
application entry points, never model tools or request-payload authority.
"""
from dataclasses import dataclass
import math
import secrets
import threading
import time

from action_boundary import admit_current
from action_session import ActionRefused, AuthenticatedScope, SubmittedInstruction, fingerprint


PRIVATE_ACTIONS = frozenset({"search_memory", "save_private_note"})


@dataclass(frozen=True)
class _Review:
    scope: AuthenticatedScope
    text: str
    actions: frozenset
    expires_at: float


class ReviewedActionAuthority:
    def __init__(self, *, enabled=False, clock=time.monotonic, capacity=256):
        if (type(enabled) is not bool or not callable(clock)
                or type(capacity) is not int or not 1 <= capacity <= 1024):
            raise ValueError("invalid_authority_configuration")
        self.enabled, self.clock, self.capacity = enabled, clock, capacity
        self._gate = threading.Condition(threading.RLock())
        self._scope = None
        self._actions = frozenset()
        self._reviews, self._submissions, self._flight = {}, {}, {}
        self._epochs = set()
        self._issued = 0
        self._closed = self._drain_failed = False

    def activate(self, scope, *, allowed_actions):
        """Privileged integration seam; this method does NOT authenticate anyone.

        The upstream issuer must attest owner, session, domain, signer, endpoint,
        device and explicit private-action consent. No history/public-share
        preference supplies private-action consent. No live issuer is wired yet.
        """
        if (type(scope) is not AuthenticatedScope
                or type(scope.expires_at) not in (int, float)
                or not math.isfinite(scope.expires_at) or scope.expires_at <= self.clock()
                or any(type(v) is not str or not 1 <= len(v) <= 256
                       for k, v in scope.binding().items() if k != "expires_at")
                or type(allowed_actions) is not frozenset or not allowed_actions
                or not allowed_actions <= PRIVATE_ACTIONS):
            raise ActionRefused("invalid_private_action_session")
        epoch = fingerprint({"owner": scope.owner, "session": scope.session, "epoch": scope.epoch})
        with self._gate:
            if (self.enabled is not True or self._closed or self._drain_failed
                    or self._scope is not None or self._flight
                    or epoch in self._epochs or len(self._epochs) >= self.capacity):
                raise ActionRefused("authority_disabled_busy_closed_or_epoch_reused")
            self._epochs.add(epoch)
            self._scope, self._actions = scope, allowed_actions

    def _live(self):
        if (self.enabled is not True or self._closed or self._drain_failed
                or self._scope is None or self.clock() >= self._scope.expires_at):
            raise ActionRefused("private_action_session_unavailable")

    def prepare_review(self, text, *, source):
        """Receive exact text for the trusted review surface, without dispatch.

        source is trusted provenance, not an authentication flag. Final speech
        still needs human review; interim ASR and recalled content have no lane.
        """
        if (type(text) is not str or not text or len(text.encode()) > 16384
                or type(source) is not str or source not in {"typed", "final_speech"}):
            raise ActionRefused("current_final_instruction_required")
        decision = admit_current(text, allow_exact_grammar=True)
        actions = frozenset(c["name"] for c in decision["calls"])
        with self._gate:
            self._live()
            if not actions or not actions <= self._actions:
                raise ActionRefused("private_action_not_consented_or_unsupported")
            if self._issued >= self.capacity:
                raise ActionRefused("review_capacity_exhausted")
            self._issued += 1
            review_id = secrets.token_hex(32)
            self._reviews[review_id] = _Review(self._scope, text, actions,
                                             min(self._scope.expires_at, self.clock() + 30))
            return review_id

    def submit_review(self, review_id, *, reviewed_text, approve_private_write=False):
        """Consume one exact displayed review; an edit requires a fresh review.

        Write approval must come from the trusted human review handler. It is
        additional to the session's explicit private-write consent, never an
        attribute read from a transcript, model plan or recalled record.
        """
        with self._gate:
            self._live()
            review = self._reviews.pop(review_id, None)
            if (review is None or review.scope != self._scope
                    or self.clock() >= review.expires_at or reviewed_text != review.text
                    or type(reviewed_text) is not str or type(approve_private_write) is not bool
                    or ("save_private_note" in review.actions and approve_private_write is not True)):
                raise ActionRefused("review_changed_expired_consumed_or_write_unapproved")
            instruction = SubmittedInstruction(secrets.token_hex(32), review.text)
            self._submissions[instruction.request_id] = [review, "submitted"]
            return instruction

    def _check(self, scope, instruction):
        self._live()
        if (type(instruction) is not SubmittedInstruction
                or type(instruction.request_id) is not str or type(instruction.text) is not str):
            raise ActionRefused("reviewed_submission_required")
        row = self._submissions.get(instruction.request_id)
        if (row is None or row[1] not in {"submitted", "running"}
                or scope != self._scope or row[0].scope != scope
                or self.clock() >= row[0].expires_at or instruction.text != row[0].text
                or not row[0].actions <= self._actions):
            raise ActionRefused("submission_or_session_revoked_changed_or_consumed")
        return row

    def resolve(self, instruction):
        with self._gate:
            self._check(self._scope, instruction)
            return self._scope

    def check(self, scope, instruction):
        with self._gate:
            try:
                self._check(scope, instruction)
                return True
            except ActionRefused:
                return False

    def cancel(self, request_id):
        with self._gate:
            row = self._submissions.get(request_id)
            if row is not None:
                row[1] = "cancelled"
            self._gate.notify_all()

    def revoke(self):
        """Close admission immediately; cancellation/drain happens outside gate."""
        with self._gate:
            self._scope, self._actions = None, frozenset()
            self._reviews.clear()
            # Remove text from the coordinator when its session is revoked.
            self._submissions.clear()
            self._gate.notify_all()

    def close(self):
        with self._gate:
            self._closed = True
            self.revoke()

    def run_guarded(self, scope, instruction, callback):
        key = instruction.request_id
        with self._gate:
            row = self._check(scope, instruction)
            if row[1] != "submitted" or key in self._flight:
                raise ActionRefused("submission_already_started")
            row[1] = "running"
            self._flight[key] = threading.get_ident()
        try:
            # Network, verification and disk fsync never hold the lifecycle gate.
            result = callback()
            with self._gate:
                self._check(scope, instruction)
                row[1] = "completed"
                return result
        finally:
            with self._gate:
                if row[1] == "running":
                    row[1] = "uncertain"
                self._flight.pop(key, None)
                self._gate.notify_all()

    def dispatch_step(self, scope, instruction, callback):
        """Serialize one NONBLOCKING socket send with lifecycle revocation.

        A callback may send one bounded nonblocking chunk or publish to a local
        sink. It must not wait, perform fsync/network reads, or run model work.
        Any already admitted bytes remain uncertain after cancellation.
        """
        with self._gate:
            self._check(scope, instruction)
            if self._flight.get(instruction.request_id) != threading.get_ident():
                raise ActionRefused("dispatch_outside_current_guard")
            result = callback()
            self._check(scope, instruction)
            return result

    def publish(self, scope, instruction, callback):
        """Fence a trusted synchronous local sink; browser delivery needs epoch checks."""
        return self.dispatch_step(scope, instruction, callback)

    def drain(self, timeout):
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 <= timeout <= 5):
            raise ActionRefused("bounded_drain_timeout_required")
        deadline = time.monotonic() + timeout
        with self._gate:
            if self._scope is not None:
                raise ActionRefused("revoke_before_drain")
            while self._flight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._drain_failed = True
                    return False
                self._gate.wait(remaining)
            return not self._drain_failed
