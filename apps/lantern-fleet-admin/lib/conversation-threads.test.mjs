import test from 'node:test';
import assert from 'node:assert/strict';
import { groupConversations, parseConversationState } from './conversation-threads.ts';
import { messageThreads, replyContact } from './message-threads.ts';
const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const item = (n, fields = {}) => ({ session_id: id(n), created_at: `2026-09-09T10:00:0${n}Z`, transcript: `Question ${n}`, response: `Answer ${n}`, ...fields });

test('the empty active chat is not an extra conversation row', () => {
  const current = { thread_id: id(9), revision: 1, active_session_id: null, items: [], context_turn_limit: 4 };
  assert.deepEqual(groupConversations([], current), []);
  assert.deepEqual(groupConversations([item(1)], current).map((thread) => thread.title), ['Question 1']);
});

test('legacy questions stay separate; current and saved turns merge without duplicate or superseded answers', () => {
  const old = item(1, { thread_id: id(9) });
  const correction = item(2, { thread_id: id(9), supersedes: id(1) });
  const current = { thread_id: id(9), revision: 3, active_session_id: null, items: [correction], context_turn_limit: 4 };
  const groups = groupConversations([old, correction, item(3)], parseConversationState(current));
  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].items, [correction]);
  assert.equal(groups[1].id, id(3));
  assert.throws(() => parseConversationState({ ...current, items: [item(4)] }));
});

test('inbox and sent messages join by SAGE key AND network, never display names', () => {
  const key = 'a'.repeat(64);
  const messages = [
    { id: 'one', peer_name: `north:${key}`, text: 'Hello', box: 'inbox', state: 'delivered', created_at: '2026-09-09T00:00:00Z' },
    { id: 'two', peer_name: `north:${key}`, text: 'Reply', box: 'outbox', state: 'sent', created_at: '2026-09-09T00:01:00Z' },
    { id: 'three', peer_name: `south:${key}`, text: 'Separate route', box: 'inbox', state: 'delivered', created_at: '2026-09-09T00:02:00Z' },
  ];
  assert.equal(messageThreads(messages, 'inbox').length, 2);
  assert.equal(messageThreads(messages, 'inbox', 'north')[0].messages.length, 2);
  assert.equal(messageThreads(messages, 'sent').length, 1);
  assert.equal(messageThreads(messages, 'outbox').length, 0);
  assert.equal(messageThreads(messages.map((row) => ({ ...row, peer_name: 'Alex' })), 'inbox').length, 2);
  const contacts = [{ id: 'bound-contact', agent_id: key, chain_id: 'north', name: 'Alex' }, { id: 'other-route', agent_id: key, chain_id: 'south', name: 'Alex' }];
  assert.equal(replyContact(messages[0], contacts).id, 'bound-contact');
  assert.equal(replyContact(messages[0], [contacts[1]]), null);
  assert.equal(replyContact(messages[0], [contacts[0], contacts[0]]), null);
});
