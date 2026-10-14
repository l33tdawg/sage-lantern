'use client';

import { deviceSessionHeaders } from '../../lib/device-session';

import { useCallback, useEffect, useRef, useState } from 'react';
import { CameraFrameDecoder } from '../../lib/camera-stream';
import { BackIcon, CameraIcon, GalleryIcon, RetakeIcon } from './icons';
import type { PhotoAttachment } from '../../lib/photo-preview';

export default function CameraScreen({ close, photoReady = false, onAttach, onOpenGallery }: { close: () => void; photoReady?: boolean; onAttach?: (photo: PhotoAttachment) => void; onOpenGallery?: () => void }) {
  const [phase, setPhase] = useState<'starting' | 'live' | 'photo' | 'off' | 'error'>('starting');
  const [message, setMessage] = useState('Opening camera…');
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const hasFrame = useRef(false);
  const originalFrame = useRef<Uint8Array | null>(null);

  const stop = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
  }, []);

  const start = useCallback(async () => {
    stop();
    hasFrame.current = false;
    originalFrame.current = null;
    setPhase('starting');
    setMessage('Opening camera…');
    const request = new AbortController();
    controller.current = request;
    let watchdog = setTimeout(() => request.abort(), 15000);
    const resetWatchdog = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => request.abort(), 7000);
    };
    const current = () => mounted.current && controller.current === request && !request.signal.aborted;
    try {
      const response = await fetch('http://127.0.0.1:3198/preview', {
        method: 'POST', headers: { 'X-Lantern-Camera': 'preview', ...deviceSessionHeaders() },
        cache: 'no-store', credentials: 'omit', signal: request.signal,
      });
      if (!response.ok || !response.body || !response.headers.get('Content-Type')?.startsWith('application/x-lantern-jpeg-stream')) {
        throw new Error(response.status === 409 ? 'Camera busy. Try again.' : 'Check the camera cable.');
      }
      const decoder = new CameraFrameDecoder();
      const reader = response.body.getReader();
      try {
        while (current()) {
          const { done, value } = await reader.read();
          if (done) { decoder.finish(); break; }
          const frames = decoder.push(value);
          if (!frames.length) continue;
          const bitmap = await createImageBitmap(new Blob([new Uint8Array(frames[frames.length - 1])], { type: 'image/jpeg' }));
          try {
            if (!current()) break;
            const surface = canvas.current;
            const context = surface?.getContext('2d');
            if (!surface || !context || bitmap.width > 4096 || bitmap.height > 4096) throw new Error('Cannot show camera.');
            surface.width = bitmap.width;
            surface.height = bitmap.height;
            context.drawImage(bitmap, 0, 0);
            originalFrame.current = new Uint8Array(frames[frames.length - 1]);
            hasFrame.current = true;
            setPhase('live');
            setMessage('Aim, then tap the camera button.');
            resetWatchdog();
          } finally { bitmap.close(); }
        }
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      if (current()) {
        setPhase('off');
        setMessage('Camera paused. Tap to restart.');
      }
    } catch (error) {
      if (mounted.current && controller.current === request) {
        setPhase('error');
        setMessage(error instanceof Error && error.name === 'Error' ? error.message : 'Camera stopped. Try again.');
      }
    } finally {
      clearTimeout(watchdog);
      request.abort();
      if (controller.current === request) controller.current = null;
    }
  }, [stop]);

  useEffect(() => {
    mounted.current = true;
    back.current?.focus();
    const surface = canvas.current;
    const frame = requestAnimationFrame(() => void start());
    const hide = () => {
      if (document.hidden) {
        stop();
        setPhase('off');
        setMessage('Camera paused. Tap to restart.');
      }
    };
    document.addEventListener('visibilitychange', hide);
    return () => {
      mounted.current = false;
      cancelAnimationFrame(frame);
      document.removeEventListener('visibilitychange', hide);
      stop();
      originalFrame.current = null;
      if (surface) surface.width = 0;
    };
  }, [start, stop]);

  const takePhoto = () => {
    if (!hasFrame.current || phase !== 'live') return;
    stop();
    setPhase('photo');
    setMessage('Photo taken');
  };

  const attach = () => {
    const source = canvas.current;
    if (!source || phase !== 'photo' || !photoReady || !onAttach) return;
    const output = document.createElement('canvas');
    const scale = Math.min(1, 256 / source.width, 192 / source.height);
    output.width = Math.max(1, Math.floor(source.width * scale));
    output.height = Math.max(1, Math.floor(source.height * scale));
    const context = output.getContext('2d');
    if (!context) { setPhase('error'); setMessage('Could not attach photo. Try again.'); return; }
    context.drawImage(source, 0, 0, output.width, output.height);
    const data = output.toDataURL('image/jpeg', .8).split(',')[1];
    output.width = 0;
    if (!data || data.length > 699052) { setPhase('error'); setMessage('Photo too large. Please retake.'); return; }
    const original = originalFrame.current;
    if (!original || original.length > 2 * 1024 * 1024) { setPhase('error'); setMessage('Photo unavailable. Please retake.'); return; }
    let binary = '';
    for (let offset = 0; offset < original.length; offset += 8192) binary += String.fromCharCode(...original.subarray(offset, offset + 8192));
    const preview = btoa(binary);
    if (!preview || preview.length > 2796204) { setPhase('error'); setMessage('Preview too large. Please retake.'); return; }
    onAttach({ image: data, preview });
  };

  return <section className="device-camera" aria-label="Camera" onKeyDown={(event) => { if (event.key === 'Escape') close(); }}>
    <header><button ref={back} onClick={close} aria-label="Back, discard photo and turn camera off"><BackIcon /><span>Back</span></button><h1>{phase === 'photo' ? 'Photo taken' : 'Camera'}</h1><span className="device-camera-state" role="status">{phase === 'live' ? '● Live' : phase === 'photo' ? '✓ Photo' : phase === 'starting' ? 'Opening…' : 'Off'}</span></header>
    <div className="device-camera-stage">
      <canvas ref={canvas} hidden={phase !== 'live' && phase !== 'photo'} aria-label={phase === 'photo' ? 'Photo taken, not saved' : 'Live camera view'} />
      {phase !== 'live' && phase !== 'photo' && <div className="device-camera-message"><CameraIcon /><p role="status">{message}</p></div>}
    </div>
    {phase === 'photo' && !photoReady && <p id="photo-answer-unavailable" role="status">Photo answers aren’t ready on this device yet.</p>}
    <div className="device-camera-tools"><button className={phase === 'photo' ? 'device-retake' : 'device-shutter'} disabled={phase === 'starting'} onClick={phase === 'live' ? takePhoto : () => void start()}>{phase === 'live' || phase === 'starting' ? <CameraIcon /> : <RetakeIcon />}<span>{phase === 'live' ? 'Take photo' : phase === 'photo' ? 'Retake' : phase === 'starting' ? 'Wait…' : 'Try again'}</span></button>{phase === 'photo' && <button className="device-shutter" disabled={!photoReady || !onAttach} aria-describedby={!photoReady ? 'photo-answer-unavailable' : undefined} onClick={attach} aria-label="Ask about photo: start microphone">Ask about photo</button>}{onOpenGallery && <button className="device-camera-gallery" onClick={onOpenGallery} aria-label="Open Gallery"><GalleryIcon /><span>Gallery</span></button>}</div>
  </section>;
}
