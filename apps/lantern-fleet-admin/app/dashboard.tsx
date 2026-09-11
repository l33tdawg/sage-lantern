'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  aiFeatures, auditEvents, clusters, compatibilityChecks, devices, evidenceBundles, federationLanes,
  operations, proposalStates, provisioningGates, radioNeighbors, releases, resolvedIncidents,
  updateCampaigns, type Device, type Tone,
} from './data';

type View = 'Overview' | 'Devices' | 'Clusters' | 'Provisioning' | 'Operations' | 'Updates' | 'Radio network' | 'Federation' | 'AI settings' | 'Commons sync' | 'Evidence' | 'Incidents' | 'Audit' | 'Authority';
type Role = 'Viewer' | 'Technician' | 'Fleet operator' | 'Release manager' | 'Auditor';
type SearchResult = { id: string; label: string; meta: string; icon: string; view: View; device?: Device };

const navigation: { group: string; items: { label: View; icon: string; badge?: string }[] }[] = [
  { group: 'Fleet', items: [
    { label: 'Overview', icon: '⌂' }, { label: 'Devices', icon: '▣' }, { label: 'Clusters', icon: '⌘' }, { label: 'Provisioning', icon: '◇', badge: '1' },
  ] },
  { group: 'Operations', items: [
    { label: 'Operations', icon: '↯', badge: '3' }, { label: 'Updates', icon: '⇧' }, { label: 'Radio network', icon: '⌁' }, { label: 'Federation', icon: '◎' }, { label: 'AI settings', icon: '✦' }, { label: 'Commons sync', icon: '↻' },
  ] },
  { group: 'Assurance', items: [
    { label: 'Evidence', icon: '▤' }, { label: 'Incidents', icon: '!' }, { label: 'Audit', icon: '≡' }, { label: 'Authority', icon: '⌾' },
  ] },
];

export default function Dashboard({ displayName }: { displayName: string }) {
  const [view, setView] = useState<View>('Overview');
  const [role, setRole] = useState<Role>('Viewer');
  const [presentation, setPresentation] = useState<'Foundation Fleet' | 'Field Operations'>('Foundation Fleet');
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedDevice, setSelectedDevice] = useState<Device | null>(null);
  const [pairingOpen, setPairingOpen] = useState(false);
  const [featureState, setFeatureState] = useState(() => Object.fromEntries(aiFeatures.map((feature) => [feature.id, feature.enabled])));
  const [toast, setToast] = useState('');
  const toastTimer = useRef<number | null>(null);

  const searchResults = useMemo(() => {
    const value = query.trim().toLowerCase();
    if (!value) return [];
    const views: SearchResult[] = navigation.flatMap((group) => group.items)
      .filter((item) => item.label.toLowerCase().includes(value))
      .map((item) => ({ id: `view-${item.label}`, label: item.label, meta: 'Console view', icon: item.icon, view: item.label }));
    const deviceResults: SearchResult[] = devices
      .filter((device) => `${device.id} ${device.label} ${device.site} ${device.model}`.toLowerCase().includes(value))
      .map((device) => ({ id: `device-${device.id}`, label: device.label, meta: `${device.site} · ${device.freshness}`, icon: '▣', view: 'Devices', device }));
    const evidenceResults: SearchResult[] = evidenceBundles
      .filter((bundle) => `${bundle.id} ${bundle.name} ${bundle.hash}`.toLowerCase().includes(value))
      .map((bundle) => ({ id: `evidence-${bundle.id}`, label: `${bundle.id} · ${bundle.name}`, meta: 'Sanitized evidence manifest', icon: '▤', view: 'Evidence' }));
    const auditResults: SearchResult[] = auditEvents
      .filter((event) => `${event.correlation} ${event.action} ${event.target}`.toLowerCase().includes(value))
      .map((event) => ({ id: `audit-${event.correlation}`, label: event.action, meta: event.correlation, icon: '≡', view: 'Audit' }));
    return [...views, ...deviceResults, ...evidenceResults, ...auditResults].slice(0, 8);
  }, [query]);

  const isAdmin = role === 'Fleet operator' || role === 'Release manager';
  const isOperator = role === 'Technician' || role === 'Fleet operator';
  const notify = (message: string) => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    setToast(message);
    toastTimer.current = window.setTimeout(() => setToast(''), 3500);
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearchOpen(true);
      }
      if (event.key === 'Escape') {
        setSearchOpen(false);
        setPairingOpen(false);
        setSelectedDevice(null);
        setQuery('');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    };
  }, []);

  useEffect(() => {
    document.body.style.overflow = searchOpen || pairingOpen || selectedDevice ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [searchOpen, pairingOpen, selectedDevice]);

  return (
    <main className="app-shell">
      <aside className="side-rail">
        <button className="brand" onClick={() => setView('Overview')}><span className="brand-mark">S</span><span><strong>SAGE</strong><small>Lantern Fleet</small></span></button>
        <nav aria-label="Primary navigation">
          {navigation.map((group) => <div className="nav-group" key={group.group}><p>{group.group}</p>{group.items.map((item) => (
            <button className={view === item.label ? 'nav-item active' : 'nav-item'} aria-current={view === item.label ? 'page' : undefined} onClick={() => setView(item.label)} key={item.label}>
              <span aria-hidden="true">{item.icon}</span>{item.label}{item.badge && <em aria-label={`${item.badge} items`}>{item.badge}</em>}
            </button>
          ))}</div>)}
        </nav>
        <button className="authority-card" onClick={() => setView('Authority')}><span className="eyebrow">Role preview only</span><strong>{role}</strong><p>This selector demonstrates affordances; it grants no authority.</p><span className="scope-status fixture"><i /> No server assignment</span></button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <button className="presentation-switcher" onClick={() => setPresentation(presentation === 'Foundation Fleet' ? 'Field Operations' : 'Foundation Fleet')}>{presentation} <span>⇄</span></button>
          <div className="topbar-actions">
            <button className="search" onClick={() => setSearchOpen(true)}>⌕ <span>Search devices, evidence…</span><kbd>⌘ K</kbd></button>
            <button className="icon-button" aria-label="Attention queue" onClick={() => setView('Overview')}>♢<b>1</b></button>
            <label className="role-picker"><span>Preview</span><select aria-label="Preview fleet role; does not grant authority" value={role} onChange={(event) => setRole(event.target.value as Role)}><option>Viewer</option><option>Technician</option><option>Fleet operator</option><option>Release manager</option><option>Auditor</option></select></label>
            <div className="user-button"><span>{initials(displayName)}</span><i>{displayName}<br/><small>Signed in · {role} preview</small></i></div>
          </div>
        </header>
        <div className="context-strip"><span><i className="fixture-dot" /> Interface available</span><span>Scope preview <strong>{presentation === 'Field Operations' ? 'Assigned cluster · MY bench' : 'Malaysia bench fleet'}</strong></span><span>Catalogue fixture <strong>11.19.10</strong></span><span>Fleet authority <strong>Not connected</strong></span><span className="boundary-copy">No Root or validator authority</span></div>
        <div className="fixture-banner" role="note"><strong>Fixture workspace</strong><span>Sanitized Unit A records · no live device, command authority, or fleet synchronization</span><a href="/live-enrollment">Open live enrollment</a><button onClick={() => setView('Authority')}>Review boundaries</button></div>

        <div className="content">
          <ViewContent view={view} role={role} isAdmin={isAdmin} isOperator={isOperator} presentation={presentation} featureState={featureState} setFeatureState={setFeatureState} setView={setView} setSelectedDevice={setSelectedDevice} setPairingOpen={setPairingOpen} notify={notify} />
        </div>
      </section>

      {searchOpen && <SearchDialog query={query} setQuery={setQuery} results={searchResults} close={() => { setSearchOpen(false); setQuery(''); }} select={(result) => { setView(result.view); if (result.device) setSelectedDevice(result.device); setSearchOpen(false); setQuery(''); }} />}
      {selectedDevice && <DeviceDrawer device={selectedDevice} close={() => setSelectedDevice(null)} setView={setView} />}
      {pairingOpen && <PairingDialog close={() => setPairingOpen(false)} notify={notify} isOperator={isOperator} />}
      {toast && <div className="toast" role="status">✓ {toast}</div>}
    </main>
  );
}

function ViewContent(props: {
  view: View; role: Role; isAdmin: boolean; isOperator: boolean; presentation: string;
  featureState: Record<string, boolean>; setFeatureState: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  setView: (view: View) => void; setSelectedDevice: (device: Device | null) => void; setPairingOpen: (open: boolean) => void; notify: (message: string) => void;
}) {
  switch (props.view) {
    case 'Devices': return <DevicesView {...props} />;
    case 'Clusters': return <ClustersView setView={props.setView} />;
    case 'Provisioning': return <ProvisioningView isOperator={props.isOperator} />;
    case 'Operations': return <OperationsView role={props.role} />;
    case 'Updates': return <UpdatesView isAdmin={props.isAdmin} notify={props.notify} />;
    case 'Radio network': return <RadioView isOperator={props.isOperator} notify={props.notify} setView={props.setView} />;
    case 'Federation': return <FederationView isAdmin={props.isAdmin} setView={props.setView} notify={props.notify} />;
    case 'AI settings': return <AISettingsView {...props} />;
    case 'Commons sync': return <CommonsView />;
    case 'Evidence': return <EvidenceView notify={props.notify} />;
    case 'Incidents': return <IncidentsView notify={props.notify} setView={props.setView} />;
    case 'Audit': return <AuditView setView={props.setView} />;
    case 'Authority': return <AuthorityView role={props.role} />;
    default: return <Overview {...props} />;
  }
}

function PageHeading({ eyebrow, title, copy, action }: { eyebrow: string; title: string; copy: string; action?: React.ReactNode }) {
  return <div className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{copy}</p></div>{action}</div>;
}

function Overview({ isOperator, presentation, setView, setSelectedDevice, setPairingOpen }: { isOperator: boolean; presentation: string; setView: (view: View) => void; setSelectedDevice: (device: Device) => void; setPairingOpen: (open: boolean) => void }) {
  return <>
    <PageHeading eyebrow={`${presentation} · fixture`} title={presentation === 'Field Operations' ? 'Field workflow preview.' : 'Fleet readiness workspace.'} copy="One fixture Lantern needs attention before activation; no live authority is connected." action={<button className="primary-button" onClick={() => setPairingOpen(true)} disabled={!isOperator}>＋ Review enrollment</button>} />
    <section className="metric-grid" aria-label="Fixture fleet summary"><Metric icon="!" label="Needs attention" value="1" note="Fixture hardware gate" tone="amber"/><Metric icon="◉" label="Fixture records" value="1" note="Last known · 30 Aug" tone="teal"/><Metric icon="⌁" label="Detected radios" value="0" note="No real observations" tone="green"/><Metric icon="▤" label="Evidence manifests" value="3" note="Sanitized references" tone="navy"/></section>
    <section className="dashboard-grid">
      <article className="panel attention-panel"><PanelHeading eyebrow="Priority queue" title="Attention" action="View all" onAction={() => setView('Provisioning')} /><div className="attention-item"><div className="lantern-avatar">A<span /></div><div className="attention-copy"><div className="item-title"><strong>Lantern A</strong><Status tone="warn">Hardware bring-up</Status></div><h3>Install and validate the display</h3><p>The screen and touch interface must pass before radio identity and final provisioning can continue.</p><Freshness label="Last known" /></div><button className="secondary-button" onClick={() => setView('Provisioning')}>Open checklist →</button></div></article>
      <article className="panel gate-panel"><div className="panel-heading"><div><span className="eyebrow">Lantern A</span><h2>Provisioning gates</h2></div><span className="progress-label">1 of 5 passed</span></div><div className="progress-track"><span /></div><GateList compact /></article>
      <article className="panel fleet-panel"><PanelHeading eyebrow="Fixture inventory" title="Fleet" action="Devices →" onAction={() => setView('Devices')} /><DeviceRow device={devices[0]} open={() => setSelectedDevice(devices[0])} /></article>
      <article className="panel boundary-panel"><span className="boundary-icon">⌾</span><div><span className="eyebrow">Authority boundary</span><h2>Private by design</h2><p>Fleet Admin sees device health, public fingerprints, and sanitized evidence. Personal memories, Root credentials, proposer keys, and validator governance stay outside this console.</p></div><button onClick={() => setView('Authority')}>Review authority map →</button></article>
    </section>
  </>;
}

function DevicesView({ isOperator, setSelectedDevice, setPairingOpen }: { isOperator: boolean; setSelectedDevice: (device: Device) => void; setPairingOpen: (open: boolean) => void }) {
  const [filter, setFilter] = useState('All');
  const visible = filter === 'All' || filter === 'Needs attention' || filter === 'Drifted' ? devices : [];
  const filters = [['All', 1], ['Needs attention', 1], ['Offline', 0], ['Drifted', 1], ['Update-ready', 0]] as const;
  return <><PageHeading eyebrow="Fixture fleet inventory" title="Lanterns" copy="Last-known state, approved profile, and explicit source for every device record." action={<button className="primary-button" disabled={!isOperator} onClick={() => setPairingOpen(true)}>＋ Review enrollment</button>} />
    <div className="filter-bar" aria-label="Filter devices">{filters.map(([label, count]) => <button key={label} className={filter === label ? 'filter active' : 'filter'} aria-pressed={filter === label} onClick={() => setFilter(label)}>{label} · {count}</button>)}</div>
    <article className="panel data-panel"><div className="table-head device-table"><span>Device</span><span>Lifecycle</span><span>Personal SAGE</span><span>Radio</span><span>Software</span><span>Observed</span><span /></div>{visible.map((device) => <DeviceRow device={device} open={() => setSelectedDevice(device)} key={device.id} />)}{visible.length === 0 && <div className="table-empty"><strong>No fixture devices match this filter</strong><span>Live results will appear only after the separate fleet authority accepts signed observations.</span></div>}</article>
    <BoundaryNote>Device inventory contains public fingerprints and bounded telemetry only. It never stores private keys, prompts, transcripts, personal memories, or recovery archives.</BoundaryNote>
  </>;
}

function DeviceRow({ device, open }: { device: Device; open: () => void }) {
  return <button className="device-row" onClick={open}><div className="lantern-avatar compact">A<span /></div><div><strong>{device.label}</strong><small>{device.site} · {device.model}</small></div><div><Status tone={device.lifecycleTone}>{device.lifecycle}</Status><small>Activation locked</small></div><div><strong>{device.personalHealth.split(' · ')[0]}</strong><small>{device.personalHealth.split(' · ')[1]}</small></div><div><strong>{device.radio}</strong><small>Radio identity pending</small></div><div><strong>{device.sageVersion}</strong><small>{device.drift}</small></div><div><span className="freshness-current">{device.freshness}</span><small>{device.observedAt}</small></div><span className="row-arrow">→</span></button>;
}

function ClustersView({ setView }: { setView: (view: View) => void }) {
  return <><PageHeading eyebrow="Assignment and scope" title="Clusters" copy="Regions and assigned field groups without crossing personal or Commons authority planes." />
    <div className="cluster-grid">{clusters.map((cluster) => <article className="panel cluster-card" key={cluster.name}><div className="cluster-map"><span>MY</span><i/><i/><i/></div><div><span className="eyebrow">Active cluster</span><h2>{cluster.name}</h2><p>{cluster.operator}</p><div className="cluster-stats"><span><strong>{cluster.devices}</strong> Lantern</span><span><strong>{cluster.attention}</strong> attention</span><span><strong>{cluster.lastSync}</strong> synced</span></div><button className="secondary-button" onClick={() => setView('Devices')}>Open cluster →</button></div></article>)}</div>
    <BoundaryNote>Field assignments are capability-scoped and time-bounded. A Torchbearer can work only with explicitly assigned clusters and approved actions.</BoundaryNote>
  </>;
}

function ProvisioningView({ isOperator }: { isOperator: boolean }) {
  const [guideOpen, setGuideOpen] = useState(false);
  const [showContract, setShowContract] = useState(false);
  return <><PageHeading eyebrow="Resumable safety workflow" title="Prepare Lantern A" copy="Every gate leaves evidence. Activation stays locked until all device-local prerequisites pass." action={<Status tone="warn">20% complete</Status>} />
    <div className="two-column"><article className="panel provisioning-card"><div className="provisioning-intro"><div className="lantern-avatar">A<span /></div><div><h2>Lantern A</h2><p>ROCK 5B+ · Malaysia bench · SAGE 11.19.10</p></div><Status tone="warn">Pre-provision</Status></div><div className="progress-track large"><span /></div><GateList /></article>
    <aside className="panel next-step"><span className="eyebrow">Next required step · fixture</span><h2>Install display & touch</h2><p>Connect video and touch using the exact display contract. Validate boot persistence, resolution, touch mapping, and thermals before continuing.</p><ul><li>Services remain disabled</li><li>PROVISIONED marker remains absent</li><li>No radio transmission is permitted yet</li></ul><button className="primary-button" disabled={!isOperator} onClick={() => setGuideOpen((open) => !open)}>{isOperator ? (guideOpen ? 'Close guided check' : 'Start guided check') : 'Technician preview required'}</button><button className="text-button" onClick={() => setShowContract((open) => !open)}>{showContract ? 'Hide wiring contract' : 'View wiring contract'}</button>{guideOpen && <div className="guided-check"><strong>Fixture checklist</strong><label><input type="checkbox" /> Display powered from approved rail</label><label><input type="checkbox" /> Video persists across warm reboot</label><label><input type="checkbox" /> Touch mapping matches orientation</label><label><input type="checkbox" /> Thermal result attached</label><small>Selections are temporary and create no provisioning receipt.</small></div>}{showContract && <div className="contract-note"><strong>Hardware contract reference</strong><span>Use the recorded display model, stable cable path, approved power budget, and no-radio-transmit gate. The canonical contract remains in the Lantern hardware documentation.</span></div>}</aside></div>
  </>;
}

function GateList({ compact = false }: { compact?: boolean }) {
  return <ol className={`gate-list ${compact ? 'compact' : ''}`}>{provisioningGates.map((gate, index) => <li key={gate.id}><span className={`gate-number ${gate.tone}`}>{gate.tone === 'good' ? '✓' : index + 1}</span><span><strong>{gate.label}</strong><small>{compact ? gate.note.split('.')[0] : gate.note}</small>{!compact && <em>{gate.evidence}</em>}</span><Status tone={gate.tone}>{gate.status}</Status></li>)}</ol>;
}

function OperationsView({ role }: { role: Role }) {
  const [selected, setSelected] = useState(operations[0]);
  return <><PageHeading eyebrow="Signed command lifecycle · fixture" title="Operations" copy="Desired state, approval, delivery, execution, and device receipt remain distinct stages." action={<Status tone="muted">Authority not deployed</Status>} />
    <div className="operations-layout"><article className="panel operation-list"><div className="panel-heading"><div><span className="eyebrow">Fixture queue</span><h2>Management operations</h2></div><span className="progress-label">{operations.length} records</span></div>{operations.map((operation) => <button key={operation.id} className={selected.id === operation.id ? 'operation-row selected' : 'operation-row'} aria-pressed={selected.id === operation.id} onClick={() => setSelected(operation)}><span><strong>{operation.action}</strong><small>{operation.id} · {operation.target}</small></span><Status tone={operation.tone}>{operation.state}</Status></button>)}</article>
    <aside className="panel operation-detail"><div className="item-title"><span className="eyebrow">{selected.id}</span><Status tone={selected.tone}>{selected.state}</Status></div><h2>{selected.action}</h2><p>{selected.reason}</p><dl><dt>Target</dt><dd>{selected.target}</dd><dt>Desired revision</dt><dd>{selected.revision}</dd><dt>Requested by</dt><dd>{selected.requestedBy}</dd><dt>Expiry</dt><dd>{selected.expires}</dd><dt>Device receipt</dt><dd>{selected.receipt}</dd><dt>Preview role</dt><dd>{role}</dd></dl><div className="operation-flow" aria-label="Operation lifecycle"><span className="complete">Draft</span><i>→</i><span className={selected.state === 'Succeeded' ? 'complete' : ''}>Approved</span><i>→</i><span>Delivered</span><i>→</i><span>Receipt</span></div><button className="primary-button" disabled>Authority deployment required</button></aside></div>
    <BoundaryNote>These are sanitized fixture records. The web interface cannot sign commands, allocate device sequences, or mark success without a verified device receipt.</BoundaryNote>
  </>;
}

function UpdatesView({ isAdmin, notify }: { isAdmin: boolean; notify: (message: string) => void }) {
  const [tab, setTab] = useState<'Release catalogue' | 'Campaigns' | 'Compatibility'>('Release catalogue');
  return <><PageHeading eyebrow="Signed release control · fixture" title="Updates" copy="Stage, canary, verify, and expand only after device-signed receipts and recovery prerequisites." action={<button className="primary-button" disabled={!isAdmin} onClick={() => notify('Campaign drafting is gated until the fleet authority and server role assignments are deployed')}>＋ Draft campaign</button>} />
    <div className="section-tabs" role="tablist" aria-label="Update workspace">{(['Release catalogue','Campaigns','Compatibility'] as const).map((label) => <button key={label} role="tab" aria-selected={tab === label} className={tab === label ? 'active' : ''} onClick={() => setTab(label)}>{label}</button>)}</div>
    {tab === 'Release catalogue' && <div className="release-grid">{releases.map((release) => <article className="panel release-card" key={release.version}><div><Status tone={release.status === 'Current' ? 'good' : 'warn'}>{release.status}</Status><span className="eyebrow">{release.channel}</span></div><h2>{release.version}</h2><dl><dt>SAGE</dt><dd>{release.sage}</dd><dt>Architecture</dt><dd>{release.arch}</dd><dt>Download</dt><dd>{release.size}</dd><dt>Compatibility</dt><dd>{release.compatibility}</dd><dt>Rollback partner</dt><dd>{release.rollback}</dd><dt>Manifest signature</dt><dd className="verified">✓ {release.signature}</dd></dl><button className="secondary-button" onClick={() => notify(`${release.version} fixture manifest: signature ${release.signature}; rollback partner ${release.rollback}`)}>View fixture manifest</button></article>)}</div>}
    {tab === 'Campaigns' && <div className="campaign-stack">{updateCampaigns.map((campaign) => <article className="panel campaign-card" key={campaign.id}><div><span className="eyebrow">{campaign.id}</span><h2>{campaign.name}</h2><p>{campaign.scope} · release {campaign.release}</p></div><div><small>Progress</small><strong>{campaign.progress}</strong></div><div><small>Next safe step</small><strong>{campaign.next}</strong></div><Status tone={campaign.tone}>{campaign.state}</Status></article>)}</div>}
    {tab === 'Compatibility' && <article className="panel compatibility-list">{compatibilityChecks.map((check) => <div key={check.check}><span><strong>{check.check}</strong><small>{check.detail}</small></span><Status tone={check.tone}>{check.result}</Status></div>)}</article>}
    <BoundaryNote>“Command delivered” is never success. A rollout completes only after a signed device receipt and post-restart health evidence.</BoundaryNote>
  </>;
}

function RadioView({ isOperator, notify, setView }: { isOperator: boolean; notify: (message: string) => void; setView: (view: View) => void }) {
  return <><PageHeading eyebrow="LoRa / MeshCore" title="Radio neighborhood" copy="Configured companions and nearby detected nodes, with honest last-heard and path information." action={<button className="secondary-button" disabled={!isOperator} onClick={() => notify('Scan is unavailable until the Heltec radio and antenna are installed')}>⌁ Scan neighborhood</button>} />
    <div className="radio-summary"><article className="panel radio-map"><div className="map-header"><div><span className="eyebrow">Topology</span><h2>Malaysia bench range</h2></div><Status tone="muted">Fixture mode</Status></div><div className="topology"><span className="range r3"/><span className="range r2"/><span className="range r1"/><div className="topo-node central"><b>A</b><small>Lantern A<br/>Radio pending</small></div><div className="topo-node neighbor"><b>?</b><small>Nearby sample<br/>-74 dBm · 1 hop</small></div></div><p className="map-note">Dashed node is representative fixture data. It is not a real detected radio.</p></article>
    <article className="panel radio-health"><span className="eyebrow">Local companion</span><h2>Heltec / MeshCore</h2><Status tone="warn">Not installed</Status><dl><dt>Serial path</dt><dd>Pending discovery</dd><dt>Radio identity</dt><dd>Generate on-device</dd><dt>Region</dt><dd>Must be confirmed</dd><dt>Antenna evidence</dt><dd>Required before TX</dd><dt>Host protocol</dt><dd>Not tested</dd></dl><button className="primary-button" disabled={!isOperator} onClick={() => setView('Provisioning')}>Open installation gate</button></article></div>
    <article className="panel data-panel"><div className="table-head radio-table"><span>Radio / node</span><span>Relationship</span><span>Status</span><span>Signal</span><span>Hops</span><span>Last heard</span><span>Region</span></div>{radioNeighbors.map((node) => <div className="table-row radio-table" key={node.id}><div><strong>{node.label}</strong><small>{node.route}</small></div><span>{node.kind}</span><Status tone={node.status === 'Not installed' ? 'warn' : 'muted'}>{node.status}</Status><span>{node.signal}</span><span>{node.hops}</span><span>{node.lastHeard}</span><span>{node.region}</span></div>)}</article>
    <BoundaryNote>Last-heard proves only a radio observation. It does not prove Lantern health, identity trust, Commons delivery, or publication.</BoundaryNote>
  </>;
}

function FederationView({ isAdmin, setView, notify }: { isAdmin: boolean; setView: (view: View) => void; notify: (message: string) => void }) {
  return <><PageHeading eyebrow="Distinct trust planes" title="Federation" copy="Local personal trust, fleet management, and cloud Commons connectivity remain separate and legible." />
    <div className="federation-stack">{federationLanes.map((lane, index) => <article className="panel federation-card" key={lane.name}><div className={`federation-symbol lane-${index}`}>{index === 0 ? 'L' : index === 1 ? 'C' : index === 2 ? 'F' : '☁'}</div><div><div className="item-title"><h2>{lane.name}</h2><Status tone={lane.tone}>{lane.connection}</Status></div><p>{lane.scope}</p><div className="federation-meta"><span><small>Trust / authority</small>{lane.trust}</span><span><small>Freshness</small>{lane.freshness}</span></div></div><button className="secondary-button" disabled={!isAdmin && index > 1} onClick={() => { if (index === 1) setView('Commons sync'); else notify(`${lane.name}: ${lane.connection}. This action remains in its separate authority plane.`); }}>{lane.management} →</button></article>)}</div>
    <BoundaryNote>Fleet Admin may explain federation and show bounded state. Personal federation changes hand off to local CEREBRUM; Commons governance remains in its separate governed surface.</BoundaryNote>
  </>;
}

function AISettingsView({ role, featureState, setFeatureState, notify }: { role: Role; featureState: Record<string, boolean>; setFeatureState: React.Dispatch<React.SetStateAction<Record<string, boolean>>>; notify: (message: string) => void }) {
  const canEdit = role === 'Fleet operator';
  return <><PageHeading eyebrow="Desired vs observed · fixture" title="AI experience" copy="Preview bounded desired-state changes without arbitrary commands or access to personal content." action={<Status tone="muted">Last verified · 30 Aug</Status>} />
    <div className="settings-layout"><section className="panel settings-panel"><div className="settings-header"><div><h2>Lantern A preference preview</h2><p>Toggles create an unsent draft only. There is no authority connection or device receipt.</p></div><span className="profile-chip">Balanced local · Qwen 3.5 4B</span></div>{aiFeatures.map((feature) => { const allowed = canEdit; return <div className="setting-row" key={feature.id}><div><div className="item-title"><strong>{feature.name}</strong><span className="setting-category">{feature.category}</span></div><p>{feature.description}</p><small>Last observed fixture: {feature.observed} · Desired draft: {featureState[feature.id] ? 'On' : 'Off'}</small></div><button className={`toggle ${featureState[feature.id] ? 'on' : ''}`} role="switch" aria-checked={featureState[feature.id]} disabled={!allowed} aria-label={`${feature.name} desired-state preview: ${featureState[feature.id] ? 'enabled' : 'disabled'}`} onClick={() => { setFeatureState((current) => ({ ...current, [feature.id]: !current[feature.id] })); notify(`${feature.name}: draft desired state changed · not submitted · no device receipt`); }}><span /></button></div>; })}</section>
    <aside className="panel resource-card"><span className="eyebrow">Last-known AI profile</span><h2>Balanced local</h2><div className="resource-ring"><span>48°C<small>Fixture · 30 Aug</small></span></div><dl><dt>Language model</dt><dd>Qwen 3.5 4B · Q4_K_M</dd><dt>Speech to text</dt><dd>Whisper 1.8.6</dd><dt>Text to speech</dt><dd>Piper 1.4.2</dd><dt>Network inference</dt><dd>Previously blocked</dd><dt>Last voice loop</dt><dd>Passed · 30 Aug 21:42</dd></dl><button className="secondary-button" onClick={() => notify('Profile comparison: Balanced local is the only approved fixture profile')}>Compare profiles</button></aside></div>
    <BoundaryNote>Normal users can change experience and privacy preferences. Fleet-level policy, model rollout, and security controls require scoped admin authorization and device acknowledgment.</BoundaryNote>
  </>;
}

function CommonsView() {
  return <><PageHeading eyebrow="Privacy-bounded aggregates" title="Commons sync" copy="Proposal and query lifecycle without ingesting personal memories or proposal bodies." action={<Status tone="warn">Proposer not enrolled</Status>} />
    <div className="commons-grid"><article className="panel sync-card"><span className="eyebrow">Public proposal outbox</span><h2>0 queued items</h2><p>No public proposal identity has been enrolled. Personal SAGE continues locally and independently.</p><div className="proposal-grid">{proposalStates.map(([state, count, note]) => <div key={state as string}><strong>{count}</strong><span>{state}</span><small>{note}</small></div>)}</div></article><article className="panel query-card"><span className="eyebrow">Public query cache</span><h2>Unavailable</h2><Status tone="muted">Never synchronized</Status><dl><dt>Covered locality</dt><dd>None</dd><dt>Topic scope</dt><dd>None</dd><dt>Valid until</dt><dd>—</dd><dt>Transport</dt><dd>Not configured</dd></dl><p>Expired knowledge can remain in labelled history, but is never presented as current.</p></article></div>
    <div className="receipt-legend"><span>Local signing</span><i>→</i><span>Transport</span><i>→</i><span>Network receipt</span><i>→</i><span>Acceptance</span><i>→</i><span>Commitment</span><i>→</i><span>Freshness / expiry</span></div>
  </>;
}

function EvidenceView({ notify }: { notify: (message: string) => void }) {
  const exportEvidence = () => { const blob = new Blob([JSON.stringify({ exportedAt: new Date().toISOString(), redaction: 'sanitized', bundles: evidenceBundles }, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'sage-lantern-evidence-sanitized.json'; anchor.click(); URL.revokeObjectURL(url); notify('Sanitized evidence manifest exported'); };
  return <><PageHeading eyebrow="Five distinct evidence planes" title="Evidence" copy="Structured, hash-addressed proof with explicit claims, retention, and redaction." action={<button className="primary-button" onClick={exportEvidence}>⇩ Export sanitized</button>} />
    <div className="evidence-planes">{['Runtime / release','Pack catalogue','Device receipts','Full-backup','State sync'].map((plane, index) => <div key={plane}><span>{index + 1}</span><strong>{plane}</strong><small>{index < 2 || index === 3 ? 'Evidence available' : 'No records yet'}</small></div>)}</div>
    <article className="panel data-panel"><div className="table-head evidence-table"><span>Bundle</span><span>Evidence plane</span><span>Source</span><span>Created</span><span>Files</span><span>Hash</span><span>Result</span></div>{evidenceBundles.map((bundle) => <div className="table-row evidence-table" key={bundle.id}><div><strong>{bundle.name}</strong><small>{bundle.id}</small></div><span>{bundle.plane}</span><span>{bundle.source}</span><span>{bundle.created}</span><span>{bundle.files}</span><code>{bundle.hash}</code><Status tone="good">{bundle.result}</Status></div>)}</article>
  </>;
}

function IncidentsView({ notify, setView }: { notify: (message: string) => void; setView: (view: View) => void }) {
  const [showResolved, setShowResolved] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  return <><PageHeading eyebrow="Operational response · fixture" title="Incidents" copy="Concrete symptoms, assigned response, preserved evidence, and redacted status—not generic red dots." action={<button className="primary-button" onClick={() => setDraftOpen(true)}>＋ Draft incident</button>} />
    {draftOpen && <article className="panel incident-draft"><div><span className="eyebrow">Unsaved fixture draft</span><h2>Describe the operational symptom</h2><p>Incident persistence and server-side attribution are not deployed. This preview cannot create an authoritative record.</p></div><label>Symptom<input placeholder="Example: display fails after warm reboot" /></label><label>Severity<select defaultValue="S3"><option>S1</option><option>S2</option><option>S3</option><option>S4</option></select></label><div><button className="primary-button" onClick={() => { notify('Incident preview discarded; no authoritative record was created'); setDraftOpen(false); }}>Review workflow</button><button className="secondary-button" onClick={() => setDraftOpen(false)}>Cancel</button></div></article>}
    {!showResolved ? <article className="panel empty-state"><span>✓</span><h2>No active fixture incidents</h2><p>One provisioning task needs attention, but no operational incident is open.</p><div><button className="secondary-button" onClick={() => setShowResolved(true)}>View resolved incidents</button><button className="text-button" onClick={() => notify('Runbook index: provisioning, radio safety, recovery, and signed update verification')}>Open runbooks</button></div></article> : <article className="panel resolved-list"><div className="panel-heading"><div><span className="eyebrow">Sanitized history</span><h2>Resolved incidents</h2></div><button onClick={() => setShowResolved(false)}>Back to active</button></div>{resolvedIncidents.map((incident) => <div key={incident.id}><span><strong>{incident.title}</strong><small>{incident.id} · {incident.opened} → {incident.resolved}</small></span><Status tone="good">{incident.severity} · Resolved</Status><button className="text-button" onClick={() => setView('Evidence')}>Evidence →</button></div>)}</article>}
  </>;
}

function AuditView({ setView }: { setView: (view: View) => void }) { return <><PageHeading eyebrow="Fixture operations trail" title="Audit" copy="Sanitized fixture events show actor, authority plane, outcome, correlation, and evidence linkage." /><article className="panel timeline">{auditEvents.map((event) => <div className="timeline-event" key={event.correlation}><time>{event.time}</time><span className="timeline-dot"/><div><div className="item-title"><strong>{event.action}</strong><Status tone="good">{event.outcome}</Status></div><p>{event.actor} · {event.plane} · {event.target}</p><code>{event.correlation}</code></div><button className="text-button" onClick={() => setView('Evidence')}>Evidence →</button></div>)}</article><BoundaryNote>This timeline is a fixture preview, not an immutable authority log. Live audit writes require server-side role attribution and append-only persistence.</BoundaryNote></>; }

function AuthorityView({ role }: { role: Role }) {
  const rows = [
    ['Fleet inventory & health', 'View', 'Diagnose assigned', 'Manage scoped', 'Release context', 'View'],
    ['Provisioning gates', 'View', 'Prepare & test', 'Approve activation', 'View', 'Audit'],
    ['LoRa neighborhood', 'View', 'Scan & diagnose', 'Configure policy', 'View', 'Audit'],
    ['Updates', 'View status', 'Stage approved', 'Canary & waves', 'Publish manifests', 'Audit'],
    ['Audit & evidence', 'View sanitized', 'Attach scoped', 'Export scoped', 'Release evidence', 'Export sanitized'],
    ['Personal SAGE memories', 'Never', 'Never', 'Never', 'Never', 'Never'],
    ['Personal Root / Admin', 'Never', 'Never', 'Never', 'Never', 'Never'],
    ['Proposer private key', 'Never', 'Never', 'Never', 'Never', 'Never'],
    ['Validator governance', 'Separate console', 'Separate console', 'Separate console', 'Separate console', 'Separate console'],
  ];
  return <><PageHeading eyebrow="Least privilege · preview" title="Authority map" copy="Fleet personas never silently become personal SAGE roles, Root holders, proposer keys, or validators." action={<Status tone="info">Previewing {role}</Status>} /><div className="authority-warning"><strong>No server role assignment loaded</strong><span>The selector changes this fixture presentation only; it does not authorize an action.</span></div><article className="panel authority-table"><div className="table-head"><span>Capability</span><span>Viewer</span><span>Technician</span><span>Fleet operator</span><span>Release manager</span><span>Auditor</span></div>{rows.map((row) => <div className={row[0].includes('Root') || row[0].includes('private') || row[0].includes('memories') ? 'authority-row prohibited' : 'authority-row'} key={row[0]}>{row.map((cell, index) => <span key={`${row[0]}-${index}`}>{cell}</span>)}</div>)}</article><BoundaryNote>Production authorization will be enforced server-side and checked again on the Lantern. This interface currently demonstrates the policy but grants no fleet authority.</BoundaryNote></>;
}

function DeviceDrawer({ device, close, setView }: { device: Device; close: () => void; setView: (view: View) => void }) {
  const [tab, setTab] = useState<'Health' | 'Inventory' | 'Identities' | 'Operations'>('Health');
  return <div className="overlay" role="presentation" onMouseDown={close}><aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="device-title" onMouseDown={(event) => event.stopPropagation()}><button className="close-button" aria-label="Close device details" onClick={close}>×</button><div className="drawer-header"><div className="lantern-avatar">A<span /></div><div><span className="eyebrow">Fixture device detail</span><h2 id="device-title">{device.label}</h2><p>{device.site} · {device.model}</p></div><Status tone="warn">{device.lifecycle}</Status></div><Freshness label={device.freshness} time={device.observedAt}/><div className="drawer-tabs" role="tablist">{(['Health','Inventory','Identities','Operations'] as const).map((label) => <button key={label} role="tab" aria-selected={tab === label} className={tab === label ? 'active' : ''} onClick={() => setTab(label)}>{label}</button>)}</div>
    {tab === 'Health' && <><div className="detail-grid"><Detail label="Personal SAGE /ready" value={device.personalHealth}/><Detail label="Local voice" value={device.voice}/><Detail label="Radio companion" value={device.radio}/><Detail label="Software" value={`SAGE ${device.sageVersion} · app ${device.appVersion}`}/><Detail label="Temperature" value={`${device.temperature} · last known`}/><Detail label="Storage" value={device.storage}/><Detail label="Clock confidence" value="NTP synchronized · fixture record"/><Detail label="Management channel" value="Not paired · authority unavailable"/></div><BoundaryNote>App availability, last-heard radio, service health, command delivery, and Commons publication are separate claims.</BoundaryNote></>}
    {tab === 'Inventory' && <div className="inventory-list"><Detail label="Compute" value="ROCK 5B+ · 16 GB · ARM64"/><Detail label="Storage" value="NVMe · stable path recorded · 74% free"/><Detail label="Operating system" value="Debian Bookworm · kernel/bootloader in EV-001"/><Detail label="AI bundle" value="Qwen 3.5 4B · Whisper 1.8.6 · Piper 1.4.2"/><Detail label="Display / touch" value="Not installed · next provisioning gate"/><Detail label="Radio / antenna" value="Not installed · transmission prohibited"/><Detail label="Approved profile" value={device.profile}/><Detail label="Drift" value={device.drift}/></div>}
    {tab === 'Identities' && <section className="identity-list"><span className="eyebrow">Public identity inventory</span><h3>Public fingerprints only</h3>{device.identities.map((identity) => <div key={identity.label}><span><strong>{identity.label}</strong><code>{identity.fingerprint}</code></span><Status tone={identity.status === 'Unique' ? 'good' : 'warn'}>{identity.status}</Status></div>)}</section>}
    {tab === 'Operations' && <div className="drawer-operation-list">{operations.filter((operation) => operation.target === device.label).map((operation) => <button key={operation.id} onClick={() => { close(); setView('Operations'); }}><span><strong>{operation.action}</strong><small>{operation.id} · {operation.receipt}</small></span><Status tone={operation.tone}>{operation.state}</Status></button>)}</div>}
    <div className="drawer-actions"><button className="primary-button" onClick={() => { close(); setView('Provisioning'); }}>Open provisioning</button><button className="secondary-button" onClick={() => { close(); setView('Evidence'); }}>View evidence</button></div></aside></div>;
}

function PairingDialog({ close, notify, isOperator }: { close: () => void; notify: (message: string) => void; isOperator: boolean }) { return <div className="overlay centered" role="presentation" onMouseDown={close}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="pair-title" onMouseDown={(event) => event.stopPropagation()}><button className="close-button" aria-label="Close enrollment preview" onClick={close}>×</button><span className="modal-symbol">◇</span><span className="eyebrow">Identity-safe enrollment preview</span><h2 id="pair-title">Pair a Lantern</h2><p>The protocol core, dedicated management identity, signed observation contract, and outbound mTLS adapter are implemented. Physical-possession enrollment and the separate fleet authority are not deployed.</p><ol><li><span>1</span>Single-use setup code</li><li><span>2</span>Two-sided comparison</li><li><span>3</span>Duplicate-identity preflight</li></ol><BoundaryNote>This preview creates no setup secret, certificate, identity record, or authority assignment.</BoundaryNote><button className="primary-button" disabled={!isOperator} onClick={() => { notify('Enrollment requirements reviewed · no pairing state was created'); close(); }}>{isOperator ? 'Acknowledge requirements' : 'Technician preview required'}</button></section></div>; }

function SearchDialog({ query, setQuery, results, close, select }: { query: string; setQuery: (value: string) => void; results: SearchResult[]; close: () => void; select: (result: SearchResult) => void }) { return <div className="overlay centered search-overlay" role="presentation" onMouseDown={close}><section className="search-dialog" role="dialog" aria-modal="true" aria-label="Search devices, evidence, audit, and console views" onMouseDown={(event) => event.stopPropagation()}><div className="search-input">⌕<input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search devices, evidence, audit, views…"/><kbd>esc</kbd></div><div className="search-results">{query && results.length === 0 && <p>No matching fixture records or console views.</p>}{results.map((result) => <button key={result.id} onClick={() => select(result)}><span>{result.icon}</span><span><strong>{result.label}</strong><small>{result.meta}</small></span><kbd>↵</kbd></button>)}{!query && <p>Try “Lantern A”, “EV-003”, “smoke”, “radio”, or “operations”. Press ⌘/Ctrl K anywhere to reopen.</p>}</div></section></div>; }

function Metric({ icon, label, value, note, tone }: { icon: string; label: string; value: string; note: string; tone: string }) { return <article className={`metric-card ${tone === 'amber' ? 'attention-metric' : ''}`}><span className={`metric-icon ${tone}`}>{icon}</span><div><small>{label}</small><strong>{value}</strong><p>{note}</p></div></article>; }
function PanelHeading({ eyebrow, title, action, onAction }: { eyebrow: string; title: string; action: string; onAction: () => void }) { return <div className="panel-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div><button onClick={onAction}>{action}</button></div>; }
function Status({ tone, children }: { tone: Tone | string; children: React.ReactNode }) { return <span className={`pill ${tone}`}>{children}</span>; }
function Freshness({ label, time = '30 Aug 2026, 22:06 MYT' }: { label: string; time?: string }) { return <div className="freshness"><span>{label}</span> {time} · <strong>Fixture reference · signed bench evidence</strong></div>; }
function BoundaryNote({ children }: { children: React.ReactNode }) { return <div className="boundary-note"><span>⌾</span><p>{children}</p></div>; }
function Detail({ label, value }: { label: string; value: string }) { return <div><small>{label}</small><strong>{value}</strong></div>; }
function initials(name: string) { return name.split(/[@\s.]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || 'LF'; }
