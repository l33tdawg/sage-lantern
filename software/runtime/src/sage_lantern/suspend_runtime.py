"""Connect the durable transition to a trusted appliance implementation."""

from .suspend_transition import (
    Acknowledgement, Gate, Outcome, SuspendTransition, SuspendTransitionError,
)


class SleepCycle:
    def __init__(self, journal, platform, transition_id):
        self.journal = journal
        self.platform = platform
        self.transition = SuspendTransition(transition_id, self.perform)
        self.terminal_verified = False

    def perform(self, request):
        gate = request.gate
        confirmed = False
        if gate is Gate.RETAIN_FENCE:
            confirmed = self.platform.retain_fence() is True
            if confirmed:
                self.journal.retain_fence(request.snapshot.transition_id)
        elif gate is Gate.CHECKPOINT:
            self.journal.checkpoint(request.snapshot)
            confirmed = True
        elif gate is Gate.STOP_AUTH:
            confirmed = self.platform.stop_auth() is True
        elif gate is Gate.AUTH_INACTIVE:
            confirmed = self.platform.auth_inactive() is True
        elif gate is Gate.QUIESCE:
            confirmed = self.platform.quiesce() is True
        elif gate is Gate.IDLE_ELIGIBLE:
            confirmed = self.platform.idle_eligible() is True
        elif gate is Gate.SUBMIT_SLEEP:
            confirmed = self.platform.sleep() is True
        elif gate in (Gate.VERIFY_RESUMED, Gate.VERIFY_ABORTED):
            verify = self.platform.resumed if gate is Gate.VERIFY_RESUMED else self.platform.aborted
            confirmed = verify() is True
            self.terminal_verified = confirmed
        elif gate is Gate.RELEASE_FENCE:
            if self.terminal_verified:
                self.journal.clear(request.snapshot, verified=True)
                confirmed = self.platform.release_fence() is True
        elif gate is Gate.START_AUTH_LOCKED:
            confirmed = self.platform.start_locked() is True
        return Acknowledgement(request, Outcome.CONFIRMED if confirmed else Outcome.UNKNOWN)

    def run(self):
        if self.journal.load().needs_recovery or self.platform.fenced():
            raise SuspendTransitionError("existing_recovery_required")
        try:
            self.transition.prepare()
            self.transition.request_sleep()
            return self.transition.resume()
        except SuspendTransitionError:
            if self.platform.aborted() is True:
                return self.transition.abort()
            raise
