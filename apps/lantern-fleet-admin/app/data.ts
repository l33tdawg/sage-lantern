export type Tone = 'good' | 'warn' | 'danger' | 'muted' | 'locked' | 'info';

export type Device = {
  id: string;
  label: string;
  model: string;
  site: string;
  lifecycle: string;
  lifecycleTone: Tone;
  connectivity: string;
  observedAt: string;
  freshness: 'Current' | 'Last known' | 'Unknown';
  sageVersion: string;
  appVersion: string;
  profile: string;
  drift: string;
  personalHealth: string;
  radio: string;
  voice: string;
  temperature: string;
  storage: string;
  identities: { label: string; fingerprint: string; status: string }[];
};

export const devices: Device[] = [
  {
    id: 'lantern-a', label: 'Lantern A', model: 'ROCK 5B+ · 16 GB', site: 'Malaysia bench',
    lifecycle: 'Pre-provision', lifecycleTone: 'warn', connectivity: 'Bench LAN',
    observedAt: '30 Aug 2026, 22:06 MYT', freshness: 'Last known', sageVersion: '11.19.10',
    appVersion: '25', profile: 'Bench beta · rev 4', drift: 'Hardware incomplete',
    personalHealth: 'Ready · services intentionally stopped', radio: 'Not installed',
    voice: 'Headless loop passed', temperature: '48°C', storage: '74% free',
    identities: [
      { label: 'Personal SAGE public identity', fingerprint: 'sha256:ce9a…e287', status: 'Unique' },
      { label: 'TLS federation identity', fingerprint: 'sha256:4055…7937', status: 'Unique' },
      { label: 'Comet node identity', fingerprint: 'sha256:78b3…fd83', status: 'Unique' },
      { label: 'Fleet management identity', fingerprint: 'Protocol core implemented · pairing pending', status: 'Required' },
      { label: 'Radio identity', fingerprint: 'Pending on-device generation', status: 'Required' },
      { label: 'Commons proposer identity', fingerprint: 'Pending on-device generation', status: 'Required' },
    ],
  },
];

export const provisioningGates = [
  { id: 'base', label: 'Base system & local AI', status: 'Passed', tone: 'good' as Tone, note: 'Bookworm ARM64, SAGE 11.19.10, local voice and inference verified.', evidence: 'Headless AI smoke · 71729807…' },
  { id: 'display', label: 'Display & touch', status: 'Next', tone: 'warn' as Tone, note: 'Install the physical screen and validate video, touch, boot persistence, and thermals.', evidence: 'No evidence yet' },
  { id: 'radio', label: 'Heltec / MeshCore radio', status: 'Waiting', tone: 'muted' as Tone, note: 'Record exact variant and serial path, attach antenna, configure region, and create identity locally.', evidence: 'Blocked by display gate' },
  { id: 'proposer', label: 'Commons proposer enrollment', status: 'Waiting', tone: 'muted' as Tone, note: 'Create a distinct public proposer key and enroll only with bounded PUBLIC Member scope.', evidence: 'Blocked by radio gate' },
  { id: 'activate', label: 'Final activation', status: 'Locked', tone: 'locked' as Tone, note: 'Verify all public fingerprints, write PROVISIONED last, then enable services and test.', evidence: 'Four prerequisites incomplete' },
];

export const evidenceBundles = [
  { id: 'EV-003', name: 'Headless AI acceptance', plane: 'Runtime evidence', created: '30 Aug 2026, 21:42', source: 'Lantern A', result: 'Passed', hash: '71729807…45787f', files: 5 },
  { id: 'EV-002', name: 'Stopped-node recovery rehearsal', plane: 'Full-backup evidence', created: '30 Aug 2026, 19:18', source: 'Lantern A', result: 'Passed', hash: '6b53b162…1863ec', files: 8 },
  { id: 'EV-001', name: 'Pre-provision inventory', plane: 'Device observation', created: '30 Aug 2026, 17:06', source: 'Lantern A', result: 'Passed', hash: 'd130c6a3…dac82', files: 12 },
];

export const releases = [
  { version: 'Lantern 0.1.0', channel: 'Bench beta', sage: '11.19.10', arch: 'ARM64', size: '4.1 GB', signature: 'Verified', compatibility: 'ROCK 5B+', status: 'Current', rollback: 'Base rev 3' },
  { version: 'Lantern 0.1.1-rc1', channel: 'Candidate', sage: '11.19.10', arch: 'ARM64', size: '312 MB delta', signature: 'Verified', compatibility: 'Requires activated device', status: 'Blocked', rollback: 'Lantern 0.1.0' },
];

export const proposalStates = [
  ['Queued locally', 0, 'Device-local only'], ['Sent', 0, 'Transport acknowledgment'],
  ['Received', 0, 'Public intake receipt'], ['Quarantined', 0, 'Schema and safety review'],
  ['Needs action', 0, 'Operator clarification'], ['Approved', 0, 'Eligible for submission'],
  ['Submitted', 0, 'Consensus proposal'], ['Committed', 0, 'Public chain reference'],
  ['Rejected', 0, 'Reason retained'], ['Expired', 0, 'Excluded from current answers'],
];

export const auditEvents = [
  { time: '21:42', actor: 'Bench operator', action: 'Completed headless AI acceptance', target: 'Lantern A', outcome: 'Passed', correlation: 'smoke-71729807', plane: 'Device management' },
  { time: '19:18', actor: 'Bench operator', action: 'Completed stopped-node recovery rehearsal', target: 'Lantern A', outcome: 'Passed', correlation: 'recovery-6b53b162', plane: 'Device management' },
  { time: '17:06', actor: 'Lantern A', action: 'Submitted sanitized pre-provision inventory', target: 'Fleet inventory', outcome: 'Accepted', correlation: 'observe-d130c6a3', plane: 'Evidence ingest' },
];

export const clusters = [{ name: 'Malaysia bench fleet', region: 'MY', devices: 1, attention: 1, lastSync: 'just now', operator: 'Foundation lab' }];

export const radioNeighbors = [
  { id: 'radio-local', label: 'Lantern A radio', kind: 'Configured companion', status: 'Not installed', signal: '—', hops: '—', lastHeard: 'Never', route: 'Direct USB', region: 'Pending', trusted: true },
  { id: 'mesh-demo-01', label: 'Nearby node · demo fixture', kind: 'MeshCore node', status: 'Fixture only', signal: '-74 dBm', hops: '1', lastHeard: 'Sample data', route: 'LoRa', region: 'MY preset', trusted: false },
];

export const federationLanes = [
  { name: 'Personal SAGE local federation', scope: 'Machine-local sovereign node', connection: 'Ready locally', trust: 'No remote trust lanes configured', freshness: 'Observed 22:06 MYT', tone: 'good' as Tone, management: 'Open local CEREBRUM' },
  { name: 'Commons proposal federation', scope: 'PUBLIC proposer and query client', connection: 'Not enrolled', trust: 'Bounded Member scope pending', freshness: 'No receipt yet', tone: 'warn' as Tone, management: 'Enrollment gate' },
  { name: 'Fleet management channel', scope: 'Dedicated device-management plane', connection: 'Authority not deployed', trust: 'Protocol core and outbound mTLS adapter implemented', freshness: 'Not paired', tone: 'muted' as Tone, management: 'Review pairing' },
  { name: 'Cloud Commons context', scope: 'Read-only network availability', connection: 'Unavailable', trust: 'No validator authority', freshness: 'Topology undecided', tone: 'muted' as Tone, management: 'View contract' },
];

export const aiFeatures = [
  { id: 'voice', name: 'Push-to-talk voice', description: 'Local capture, transcription, reasoning, and spoken response.', enabled: true, observed: 'Verified offline', category: 'Experience', editableBy: 'user' },
  { id: 'wake', name: 'Wake phrase', description: 'Hands-free activation. Disabled by default for privacy.', enabled: false, observed: 'Disabled', category: 'Experience', editableBy: 'user' },
  { id: 'tts', name: 'Spoken responses', description: 'Read Lantern replies aloud with the local Piper voice.', enabled: true, observed: 'Piper local', category: 'Experience', editableBy: 'user' },
  { id: 'history', name: 'Local conversation history', description: 'Retain recent conversations on this Lantern only.', enabled: false, observed: 'No retention', category: 'Privacy', editableBy: 'user' },
  { id: 'offline', name: 'Offline-only AI', description: 'Prevent AI requests from using network inference.', enabled: true, observed: 'Enforced locally', category: 'Privacy', editableBy: 'admin' },
  { id: 'commons', name: 'Commons query suggestions', description: 'Offer relevant public Commons knowledge when available.', enabled: false, observed: 'Proposer not enrolled', category: 'Federation', editableBy: 'admin' },
];

export const operations = [
  { id: 'OP-014', action: 'apply_device_profile', target: 'Lantern A', revision: 'bench-beta:5', state: 'State conflict', tone: 'warn' as Tone, requestedBy: 'Fixture operator', expires: 'Not issued', receipt: 'No device receipt', reason: 'Expected state hash belongs to rev 4; reconcile before signing.' },
  { id: 'OP-013', action: 'collect_sanitized_evidence', target: 'Lantern A', revision: 'evidence:headless-ai', state: 'Succeeded', tone: 'good' as Tone, requestedBy: 'Bench operator', expires: 'Completed 21:42', receipt: 'Fixture receipt reference · 71729807…', reason: 'Accepted in the fixture timeline; no live authority connection.' },
  { id: 'OP-012', action: 'stage_release', target: 'Lantern A', revision: 'lantern:0.1.1-rc1', state: 'Blocked', tone: 'locked' as Tone, requestedBy: 'Release manager', expires: 'Not issued', receipt: 'No device receipt', reason: 'Device activation and recovery preflight are incomplete.' },
];

export const updateCampaigns = [
  { id: 'CAM-002', name: 'Lantern 0.1.1 canary', release: '0.1.1-rc1', scope: 'Malaysia bench · 1 canary', state: 'Blocked', tone: 'locked' as Tone, progress: '0 of 1', next: 'Complete activation and stopped-node backup evidence' },
  { id: 'CAM-001', name: 'Base rev 4 verification', release: '0.1.0', scope: 'Malaysia bench · 1 device', state: 'Fixture complete', tone: 'good' as Tone, progress: '1 of 1', next: 'No expansion planned' },
];

export const compatibilityChecks = [
  { check: 'Target architecture', result: 'Compatible', detail: 'ARM64 · ROCK 5B+', tone: 'good' as Tone },
  { check: 'Device activation', result: 'Blocked', detail: 'Four provisioning prerequisites incomplete', tone: 'locked' as Tone },
  { check: 'Recovery evidence', result: 'Available', detail: 'Stopped-node rehearsal EV-002', tone: 'good' as Tone },
  { check: 'Power / thermal window', result: 'Unknown', detail: 'Requires a fresh signed observation', tone: 'muted' as Tone },
  { check: 'Device Agent receipt path', result: 'Not deployed', detail: 'Protocol verified; fleet authority pending', tone: 'warn' as Tone },
];

export const resolvedIncidents = [
  { id: 'INC-002', title: 'Headless inference server failed readiness', severity: 'S2', opened: '30 Aug · 20:56', resolved: '30 Aug · 21:42', outcome: 'Resolved with sanitized evidence EV-003' },
  { id: 'INC-001', title: 'NVMe path changed after bench reboot', severity: 'S3', opened: '30 Aug · 16:42', resolved: '30 Aug · 17:06', outcome: 'Stable device path recorded in EV-001' },
];
