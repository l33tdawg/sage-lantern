from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    state_log: Path
    output_dir: Path
    whisper_binary: Path
    whisper_model: Path
    whisper_threads: int
    llama_base_url: str
    llama_model: str
    tts_enabled: bool
    piper_python: Path | None
    piper_model: Path | None
    piper_config: Path | None
    sage_base_url: str
    sage_identity_path: Path | None
    sage_domain: str | None
    static_context: list[str]
    conversation_dir: Path | None = None
    protected_storage: bool = False
    sage_chain_id: str | None = None
    experimental_answer_suggestions: bool = False
    verify_answers: bool = False
    verify_answers_background: bool = False
    verifier_base_url: str = "http://127.0.0.1:8082"
    verifier_model: str = "qwen3.5-4b-q4_k_m"
    verifier_timeout_seconds: float = 60.0
    # Spoken responses, operator layer. `tts_enabled` above decides whether the
    # synthesis engine exists; these decide whether its output has somewhere to
    # go. Both default off so an installation that has not been given an output
    # device behaves exactly as it did before playback existed — a WAV on disk.
    audio_enabled: bool = False
    audio_device: str | None = None
    # The rate the output device actually accepts, when the operator states it.
    # Measured on unit C, ``hw:4,0`` accepts 16000 Hz and *refuses* 22050 — Piper's
    # own rate — so without this the answer is synthesized and then never played.
    # Left None by default: it is a hardware fact, and inventing one would convert
    # a device we have not measured. Stating it is what authorizes the conversion.
    audio_target_rate: int | None = None
    # Where the user's spoken-responses preference lives. Optional: with no
    # directory there is no user setting to read and the operator keys above are
    # the only authority, which is exactly the behaviour before this existed.
    spoken_dir: Path | None = None

    @classmethod
    def load(cls, path: Path) -> "RuntimeConfig":
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        root = path.parent

        def resolve(value: str | None) -> Path | None:
            if value is None:
                return None
            candidate = Path(value).expanduser()
            return candidate if candidate.is_absolute() else (root / candidate).resolve()

        runtime = raw.get("runtime", {})
        stt = raw.get("stt", {})
        llm = raw.get("llm", {})
        experimental_answer_suggestions = llm.get("experimental_answer_suggestions", False)
        if type(experimental_answer_suggestions) is not bool:
            raise ValueError("llm.experimental_answer_suggestions must be a boolean")
        verify_answers = llm.get("verify_answers", False)
        if type(verify_answers) is not bool:
            raise ValueError("llm.verify_answers must be a boolean")
        verify_answers_background = llm.get("verify_answers_background", False)
        if type(verify_answers_background) is not bool:
            raise ValueError("llm.verify_answers_background must be a boolean")
        verifier_base_url = llm.get("verifier_base_url", "http://127.0.0.1:8082")
        verifier_model = llm.get("verifier_model", str(llm.get("model") or "qwen3.5-4b-q4_k_m"))
        verifier_timeout = llm.get("verifier_timeout_seconds", 60.0)
        for name, value in (("llm.verifier_base_url", verifier_base_url), ("llm.verifier_model", verifier_model)):
            if type(value) is not str or not value or len(value) > 256:
                raise ValueError(name + " must be a short non-empty string")
        if type(verifier_timeout) not in (int, float) or not 1.0 <= float(verifier_timeout) <= 600.0:
            raise ValueError("llm.verifier_timeout_seconds must be between 1 and 600")
        tts = raw.get("tts", {})
        sage = raw.get("sage", {})
        context = raw.get("context", {})
        protected_storage = sage.get("protected_storage", False)
        chain_id = sage.get("chain_id")
        if type(protected_storage) is not bool:
            raise ValueError("sage.protected_storage must be a boolean")
        if protected_storage and (type(chain_id) is not str or not chain_id or len(chain_id) > 128):
            raise ValueError("protected storage requires an operator-bound sage.chain_id")
        if protected_storage:
            for field in ("conversation_dir", "identity_path"):
                value = sage.get(field)
                if (type(value) is not str or not value or not Path(value).is_absolute()
                        or ".." in Path(value).parts or str(Path(value)) != value):
                    raise ValueError("protected storage requires an explicit canonical sage." + field)
        tts_enabled = bool(tts.get("enabled", True))
        audio = raw.get("audio", {})
        audio_enabled = audio.get("enabled", False)
        if type(audio_enabled) is not bool:
            raise ValueError("audio.enabled must be a boolean")
        audio_device = audio.get("device")
        if audio_device is not None and (type(audio_device) is not str or not audio_device.strip()
                                         or len(audio_device) > 256):
            raise ValueError("audio.device must be a short non-empty ALSA device string")
        # Enabled without a device is a configuration mistake, not a silent
        # fallback: it would start a player with nowhere to send sound, and the
        # failure would surface as a per-turn playback error instead of at start-up.
        if audio_enabled and audio_device is None:
            raise ValueError("audio.device is required when audio.enabled is true")
        audio_target_rate = audio.get("target_rate")
        if audio_target_rate is not None and (type(audio_target_rate) is not int
                                              or not 8000 <= audio_target_rate <= 192000):
            raise ValueError("audio.target_rate must be an integer between 8000 and 192000")
        # The user's preference directory is trusted operator configuration, never
        # request input, so it must be an explicit canonical absolute path.
        spoken_dir = audio.get("spoken_dir")
        if spoken_dir is not None:
            if (type(spoken_dir) is not str or not spoken_dir or not Path(spoken_dir).is_absolute()
                    or ".." in Path(spoken_dir).parts or str(Path(spoken_dir)) != spoken_dir
                    or len(spoken_dir) > 4096):
                raise ValueError("audio.spoken_dir must be an explicit canonical absolute path")
        required = {
            "runtime.state_log": runtime.get("state_log"),
            "runtime.output_dir": runtime.get("output_dir"),
            "stt.binary": stt.get("binary"),
            "stt.model": stt.get("model"),
            "llm.base_url": llm.get("base_url"),
            "llm.model": llm.get("model"),
            "sage.base_url": sage.get("base_url"),
        }
        if tts_enabled:
            required.update({"tts.python": tts.get("python"), "tts.model": tts.get("model")})
        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            raise ValueError(f"missing configuration values: {', '.join(missing)}")

        return cls(
            state_log=resolve(runtime["state_log"]),  # type: ignore[arg-type]
            output_dir=resolve(runtime["output_dir"]),  # type: ignore[arg-type]
            whisper_binary=resolve(stt["binary"]),  # type: ignore[arg-type]
            whisper_model=resolve(stt["model"]),  # type: ignore[arg-type]
            whisper_threads=int(stt.get("threads", 6)),
            llama_base_url=str(llm["base_url"]),
            llama_model=str(llm["model"]),
            experimental_answer_suggestions=experimental_answer_suggestions,
            verify_answers=verify_answers,
            verify_answers_background=verify_answers_background,
            verifier_base_url=verifier_base_url,
            verifier_model=verifier_model,
            verifier_timeout_seconds=float(verifier_timeout),
            tts_enabled=tts_enabled,
            audio_enabled=audio_enabled,
            audio_device=audio_device,
            audio_target_rate=audio_target_rate,
            spoken_dir=Path(spoken_dir) if spoken_dir is not None else None,
            piper_python=resolve(tts.get("python")),
            piper_model=resolve(tts.get("model")),
            piper_config=resolve(tts.get("config")),
            sage_base_url=str(sage["base_url"]),
            sage_identity_path=resolve(sage.get("identity_path")),
            sage_domain=sage.get("domain"),
            static_context=list(context.get("static_items", [])),
            conversation_dir=resolve(sage.get("conversation_dir")),
            protected_storage=protected_storage,
            sage_chain_id=chain_id,
        )
