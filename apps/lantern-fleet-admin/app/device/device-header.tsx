'use client';

import type { RefObject } from 'react';
import { EnvelopeIcon, LanternIcon } from './icons';
import RadioSignal from './radio-signal';

export type HeaderPage = 'home' | 'main' | 'camera' | 'history' | 'status' | 'about' | 'settings' | 'messages' | 'sharing' | 'gallery' | 'agent' | 'heartbeat' | 'hardware' | 'correction';

// One header for every unlocked device screen: the wordmark returns home (or
// starts a new chat on the main screen), the meter opens Nearby, and Messages
// and Menu stay reachable instead of only existing on the home screen.
export default function DeviceHeader({ busy, clearing, page, menuOpen, homeRef, messagesRef, nearbyRef, menuRef,
  wordmarkStartsChat = true, wordmarkBlocked = false, onHome, onNearby, onMessages, onMenu }: {
  busy: boolean; clearing: boolean; page: HeaderPage; menuOpen: boolean;
  homeRef?: RefObject<HTMLButtonElement | null>; messagesRef?: RefObject<HTMLButtonElement | null>;
  nearbyRef?: RefObject<HTMLButtonElement | null>;
  menuRef?: RefObject<HTMLButtonElement | null>;
  /** False when a Home screen exists: then the wordmark navigates and New chat is its own action. */
  wordmarkStartsChat?: boolean;
  /** Navigation would unmount and discard an outstanding review or draft. */
  wordmarkBlocked?: boolean;
  onHome: () => void; onNearby: () => void; onMessages: () => void; onMenu: () => void;
}) {
  const home = page === 'main' && wordmarkStartsChat;
  // With a Home screen the wordmark is a destination from anywhere, including
  // the Chat screen, so on `main` it must not advertise a reset.
  const atHome = page === 'home';
  return <header className="device-top" inert={menuOpen}>
    <div className="device-home-group">
      <button ref={homeRef} className="device-wordmark" disabled={wordmarkBlocked || (busy && wordmarkStartsChat)} onClick={onHome} aria-busy={clearing}
        aria-label={home ? 'New chat; keep saved conversations' : atHome ? 'Lantern home' : 'Back to your Lantern home screen'} title={wordmarkBlocked ? 'Finish or cancel your question before returning Home.' : home ? 'New chat' : 'Home'}><LanternIcon /><span>SAGE Lantern</span></button>
      <button ref={nearbyRef} className="device-lora-indicator" disabled={busy} onClick={onNearby} aria-current={page === 'status' ? 'page' : undefined} aria-label="Open nearby connections" title="Nearby"><RadioSignal paused={busy} /></button>
    </div>
    <div className="device-top-actions">
      <button ref={messagesRef} className="device-messages-shortcut" disabled={busy} onClick={onMessages} aria-current={page === 'messages' ? 'page' : undefined} aria-label="Messages" title="Messages"><EnvelopeIcon /></button>
      <button ref={menuRef} disabled={busy} onClick={onMenu} aria-haspopup="dialog" aria-expanded={menuOpen}><span aria-hidden="true">☰</span> Menu</button>
    </div>
  </header>;
}
