'use client';

import type { RefObject } from 'react';
import { EnvelopeIcon, LanternIcon } from './icons';
import RadioSignal from './radio-signal';

export type HeaderPage = 'main' | 'camera' | 'history' | 'status' | 'about' | 'settings' | 'messages' | 'sharing' | 'correction';

// One header for every unlocked device screen: the wordmark returns home (or
// starts a new chat on the main screen), the meter opens Nearby, and Messages
// and Menu stay reachable instead of only existing on the home screen.
export default function DeviceHeader({ busy, clearing, page, menuOpen, homeRef, messagesRef, menuRef,
  onHome, onNearby, onMessages, onMenu }: {
  busy: boolean; clearing: boolean; page: HeaderPage; menuOpen: boolean;
  homeRef?: RefObject<HTMLButtonElement | null>; messagesRef?: RefObject<HTMLButtonElement | null>;
  menuRef?: RefObject<HTMLButtonElement | null>;
  onHome: () => void; onNearby: () => void; onMessages: () => void; onMenu: () => void;
}) {
  const home = page === 'main';
  return <header className="device-top" inert={menuOpen}>
    <div className="device-home-group">
      <button ref={homeRef} className="device-wordmark" disabled={busy} onClick={onHome} aria-busy={clearing}
        aria-label={home ? 'New chat; keep saved conversations' : 'Back to your Lantern home screen'} title={home ? 'New chat' : 'Home'}><LanternIcon /><span>SAGE Lantern</span></button>
      <button className="device-lora-indicator" disabled={busy} onClick={onNearby} aria-current={page === 'status' ? 'page' : undefined} aria-label="Open nearby connections" title="Nearby"><RadioSignal paused={busy} /></button>
    </div>
    <div className="device-top-actions">
      <button ref={messagesRef} className="device-messages-shortcut" disabled={busy} onClick={onMessages} aria-current={page === 'messages' ? 'page' : undefined} aria-label="Messages" title="Messages"><EnvelopeIcon /></button>
      <button ref={menuRef} disabled={busy} onClick={onMenu} aria-haspopup="dialog" aria-expanded={menuOpen}><span aria-hidden="true">☰</span> Menu</button>
    </div>
  </header>;
}
