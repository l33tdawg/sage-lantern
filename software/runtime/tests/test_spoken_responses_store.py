"""Persisting the user's answer-read-aloud choice.

`test_spoken_responses.py` covers the decision and `test_audio_config.py` the
operator keys; this covers the storage between them, on the same
revision-and-atomic-replace discipline as the lock and power preferences.

Nothing here opens an audio device or reads a real settings directory.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from sage_lantern.power_settings import PowerRevisionConflict, PowerSettingsError
from sage_lantern.settings import (
    SettingId,
    playback_decision,
    spoken_responses_enabled,
    spoken_responses_intent,
    validate_spoken_policy,
)
from sage_lantern.spoken_responses import SpokenResponsesStore


class SpokenResponsesStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve() / "spoken"
        self.store = SpokenResponsesStore(self.directory)

    def update(self, **changes):
        values = dict(expected_revision=0, spoken_responses=True)
        values.update(changes)
        return self.store.update(**values)

    # --- the default must never speak ------------------------------------

    def test_an_absent_store_defaults_to_not_speaking(self):
        self.assertEqual(self.store.load(), dict(revision=0, spoken_responses=False))

    def test_the_default_survives_the_decision_path(self):
        """Absent storage must resolve to silence, not to a speaking device."""
        loaded = self.store.load()
        self.assertFalse(spoken_responses_enabled(spoken_responses_intent(loaded)))
        self.assertFalse(playback_decision(
            engine_enabled=True, device="hw:X", desired=spoken_responses_intent(loaded)).enabled)

    # --- round trip and revisioning --------------------------------------

    def test_a_change_is_durable_and_revisioned(self):
        result = self.update()
        self.assertEqual((result["revision"], result["spoken_responses"]), (1, True))
        self.assertEqual(SpokenResponsesStore(self.directory).load(), result)
        self.assertTrue(spoken_responses_enabled(spoken_responses_intent(result)))

    def test_turning_it_back_off_is_also_durable(self):
        self.update()
        off = self.store.update(expected_revision=1, spoken_responses=False)
        self.assertEqual(off["revision"], 2)
        self.assertFalse(spoken_responses_enabled(spoken_responses_intent(off)))

    def test_a_stale_revision_is_refused_and_changes_nothing(self):
        first = self.update()
        with self.assertRaises(PowerRevisionConflict):
            self.store.update(expected_revision=0, spoken_responses=False)
        self.assertEqual(self.store.load(), first, "a refused write must not alter the record")

    def test_the_stored_file_is_owner_only(self):
        self.update()
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.directory / "spoken-responses.json").stat().st_mode), 0o600)

    # --- authorization gates the rename ----------------------------------

    def test_a_refused_authorization_does_not_commit(self):
        with self.assertRaises(PowerSettingsError):
            self.update(authorize=lambda: False)
        self.assertEqual(self.store.load(), dict(revision=0, spoken_responses=False),
                         "a refused authorization must leave the record untouched")

    def test_an_authorization_that_is_not_true_does_not_commit(self):
        for value in (None, 1, "yes"):
            with self.assertRaises(PowerSettingsError):
                self.update(authorize=lambda v=value: v)
            self.assertEqual(self.store.load()["revision"], 0)

    # --- what is refused --------------------------------------------------

    def test_a_non_boolean_is_refused(self):
        for value in (1, 0, "true", "on", [], None):
            with self.assertRaises(PowerSettingsError):
                self.update(spoken_responses=value)
        self.assertEqual(self.store.load()["revision"], 0)

    def test_an_extra_field_is_refused(self):
        with self.assertRaises(PowerSettingsError):
            validate_spoken_policy(dict(revision=0, spoken_responses=True, extra=1))

    def test_a_missing_field_is_refused(self):
        with self.assertRaises(PowerSettingsError):
            validate_spoken_policy(dict(revision=0))

    def test_a_negative_or_oversized_revision_is_refused(self):
        for revision in (-1, 1 << 53):
            with self.assertRaises(PowerSettingsError):
                validate_spoken_policy(dict(revision=revision, spoken_responses=True))

    def test_a_malformed_file_is_refused_rather_than_read_as_off(self):
        """An unreadable preference and a preference of 'no' are different facts."""
        self.directory.mkdir(mode=0o700, exist_ok=True)
        path = self.directory / "spoken-responses.json"
        path.write_text('{"version": 1, "revision": 0, "spoken_responses": "yes"}', encoding="utf-8")
        path.chmod(0o600)
        with self.assertRaises(PowerSettingsError):
            self.store.load()

    def test_a_file_that_is_not_json_is_refused(self):
        self.directory.mkdir(mode=0o700, exist_ok=True)
        path = self.directory / "spoken-responses.json"
        path.write_text("not json at all", encoding="utf-8")
        path.chmod(0o600)
        with self.assertRaises(PowerSettingsError):
            self.store.load()

    def test_a_world_readable_file_is_refused(self):
        self.update()
        (self.directory / "spoken-responses.json").chmod(0o644)
        with self.assertRaises(PowerSettingsError):
            self.store.load()

    def test_an_overlong_file_is_refused(self):
        self.directory.mkdir(mode=0o700, exist_ok=True)
        path = self.directory / "spoken-responses.json"
        path.write_bytes(b"x" * 2048)
        path.chmod(0o600)
        with self.assertRaises(PowerSettingsError):
            self.store.load()

    def test_a_symlinked_file_is_refused(self):
        self.directory.mkdir(mode=0o700, exist_ok=True)
        target = Path(self.temporary.name) / "elsewhere.json"
        target.write_text('{"version": 1, "revision": 0, "spoken_responses": true}', encoding="utf-8")
        target.chmod(0o600)
        os.symlink(target, self.directory / "spoken-responses.json")
        with self.assertRaises(PowerSettingsError):
            self.store.load()

    # --- the reader that feeds the decision -------------------------------

    def test_the_reader_accepts_a_stored_record(self):
        self.assertEqual(spoken_responses_intent(dict(spoken_responses=True)), {SettingId.TTS: True})
        self.assertEqual(spoken_responses_intent(dict(spoken_responses=False)), {SettingId.TTS: False})

    def test_the_reader_refuses_a_record_with_no_value(self):
        for value in ({}, dict(revision=0), None, "true"):
            with self.assertRaises(ValueError, msg=repr(value)):
                spoken_responses_intent(value)

    def test_the_reader_refuses_a_non_boolean_value(self):
        for value in (1, 0, "true", [], None):
            with self.assertRaises(ValueError, msg=repr(value)):
                spoken_responses_intent(dict(spoken_responses=value))

    def test_a_stored_yes_reaches_the_speaker_and_a_stored_no_does_not(self):
        """The whole seam, end to end through the decision."""
        on = self.update()
        self.assertTrue(playback_decision(
            engine_enabled=True, device="hw:X", desired=spoken_responses_intent(on)).enabled)
        off = self.store.update(expected_revision=1, spoken_responses=False)
        self.assertFalse(playback_decision(
            engine_enabled=True, device="hw:X", desired=spoken_responses_intent(off)).enabled)

    def test_a_stored_yes_still_cannot_speak_without_an_engine_or_device(self):
        on = self.update()
        intent = spoken_responses_intent(on)
        self.assertFalse(playback_decision(
            engine_enabled=False, device="hw:X", desired=intent).enabled)
        self.assertFalse(playback_decision(
            engine_enabled=True, device=None, desired=intent).enabled)


if __name__ == "__main__":
    unittest.main()
