'use client';
import { useEffect, useRef, useState } from 'react';
import type { DeviceStatus } from '../../lib/device-state';
import { appendTemperature, storagePercent, temperatureSegments, type TemperatureSample } from '../../lib/device-metrics';
import { parseSageStatus, sageStatusIsFresh } from '../../lib/sage-status';
import { BackIcon, InfoIcon } from './icons';
import RadioObservations from './radio-observations';

export default function StatusScreen({ status, connected, close }: { status: DeviceStatus | null; connected: boolean; close: () => void }) {
  const back = useRef<HTMLButtonElement>(null);
  const [tab, setTab] = useState('Device');
  const [samples, setSamples] = useState<TemperatureSample[]>([]);
  useEffect(() => { back.current?.focus(); }, []);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setSamples((previous) => appendTemperature(previous, connected && status ? Date.parse(status.observed_at) : Date.now(), connected ? status?.temperature_millicelsius ?? null : null)));
    return () => cancelAnimationFrame(frame);
  }, [status, connected]);
  const state = (name: string) => status?.services[name] === 'active' ? 'Running' : status?.services[name] || 'Unknown';
  const used = storagePercent(status?.storage_free_bytes, status?.storage_total_bytes);
  const sage = parseSageStatus(status?.sage);
  const sageFresh = connected && sageStatusIsFresh(sage);
  const count = (value?: number | null) => sageFresh && value != null ? value.toLocaleString() : '—';
  return <section className="device-subscreen" aria-label="Device status" onKeyDown={(event) => { if (event.key === 'Escape') close(); }}>
    <header className="device-subheader"><button ref={back} onClick={close}><BackIcon />Back</button><div className="device-page-title"><InfoIcon /><h1>Device status</h1></div></header>
    <nav className="device-status-tabs" aria-label="Status category">{['Device', 'SAGE', 'LoRa', 'Wireless'].map((name) => <button key={name} aria-pressed={tab === name} onClick={() => setTab(name)}>{name}</button>)}</nav>
    <div className="device-subcontent" tabIndex={0} key={tab}>
      {!connected || !status ? <p role="status">Waiting for fresh measurements…</p> : tab === 'Device' ? <>
        <div className="device-chart-grid"><section className="device-stat-tile"><h2>Temperature</h2><strong>{status.temperature_millicelsius != null ? `${(status.temperature_millicelsius / 1000).toFixed(1)} °C` : 'Unavailable'}</strong>
          <svg viewBox="0 0 300 100" role="img" aria-label="Temperature trend while this screen is open, scale zero to 100 degrees Celsius"><path d="M10 10v80h280" fill="none" stroke="#557080" />{temperatureSegments(samples).map((points, index) => <polyline key={index} points={points} fill="none" stroke="#ffbe62" strokeWidth="3" />)}</svg><small>{samples.filter((sample) => sample.celsius != null).length < 2 ? 'Collecting samples…' : 'Recent samples · 0–100 °C'}</small>
        </section><section className="device-stat-tile"><h2>Storage</h2><strong>{used != null ? `${used.toFixed(0)}% used` : 'Unavailable'}</strong>{used != null && <progress max={100} value={used} aria-label="Storage used" />}<small>{status.storage_free_bytes != null ? `${(status.storage_free_bytes / 1073741824).toFixed(1)} GiB free` : 'No measurement'}</small></section></div>
        <dl className="device-info-table"><dt>Battery</dt><dd>No telemetry</dd><dt>Uptime</dt><dd>{status.uptime_seconds != null ? `${Math.floor(status.uptime_seconds / 60)} minutes` : 'Unknown'}</dd></dl>
      </> : tab === 'SAGE' ? <>
        <div className="device-chart-grid"><section className="device-stat-tile"><h2>Committed memories</h2><strong>{count(sage?.committed_memories)}</strong><small>Local node projection</small></section><section className="device-stat-tile"><h2>Block height</h2><strong>{count(sage?.block_height)}</strong><small>{sageFresh && sage?.block_height != null ? sage.catching_up ? 'Catching up' : sage.stuck ? 'Needs attention' : sage.idle ? 'Idle · no new transactions' : 'Local chain' : 'Telemetry unavailable'}</small></section></div>
        {!sageFresh && <p role="status">Waiting for fresh SAGE telemetry. Missing values are not zero.</p>}
        <dl className="device-info-table"><dt>Total memories</dt><dd>{count(sage?.total_memories)}</dd><dt>Chain peers</dt><dd>{count(sage?.chain_peers)}</dd><dt>Validator roster</dt><dd>{count(sage?.validator_count)}</dd><dt>Validator connections</dt><dd>{count(sage?.validator_connections)}</dd><dt>Pending transactions</dt><dd>{count(sage?.mempool_txs)}</dd><dt>Federated transport</dt><dd>{sageFresh && sage?.federation_enabled != null ? sage.federation_enabled ? 'Enabled' : 'Disabled' : 'Not verified'}</dd><dt>User inbox / outbox</dt><dd>Not configured</dd><dt>Public sharing</dt><dd>Not enabled</dd></dl>
        <p>The validator roster is observed membership, not verified connections or a guaranteed complete list. Chain peers and LoRa neighbors are separate. Messages are not automatically committed memories.</p>
        <h2>Local services</h2><dl className="device-info-table"><dt>SAGE</dt><dd>{state('personal_sage')}</dd><dt>Answer model</dt><dd>{state('llm')}</dd><dt>Voice</dt><dd>{state('voice_loop')}</dd><dt>Readiness check</dt><dd>{state('runtime_gate')}</dd></dl>
      </> : tab === 'LoRa' ? <>
        <RadioObservations />
      </>
          : <><h2>Wi-Fi</h2><dl className="device-info-table"><dt>Reported device connection</dt><dd>{status.network?.detail || 'Unavailable'}</dd><dt>Wi-Fi signal</dt><dd>Not measured</dd><dt>Internet access</dt><dd>Not tested</dd></dl><section className="device-panel"><h2>Bluetooth audio</h2><p>Headphone pairing and spoken answers are not available in Lantern yet.</p></section></>}
    </div>
  </section>;
}
