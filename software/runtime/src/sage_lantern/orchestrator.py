from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .adapters import ContextSource, LanguageModel, SpeechToText, TextToSpeech
from .state import RuntimeState, StateMachine
from .inference_metrics import project as project_metrics
from .active_conversation import validate_messages
from .answer_suggestions import validate_suggestions
from .answer_verification import AnswerVerifier


@dataclass(frozen=True)
class VoiceResult:
    session_id: str
    transcript: str
    response: str
    audio_path: Path | None
    timings_ms: dict[str, float]
    inference_metrics: dict[str, int | float | None] | None = None
    follow_up_questions: list[str] | None = None
    verification: dict[str, object] | None = None


class VoiceOrchestrator:
    def __init__(
        self,
        state: StateMachine,
        stt: SpeechToText,
        context: ContextSource,
        llm: LanguageModel,
        tts: TextToSpeech | None,
        output_dir: Path,
        verifier: AnswerVerifier | None = None,
    ) -> None:
        self.state = state
        self.stt = stt
        self.context = context
        self.llm = llm
        self.tts = tts
        self.output_dir = output_dir
        self.verifier = verifier

    def run_audio(
        self,
        audio_path: Path,
        session_id: str | None = None,
        on_delta: Callable[[str, str], None] | None = None,
        image_path: Path | None = None,
        on_progress: Callable[[str], None] | None = None,
        conversation: list[dict[str, str]] | None = None,
    ) -> VoiceResult:
        return self._run(audio_path, session_id, on_delta, image_path, on_progress, conversation=conversation)

    def run_text(self, question: str, session_id: str | None = None,
                 on_delta: Callable[[str, str], None] | None = None,
                 image_path: Path | None = None,
                 on_progress: Callable[[str], None] | None = None,
                 conversation: list[dict[str, str]] | None = None) -> VoiceResult:
        question = validated_question(question)
        return self._run(None, session_id, on_delta, image_path, on_progress, question=question, conversation=conversation)

    def _run(self, audio_path, session_id, on_delta, image_path, on_progress, *, question=None, conversation=None):
        """Time stages in milliseconds, including synchronous callback backpressure.

        first_text starts at this method's entry, excluding audio hold and caller
        setup. llm_first_token starts before the LLM adapter call. Both stop
        before the first non-whitespace content callback. delta_callback is the
        callback-time subset of llm, not an additional stage. total sums only
        stt, sage_context, llm and optional tts; it is not end-to-end wall time.
        Photo header validation and encoding are included in llm. The caller
        owns photo cleanup; only the transcript is passed to SAGE recall.
        """
        processing_started = time.monotonic()
        current_session = session_id or str(uuid.uuid4())
        timings: dict[str, float] = {}
        if question is None:
            self.state.transition(RuntimeState.LISTENING, current_session, "audio_ready")

        try:
            image_options = {}
            if conversation:
                if getattr(self.llm, 'supports_conversation', False) is not True:
                    raise RuntimeError('Language model does not support conversation')
                image_options['conversation'] = validate_messages(conversation)
            if image_path is not None:
                if getattr(self.llm, "supports_images", False) is not True:
                    raise RuntimeError("Language model does not support photos; text fallback is forbidden")
                image_options["image_path"] = image_path
            if question is None:
                self.state.transition(RuntimeState.TRANSCRIBING, current_session)
                if on_progress is not None:
                    on_progress("transcribing")
                started = time.monotonic()
                transcript = self.stt.transcribe(audio_path)
                timings["stt"] = (time.monotonic() - started) * 1000
            else:
                transcript = question

            self.state.transition(RuntimeState.THINKING, current_session)
            if on_progress is not None:
                on_progress("recalling")
            started = time.monotonic()
            context_items = self.context.recall(transcript)
            timings["sage_context"] = (time.monotonic() - started) * 1000
            if on_progress is not None:
                on_progress("photo_analysis" if image_path is not None else "generating")
            started = time.monotonic()
            stream = getattr(self.llm, "respond_stream", None)
            if on_delta is not None and callable(stream):
                timings["delta_callback"] = 0.0

                def emit_delta(text: str) -> None:
                    if text:
                        if text.strip() and "llm_first_token" not in timings:
                            first_visible = time.monotonic()
                            timings["llm_first_token"] = (first_visible - started) * 1000
                            timings["first_text"] = (first_visible - processing_started) * 1000
                            if on_progress is not None:
                                on_progress("answering")
                        callback_started = time.monotonic()
                        try:
                            on_delta(text, transcript)
                        finally:
                            timings["delta_callback"] += (time.monotonic() - callback_started) * 1000

                response = stream(transcript, context_items, emit_delta, **image_options)
            else:
                response = self.llm.respond(transcript, context_items, **image_options)
            timings["llm"] = (time.monotonic() - started) * 1000

            verification = None
            if self.verifier is not None and image_path is None and response.strip():
                verdict = self.verifier.verify(transcript, response)
                timings["verify"] = verdict.seconds * 1000
                verification = {"status": verdict.status, "seconds": round(verdict.seconds, 3)}
                if verdict.status == "corrected":
                    response = verdict.text
                    verification["corrected"] = True

            response_path = None
            if self.tts is not None:
                self.state.transition(RuntimeState.SPEAKING, current_session)
                response_path = self.output_dir / f"{current_session}.wav"
                if on_progress is not None:
                    on_progress("speaking")
                started = time.monotonic()
                self.tts.synthesize(response, response_path)
                timings["tts"] = (time.monotonic() - started) * 1000
            timings["total"] = sum(timings.get(stage, 0.0)
                                   for stage in ("stt", "sage_context", "llm", "verify", "tts"))

            self.state.transition(RuntimeState.IDLE, current_session, "completed")
            return VoiceResult(current_session, transcript, response, response_path, timings,
                               project_metrics(getattr(self.llm, 'last_metrics', None)),
                               validate_suggestions(getattr(self.llm, 'last_suggestions', [])),
                               verification)
        except Exception as exc:
            self.state.transition(RuntimeState.ERROR, current_session, type(exc).__name__)
            raise


def validated_question(value):
    if (type(value) is not str or not value.strip() or len(value.encode('utf-8')) > 4096
            or any(ord(char) < 32 and char not in '\n\t' for char in value)):
        raise ValueError('invalid_question')
    return value
