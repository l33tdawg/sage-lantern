import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { communityLabel } from './community.ts';
import { deliveryLabel, folderItems, folderNotice, folderUnavailable, loadMessageFolders, outgoingLabel, previewPrefix } from './message-threads.ts';

const key = 'a'.repeat(64);
const inbox = { id: 'message-in', peer_name: `north:${key}`, text: 'Hello', state: 'delivered', created_at: '2026-09-09T00:00:00Z' };
const outbox = { id: 'message-out', peer_name: `north:${key}`, text: 'Are you there?', state: 'queued', created_at: '2026-09-09T00:01:00Z' };

test('one unreadable folder never hides or rejects the other', async () => {
  const loads = await loadMessageFolders(async (box) => {
    if (box === 'inbox') throw new Error('Community unavailable');
    return { items: [outbox], more: false };
  });
  assert.deepEqual(folderUnavailable(loads), ['inbox']);
  assert.deepEqual(folderItems(loads).map((item) => [item.id, item.box]), [['message-out', 'outbox']]);
  assert.equal(folderNotice('inbox'), 'Inbox unavailable. Try Refresh.');
  assert.equal(folderNotice('outbox'), 'Outbox unavailable. Try Refresh.');
});

test('both folders are asked for and tagged when their routes answer', async () => {
  const asked = [];
  const loads = await loadMessageFolders(async (box) => {
    asked.push(box);
    return { items: [box === 'inbox' ? inbox : outbox], more: box === 'inbox' };
  });
  assert.deepEqual(asked, ['inbox', 'outbox']);
  assert.deepEqual(folderUnavailable(loads), []);
  assert.deepEqual(folderItems(loads).map((item) => item.box), ['inbox', 'outbox']);
});

test('a queued row is labelled not delivered yet, never as sent', () => {
  assert.equal(outgoingLabel('queued'), 'Not delivered yet');
  assert.equal(deliveryLabel({ box: 'outbox', state: 'queued' }), 'Not delivered yet');
  assert.notEqual(deliveryLabel({ box: 'outbox', state: 'queued' }), communityLabel('sent'));
  assert.equal(deliveryLabel({ box: 'outbox', state: 'sent' }), communityLabel('sent'));
  assert.equal(deliveryLabel({ box: 'outbox', state: 'failed' }), 'Not sent');
  assert.equal(deliveryLabel({ box: 'inbox', state: 'queued' }), 'received');
  assert.equal(previewPrefix({ box: 'outbox', state: 'queued' }), 'You · Not delivered yet: ');
  assert.equal(previewPrefix({ box: 'outbox', state: 'delivered' }), 'You · Delivered: ');
  assert.equal(previewPrefix({ box: 'inbox', state: 'delivered' }), '');
});

test('the chats screen keeps one folder failure local and never blanks the list', () => {
  const source = readFileSync(new URL('../app/device/messages.tsx', import.meta.url), 'utf8');
  assert.match(source, /loadMessageFolders\(/);
  assert.match(source, /folderNotice\(box\)/);
  assert.match(source, /previewPrefix\(thread\.last\)/);
  assert.match(source, /deliveryLabel\(message\)/);
  assert.doesNotMatch(source, /Chats unavailable/);
});
