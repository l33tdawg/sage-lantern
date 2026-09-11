'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { deviceAuth, getDeviceToken, setDeviceToken, validDeviceToken } from '../../lib/device-session';
import { linkVoiceSession } from '../../lib/voice-control';
import LockScreen, { type UnlockResult } from './lock';
import DeviceScreen from './screen';
import DeviceNameScreen from './name';
import { loadDeviceProfile, type DeviceProfile } from '../../lib/device-profile';
import { parseDeviceSetup, type DeviceSetup } from '../../lib/device-setup';
import DeviceSetupScreen from './setup';
import { WakeInputGuard, WAKE_IDLE_MS, WAKE_QUIET_MS } from '../../lib/wake-input';
import { LockIcon } from './icons';

export default function DeviceAccessBoundary({ enabled }: { enabled: boolean }) {
  const [unlocked, setUnlocked] = useState(false);
  const [sleeping, setSleeping] = useState(false);
  const [waking, setWaking] = useState(false);
  const [securing, setSecuring] = useState(false);
  const [lockError, setLockError] = useState(false);
  const [authenticating, setAuthenticating] = useState(false);
  const [profile, setProfile] = useState<DeviceProfile | null>(null);
  const [setup, setSetup] = useState<DeviceSetup | null>(null);
  const [setupError, setSetupError] = useState(false);
  const [setupRevision, setSetupRevision] = useState(0);
  const [wakeRevision, setWakeRevision] = useState(0);
  const surface = useRef<HTMLDivElement>(null);
  const pendingRevocation = useRef<string | null>(null);
  const mounted = useRef(false);
  const generation = useRef(0);
  const lastActivity = useRef(0);
  const refreshSetup = useCallback(() => { setSetup(null); setSetupError(false); setSetupRevision((value) => value + 1); }, []);

  useLayoutEffect(() => {
    if (!unlocked && surface.current) surface.current.style.visibility = '';
  }, [unlocked, wakeRevision]);

  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    void deviceAuth('setup/status', {}).then((value) => {
      if (!disposed) setSetup(parseDeviceSetup(value));
    }).catch(() => { if (!disposed) setSetupError(true); });
    return () => { disposed = true; };
  }, [enabled, setupRevision]);

  const lock = useCallback(() => {
    const token = getDeviceToken();
    generation.current += 1;
    setDeviceToken(null); setUnlocked(false); setSleeping(false); setProfile(null);
    if (token) {
      pendingRevocation.current = token; setSecuring(true);
      void deviceAuth('lock', { token }).then((result) => {
        if (mounted.current && pendingRevocation.current === token && result.locked === true) {
          pendingRevocation.current = null; setSecuring(false); setLockError(false);
        }
      }).catch(() => { if (mounted.current) setLockError(true); });
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    mounted.current = true;
    setDeviceToken(null);
    const hide = () => { if (document.hidden) lock(); };
    const leave = () => { if (surface.current) surface.current.style.visibility = 'hidden'; lock(); };
    const restore = (event: PageTransitionEvent) => { if (event.persisted) { leave(); setWakeRevision((value) => value + 1); } };
    document.addEventListener('visibilitychange', hide);
    window.addEventListener('pagehide', leave);
    window.addEventListener('pageshow', restore);
    let disposed = false;
    let next: ReturnType<typeof setTimeout>;
    let lastLink = 0;
    const poll = async () => {
      try {
        const revoked = pendingRevocation.current;
        if (revoked) {
          const result = await deviceAuth('cancel', { token: revoked });
          const confirmed = typeof result.cancelled === 'boolean';
          if (!disposed && confirmed && pendingRevocation.current === revoked) {
            pendingRevocation.current = null; setSecuring(false); setLockError(false);
          }
        } else {
          const token = getDeviceToken();
          if (token && !document.hidden) {
            const result = await deviceAuth('presence', { token });
            if (!disposed && token === getDeviceToken()) {
              if (result.authorized !== true) lock();
              else if (Date.now() - lastLink >= 5000) {
                lastLink = Date.now(); void linkVoiceSession();
              }
            }
          }
        }
      } catch {
        if (!disposed) { setLockError(true); if (getDeviceToken()) lock(); }
      } finally { if (!disposed) next = setTimeout(poll, 1000); }
    };
    void poll();
    return () => {
      disposed = true; mounted.current = false; generation.current += 1; clearTimeout(next);
      document.removeEventListener('visibilitychange', hide);
      window.removeEventListener('pagehide', leave);
      window.removeEventListener('pageshow', restore);
      const token = getDeviceToken() || pendingRevocation.current; setDeviceToken(null);
      if (token) void deviceAuth('cancel', { token }).catch(() => undefined);
    };
  }, [enabled, lock]);

  useEffect(() => {
    if (!enabled || !unlocked) return;
    const activity = (event: Event) => {
      if (!event.isTrusted || Date.now() - lastActivity.current < 10000) return;
      const token = getDeviceToken();
      if (!token) return;
      lastActivity.current = Date.now();
      void deviceAuth('activity', { token }).then((result) => {
        if (mounted.current && token === getDeviceToken() && result.authorized !== true) lock();
      }).catch(() => { if (mounted.current && token === getDeviceToken()) lock(); });
    };
    document.addEventListener('pointerdown', activity, { passive: true });
    document.addEventListener('keydown', activity);
    return () => {
      document.removeEventListener('pointerdown', activity);
      document.removeEventListener('keydown', activity);
    };
  }, [enabled, unlocked, lock]);

  useEffect(() => {
    if (!enabled || unlocked || securing || sleeping || waking || authenticating || !setup?.enabled || !setup.recovery_backup_confirmed) return;
    let timer = setTimeout(() => setSleeping(true), 30000);
    const activity = () => { clearTimeout(timer); timer = setTimeout(() => setSleeping(true), 30000); };
    document.addEventListener('pointerdown', activity, { passive: true });
    document.addEventListener('keydown', activity);
    return () => { clearTimeout(timer); document.removeEventListener('pointerdown', activity); document.removeEventListener('keydown', activity); };
  }, [enabled, unlocked, securing, sleeping, waking, authenticating, setup]);

  useEffect(() => {
    if (!enabled || unlocked) return;
    const guard = new WakeInputGuard();
    let lastInput = performance.now();
    let timer: ReturnType<typeof setTimeout>;
    const finish = () => {
      if (!guard.release(performance.now())) { timer = setTimeout(finish, 100); return; }
      setSleeping(false); setWaking(false);
    };
    const capture = (event: Event) => {
      const now = performance.now();
      const trigger = event.type !== 'blur' && (sleeping || now - lastInput >= WAKE_IDLE_MS);
      lastInput = now;
      if (!guard.consume(event.type, now, trigger,
        event instanceof PointerEvent ? event.pointerId : 0,
        event instanceof KeyboardEvent ? event.code : '')) return;
      if (event.cancelable) event.preventDefault();
      event.stopImmediatePropagation();
      setWaking(true);
      clearTimeout(timer);
      timer = setTimeout(finish, WAKE_QUIET_MS);
    };
    const events = ['pointerdown', 'pointerup', 'pointercancel', 'click', 'mousedown', 'mouseup', 'touchstart', 'touchend', 'touchcancel', 'keydown', 'keyup', 'wheel'];
    for (const name of events) window.addEventListener(name, capture, { capture: true, passive: false });
    window.addEventListener('blur', capture);
    return () => {
      clearTimeout(timer);
      for (const name of events) window.removeEventListener(name, capture, true);
      window.removeEventListener('blur', capture);
    };
  }, [enabled, unlocked, sleeping]);

  const unlock = async (pin: string): Promise<UnlockResult> => {
    if (pendingRevocation.current) return 'unavailable';
    const attempt = generation.current;
    let token: string | null = null;
    setAuthenticating(true);
    try {
      const result = await deviceAuth('unlock', { pin });
      if (!validDeviceToken(result.token)) return 'unavailable';
      token = result.token;
      if (!mounted.current || generation.current !== attempt || document.hidden) {
        await deviceAuth('cancel', { token }); return 'unavailable';
      }
      const activated = await deviceAuth('activate', { token });
      if (activated.authorized !== true) throw new Error('Activation unavailable');
      if (!mounted.current || generation.current !== attempt || document.hidden) {
        await deviceAuth('cancel', { token }); return 'unavailable';
      }
      setDeviceToken(token);
      void linkVoiceSession();
      const currentProfile = await loadDeviceProfile();
      if (!mounted.current || generation.current !== attempt || document.hidden) {
        setDeviceToken(null); await deviceAuth('cancel', { token }); return 'unavailable';
      }
      setLockError(false); lastActivity.current = 0;
      setProfile(currentProfile);
      return 'unlocked';
    } catch (error) {
      if (token) {
        setDeviceToken(null); pendingRevocation.current = token;
        if (mounted.current) setSecuring(true);
      }
      return error instanceof Error && error.message === 'rate_limited' ? 'retry_later' : 'unavailable';
    } finally { if (mounted.current) setAuthenticating(false); }
  };

  if (!enabled) return <DeviceScreen />;
  let content;
  if (unlocked && profile?.setup_required) content = <main className="device-screen"><DeviceNameScreen profile={profile} saved={(value) => { if (getDeviceToken()) setProfile(value); }} close={lock} /></main>;
  else if (unlocked && profile) content = <DeviceScreen onLock={lock} profile={profile} onProfileChange={setProfile} />;
  else if (waking) content = <main className="device-screen device-lock-wait"><h1 role="status">Waking…</h1></main>;
  else if (sleeping) content = <main className="device-screen device-sleep"><button aria-label="Device locked. Tap to wake"><LockIcon /><span>Device locked</span><span>Tap to wake</span></button></main>;
  else if (securing) content = <main className="device-screen device-lock-wait"><h1>Locking…</h1><p role="status">{lockError ? 'Securing your Lantern. Please wait.' : 'Closing your session.'}</p></main>;
  else if (!setup) content = <main className="device-screen device-lock-wait"><h1>{setupError ? 'Setup unavailable' : 'Checking your Lantern…'}</h1>{setupError && <button onClick={refreshSetup}>Try again</button>}</main>;
  else if (!setup.enabled || !setup.recovery_backup_confirmed) content = <DeviceSetupScreen status={setup} refresh={refreshSetup} />;
  else content = <LockScreen unlock={unlock} onUnlocked={() => { if (getDeviceToken()) setUnlocked(true); }} />;
  return <div ref={surface}>{content}</div>;
}
