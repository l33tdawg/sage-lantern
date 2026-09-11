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
