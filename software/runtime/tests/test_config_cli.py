import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sage_lantern import __version__
from sage_lantern.cli import main
from sage_lantern.config import RuntimeConfig
from sage_lantern.orchestrator import VoiceResult


class ConfigAndCliTests(unittest.TestCase):
    def test_protected_paths_are_explicit_and_canonical(self):
        template = (Path(__file__).resolve().parents[2] / 'config/lantern.example.toml').read_text()
        protected = template.replace('[sage]', '[sage]\nprotected_storage=true\nchain_id="sage-personal-test"\n'
                                     'conversation_dir="/var/lib/sage-lantern/history/conversations-v1"')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'lantern.toml'
            path.write_text(protected)
            self.assertEqual(RuntimeConfig.load(path).conversation_dir,
                             Path('/var/lib/sage-lantern/history/conversations-v1'))
            for field, original in (('conversation_dir', '/var/lib/sage-lantern/history/conversations-v1'),
                                    ('identity_path', '/var/lib/sage-lantern/identity/lantern-agent.key')):
                for value in ('', 'relative', '/var/lib/../legacy', '/var//lib/legacy'):
                    with self.subTest(field=field, value=value):
                        path.write_text(protected.replace(original, value))
                        with self.assertRaisesRegex(ValueError, 'explicit canonical'):
                            RuntimeConfig.load(path)
            path.write_text(template.replace('[sage]', '[sage]\nprotected_storage=true\nchain_id="sage-personal-test"'))
            with self.assertRaisesRegex(ValueError, 'conversation_dir'):
                RuntimeConfig.load(path)

    def test_relative_paths_resolve_from_config_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "lantern.toml"
            config_path.write_text(
                """
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
python = "venv/bin/python"
model = "models/voice.onnx"
[sage]
base_url = "http://127.0.0.1:8080"
[context]
static_items = ["offline fixture"]
""".strip(),
                encoding="utf-8",
            )
            config = RuntimeConfig.load(config_path)
            self.assertEqual(config.state_log, (root / "state/events.jsonl").resolve())
            self.assertEqual(config.whisper_binary, (root / "bin/whisper-cli").resolve())
            self.assertEqual(config.static_context, ["offline fixture"])

    def test_missing_required_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.toml"
            path.write_text("[runtime]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing configuration values"):
                RuntimeConfig.load(path)

    def test_cli_reports_version(self):
        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(stdout):
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), __version__)


class FlushedOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushed = []

    def flush(self):
        self.flushed.append(self.getvalue())


def test_voice_cli_default_retains_single_json_and_no_callback():
    result = VoiceResult("session", "question", "answer", None, {"total": 1.0})
    runner = Mock()
    runner.run_audio.return_value = result
    stdout = io.StringIO()
    with patch("sage_lantern.cli.RuntimeConfig.load"), patch("sage_lantern.cli.build_orchestrator", return_value=runner), contextlib.redirect_stdout(stdout):
        assert main(["voice", "--config", "config.toml", "--audio", "input.wav", "--session-id", "session"]) == 0
    payload = json.loads(stdout.getvalue())
    assert payload == {"session_id": "session", "transcript": "question", "response": "answer", "audio_path": None, "timings_ms": {"total": 1.0}, 'inference_metrics': None}
    assert "type" not in payload
    runner.run_audio.assert_called_once_with(Path("input.wav"), "session")


def test_voice_cli_events_are_individually_flushed_before_final_result():
    stdout = FlushedOutput()
    result = VoiceResult("session", "question", "hello world", None, {"llm_first_token": 1, "first_text": 3, "total": 5}, {'prompt_tokens': 20})

    class Runner:
        def run_audio(self, path, session_id, on_delta, on_progress):
            on_progress("transcribing")
            assert json.loads(stdout.flushed[0]) == {"type": "progress", "stage": "transcribing"}
            on_delta("hello ", "question")
            assert len(stdout.flushed) == 2
            assert json.loads(stdout.flushed[1].splitlines()[-1]) == {"type": "delta", "text": "hello ", "transcript": "question"}
            on_delta("world", "question")
            assert len(stdout.flushed) == 3
            return result

    with patch("sage_lantern.cli.RuntimeConfig.load"), patch("sage_lantern.cli.build_orchestrator", return_value=Runner()), contextlib.redirect_stdout(stdout):
        assert main(["voice", "--config", "config.toml", "--audio", "input.wav", "--events"]) == 0
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["type"] for event in events] == ["progress", "delta", "delta", "result"]
    assert events[-1]["result"] == {"session_id": "session", "transcript": "question", "response": "hello world", "audio_path": None, "timings_ms": result.timings_ms, 'inference_metrics': result.inference_metrics}
    assert len(stdout.flushed) == 4


def test_voice_cli_no_speech_emits_safe_error():
    from sage_lantern.adapters import NoSpeechDetected
    stdout = FlushedOutput()
    runner = Mock()
    runner.run_audio.side_effect = NoSpeechDetected("private detail")
    with patch("sage_lantern.cli.RuntimeConfig.load"), patch("sage_lantern.cli.build_orchestrator", return_value=runner), contextlib.redirect_stdout(stdout):
        assert main(["voice", "--config", "config.toml", "--audio", "input.wav", "--events"]) == 2
    assert json.loads(stdout.getvalue()) == {"type": "error", "code": "no_spoken_question"}


def test_voice_cli_events_failure_has_no_final_result():
    stdout, stderr = FlushedOutput(), io.StringIO()

    class Runner:
        def run_audio(self, path, session_id, on_delta, on_progress):
            on_delta("partial", "question")
            raise RuntimeError("stream failed")

    with patch("sage_lantern.cli.RuntimeConfig.load"), patch("sage_lantern.cli.build_orchestrator", return_value=Runner()), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert main(["voice", "--config", "config.toml", "--audio", "input.wav", "--events"]) == 2
    assert [json.loads(line)["type"] for line in stdout.getvalue().splitlines()] == ["delta"]
    assert "stream failed" in stderr.getvalue()


def test_voice_cli_events_without_streaming_support_has_no_answering_stage(tmp_path):
    from sage_lantern.orchestrator import VoiceOrchestrator
    from sage_lantern.state import JsonlEventLog, StateMachine

    class STT:
        def transcribe(self, path):
            return "question"

    class Context:
        def recall(self, query):
            return []

    class Model:
        def respond(self, query, context):
            return "answer"

    runner = VoiceOrchestrator(StateMachine(JsonlEventLog(tmp_path / "events.jsonl")), STT(), Context(), Model(), None, tmp_path)
    stdout = FlushedOutput()
    with patch("sage_lantern.cli.RuntimeConfig.load"), patch("sage_lantern.cli.build_orchestrator", return_value=runner), contextlib.redirect_stdout(stdout):
        assert main(["voice", "--config", "config.toml", "--audio", "input.wav", "--events"]) == 0
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["stage"] for event in events[:-1]] == ["transcribing", "recalling", "generating"]
    assert events[-1]["type"] == "result"
    assert events[-1]["result"]["response"] == "answer"
    assert "llm_first_token" not in events[-1]["result"]["timings_ms"]
    assert "first_text" not in events[-1]["result"]["timings_ms"]
    assert len(stdout.flushed) == 4


if __name__ == "__main__":
    unittest.main()
