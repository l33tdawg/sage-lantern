'use client';

import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { parseTouchSession, voiceCommand, type TouchSession } from '../../lib/voice-control';
import { deviceView, type DeviceStatus } from '../../lib/device-state';
import { CancelIcon, MicrophoneIcon, SendIcon } from './icons';
import type { PhotoAttachment } from '../../lib/photo-preview';
import { parseDeviceHistory } from '../../lib/device-history';

export default function TalkControls({ status, connected, compact = false, onBusyChange, label = 'Ask', photo, autoStart, onAutoStartHandled, onStarted, onCancelled, cancelLabel = 'Cancel' }: { status: DeviceStatus | null; connected: boolean; compact?: boolean; onBusyChange?: (busy: boolean) => void; label?: string; photo?: PhotoAttachment; autoStart?: string; onAutoStartHandled?: () => void; onStarted?: (sessionId: string, photo?: PhotoAttachment) => void; onCancelled?: () => void; cancelLabel?: string }) {
  const [owned, setOwned] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const session = useRef<TouchSession | null>(null);
  const heartbeat = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mounted = useRef(false);
  const busy = useRef(false);
  const recordingActive = useRef(false);
  const consumedStart = useRef<string | undefined>(undefined);

  useEffect(() => {
    onBusyChange?.(owned || pending || uncertain);
    return () => onBusyChange?.(false);
  }, [owned, pending, uncertain, onBusyChange]);
  useEffect(() => {
    if (!connected || !session.current || status?.voice.session_id !== session.current.session_id || !['review', 'processing', 'complete', 'idle', 'error'].includes(status.voice.phase)) return;
    session.current = null;
    recordingActive.current = false;
    clearTimeout(heartbeat.current);
    setOwned(false); setUncertain(false);
  }, [status, connected]);
  useEffect(() => {
    if (!uncertain) return;
    const expiry = setTimeout(() => { session.current = null; setUncertain(false); }, 6000);
    return () => clearTimeout(expiry);
  }, [uncertain]);

  useEffect(() => {
    mounted.current = true;
    const abandon = () => {
      recordingActive.current = false;
      const current = session.current;
      session.current = null;
      clearTimeout(heartbeat.current);
      if (current) void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {});
    };
    const hide = () => {
      if (document.hidden) { abandon(); setOwned(false); }
    };
    document.addEventListener('visibilitychange', hide);
    return () => { mounted.current = false; document.removeEventListener('visibilitychange', hide); abandon(); };
  }, []);

  const keepRecording = (current: TouchSession) => {
    heartbeat.current = setTimeout(async () => {
      if (session.current !== current || !recordingActive.current) return;
      try {
        await voiceCommand('keepalive', { session_id: current.session_id, token: current.token });
        if (session.current === current && recordingActive.current) keepRecording(current);
      } catch {
        if (session.current !== current || !recordingActive.current) return;
        recordingActive.current = false;
        void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {});
        if (mounted.current) { setOwned(false); setUncertain(true); setError('Connection lost. Checking recording status.'); }
      }
    }, 1000);
  };

  const start = async () => {
    if (busy.current || session.current) return;
    busy.current = true;
    setPending(true);
    setError('');
    try {
      const history = photo ? parseDeviceHistory(await voiceCommand('history/list', {})) : null;
      if (!mounted.current || document.hidden) return;
      const retainOriginal = history?.photo_saving_supported === true && history.enabled && history.photos_enabled === true;
      const current = parseTouchSession(await voiceCommand('start', { request_id: crypto.randomUUID(), ...(photo ? { image_base64: photo.image, ...(retainOriginal ? { original_base64: photo.preview } : {}) } : {}) }));
      if (!mounted.current || document.hidden) {
        void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {});
        return;
      }
      session.current = current;
      recordingActive.current = true;
      setOwned(true);
      keepRecording(current);
      onStarted?.(current.session_id, photo);
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error && failure.name === 'Error' ? failure.message : 'Could not start. Try again.');
    } finally { busy.current = false; if (mounted.current) setPending(false); }
  };

  const finish = async (action: 'stop' | 'cancel') => {
    const current = session.current;
    if (!current || busy.current) return;
    busy.current = true;
    setPending(true);
    recordingActive.current = false;
    clearTimeout(heartbeat.current);
    try {
      await voiceCommand(action, { session_id: current.session_id, token: current.token });
      session.current = null;
      if (action === 'cancel' && mounted.current) onCancelled?.();
      if (mounted.current) setError('');
    } catch {
      if (mounted.current) { setUncertain(true); setError('Connection lost. Check recording status.'); }
    } finally {
      busy.current = false;
      if (mounted.current) { setOwned(false); setPending(false); }
    }
  };

  const view = deviceView(status, connected);
  const canStart = !uncertain && !!status?.voice.touch_ready && connected && ['ready', 'complete', 'error'].includes(view.phase);
  const startPhotoQuestion = useEffectEvent(() => {
    onAutoStartHandled?.();
    if (canStart && !document.hidden) void start();
    else setError('Could not start recording. Tap Ask to try again.');
  });
  useEffect(() => {
    if (!autoStart || consumedStart.current === autoStart) return;
    const frame = requestAnimationFrame(() => {
      if (consumedStart.current === autoStart) return;
      consumedStart.current = autoStart;
      startPhotoQuestion();
    });
    return () => cancelAnimationFrame(frame);
  }, [autoStart]);
  return <div className={`device-talk-controls${compact ? ' device-talk-compact' : ''}`}>
    {owned && <div className="device-recording-actions"><button className="device-talk-cancel" disabled={pending} onClick={() => void finish('cancel')}><CancelIcon />{cancelLabel}</button><button className="device-action-primary" disabled={pending} onClick={() => void finish('stop')}><SendIcon />{status?.voice.review_ready ? 'Done' : 'Send'}</button></div>}
    {(error || (!compact && canStart && !owned)) && <span className="device-talk-status" role="status">{error || 'Tap to speak'}</span>}
    <button className="device-talk-primary" disabled={!canStart || pending || owned} aria-busy={pending} onClick={() => void start()}><MicrophoneIcon />{label}</button>
  </div>;
}
