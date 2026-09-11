'use client';

import { useEffect, useRef, useState } from 'react';
import { parseDeviceHistory, type DeviceHistory } from '../../lib/device-history';
import { voiceCommand } from '../../lib/voice-control';
import { BackIcon, SettingsIcon } from './icons';
import type { DeviceProfile } from '../../lib/device-profile';
import DeviceNameScreen from './name';
import PowerSettingsPanel from './power-settings';

export default function SettingsScreen({ close, profile, onProfileChange, onLock }: { close: () => void; profile?: DeviceProfile; onProfileChange?: (profile: DeviceProfile) => void; onLock?: () => void }) {
  const [renaming, setRenaming] = useState(false);
  const [history, setHistory] = useState<DeviceHistory | null>(null);
  const [confirm, setConfirm] = useState<'questions' | 'photos' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    back.current?.focus();
    let disposed = false;
    void voiceCommand('history/list', {}).then((value) => {
      if (!disposed) setHistory(parseDeviceHistory(value));
    }).catch(() => { if (!disposed) setError('Settings unavailable. Go back and try again.'); });
    return () => { disposed = true; };
  }, []);
  const change = async () => {
    if (!history || history.saving_off_keeps_items !== true || busy || !confirm) return;
    if (confirm === 'photos' && history.photo_saving_supported !== true) return;
    setBusy(true); setError('');
    try {
      const value = confirm === 'photos'
        ? await voiceCommand('history/settings', { photos_enabled: !history.photos_enabled })
        : await voiceCommand('history/settings', { enabled: !history.enabled });
      setHistory(parseDeviceHistory(value));
      setConfirm(null);
    } catch {
      setConfirm(null);
      setHistory(null);
      setError('Could not confirm the setting. Go back and reopen Settings before retrying.');
    } finally { setBusy(false); }
  };
  const goBack = () => { if (!busy) { if (confirm) setConfirm(null); else close(); } };
  const stopping = confirm === 'photos' ? history?.photos_enabled : history?.enabled;
  if (renaming && profile && onProfileChange) return <DeviceNameScreen profile={profile} saved={(value) => { onProfileChange(value); setRenaming(false); }} close={() => setRenaming(false)} />;
  return <section className="device-subscreen" aria-label="Settings" onKeyDown={(event) => { if (event.key === 'Escape') goBack(); }}>
    <header className="device-subheader"><button ref={back} disabled={busy} onClick={goBack}><BackIcon />Back</button><div className="device-page-title"><SettingsIcon /><h1>Settings</h1></div></header>
    <div className="device-subcontent" tabIndex={0}>
      {!confirm && profile && <section className="device-panel"><span className="device-section-label">Your Lantern</span><div className="device-history-setting"><h2>{profile.display_name}</h2><button disabled={!onProfileChange} onClick={() => setRenaming(true)}>Change name</button></div>{onLock && <button onClick={onLock}>Lock now</button>}<p className="device-note">Your name stays on this device. Discoverability belongs in Nearby.</p></section>}
      {!confirm && <PowerSettingsPanel />}
      {error && <p role="alert">{error}</p>}
      {history ? confirm ? <div className="device-confirm" role="alertdialog" aria-label="Confirm saving preference">
        <h2>{confirm === 'photos' ? stopping ? 'Stop saving new photos?' : 'Save attached photos?' : stopping ? 'Stop saving new questions?' : 'Save questions on this device?'}</h2>
        <p>{stopping ? 'Existing saved conversations stay available. Remove them separately in Past questions.' : history.sage_backed ? 'Save conversations so Lantern can remember them later. Anyone using this device can read them. Original records cannot be erased; nothing is shared publicly.' : 'Anyone using this device can read saved questions and view saved photos. Only future completed answers are saved. Nothing is published.'}</p>
        <div className="device-actions"><button disabled={busy} onClick={() => setConfirm(null)}>Cancel</button><button className="device-action-primary" disabled={busy} onClick={() => void change()}>{busy ? 'Saving…' : stopping ? 'Stop saving' : 'Start saving'}</button></div>
      </div> : <>
        <section className="device-panel"><span className="device-section-label">Privacy · on this device</span>
        <div className="device-history-setting"><h2>Save questions</h2><button className="device-setting-switch" role="switch" aria-label="Save questions" aria-checked={history.enabled} disabled={history.saving_off_keeps_items !== true} onClick={() => setConfirm('questions')}><span aria-hidden="true" className="device-switch-track"><span /></span>{history.enabled ? 'On' : 'Off'}</button></div>
        <p>Anyone using this device can read saved questions and view saved photos.</p>
        {history.photo_saving_supported === true && <><div className="device-history-setting"><h2>Keep attached photos</h2><button className="device-setting-switch" role="switch" aria-label="Keep attached photos" aria-checked={history.photos_enabled === true} disabled={!history.enabled || history.saving_off_keeps_items !== true} onClick={() => setConfirm('photos')}><span aria-hidden="true" className="device-switch-track"><span /></span>{history.photos_enabled ? 'On' : 'Off'}</button></div><p className="device-note">{history.enabled ? 'Keeps the original photo with its completed answer.' : 'Photo saving is paused while question saving is off.'}</p></>}
        <div className="device-facts"><span>{history.sage_backed ? 'Conversations remembered' : '100 answers · 30 days'}</span><span>Saved on this device</span></div>
        </section>
        <p className="device-note">{history.photo_saving_supported !== true ? 'Photo saving requires a device update. ' : ''}Turning saving off keeps existing history. Nothing is shared publicly.</p>
        {history.saving_off_keeps_items !== true && <p role="status">A device update is required before changing this setting safely.</p>}
      </> : !error && <p role="status">Loading settings…</p>}
      {!confirm && <section className="device-panel"><span className="device-section-label">Planned</span><h2>Bluetooth audio</h2><p>Listen to spoken answers through headphones or a headset.</p><p className="device-note">Pairing and audio output controls are not available in Lantern yet.</p></section>}
    </div>
  </section>;
}
