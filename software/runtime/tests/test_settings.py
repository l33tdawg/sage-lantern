import unittest
from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

from sage_lantern.settings import (
    OBSERVATION_MAX_AGE,
    REGISTRY,
    Implementation,
    ObservationState,
    ProjectionReason,
    SettingId,
    SettingKind,
    SettingObservation,
    project_settings,
)


NOW = datetime(2026, 9, 5, 8, tzinfo=UTC)


class SettingsTests(unittest.TestCase):
    def projection(self, setting_id=SettingId.HISTORY, **inputs):
        return next(
            row for row in project_settings(now=NOW, **inputs)
            if row.definition.setting_id is setting_id
        )

    def test_registry_is_bounded_and_immutable(self):
        self.assertEqual(set(REGISTRY), set(SettingId))
        self.assertEqual(REGISTRY[SettingId.RECOVERY].kind, SettingKind.ACTION)
        with self.assertRaises(TypeError):
            REGISTRY[SettingId.HISTORY] = REGISTRY[SettingId.VOICE]
        with self.assertRaises(FrozenInstanceError):
            REGISTRY[SettingId.HISTORY].implementation = Implementation.UNAVAILABLE

    def test_defaults_never_invent_effective_or_desired_state(self):
        rows = project_settings(now=NOW)
        self.assertIsInstance(rows, tuple)
        self.assertEqual(tuple(row.definition.setting_id for row in rows), tuple(REGISTRY))
        for row in rows:
            self.assertIsNone(row.desired)
            self.assertIsNone(row.effective)
            self.assertIsNone(row.observed_at)
            self.assertEqual(row.observation_state, ObservationState.UNKNOWN)
            self.assertTrue(row.read_only)
        self.assertEqual(self.projection().reason, ProjectionReason.NO_OBSERVATION)

    def test_intent_is_not_effective_state(self):
        row = self.projection(desired={SettingId.HISTORY: True})
        self.assertTrue(row.desired)
        self.assertIsNone(row.effective)
        self.assertEqual(row.observation_state, ObservationState.UNKNOWN)

    def test_fresh_observation_preserves_mismatch_and_false(self):
        for value in (True, False):
            with self.subTest(value=value):
                row = self.projection(
                    desired={SettingId.HISTORY: not value},
                    observed={SettingId.HISTORY: SettingObservation(value, NOW)},
                )
                self.assertIs(row.desired, not value)
                self.assertIs(row.effective, value)
                self.assertEqual(row.observation_state, ObservationState.FRESH)
                self.assertEqual(row.reason, ProjectionReason.OBSERVED)
                self.assertEqual(row.observed_at, NOW)

    def test_observation_does_not_invent_desired_state(self):
        for setting_id in (SettingId.HISTORY, SettingId.VOICE, SettingId.PHOTO, SettingId.TTS):
            row = self.projection(
                setting_id, observed={setting_id: SettingObservation(True, NOW)},
            )
            self.assertIsNone(row.desired)
            self.assertTrue(row.effective)

    def test_expiry_boundary_and_stale_values(self):
        for age, expected in (
            (OBSERVATION_MAX_AGE - timedelta(microseconds=1), ObservationState.FRESH),
            (OBSERVATION_MAX_AGE, ObservationState.STALE),
            (OBSERVATION_MAX_AGE + timedelta(days=1), ObservationState.STALE),
        ):
            with self.subTest(age=age):
                row = self.projection(observed={SettingId.HISTORY: SettingObservation(True, NOW - age)})
                self.assertEqual(row.observation_state, expected)
                if expected is ObservationState.STALE:
                    self.assertIsNone(row.effective)
                    self.assertEqual(row.reason, ProjectionReason.STALE_OBSERVATION)

    def test_explicit_unknown_and_future_observations(self):
        for observation, reason in (
            (SettingObservation(None, NOW), ProjectionReason.UNKNOWN_OBSERVATION),
            (SettingObservation(True, NOW + timedelta(microseconds=1)), ProjectionReason.FUTURE_OBSERVATION),
        ):
            row = self.projection(observed={SettingId.HISTORY: observation})
            self.assertIsNone(row.effective)
            self.assertEqual(row.observation_state, ObservationState.UNKNOWN)
            self.assertEqual(row.reason, reason)

    def test_equivalent_timezone_is_fresh(self):
        local_time = NOW.astimezone(timezone(timedelta(hours=8)))
        row = self.projection(observed={SettingId.HISTORY: SettingObservation(False, local_time)})
        self.assertIs(row.effective, False)
        self.assertEqual(row.observation_state, ObservationState.FRESH)

    def test_messaging_cannot_be_promoted_by_intent_or_observation(self):
        row = self.projection(
            SettingId.MESSAGING,
            desired={SettingId.MESSAGING: True},
            observed={SettingId.MESSAGING: SettingObservation(True, NOW)},
        )
        self.assertTrue(row.desired)
        self.assertIsNone(row.effective)
        self.assertIsNone(row.observed_at)
        self.assertEqual(row.observation_state, ObservationState.UNKNOWN)
        self.assertEqual(row.definition.implementation, Implementation.UNAVAILABLE)
        self.assertEqual(row.reason, ProjectionReason.UNSUPPORTED_MESSAGING)

    def test_recovery_is_unavailable_action_not_a_toggle(self):
        row = self.projection(SettingId.RECOVERY)
        self.assertEqual(row.definition.implementation, Implementation.UNAVAILABLE)
        self.assertEqual(row.reason, ProjectionReason.RECOVERY_UNAVAILABLE)
        for inputs in (
            {"desired": {SettingId.RECOVERY: True}},
            {"observed": {SettingId.RECOVERY: SettingObservation(True, NOW)}},
        ):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                project_settings(now=NOW, **inputs)

    def test_rejects_untyped_unknown_or_private_identifiers_without_echo(self):
        for identifier in ("local_history", "https://private.invalid", "/private/key", "private.service", "private-owner"):
            for field, value in (("desired", True), ("observed", SettingObservation(True, NOW))):
                with self.subTest(identifier=identifier, field=field), self.assertRaises(ValueError) as raised:
                    project_settings(now=NOW, **{field: {identifier: value}})
                self.assertNotIn(identifier, str(raised.exception))

    def test_strict_values_and_observation_shape(self):
        for value in (0, 1, "true", "private text", {}, [], None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.projection(desired={SettingId.HISTORY: value})
            if value is not None:
                with self.assertRaises(ValueError):
                    SettingObservation(value, NOW)
        for value in (True, None, {"value": True, "observed_at": NOW, "content": "private"}):
            with self.assertRaises(ValueError):
                self.projection(observed={SettingId.HISTORY: value})
        with self.assertRaises(TypeError):
            SettingObservation(True, NOW, owner="private")
        for field in ("desired", "observed"):
            with self.assertRaises(ValueError):
                project_settings(now=NOW, **{field: []})

    def test_rejects_naive_or_invalid_timestamps(self):
        for value in (NOW.replace(tzinfo=None), "private timestamp", None, 1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    project_settings(now=value)
                with self.assertRaises(ValueError):
                    SettingObservation(True, value)

    def test_projection_is_pure_and_inputs_outputs_are_independent(self):
        desired = {SettingId.HISTORY: True}
        observed = {SettingId.HISTORY: SettingObservation(False, NOW)}
        with patch("builtins.open", side_effect=AssertionError("unexpected file access")):
            rows = project_settings(now=NOW, desired=desired, observed=observed)
            self.assertEqual(rows, project_settings(now=NOW, desired=desired, observed=observed))
        self.assertEqual(desired, {SettingId.HISTORY: True})
        self.assertEqual(observed, {SettingId.HISTORY: SettingObservation(False, NOW)})
        desired.clear()
        observed.clear()
        self.assertTrue(rows[0].desired)
        self.assertIs(rows[0].effective, False)
        with self.assertRaises(FrozenInstanceError):
            rows[0].effective = True
        with self.assertRaises(FrozenInstanceError):
            rows[0].read_only = False
        self.assertEqual(set(asdict(rows[0])), {
            "definition", "desired", "effective", "observation_state", "reason", "observed_at", "read_only",
        })


if __name__ == "__main__":
    unittest.main()
