'use client';

/**
 * Agent: the task destination.
 *
 * The governing decision is unambiguous that this build has **no approved device
 * task binding** — the existing action unions expose none, and the institutional
 * SAGE task list Rook and Ember use is project coordination, not an authenticated
 * personal scheduler. So this screen shows the reference destination honestly
 * instead of inventing a backend: with no provider it says tasks are unavailable,
 * and it never says "no tasks", which would assert that the device answered.
 *
 * What the screen is careful about:
 *
 *  - **A draft cannot outlive its authority.** The draft lives in this
 *    component's state and every authority boundary runs `discardDraft()`. A
 *    replaced or cleared session token, a rotated epoch and a lost authority all
 *    arrive through `subscribeDeviceSession`; a lock additionally unmounts this
 *    whole screen, because the device screen is rendered only while unlocked —
 *    which is the same discard by a different route, and the reason the draft is
 *    deliberately not mirrored anywhere that would survive the mount. A relock by
 *    the *same* owner is therefore discarded too: the new mount starts empty.
 *  - **Nothing is persisted.** No localStorage, no IndexedDB, no SAGE record. A
 *    draft is screen state and nothing else, exactly as the decision requires.
 *  - **Cards come from the provider or not at all.** With no provider there are no
 *    cards, and the write actions report why they cannot act rather than
 *    simulating a save. Synthetic tasks belong to the labelled fixture, which
 *    passes a provider and a write command explicitly.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AGENT_UNAVAILABLE_REASON, EMPTY_DRAFT, agentAvailability, agentListState, discardDraft,
  taskCard, type AgentAvailability, type AgentTaskDraft, type TaskProvider, type TaskSchedule,
} from '../../lib/agent-tasks';
import { subscribeDeviceSession } from '../../lib/device-session';
import { AgentIcon, BackIcon } from './icons';
import AgentTaskForm from './agent-task-form';

/** The zone a one-time task is rendered in, named on the card so it is placeable. */
const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time';

/**
 * What a failed write may be reported as.
 *
 * A rejected command says the command did not complete; it does not say the
 * provider wrote nothing. The device may have applied the change and failed to
 * answer, so neither sentence claims the list is unchanged — that claim would be
 * invented from a rejection, exactly the kind of unearned statement this screen
 * exists to avoid.
 */
const SAVE_FAILED_REASON = 'Could not save the task. It may not have been saved.';
const REMOVE_FAILED_REASON = 'Could not remove the task. It may still be listed.';

/** One task as an approved provider reports it. This screen never mints one. */
export type AgentTaskRecord = {
  id: string;
  name: string;
  description?: string;
  schedule: TaskSchedule | null;
  instruction?: string;
};

export default function AgentScreen({ close, provider = null, tasks, onSaveTask, onRemoveTask, onOpenHeartbeat }: {
  close: () => void;
  /** The task service as the screen sees it. Null in this build, and that is the point. */
  provider?: TaskProvider;
  /** What the provider reported, when it reported a complete list. */
  tasks?: readonly AgentTaskRecord[];
  /** The provider's own write commands. Absent here, which is why saving is refused. */
  onSaveTask?: (draft: AgentTaskDraft, taskId: string | null) => Promise<void>;
  onRemoveTask?: (taskId: string) => Promise<void>;
  /** Present only when a Heartbeat screen exists to open. */
  onOpenHeartbeat?: () => void;
}) {
  const availability: AgentAvailability = agentAvailability(provider);
  /**
   * What occupies the list region.
   *
   * An answered provider that handed us no list is *not* an empty list, so the
   * count is `NaN` rather than zero and the region reports that it cannot say.
   * Passing 0 there would be the screen asserting a fact nobody gave it.
   */
  const listState = agentListState(availability, tasks === undefined ? Number.NaN : tasks.length);

  const [form, setForm] = useState<'add' | 'edit' | null>(null);
  const [editing, setEditing] = useState<AgentTaskRecord | null>(null);
  const [draft, setDraft] = useState<AgentTaskDraft>(EMPTY_DRAFT);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const back = useRef<HTMLButtonElement>(null);
  const newTask = useRef<HTMLButtonElement>(null);
  const editRefs = useRef(new Map<string, HTMLButtonElement>());

  /**
   * The fence every deferred result has to pass before it may touch this screen.
   *
   * `operation` counts started writes, and both `discard()` and each new write
   * advance it. A result that arrives after the draft it belonged to is gone can
   * therefore see that it is stale and publish nothing — no completion, no error,
   * no busy flag and no focus move into whatever replaced the editor. `live`
   * covers the last route a late result could take: the unmount a lock performs.
   *
   * `live` is set in setup as well as cleared in cleanup, which is what makes this
   * effect replayable. React runs an effect's setup, cleanup and setup again when it
   * double-invokes in development — the App Router does that by default — so a
   * cleanup-only flag would leave the screen permanently unable to publish anything
   * of its own: a save would stay at "Saving…" for good. Invalidation still happens
   * in cleanup, so work started before a replay stays stale.
   */
  const operation = useRef(0);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; operation.current += 1; };
  }, []);
  const settleAllowed = (token: number) => live.current && operation.current === token;

  /** Drop the held draft and leave the form. The seam is named so callers cannot forget it. */
  const discard = useCallback(() => {
    // Anything in flight belongs to the draft being discarded, and nothing it
    // reports afterwards is about the screen replacing it. The busy flag is
    // cleared here rather than by that result, which may never touch this screen
    // again — otherwise a discarded write would leave the new view permanently
    // busy. The error goes with the draft for the same reason.
    operation.current += 1;
    setWorking(false);
    setError('');
    setDraft(discardDraft() ?? EMPTY_DRAFT);
    setForm(null);
    setEditing(null);
  }, []);

  useEffect(() => {
    back.current?.focus();
    // A hidden document is a lock or a sleep, and the device keeps the screen
    // mounted through both: the poll is what discovers the change, and by then
    // the draft has already been readable on a screen nobody is looking at.
    const unsubscribe = subscribeDeviceSession(discard);
    const hide = () => { if (document.hidden) discard(); };
    document.addEventListener('visibilitychange', hide);
    return () => { document.removeEventListener('visibilitychange', hide); unsubscribe(); };
  }, [discard]);

  const openAdd = () => { setDraft(EMPTY_DRAFT); setEditing(null); setError(''); setForm('add'); };
  const openEdit = (record: AgentTaskRecord) => {
    setDraft({ name: record.name, description: record.description ?? '', schedule: record.schedule, instruction: record.instruction ?? '' });
    setEditing(record);
    setError('');
    setForm('edit');
  };

  /** Leave the form and put focus back on the control that opened it. */
  const closeForm = () => {
    const returning = editing?.id ?? null;
    discard();
    setError('');
    // The frame runs after this handler returns, so it has to present the same
    // fence a write result does: a lock, a sleep or a revocation in between would
    // otherwise move focus on a screen nobody is looking at. The token is taken
    // after `discard()`, so it is the generation this view now owns.
    const focusToken = operation.current;
    requestAnimationFrame(() => {
      if (settleAllowed(focusToken)) ((returning && editRefs.current.get(returning)) || newTask.current)?.focus();
    });
  };

  /** Open a write and return the token its result has to present to be published. */
  const beginWrite = () => {
    const token = operation.current + 1;
    operation.current = token;
    setWorking(true);
    setError('');
    return token;
  };

  const save = async () => {
    if (!onSaveTask || working) return;
    const token = beginWrite();
    let saved = false;
    try {
      await onSaveTask(draft, editing?.id ?? null);
      saved = true;
    } catch {
      if (settleAllowed(token)) setError(SAVE_FAILED_REASON);
    } finally {
      // The busy flag belongs to the write that set it: a stale one must not clear
      // the flag of whatever replaced it.
      if (settleAllowed(token)) setWorking(false);
    }
    if (saved && settleAllowed(token)) closeForm();
  };

  const remove = async (record: AgentTaskRecord) => {
    if (!onRemoveTask || working) return;
    const token = beginWrite();
    try {
      await onRemoveTask(record.id);
    } catch {
      if (settleAllowed(token)) setError(REMOVE_FAILED_REASON);
    } finally {
      if (settleAllowed(token)) setWorking(false);
    }
  };

  if (form) {
    return <AgentTaskForm
      // The editor seeds its interval field from the draft it is given, so
      // switching between adding and editing has to remount it: a reused instance
      // would keep the previous task's text in an unchanged input.
      key={form === 'edit' ? `edit-${editing?.id}` : 'add'}
      title={form === 'edit' ? 'Edit task' : 'New task'}
      draft={draft}
      availability={availability}
      writeAvailable={!!onSaveTask}
      saving={working}
      error={error}
      onChange={setDraft}
      onCancel={closeForm}
      onSave={() => void save()}
    />;
  }

  return <section className="device-subscreen" aria-label="Agent" onKeyDown={(event) => { if (event.key === 'Escape' && !working) close(); }}>
    <header className="device-subheader">
      <button ref={back} disabled={working} onClick={close}><BackIcon />Back</button>
      <div className="device-page-title"><AgentIcon /><h1>Agent</h1></div>
      {/* The form is openable whatever the service says: the decision allows the
          composition to be inspected, and only the save is refused. */}
      <button ref={newTask} className="device-page-bar-action" disabled={working} onClick={openAdd}>New task</button>
    </header>

    <div className="device-subcontent" tabIndex={0}>
      {error && <p role="alert">{error}</p>}

      {listState.state === 'unavailable' && <div className="device-panel" role="status">
        {/* The glyph is sized by its own rule: an icon carrying only a viewBox
            fills whatever box it lands in, which is the trap the Home tiles hit. */}
        <span className="device-panel-glyph" aria-hidden="true"><AgentIcon /></span>
        <h2>Tasks unavailable</h2>
        <p>{listState.reason}</p>
      </div>}

      {listState.state === 'empty' && <div className="device-panel" role="status">
        <span className="device-panel-glyph" aria-hidden="true"><AgentIcon /></span>
        <h2>Add your first task</h2>
        <p>{listState.reason}</p>
      </div>}

      {listState.state === 'ready' && <ul aria-label="Scheduled tasks">
        {(tasks ?? []).map((record) => {
          const card = taskCard(record, ZONE);
          return <li key={card.id}>
            <section className="device-panel">
              <h2>{card.name}</h2>
              {card.description && <p>{card.description}</p>}
              <p className="device-note">{card.schedule}</p>
              {card.instruction && <p>{card.instruction}</p>}
              <div className="device-actions">
                <button ref={(element) => { if (element) editRefs.current.set(card.id, element); else editRefs.current.delete(card.id); }}
                  disabled={working} onClick={() => openEdit(record)}>Edit</button>
                <button disabled={working || !onRemoveTask} aria-describedby={onRemoveTask ? undefined : 'agent-remove-reason'}
                  onClick={() => void remove(record)}>Remove</button>
              </div>
            </section>
          </li>;
        })}
      </ul>}
      {listState.state === 'ready' && !onRemoveTask && <p id="agent-remove-reason" className="device-note" role="status">{AGENT_UNAVAILABLE_REASON}</p>}

      {/* The schedule a task runs on is not a property of the list, so the screen
          that owns tasks is where its own state is reached from. Back returns
          here, because the origin is recorded when this is opened rather than
          being inferred from whichever screen happens to be underneath. */}
      {onOpenHeartbeat && <section className="device-panel">
        <span className="device-section-label">Scheduled runs</span>
        <div className="device-history-setting"><h2>Heartbeat</h2><button disabled={working} onClick={onOpenHeartbeat}>Open</button></div>
        <p className="device-note">When Lantern checks in on its own, and how often.</p>
      </section>}
    </div>
  </section>;
}
