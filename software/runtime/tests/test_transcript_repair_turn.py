"""The confusion table has to fire inside a real turn, not only in isolation.

A repair that is correct in a unit test but never reached, or that runs after the
model has already read the question, would leave the device exactly as it was.
These cases drive VoiceOrchestrator with a recogniser that returns a recorded
mishearing and watch three things: what the language model was asked, what the
state log recorded, and that the timing stays numeric.
"""
import json
import unittest

from sage_lantern.orchestrator import VoiceOrchestrator
from sage_lantern.state import JsonlEventLog, StateMachine


class Heard:
    """A recogniser that returns whatever was set up, like the real one."""

    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe(self, audio_path):
        self.calls += 1
        return self.text


class Context:
    def recall(self, query):
        return []


class Recorder:
    """Answers and remembers exactly what it was asked."""

    def __init__(self):
        self.asked = []

    def respond(self, query, context):
        self.asked.append(query)
        return "Paris."


class TTS:
    def synthesize(self, text, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFFfake")


class Player:
    def play(self, audio_path):
        return self

    def wait(self):
        return None


def turn(tmp_path, heard, *, supplied=None):
    """One turn, either recognised from audio or supplied as text by the user."""
    log = JsonlEventLog(tmp_path / "state.jsonl")
    llm = Recorder()
    orchestrator = VoiceOrchestrator(
        StateMachine(log), Heard(heard), Context(), llm, TTS(), tmp_path, None,
        playback=Player())
    audio = tmp_path / "question.wav"
    audio.write_bytes(b"RIFFfake")
    if supplied is None:
        result = orchestrator.run_audio(audio)
    else:
        result = orchestrator.run_text(supplied)
    details = [json.loads(line)["detail"] for line in
               (tmp_path / "state.jsonl").read_text().splitlines()]
    return result, llm, details


class RepairRunsBeforeTheModel(unittest.TestCase):

    def test_a_recorded_mishearing_reaches_the_model_repaired(self):
        for heard, expected in (
            ("What is the population of Prince?", "What is the population of France?"),
            ("What is the capital of Rams?", "What is the capital of France?"),
            ("Describe first.", "Describe France."),
        ):
            with self.subTest(heard=heard):
                import tempfile
                from pathlib import Path
                with tempfile.TemporaryDirectory() as directory:
                    result, llm, _details = turn(Path(directory), heard)
                self.assertEqual(llm.asked, [expected])
                self.assertEqual(result.transcript, expected)

    def test_a_clean_question_is_passed_through_untouched(self):
        import tempfile
        from pathlib import Path
        for sentence in ("Tell me about friends.",
                         "How many people live in Paris?",
                         "What is the capital of Germany?"):
            with self.subTest(sentence=sentence):
                with tempfile.TemporaryDirectory() as directory:
                    result, llm, details = turn(Path(directory), sentence)
                self.assertEqual(result.transcript, sentence)
                self.assertEqual(llm.asked, [sentence])
                self.assertNotIn("repaired:", " ".join(details))

    def test_the_state_log_names_the_rules_that_fired(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            _result, _llm, details = turn(Path(directory), "What is the population of Prince?")
        repaired = [detail for detail in details if detail.startswith("repaired:")]
        self.assertEqual(repaired, ["repaired:population-of-france"])

    def test_a_supplied_question_is_never_rewritten(self):
        # A question the user typed, or corrected by hand on the review screen,
        # belongs to them: run_text is the supplied-text path. The table is for
        # recognised speech only, and "Describe Brown." is a thing a person can
        # legitimately type.
        for supplied in ("Describe Brown.", "What is the population of Brown?"):
            with self.subTest(supplied=supplied):
                import tempfile
                from pathlib import Path
                with tempfile.TemporaryDirectory() as directory:
                    result, llm, details = turn(Path(directory), "ignored", supplied=supplied)
                self.assertEqual(result.transcript, supplied)
                self.assertEqual(llm.asked, [supplied])
                self.assertFalse([d for d in details if d.startswith("repaired:")])

    def test_repairing_adds_no_timing_key(self):
        # The timings table is a millisecond contract that other tests pin. A
        # regex over one short string does not belong in it, and which rules
        # fired is recorded in the state log instead.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            repaired, _llm, _details = turn(Path(directory), "What is the capital of Rams?")
        with tempfile.TemporaryDirectory() as directory:
            clean, _llm, _details = turn(Path(directory), "What is the capital of Germany?")
        self.assertEqual(set(repaired.timings_ms), set(clean.timings_ms))
        self.assertNotIn("repair", repaired.timings_ms)


if __name__ == "__main__":
    unittest.main()
