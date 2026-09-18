import assert from 'node:assert/strict';
import test from 'node:test';
import { communityLabel, communityRequest, parseCommunityResult, parseCommunityStatus, parseContacts, parseMessages, parseSharePreview, parseShares } from './community.ts';
import { parseDictationStatus, replaceParagraph } from './message-dictation.ts';
import { setDeviceToken } from './device-session.ts';
import { readFileSync } from 'node:fs';

const created_at = '2026-09-06T01:00:00Z';
const preview_id = '11111111-1111-4111-8111-111111111111';
const status = { schema: 'sage.lantern.community.v1', messaging: { available: false, reason: 'encrypted_storage_unavailable' }, sharing: { available: false, reason: 'public_identity_unavailable' }, dictation: { available: true } };
const preview = { preview_id, digest: 'a'.repeat(64), question: 'What is a seed?', answer: 'A seed can grow into a plant.', photo_included: false, expires_at: created_at };

test('capabilities require exact booleans, known reasons and the wire version', () => {
  assert.deepEqual(parseCommunityStatus(status), status);
  for (const invalid of [{ ...status, schema: 'other' }, { ...status, secret: 'extra' }, { ...status, messaging: { available: 1, reason: 'ready' } }, { ...status, messaging: { available: true, reason: 'not_ready' } }, { ...status, sharing: { available: false, reason: 'ignore safeguards' } }]) assert.throws(() => parseCommunityStatus(invalid));
});
test('message lists are bounded, duplicate-free and states do not imply read', () => {
  const item = { id: 'message-1', peer_name: 'Example person', text: '<script>not executable</script>', state: 'sent', created_at };
  assert.equal(parseMessages({ items: [item], more: false }).items[0].text, item.text);
  assert.equal(communityLabel('sent'), 'Sent · delivery unconfirmed');
  for (const items of [[item, item], [{ ...item, state: 'completed' }], [{ ...item, text: 'x'.repeat(8193) }], [{ ...item, token: 'hidden' }]]) assert.throws(() => parseMessages({ items, more: false }));
  assert.throws(() => parseMessages({ items: [], more: 'false' }));
});
test('contact identity is distinct from a name and untrusted URLs are not identities', () => {
  assert.equal(parseContacts({ items: [{ id: 'chain:' + 'a'.repeat(64), name: 'Example' }], more: false }).items.length, 1);
  assert.throws(() => parseContacts({ items: [{ id: 'https://example.com', name: 'Example' }], more: false }));
});
test('public previews cannot quietly include a photo or extra private context', () => {
  assert.deepEqual(parseSharePreview(preview), preview);
  for (const invalid of [{ ...preview, photo_included: true }, { ...preview, history: 'private' }, { ...preview, digest: 'not-a-hash' }, { ...preview, expires_at: 'yesterday' }, { ...preview, answer: 'x'.repeat(12001) }]) assert.throws(() => parseSharePreview(invalid));
});
test('submission state is explicit; queued is never accepted', () => {
  assert.equal(parseCommunityResult({ id: preview_id, state: 'pending', created_at }, 'share').state, 'pending');
  assert.notEqual(communityLabel('queued'), communityLabel('accepted'));
  assert.throws(() => parseCommunityResult({ id: preview_id, state: 'read', created_at }, 'share'));
  assert.throws(() => parseCommunityResult({ id: preview_id, state: 'accepted', created_at }, 'message'));
  assert.throws(() => parseShares({ items: [{ id: preview_id, question: 'Example?', state: 'committed', created_at }], more: false }));
});
test('dictation text only comes from a matching message review', () => {
  const value = { session_id: preview_id, purpose: 'message', phase: 'review', text: 'Meet near the water pump.' };
  assert.equal(parseDictationStatus(value, preview_id).text, value.text);
  for (const invalid of [{ ...value, purpose: 'answer' }, { ...value, session_id: 'other' }, { ...value, phase: 'recording' }, { ...value, text: 'x'.repeat(2049) }, { ...value, text: 'hello\nsecond paragraph' }]) assert.throws(() => parseDictationStatus(invalid, preview_id));
});
test('paragraph replacement preserves the other paragraphs and checks limits', () => {
  const original = ['First.', 'Second.', 'Third.'];
  assert.deepEqual(replaceParagraph(original, 1, 'Replacement.'), ['First.', 'Replacement.', 'Third.']);
  assert.deepEqual(original, ['First.', 'Second.', 'Third.']);
  assert.throws(() => replaceParagraph(original, 99, 'New.'));
  assert.throws(() => replaceParagraph(Array(32).fill('Paragraph.'), null, 'Extra.'));
  assert.throws(() => replaceParagraph(Array(4).fill('x'.repeat(2048)), null, 'Extra.'));
});
test('community requests are session-bound, local-only and reject oversized replies', async () => {
  const original = globalThis.fetch;
  const token = 'a'.repeat(43);
  try {
    setDeviceToken(null);
    globalThis.fetch = async () => { throw new Error('Must not fetch'); };
    await assert.rejects(communityRequest('status'), /Device locked/);
    setDeviceToken(token);
    globalThis.fetch = async (url, options) => {
      assert.equal(url, 'http://127.0.0.1:3197/community/status');
      assert.equal(options.headers['X-Lantern-Session'], token);
      assert.equal(options.redirect, 'error');
      assert.equal(options.credentials, 'omit');
      return Response.json(status);
    };
    assert.deepEqual(await communityRequest('status'), status);
    globalThis.fetch = async () => { setDeviceToken(null); return Response.json(status); };
    await assert.rejects(communityRequest('status'), /Device locked/);
    setDeviceToken(token);
    globalThis.fetch = async () => new Response('x'.repeat(262145), { headers: { 'content-type': 'application/json' } });
    await assert.rejects(communityRequest('status'), /Response too large/);
    globalThis.fetch = async () => new Response('private error', { status: 503 });
    await assert.rejects(communityRequest('status'), /Community unavailable/);
  } finally { globalThis.fetch = original; setDeviceToken(null); }
});
test('public and private interfaces keep consent separate and never render HTML', () => {
  const share = readFileSync(new URL('../app/device/share.tsx', import.meta.url), 'utf8');
  const messages = readFileSync(new URL('../app/device/messages.tsx', import.meta.url), 'utf8');
  const dictation = readFileSync(new URL('../app/device/dictation.tsx', import.meta.url), 'utf8');
  assert.match(share, /Public records and other people’s copies cannot be recalled/);
  assert.match(share, /preview_id: preview.preview_id, digest: preview.digest/);
  assert.match(share, /!checked/);
  assert.match(share, /photo sharing|Photo sharing/);
  assert.match(messages, /Check the chat before trying again/);
  assert.match(dictation, /purpose: 'message'/);
  assert.match(dictation, /Use this text/);
  for (const source of [share, messages, dictation]) assert.doesNotMatch(source, /dangerouslySetInnerHTML|localStorage|sessionStorage/);
});

test('recording completion cannot discard a message draft or regress a reviewed transcript', () => {
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  const dictation = readFileSync(new URL('../app/device/dictation.tsx', import.meta.url), 'utf8');
  assert.match(screen, /setPage\(\(current\) => current === 'messages' \|\| current === 'sharing' \? current : 'main'\)/);
  assert.match(dictation, /\['review', 'error', 'cancelled', 'cancelling'\]\.includes\(current.phase\) \? current/);
  assert.match(dictation, /mounted.current && !terminal.current/);
  assert.match(dictation, /Tap Cancel, then record again/);
});

test('unknown send preserves recovery identity when browsing outbox', () => {
  const messages = readFileSync(new URL('../app/device/messages.tsx', import.meta.url), 'utf8');
  assert.match(messages, /if \(!unconfirmed\) resetDraft\(\); setComposing\(false\); setRefresh\(\(value\) => value \+ 1\)/);
  assert.match(messages, /Discarding this draft will not cancel delivery/);
  assert.match(messages, /else if \(composing\) \{ if \(unconfirmed \|\| paragraphs\.length && !outcome\) setDiscard\(true\)/);
  const share = readFileSync(new URL('../app/device/share.tsx', import.meta.url), 'utf8');
  assert.match(share, /ShareContent key=\{JSON.stringify\(props.source \?\? null\)\}/);
});

test('lock wake copy stacks under its padlock and menu destinations are distinct', () => {
  const access = readFileSync(new URL('../app/device/access.tsx', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../app/device/lock.css', import.meta.url), 'utf8');
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.match(access, /<LockIcon \/><span>Device locked<\/span><span>Tap to wake<\/span>/);
  assert.match(css, /\.device-screen.device-sleep button \{[^}]*flex-direction: column/);
  assert.match(screen, /<NetworkIcon \/> Nearby/);
  assert.match(screen, /<InfoIcon \/> About/);
  assert.match(screen, /<LockIcon \/> Lock/);
});
