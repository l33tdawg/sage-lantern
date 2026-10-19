import assert from 'node:assert/strict';
import test from 'node:test';
import {GuardState} from '../../software/gnome/lanternGuardState.js';
function rig() {
    const events = [];
    let now = 1000;
    const state = new GuardState(() => events.push('conceal'),
        () => events.push('reveal'), () => now);
    return {state, events, advance: amount => now += amount, setNow: n => now = n};
}
const yes = {ok: true, result: {authorized: true}};
test('startup is concealed and status/presence cannot reveal it', () => {
    const {state, events} = rig();
    assert.deepEqual(events, ['conceal']);
    assert.equal(state.accept(state.generation, yes), false);
    assert.equal(state.authorized, false);
});
test('only a PIN reply belonging to this generation reveals the compositor', () => {
    const {state, events} = rig();
    assert.equal(state.accept(state.generation, yes, true), true);
    assert.deepEqual(events, ['conceal', 'reveal']);
});
for (const mode of ['kiosk', 'desktop']) {
    test(`mode ${mode} conceals synchronously and rejects the prior PIN result`, () => {
        const {state, events} = rig();
        const stamp = state.generation;
        state.accept(stamp, yes, true);
        state.setMode(mode);
        assert.equal(events.at(-1), 'conceal');
        assert.equal(state.accept(stamp, yes, true), false);
        assert.equal(state.authorized, false);
    });
}
test('lost connection or broker refusal conceals without an app/session shutdown', () => {
    const {state, events} = rig();
    state.accept(state.generation, yes, true);
    state.accept(state.generation, {ok: false});
    assert.equal(events.at(-1), 'conceal');
    assert.equal(state.authorized, false);
});
test('local lease conceals before the longer broker lease and late renewal cannot revive it', () => {
    const {state, advance} = rig();
    const stamp = state.generation;
    state.accept(stamp, yes, true);
    advance(3500);
    state.tick();
    assert.equal(state.authorized, false);
    assert.equal(state.accept(stamp, yes), false);
});
test('a delayed presence reply cannot extend an already elapsed local deadline', () => {
    const {state, advance} = rig();
    const stamp = state.generation;
    state.accept(stamp, yes, true);
    advance(3500);
    assert.equal(state.accept(stamp, yes), false);
    assert.equal(state.authorized, false);
});
for (const clock of [NaN, Infinity, 1]) {
    test(`invalid or backwards clock ${clock} conceals`, () => {
        const {state, setNow} = rig();
        state.accept(state.generation, yes, true);
        setNow(clock);
        state.tick();
        assert.equal(state.authorized, false);
    });
}
test('failure while releasing input returns to concealment', () => {
    const events = [];
    const state = new GuardState(() => events.push('conceal'),
        () => { throw new Error('synthetic'); }, () => 1000);
    assert.equal(state.accept(state.generation, yes, true), false);
    assert.equal(state.authorized, false);
    assert.deepEqual(events, ['conceal', 'conceal']);
});
