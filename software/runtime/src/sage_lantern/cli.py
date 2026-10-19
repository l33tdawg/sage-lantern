from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from . import __version__
from .adapters import AplayPlayback, LanternConversationContext, LlamaCppHTTP, NoSpeechDetected, PiperTTS, SageSDKContext, StaticContext, WhisperCppSTT
from .answer_verification import AnswerVerifier
from .config import RuntimeConfig
from .envelope import LanternEnvelope
from .orchestrator import VoiceOrchestrator, validated_question
from .power_settings import PowerSettingsError
from .public_queue import ProposalOutbox
from .settings import SettingId, playback_decision, spoken_responses_intent
from .spoken_responses import SpokenResponsesStore
from .state import JsonlEventLog, StateMachine
from .active_conversation import MAX_INPUT_BYTES, validate_messages


def _conversation_input(stream, *, text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('invalid_conversation_input')
            result[key] = value
        return result
    raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('conversation_input_too_large')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
    if type(value) is not dict or set(value) != {'question', 'messages'}:
        raise ValueError('invalid_conversation_input')
    question = validated_question(value['question']) if text else None
    if not text and value['question'] is not None:
        raise ValueError('unexpected_conversation_question')
    return question, validate_messages(value['messages'])


def spoken_intent(config: RuntimeConfig) -> Mapping[SettingId, bool] | None:
    """The user's stored choice, or `None` when there is no store to read.

    `None` and `{SettingId.TTS: False}` are deliberately different inputs: `None`
    means no user layer is configured at all, while an explicit `False` means a
    user turned it off. Both resolve to silence today, but a caller that wanted to
    explain *why* the device is quiet can tell them apart, and keeping the
    distinction here stops it being flattened by an accident later.

    An unreadable store resolves to `None` rather than raising. The voice loop has
    to keep answering: `build_orchestrator` failing would take the screen answer
    down with the speaker, turning a settings fault into a device that does not
    work. Silence plus a readable reason is the smaller failure, and `health()`
    reports the store's state separately so an operator can see it.
    """
    if config.spoken_dir is None:
        return None
    try:
        return spoken_responses_intent(SpokenResponsesStore(config.spoken_dir).load())
    except (PowerSettingsError, ValueError, OSError):
        return None


def build_orchestrator(config: RuntimeConfig) -> VoiceOrchestrator:
    context = (
        StaticContext(config.static_context)
        if config.static_context
        else LanternConversationContext() if config.conversation_dir else SageSDKContext(
            config.sage_base_url,
            _required(config.sage_identity_path, "sage.identity_path"),
            config.sage_domain,
        )
    )
    # Both layers, and the shared decision rather than a local restatement of it.
    # `tts.enabled` gates the engine, the `[audio]` keys gate the routing, and the
    # stored preference gates the sound — so the construction site cannot drift
    # from `playback_decision`, which the end-to-end test also pins.
    decision = playback_decision(
        engine_enabled=config.tts_enabled,
        device=config.audio_device if config.audio_enabled else None,
        desired=spoken_intent(config),
    )
    return VoiceOrchestrator(
        StateMachine(JsonlEventLog(config.state_log)),
        WhisperCppSTT(config.whisper_binary, config.whisper_model, config.whisper_threads),
        context,
        LlamaCppHTTP(config.llama_base_url, config.llama_model,
                     experimental_answer_suggestions=config.experimental_answer_suggestions),
        PiperTTS(
            _required(config.piper_python, "tts.python"),
            _required(config.piper_model, "tts.model"),
            config.piper_config,
        ) if config.tts_enabled else None,
        config.output_dir,
        AnswerVerifier(config.verifier_base_url, config.verifier_model,
                       timeout_seconds=config.verifier_timeout_seconds)
        if config.verify_answers else None,
        playback=AplayPlayback(
            config.audio_device,
            target_rate=config.audio_target_rate,
        ) if decision.enabled else None,
    )


def _required(value: Path | None, name: str) -> Path:
    if value is None:
        raise ValueError(f"{name} is required when static context is disabled")
    return value


def health(config: RuntimeConfig) -> int:
    checks: dict[str, dict[str, str | bool]] = {}
    paths = {
        "whisper_binary": config.whisper_binary,
        "whisper_model": config.whisper_model,
    }
    if config.tts_enabled:
        paths.update({"piper_python": config.piper_python, "piper_model": config.piper_model})
    for name, path in paths.items():
        assert path is not None
        checks[name] = {"ok": path.is_file(), "detail": str(path)}
    if config.sage_identity_path is not None:
        checks["sage_identity"] = {
            "ok": config.sage_identity_path.is_file(),
            "detail": str(config.sage_identity_path),
        }
    for name, url in {
        "llama_server": f"{config.llama_base_url.rstrip('/')}/health",
        "sage_node": f"{config.sage_base_url.rstrip('/')}/health",
    }.items():
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                checks[name] = {"ok": 200 <= response.status < 300, "detail": url}
        except (OSError, urllib.error.URLError) as exc:
            checks[name] = {"ok": False, "detail": f"{url}: {type(exc).__name__}"}
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if all(bool(item["ok"]) for item in checks.values()) else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sage-lantern")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)

    health_parser = commands.add_parser("health", help="check local runtime dependencies")
    health_parser.add_argument("--config", type=Path, required=True)

    voice_parser = commands.add_parser("voice", help="run one local voice loop from a WAV file")
    voice_parser.add_argument("--config", type=Path, required=True)
    voice_input = voice_parser.add_mutually_exclusive_group(required=True)
    voice_input.add_argument("--audio", type=Path)
    voice_input.add_argument("--question-stdin", action="store_true", help="bounded UTF-8 corrected question on stdin; normal admitted pipeline")
    voice_parser.add_argument("--session-id")
    voice_parser.add_argument("--image", type=Path, help="one JPEG: 512 KiB, dimensions <=640, <=307200 pixels")
    voice_parser.add_argument("--events", action="store_true", help="emit flushed JSONL visible-text deltas and final result")
    voice_parser.add_argument('--conversation-stdin', action='store_true',
                              help='bounded private question/messages envelope on stdin')

    envelope_parser = commands.add_parser("validate-envelope", help="validate one incoming mesh envelope")
    envelope_parser.add_argument("--file", type=Path, required=True)
    envelope_parser.add_argument("--max-body-bytes", type=int, default=1024)

    queue_parser = commands.add_parser("queue-proposal", help="durably queue one signed PUBLIC derivative")
    queue_parser.add_argument("--database", type=Path, required=True)
    queue_parser.add_argument("--file", type=Path, required=True)

    status_parser = commands.add_parser("proposal-status", help="read one local public proposal state")
    status_parser.add_argument("--database", type=Path, required=True)
    status_parser.add_argument("--envelope-id", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate-envelope":
            envelope = LanternEnvelope.from_json(
                args.file.read_text(encoding="utf-8"),
                max_body_bytes=args.max_body_bytes,
                require_integrity=True,
            )
            print(json.dumps({"valid": True, "message_id": envelope.message_id, "encoded_size": envelope.encoded_size()}))
            return 0

        if args.command == "queue-proposal":
            outbox = ProposalOutbox(args.database)
            try:
                record, created = outbox.enqueue(args.file.read_text(encoding="utf-8"))
            finally:
                outbox.close()
            print(json.dumps({**record.to_dict(), "created": created}, sort_keys=True))
            return 0

        if args.command == "proposal-status":
            outbox = ProposalOutbox(args.database)
            try:
                record = outbox.get(args.envelope_id)
            finally:
                outbox.close()
            print(json.dumps(record.to_dict(), sort_keys=True))
            return 0

        config = RuntimeConfig.load(args.config)
        if args.command == "health":
            return health(config)
        if args.command == "voice":
            orchestrator = build_orchestrator(config)
            run_input = orchestrator.run_audio
            question_input = args.audio
            conversation = None
            if args.conversation_stdin:
                supplied_question, conversation = _conversation_input(sys.stdin.buffer, text=args.question_stdin)
            if args.question_stdin:
                question_input = supplied_question if args.conversation_stdin else validated_question(sys.stdin.buffer.read(4097).decode('utf-8'))
                run_input = orchestrator.run_text
            image_options = {"image_path": args.image} if args.image is not None else {}
            if conversation:
                image_options['conversation'] = conversation
            if args.events:
                def emit_delta(text: str, transcript: str) -> None:
                    print(json.dumps({"type": "delta", "text": text, "transcript": transcript}), flush=True)

                def emit_progress(stage: str) -> None:
                    print(json.dumps({"type": "progress", "stage": stage}), flush=True)

                result = run_input(question_input, args.session_id, on_delta=emit_delta,
                                                on_progress=emit_progress, **image_options)
            else:
                result = run_input(question_input, args.session_id, **image_options)
            payload = {
                "session_id": result.session_id,
                "transcript": result.transcript,
                "response": result.response,
                "audio_path": str(result.audio_path) if result.audio_path else None,
                "timings_ms": result.timings_ms,
                "inference_metrics": result.inference_metrics,
            }
            if result.follow_up_questions:
                payload["follow_up_questions"] = result.follow_up_questions
            if result.verification:
                payload["verification"] = result.verification
            if args.events:
                print(json.dumps({"type": "result", "result": payload}), flush=True)
            else:
                print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
    except NoSpeechDetected:
        if args.command == "voice" and args.events:
            print(json.dumps({"type": "error", "code": "no_spoken_question"}), flush=True)
        else:
            print("sage-lantern: no_spoken_question", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"sage-lantern: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
