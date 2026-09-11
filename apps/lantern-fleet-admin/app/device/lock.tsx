'use client';

import { useEffect, useRef, useState } from 'react';
import { editDevicePin, validDevicePin } from '../../lib/device-pin';
import { LanternIcon } from './icons';
import './screen.css';
import './lock.css';

export type UnlockResult = 'unlocked' | 'incorrect' | 'retry_later' | 'unavailable';

export default function LockScreen({ unlock, onUnlocked, title = 'Unlock', hint = 'Enter your 8–12 digit passcode', submitLabel = 'Unlock', allowReveal = false }: {
  unlock: (pin: string) => Promise<UnlockResult>;
  onUnlocked: () => void;
  title?: string; hint?: string; submitLabel?: string; allowReveal?: boolean;
}) {
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [message, setMessage] = useState(hint);
  const generation = useRef(0);
  const pending = useRef(false);

  useEffect(() => {
    const hide = (event: Event) => {
      if (!document.hidden && event.type !== 'pagehide') return;
      generation.current += 1;
      setPin('');
      setRevealed(false);
      setMessage(hint);
    };
    document.addEventListener('visibilitychange', hide);
    window.addEventListener('pagehide', hide);
    return () => {
      generation.current += 1;
      document.removeEventListener('visibilitychange', hide);
      window.removeEventListener('pagehide', hide);
    };
  }, [hint]);

  useEffect(() => {
    if (!revealed) return;
    const timer = setTimeout(() => setRevealed(false), 15000);
    return () => clearTimeout(timer);
  }, [revealed]);

  const submit = async () => {
    if (pending.current || !validDevicePin(pin)) return;
    pending.current = true;
    setBusy(true);
    setMessage('Please wait…');
    const attempt = generation.current;
    const entered = pin;
    setPin('');
    setRevealed(false);
    let result: UnlockResult = 'unavailable';
    try { result = await unlock(entered); } catch { result = 'unavailable'; }
    finally { pending.current = false; }
    if (attempt !== generation.current) {
      setBusy(false);
      return;
    }
    setBusy(false);
    if (result === 'unlocked') { onUnlocked(); return; }
    setMessage(result === 'incorrect' ? 'Check your passcode and try again.' : result === 'retry_later' ? 'Too many tries. Please wait.' : 'Unable to unlock. Please try again.');
  };

  if (busy) return <main className="device-screen device-lock-wait" aria-busy="true"><h1 role="status">{submitLabel === 'Unlock' ? 'Unlocking…' : 'Please wait…'}</h1></main>;

  return <main className="device-screen device-lock">
    <header className="device-lock-heading"><LanternIcon /><span>SAGE Lantern</span></header>
    <section className="device-lock-content" aria-labelledby="device-lock-title">
      <div className="device-lock-copy">
        <h1 id="device-lock-title">{title}</h1>
        <div className="device-lock-digits" aria-label={`${pin.length} digits entered`}><span aria-hidden="true">{pin ? (allowReveal && revealed ? pin : '●'.repeat(pin.length)) : '—'}</span></div>
        {allowReveal && <button type="button" className="device-lock-reveal" disabled={busy || !pin} aria-pressed={revealed} onClick={() => setRevealed((value) => !value)}>{revealed ? 'Hide passcode' : 'Show passcode'}</button>}
        <p role="status" aria-live="polite">{message}</p>
      </div>
      <div className="device-lock-keypad" role="group" aria-label="Number keypad" aria-busy={busy}>
        {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((digit) => <button type="button" key={digit} disabled={busy} onClick={() => setPin((value) => editDevicePin(value, digit))}>{digit}</button>)}
        <button type="button" className="device-lock-secondary" disabled={busy || !pin} onClick={() => { setPin(''); setRevealed(false); }}>Clear</button>
        <button type="button" disabled={busy} onClick={() => setPin((value) => editDevicePin(value, '0'))}>0</button>
        <button type="button" disabled={busy || !pin} aria-label="Erase last number" onClick={() => setPin((value) => editDevicePin(value, 'backspace'))}>⌫</button>
        <button type="button" className="device-lock-submit" disabled={busy || !validDevicePin(pin)} onClick={() => void submit()}>{busy ? 'Please wait…' : submitLabel}</button>
      </div>
    </section>
  </main>;
}
