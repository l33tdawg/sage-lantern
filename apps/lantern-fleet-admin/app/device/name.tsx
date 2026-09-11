'use client';

import { useState } from 'react';
import { saveDeviceName, type DeviceProfile } from '../../lib/device-profile';
import { BackIcon, LanternIcon } from './icons';

export default function DeviceNameScreen({ profile, saved, close }: {
  profile: DeviceProfile; saved: (profile: DeviceProfile) => void; close?: () => void;
}) {
  const [name, setName] = useState(profile.display_name);
  const [typing, setTyping] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    if (busy || !name.trim()) return;
    setBusy(true); setError('');
    try { saved(await saveDeviceName(profile, name)); }
    catch { setError('Could not save the name. Go back and try again.'); }
    finally { setBusy(false); }
  };
  return <section className="device-subscreen device-name" aria-label="Name your Lantern">
    <header className="device-subheader">{close && <button disabled={busy} onClick={close}><BackIcon />Back</button>}<div className="device-page-title"><LanternIcon /><h1>Your Lantern</h1></div></header>
    <div className="device-subcontent">
      <h2>{profile.setup_required ? 'Choose a name' : 'Change the name'}</h2>
      <p className="device-note">{profile.setup_required ? 'This name stays on your device. You can change it later.' : 'Your questions and memories stay the same.'}</p>
      <output className="device-name-value" aria-live="polite" aria-label="Device name">{name || 'Your name here'}</output>
      {typing ? <div className="device-name-keyboard" role="group" aria-label="Name keyboard">
        {'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.split('').map((letter) => <button type="button" key={letter} disabled={busy || name.length >= 40} onClick={() => setName((value) => value + letter)}>{letter}</button>)}
        <button type="button" disabled={busy || name.length >= 40} onClick={() => setName((value) => value + ' ')}>Space</button>
        <button type="button" disabled={busy || !name} onClick={() => setName((value) => value.slice(0, -1))} aria-label="Erase last letter">⌫</button>
        <button type="button" disabled={busy || !name} onClick={() => setName('')}>Clear</button>
      </div> : <div className="device-name-choices">{['River', 'Cedar', 'Sunrise', 'Moon', 'Orchid', 'Horizon'].map((choice) => <button key={choice} disabled={busy} aria-pressed={name === choice} onClick={() => setName(choice)}>{choice}</button>)}<button disabled={busy} onClick={() => setTyping(true)}>Choose another name</button></div>}
      {error && <p role="alert">{error}</p>}
      <div className="device-name-actions">{typing && <button disabled={busy} onClick={() => setTyping(false)}>Suggestions</button>}<button className="device-action-primary" disabled={busy || !name.trim()} onClick={() => void save()}>{busy ? 'Saving…' : 'Use this name'}</button></div>
    </div>
  </section>;
}
