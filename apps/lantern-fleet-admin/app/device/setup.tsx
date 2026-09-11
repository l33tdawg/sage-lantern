'use client';

import { useEffect, useRef, useState } from 'react';
import { deviceAuth } from '../../lib/device-session';
import { validRecoveryKey, type DeviceSetup } from '../../lib/device-setup';
import LockScreen, { type UnlockResult } from './lock';

export default function DeviceSetupScreen({ status, refresh }: { status: DeviceSetup; refresh: () => void }) {
  const [phase, setPhase] = useState<'choose' | 'confirm' | 'recover' | 'backup' | 'check'>(status.enabled ? 'recover' : 'choose');
  const [recoveryKey, setRecoveryKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pin = useRef('');
  const generation = useRef(0);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    const hide = (event: Event) => {
      if (!document.hidden && event.type !== 'pagehide') return;
      generation.current += 1; pin.current = ''; setRecoveryKey(''); setPhase('check');
    };
    document.addEventListener('visibilitychange', hide);
    window.addEventListener('pagehide', hide);
    return () => { mounted.current = false; generation.current += 1; pin.current = ''; document.removeEventListener('visibilitychange', hide); window.removeEventListener('pagehide', hide); };
  }, []);

  useEffect(() => {
    if (phase !== 'backup') return;
    const timer = setTimeout(() => { generation.current += 1; pin.current = ''; setRecoveryKey(''); setPhase('check'); }, 120000);
    return () => clearTimeout(timer);
  }, [phase]);

  const obtainRecovery = async (entered: string): Promise<UnlockResult> => {
    const attempt = generation.current;
    if (phase === 'confirm' && entered !== pin.current) return 'incorrect';
    try {
      const result = await deviceAuth(phase === 'recover' ? 'setup/recovery' : 'setup/enable', phase === 'recover' ? { pin: entered } : { pin: pin.current, confirmation: entered });
      if (!mounted.current || generation.current !== attempt || document.hidden) return 'unavailable';
      if (!validRecoveryKey(result.recovery_key)) throw new Error('Invalid recovery response');
      pin.current = entered; setRecoveryKey(result.recovery_key);
      return 'unlocked';
    } catch {
      if (mounted.current && generation.current === attempt) { pin.current = ''; setRecoveryKey(''); setPhase('check'); }
      return 'unavailable';
    }
  };

  const confirmBackup = async () => {
    if (busy || !pin.current || !recoveryKey) return;
    const attempt = generation.current;
    setBusy(true); setError('');
    try {
      const result = await deviceAuth('setup/confirm', { pin: pin.current });
      if (!mounted.current || generation.current !== attempt || document.hidden) return;
      if (result.recovery_backup_confirmed !== true) throw new Error('Confirmation unavailable');
      pin.current = ''; setRecoveryKey(''); refresh();
    } catch { if (mounted.current) setError('Could not confirm. Please check setup again.'); }
    finally { if (mounted.current) setBusy(false); }
  };

  if (phase === 'check' || (!status.enabled && !status.can_initialize)) return <main className="device-screen device-lock-wait"><h1>Check setup</h1><p>Setup needs attention. Your existing keys will not be replaced.</p><button onClick={refresh}>Check again</button></main>;
  if (phase === 'backup') return <main className="device-screen device-lock-wait"><h1>Save your recovery key</h1><p>Keep this somewhere private. You need it if you forget your passcode.</p><code className="device-recovery-key">{recoveryKey}</code><p>Anyone with this key can access your encrypted memories.</p>{error && <p role="alert">{error}</p>}<button disabled={busy} onClick={() => void confirmBackup()}>{busy ? 'Saving…' : 'I have saved it safely'}</button></main>;
  if (phase === 'choose') return <LockScreen key="choose" title="Create your passcode" hint="Use 8–12 digits you can remember" submitLabel="Continue" allowReveal unlock={async (entered) => { pin.current = entered; return 'unlocked'; }} onUnlocked={() => setPhase('confirm')} />;
  return <LockScreen key={phase} title={phase === 'confirm' ? 'Confirm your passcode' : 'Your recovery key'} hint={phase === 'confirm' ? 'Enter the same passcode again' : 'Enter your current passcode'} submitLabel="Continue" allowReveal={phase === 'confirm'} unlock={obtainRecovery} onUnlocked={() => setPhase('backup')} />;
}
