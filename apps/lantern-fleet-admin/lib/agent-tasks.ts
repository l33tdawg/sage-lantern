/**
 * Agent destination: task-list state and schedule wording for the Lantern screen.
 *
 * Pure functions only: no React, no fetch, no storage, no timers. The Agent
 * screen and its add/edit form render what these return, so "unavailable" versus
 * "genuinely empty", what a schedule is allowed to say, and when a draft must be
 * thrown away can be tested without a browser and argued about without a device.
 *
 * The governing decision is `SECONDARY_SCREEN_DECISIONS.md`: there is **no**
 * approved device task CRUD binding. The existing `voiceCommand` and `deviceAuth`
 * action unions expose none, and the institutional SAGE task list used by Rook
 * and Ember is project coordination, not an authenticated personal scheduler. So
 * this build must show the reference destination honestly rather than invent a
 * backend, and the distinction that carries the whole screen is:
 *
 *   **an unavailable service is not an empty list.**
 *
 * "No tasks yet" asserts that we asked and the device answered. We cannot ask, so
 * saying it would be a fabricated result. The two states therefore have different
 * sentences and only one of them is ever reachable in this build.
 */

/** The one reason shown whenever this build cannot reach a task service. */
export const AGENT_UNAVAILABLE_REASON = 'Tasks are not available on this device yet.';

/**
 * What the Agent screen is allowed to claim.
 *
 * `ready` and `empty` both require an approved provider that actually answered;
 * `empty` additionally requires that answer to have been a complete, verified
 * list. A provider that answered with a partial or unverifiable list is not
 * `empty` either — that is `unavailable`, because we still cannot say there are
 * no tasks.
 */
export type AgentAvailability =
  | { state: 'unavailable'; reason: string }
  | { state: 'empty'; reason: string }
  | { state: 'ready'; reason: '' };

/**
 * A task-service binding, as the screen sees it.
 *
 * `provider` is null in this build and that is the point: the type exists so a
 * future approved provider is a deliberate addition rather than an assumption
 * the UI already made.
 */
export type TaskProvider = {
  /** True only for an approved, authenticated provider. */
  approved: boolean;
  /** True only when the provider answered and the answer verified. */
  answered: boolean;
  /** Whether the answer was a complete list rather than a page or a partial. */
  complete: boolean;
} | null;

export function agentAvailability(provider: TaskProvider): AgentAvailability {
  if (!provider || !provider.approved) {
    return { state: 'unavailable', reason: AGENT_UNAVAILABLE_REASON };
  }
  if (!provider.answered || !provider.complete) {
    // Answered-but-unverified and silent are the same answer for the user: we
    // cannot state that there are no tasks, so we say we cannot state it.
    return { state: 'unavailable', reason: AGENT_UNAVAILABLE_REASON };
  }
  return { state: 'ready', reason: '' };
}

/** The empty-list sentence, reachable only from a verified complete answer. */
export const AGENT_EMPTY_REASON = 'No tasks yet. Add one to get started.';

/**
 * What occupies the list region.
 *
 * Kept separate from `agentAvailability` because the empty sentence must be
 * impossible to reach without a verified empty answer, and folding the two
 * together is how "no tasks" leaks into a build that never asked.
 *
 * `taskCount` must be a verified count, so only a finite nonnegative integer
 * can produce `empty`. A count that is missing, fractional or otherwise not a
 * count at all proves nothing, and is reported as something we cannot state
 * rather than as an empty list.
 */
export function agentListState(availability: AgentAvailability, taskCount: number): AgentAvailability {
  if (availability.state !== 'ready') return availability;
  if (!Number.isInteger(taskCount) || taskCount < 0) {
    // Unknown is not zero. Only a verified zero proves there are no tasks.
    return { state: 'unavailable', reason: AGENT_UNAVAILABLE_REASON };
  }
  return taskCount > 0 ? availability : { state: 'empty', reason: AGENT_EMPTY_REASON };
}

// --- Schedules --------------------------------------------------------------

/**
 * A schedule as the reference presents it: a repeating interval or a one-time
 * moment. Deliberately not a cron expression and not an RRULE — the decision
 * says raw cron/RRULE is not the product interface, and the device has no
 * scheduler to hand one to anyway.
 */
export type TaskSchedule =
  | { mode: 'every'; minutes: number }
  | { mode: 'once'; at: string };

/** Units a repeating interval may be stated in, largest first. */
const INTERVAL_UNITS: readonly { minutes: number; singular: string; plural: string }[] = Object.freeze([
  { minutes: 1440, singular: 'day', plural: 'days' },
  { minutes: 60, singular: 'hour', plural: 'hours' },
  { minutes: 1, singular: 'minute', plural: 'minutes' },
]);

/**
 * A repeating interval is only a real schedule when it is a finite, positive
 * duration.
 *
 * The wording and the draft rules share this one predicate deliberately: when
 * each carries its own copy of the rule they drift, and the form starts
 * accepting a value the card then renders as `Not scheduled`.
 */
function isRealInterval(minutes: number): boolean {
  return Number.isFinite(minutes) && minutes > 0;
}

/**
 * A repeating interval in words, with its unit always visible.
 *
 * A bare number would be ambiguous, which is why the unit is chosen rather than
 * assumed, and why an interval that is not a whole number of any unit is stated
 * in minutes rather than rounded into a unit it does not divide.
 */
export function formatInterval(minutes: number): string {
  if (!isRealInterval(minutes)) return 'Not scheduled';
  for (const unit of INTERVAL_UNITS) {
    if (minutes % unit.minutes === 0) {
      const count = minutes / unit.minutes;
      return `Every ${count} ${count === 1 ? unit.singular : unit.plural}`;
    }
  }
  return `Every ${minutes} minutes`;
}

/**
 * A one-time moment in words, with its timezone always shown.
 *
 * The timestamp is rendered in the zone the device is actually in, and the zone
 * is named, because a one-time task that fires at a time the user cannot place
 * is worse than no schedule at all. An unparseable value says so instead of
 * rendering `Invalid Date`, and a zone the runtime cannot resolve is refused
 * the same way instead of throwing out of the card that renders it.
 */
export function formatOneTime(at: string, timeZone?: string): string {
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return 'Not scheduled';
  const zone = timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time';
  try {
    const date = new Intl.DateTimeFormat('en-GB', {
      day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: zone,
    }).format(when);
    return `Once, ${date} (${zone})`;
  } catch {
    // An unusable zone names a time the user cannot place, and substituting a
    // different zone would be inventing one. So there is no schedule to state.
    return 'Not scheduled';
  }
}

export function formatSchedule(schedule: TaskSchedule | null, timeZone?: string): string {
  if (!schedule) return 'Not scheduled';
  return schedule.mode === 'every'
    ? formatInterval(schedule.minutes)
    : formatOneTime(schedule.at, timeZone);
}

/** The two modes the form offers, in the order the reference shows them. */
export const SCHEDULE_MODES: readonly { id: TaskSchedule['mode']; label: string }[] = Object.freeze([
  { id: 'every', label: 'Every' },
  { id: 'once', label: 'Once' },
]);

/** Units the form offers for a repeating task, so the field has visible units. */
export const INTERVAL_CHOICES: readonly { id: string; label: string; minutes: number }[] = Object.freeze([
  { id: 'minutes', label: 'Minutes', minutes: 1 },
  { id: 'hours', label: 'Hours', minutes: 60 },
  { id: 'days', label: 'Days', minutes: 1440 },
]);

// --- Drafts -----------------------------------------------------------------

/**
 * What the add/edit form is holding.
 *
 * `schedule` is null until the user picks a mode, so the form never starts on a
 * schedule the user did not choose. Nothing here is persisted: the decision is
 * explicit that drafts must not be written to localStorage or SAGE as a
 * substitute for a real scheduler.
 */
export type AgentTaskDraft = {
  name: string;
  description: string;
  schedule: TaskSchedule | null;
  instruction: string;
};

export const EMPTY_DRAFT: AgentTaskDraft = Object.freeze({
  name: '', description: '', schedule: null, instruction: '',
});

/**
 * Why the draft cannot be saved yet, or `null` when it could be.
 *
 * The decision says this form implies no new validation contract, so the rules
 * are the minimum that makes a task meaningful — a name, and a schedule — rather
 * than a scheduler's worth of invented constraints. The unavailability reason
 * outranks them all, because in this build saving is unavailable whatever the
 * draft says.
 *
 * Only `unavailable` refuses. A verified empty list is a provider that answered,
 * and it is the state this form is most often used from — so the shared
 * availability type is safe here precisely because no list presentation can turn
 * into a refusal.
 */
export function draftRefusal(draft: AgentTaskDraft, availability: AgentAvailability): string | null {
  if (availability.state === 'unavailable') return availability.reason;
  if (!draft.name.trim()) return 'Give the task a name.';
  if (!draft.schedule) return 'Choose when this task should run.';
  if (draft.schedule.mode === 'every' && !isRealInterval(draft.schedule.minutes)) {
    return 'Choose how often this task should run.';
  }
  if (draft.schedule.mode === 'once' && Number.isNaN(new Date(draft.schedule.at).getTime())) {
    return 'Choose when this task should run once.';
  }
  return null;
}

/**
 * A draft together with the owner it belongs to.
 *
 * Drafts are scoped to the current authenticated owner and discarded on the
 * existing lock/owner-change boundaries. The pairing is modelled rather than
 * left to the component so "a draft cannot outlive its owner" is a tested
 * property instead of a hope about effect ordering.
 */
export type OwnedDraft = { owner: string; draft: AgentTaskDraft };

export function keepDraft(owner: string | null, draft: AgentTaskDraft): OwnedDraft | null {
  // No established owner means no draft may be held at all: an unowned draft is
  // one that could be written under somebody else's identity later.
  if (!owner) return null;
  return { owner, draft };
}

/**
 * The draft still readable for this owner, or `null`.
 *
 * A held draft whose owner no longer matches is discarded, not carried over —
 * which is the whole reason the owner travels with it.
 */
export function draftForOwner(held: OwnedDraft | null, owner: string | null): AgentTaskDraft | null {
  if (!held || !owner || held.owner !== owner) return null;
  return held.draft;
}

/** Discard on any authority boundary. Named so callers cannot forget it. */
export function discardDraft(): null {
  return null;
}

// --- Cards ------------------------------------------------------------------

/** The fields a task card shows, in the reference's order. */
export type AgentTaskCard = {
  id: string;
  name: string;
  description: string;
  /** Already formatted by `formatSchedule`; the card renders it verbatim. */
  schedule: string;
  instruction: string;
};

/**
 * Presentation of a provider record.
 *
 * Kept here so a card cannot render a raw cron string or omit the schedule: the
 * card shows what the formatter produced, and nothing reaches the card without
 * going through it.
 */
export function taskCard(task: {
  id: string; name: string; description?: string;
  schedule: TaskSchedule | null; instruction?: string;
}, timeZone?: string): AgentTaskCard {
  return {
    id: task.id,
    name: task.name,
    description: task.description ?? '',
    schedule: formatSchedule(task.schedule, timeZone),
    instruction: task.instruction ?? '',
  };
}
