'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { parseDeviceStatus, statusIsFresh } from '../lib/device-state';
import './admin/admin.css';

type ServiceState = 'active' | 'inactive' | 'failed' | 'unknown';
type PeripheralState = {
  present: boolean;
  detail: string;
};
type LocalStatus = {
  schema: 'sage.lantern.local-status.v1';
  observed_at: string;
  unit_id: string;
  hostname: string;
  uptime_seconds: number;
  temperature_millicelsius: number | null;
  storage_free_bytes: number;
  storage_total_bytes: number;
  provisioned: boolean;
  local_voice_ready: boolean;
  network: PeripheralState;
  display: PeripheralState;
  touch: PeripheralState;
  radio: PeripheralState & {
    name?: string;
    frequency_mhz?: number;
    firmware?: string;
  };
  audio: PeripheralState & { serial?: string; ptt_ready?: boolean; ready?: boolean; capture_verified?: boolean };
  ptt: PeripheralState & { state?: 'up' | 'down' | 'unknown'; event_count?: number; verified?: boolean };
  voice: { phase: string; session_id?: string; transcript: string; response: string; detail?: string; timings_ms?: Record<string, number> };
  services: Record<string, ServiceState>;
};

type View = 'Home' | 'Voice' | 'Radio' | 'System' | 'Setup';

const EMPTY_STATUS: LocalStatus = {
  schema: 'sage.lantern.local-status.v1',
  observed_at: '',
  unit_id: 'unit-a',
  hostname: 'rock-5b-plus',
  uptime_seconds: 0,
  temperature_millicelsius: null,
  storage_free_bytes: 0,
  storage_total_bytes: 0,
  provisioned: false,
  local_voice_ready: false,
  network: { present: false, detail: 'Checking…' },
  display: { present: true, detail: 'HDMI display' },
  touch: { present: true, detail: 'Waveshare touch' },
  radio: { present: false, detail: 'Checking…' },
  audio: { present: false, detail: 'Checking…' },
  ptt: { present: false, detail: 'Checking…', state: 'unknown', verified: false },
  voice: { phase: 'unavailable', transcript: '', response: '', detail: 'Starting local voice service' },
  services: {},
};

const NAV: { view: View; icon: string }[] = [
  { view: 'Home', icon: '⌂' },
  { view: 'Voice', icon: '◉' },
  { view: 'Radio', icon: '⌁' },
  { view: 'System', icon: '▤' },
  { view: 'Setup', icon: '◇' },
];

export default function LanternLocalConsole({ displayName }: { displayName: string }) {
  const [view, setView] = useState<View>('Home');
  const [status, setStatus] = useState<LocalStatus>(EMPTY_STATUS);
  const [connected, setConnected] = useState(false);
  const [lastError, setLastError] = useState('');

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const refresh = async () => {
      controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), 4000);
      try {
        const response = await fetch('http://127.0.0.1:3199/status', { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error(`status ${response.status}`);
        const next = await response.json() as LocalStatus;
        if (!statusIsFresh(parseDeviceStatus(next))) throw new Error('Stale device status');
        if (![next.network, next.display, next.touch, next.ptt].every((item) => item && typeof item.present === 'boolean' && typeof item.detail === 'string')
          || typeof next.hostname !== 'string' || typeof next.provisioned !== 'boolean'
          || ![next.uptime_seconds, next.storage_free_bytes, next.storage_total_bytes].every(Number.isFinite)
          || (next.temperature_millicelsius !== null && !Number.isFinite(next.temperature_millicelsius))) throw new Error('Invalid maintenance status');
        if (!cancelled) {
          setStatus(next);
          setConnected(true);
          setLastError('');
        }
      } catch (error) {
        if (!cancelled) {
          setConnected(false);
          setLastError(error instanceof Error ? error.message : 'status unavailable');
        }
      } finally {
        clearTimeout(deadline);
        if (!cancelled) timer = setTimeout(refresh, 1500);
      }
    };
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); controller?.abort(); };
  }, []);

  const activeCore = ['llm', 'personal_sage'].filter((name) => status.services[name] === 'active').length;
  const readyCount = [status.display.present, status.touch.present, status.radio.present, status.audio.present].filter(Boolean).length;

  return (
    <main className="lantern-console lantern-admin">
      <header className="lantern-header">
        <button className="lantern-brand" onClick={() => setView('Home')} aria-label="Lantern home">
          <span>S</span><strong>SAGE <small>Lantern maintenance · read-only</small></strong>
        </button>
        <div className="lantern-header-state">
          <i className={connected ? 'ok' : 'bad'} />
          <span>{connected ? 'Device status connected' : 'Status reconnecting'}</span>
          <time>{status.observed_at ? new Date(status.observed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—'}</time>
        </div>
      </header>

      <section className="lantern-body">
        {!connected ? <div className="lantern-view" role="status"><h1>Device status unavailable</h1><p>No current readiness claims are available. Check the authenticated SSH connection and both port forwards (3100 and 3199).</p><p>{lastError || 'Connecting…'}</p></div> : <>
        {view === 'Home' && <HomeView status={status} readyCount={readyCount} activeCore={activeCore} setView={setView} />}
        {view === 'Voice' && <VoiceView status={status} />}
        {view === 'Radio' && <RadioView status={status} />}
        {view === 'System' && <SystemView status={status} connected={connected} error={lastError} />}
        {view === 'Setup' && <SetupView status={status} />}
        </>}
      </section>

      <nav className="lantern-nav" aria-label="Lantern navigation">
        {NAV.map((item) => <button key={item.view} className={view === item.view ? 'active' : ''} onClick={() => setView(item.view)} aria-current={view === item.view ? 'page' : undefined}><span>{item.icon}</span>{item.view}</button>)}
      </nav>
      <span className="lantern-operator">Maintenance session · configured label: {displayName}</span>
    </main>
  );
}

function HomeView({ status, readyCount, activeCore, setView }: { status: LocalStatus; readyCount: number; activeCore: number; setView: (view: View) => void }) {
  const temperature = status.temperature_millicelsius === null ? '—' : `${Math.round(status.temperature_millicelsius / 1000)}°C`;
  return <div className="lantern-view">
    <div className="lantern-title"><div><small>DEVICE MAINTENANCE</small><h1>System overview</h1></div><span className={status.provisioned ? 'lantern-chip good' : 'lantern-chip warn'}>{status.provisioned ? 'Provisioned' : 'Bring-up mode'}</span></div>
    <section className="lantern-summary-grid">
      <button className="lantern-primary-card" onClick={() => setView('Voice')}>
        <span className="lantern-orb">◉</span><div><small>VOICE</small><h2>{status.audio.present ? 'Audio board detected' : 'Audio board missing'}</h2><p>{status.audio.detail}</p></div><b>→</b>
      </button>
      <div className="lantern-stat"><small>RADIO</small><strong>{status.radio.present ? 'USB detected' : 'Missing'}</strong><span>Link unverified</span></div>
      <div className="lantern-stat"><small>CORE</small><strong>{activeCore}/2 active</strong><span>{status.local_voice_ready ? 'Private local voice ready' : 'Local activation pending'}</span></div>
      <div className="lantern-stat"><small>HARDWARE</small><strong>{readyCount}/4 seen</strong><span>Display · touch · radio · audio</span></div>
      <div className="lantern-stat"><small>THERMAL</small><strong>{temperature}</strong><span>ROCK 5B+ package</span></div>
    </section>
  </div>;
}

function VoiceView({ status }: { status: LocalStatus }) {
  const bridgeReady = status.audio.present && status.audio.ready === true;
  const pttReady = bridgeReady && status.audio.ptt_ready === true && status.local_voice_ready;
  const phase = status.voice.phase;
  const listening = phase === 'listening';
  const processing = phase === 'processing';
  const headline = listening ? 'Listening…' : processing ? 'Thinking locally…' : phase === 'complete' ? 'Answer ready' : phase === 'error' ? 'Try again' : 'Hold the side button and ask';
  return <div className="lantern-view voice-view">
    <div className="lantern-title"><div><small>PRIVATE · ON DEVICE</small><h1>{headline}</h1></div><span className={pttReady ? 'lantern-chip good' : 'lantern-chip warn'}>{listening ? 'Recording' : processing ? 'Processing' : pttReady ? 'Ready' : 'Setup'}</span></div>
    <div className="voice-stage">
      <div className={`ptt-control ${listening ? 'listening' : ''}`} aria-label="Physical push to talk state"><span>◉</span><strong>{listening ? 'LISTENING' : processing ? 'THINKING' : pttReady ? 'HOLD SIDE BUTTON' : 'NOT READY'}</strong></div>
      <div><h2>{pttReady ? 'Ask naturally. Release when finished.' : bridgeReady ? 'Local runtime activation pending.' : 'Connect the audio board.'}</h2><p>Your speech is transcribed locally. The answer appears here; no synthesized voice is played.</p><code>{status.audio.serial ?? 'No audio-board serial'}</code></div>
    </div>
    <section className="conversation-card" aria-live="polite">
      <div><small>YOU SAID</small><p>{status.voice.transcript || (listening ? 'Listening to the microphone…' : processing ? 'Transcribing…' : 'Your transcript will appear here.')}</p></div>
      <div><small>SAGE LANTERN</small><p>{status.voice.response || (processing ? 'Searching private local context and composing an answer…' : status.voice.detail || 'Hold the side button to ask your first question.')}</p></div>
    </section>
    {phase === 'complete' && status.voice.timings_ms && <section className="system-grid" aria-label="Processing timings">{[
      ['stt', 'Speech recognition'], ['sage_context', 'SAGE recall'], ['llm_first_token', 'Model first text'],
      ['first_text', 'First text incl. speech recognition'], ['llm', 'Full model answer'], ['total', 'Total processing'],
    ].map(([key, label]) => {
      const value = status.voice.timings_ms?.[key];
      return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? <LiveRow key={key} label={label} value={`${(value / 1000).toFixed(2)} s`} /> : null;
    })}</section>}
  </div>;
}

function RadioView({ status }: { status: LocalStatus }) {
  return <div className="lantern-view">
    <div className="lantern-title"><div><small>LORA · MESHCORE</small><h1>Radio companion</h1></div><span className={status.radio.present ? 'lantern-chip good' : 'lantern-chip bad'}>{status.radio.present ? 'USB online' : 'Unavailable'}</span></div>
    <section className="radio-live-card">
      <div className="radio-rings"><i/><i/><span>A</span></div>
      <dl><dt>Name</dt><dd>{status.radio.name ?? '—'}</dd><dt>Frequency</dt><dd>{status.radio.frequency_mhz ? `${status.radio.frequency_mhz.toFixed(3)} MHz` : '—'}</dd><dt>Firmware</dt><dd>{status.radio.firmware ?? '—'}</dd><dt>Host route</dt><dd>{status.radio.detail}</dd><dt>Nearby nodes</dt><dd>Unit B unavailable</dd></dl>
    </section>
    <p className="lantern-note">LoRa carries bounded discovery and coordination messages. Larger verified bundles move over Wi-Fi, Ethernet, USB, or storage.</p>
  </div>;
}

function SystemView({ status, connected, error }: { status: LocalStatus; connected: boolean; error: string }) {
  const used = status.storage_total_bytes ? 100 - Math.round(status.storage_free_bytes / status.storage_total_bytes * 100) : 0;
  const uptimeHours = Math.floor(status.uptime_seconds / 3600);
  return <div className="lantern-view">
    <div className="lantern-title"><div><small>LIVE DEVICE STATE</small><h1>System</h1></div><span className={connected ? 'lantern-chip good' : 'lantern-chip bad'}>{connected ? 'Reporting' : 'Degraded'}</span></div>
    <section className="system-grid">
      <LiveRow label="Hostname" value={status.hostname}/><LiveRow label="Uptime" value={`${uptimeHours}h`}/><LiveRow label="Storage used" value={`${used}%`}/><LiveRow label="Network" value={status.network.detail}/><LiveRow label="Display" value={status.display.detail}/><LiveRow label="Touch" value={status.touch.detail}/><LiveRow label="PTT input" value={status.ptt.detail}/>
      {Object.entries(status.services).map(([name, state]) => <LiveRow key={name} label={name.replaceAll('_', ' ')} value={state} tone={state === 'active' ? 'good' : state === 'failed' ? 'bad' : 'warn'} />)}
    </section>
    {!connected && <p className="lantern-error">Live status unavailable: {error || 'reconnecting'}</p>}
  </div>;
}

function SetupView({ status }: { status: LocalStatus }) {
  const gates = [
    ['Base OS & local console', false, 'Release-specific installation and cold-boot evidence required'],
    ['Display & touch', status.display.present && status.touch.present, status.touch.detail],
    ['MeshCore radio', status.radio.present, status.radio.detail],
    ['USB audio available', status.audio.present && status.audio.ready === true, status.audio.detail],
    ['Physical PTT', status.audio.ptt_ready === true, status.audio.ptt_ready ? 'Press/release events verified' : status.ptt.present ? 'GPIO adapter online; connect and test the button' : 'Button wiring and GPIO event adapter pending'],
    ['Commons proposer enrollment', false, 'Authority workflow not yet available'],
    ['Local voice activation', status.local_voice_ready, status.local_voice_ready ? 'Private speech-to-screen loop enabled' : 'Pending local acceptance'],
    ['Commons activation', status.provisioned, status.provisioned ? 'Verified' : 'Public proposer enrollment still locked'],
  ] as const;
  return <div className="lantern-view">
    <div className="lantern-title"><div><small>RESUMABLE CHECKLIST</small><h1>Device setup</h1></div><span className="lantern-chip warn">Not fully provisioned</span></div>
    <p><Link href="/admin/setup">Set up Wi-Fi</Link> · Requires a temporary installer code and secure setup connection.</p>
    <section className="setup-list">{gates.map(([name, passed, note], index) => <div key={name}><span className={passed ? 'passed' : ''}>{passed ? '✓' : index + 1}</span><div><strong>{name}</strong><small>{note}</small></div><b>{passed ? 'Passed' : 'Pending'}</b></div>)}</section>
  </div>;
}

function LiveRow({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return <div className="live-row"><small>{label}</small><strong className={tone}>{value}</strong></div>;
}
