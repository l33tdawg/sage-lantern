'use client';

import { useEffect, useRef, useState } from 'react';
import { networkSetupRequest, parseSetupNetworks, parseSetupStatus, type SetupNetwork, type SetupStatus } from '../../../lib/network-setup';
import './setup.css';

export default function NetworkSetup() {
  const capability = useRef('');
  const request = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const codeInput = useRef<HTMLInputElement>(null);
  const passwordInput = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [networks, setNetworks] = useState<SetupNetwork[]>([]);
  const [selected, setSelected] = useState<SetupNetwork | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [finished, setFinished] = useState(false);
  useEffect(() => () => { request.current?.abort(); capability.current = ''; }, []);

  async function run(operation: (signal: AbortSignal) => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError('');
    try { await operation(controller.signal); }
    catch { if (!controller.signal.aborted) setError('Could not confirm setup. Check the connection and setup code, then retry.'); }
    finally { inFlight.current = false; if (!controller.signal.aborted) setBusy(false); }
  }

  const start = () => {
    const token = codeInput.current?.value.trim() ?? '';
    if (codeInput.current) codeInput.current.value = '';
    void run(async (signal) => {
      capability.current = '';
      const value = parseSetupStatus(await networkSetupRequest('status', token, {}, signal));
      if (signal.aborted) return;
      capability.current = token;
      setStatus(value);
    });
  };
  const scan = () => void run(async (signal) => {
    const value = parseSetupNetworks(await networkSetupRequest('scan', capability.current, {}, signal));
    if (!signal.aborted) { setNetworks(value); setSelected(null); }
  });
  const connect = () => {
    if (!selected) return;
    const network = selected;
    const password = passwordInput.current?.value ?? '';
    if (passwordInput.current) passwordInput.current.value = '';
    void run(async (signal) => {
      await networkSetupRequest('connect', capability.current, { ssid: network.ssid, password, security: network.security }, signal);
      const value = parseSetupStatus(await networkSetupRequest('status', capability.current, {}, signal));
      if (!value.connected || value.ssid !== network.ssid) throw new Error('Connection unconfirmed');
      if (!signal.aborted) { setStatus(value); setSelected(null); }
    });
  };
  const finish = () => void run(async (signal) => {
    const value = await networkSetupRequest('finish', capability.current, {}, signal);
    if (!value || typeof value !== 'object' || !('finished' in value) || value.finished !== true) throw new Error('Setup closure unconfirmed');
    if (!signal.aborted) { capability.current = ''; setNetworks([]); setStatus(null); setFinished(true); }
  });

  return <main className="lantern-setup">
    <header><span className="setup-brand">SAGE Lantern</span><p>First-time setup</p></header>
    <h1>{finished ? 'Network setup closed' : 'Connect your Lantern'}</h1>
    <p className="setup-intro">Wi-Fi here. Your private passcode and name stay on the device.</p>
    {finished ? <section className="setup-card"><h2>Continue on your Lantern</h2><p>When the secure device setup is ready, use its screen to create your passcode, save your recovery key and choose a name.</p><p>Closing this network session does not certify the rest of the installation.</p></section>
      : !status ? <form className="setup-card" onSubmit={(event) => { event.preventDefault(); start(); }}>
        <h2>Open a secure setup session</h2><p>Use an SSH tunnel and open this page at <code>http://127.0.0.1:3100/admin/setup</code>. Keep Ethernet connected while setting up Wi-Fi.</p>
        <label htmlFor="setup-code">Temporary setup code</label><input id="setup-code" ref={codeInput} type="password" autoComplete="off" spellCheck={false} required minLength={43} maxLength={43} disabled={busy} />
        <p className="setup-note">Paste the code shown by the local setup tool. It expires after 15 minutes; it is not your device passcode.</p>
        <button disabled={busy}>{busy ? 'Checking…' : 'Continue'}</button>
      </form> : <section className="setup-card">
        <div className="setup-connection"><div><h2>{status.unit_id}</h2><p>{status.connected ? `Connected to ${status.ssid}` : 'Wi-Fi not connected'}</p></div><span>{status.interface}</span></div>
        <p className="setup-note">A Wi-Fi connection does not necessarily provide internet access. Lantern is designed to work offline.</p>
        <button disabled={busy} onClick={scan}>{busy ? 'Working…' : 'Find Wi-Fi networks'}</button>
        <ul className="setup-networks">{networks.map((network, index) => <li key={`${network.ssid}-${network.security}-${index}`}><button className="setup-network" disabled={busy || network.security === 'unsupported'} onClick={() => setSelected(network)} aria-pressed={selected === network}><span>{network.ssid}<small>{network.security === 'unsupported' ? 'Not supported by this setup tool' : network.security === 'open' ? 'Open · no password' : 'Password protected'}</small></span><span>{network.signal}%</span></button></li>)}</ul>
        {selected && <form onSubmit={(event) => { event.preventDefault(); connect(); }}><h3>Connect to {selected.ssid}</h3>{selected.security === 'open' ? <p>This network is not encrypted. Only join if you trust it.</p> : <><label htmlFor="wifi-password">Wi-Fi password</label><input key={selected.ssid} id="wifi-password" ref={passwordInput} type="password" autoComplete="new-password" required minLength={8} maxLength={64} disabled={busy} /></>}<button disabled={busy}>Connect</button></form>}
        <button className="setup-secondary" disabled={busy} onClick={finish}>Finish network setup</button>
      </section>}
    {error && <p className="setup-error" role="alert">{error}</p>}
    <p className="setup-note">This session only configures Wi-Fi. It cannot read your questions, photos or recovery key.</p>
  </main>;
}
