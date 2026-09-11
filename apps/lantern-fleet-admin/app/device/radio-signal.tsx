'use client';
import { useEffect, useState } from 'react';
import { getDeviceToken, subscribeDeviceSession } from '../../lib/device-session';
import { requestRadioStatus } from '../../lib/radio-request';

import { getRadioSample } from '../../lib/radio-sample';

const FRESH_WINDOW_MS = 120000;
const RECENT_WINDOW_MS = 900000;

function ageLabel(seconds: number) {
  return seconds < 90 ? `${seconds} seconds ago` : `${Math.floor(seconds / 60)} minutes ago`;
}

export default function RadioSignal({ paused }: { paused: boolean }) {
  const [sample, setSample] = useState(() => getRadioSample(getDeviceToken()));
  const [now, setNow] = useState(() => typeof performance === 'undefined' ? 0 : performance.now());
  useEffect(() => {
    let disposed = false;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout>;
    const clear = () => { controller?.abort(); setSample(null); };
    const poll = async () => {
      const token = getDeviceToken();
      if (!token) { clear(); return; }
      if (paused || document.hidden) return;
      controller = new AbortController();
      try {
        await requestRadioStatus(controller.signal);
        if (disposed || controller.signal.aborted || token !== getDeviceToken()) return;
        const current = performance.now();
        setSample(getRadioSample(token));
        setNow(current);
      } catch { }
      finally { if (!disposed) timer = setTimeout(poll, 5000); }
    };
    const visibility = () => { clearTimeout(timer); if (document.hidden) clear(); else void poll(); };
    const unsubscribe = subscribeDeviceSession(() => { clearTimeout(timer); clear(); void poll(); });
    void poll();
    const clock = setInterval(() => { setSample(getRadioSample(getDeviceToken())); setNow(performance.now()); }, 1000);
    document.addEventListener('visibilitychange', visibility);
    return () => { disposed = true; clearTimeout(timer); clearInterval(clock); controller?.abort(); unsubscribe(); document.removeEventListener('visibilitychange', visibility); };
  }, [paused]);
  // Polling pauses while the device is recording or answering; hold the last
  // verified sample then instead of blanking the meter mid-interaction.
  const current = sample?.token === getDeviceToken() && sample && (paused || now - sample.checkedAt < 30000) ? sample : null;
  const measured = current && current.heardAt > 0 && current.snr !== null && now - current.heardAt < RECENT_WINDOW_MS
    ? { bars: current.bars, snr: current.snr, rssi: current.rssi, heardAt: current.heardAt } : null;
  const fresh = !!measured && now - measured.heardAt < FRESH_WINDOW_MS;
  const bars = measured ? measured.bars : 0;
  const label = !current ? 'Checking radio' : !current.connected ? 'Radio unavailable' : measured ? `Radio link ${measured.snr} dB SNR${measured.rssi !== null && measured.rssi < 0 ? ` at ${measured.rssi} dBm` : ''} measured ${ageLabel(Math.floor((now - measured.heardAt) / 1000))}` : 'Radio connected; no recent packet signal measured';
  return <svg viewBox="0 0 32 28" width="32" height="28" role="img" aria-label={label}><title>{label}</title>{[0, 1, 2, 3].map((index) => <rect key={index} x={3 + index * 7} y={20 - index * 5} width="4" height={5 + index * 5} rx="1" fill={index < bars ? '#a9e8c6' : 'none'} stroke={index < bars ? '#a9e8c6' : '#8294a8'} opacity={!measured ? 0.6 : fresh ? 1 : 0.55} />)}{!measured && <line x1="2" y1="25" x2="26" y2="7" stroke="#8294a8" strokeWidth="2" strokeLinecap="round" />}<circle cx="28" cy="3" r="2" fill={current?.connected ? '#a9e8c6' : '#d1ae65'} /></svg>;
}
