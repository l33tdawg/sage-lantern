/**
 * Agent destination: the two states that must not be confused, schedule wording,
 * and draft ownership.
 *
 * Adversarial in the same spirit as the rest of this app's helpers: the cases
 * that matter are the ones where a wrong answer would be *plausible*. "No tasks
 * yet" on a device that was never asked is the failure this file exists to make
 * impossible.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AGENT_EMPTY_REASON, AGENT_UNAVAILABLE_REASON, EMPTY_DRAFT,
  agentAvailability, agentListState, discardDraft, draftForOwner, draftRefusal,
  formatInterval, formatOneTime, formatSchedule, keepDraft, taskCard,
} from './agent-tasks.ts';

const approved = { approved: true, answered: true, complete: true };

test('an unavailable service is never reported as an empty list', () => {
  // The distinction the whole screen rests on. Every route to "we could not ask"
  // must produce the unavailable sentence and must NOT produce the empty one.
  const cases = [
    ['no provider at all', null],
    ['a provider that is not approved', { approved: false, answered: true, complete: true }],
    ['approved but silent', { approved: true, answered: false, complete: true }],
    ['approved and answered, but incomplete', { approved: true, answered: true, complete: false }],
  ];
  for (const [label, provider] of cases) {
    const availability = agentAvailability(provider);
    assert.equal(availability.state, 'unavailable', label);
    assert.equal(availability.reason, AGENT_UNAVAILABLE_REASON, label);
    assert.notEqual(availability.reason, AGENT_EMPTY_REASON, label);
    // And the list region must agree, whatever the count claims to be.
    assert.equal(agentListState(availability, 0).state, 'unavailable', label);
    assert.equal(agentListState(availability, 7).state, 'unavailable', label);
  }
});

test('the empty sentence is reachable only from a verified complete answer', () => {
  const ready = agentAvailability(approved);
  assert.equal(ready.state, 'ready');
  assert.equal(ready.reason, '');

  const empty = agentListState(ready, 0);
  assert.equal(empty.state, 'empty');
  assert.equal(empty.reason, AGENT_EMPTY_REASON);

  // A verified provider with tasks is simply ready; it is not "empty" and its
  // reason must not carry the empty sentence.
  const populated = agentListState(ready, 3);
  assert.equal(populated.state, 'ready');
  assert.equal(populated.reason, '');
});

test('a repeating interval always states its unit', () => {
  assert.equal(formatInterval(1), 'Every 1 minute');
  assert.equal(formatInterval(30), 'Every 30 minutes');
  assert.equal(formatInterval(60), 'Every 1 hour');
  assert.equal(formatInterval(120), 'Every 2 hours');
  assert.equal(formatInterval(1440), 'Every 1 day');
  assert.equal(formatInterval(2880), 'Every 2 days');
  // Not a whole number of hours, so it is stated in minutes rather than rounded
  // into a unit it does not divide.
  assert.equal(formatInterval(90), 'Every 90 minutes');
  assert.equal(formatInterval(100), 'Every 100 minutes');
  // 1500 is a whole number of hours, so it is stated in hours: the unit is
  // chosen by divisibility, not by whichever keeps the number smallest-looking.
  assert.equal(formatInterval(1500), 'Every 25 hours');
});

test('an interval that is not a real duration is not scheduled', () => {
  for (const bad of [0, -5, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(formatInterval(bad), 'Not scheduled', String(bad));
  }
});

test('a one-time schedule names its timezone, and refuses to invent one', () => {
  const at = '2026-10-03T14:30:00Z';
  const rendered = formatOneTime(at, 'UTC');
  assert.match(rendered, /^Once, /);
  assert.match(rendered, /\(UTC\)$/);
  assert.match(rendered, /2026/);

  // The zone is always visible, including when it is taken from the host.
  const hostZone = formatOneTime(at);
  assert.match(hostZone, /^Once, /);
  assert.match(hostZone, /\(.+\)$/);

  // An unparseable moment says so rather than rendering "Invalid Date".
  for (const bad of ['', 'not a date', 'yesterday']) {
    assert.equal(formatOneTime(bad, 'UTC'), 'Not scheduled', bad);
  }
});

test('a zone the runtime cannot resolve is refused, not thrown out of a card', () => {
  // The zone arrives from the host and from provider records, so an unusable one
  // must degrade to "Not scheduled" instead of escaping as a RangeError while a
  // card is rendering. The card is asserted too: that is the path it travelled.
  const at = '2026-10-03T14:30:00Z';
  assert.doesNotThrow(() => formatOneTime(at, 'Unknown/Device'));
  assert.equal(formatOneTime(at, 'Unknown/Device'), 'Not scheduled');
  assert.equal(formatSchedule({ mode: 'once', at }, 'Unknown/Device'), 'Not scheduled');
  assert.equal(
    taskCard({ id: 't4', name: 'Dentist', schedule: { mode: 'once', at } }, 'Unknown/Device').schedule,
    'Not scheduled',
  );
  // The refusal is specific to the zone, not a blanket fallback for one-time
  // schedules: a resolvable zone still names itself.
  assert.match(formatOneTime(at, 'UTC'), /\(UTC\)$/);
});

test('an unchosen schedule is not scheduled', () => {
  assert.equal(formatSchedule(null), 'Not scheduled');
  assert.equal(formatSchedule({ mode: 'every', minutes: 15 }), 'Every 15 minutes');
  assert.match(formatSchedule({ mode: 'once', at: '2026-10-03T14:30:00Z' }, 'UTC'), /^Once, /);
});

test('saving is refused for the unavailable reason before any field is judged', () => {
  // A perfectly filled draft still cannot be saved in this build, and the reason
  // shown must be the unavailability rather than a field complaint -- otherwise
  // the form would imply that fixing the fields would let it save.
  const complete = { name: 'Water the plants', description: '', schedule: { mode: 'every', minutes: 1440 }, instruction: 'Check the soil first.' };
  assert.equal(draftRefusal(complete, agentAvailability(null)), AGENT_UNAVAILABLE_REASON);
  assert.equal(draftRefusal(complete, agentAvailability({ approved: true, answered: false, complete: true })), AGENT_UNAVAILABLE_REASON);
});

test('the draft rules are the minimum that makes a task meaningful', () => {
  const ready = agentAvailability(approved);
  assert.equal(draftRefusal(EMPTY_DRAFT, ready), 'Give the task a name.');
  assert.equal(draftRefusal({ ...EMPTY_DRAFT, name: '  ' }, ready), 'Give the task a name.');
  assert.equal(draftRefusal({ ...EMPTY_DRAFT, name: 'Water the plants' }, ready), 'Choose when this task should run.');
  assert.equal(
    draftRefusal({ ...EMPTY_DRAFT, name: 'Water', schedule: { mode: 'every', minutes: 0 } }, ready),
    'Choose how often this task should run.',
  );
  assert.equal(
    draftRefusal({ ...EMPTY_DRAFT, name: 'Water', schedule: { mode: 'once', at: 'nonsense' } }, ready),
    'Choose when this task should run once.',
  );
  // A minimal but valid draft is saveable, and the optional fields stay optional.
  assert.equal(
    draftRefusal({ ...EMPTY_DRAFT, name: 'Water', schedule: { mode: 'every', minutes: 1440 } }, ready),
    null,
  );
});

test('the form cannot accept a duration the card calls not scheduled', () => {
  // These two functions must not each carry their own copy of "is this a real
  // duration". If they drift, the form saves a task whose card then reads "Not
  // scheduled". The agreement is asserted directly against what formatInterval
  // decides, so the two cannot disagree without failing here -- in either
  // direction, which is why valid durations are in the same sweep.
  const ready = agentAvailability(approved);
  const durations = [0, -5, Number.NaN, Number.POSITIVE_INFINITY, 15, 90, 1440];
  for (const minutes of durations) {
    const label = String(minutes);
    const draft = { ...EMPTY_DRAFT, name: 'Water', schedule: { mode: 'every', minutes } };
    if (formatInterval(minutes) === 'Not scheduled') {
      assert.notEqual(draftRefusal(draft, ready), null, `the form accepted ${label}`);
    } else {
      assert.equal(draftRefusal(draft, ready), null, `the form refused ${label}`);
    }
  }
});

test('only an unreachable provider refuses a draft, and only a verified count is empty', () => {
  const ready = agentAvailability(approved);
  const valid = { ...EMPTY_DRAFT, name: 'Water', schedule: { mode: 'every', minutes: 1440 } };

  // A verified empty list is a provider that answered, and it is precisely the
  // state the first task is added from. Its sentence must never be served as a
  // refusal, or adding the first task would be impossible on an empty device.
  const empty = agentListState(ready, 0);
  assert.equal(empty.state, 'empty');
  assert.equal(draftRefusal(valid, empty), null);
  assert.notEqual(draftRefusal(valid, empty), AGENT_EMPTY_REASON);

  // Only a verified zero proves there are no tasks. Any other count is a count
  // we do not have, which is not the same claim as an empty list.
  for (const bad of [Number.NaN, -1, 1.5, Number.POSITIVE_INFINITY]) {
    const label = String(bad);
    const state = agentListState(ready, bad);
    assert.equal(state.state, 'unavailable', label);
    assert.equal(state.reason, AGENT_UNAVAILABLE_REASON, label);
    assert.notEqual(state.reason, AGENT_EMPTY_REASON, label);
  }

  // The valid controls still hold, including a zero written as -0.
  assert.equal(agentListState(ready, 0).state, 'empty');
  assert.equal(agentListState(ready, -0).state, 'empty');
  assert.equal(agentListState(ready, 3).state, 'ready');
});

test('a draft cannot outlive the owner it was written under', () => {
  const draft = { ...EMPTY_DRAFT, name: 'Water the plants' };

  // No established owner means no draft is held at all.
  assert.equal(keepDraft(null, draft), null);
  assert.equal(draftForOwner(null, 'owner-a'), null);

  const held = keepDraft('owner-a', draft);
  assert.equal(draftForOwner(held, 'owner-a')?.name, 'Water the plants');

  // A different owner does not inherit it, and neither does no owner at all.
  assert.equal(draftForOwner(held, 'owner-b'), null);
  assert.equal(draftForOwner(held, null), null);
  assert.equal(draftForOwner(held, ''), null);

  assert.equal(discardDraft(), null);
  assert.equal(draftForOwner(discardDraft(), 'owner-a'), null);
});

test('a card shows the formatted schedule and never a raw expression', () => {
  const card = taskCard({ id: 't1', name: 'Water the plants', schedule: { mode: 'every', minutes: 1440 } });
  assert.equal(card.name, 'Water the plants');
  assert.equal(card.schedule, 'Every 1 day');
  assert.equal(card.description, '');
  assert.equal(card.instruction, '');

  const once = taskCard({ id: 't2', name: 'Dentist', schedule: { mode: 'once', at: '2026-10-03T14:30:00Z' } }, 'UTC');
  assert.match(once.schedule, /^Once, /);
  assert.doesNotMatch(once.schedule, /\d+\s+\d+\s+\*/, 'no cron expression may reach a card');

  const unscheduled = taskCard({ id: 't3', name: 'Later', schedule: null });
  assert.equal(unscheduled.schedule, 'Not scheduled');
});
