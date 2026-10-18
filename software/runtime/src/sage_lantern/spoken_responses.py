"""The user's spoken-responses choice, persisted with the same discipline as the
lock and power preferences.

`settings_store.DraftSettingsStore` already persists this setting, and its
docstring is explicit that it is *"DRAFT ONLY … no apply handler"*. That store is
the right place for a draft; it is not a place a runtime can read an authoritative
value from, and it teaches nothing about how the value is revised. The lock and
power preferences in this package have a different, older discipline: a revision,
a compare-and-set write that refuses a stale one, an atomic replace, a lock, and
explicit POSIX safety checks. This mirrors that, because the spoken-responses
choice has the same shape as `lock_seconds` — one value the owner sets and the
runtime reads.

`LockSettingsStore` is the precedent: a `PowerSettingsStore` subclass overriding
`filename`, `validate`, `defaults` and `update`. This is deliberately the same
three-line shape rather than a new mechanism, because a second implementation of
revision-and-atomic-replace would be a second place for it to be wrong.

**There is no default that speaks.** `defaults()` is `spoken_responses = False`,
and the base refuses a record that does not validate, so an absent, unreadable or
malformed store resolves to silence with a stated reason — never to a device that
started talking because a file was missing.
"""

from __future__ import annotations

from pathlib import Path

from .power_settings import PowerSettingsStore, PowerRevisionConflict  # noqa: F401  (re-exported for callers)
from .settings import validate_spoken_policy


class SpokenResponsesStore(PowerSettingsStore):
    """Revisioned storage for the user's answer-read-aloud preference.

    Inherits flock serialization, uid/mode/link checks, `O_NOFOLLOW` opens, the
    atomic temp-file-then-replace write, the `MAX_REVISION` guard and the
    `authorize()` hook that is checked *before* the rename — so a refused
    authorization cannot commit.

    Like its base: **a failure after the replace may already have committed.**
    Reconcile a suspected failure by loading, never by retrying the write blind.
    """

    filename = "spoken-responses.json"
    validate = staticmethod(validate_spoken_policy)

    def defaults(self):
        # Not speaking is the default, not merely the initial value.
        return dict(revision=0, spoken_responses=False)

    def update(self, *, expected_revision: int, spoken_responses: bool, authorize=None) -> dict:
        candidate = validate_spoken_policy(
            dict(revision=expected_revision, spoken_responses=spoken_responses))
        return self._update(candidate, authorize)

    def __init__(self, directory: Path):
        super().__init__(directory)
