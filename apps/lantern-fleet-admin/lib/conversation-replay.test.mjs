import test from 'node:test';
import assert from 'node:assert/strict';

import { groupConversations } from './conversation-threads.ts';
const uid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const thread = uid(90);
const other = uid(91);
const row = (n, fields = {}) => ({ session_id:uid(n), thread_id:thread,
  created_at:`2026-10-09T10:00:${String(n).padStart(2, '0')}.000000Z`, transcript:`Question ${n}`, response:`Answer ${n}`, ...fields });
const legacy = (n) => { const value=row(n); delete value.thread_id; return value; };
const ids = (items) => items.map((item)=>item.session_id);
const grouped = (rows, current=null, id=thread) => groupConversations(rows,current).find((group)=>group.id === id)?.items || [];
const snapshot = (items) => ({thread_id:thread, revision:7, active_session_id:null, context_turn_limit:4, items});
const tied='2026-10-09T10:00:04.000000Z';

const cases = [
  { name:'plain saved exchanges use chronological order', rows:[row(3),row(1),row(2)], expected:[uid(1),uid(2),uid(3)] },
  { name:'a correction replaces its target and later descendants', rows:[row(1),row(2),row(3),row(4,{supersedes:uid(2)})], expected:[uid(1),uid(4)] },
  { name:'a missing correction target drops the unprovable old prefix', rows:[row(1),row(4,{supersedes:uid(2)}),row(5)], expected:[uid(4),uid(5)] },
  { name:'a nested correction removes descendants of the corrected question', rows:[row(1),row(2),row(3),row(4,{supersedes:uid(2)}),row(5),row(6,{supersedes:uid(4)})], expected:[uid(1),uid(6)] },
  { name:'a target removed by a previous correction is now missing', rows:[row(1),row(2),row(3),row(4,{supersedes:uid(2)}),row(5,{supersedes:uid(2)})], expected:[uid(5)] },
  { name:'correcting the first question starts a replacement branch', rows:[row(1),row(2),row(3),row(4,{supersedes:uid(1)})], expected:[uid(4)] },
  { name:'a tied correction discards ambiguous plain rows and retains its terminal', rows:[row(1),row(2,{created_at:row(1).created_at}),row(3,{created_at:row(1).created_at,supersedes:uid(2)})], expected:[uid(3)] },
  { name:'submillisecond chronology follows the runtime microseconds', rows:[4,3,2,1].map((n)=>row(n,{created_at:`2026-10-09T10:00:00.000${n}00Z`,...(n===4?{supersedes:uid(2)}:{})})), expected:[uid(1),uid(4)] },
  { name:'timezone offsets are compared as chronological instants', rows:[row(4,{created_at:'2026-10-09T12:00:00+02:00',supersedes:uid(2)}),row(3,{created_at:'2026-10-09T09:59:59Z'}),row(1,{created_at:'2026-10-09T09:59:57Z'}),row(2,{created_at:'2026-10-09T09:59:58Z'})], expected:[uid(1),uid(4)] },
  { name:'one complete tied nested edit chain keeps only its terminal and earlier prefix', rows:[row(5,{created_at:tied,supersedes:uid(4)}),row(3,{created_at:tied}),row(2,{created_at:tied}),row(1),row(4,{created_at:tied,supersedes:uid(2)})], expected:[uid(1),uid(5)] },
  { name:'independent tied edit terminals keep only the provable prefix', rows:[row(1),row(2),row(3,{created_at:tied}),row(4,{created_at:tied,supersedes:uid(2)}),row(5,{created_at:tied,supersedes:uid(3)})], expected:[uid(1)] },
  { name:'cyclic tied edits are omitted without dropping a provable earlier prefix', rows:[row(1),row(3,{created_at:tied}),row(4,{created_at:tied,supersedes:uid(5)}),row(5,{created_at:tied,supersedes:uid(4)})], expected:[uid(1)] },
  { name:'a missing external tied edit target drops the unknown earlier prefix', rows:[row(1),row(3,{created_at:tied}),row(4,{created_at:tied,supersedes:uid(99)})], expected:[uid(4)] },
  { name:'a tied bucket without edits preserves supplied plain-row order', rows:[row(3,{created_at:tied}),row(1),row(2,{created_at:tied})], expected:[uid(1),uid(3),uid(2)] },
  { name:'a branching tied edit chain has ambiguous terminals and is omitted', rows:[row(1),row(2),row(4,{created_at:tied,supersedes:uid(2)}),row(5,{created_at:tied,supersedes:uid(4)}),row(6,{created_at:tied,supersedes:uid(4)})], expected:[uid(1)] },
  { name:'a single terminal alongside a separate tied cycle is not a complete chain', rows:[row(1),row(2),row(4,{created_at:tied,supersedes:uid(5)}),row(5,{created_at:tied,supersedes:uid(4)}),row(6,{created_at:tied,supersedes:uid(2)})], expected:[uid(1)] },
];

for (const entry of cases) test(entry.name,()=>assert.deepEqual(ids(grouped(entry.rows)),entry.expected));

test('active corrected Snapshot remains authoritative with missing target and stale retained descendants',()=>{
  const retained=cases[1].rows;
  const current=snapshot([retained[0],retained[3]]);
  const result=grouped(retained,current);
  assert.deepEqual(ids(result),[uid(1),uid(4)]);
  assert.strictEqual(result[0],current.items[0]);
  assert.strictEqual(result[1],current.items[1]);
});

test('bounded active Snapshot does not expand to the saved prefix',()=>{
  const retained=cases[1].rows;
  assert.deepEqual(ids(grouped(retained,snapshot([retained[3]]))),[uid(4)]);
});

test('active Snapshot is not replayed through saved exact-tie conservatism',()=>{
  const saved=[row(1,{created_at:tied}),row(2,{created_at:tied}),row(3,{created_at:tied}),row(4,{created_at:tied,supersedes:uid(2)})];
  const active=[saved[0],saved[3]];
  assert.deepEqual(grouped(saved,snapshot(active)),active);
});

test('an empty active Snapshot cannot revive its retained thread',()=>{
  const saved=[row(1),row(2),legacy(7)];
  const groups=groupConversations(saved,snapshot([]));
  assert.deepEqual(groups.map((group)=>group.id),[uid(7)]);
  assert.deepEqual(groups[0].items,[saved[2]]);
});

test('an all-ambiguous saved cohort creates no phantom recent-chat entry',()=>{
  const ambiguous=[row(1,{created_at:tied}),row(2,{created_at:tied}),
    row(3,{created_at:tied,supersedes:uid(1)}),row(4,{created_at:tied,supersedes:uid(2)})];
  const before=JSON.stringify(ambiguous);
  assert.deepEqual(groupConversations(ambiguous,null),[]);
  assert.deepEqual(groupConversations([...ambiguous,legacy(7)],null).map((group)=>group.id),[uid(7)]);
  assert.equal(JSON.stringify(ambiguous),before);
});

test('active Snapshot order and text win over retained copies',()=>{
  const active=[row(2,{transcript:'Authoritative second'}),row(1,{transcript:'Authoritative first'})];
  const result=grouped([row(1),row(2)],snapshot(active));
  assert.deepEqual(result,active);
});

test('legacy unthreaded saved records remain independent conversations',()=>{
  const saved=[legacy(7),legacy(8),...cases[1].rows];
  const groups=groupConversations(saved,null);
  assert.deepEqual(new Set(groups.map((group)=>group.id)),new Set([uid(7),uid(8),thread]));
  assert.deepEqual(groups.find((group)=>group.id === uid(7)).items,[saved[0]]);
  assert.deepEqual(ids(groups.find((group)=>group.id === thread).items),[uid(1),uid(4)]);
});

test('correction replay is confined to its own thread',()=>{
  const saved=[row(1),row(2),row(3,{thread_id:other}),row(4,{thread_id:other,supersedes:uid(1)})];
  assert.deepEqual(ids(grouped(saved,null,thread)),[uid(1),uid(2)]);
  assert.deepEqual(ids(grouped(saved,null,other)),[uid(4)]);
});

test('replay changes no retained records and returns the original row objects',()=>{
  const saved=cases[1].rows.map((item)=>Object.freeze({...item}));
  Object.freeze(saved);
  const before=JSON.stringify(saved);
  const result=grouped(saved);
  assert.equal(JSON.stringify(saved),before);
  assert.strictEqual(result[0],saved[0]);
  assert.strictEqual(result[1],saved[3]);
});

test('retained duplicate session IDs remain deduplicated with the final saved row',()=>{
  const second=row(1,{transcript:'Latest saved copy'});
  assert.deepEqual(grouped([row(1),second]),[second]);
});

test('titles and updated timestamps describe the effective branch',()=>{
  const result=groupConversations(cases[2].rows,null)[0];
  assert.equal(result.title,'Question 4');
  assert.equal(result.updated,row(5).created_at);
});

test('effective titles and sidebar recency use actual instants across timezone offsets',()=>{
  const saved=[row(1,{created_at:'2026-10-09T18:00:00+08:00'}),
    row(4,{created_at:'2026-10-09T18:05:00+08:00',supersedes:uid(1)}),
    row(6,{thread_id:other,created_at:'2026-10-09T10:10:00Z'})];
  const groups=groupConversations(saved,null);
  assert.deepEqual(groups.map((group)=>group.id),[other,thread]);
  assert.equal(groups[1].title,'Question 4');
  assert.equal(groups[1].updated,'2026-10-09T18:05:00+08:00');
});

test('sidebar recency preserves microseconds when equal milliseconds use different offsets',()=>{
  const saved=[row(1,{created_at:'2026-10-09T18:00:00.000100+08:00'}),
    row(2,{thread_id:other,created_at:'2026-10-09T10:00:00.000200Z'})];
  assert.deepEqual(groupConversations(saved,null).map((group)=>group.id),[other,thread]);
});

