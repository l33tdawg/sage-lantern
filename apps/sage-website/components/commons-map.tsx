'use client';

import { useState } from 'react';
import { AudioLines, BookOpen, GraduationCap, HeartHandshake, LockKeyhole, Plus, RadioTower, type LucideIcon } from 'lucide-react';

type Node = {
 step: string;
 Icon: LucideIcon;
 kicker: string;
 title: string;
 role: string;
 owner: string;
 licences: string[];
 detail: string;
 facts: [string, string][];
 privateNote?: string;
};

const nodes: Node[] = [
 {
  step: '01',
  Icon: HeartHandshake,
  kicker: 'Steward',
  title: 'SAGE Foundation',
  role: 'Releases, signing trust and transparent reporting — in formation.',
  owner: 'The Foundation (in formation)',
  licences: ['Name and marks · trademark'],
  detail: 'The steward. It funds and holds the mission’s long-term home: releases, signing trust and transparent reporting. In formation. It does not own the commons, is not designed to read your private knowledge, and is not the network’s authority.',
  facts: [
   ['Holds', 'Releases, signing trust and transparent reporting'],
   ['Owner', 'The Foundation itself, in formation. The name and marks are held as a trademark, not licensed away.'],
   ['Boundary', 'Does not own the commons, and is not the network’s authority'],
  ],
 },
 {
  step: '02',
  Icon: GraduationCap,
  kicker: 'Project',
  title: 'SAGE — the project',
  role: 'The papers, the protocol and the research the commons is built on.',
  owner: 'The founder, who wrote the papers',
  licences: ['Papers · published openly'],
  detail: 'The main project. SAGE is the research and engineering work behind the commons: its papers and the protocol they describe. Founded by the Foundation’s founder, who wrote the papers, and stewarded under the Foundation.',
  facts: [
   ['What it is', 'The research project: its papers and the protocol they describe'],
   ['Written by', 'The Foundation’s founder, sole author of the papers'],
   ['Owner', 'The founder holds the papers; licence terms for the specifications and fixtures are published as they are signed off.'],
  ],
 },
 {
  step: '03',
  Icon: BookOpen,
  kicker: 'Commons',
  title: 'SAGE Commons',
  role: 'Shared knowledge: reviewed, content-addressed and signed, so any copy can be checked offline.',
  owner: 'The people who contribute it',
  licences: ['Licence recorded per item, beside its source'],
  detail: 'The shared knowledge. What people choose to publish, reviewed before it travels, content-addressed and signed so any copy can be checked without an account or a connection. Under development.',
  facts: [
   ['Grows from', 'What people choose to contribute, reviewed before it travels'],
   ['Checkable', 'Any copy can be verified offline, with no account and no connection'],
   ['Owner and licence', 'Each item stays the contributor’s, under the licence they chose when they published it. The manifest records that licence beside the source for every chunk.'],
  ],
 },
 {
  step: '04',
  Icon: RadioTower,
  kicker: 'Network',
  title: 'Community networks',
  role: 'MeshCore radio, community repeaters and gateways, with WiFi or satellite backhaul where available.',
  owner: 'Whoever runs the nodes',
  licences: ['MeshCore’s own licence and community'],
  detail: 'The way it travels. Local MeshCore radio, community repeaters and gateways with WiFi or satellite backhaul where available. Short messages and compact knowledge move even when the usual connections don’t.',
  facts: [
   ['Carries', 'Short messages and compact knowledge, between reachable powered peers'],
   ['Built by', 'Anyone who runs a node, holds a copy, extends coverage or hosts a gateway'],
   ['Owner', 'The radio standard is MeshCore’s, not ours — we build above it and contribute improvements upstream. Each repeater or gateway belongs to whoever runs it.'],
  ],
 },
 {
  step: '05',
  Icon: AudioLines,
  kicker: 'Device',
  title: 'SAGE Lantern',
  role: 'Local AI that answers on the device — private by default. A working prototype.',
  owner: 'Yours once you have it',
  licences: ['Open licences published as they are signed off'],
  detail: 'The device at the edge. Local AI that answers on the device, private by default, holding the part of the commons that is useful to you. A working prototype.',
  facts: [
   ['Keeps', 'Your knowledge, private by default'],
   ['Keeps working', 'When the network is down, and can keep relaying what it holds'],
   ['Owner and licence', 'The unit is yours. The open licences for the design, firmware and application are published with each release as they are signed off.'],
  ],
  privateNote: 'Questions, photos and notes stay on this device. Nothing leaves it without a deliberate decision.',
 },
];

export function CommonsMap(){
 const [open, setOpen] = useState(0);
 return <figure className="commons-map">
  <ol className="flow-list">
   {nodes.map((node, index) => {
    const expanded = open === index;
    const panelId = `flow-panel-${index}`;
    return <li className={`flow-item${expanded ? ' is-open' : ''}`} key={node.title}>
     <button
      type="button"
      className="flow-row"
      aria-expanded={expanded}
      aria-controls={panelId}
      onClick={()=>setOpen(expanded ? -1 : index)}
      onKeyDown={(event)=>{if(event.key==='Escape')setOpen(-1);}}
     >
      <span className="flow-step" aria-hidden="true">{node.step}</span>
      <span className="flow-icon"><node.Icon aria-hidden="true"/></span>
      <span className="flow-copy">
       <span className="flow-kicker">{node.kicker}</span>
       <strong className="flow-title">{node.title}</strong>
       <span className="flow-role">{node.role}</span>
       <span className="flow-meta">
        <span className="flow-tag"><em>Owner</em>{node.owner}</span>
        {node.licences.map(licence=><span className="flow-tag" key={licence}><em>Licence</em>{licence}</span>)}
       </span>
      </span>
      <span className="flow-toggle" aria-hidden="true"><Plus/></span>
     </button>
     <section className="flow-panel" id={panelId} aria-label={`${node.title} detail`} hidden={!expanded}>
      <p className="flow-detail">{node.detail}</p>
      <dl className="flow-facts">
       {node.facts.map(([term,description])=><div key={term}>
        <dt>{term}</dt><dd>{description}</dd>
       </div>)}
      </dl>
      {node.privateNote&&<p className="flow-private">
       <LockKeyhole aria-hidden="true"/>
       <span><strong>Your private knowledge.</strong> {node.privateNote}</span>
      </p>}
     </section>
    </li>;
   })}
  </ol>
  <figcaption>Stewardship at the top, the project and the commons beneath it, and the device at the edge. Published knowledge travels; your knowledge stays with you.</figcaption>
 </figure>;
}
