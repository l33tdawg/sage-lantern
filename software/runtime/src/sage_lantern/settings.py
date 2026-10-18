"""Pure capability projection, not authorization, observation collection, or apply.

Callers supply sanitized intent and observations from their own trusted boundary.
Existing implementation means code exists, not that a feature is enabled or that
the caller is authorized to change it. All projections are read-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType


class SettingId(StrEnum):
    HISTORY = "local_history"
    VOICE = "voice_capture"
    PHOTO = "photo_answers"
    TTS = "spoken_responses"
    MESSAGING = "messaging"
    RECOVERY = "personal_recovery"


class SettingKind(StrEnum):
    PREFERENCE = "preference"
    CAPABILITY = "capability"
    ACTION = "action"


class Implementation(StrEnum):
    EXISTING = "existing_code"
    UNAVAILABLE = "unavailable"


class ObservationState(StrEnum):
    UNKNOWN = "unknown"
    FRESH = "fresh"
    STALE = "stale"


class ProjectionReason(StrEnum):
    NO_OBSERVATION = "no_observation"
    UNKNOWN_OBSERVATION = "unknown_observation"
    STALE_OBSERVATION = "stale_observation"
    FUTURE_OBSERVATION = "future_observation"
    OBSERVED = "observed_not_applied"
    UNSUPPORTED_MESSAGING = "messaging_not_supported"
    RECOVERY_UNAVAILABLE = "recovery_not_implemented_or_authorized"


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    setting_id: SettingId
    kind: SettingKind
    implementation: Implementation


REGISTRY: Mapping[SettingId, SettingDefinition] = MappingProxyType({
    definition.setting_id: definition
    for definition in (
        SettingDefinition(SettingId.HISTORY, SettingKind.PREFERENCE, Implementation.EXISTING),
        SettingDefinition(SettingId.VOICE, SettingKind.CAPABILITY, Implementation.EXISTING),
        SettingDefinition(SettingId.PHOTO, SettingKind.CAPABILITY, Implementation.EXISTING),
        SettingDefinition(SettingId.TTS, SettingKind.CAPABILITY, Implementation.EXISTING),
        SettingDefinition(SettingId.MESSAGING, SettingKind.CAPABILITY, Implementation.UNAVAILABLE),
        SettingDefinition(SettingId.RECOVERY, SettingKind.ACTION, Implementation.UNAVAILABLE),
    )
})

OBSERVATION_MAX_AGE = timedelta(minutes=5)


def _require_timestamp(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")


@dataclass(frozen=True, slots=True)
class SettingObservation:
    value: bool | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.value is not None and type(self.value) is not bool:
            raise ValueError("observation value must be boolean or unknown")
        _require_timestamp(self.observed_at)


@dataclass(frozen=True, slots=True)
class SettingProjection:
    definition: SettingDefinition
    desired: bool | None
    effective: bool | None
    observation_state: ObservationState
    reason: ProjectionReason
    observed_at: datetime | None
    read_only: bool = True


def _require_setting(setting_id: SettingId) -> None:
    if not isinstance(setting_id, SettingId) or setting_id not in REGISTRY:
        raise ValueError("unknown setting identifier")
    if REGISTRY[setting_id].kind is SettingKind.ACTION:
        raise ValueError("actions cannot carry boolean settings state")


def spoken_responses_enabled(desired: Mapping[SettingId, bool] | None) -> bool:
    """Whether the user has asked Lantern to read its answers aloud.

    One rule: **speaking is off unless the user explicitly turned it on.**

    This is the consumer the modules around it deliberately are not. `settings.py`
    is a read-only projection and says so; the draft store persists what the user
    asked for and says it provides "no apply handler". Neither decides whether
    sound leaves the speaker, and something has to. That decision is here, kept
    pure so it can be tested without an audio device.

    Fail-closed in both directions:

    - absent, `None`, or an empty mapping is **off**, because nothing was asked
      for. An installation that has never opened the setting must not start
      talking because a value defaulted true somewhere.
    - a value that is not exactly the boolean `True` is **off**. `1`, `"true"`
      and `"on"` are refused rather than coerced: a truthy string is how a
      configuration mistake becomes an unexplained speaking device, and refusing
      is cheaper than explaining.
    - a malformed mapping raises instead of resolving to a silent `False`. The
      `SettingId.TTS` kind is validated through the same `_require_setting` the
      projection uses, so an `ACTION` or an unknown identifier is loud, because a
      typo that quietly disables a feature is worse than an error.

    This is supplied intent, not authorization, and not a claim that a speaker
    path is qualified on this unit. It says the user asked; the caller still needs
    an output device, and the audio commission gate still applies.
    """
    if desired is None:
        return False
    if not isinstance(desired, Mapping):
        raise ValueError("desired settings must be a mapping")
    for setting_id, value in desired.items():
        _require_setting(setting_id)
        if type(value) is not bool:
            raise ValueError("desired value must be boolean")
    if SettingId.TTS not in desired:
        return False
    return desired[SettingId.TTS] is True


class PlaybackUnavailable(StrEnum):
    """Why the answer will not be spoken, when the user asked for it anyway."""

    ENGINE_DISABLED = "tts_engine_disabled"
    NO_DEVICE = "no_output_device"


@dataclass(frozen=True, slots=True)
class PlaybackDecision:
    """Whether to speak, and if not, why not.

    The reason is carried rather than reduced to a boolean because "the device is
    silent" has several causes that need different answers: a user who asked to be
    read to and hears nothing should be able to learn that the engine is off or
    that no output device is configured, instead of guessing at a mute.
    """

    enabled: bool
    reason: PlaybackUnavailable | None = None


def playback_decision(
    *,
    engine_enabled: bool,
    device: str | None,
    desired: Mapping[SettingId, bool] | None,
) -> PlaybackDecision:
    """Combine the three things that must all hold before an answer is spoken.

    This is the composition of two layers the owner fixed on 10 October 2026 —
    the config gates the *engine*, the user's setting gates the *sound* — plus the
    one fact neither layer carries: whether an output device is configured at all.

    All three are required:

    1. **Intent** — `spoken_responses_enabled(desired)`. Off unless the user
       explicitly asked, and it raises on a malformed mapping rather than
       returning a quiet `False`.
    2. **Engine** — `engine_enabled`, which is `tts.enabled`. The sound layer can
       never conjure an engine the config disabled, so this is checked, not
       assumed. A caller that has already decided not to build a TTS passes
       `False` here.
    3. **Device** — an ALSA device string. Without one there is no speaker to
       address, and the answer is text only.

    Order matters only for the reason reported: intent is checked first, because a
    user who never asked to be read to is not owed an explanation about hardware.
    Conversely, a user who *did* ask and gets silence is owed the real cause, which
    is why `reason` is reported rather than swallowed.

    Pure: no store, no device, no process. The one thing it refuses to do is
    guess — an empty device string is no device, not a default.
    """
    if not spoken_responses_enabled(desired):
        return PlaybackDecision(False)
    if not engine_enabled:
        return PlaybackDecision(False, PlaybackUnavailable.ENGINE_DISABLED)
    if device is None or not device.strip():
        return PlaybackDecision(False, PlaybackUnavailable.NO_DEVICE)
    return PlaybackDecision(True)


def project_settings(
    *,
    now: datetime,
    desired: Mapping[SettingId, bool] | None = None,
    observed: Mapping[SettingId, SettingObservation] | None = None,
) -> tuple[SettingProjection, ...]:
    """Project bounded inputs without I/O, persistence, authorization or mutation.

    Desired values are supplied intent, not accepted/applied revisions. Effective
    values come only from fresh supplied observations of existing capabilities.
    Missing, future and expired observations never imply an effective value.
    The five-minute horizon is a projection ceiling, not a runtime health probe;
    callers must omit observations invalidated by reboot or prerequisite loss.
    """
    _require_timestamp(now)
    if desired is not None and not isinstance(desired, Mapping):
        raise ValueError("desired settings must be a mapping")
    if observed is not None and not isinstance(observed, Mapping):
        raise ValueError("observed settings must be a mapping")
    desired_values = dict(desired) if desired is not None else {}
    observations = dict(observed) if observed is not None else {}
    for setting_id, value in desired_values.items():
        _require_setting(setting_id)
        if type(value) is not bool:
            raise ValueError("desired value must be boolean")
    for setting_id, observation in observations.items():
        _require_setting(setting_id)
        if type(observation) is not SettingObservation:
            raise ValueError("typed observation required")

    projections = []
    for setting_id, definition in REGISTRY.items():
        observation = observations.get(setting_id)
        effective = None
        state = ObservationState.UNKNOWN
        observed_at = None
        reason = ProjectionReason.NO_OBSERVATION
        if setting_id is SettingId.MESSAGING:
            reason = ProjectionReason.UNSUPPORTED_MESSAGING
        elif setting_id is SettingId.RECOVERY:
            reason = ProjectionReason.RECOVERY_UNAVAILABLE
        elif observation is not None:
            observed_at = observation.observed_at
            age = now - observed_at
            if age < timedelta(0):
                reason = ProjectionReason.FUTURE_OBSERVATION
            elif age >= OBSERVATION_MAX_AGE:
                state = ObservationState.STALE
                reason = ProjectionReason.STALE_OBSERVATION
            elif observation.value is None:
                reason = ProjectionReason.UNKNOWN_OBSERVATION
            else:
                effective = observation.value
                state = ObservationState.FRESH
                reason = ProjectionReason.OBSERVED
        projections.append(SettingProjection(
            definition=definition,
            desired=desired_values.get(setting_id),
            effective=effective,
            observation_state=state,
            reason=reason,
            observed_at=observed_at,
        ))
    return tuple(projections)


def validate_spoken_policy(value: object) -> dict:
    """Validate a stored spoken-responses record.

    Mirrors `power_settings.validate_lock_policy`: a revisioned record with one
    field, refused unless it is exactly the shape expected. `spoken_responses` must
    be a real boolean — `1`, `0` and `"true"` are refused rather than coerced, for
    the same reason `spoken_responses_enabled` refuses them: a truthy value that
    reaches the speaker is how a configuration mistake becomes an unexplained
    speaking device.

    Raises `PowerSettingsError` because that is what the store base this feeds
    expects; keeping the reader and the writer on one error type means a caller
    cannot accidentally handle one and not the other.
    """
    from .power_settings import MAX_REVISION, PowerSettingsError

    if (type(value) is not dict
            or set(value) != {"revision", "spoken_responses"}
            or type(value["revision"]) is not int or not 0 <= value["revision"] <= MAX_REVISION
            or type(value["spoken_responses"]) is not bool):
        raise PowerSettingsError()
    return dict(value)


def spoken_responses_intent(value: object) -> Mapping[SettingId, bool]:
    """Turn a stored record into the mapping the decision already consumes.

    This is the seam that keeps storage and behaviour from drifting: a caller
    reads the record, passes it through here, and hands the result to
    `playback_decision`. Nothing else needs to know the record's field name, and a
    record that is malformed raises here rather than resolving to a quiet silence.

    A record with no field at all is refused rather than read as `False`: an
    unreadable preference and a preference of "no" are different facts, and
    collapsing them would hide a storage fault behind a legitimate-looking
    default.
    """
    if type(value) is not dict or type(value.get("spoken_responses")) is not bool:
        raise ValueError("a stored spoken-responses record must carry a boolean value")
    return {SettingId.TTS: value["spoken_responses"]}
