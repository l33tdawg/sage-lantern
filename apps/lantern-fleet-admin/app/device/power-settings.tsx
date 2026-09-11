'use client';

import { useEffect, useRef, useState } from 'react';
import { delayLabel, DISPLAY_DELAYS, loadPowerSettings, savePowerSettings, SLEEP_DELAYS, type PowerSettings } from '../../lib/power-settings';
import { DEFAULT_LOCK_SECONDS, LOCK_DELAYS, loadLockSettings, saveLockSettings, type LockSettings } from '../../lib/lock-settings';

export default function PowerSettingsPanel() {
  const [settings, setSettings] = useState<PowerSettings | null>(null);
  const [display, setDisplay] = useState(30);
  const [sleep, setSleep] = useState(300);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [lockSettings, setLockSettings] = useState<LockSettings | null>(null);
  const [lockDelay, setLockDelay] = useState<number>(DEFAULT_LOCK_SECONDS);
  const [lockBusy, setLockBusy] = useState(false);
  const [lockError, setLockError] = useState('');
  const [lockSaved, setLockSaved] = useState(false);
  const lockOperation = useRef(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    let disposed = false;
    void loadLockSettings().then((value) => {
      if (!disposed) { setLockSettings(value); setLockDelay(value.lock_seconds); }
    }).catch(() => { if (!disposed) setLockError('Lock settings need a device update or a new unlocked session.'); });
    return () => { disposed = true; mounted.current = false; };
  }, []);
  useEffect(() => {
    let disposed = false;
    void loadPowerSettings().then((value) => {
      if (!disposed) { setSettings(value); setDisplay(value.display_off_seconds); setSleep(value.deep_sleep_seconds); }
    }).catch(() => { if (!disposed) setError('Sleep settings need a device update or a new unlocked session.'); });
    return () => { disposed = true; };
  }, []);
  async function save() {
    if (!settings || busy) return;
    setBusy(true); setError(''); setSaved(false);
    try {
      const value = await savePowerSettings(settings, display, sleep);
      setSettings(value); setDisplay(value.display_off_seconds); setSleep(value.deep_sleep_seconds); setSaved(true);
    } catch { setSettings(null); setError('Could not confirm the change. Reopen Settings before trying again.'); }
    finally { setBusy(false); }
  }
  async function saveLock() {
    if (!lockSettings || lockOperation.current) return;
    lockOperation.current = true; setLockBusy(true); setLockError(''); setLockSaved(false);
    try {
      const value = await saveLockSettings(lockSettings, lockDelay);
      if (mounted.current) { setLockSettings(value); setLockDelay(value.lock_seconds); setLockSaved(true); }
    } catch {
      if (mounted.current) { setLockSettings(null); setLockError('Could not confirm the lock delay. Reopen Settings to reload it before trying again.'); }
    } finally { lockOperation.current = false; if (mounted.current) setLockBusy(false); }
  }
  return <><section className="device-panel" aria-label="Automatic lock">
    <h2>Automatic lock</h2>
    <p className="device-note" id="lock-delay-help">While unlocked, this is the idle time since your last interaction. Default: 5 minutes. Screen-off and deep-sleep delays start only after lock.</p>
    {lockError && <p role="alert">{lockError}</p>}
    {lockSettings ? <>
      <div className="device-history-setting"><label htmlFor="lock-delay">Lock after</label><select id="lock-delay" aria-describedby="lock-delay-help" disabled={lockBusy} value={lockDelay} onChange={(event) => { setLockDelay(Number(event.target.value)); setLockSaved(false); }}>
        {LOCK_DELAYS.map((delay) => <option key={delay} value={delay}>{delayLabel(delay)}</option>)}
      </select></div>
      <button disabled={lockBusy || lockDelay === lockSettings.lock_seconds} onClick={() => void saveLock()}>{lockBusy ? 'Saving…' : 'Save lock delay'}</button>
      {lockSaved && <p role="status">Lock delay saved</p>}
    </> : !lockError && <p role="status">Loading lock settings…</p>}
  </section><section className="device-panel" aria-label="Screen and sleep">
    <h2>Screen and sleep</h2>
    {error && <p role="alert">{error}</p>}
    {settings ? <>
      <p className="device-note">Delays start after your Lantern locks. Touching the screen starts the wait again.</p>
      <div className="device-history-setting"><label htmlFor="display-delay">Turn screen off after</label><select id="display-delay" disabled={busy} value={display} onChange={(event) => {
        const delay = Number(event.target.value); setDisplay(delay); setSaved(false);
        if (sleep <= delay) setSleep(600);
      }}>{DISPLAY_DELAYS.map((delay) => <option key={delay} value={delay}>{delayLabel(delay)}</option>)}</select></div>
      <div className="device-history-setting"><label htmlFor="sleep-delay">Deep sleep after</label><select id="sleep-delay" disabled={busy} value={sleep} onChange={(event) => { setSleep(Number(event.target.value)); setSaved(false); }}>{SLEEP_DELAYS.filter((delay) => delay > display).map((delay) => <option key={delay} value={delay}>{delayLabel(delay)}</option>)}</select></div>
      {!settings.deep_sleep_available && <p className="device-note">Automatic deep sleep is not available yet. You can save its delay, but this does not turn deep sleep on.</p>}
      <button disabled={busy || (display === settings.display_off_seconds && sleep === settings.deep_sleep_seconds)} onClick={() => void save()}>{busy ? 'Saving…' : 'Save sleep settings'}</button>
      {saved && <p role="status">Saved</p>}
    </> : !error && <p role="status">Loading sleep settings…</p>}
  </section></>;
}
