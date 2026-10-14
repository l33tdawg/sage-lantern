export function CameraIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true"><path d="M4 10h6l2-4h8l2 4h6v16H4z" /><circle cx="16" cy="18" r="5" /></svg>;
}

export function LanternIcon() {
  return <svg viewBox="0 0 40 40" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 9V7a5 5 0 0 1 10 0v2M12 12h16l3 20H9l3-20ZM11 36h18M15 9h10M20 12v3" /><path d="M20 18c1 4 5 5 5 9a5 5 0 0 1-10 0c0-3 3-5 5-9Z" fill="currentColor" stroke="none" /></svg>;
}

export function UnmeasuredSignalIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M3 24h4v5H3zM10 18h4v11h-4zM17 12h4v17h-4zM24 5h4v24h-4z" opacity=".5" /><path d="m3 3 26 26" strokeWidth="2.5" /></svg>;
}

export function BackIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true"><path d="m14 6-10 10 10 10M5 16h23" /></svg>;
}

export function RetakeIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true"><path d="M6 14a11 11 0 1 1 1 10M6 5v9h9" /></svg>;
}

export function MicrophoneIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true"><rect x="11" y="3" width="10" height="17" rx="5" /><path d="M7 15v2a9 9 0 0 0 18 0v-2M16 26v4M10 30h12" /></svg>;
}

export function KeyboardIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><rect x="2" y="8" width="28" height="17" rx="3" /><path d="M7 13h2M13 13h2M19 13h2M25 13h.5M7 18h2M13 18h2M19 18h2M25 18h.5M10 22h12" /></svg>;
}

export function SendIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true"><path d="m3 4 26 12L3 28l4-12-4-12ZM7 16h22" /></svg>;
}

export function CancelIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true"><path d="m7 7 18 18M25 7 7 25" /></svg>;
}

export function SettingsIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m13 3-1 4-3 2-4-1-3 5 3 3v3l-3 3 3 5 4-1 3 2 1 3h6l1-3 3-2 4 1 3-5-3-3v-3l3-3-3-5-4 1-3-2-1-4z" transform="translate(0 -1)" /><circle cx="16" cy="16" r="5" /></svg>;
}

export function HistoryIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 11a12 12 0 1 1-1 12M4 4v8h8M16 9v8l5 3" /></svg>;
}

export function InfoIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" aria-hidden="true"><circle cx="16" cy="16" r="12" /><path d="M16 15v8M16 9v1" /></svg>;
}

export function LockIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="6" y="14" width="20" height="15" rx="3" /><path d="M10 14V9a6 6 0 0 1 12 0v5M16 20v3" /></svg>;
}

export function NetworkIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m14 10-5 12m9-12 5 12M11 25h10" /><circle cx="16" cy="7" r="4" /><circle cx="7" cy="25" r="4" /><circle cx="25" cy="25" r="4" /></svg>;
}

export function EnvelopeIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="6" width="26" height="20" rx="3" /><path d="m4 8 12 10L28 8" /></svg>;
}

export function ShareIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M16 21V3m-6 6 6-6 6 6M9 15H5v14h22V15h-4" /></svg>;
}

/** Retained photos. One shared glyph, so the Home tile and the Gallery header
 *  cannot drift into two different pictures for the same destination. */
export function GalleryIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="6" width="26" height="20" rx="4" /><circle cx="11" cy="13" r="2.4" /><path d="m4 23 7-6 5 4 4-3 8 6" /></svg>;
}

/** Chat. Shared by the Home tile and Chat's own title bar for the same reason. */
export function ChatIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 8h22v14H12l-7 6z" /></svg>;
}

/** Tasks. Shared by the Home tile and the Agent title bar, never a second picture. */
export function AgentIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 8h16M12 16h16M12 24h16" /><path d="m3 8 2 2 3-4M3 16l2 2 3-4M3 24l2 2 3-4" /></svg>;
}

/** Heartbeat. A pulse, because the screen reports a state rather than drawing one. */
export function HeartbeatIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M2 17h6l3-9 5 19 4-14 3 4h7" /></svg>;
}

/** Hardware. A board outline with its connector row. Deliberately no pin numbers:
 *  the schema has no pin map, so a numbered connector would be an invented one. */
export function HardwareIcon() {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="7" y="7" width="18" height="18" rx="3" /><path d="M12 3v4M20 3v4M12 25v4M20 25v4M3 12h4M3 20h4M25 12h4M25 20h4" /><rect x="12" y="12" width="8" height="8" rx="1.5" /></svg>;
}
