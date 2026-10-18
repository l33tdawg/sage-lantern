"""When a user flips the switch, when does the device start speaking?

This is a question the design left open, and leaving it unstated would let two
people assume opposite answers. The answer is fixed by *where* the preference is
read, so it is worth pinning rather than describing.

`build_orchestrator` reads the stored preference **once**, when the orchestrator is
built. Therefore:

* a change takes effect on the **next build**, which is the next start of the voice
  loop — not in the middle of a turn, and not mid-session on a process already
  running;
* a turn already in flight is unaffected, because the player it was given was
  decided before the turn began.

That is a deliberate consequence, not an oversight: re-reading the store per turn
would put file I/O and lock acquisition inside the answering path, and would let a
settings write change behaviour between two turns of the same conversation. The
cost is that turning speech on needs the loop restarted, and this file records that
cost where it can be seen.

No audio device is opened and no model runs.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sage_lantern.config import RuntimeConfig
from sage_lantern.power_settings import PowerRevisionConflict
from sage_lantern.settings import SettingId, playback_decision
from sage_lantern.spoken_responses import SpokenResponsesStore


CONFIG = """
[runtime]
state_log = "state/events.jsonl"
output_dir = "audio"
[stt]
binary = "bin/whisper-cli"
model = "models/whisper.bin"
[llm]
base_url = "http://127.0.0.1:8081"
model = "test-model"
[tts]
enabled = true
python = "venv/bin/python"
model = "models/voice.onnx"
[sage]
base_url = "http://127.0.0.1:8080"
[context]
static_items = ["offline fixture"]
"""


class Player:
    """Stands in for AplayPlayback; records construction, not playback."""

    built = []

    def __init__(self, device, *, target_rate=None, **kwargs):
        # Mirrors the real AplayPlayback signature, so a new keyword added to the
        # production construction site cannot slip past these tests unnoticed.
        self.device = device
        self.target_rate = target_rate
        self.kwargs = kwargs
        Player.built.append(self)


class EffectOfAChangeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        Player.built = []
        self.spoken = self.root / "spoken"
        for name in ("state", "audio", "bin", "models", "venv/bin"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in ("bin/whisper-cli", "models/whisper.bin", "models/voice.onnx", "venv/bin/python"):
            (self.root / name).write_bytes(b"")
        self.path = self.root / "lantern.toml"
        self.path.write_text(
            CONFIG + f'[audio]\nenabled = true\ndevice = "hw:X,DEV=0"\nspoken_dir = "{self.spoken}"\n',
            encoding="utf-8")
        self.store = SpokenResponsesStore(self.spoken)

    def build(self):
        """Build the orchestrator the way the CLI does, patching only the engines."""
        from sage_lantern.cli import build_orchestrator

        config = RuntimeConfig.load(self.path)
        with patch("sage_lantern.cli.AplayPlayback", Player), \
             patch("sage_lantern.cli.PiperTTS"), \
             patch("sage_lantern.cli.WhisperCppSTT"), \
             patch("sage_lantern.cli.LlamaCppHTTP"):
            return build_orchestrator(config)

    def intent(self):
        from sage_lantern.cli import spoken_intent

        return spoken_intent(RuntimeConfig.load(self.path))

    # --- the answer -------------------------------------------------------

    def test_a_fresh_device_does_not_speak(self):
        """Configured store, nothing written yet: reads as an explicit no.

        Not `None` — the store *is* configured, so the honest reading is a user
        layer that exists and has not asked for speech. (`None` is reserved for no
        user layer at all, which the audio-config tests cover.)
        """
        self.assertEqual(self.intent(), {SettingId.TTS: False})
        self.assertIsNone(self.build().playback, "an untouched device must not get a player")

    def test_turning_it_on_takes_effect_on_the_next_build(self):
        self.build()
        self.assertEqual(len(Player.built), 0)
        self.store.update(expected_revision=0, spoken_responses=True)
        self.assertTrue(playback_decision(
            engine_enabled=True, device="hw:X,DEV=0", desired=self.intent()).enabled)
        self.assertIsNotNone(self.build().playback, "the rebuilt loop must speak")
        self.assertEqual(len(Player.built), 1)

    def test_turning_it_off_also_takes_effect_on_the_next_build(self):
        self.store.update(expected_revision=0, spoken_responses=True)
        self.assertIsNotNone(self.build().playback)
        self.store.update(expected_revision=1, spoken_responses=False)
        self.assertIsNone(self.build().playback)

    def test_an_orchestrator_already_built_is_unaffected_by_a_later_change(self):
        """The read happens at build time, so a running loop keeps its decision."""
        live = self.build()
        self.assertIsNone(live.playback)
        self.store.update(expected_revision=0, spoken_responses=True)
        self.assertIsNone(live.playback, "a built orchestrator must not change under itself")
        self.assertIsNotNone(self.build().playback, "a fresh build sees the new value")

    def test_revisions_are_ordered_and_a_replayed_write_is_refused(self):
        """Replaying an old switch position must not resurrect it."""
        first = self.store.update(expected_revision=0, spoken_responses=True)
        second = self.store.update(expected_revision=first["revision"], spoken_responses=False)
        self.assertEqual((first["revision"], second["revision"]), (1, 2))
        with self.assertRaises(PowerRevisionConflict):
            self.store.update(expected_revision=first["revision"], spoken_responses=True)
        self.assertFalse(self.intent()[SettingId.TTS], "the refused replay must not win")

    def test_the_last_write_wins_on_the_next_build(self):
        self.store.update(expected_revision=0, spoken_responses=True)
        self.store.update(expected_revision=1, spoken_responses=False)
        self.store.update(expected_revision=2, spoken_responses=True)
        self.assertIsNotNone(self.build().playback)

    def test_a_change_does_not_disturb_the_operator_audio_settings(self):
        """The preference is its own file; the operator keys stay in the config."""
        self.store.update(expected_revision=0, spoken_responses=True)
        config = RuntimeConfig.load(self.path)
        self.assertTrue(config.audio_enabled)
        self.assertEqual(config.audio_device, "hw:X,DEV=0")
        self.assertTrue(config.tts_enabled)
        self.assertEqual(self.store.load()["revision"], 1)


if __name__ == "__main__":
    unittest.main()
