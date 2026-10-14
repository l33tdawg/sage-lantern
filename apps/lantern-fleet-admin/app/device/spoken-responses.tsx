'use client';

import { useEffect, useRef, useState } from 'react';
import { loadSpokenResponses, saveSpokenResponses, type SpokenResponses } from '../../lib/spoken-responses';

/**
 * The Settings control for reading answers aloud.
 *
 * Off until the person at the device turns it on. The wording says what actually
 * happens rather than implying a capability: the answer is always shown on screen,
 * and this only adds sound. A device whose speaker or voice is not configured will
 * accept the preference and still be silent, so the note says that too instead of
 * promising audio the unit may not be able to produce.
 */
export default function SpokenResponsesPanel() {
  const [settings, setSettings] = useState<SpokenResponses | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const operation = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    let disposed = false;
    void loadSpokenResponses().then((value) => {
      if (!disposed) setSettings(value);
    }).catch(() => {
      if (!disposed) setError('Spoken responses need a device update or a new unlocked session.');
    });
    return () => { disposed = true; mounted.current = false; };
  }, []);

  async function toggle() {
    if (!settings || operation.current) return;
    operation.current = true;
    setBusy(true); setError(''); setSaved(false);
    try {
      const value = await saveSpokenResponses(settings, !settings.spoken_responses);
      if (mounted.current) { setSettings(value); setSaved(true); }
    } catch {
      // A refused write may be a stale revision, so drop the value and make the
      // screen reload rather than leaving a switch that shows a state the device
      // never accepted.
      if (mounted.current) { setSettings(null); setError('Could not confirm the change. Reopen Settings before trying again.'); }
    } finally {
      operation.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  const enabled = settings?.spoken_responses === true;

  return <section className="device-panel" aria-label="Spoken responses">
    <h2>Read answers aloud</h2>
    <p className="device-note" id="spoken-responses-help">
      Answers always appear on screen. Turn this on to also hear them read out on this Lantern.
      It stays off until you turn it on, and it needs a speaker set up on the device.
    </p>
    {error && <p role="alert">{error}</p>}
    {settings ? <div className="device-history-setting">
      <h2>Read answers aloud</h2>
      <button className="device-setting-switch" role="switch" aria-label="Read answers aloud"
        aria-describedby="spoken-responses-help" aria-checked={enabled} disabled={busy}
        onClick={() => void toggle()}>
        <span aria-hidden="true" className="device-switch-track"><span /></span>
        {enabled ? 'On' : 'Off'}
      </button>
      {saved && <p className="device-note">Saved.</p>}
    </div> : !error && <p className="device-note">Checking this device…</p>}
  </section>;
}
