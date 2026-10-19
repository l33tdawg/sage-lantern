'use client';
import { useEffect, useState } from 'react';
import { getDeviceToken, subscribeDeviceSession } from '../../lib/device-session';
import { requestRadioStatus } from '../../lib/radio-request';
import { radioStatusIsFresh, type RadioStatus } from '../../lib/radio-status';
import { requestCustodyStatus } from '../../lib/custody-request';
import type { CustodyStatus } from '../../lib/custody-status';
import { NetworkIcon, SendIcon, InfoIcon } from './icons';

export default function RadioObservations() {
  const [snapshot, setSnapshot] = useState<{ value: RadioStatus; startedAt: number; token: string } | null>(null);
  const [custody, setCustody] = useState<{ value: CustodyStatus; token: string } | null>(null);
  const [now, setNow] = useState(0);
  useEffect(() => {
    let disposed = false;
    let active: AbortController | null = null;
    let nextRead = 0;
    let session = getDeviceToken();
    const clear = () => { active?.abort(); setSnapshot(null); };
    const tick = () => {
      const token = getDeviceToken();
      const current = performance.now();
      setNow(current);
      if (document.hidden || !token || token !== session) {
        clear(); session = token; return;
      }
      if (active || current < nextRead) return;
      const controller = new AbortController();
      active = controller;
      nextRead = current + 5000;
      void requestRadioStatus(controller.signal).then((result) => {
        if (!disposed && !controller.signal.aborted && !document.hidden && token === getDeviceToken()) {
          setSnapshot({ ...result, token }); setNow(performance.now());
        }
      }).catch(() => {}).finally(() => { if (active === controller) active = null; });
    };
    const visibility = () => { if (document.hidden) clear(); else tick(); };
    const unsubscribe = subscribeDeviceSession(() => { clear(); session = getDeviceToken(); });
    tick();
    const interval = setInterval(tick, 1000);
    document.addEventListener('visibilitychange', visibility);
    return () => { disposed = true; unsubscribe(); active?.abort(); clearInterval(interval); document.removeEventListener('visibilitychange', visibility); };
  }, []);
  useEffect(() => {
    let disposed = false;
    let active: AbortController | null = null;
    let nextRead = 0;
    let session = getDeviceToken();
    const clear = () => { active?.abort(); setCustody(null); };
    const tick = () => {
      const token = getDeviceToken();
      const current = performance.now();
      if (document.hidden || !token || token !== session) { clear(); session = token; return; }
      if (active || current < nextRead) return;
      const controller = new AbortController();
      active = controller;
      nextRead = current + 15000;
      void requestCustodyStatus(controller.signal).then((result) => {
        if (!disposed && !controller.signal.aborted && !document.hidden && token === getDeviceToken()) {
          setCustody({ value: result, token });
        }
      }).catch(() => {}).finally(() => { if (active === controller) active = null; });
    };
    const visibility = () => { if (document.hidden) clear(); else tick(); };
    const unsubscribe = subscribeDeviceSession(() => { clear(); session = getDeviceToken(); });
    tick();
    const interval = setInterval(tick, 1000);
    document.addEventListener('visibilitychange', visibility);
    return () => { disposed = true; unsubscribe(); active?.abort(); clearInterval(interval); document.removeEventListener('visibilitychange', visibility); };
  }, []);
  const radio = snapshot && snapshot.token === getDeviceToken() ? snapshot.value : null;
  const fresh = !!snapshot && radioStatusIsFresh(snapshot.startedAt, now);
  const age = snapshot ? Math.max(0, Math.floor((now - snapshot.startedAt) / 1000)) : 0;
  const updating = !!radio && age <= 15;
  const health = radio?.health;
  const store = custody && custody.token === getDeviceToken() ? custody.value : null;
  const nodes = health?.discovered_nodes.filter((node) => node.age_seconds + age < 300) ?? [];
  return <section className="radio-dashboard" aria-label="Radio dashboard">
    <div className="radio-dashboard-heading"><span className={`radio-poll-status ${updating ? 'radio-fresh' : 'radio-stale'}`}><span className={`radio-poll-dot${updating ? ' is-live' : ''}`} aria-hidden="true" />{!radio ? 'Connecting…' : updating ? 'Live' : 'Updates paused'}</span></div>
    <div className="radio-card-grid">
      <article className="radio-metric radio-metric-link"><NetworkIcon /><span>Radio</span><strong>{!radio ? 'Checking' : !fresh && age > 15 ? 'Last known' : radio.serial_identity_verified ? 'Connected' : 'Not ready'}</strong><small>{radio?.serial_identity_verified ? 'USB identity verified' : 'Waiting for hardware'}</small></article>
      <article className="radio-metric radio-metric-nearby"><NetworkIcon /><span>Radios heard</span><strong>{health ? nodes.length : '—'}</strong><small>Advertisements · last 5 min</small></article>
      <article className="radio-metric radio-metric-received"><span className="radio-receive-icon"><SendIcon /></span><span>Received</span><strong>{health?.packets_received.toLocaleString() ?? '—'}</strong><small>{health && health.packets_received > 0 ? `Last packet${health.last_rssi_dbm < 0 ? ` ${health.last_rssi_dbm} dBm` : ''} · ${health.last_snr_db} dB SNR` : 'Packets since radio boot'}</small></article>
      <article className="radio-metric radio-metric-sent"><SendIcon /><span>Sent</span><strong>{health?.packets_sent.toLocaleString() ?? '—'}</strong><small>{health ? `${health.tx_queue} queued` : 'Packets since radio boot'}</small></article>
      <article className="radio-metric radio-metric-link"><NetworkIcon /><span>Relay</span><strong>{radio?.repeater ? radio.repeater.enabled ? 'Repeater on' : 'Node only' : 'Unknown'}</strong><small>{radio?.repeater ? radio.repeater.enabled ? 'Forwards flood traffic for nearby radios' : 'Not forwarding other radios' : 'Waiting for firmware flags'}</small></article>
      <article className="radio-metric radio-metric-nearby"><span className="radio-receive-icon"><SendIcon /></span><span>Store &amp; forward</span><strong>{store ? store.enabled ? `${store.held} held` : 'Off' : '—'}</strong><small>{store ? store.enabled ? `${store.recipients} peer${store.recipients === 1 ? '' : 's'} · stored, not delivered` : 'Custody disabled on this unit' : 'Waiting for custody status'}</small></article>
    </div>
    <section className="radio-node-list" aria-label="Discovered radios">
      <h3>Discovered radios</h3>
      {!health ? <p>Waiting for measurements.</p> : nodes.length === 0 ? <p>No recent advertisements heard yet.</p> : nodes.map((node) => <details key={node.public_key}><summary><NetworkIcon /><span>{node.name || `Radio ${node.public_key.slice(0, 8)}`}</span><small>{Math.floor(node.age_seconds + age)}s ago</small></summary><p className="radio-node-key">{node.public_key}</p><p>May be reached through a relay. This radio identity is not a verified person.</p></details>)}
    </section>
    <details className="radio-details"><summary><InfoIcon />Technical details</summary>
      <dl className="device-info-table"><dt>Firmware</dt><dd>{radio?.firmware ?? 'Unknown'}</dd><dt>Repeater</dt><dd>{radio?.repeater ? radio.repeater.enabled ? 'Enabled' : 'Disabled' : 'Unknown'}</dd><dt>Message receiver</dt><dd>{radio ? radio.receiving ? 'Running' : 'Stopped' : 'Unknown'}</dd><dt>Radio uptime</dt><dd>{health ? `${Math.floor(health.uptime_seconds / 60)} min` : '—'}</dd><dt>Last packet SNR</dt><dd>{health && health.packets_received > 0 ? `${health.last_snr_db} dB` : '—'}</dd><dt>Noise floor</dt><dd>{health ? `${health.noise_floor_dbm} dBm` : '—'}</dd><dt>Sent flood / direct</dt><dd>{health ? `${health.sent_flood.toLocaleString()} / ${health.sent_direct.toLocaleString()}` : '—'}</dd><dt>Receive errors</dt><dd>{health?.receive_errors ?? '—'}</dd><dt>TX / RX airtime</dt><dd>{health ? `${health.tx_airtime_seconds}s / ${health.rx_airtime_seconds}s` : '—'}</dd><dt>Custody held</dt><dd>{store ? store.enabled ? `${store.held} · ${store.bytes} B` : 'Disabled' : '—'}</dd><dt>Oldest custody expiry</dt><dd>{store?.oldest_expires_at ? new Date(store.oldest_expires_at * 1000).toLocaleString() : '—'}</dd><dt>Trusted depositors</dt><dd>{store ? store.enabled ? store.depositors : 'Disabled' : '—'}</dd><dt>Error flags</dt><dd>{health?.error_flags ?? '—'}</dd></dl>
      <p>Counts do not prove message delivery. Heard radios may be relayed. Repeater on means this radio forwards flood traffic for others; it does not mean the band&apos;s duty-cycle rules are satisfied. Custody held means sealed messages are stored for an absent peer, not delivered.</p>
    </details>
  </section>;
}
