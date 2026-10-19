'use client';

/**
 * The Agent add/edit form.
 *
 * One component for both modes, because the governing decision says add and edit
 * *are* the same form: two components would drift into two validation rules for
 * one task. The caller supplies the draft and receives every change, so the draft
 * has exactly one owner above this file and a boundary that discards it does not
 * have to reach into a child's state to do it.
 *
 * Four things this file is careful about:
 *
 *  - **Nothing here saves.** There is no approved task binding and no transport
 *    call in this file. The save button reports why it cannot act; the caller
 *    decides whether a real write exists at all.
 *  - **The wording comes from the pure core.** Interval, one-time and refusal
 *    text are `formatSchedule` and `draftRefusal`, so the button that refuses and
 *    the card that renders cannot disagree about what a valid schedule is.
 *  - **Enter is not a scheduling action.** The fields are deliberately not inside
 *    a `<form>`, so pressing Enter in a description or instruction is a newline,
 *    never a silent save.
 *  - **The text fields are edited with Lantern's own keyboard.** The brief and the
 *    secondary-screen decision both require the *existing* inline keyboard on
 *    forms, with its grapheme handling, so `KeyboardKeys` — the same key grid the
 *    question surfaces render — sits under whichever field is being edited and
 *    edits it through `lib/question-edit`. The system keyboard is suppressed on
 *    those fields (`inputMode="none"`) because it would cover the device's own.
 *    The date field keeps its native control: it is a picker, not typewritten
 *    text, and the schedule mode the form offers is not a keyboard decision.
 *    A tap reopens that keyboard even when the field is already the focused
 *    element: Done puts the keys away without moving DOM focus, so a second tap
 *    fires no focus event and `onFocus` alone would leave the field uneditable.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  AGENT_UNAVAILABLE_REASON, INTERVAL_CHOICES, SCHEDULE_MODES,
  draftRefusal, formatSchedule, type AgentAvailability, type AgentTaskDraft, type TaskSchedule,
} from '../../lib/agent-tasks';
import { eraseQuestion, insertQuestion, selectQuestion, type QuestionDraft } from '../../lib/question-edit';
import { BackIcon } from './icons';
import KeyboardKeys from './keyboard-keys';

/** The zone a one-time schedule is stated in, named so the moment is placeable. */
const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time';

/**
 * An ISO instant as the `datetime-local` control wants it, in local time.
 *
 * The control has no notion of a zone, so the instant has to be converted before
 * it is shown. Handing it the raw ISO string would render the moment shifted by
 * the offset, which is the same class of error as naming the wrong zone.
 */
function localInputValue(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '';
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}T${pad(when.getHours())}:${pad(when.getMinutes())}`;
}

/** The largest offered unit the interval divides evenly into, so 1440 reads as 1 day. */
function unitFor(minutes: number) {
  return [...INTERVAL_CHOICES].reverse().find((choice) => minutes % choice.minutes === 0) ?? INTERVAL_CHOICES[0];
}

/** The fields the inline keyboard edits, and the label each one is named by. */
const FIELD_LABELS = { name: 'Name', description: 'Description', amount: 'Repeat every', instruction: 'Instruction' } as const;
type FieldKey = keyof typeof FIELD_LABELS;

export default function AgentTaskForm({ title, draft, availability, writeAvailable, saving, error, onChange, onCancel, onSave }: {
  title: string;
  draft: AgentTaskDraft;
  availability: AgentAvailability;
  /** Whether a real task-write binding exists. It does not in this build. */
  writeAvailable: boolean;
  saving: boolean;
  /**
   * Why the last save or remove attempt failed, as the screen holding the write
   * command reported it. Rendered here rather than only on the list, because the
   * editor stays open when a save fails and an editor that says nothing looks
   * like an editor that worked.
   */
  error: string;
  onChange: (draft: AgentTaskDraft) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const name = useRef<HTMLInputElement>(null);
  const schedule = draft.schedule;
  /**
   * The interval as typed, kept apart from the parsed duration.
   *
   * The schedule the caller validates is a *number* of minutes, but the field is
   * text: deriving the visible value from the number would rewrite "14" to "1"
   * the moment it became a whole number of days, moving text under the cursor.
   * So the editor keeps what was typed and publishes the parsed result upward.
   */
  const [mode, setMode] = useState<TaskSchedule['mode']>(schedule?.mode ?? 'every');
  const [amount, setAmount] = useState(schedule?.mode === 'every' ? String(schedule.minutes / unitFor(schedule.minutes).minutes) : '');
  const [unit, setUnit] = useState(schedule?.mode === 'every' ? unitFor(schedule.minutes).id : INTERVAL_CHOICES[1].id);
  const [onceAt, setOnceAt] = useState(schedule?.mode === 'once' ? localInputValue(schedule.at) : '');
  /**
   * Which field the inline keyboard is editing, and where that field's caret is.
   *
   * The keys insert *at the selection*, which is what makes Shift, the space key
   * and a backspace in the middle of a word behave; so the caret travels with the
   * keyboard. The field is named alongside it because a key press must never land
   * in a field the user has since left. `null` means the keyboard is put away.
   */
  const [active, setActive] = useState<FieldKey | null>('name');
  const [caret, setCaret] = useState<{ field: FieldKey; start: number; end: number } | null>(null);
  const editors = useRef(new Map<FieldKey, HTMLInputElement | HTMLTextAreaElement>());
  const restoring = useRef<{ field: FieldKey; start: number; end: number } | null>(null);
  const keys = useRef<HTMLElement>(null);

  useEffect(() => { name.current?.focus(); }, []);

  // The keys open under a field the user may have had to scroll to, so bring them
  // into view. `nearest` leaves the scroll position alone when they already are.
  useEffect(() => { if (active) keys.current?.scrollIntoView({ block: 'nearest' }); }, [active]);

  useLayoutEffect(() => {
    const pending = restoring.current;
    if (!pending) return;
    restoring.current = null;
    const element = editors.current.get(pending.field);
    element?.focus({ preventScroll: true });
    element?.setSelectionRange(pending.start, pending.end);
  }, [draft, amount]);

  const publish = (next: Partial<{ mode: TaskSchedule['mode']; amount: string; unit: string; onceAt: string }>) => {
    const chosen = { mode, amount, unit, onceAt, ...next };
    const choice = INTERVAL_CHOICES.find((option) => option.id === chosen.unit) ?? INTERVAL_CHOICES[0];
    // An empty or unparseable field becomes `NaN` minutes rather than zero: zero
    // is a duration the user could have meant, and this one they did not type.
    const minutes = chosen.amount.trim() === '' ? Number.NaN : Number(chosen.amount) * choice.minutes;
    onChange({
      ...draft,
      schedule: chosen.mode === 'every'
        ? { mode: 'every', minutes }
        : { mode: 'once', at: chosen.onceAt ? new Date(chosen.onceAt).toISOString() : '' },
    });
  };

  /** The text a field currently holds, from wherever that field's value lives. */
  const textOf = (field: FieldKey) => field === 'amount' ? amount
    : field === 'name' ? draft.name
      : field === 'description' ? draft.description : draft.instruction;

  const write = (field: FieldKey, text: string) => {
    switch (field) {
      case 'amount': setAmount(text); publish({ amount: text }); return;
      case 'name': onChange({ ...draft, name: text }); return;
      case 'description': onChange({ ...draft, description: text }); return;
      case 'instruction': onChange({ ...draft, instruction: text }); return;
    }
  };

  const register = (field: FieldKey) => (element: HTMLInputElement | HTMLTextAreaElement | null) => {
    if (element) editors.current.set(field, element); else editors.current.delete(field);
  };
  const track = (field: FieldKey) => (element: HTMLInputElement | HTMLTextAreaElement) => {
    setCaret({ field, start: element.selectionStart ?? 0, end: element.selectionEnd ?? 0 });
  };

  /**
   * A tap on a field opens the device keyboard under it, and takes the caret.
   *
   * Deliberately not `onFocus` alone. Done is `setActive(null)`: it puts the keys
   * away and never moves DOM focus, so the field is still the focused element and
   * tapping it again fires no focus event — the keys could not be brought back
   * until some other field was tapped first. A click fires whether or not the
   * field already had focus, and a tap is also what places the caret, so both the
   * activation and the caret are read from it.
   */
  const tapField = (field: FieldKey) => (element: HTMLInputElement | HTMLTextAreaElement) => {
    setActive(field);
    track(field)(element);
  };

  /**
   * One key press, applied to the active field through the shared grapheme
   * helpers, with the caret put back where the edit left it.
   *
   * The caret is read from the field itself when this component has not seen a
   * selection event for it: a field focused by a tap has a real caret before any
   * key is pressed, and inserting at the end instead would move the user's text.
   */
  const applyKey = (mutate: (current: QuestionDraft) => QuestionDraft) => {
    if (!active) return;
    const text = textOf(active);
    const element = editors.current.get(active);
    const known = caret?.field === active ? caret : null;
    const start = known ? known.start : element?.selectionStart ?? text.length;
    const end = known ? known.end : element?.selectionEnd ?? text.length;
    const next = mutate(selectQuestion(text, start, end));
    write(active, next.text);
    setCaret({ field: active, start: next.start, end: next.end });
    restoring.current = { field: active, start: next.start, end: next.end };
  };

  /** The device's own keyboard, under the field it edits. */
  const inlineKeyboard = (field: FieldKey) => (
    <section className="question-keyboard" ref={keys} aria-label={`${FIELD_LABELS[field]} keyboard`}>
      <KeyboardKeys busy={saving}
        onInsert={(text) => applyKey((current) => insertQuestion(current, text))}
        onErase={() => applyKey(eraseQuestion)}
        onDone={() => setActive(null)} />
    </section>
  );

  // No write binding refuses the save whatever the fields say, and so does an
  // unreachable provider: neither is a complaint about the draft, and showing one
  // would imply that fixing the fields would let it save.
  const refusal = writeAvailable ? draftRefusal(draft, availability) : AGENT_UNAVAILABLE_REASON;
  const preview = formatSchedule(draft.schedule, ZONE);

  return <section className="device-subscreen device-task-form" aria-label={title}
    // Escape leaves the editor, but not while the caller is awaiting a write: the
    // result would then arrive with nowhere to be reported.
    onKeyDown={(event) => { if (event.key === 'Escape' && !saving) onCancel(); }}>
    <header className="device-subheader">
      <button onClick={onCancel} disabled={saving}><BackIcon />Back</button>
      <div className="device-page-title"><h1>{title}</h1></div>
    </header>

    <div className="device-subcontent" tabIndex={0}>
      <section className="device-panel">
        <label className="device-section-label" htmlFor="agent-task-name">Name</label>
        <input id="agent-task-name" ref={(element) => { name.current = element; register('name')(element); }}
          inputMode="none" value={draft.name} disabled={saving}
          onFocus={(event) => tapField('name')(event.currentTarget)}
          onClick={(event) => tapField('name')(event.currentTarget)}
          onSelect={(event) => track('name')(event.currentTarget)}
          onChange={(event) => { track('name')(event.currentTarget); onChange({ ...draft, name: event.target.value }); }} />
        {active === 'name' && inlineKeyboard('name')}

        <label className="device-section-label" htmlFor="agent-task-description">Description</label>
        <textarea id="agent-task-description" rows={3} ref={register('description')}
          inputMode="none" value={draft.description} disabled={saving}
          onFocus={(event) => tapField('description')(event.currentTarget)}
          onClick={(event) => tapField('description')(event.currentTarget)}
          onSelect={(event) => track('description')(event.currentTarget)}
          onChange={(event) => { track('description')(event.currentTarget); onChange({ ...draft, description: event.target.value }); }} />
        {active === 'description' && inlineKeyboard('description')}

        <span className="device-section-label" id="agent-schedule-label">Schedule</span>
        <div className="device-actions" role="group" aria-labelledby="agent-schedule-label">
          {SCHEDULE_MODES.map((option) => <button key={option.id} disabled={saving}
            aria-pressed={mode === option.id}
            // The interval field the keyboard is editing does not exist in the
            // one-time mode, so the keyboard moves to a field that does.
            onClick={() => { if (option.id !== 'every' && active === 'amount') setActive('name'); setMode(option.id); publish({ mode: option.id }); }}>{option.label}</button>)}
        </div>

        {mode === 'every' ? <div className="device-actions">
          <label className="device-section-label" htmlFor="agent-task-amount">Repeat every</label>
          <input id="agent-task-amount" ref={register('amount')} inputMode="none" value={amount} disabled={saving}
            onFocus={(event) => tapField('amount')(event.currentTarget)}
            onClick={(event) => tapField('amount')(event.currentTarget)}
            onSelect={(event) => track('amount')(event.currentTarget)}
            onChange={(event) => { track('amount')(event.currentTarget); setAmount(event.target.value); publish({ amount: event.target.value }); }} />
          {active === 'amount' && inlineKeyboard('amount')}
          <label className="device-section-label" htmlFor="agent-task-unit">Unit</label>
          <select id="agent-task-unit" value={unit} disabled={saving}
            onChange={(event) => { setUnit(event.target.value); publish({ unit: event.target.value }); }}>
            {INTERVAL_CHOICES.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select>
        </div> : <div className="device-actions">
          <label className="device-section-label" htmlFor="agent-task-once">Once on</label>
          <input id="agent-task-once" type="datetime-local" value={onceAt} disabled={saving}
            onChange={(event) => { setOnceAt(event.target.value); publish({ onceAt: event.target.value }); }} />
        </div>}
        {/* The zone is named rather than assumed: a time the user cannot place is
            worse than no schedule, and the device may not be in their zone. */}
        <p className="device-note" role="status">{preview}{mode === 'once' ? ` · times shown in ${ZONE}` : ''}</p>

        <label className="device-section-label" htmlFor="agent-task-instruction">Instruction</label>
        <textarea id="agent-task-instruction" rows={3} ref={register('instruction')}
          inputMode="none" value={draft.instruction} disabled={saving}
          onFocus={(event) => tapField('instruction')(event.currentTarget)}
          onClick={(event) => tapField('instruction')(event.currentTarget)}
          onSelect={(event) => track('instruction')(event.currentTarget)}
          onChange={(event) => { track('instruction')(event.currentTarget); onChange({ ...draft, instruction: event.target.value }); }} />
        {active === 'instruction' && inlineKeyboard('instruction')}
      </section>
    </div>

    {/* The failure sits above the footer rather than inside the scrolled panel, so
        it is visible at the moment Save was pressed whatever the form's length. */}
    {error && <p id="agent-task-error" role="alert">{error}</p>}
    <div className="device-actions">
      <button onClick={onCancel} disabled={saving}>Cancel</button>
      <button className="device-action-primary" disabled={saving || refusal !== null}
        aria-describedby={[error ? 'agent-task-error' : null, refusal ? 'agent-task-refusal' : null].filter(Boolean).join(' ') || undefined}
        onClick={onSave}>
        {saving ? 'Saving…' : 'Save task'}
      </button>
    </div>
    {refusal && <p id="agent-task-refusal" className="device-note" role="status">{refusal}</p>}
  </section>;
}
