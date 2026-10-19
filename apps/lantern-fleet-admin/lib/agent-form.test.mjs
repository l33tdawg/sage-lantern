/**
 * The Agent form: its inline keyboard, and the fence around every write result.
 *
 * Rook's 2 October review held 2c acceptance on three component findings, and this
 * file is the writer-owned regression set for them. It mounts the **live** sources
 * — `agent.tsx`, `agent-task-form.tsx`, `keyboard-keys.tsx` and the real
 * `lib/question-edit` — so the assertions are about the code in this repository,
 * not about a frozen snapshot. (The review harness in
 * `work/rook-agent-2c-review-20261002/` compiles its own `source/` copy, so it can
 * only ever describe the revision frozen there.)
 *
 * What is asserted, and why each one is here:
 *
 *  1. **The existing inline keyboard edits every text field.** The brief and
 *     `SECONDARY_SCREEN_DECISIONS.md` require Lantern's own keyboard on forms, with
 *     its grapheme handling — so the keys must reach the field being edited, insert
 *     at the caret, replace a selection and erase a whole grapheme cluster.
 *  2. **A failed save is reported in the still-open editor**, without the claim
 *     that the provider wrote nothing: a rejected command does not establish the
 *     provider's final outcome.
 *  3. **A write that outlives its draft publishes nothing.** Its failure cannot
 *     appear on whatever replaced the editor, its success cannot close it, and its
 *     completion cannot clear the busy state of a write that replaced it.
 *  4. **Escape cannot abandon the editor mid-write**, so the result has somewhere
 *     to be reported.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import * as agentTasks from './agent-tasks.ts';
import * as questionEdit from './question-edit.ts';

const require = createRequire(import.meta.url);

const runtime = require('react/jsx-runtime');
const nulled = new Proxy({}, { get: () => () => null });

// The screen restores focus through `requestAnimationFrame`. Nothing here has a
// real frame clock, and its focus targets are refs this shim renders without a DOM.
const realAnimationFrame = globalThis.requestAnimationFrame;
globalThis.requestAnimationFrame = () => 0;
test.after(() => { globalThis.requestAnimationFrame = realAnimationFrame; });

/** Compile one device module to CommonJS, resolving its imports through `load`. */
function compile(file, load) {
  const source = readFileSync(new URL(`../app/device/${file}`, import.meta.url), 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const cjs = { exports: {} };
  new Function('require', 'module', 'exports', js)(load, cjs, cjs.exports);
  return cjs.exports.default;
}

/** A provider that answered with a complete list: the only state that may render cards. */
const ready = { approved: true, answered: true, complete: true };
const draft = { name: 'Private draft', description: 'Review', schedule: { mode: 'every', minutes: 15 }, instruction: 'Read' };
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

/**
 * Mount one device screen with a slot table of its own.
 *
 * The modules are compiled per mount because a CommonJS `require('react')` is
 * evaluated once, when the module loads: a module compiled against a previous
 * mount's slots would render on the wrong table.
 */
function mountWith(file, props = {}) {
  const slots = [];
  const effects = [];
  let cursor = 0;
  let tree;
  let subscriber = null;
  const document = new EventTarget();
  document.hidden = false;
  globalThis.document = document;
  const hooks = { ...React,
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, (value) => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value; }];
    },
    useRef(initial) { const index = cursor++; slots[index] ??= { current: initial }; return slots[index]; },
    useCallback(callback) { return callback; },
    useEffect(callback, dependencies) { schedule(callback, dependencies); },
    useLayoutEffect(callback, dependencies) { schedule(callback, dependencies); },
  };
  function schedule(callback, dependencies) {
    const index = cursor++;
    const previous = slots[index];
    const changed = !previous || !dependencies
      || dependencies.some((value, offset) => value !== previous.dependencies[offset]);
    if (!changed) return;
    previous?.cleanup?.();
    // The callback itself is kept, not only its cleanup, so a development effect
    // replay can run the setup again the way React does.
    const slot = previous ?? {};
    slot.dependencies = dependencies;
    slot.effect = callback;
    slots[index] = slot;
    effects.push(() => { slot.cleanup = callback(); });
  }
  const base = (name) => name === 'react' ? hooks
    : name === 'react/jsx-runtime' ? runtime
      : name === './icons' ? nulled
        : name === '../../lib/agent-tasks' ? agentTasks
          : name === '../../lib/question-edit' ? questionEdit
            // The session boundary is a recorder here: the real subscription and the
            // real lock/unmount path are exercised in the browser fixture, while this
            // file needs to fire the boundary on demand.
            : name === '../../lib/device-session' ? { subscribeDeviceSession: (next) => { subscriber = next; return () => { subscriber = null; }; } }
              : name.endsWith('.css') ? {}
                : require(name);
  const compileOne = (path, extra) => compile(path, (name) => extra?.(name) ?? base(name));
  const KeyboardKeys = compileOne('keyboard-keys.tsx');
  const Form = compileOne('agent-task-form.tsx', (name) => name === './keyboard-keys' ? { default: KeyboardKeys } : null);
  const Screen = compileOne(file, (name) => name === './agent-task-form' ? { default: Form } : null);
  /**
   * Render every nested component, so a query sees what the browser would.
   *
   * The shim calls the screen function directly, so a child component is an
   * element nobody has invoked until this walk invokes it. Its hooks are consumed
   * in the same depth-first order React uses, which is why the walk happens inside
   * the render pass rather than after it.
   */
  const expand = (node) => {
    if (!node || typeof node !== 'object') return node;
    if (Array.isArray(node)) return node.map(expand);
    if (typeof node.type === 'function') return expand(node.type(node.props));
    const children = node.props?.children;
    if (children === undefined || children === null) return node;
    return { ...node, props: { ...node.props, children: expand(children) } };
  };
  let root;
  const render = () => {
    cursor = 0;
    root = Screen(props);
    tree = expand(root);
    while (effects.length) effects.shift()();
    return tree;
  };
  const nodes = () => {
    const found = [];
    const walk = (node) => {
      if (!node || typeof node !== 'object') return;
      if (Array.isArray(node)) { node.forEach(walk); return; }
      found.push(node);
      walk(node.props?.children);
    };
    walk(tree);
    return found;
  };
  const find = (predicate) => nodes().find(predicate);
  const button = (label) => find((node) => node.type === 'button'
    && (node.props?.children === label || node.props?.['aria-label'] === label));
  const press = (label) => {
    const target = button(label);
    assert.ok(target, `button ${label} is on screen`);
    assert.equal(!!target.props.disabled, false, `button ${label} is enabled`);
    target.props.onClick();
    render();
  };
  const field = (id) => find((node) => node.props?.id === id);
  /** Change a field the way a browser does: new value, and a caret in it. */
  const change = (id, value, start = value.length, end = start) => {
    field(id).props.onChange({ target: { value }, currentTarget: { selectionStart: start, selectionEnd: end } });
    render();
  };
  render();
  return {
    render,
    press,
    field,
    change,
    nodes,
    button,
    markup: () => renderToStaticMarkup(tree),
    form: () => { assert.equal(root.type, Form); return root.props; },
    /** An authority move: the session subscriber fires. */
    revoke: () => { subscriber(); render(); },
    /** A lock or a sleep: the document goes hidden, which discards the draft. */
    hide: () => { document.hidden = true; document.dispatchEvent(new Event('visibilitychange')); render(); },
    unmount: () => slots.forEach((slot) => slot?.cleanup?.()),
    /**
     * React's development double-invoke: every cleanup, then every setup again, on
     * the already-mounted tree. The App Router enables this by default.
     */
    replay: () => {
      const mounted = slots.filter((slot) => typeof slot?.effect === 'function');
      for (const slot of mounted) { slot.cleanup?.(); slot.cleanup = undefined; }
      for (const slot of mounted) slot.cleanup = slot.effect();
      render();
    },
  };
}

const mount = (props = {}) => mountWith('agent.tsx', { close() {}, ...props });

test('the task form edits every text field with the existing inline keyboard', () => {
  const m = mount();
  m.press('New task');
  // The shared key grid, by its own accessible name, under the field it edits.
  assert.match(m.markup(), /aria-label="Question keyboard"/);
  assert.match(m.markup(), /aria-label="Name keyboard"/);

  m.press('Shift'); m.press('P'); m.press('Shift');
  m.press('a'); m.press('Space'); m.press('?');
  assert.equal(m.field('agent-task-name').props.value, 'Pa ?');

  // The keyboard follows the field being edited, and only one is open at a time.
  m.field('agent-task-instruction').props.onFocus({ currentTarget: { selectionStart: 0, selectionEnd: 0 } });
  m.render();
  assert.match(m.markup(), /aria-label="Instruction keyboard"/);
  assert.doesNotMatch(m.markup(), /aria-label="Name keyboard"/);

  // A key press replaces the selection, through the shared editor.
  m.change('agent-task-instruction', 'standup', 0, 7);
  m.press('s');
  assert.equal(m.field('agent-task-instruction').props.value, 's');

  // Erase is grapheme-aware: a family emoji is one character to this key, not five.
  const family = '👨‍👩‍👧';
  m.change('agent-task-instruction', family);
  m.press('⌫');
  assert.equal(m.field('agent-task-instruction').props.value, '');

  // The interval field is edited the same way, and publishes through the pure core.
  m.field('agent-task-amount').props.onFocus({ currentTarget: { selectionStart: 0, selectionEnd: 0 } });
  m.render();
  assert.match(m.markup(), /aria-label="Repeat every keyboard"/);
  m.press('123');
  m.press('4');
  assert.equal(m.field('agent-task-amount').props.value, '4');
  assert.deepEqual(m.form().draft.schedule, { mode: 'every', minutes: 240 });

  // Done puts the keyboard away without leaving the editor or discarding the draft.
  m.press('Done');
  assert.doesNotMatch(m.markup(), /aria-label="Question keyboard"/);
  assert.equal(m.form().draft.schedule.minutes, 240);
  m.unmount();
});

test('a tap brings the keyboard back after Done, which never moves DOM focus', () => {
  // Done is `setActive(null)`: it puts the keys away and leaves the field as the
  // focused element, so the next tap on that field fires no focus event. On the
  // glass that made the form a dead end — the field could not be edited again
  // until some *other* field had been tapped first — and the only keyboard a
  // device has is this one.
  const m = mount();
  m.press('New task');
  m.change('agent-task-instruction', 'standup');

  m.field('agent-task-instruction').props.onFocus({ currentTarget: { selectionStart: 7, selectionEnd: 7 } });
  m.render();
  assert.match(m.markup(), /aria-label="Instruction keyboard"/);
  m.press('Done');
  assert.doesNotMatch(m.markup(), /aria-label="Instruction keyboard"/);

  // Exactly what a browser delivers to a field that already has focus: a click,
  // with no focus event before it.
  m.field('agent-task-instruction').props.onClick({ currentTarget: { selectionStart: 3, selectionEnd: 3 } });
  m.render();
  assert.match(m.markup(), /aria-label="Instruction keyboard"/);
  // And the tap is where the caret went, so the next key lands under the finger
  // rather than at the end of the field.
  m.press('s');
  assert.equal(m.field('agent-task-instruction').props.value, 'stasndup');
  m.unmount();
});

test('a rejected save is reported in the still-open editor, without claiming the write did not land', async () => {
  const m = mount({ provider: ready, tasks: [], onSaveTask: async () => { throw new Error('refused'); } });
  m.press('New task');
  m.form().onChange(draft);
  m.render();
  m.press('Save task');
  await flush();
  m.render();
  const text = m.markup();
  assert.match(text, /aria-label="New task"/, 'the editor is still open');
  assert.match(text, /Could not save the task/);
  assert.doesNotMatch(text, /Nothing was changed/);
  m.unmount();
});

test('a failed remove is reported without claiming the task is unchanged', async () => {
  const m = mount({
    provider: ready,
    tasks: [{ id: 'a', name: 'Standup', schedule: { mode: 'every', minutes: 1440 } }],
    onRemoveTask: async () => { throw new Error('refused'); },
  });
  m.press('Remove');
  await flush();
  m.render();
  assert.match(m.markup(), /Could not remove the task/);
  assert.doesNotMatch(m.markup(), /Nothing was changed/);
  m.unmount();
});

test('a save that outlives its draft cannot publish its failure into the replacement surface', async () => {
  for (const boundary of ['hide', 'revoke']) {
    let reject;
    const pending = new Promise((_, fail) => { reject = fail; });
    const m = mount({ provider: ready, tasks: [], onSaveTask: () => pending });
    m.press('New task');
    m.form().onChange(draft);
    m.render();
    m.press('Save task');
    m.render();
    m[boundary]();
    reject(new Error('the old request failed'));
    await flush();
    m.render();
    assert.doesNotMatch(m.markup(), /Could not save the task/, `after ${boundary}`);
    // The replacement surface is usable: the discarded write left nothing busy.
    m.press('New task');
    assert.equal(m.form().draft.name, '');
    m.unmount();
  }
});

test('a save that outlives its draft cannot close the editor that replaced it', async () => {
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const m = mount({ provider: ready, tasks: [], onSaveTask: () => pending });
  m.press('New task');
  m.form().onChange(draft);
  m.render();
  m.press('Save task');
  m.render();
  m.revoke();
  m.press('New task');
  resolve();
  await flush();
  m.render();
  assert.match(m.markup(), /aria-label="New task"/, 'the new editor is still open');
  assert.equal(m.form().draft.name, '', 'the new editor still holds the draft the user typed');
  m.unmount();
});

test('a stale write cannot clear the busy state of the write that replaced it', async () => {
  let failFirst;
  const first = new Promise((_, fail) => { failFirst = fail; });
  let finishSecond;
  const second = new Promise((done) => { finishSecond = done; });
  const queue = [first, second];
  const m = mount({ provider: ready, tasks: [], onSaveTask: () => queue.shift() });
  m.press('New task');
  m.form().onChange(draft);
  m.render();
  m.press('Save task');
  m.render();
  m.hide();
  m.press('New task');
  m.form().onChange(draft);
  m.render();
  m.press('Save task');
  m.render();
  assert.match(m.markup(), /Saving…/);

  // The discarded write settles while the new one is still in flight.
  failFirst(new Error('the old request failed'));
  await flush();
  m.render();
  assert.doesNotMatch(m.markup(), /Could not save the task/);
  assert.match(m.markup(), /Saving…/, 'the replacement write is still allowed to report itself busy');

  finishSecond();
  await flush();
  m.render();
  assert.match(m.markup(), /aria-label="Agent"/, 'the completed write closed the form it belonged to');
  m.unmount();
});

test('Escape cannot abandon the editor while a write is in flight', async () => {
  let reject;
  const pending = new Promise((_, fail) => { reject = fail; });
  const m = mount({ provider: ready, tasks: [], onSaveTask: () => pending });
  m.press('New task');
  m.form().onChange(draft);
  m.render();
  m.press('Save task');
  m.render();
  const subscreen = m.nodes().find((node) => node.props?.['aria-label'] === 'New task' && typeof node.props?.onKeyDown === 'function');
  subscreen.props.onKeyDown({ key: 'Escape' });
  m.render();
  assert.match(m.markup(), /aria-label="New task"/, 'the editor stayed open');
  reject(new Error('refused'));
  await flush();
  m.render();
  assert.match(m.markup(), /Could not save the task/, 'the failure had an editor to land in');
  m.unmount();
});

test('a development effect replay leaves the screen able to publish its own results', async () => {
  // React runs setup, cleanup and setup again when it double-invokes effects in
  // development, and the App Router enables that by default. A screen whose
  // liveness flag is only ever cleared would come out of that replay unable to
  // report anything: `Saving…` forever, no failure, no completion.
  const failed = mount({ provider: ready, tasks: [], onSaveTask: async () => { throw new Error('refused'); } });
  failed.replay();
  failed.press('New task');
  failed.form().onChange(draft);
  failed.render();
  failed.press('Save task');
  await flush();
  failed.render();
  assert.doesNotMatch(failed.markup(), /Saving…/, 'the save did not stay in flight for ever');
  assert.match(failed.markup(), /Could not save the task/, 'the failure was reported');
  failed.unmount();

  const saved = mount({ provider: ready, tasks: [], onSaveTask: async () => {} });
  saved.replay();
  saved.press('New task');
  saved.form().onChange(draft);
  saved.render();
  saved.press('Save task');
  await flush();
  saved.render();
  assert.match(saved.markup(), /aria-label="Agent"/, 'the successful save closed its editor');
  saved.unmount();

  const removed = mount({
    provider: ready,
    tasks: [{ id: 'a', name: 'Standup', schedule: { mode: 'every', minutes: 1440 } }],
    onRemoveTask: async () => { throw new Error('refused'); },
  });
  removed.replay();
  removed.press('Remove');
  await flush();
  removed.render();
  assert.match(removed.markup(), /Could not remove the task/, 'the failed remove was reported');
  assert.doesNotMatch(removed.markup(), /Saving…/, 'and the list is not left disabled');
  removed.unmount();
});

test('a verified empty list and provider cards render distinctly, with long text intact', () => {
  const long = 'W'.repeat(400);
  const empty = mount({ provider: ready, tasks: [] });
  assert.match(empty.markup(), /No tasks yet/);
  empty.unmount();

  const cards = mount({
    provider: ready,
    tasks: [{ id: 'a', name: long, description: long, instruction: long, schedule: { mode: 'once', at: '2026-10-05T09:30:00.000Z' } }],
  });
  assert.match(cards.markup(), new RegExp(long));
  assert.match(cards.markup(), /Once, /);
  assert.ok(cards.button('Edit'), 'a provider card offers Edit');
  assert.ok(cards.button('Remove'), 'a provider card offers Remove');
  cards.unmount();

  const missing = mount();
  assert.match(missing.markup(), /Tasks are not available/);
  assert.doesNotMatch(missing.markup(), /No tasks/);
  missing.unmount();
});

test('Hardware with no reading reports every item unknown rather than zero or healthy', () => {
  const m = mountWith('hardware.tsx', { close() {}, status: null });
  const text = m.markup();
  assert.match(text, /Hardware controls are not available on this device\./);
  assert.match(text, /Not reported/);
  assert.doesNotMatch(text, /RK3588|GPIO|BCM/);
  m.unmount();
});
