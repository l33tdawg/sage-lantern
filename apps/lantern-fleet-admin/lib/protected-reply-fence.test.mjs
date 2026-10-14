/**
 * Every protected reply that is adopted has to be judged against the authority
 * its request was issued under.
 *
 * The status poll is not that judgment. `observeSessionContext` invalidates only
 * *after* a reading has shown the rotation, so a reply that lands inside that
 * window was previously adopted whole: a conversation, a page of saved history,
 * a typed review, a submitted question. These assertions pin the fence to the
 * paths that consume such a reply, and pin the shared primitive it is built on.
 *
 * The behavioural half — that the fence refuses the stale reply and admits the
 * current one — is covered by `device-session.test.mjs`,
 * `conversation-request.test.mjs` and the browser driver
 * `work/ember-lantern-home-20261002/drive-auth-fixture.mjs` (scenarios 13–16).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const session = read('./device-session.ts');
const threads = read('./conversation-threads.ts');
const talk = read('../app/device/talk.tsx');
const review = read('../app/device/question-review.tsx');
const correction = read('../app/device/question-correction.tsx');
const suggested = read('../app/device/suggested-question.tsx');
const dictation = read('../app/device/dictation.tsx');

test('the shared fence separates an unestablished authority from a legacy device', () => {
  // `observedContext === null` is both "nothing observed yet" and "the device
  // publishes no context". Reading those as one is how a reply that cannot be
  // judged gets accepted.
  assert.match(session, /let observedState: 'unknown' \| 'legacy' \| 'context' \| 'lost' = 'unknown';/);
  assert.match(session, /export type EstablishedContext =\n  \| \{ state: 'unknown' \}\n  \| \{ state: 'legacy'; generation: number \}\n  \| \{ state: 'lost' \}\n  \| \{ state: 'context'; context: SessionContext; generation: number \};/);
  assert.match(session, /export function establishedSessionContext\(\): EstablishedContext \{/);
  assert.match(session, /export function fenceProtectedReply\(issuedUnder: EstablishedContext, reply: SessionContext \| null\): ReplyFence \{/);
  // A replaced token clears the established authority rather than leaving the
  // dead epoch in place for the next comparison.
  assert.match(session, /observedContext = null;\n  observedState = 'unknown';/);
  // Neither half of the fence is optional: the reply must be the one this
  // request was admitted under, and that authority must still be the one held.
  assert.match(session, /if \(!sessionContextEqual\(expected, reply\)\) return 'not-issued-under';/);
  assert.match(session, /if \(!sessionContextEqual\(current, reply\)\) return 'moved';/);
  // A refusal drops the authority and tells the one seam, so retained content
  // and cached challenges are invalidated rather than left in place.
  assert.match(session, /function loseAuthority\(\): ContextObservation \{/);
  assert.match(session, /observedState = 'lost';\n    authorityGeneration \+= 1;\n    for \(const listener of sessionListeners\) listener\(\);/);
  // An expectation captured before the authority was re-keyed can never be
  // satisfied again, however familiar its context looks.
  assert.match(session, /if \(issuedUnder\.generation !== authorityGeneration\) return 'not-issued-under';/);
});

test('the conversation and history helper fences before the parsers and caches see anything', () => {
  const issuedAt = threads.indexOf('const issuedUnder = establishedSessionContext();');
  assert.ok(issuedAt >= 0 && issuedAt < threads.indexOf('await fetch('), 'the authority is captured before the read is dispatched');
  assert.match(threads, /if \(fenceProtectedReply\(issuedUnder, stamp\) !== 'ok'\) throw new DeviceSessionError\('Conversation authority changed'\);/);
  assert.ok(threads.indexOf('fenceProtectedReply(') < threads.indexOf('return state;'), 'the fence precedes what the parser is handed');
  // The admission stamp is transport metadata and does not travel into the state
  // the parsers, the caches and `assert.deepEqual` see.
  assert.match(threads, /delete state\.session_context;/);
  assert.doesNotMatch(threads, /return JSON\.parse\(text\);/);
});

test('a typed review is opened only under the authority it was issued under', () => {
  const open = talk.slice(talk.indexOf('const openTyped = async () =>'), talk.indexOf('const view = deviceView('));
  const issuedAt = open.indexOf('const issuedUnder = publishedContext();');
  assert.ok(issuedAt >= 0 && issuedAt < open.indexOf("await voiceCommand('question/start'"), 'captured before the typed command');
  assert.match(open, /if \(issuedUnder === undefined\) \{ setTypedError\(TYPED_SESSION_CHANGED_NOTICE\); return; \}/);
  assert.match(open, /if \(!sessionContextEqual\(openedUnder, issuedUnder\) \|\| !contextMatches\(openedUnder\)\) \{/);
  // A refused review is cancelled with the credentials the reply carried, and
  // `onTypedSession` is what must not run.
  assert.match(open, /const cancelOpened = \(\) => \{ void voiceCommand\('cancel', \{ session_id: opened\.session_id, token: opened\.token \}, true\)\.catch\(\(\) => \{\}\); \};/);
  assert.ok(open.indexOf('cancelOpened();') < open.indexOf('onTypedSession?.(opened.session_id, photo)'), 'the refusal precedes adoption');
  assert.match(talk, /export const TYPED_SESSION_CHANGED_NOTICE = 'The device session changed\. Tap Type to try again\.';/);
});

test('review cancel and submit are fenced before anything downstream runs', () => {
  const cancel = review.slice(review.indexOf('const cancel = async (retry: boolean)'), review.indexOf('const submit = async ()'));
  assert.ok(cancel.indexOf('const issuedUnder = establishedSessionContext();') < cancel.indexOf("voiceCommand('review/cancel'"), 'captured before the cancellation');
  // `recordAgain` begins a new capture: it must not run on a cancellation that
  // belongs to an authority the device has left.
  assert.ok(cancel.indexOf('fenceProtectedReply(issuedUnder, cancelledUnder)') < cancel.indexOf('recordAgain()'), 'the fence precedes recordAgain');
  const submit = review.slice(review.indexOf('const submit = async ()'), review.indexOf('const actions ='));
  assert.ok(submit.indexOf('const issuedUnder = establishedSessionContext();') < submit.indexOf("voiceCommand('review/submit'"), 'captured before the submission');
  assert.match(submit, /if \(fenceProtectedReply\(issuedUnder, submittedUnder\) !== 'ok'\) throw new Error\('Unconfirmed submission'\);/);
  // The submission has already been sent: the refusal reports it unconfirmed and
  // there is exactly one send, so nothing here replays it automatically.
  assert.equal((submit.match(/voiceCommand\('review\/submit'/g) || []).length, 1, 'a refused submission is never replayed automatically');
});

test('correction and follow-up adopt their session only under their own authority', () => {
  const correct = correction.slice(correction.indexOf('const submit = async () =>'), correction.indexOf('return <section'));
  assert.ok(correct.indexOf('const issuedUnder = establishedSessionContext();') < correct.indexOf("fetch('http://127.0.0.1:3197/correct'"), 'captured before the write');
  assert.ok(correct.indexOf('fenceProtectedReply(issuedUnder, replySessionContext(reply))') < correct.indexOf('attempts.confirmed(sourceId)'), 'the fence precedes the confirmation the attempt is recorded against');
  assert.ok(correct.indexOf('fenceProtectedReply(issuedUnder, replySessionContext(reply))') < correct.indexOf('submitted(session.session_id, photo)'), 'the fence precedes adoption');
  const follow = suggested.slice(suggested.indexOf('const submit = async () =>'), suggested.indexOf('const actions ='));
  assert.ok(follow.indexOf('const issuedUnder = establishedSessionContext();') < follow.indexOf("voiceCommand('followup'"), 'captured before the follow-up');
  assert.ok(follow.indexOf('fenceProtectedReply(issuedUnder, replySessionContext(reply))') < follow.indexOf('submitted(session.session_id, submission.current.question)'), 'the fence precedes adoption');
});

test('dictation fences the microphone it opens and every reading it renders', () => {
  const start = dictation.slice(dictation.indexOf('const start = async () =>'), dictation.indexOf('const stop = async () =>'));
  assert.ok(start.indexOf('const issuedUnder = establishedSessionContext();') < start.indexOf("voiceCommand('start'"), 'captured before the microphone is opened');
  assert.match(start, /if \(fenceProtectedReply\(issuedUnder, replySessionContext\(reply\)\) !== 'ok'\) \{/);
  assert.ok(start.indexOf('fenceProtectedReply(issuedUnder, replySessionContext(reply))') < start.indexOf('void voiceCommand(\'cancel\''), 'a refused session is cancelled with the credentials the reply carried');
  const poll = dictation.slice(dictation.indexOf('const poll = (current: TouchSession)'), dictation.indexOf('const start = async () =>'));
  assert.ok(poll.indexOf('const issuedUnder = establishedSessionContext();') < poll.indexOf('await voiceCommand('), 'captured before the reading is taken');
  assert.ok(poll.indexOf('fenceProtectedReply(issuedUnder, replySessionContext(reply))') < poll.indexOf('setStatus(next);'), 'the fence precedes rendering the reading');
});

test('every audited path judges a reply rather than trusting token equality', () => {
  // The shared helper where the surface has no reading of its own, and the
  // owner's own published-context comparison where it does.
  const audited = {
    threads: [/fenceProtectedReply\(issuedUnder, stamp\)/, /const issuedUnder = establishedSessionContext\(\);/],
    talk: [/sessionContextEqual\(openedUnder, issuedUnder\)/, /const issuedUnder = publishedContext\(\);/],
    review: [/fenceProtectedReply\(issuedUnder, cancelledUnder\)/, /const issuedUnder = establishedSessionContext\(\);/],
    correction: [/fenceProtectedReply\(issuedUnder, replySessionContext\(reply\)\)/, /const issuedUnder = establishedSessionContext\(\);/],
    suggested: [/fenceProtectedReply\(issuedUnder, replySessionContext\(reply\)\)/, /const issuedUnder = establishedSessionContext\(\);/],
    dictation: [/fenceProtectedReply\(issuedUnder, replySessionContext\(reply\)\)/, /const issuedUnder = establishedSessionContext\(\);/],
  };
  for (const [name, source] of Object.entries({ threads, talk, review, correction, suggested, dictation })) {
    const [judge, capture] = audited[name];
    assert.match(source, judge, `${name} must judge the reply's own context`);
    assert.match(source, capture, `${name} must capture the authority it is acting under`);
  }
});
