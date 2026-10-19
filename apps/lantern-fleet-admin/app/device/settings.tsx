'use client';

import { useEffect, useRef, useState } from 'react';
import { parseDeviceHistory, type DeviceHistory } from '../../lib/device-history';
import { photoRetentionNote, retentionFacts, savingNotice } from '../../lib/device-retention';
import { voiceCommand } from '../../lib/voice-control';
import { BackIcon, SettingsIcon } from './icons';
import type { DeviceProfile } from '../../lib/device-profile';
import DeviceNameScreen from './name';
import PowerSettingsPanel from './power-settings';

export default function SettingsScreen({ close, profile, onProfileChange, onLock, onOpen }: { close: () => void; profile?: DeviceProfile; onProfileChange?: (profile: DeviceProfile) => void; onLock?: () => void; onOpen?: (destination: 'agent' | 'heartbeat' | 'hardware') => void }) {
  const [renaming, setRenaming] = useState(false);
  const [history, setHistory] = useState<DeviceHistory | null>(null);
  const [confirm, setConfirm] = useState<'questions' | 'photos' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const back = useRef<HTMLButtonElement>(null);
  const questionSwitch = useRef<HTMLButtonElement>(null);
  const photoSwitch = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  /** The control that opens the rename surface. */
  const nameSwitch = useRef<HTMLButtonElement>(null);
  /**
   * Whether that surface was opened from here.
   *
   * It replaces the panel that holds the button, so the element that had focus is
   * unmounted and focus falls to the document. This flag keeps a first mount from
   * being read as a return, which would take focus off Back before the user has
   * done anything.
   */
  const renameOpened = useRef(false);
  /**
   * Which switch opened the confirmation.
   *
   * The dialog replaces the panel that held the switch, so the element that had
   * focus is unmounted and focus falls to the document. Remembering *which*
   * control opened it — rather than the element itself — is what lets focus
   * return to the switch once it is remounted, and fall back to Back when a
   * failed write removed the panel for good.
   */
  const openedFrom = useRef<'questions' | 'photos' | null>(null);
  const openConfirm = (which: 'questions' | 'photos') => {
    openedFrom.current = which;
    setConfirm(which);
  };
  // Focus travels with the confirmation: into it on entry, and back to the
  // switch that opened it when it goes away, whatever closed it — Cancel,
  // Escape, or the write's own outcome.
  useEffect(() => {
    if (confirm) { cancel.current?.focus(); return; }
    const from = openedFrom.current;
    if (!from) return;
    openedFrom.current = null;
    const opener = from === 'photos' ? photoSwitch.current : questionSwitch.current;
    (opener ?? back.current)?.focus();
  }, [confirm]);
  // The rename surface follows the same rule in the other direction: it focuses
  // its own Back on entry (`name.tsx`), and this returns focus to the button that
  // was unmounted to make room for it — whether Back or a saved name closed it.
  useEffect(() => {
    if (renaming || !renameOpened.current) return;
    renameOpened.current = false;
    nameSwitch.current?.focus();
  }, [renaming]);
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
  // The saving and retention sentences are selected from the runtime's policy
  // fields rather than from `sage_backed` alone, and the photo half appears only
  // when photos are really being saved — a supported policy is not a photo kept.
  const facts = history ? retentionFacts(history) : null;
  if (renaming && profile && onProfileChange) return <DeviceNameScreen profile={profile} saved={(value) => { onProfileChange(value); setRenaming(false); }} close={() => setRenaming(false)} />;
  return <section className="device-subscreen" aria-label="Settings" onKeyDown={(event) => { if (event.key === 'Escape') goBack(); }}>
    <header className="device-subheader"><button ref={back} disabled={busy} onClick={goBack}><BackIcon />Back</button><div className="device-page-title"><SettingsIcon /><h1>Settings</h1></div></header>
    <div className="device-subcontent" tabIndex={0}>
      {!confirm && profile && <section className="device-panel"><span className="device-section-label">Your Lantern</span><div className="device-history-setting"><h2>{profile.display_name}</h2><button ref={nameSwitch} disabled={!onProfileChange} onClick={() => { renameOpened.current = true; setRenaming(true); }}>Change name</button></div>{onLock && <button onClick={onLock}>Lock now</button>}<p className="device-note">Your name stays on this device. Discoverability belongs in Nearby.</p></section>}
      {!confirm && <PowerSettingsPanel />}
      {error && <p role="alert">{error}</p>}
      {history ? confirm ? <div className="device-confirm" role="alertdialog" aria-label="Confirm saving preference">
        <h2>{confirm === 'photos' ? stopping ? 'Stop saving new photos?' : 'Save attached photos?' : stopping ? 'Stop saving new questions?' : 'Save questions on this device?'}</h2>
        <p>{stopping ? 'Existing saved conversations stay available. Remove them separately in Past questions.' : savingNotice(history)}</p>
        <div className="device-actions"><button ref={cancel} disabled={busy} onClick={() => setConfirm(null)}>Cancel</button><button className="device-action-primary" disabled={busy} onClick={() => void change()}>{busy ? 'Saving…' : stopping ? 'Stop saving' : 'Start saving'}</button></div>
      </div> : <>
        <section className="device-panel"><span className="device-section-label">Privacy · on this device</span>
        <div className="device-history-setting"><h2>Save questions</h2><button ref={questionSwitch} className="device-setting-switch" role="switch" aria-label="Save questions" aria-checked={history.enabled} disabled={history.saving_off_keeps_items !== true} onClick={() => openConfirm('questions')}><span aria-hidden="true" className="device-switch-track"><span /></span>{history.enabled ? 'On' : 'Off'}</button></div>
        <p>Anyone using this device can read saved questions and view saved photos.</p>
        {history.photo_saving_supported === true && <><div className="device-history-setting"><h2>Keep attached photos</h2><button ref={photoSwitch} className="device-setting-switch" role="switch" aria-label="Keep attached photos" aria-checked={history.photos_enabled === true} disabled={!history.enabled || history.saving_off_keeps_items !== true} onClick={() => openConfirm('photos')}><span aria-hidden="true" className="device-switch-track"><span /></span>{history.photos_enabled ? 'On' : 'Off'}</button></div><p className="device-note">{photoRetentionNote(history)}</p></>}
        <div className="device-facts"><span>{facts?.retention}</span><span>{facts?.storage}</span></div>
        </section>
        <p className="device-note">{history.photo_saving_supported !== true ? 'Photo saving requires a device update. ' : ''}Turning saving off keeps existing history. Nothing is shared publicly.</p>
        {history.saving_off_keeps_items !== true && <p role="status">A device update is required before changing this setting safely.</p>}
      </> : !error && <p role="status">Loading settings…</p>}
      {!confirm && onOpen && <section className="device-panel"><span className="device-section-label">Other screens</span>
        <div className="device-history-setting"><h2>Agent</h2><button onClick={() => onOpen('agent')}>Open</button></div>
        <div className="device-history-setting"><h2>Heartbeat</h2><button onClick={() => onOpen('heartbeat')}>Open</button></div>
        <div className="device-history-setting"><h2>Hardware</h2><button onClick={() => onOpen('hardware')}>Open</button></div>
        <p className="device-note">Each of these reports what the device actually exposes. Tasks and heartbeat settings are not available on this device yet.</p>
      </section>}
      {!confirm && <section className="device-panel"><span className="device-section-label">Planned</span><h2>Bluetooth audio</h2><p>Listen to spoken answers through headphones or a headset.</p><p className="device-note">Pairing and audio output controls are not available in Lantern yet.</p></section>}
    </div>
  </section>;
}
