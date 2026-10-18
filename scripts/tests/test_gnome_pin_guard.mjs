// Exercise the actual GJS guard's races with synthetic compositor/Gio objects.
// This does not assert physical input grabs or first-frame behavior on C.
import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import {GuardState} from '../../software/gnome/lanternGuardState.js';

function rig() {
    const events = [];
    let now = 1000;
    const mode = {currentMode: 'ubuntu',
        pushMode: value => { mode.currentMode = value; },
        popMode: () => { mode.currentMode = 'ubuntu'; }};
    const Main = {sessionMode: mode,
        pushModal: () => { events.push('grab'); return {get_seat_state: () => 1}; },
        popModal: () => events.push('release'),
        setActionMode: n => events.push(`action:${n}`),
        screenShield: {_setLocked: b => events.push(`locked:${b}`),
            _setActive: b => events.push(`active:${b}`)}};
    const Clutter = {GrabState: {ALL: 1}};
    const Shell = {ActionMode: {NONE: 0, NORMAL: 1}};
    const Gio = {Cancellable: class {
        constructor() { this.cancelled = false; }
        cancel() { this.cancelled = true; }
        is_cancelled() { return this.cancelled; }
    }};
    const GLib = {get_monotonic_time: () => now * 1000,
        timeout_add: () => 7, source_remove: () => {},
        PRIORITY_HIGH: -100, PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false};
    const stage = {set_child_above_sibling: () => {}, set_key_focus: () => {}};
    const code = fs.readFileSync(new URL('../../software/gnome/lanternPinGuard.js', import.meta.url), 'utf8')
        .replace(/^import .*;\n/gm, '')
        .replaceAll('export function ', 'function ') + '\nglobalThis.Guard = PinGuard;';
    const context = {Main, Clutter, Shell, Gio, GLib, GuardState, St: {},
        global: {stage}, TextEncoder, TextDecoder};
    vm.createContext(context);
    vm.runInContext(code, context);
    const g = Object.create(context.Guard.prototype);
    Object.assign(g, {grab: null, serial: 0, busy: false, syncMode: false, activity: false,
        pin: '', digits: {text: ''}, message: {text: ''}, modeUntil: 5000,
        actor: {show: () => events.push('show'), hide: () => events.push('hide')},
        connection: {close: () => events.push('disconnect')}});
    g.state = new GuardState(() => g._conceal(), () => g._reveal(), () => now);
    g._connect = async () => {};
    return {g, events, mode, setNow: n => now = n};
}

test('the actual guard releases modal input only after its current PIN reply', async () => {
    const {g, events} = rig();
    g._request = async () => ({ok: true, result: {authorized: true}});
    await g._poll('12345678');
    assert.equal(g.state.authorized, true);
    assert.equal(events.at(-1), 'hide');
    assert.ok(events.includes('release'));
    assert.equal(g.grab, null);
});

test('a dock transition during actual asynchronous PIN I/O discards the reply', async () => {
    const {g, events} = rig();
    let resolve;
    g._request = async () => new Promise(done => resolve = done);
    const pending = g._poll('12345678');
    await new Promise(setImmediate);
    g.setMode('desktop');
    resolve({ok: true, result: {authorized: true}});
    await pending;
    assert.equal(g.state.authorized, false);
    assert.equal(g.connection, null);
    assert.equal(events.includes('release'), false);
});

test('a mode-supervisor lease is mandatory before the actual guard sends a PIN', async () => {
    const {g, setNow, events} = rig();
    let requests = 0;
    g._request = async () => { requests++; };
    setNow(5000);
    await g._poll('12345678');
    assert.equal(requests, 0);
    assert.equal(events.includes('release'), false);
});

test('a broker error in the actual guard keeps modal input and closes the transport', async () => {
    const {g, events} = rig();
    g._request = async () => { throw new Error('synthetic'); };
    await g._poll('12345678');
    assert.equal(g.state.authorized, false);
    assert.equal(g.connection, null);
    assert.equal(events.includes('release'), false);
});

test('a mismatch in native session mode is caught before input is released', async () => {
    const {g, events, mode} = rig();
    mode.currentMode = 'unexpected';
    g._request = async () => ({ok: true, result: {authorized: true}});
    await g._poll('12345678');
    assert.equal(g.state.authorized, false);
    assert.equal(events.includes('release'), false);
});

test('a failed mode-transition grab still closes the native transport and fences PIN I/O', () => {
    const {g, events} = rig();
    g.state.conceal = () => { throw new Error('synthetic grab failure'); };
    assert.throws(() => g.setMode('desktop'));
    assert.equal(g.connection, null);
    assert.equal(g.state.authorized, false);
    assert.equal(events.includes('disconnect'), true);
});

test('sleep preparation fences a pending PIN and wake cannot restore its grant', async () => {
    const {g, events} = rig();
    let resolve;
    g._request = async () => new Promise(done => resolve = done);
    const pending = g._poll('12345678');
    await new Promise(setImmediate);
    assert.equal(g.prepareSleep(), true);
    assert.equal(g.sleeping, true);
    g._key('1');
    assert.equal(g.pin, '');
    resolve({ok: true, result: {authorized: true}});
    await pending;
    assert.equal(g.state.authorized, false);
    assert.equal(g.wake(), true);
    assert.equal(g.sleeping, false);
    assert.equal(g.state.authorized, false);
    assert.equal(events.includes('release'), false);
});

test('no background presence or PIN verification runs while preparing for sleep', async () => {
    const {g} = rig();
    g.prepareSleep();
    g._request = async () => { throw new Error('must not send a request'); };
    await g._poll('12345678');
    assert.equal(g.state.authorized, false);
    assert.equal(g.connection, null);
});
