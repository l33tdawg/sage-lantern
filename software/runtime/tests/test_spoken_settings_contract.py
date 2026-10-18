"""The seam between the Settings control and the runtime, frozen as a contract.

Everything in this goal has been tested on one side of a boundary or the other: the
runtime accepts a revisioned record and turns it into playback, and the app's client
pattern (`deviceAuth('settings/power/get', { token })`) is known from inspection.
**Nothing tested that what a client sends is what the runtime accepts.** That is the
seam the user-facing switch will be built on, and the one place where two lanes can
each be correct while disagreeing.

So this file freezes the wire contract and pins it to the runtime's own validators.
It is deliberately a *specification*, not an implementation: the client helper
belongs to the app lane, and `apps/lantern-fleet-admin/lib/device-session.ts` is
dirty with another session's in-flight work (session-epoch fencing), so this is
written where it can be tested without moving bytes under that work.

**Synthetic.** Nothing here contacts a device, a broker or a socket. These are the
JSON documents the two sides exchange and the runtime functions that judge them.

The frozen contract, derived from `validate_spoken_policy` and the `power`
precedent rather than invented:

* the client calls ``settings/spoken/get`` with ``{"token": ...}``;
* the client calls ``settings/spoken/set`` with
  ``{"token", "expected_revision", "spoken_responses"}`` — a compare-and-set, so a
  stale write is refused rather than winning a race;
* the broker's reply carries **exactly** ``revision`` and ``spoken_responses``;
* ``spoken_responses`` is a real boolean — never ``1``, ``0`` or ``"true"``.
"""

import json
import tempfile
import unittest
from pathlib import Path

from sage_lantern.power_settings import MAX_REVISION, PowerSettingsError
from sage_lantern.settings import (
    SettingId,
    playback_decision,
    spoken_responses_enabled,
    spoken_responses_intent,
    validate_spoken_policy,
)


# --- the contract in one place, so both sides can cite the same strings ---

SPOKEN_GET_ACTION = "settings/spoken/get"
SPOKEN_SET_ACTION = "settings/spoken/set"
SPOKEN_REPLY_KEYS = frozenset({"revision", "spoken_responses"})


def client_get_request(token: str) -> dict:
    """Exactly what the app must send to read the preference."""
    return {"token": token}


def client_set_request(token: str, *, expected_revision: int, spoken: bool) -> dict:
    """Exactly what the app must send to change it."""
    return {
        "token": token,
        "expected_revision": expected_revision,
        "spoken_responses": spoken,
    }


def broker_reply(revision: int, spoken: bool) -> dict:
    """Exactly what the broker must return — no extra keys, no coercion."""
    return {"revision": revision, "spoken_responses": spoken}


class TheWireContractTests(unittest.TestCase):
    def test_the_request_carries_exactly_the_frozen_keys(self):
        self.assertEqual(set(client_get_request("t")), {"token"})
        self.assertEqual(
            set(client_set_request("t", expected_revision=3, spoken=True)),
            {"token", "expected_revision", "spoken_responses"},
        )

    def test_the_reply_shape_is_the_runtimes_own_required_shape(self):
        """The authority is `validate_spoken_policy`, not this file."""
        reply = broker_reply(4, True)
        self.assertEqual(set(reply), SPOKEN_REPLY_KEYS)
        self.assertEqual(validate_spoken_policy(reply), reply)

    def test_a_round_trip_reply_reaches_playback_without_translation(self):
        """The point of the seam: what the broker returns is what playback consumes."""
        checked = validate_spoken_policy(broker_reply(1, True))
        intent = spoken_responses_intent(checked)
        self.assertTrue(spoken_responses_enabled(intent))
        decision = playback_decision(engine_enabled=True, device="hw:4,0", desired=intent)
        self.assertTrue(decision.enabled, "a stored yes must reach the player")

    def test_a_stored_no_reaches_playback_as_silence(self):
        intent = spoken_responses_intent(validate_spoken_policy(broker_reply(2, False)))
        decision = playback_decision(engine_enabled=True, device="hw:4,0", desired=intent)
        self.assertFalse(decision.enabled)

    def test_the_default_record_a_fresh_device_holds_is_valid(self):
        """The store's own defaults must satisfy its validator, or nothing loads."""
        from sage_lantern.spoken_responses import SpokenResponsesStore

        # Resolved, not merely temporary: the store refuses a directory whose
        # resolved path differs from itself, and on macOS /var is a symlink to
        # /private/var, so a raw TemporaryDirectory path is correctly rejected.
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        store = SpokenResponsesStore(root / "spoken")
        defaults = store.defaults()
        self.assertEqual(validate_spoken_policy(defaults), defaults)
        self.assertFalse(defaults["spoken_responses"], "spoken responses must default off")
        # And the store agrees with its own default before anything is written —
        # except for the directory, which it creates on first use.
        store.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.assertEqual(store.load(), defaults)

    def test_the_wire_field_and_the_setting_id_agree(self):
        """`SettingId.TTS` is the runtime's name; the wire says `spoken_responses`.

        Both are legitimate, but if they diverged silently the switch would write a
        field nothing reads. Pin the correspondence here.
        """
        self.assertEqual(SettingId.TTS.value, "spoken_responses")
        self.assertIn("spoken_responses", SPOKEN_REPLY_KEYS)

    def test_the_actions_follow_the_existing_settings_namespace(self):
        for action in (SPOKEN_GET_ACTION, SPOKEN_SET_ACTION):
            self.assertTrue(action.startswith("settings/"), action)
            self.assertFalse(action.endswith("/"), action)


class WhatTheRuntimeRefusesTests(unittest.TestCase):
    """A client sending any of these must expect an error, not silent success."""

    def test_coerced_booleans_are_refused(self):
        for wrong in (1, 0, "true", "false", None, [], {}):
            with self.subTest(wrong=wrong), self.assertRaises(PowerSettingsError):
                validate_spoken_policy({"revision": 1, "spoken_responses": wrong})

    def test_a_missing_extra_or_renamed_field_is_refused(self):
        for wrong in (
            {"revision": 1},
            {"spoken_responses": True},
            {"revision": 1, "spoken_responses": True, "extra": 1},
            {"revision": 1, "spoken": True},
        ):
            with self.subTest(wrong=wrong), self.assertRaises(PowerSettingsError):
                validate_spoken_policy(wrong)

    def test_an_out_of_range_or_non_integer_revision_is_refused(self):
        for revision in (-1, MAX_REVISION + 1, 1.0, "1", None, True):
            with self.subTest(revision=revision), self.assertRaises(PowerSettingsError):
                validate_spoken_policy({"revision": revision, "spoken_responses": True})

    def test_the_revision_ceiling_is_the_shared_one(self):
        accepted = validate_spoken_policy({"revision": MAX_REVISION, "spoken_responses": True})
        self.assertEqual(accepted["revision"], MAX_REVISION)

    def test_a_reply_that_is_not_an_object_is_refused(self):
        for wrong in (None, [], "ok", 1, True):
            with self.subTest(wrong=wrong), self.assertRaises(PowerSettingsError):
                validate_spoken_policy(wrong)

    def test_the_intent_reader_refuses_a_record_with_no_boolean(self):
        """An unreadable preference is not a preference of 'no'."""
        for wrong in ({}, {"revision": 3}, {"revision": 3, "spoken_responses": 1}, None, []):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                spoken_responses_intent(wrong)


class ItSurvivesJsonTests(unittest.TestCase):
    """The contract crosses a JSON boundary, so test it as JSON, not as dicts."""

    def test_a_set_request_survives_json_byte_for_byte(self):
        request = client_set_request("a" * 43, expected_revision=7, spoken=True)
        self.assertEqual(json.loads(json.dumps(request)), request)
        self.assertIs(json.loads(json.dumps(request))["spoken_responses"], True)

    def test_a_reply_survives_json_and_is_accepted_on_the_other_side(self):
        reply = json.loads(json.dumps(broker_reply(7, False)))
        self.assertEqual(validate_spoken_policy(reply),
                         {"revision": 7, "spoken_responses": False})

    def test_json_cannot_turn_a_coerced_boolean_into_speech(self):
        """`spoken_responses` arriving as the string "true" must not speak."""
        for encoded in ('{"revision":1,"spoken_responses":"true"}',
                        '{"revision":1,"spoken_responses":1}'):
            with self.subTest(encoded=encoded), self.assertRaises(PowerSettingsError):
                validate_spoken_policy(json.loads(encoded))


class TheOtherSideOfTheSeamAgreesTests(unittest.TestCase):
    """Read the app's own source and check it uses these strings.

    Two tests that each pin a constant can both pass while disagreeing, because
    neither reads the other. This one reads the client module, so renaming an
    action on one side and not the other fails here rather than at a user's switch.
    """

    HELPER = (Path(__file__).resolve().parents[3]
              / "apps/lantern-fleet-admin/lib/spoken-responses.ts")

    def setUp(self):
        if not self.HELPER.exists():
            self.skipTest("the app lane is not present in this checkout")
        self.source = self.HELPER.read_text(encoding="utf-8")

    def test_the_client_calls_the_frozen_action_names(self):
        self.assertIn(f"'{SPOKEN_GET_ACTION}'", self.source)
        self.assertIn(f"'{SPOKEN_SET_ACTION}'", self.source)

    def test_the_client_sends_the_frozen_field_names(self):
        for field in ("token", "expected_revision", "spoken_responses"):
            with self.subTest(field=field):
                self.assertIn(field, self.source)

    def test_the_client_carries_no_field_the_runtime_would_refuse(self):
        """A reply with extra keys is refused, so the client must not expect any."""
        for stray in ("display_off_seconds", "deep_sleep_seconds", "lock_seconds",
                      "deep_sleep_available"):
            with self.subTest(stray=stray):
                self.assertNotIn(stray, self.source)

    def test_the_client_refuses_a_coerced_boolean_too(self):
        """Both sides must agree that only a real boolean is a preference."""
        self.assertIn("typeof result.spoken_responses !== 'boolean'", self.source)


if __name__ == "__main__":
    unittest.main()
