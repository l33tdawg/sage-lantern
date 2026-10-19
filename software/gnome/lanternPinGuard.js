// Mandatory GNOME 50.1 main.js module. Never an optional extension.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from './main.js';
import {GuardState} from './lanternGuardState.js';

const SOCKET = '/run/sage-lantern-desktop/native.sock';
const IFACE = '<node><interface name="org.sage.Lantern.DesktopGuard1">' +
    '<method name="SetMode"><arg type="s" direction="in"/>' +
    '<arg type="b" direction="out"/></method>' +
    '<method name="KeepMode"><arg type="s" direction="in"/>' +
    '<arg type="b" direction="out"/></method>' +
    '<method name="PrepareSleep"><arg type="b" direction="out"/></method>' +
    '<method name="Wake"><arg type="b" direction="out"/></method>' +
    '<method name="GetLocked"><arg type="b" direction="out"/></method>' +
    '<method name="Lock"/></interface></node>';
let guard = null;

class PinGuard {
    constructor() {
        this.grab = null;
        this.connection = null;
        this.serial = 0;
        this.busy = false;
        this.sleeping = false;
        this.activity = false;
        this.syncMode = true;
        this.modeUntil = 0;
        this.pin = '';
        this.actor = new St.Widget({reactive: true, can_focus: true,
            layout_manager: new Clutter.BinLayout(),
            style: 'background-color: #102329; color: #f5f0df;'});
        this.actor.add_constraint(new Clutter.BindConstraint({
            source: global.stage, coordinate: Clutter.BindCoordinate.ALL}));
        global.stage.add_child(this.actor); // Outside the animated/scaled uiGroup.
        this.panel = new St.BoxLayout({vertical: true,
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER,
            style: 'spacing: 16px; padding: 28px;'});
        this.actor.add_child(this.panel);
        this.panel.add_child(new St.Label({text: 'SAGE Lantern',
            style: 'font-size: 28px; font-weight: bold;'}));
        this.message = new St.Label({text: 'Enter your 8-digit PIN',
            style: 'font-size: 18px;'});
        this.panel.add_child(this.message);
        this.digits = new St.Label({text: '', style: 'font-size: 28px; min-height: 36px;'});
        this.panel.add_child(this.digits);
        for (const row of [['1', '2', '3'], ['4', '5', '6'], ['7', '8', '9'],
            ['Clear', '0', 'Unlock']]) {
            const box = new St.BoxLayout({style: 'spacing: 12px;'});
            for (const label of row) {
                const button = new St.Button({label, can_focus: true,
                    style: 'font-size: 20px; min-width: 88px; min-height: 64px; ' +
                        'background-color: #27434a; border-radius: 12px;'});
                button.connect('clicked', () => this._safe(() => this._key(label)));
                box.add_child(button);
            }
            this.panel.add_child(box);
        }
        this.actor.connect('key-press-event', (_, event) => {
            this._safe(() => {
                const key = event.get_key_symbol();
                if (key === Clutter.KEY_Return || key === Clutter.KEY_KP_Enter)
                    this._key('Unlock');
                else if (key === Clutter.KEY_BackSpace)
                    this._key('Backspace');
                else if (key === Clutter.KEY_Escape)
                    this._key('Clear');
                else {
                    const value = String.fromCodePoint(event.get_key_unicode());
                    if (/^[0-9]$/.test(value))
                        this._key(value);
                }
            });
            return Clutter.EVENT_STOP;
        });
        this.state = new GuardState(() => this._conceal(), () => this._reveal(),
            () => GLib.get_monotonic_time() / 1000);
        // Never let a later popup, OSK, stage child or focus grab cover the lock.
        global.stage.connect('child-added', () => this._safe(() => {
            if (!this.state.authorized)
                global.stage.set_child_above_sibling(this.actor, null);
        }));
        global.stage.connect('captured-event', (_, event) => {
            if (!this.state.authorized) {
                const target = global.stage.get_event_actor(event);
                if (!target || !this.actor.contains(target))
                    return Clutter.EVENT_STOP;
            }
            if (this.state.authorized && [Clutter.EventType.KEY_PRESS,
                Clutter.EventType.BUTTON_PRESS, Clutter.EventType.TOUCH_BEGIN,
                Clutter.EventType.MOTION, Clutter.EventType.SCROLL].includes(event.type()))
                this.activity = true;
            return Clutter.EVENT_PROPAGATE;
        });
        // Native stock unlock/deactivation never affects our separate actor/grab.
        Main.screenShield.lock = () => this.lock();
        Main.screenShield.showDialog = () => this.lock();
        Main.screenShield.activate = () => this.lock();
        Main.screenShield.deactivate = () => {}; // Public unlock-session cannot authorize.
        this.dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, {
            SetMode: mode => {
                this.setMode(mode);
                return true; // Concealment acknowledged, authentication still required.
            },
            KeepMode: mode => {
                if (this.state.mode !== mode)
                    this.setMode(mode);
                this.modeUntil = GLib.get_monotonic_time() / 1000 + 4000;
                return true;
            },
            Lock: () => this.lock(),
            PrepareSleep: () => this.prepareSleep(),
            Wake: () => this.wake(),
            GetLocked: () => !this.state.authorized,
        });
        this.dbus.export(Gio.DBus.session, '/org/sage/Lantern/DesktopGuard');
        GLib.timeout_add(GLib.PRIORITY_HIGH, 100, () => {
            this._safe(() => {
                this.state.tick();
                if (this.modeUntil <= GLib.get_monotonic_time() / 1000 &&
                    (this.state.authorized || this.connection))
                    this.lock();
                if (this.connection && !this.state.authorized && this.hadAuthorization) {
                    this.hadAuthorization = false;
                    this._disconnect();
                }
                if (!this.state.authorized)
                    this._conceal();
            });
            return GLib.SOURCE_CONTINUE;
        });
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, () => {
            void this._poll();
            return GLib.SOURCE_CONTINUE;
        });
        global.connect('shutdown', () => this._disconnect());
    }

    _safe(callback) {
        try {
            callback();
        } catch {
            this._disconnect();
            this.state?.lock();
            this._conceal(); // No session termination or app closure for guard failures.
        }
    }

    _conceal() {
        this.actor.show();
        global.stage.set_child_above_sibling(this.actor, null);
        if (this.grab && this.grab.get_seat_state() !== Clutter.GrabState.ALL) {
            const old = this.grab;
            this.grab = null;
            try {
                Main.popModal(old);
            } catch {}
        }
        if (!this.grab) {
            this.grab = Main.pushModal(this.actor, {actionMode: Shell.ActionMode.NONE});
            if (this.grab.get_seat_state() !== Clutter.GrabState.ALL)
                throw new Error('modal_unavailable');
        }
        // Reassert NONE even if another modal changed the action mode.
        Main.setActionMode(Shell.ActionMode.NONE);
        if (Main.sessionMode.currentMode !== 'unlock-dialog')
            Main.sessionMode.pushMode('unlock-dialog');
        Main.screenShield._setLocked(true);
        Main.screenShield._setActive(true);
        global.stage.set_key_focus(this.actor);
    }

    _reveal() {
        const grab = this.grab;
        if (!grab || Main.sessionMode.currentMode !== 'unlock-dialog')
            throw new Error('modal_unavailable');
        Main.sessionMode.popMode('unlock-dialog');
        Main.screenShield._setLocked(false);
        Main.screenShield._setActive(false);
        this.grab = null;
        Main.popModal(grab);
        Main.setActionMode(Shell.ActionMode.NORMAL);
        this.actor.hide();
        this.pin = '';
        this.digits.text = '';
        this.hadAuthorization = true;
    }

    _disconnect() {
        this.serial++;
        this.cancellable?.cancel();
        this.cancellable = null;
        try {
            this.connection?.close(null);
        } catch {}
        this.connection = null;
        this.input = this.output = null;
        this.syncMode = true;
        this.pin = '';
        this.digits.text = '';
    }

    lock() {
        try {
            this.state.lock();
        } finally {
            this._disconnect(); // Fence verification even if acquiring modal input fails.
        }
    }

    setMode(mode) {
        try {
            this.state.setMode(mode);
        } finally {
            this._disconnect();
        }
        this.modeUntil = GLib.get_monotonic_time() / 1000 + 4000;
    }

    prepareSleep() {
        this.sleeping = true;
        this.lock();
        return true; // Concealed and disconnected, never a physical HDMI-off claim.
    }

    wake() {
        this.sleeping = false;
        this.lock(); // Wake shows the PIN actor; it cannot restore a desktop grant.
        return true;
    }

    _key(value) {
        if (this.sleeping)
            return;
        if (value === 'Clear')
            this.pin = '';
        else if (value === 'Backspace')
            this.pin = this.pin.slice(0, -1);
        else if (value === 'Unlock') {
            if (this.pin.length === 8 && !this.busy) {
                const pin = this.pin;
                this.pin = '';
                this.digits.text = '';
                void this._poll(pin);
            }
        } else if (this.pin.length < 8)
            this.pin += value;
        this.digits.text = '•'.repeat(this.pin.length);
    }

    async _connect() {
        if (this.connection)
            return;
        const client = new Gio.SocketClient({timeout: 3});
        const serial = this.serial;
        const connection = await new Promise((resolve, reject) => {
            client.connect_async(new Gio.UnixSocketAddress({path: SOCKET}), this.cancellable,
                (object, result) => {
                    try {
                        resolve(object.connect_finish(result));
                    } catch {
                        reject(new Error('unavailable'));
                    }
                });
        });
        if (serial !== this.serial) {
            connection.close(null);
            throw new Error('stale_guard');
        }
        this.connection = connection;
        // Refuse a substituted user-owned Unix server before sending any PIN.
        if (this.connection.get_socket().get_credentials().get_unix_user() !== 0)
            throw new Error('unavailable');
        this.input = new Gio.DataInputStream({base_stream: this.connection.get_input_stream()});
        this.output = this.connection.get_output_stream();
    }

    async _request(payload) {
        const body = new TextEncoder().encode(`${JSON.stringify(payload)}\n`);
        await new Promise((resolve, reject) => this.output.write_all_async(body,
            GLib.PRIORITY_DEFAULT, this.cancellable, (stream, result) => {
                try {
                    stream.write_all_finish(result);
                    resolve();
                } catch {
                    reject(new Error('unavailable'));
                }
            }));
        // Bounded byte reads: DataInputStream.read_line can allocate without a cap.
        let bytes = [];
        while (bytes.length <= 512) {
            const chunk = await new Promise((resolve, reject) => this.input.read_bytes_async(1,
                GLib.PRIORITY_DEFAULT, this.cancellable, (stream, result) => {
                    try {
                        resolve(stream.read_bytes_finish(result).get_data());
                    } catch {
                        reject(new Error('unavailable'));
                    }
                }));
            if (chunk.length !== 1)
                throw new Error('unavailable');
            if (chunk[0] === 10)
                return JSON.parse(new TextDecoder().decode(new Uint8Array(bytes)));
            bytes.push(chunk[0]);
        }
        throw new Error('unavailable');
    }

    async _poll(pin = null) {
        if (this.busy || this.sleeping || this.modeUntil <= GLib.get_monotonic_time() / 1000)
            return;
        this.busy = true;
        const stamp = this.state.generation;
        const serial = this.serial;
        this.cancellable = new Gio.Cancellable();
        const cancel = this.cancellable;
        const timeout = GLib.timeout_add(GLib.PRIORITY_HIGH, pin ? 15000 : 2500, () => {
            cancel.cancel();
            return GLib.SOURCE_REMOVE;
        });
        try {
            await this._connect();
            if (serial !== this.serial)
                throw new Error('stale_guard');
            if (this.syncMode) {
                const opened = await this._request({action: 'open', mode: this.state.mode});
                if (opened.ok !== true || opened.result?.authorized !== false)
                    throw new Error('unavailable');
                this.syncMode = false;
            }
            const action = pin ? 'unlock' : this.activity ? 'activity' : 'presence';
            this.activity = false;
            let reply = await this._request(pin ? {action, pin} : {action});
            pin = null;
            if (action === 'activity' && reply.result?.authorized === true)
                reply = await this._request({action: 'presence'});
            if (serial === this.serial) {
                this.state.accept(stamp, reply, action === 'unlock');
                this.message.text = reply.error === 'rate_limited'
                    ? 'Too many attempts. Please wait.' : 'Enter your 8-digit PIN';
            }
        } catch {
            if (serial === this.serial)
                this.lock();
            this.message.text = 'Locked. Waiting for secure connection.';
        } finally {
            if (!cancel.is_cancelled())
                GLib.source_remove(timeout);
            this.busy = false;
        }
    }
}

export function install() {
    if (guard || Main.sessionMode.isGreeter || !Main.screenShield)
        throw new Error('mandatory_guard_unavailable');
    guard = new PinGuard(); // The startup caller must propagate construction failure.
}

export function isLocked() {
    return guard?.state?.authorized !== true;
}
