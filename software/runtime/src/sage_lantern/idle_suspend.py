"""Pure, fail-safe suspend eligibility; no clocks, files, RTC or external ops.

The trusted caller supplies monotonic seconds, X11 idle seconds and the exact
public auth locked boolean every five seconds. Never derive locked=True from
an authorization failure. Poll gaps over fifteen seconds reset all timing and
input-comparison evidence. Startup and evidence loss require fresh observation
for the configured delay (300, 600, 900 or 1800 seconds), alongside continuous
positive lock for that entire delay and X11 idle at least that long.

A True tick is itself the suspend attempt: the policy disarms BEFORE returning,
regardless of whether the caller executes, succeeds or fails. Rearming requires
a decrease between consecutive valid idle samples, then another full-delay
grace. An observed unlock transition also disarms. Input on later unlocked ticks
can rearm, but suspension still requires a subsequent continuous positive lock.
An idle decrease is only an input indication, not proof of human presence;
X11 counter resets also receive the full grace. Polling cannot see transitions
between snapshots. Construct once per runner lifetime; persistence and trusted
root execution are integration responsibilities, not provided here.

IdleSuspendPolicy() and tick keywords remain compatible, with a 300-second
default; the old thirty-second lock threshold is intentionally tightened.
Use IdleSuspendPolicy(delay_seconds=600, revision=0), or configure with those
keywords after reading saved policy. configure returns True for accepted input,
False for malformed input (which disables requests until valid configuration).
Revision is a nonnegative 63-bit integer, a change indicator, not authorization
or CAS. A changed revision OR delay resets evidence, never the one-shot latch.
Repeating the exact valid configuration is a no-op, safe on each polling tick.
"""

import math


IDLE_SECONDS = 300
LOCK_SECONDS = IDLE_SECONDS
DELAY_CHOICES = (300, 600, 900, 1800)
MAX_POLL_GAP = 15.0
POLL_SECONDS = 5.0


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


class IdleSuspendPolicy:
    """tick returns a one-shot request, never performs or confirms suspension."""

    def __init__(self, *, delay_seconds=IDLE_SECONDS, revision=0):
        self._last_time = None
        self._last_idle = None
        self._last_locked = None
        self._grace_since = None
        self._locked_since = None
        self._disarmed = False
        self._configuration = None
        self.configure(delay_seconds=delay_seconds, revision=revision)

    def configure(self, *, delay_seconds=IDLE_SECONDS, revision=0):
        """Accept trusted saved policy, invalidating old timing on any change."""
        if (type(delay_seconds) is not int or delay_seconds not in DELAY_CHOICES
                or type(revision) is not int or not 0 <= revision <= 2 ** 63 - 1):
            self._configuration = None
            self._reset_evidence()
            return False
        configuration = (delay_seconds, revision)
        if configuration != self._configuration:
            self._configuration = configuration
            self._reset_evidence()
        return True

    def _reset_evidence(self):
        self._last_time = None
        self._last_idle = None
        self._last_locked = None
        self._grace_since = None
        self._locked_since = None

    def tick(self, *, now=None, idle_seconds=None, locked=None):
        """Missing, invalid or stale snapshots deny; all times are seconds."""
        if (self._configuration is None or not _finite_number(now)
                or not _finite_number(idle_seconds) or type(locked) is not bool):
            self._reset_evidence()
            return False
        delay = self._configuration[0]
        if self._last_time is not None and not 0 < now - self._last_time <= MAX_POLL_GAP:
            self._reset_evidence()
        if self._grace_since is None:
            self._grace_since = now
        unlocked_transition = locked is False and self._last_locked is not False
        new_input = self._last_idle is not None and idle_seconds < self._last_idle
        if unlocked_transition:
            self._disarmed = True
            self._grace_since = now
        elif new_input:
            self._disarmed = False
            self._grace_since = now
        if locked is True:
            if self._locked_since is None:
                self._locked_since = now
        else:
            self._locked_since = None
        self._last_time = now
        self._last_idle = idle_seconds
        self._last_locked = locked
        if (self._disarmed or locked is not True or idle_seconds < delay
                or now - self._grace_since < delay
                or now - self._locked_since < delay):
            return False
        self._disarmed = True
        return True
