'use client';

import { useState } from 'react';
import { AudioLines, BookOpen, GraduationCap, HeartHandshake, LockKeyhole, RadioTower, X, type LucideIcon } from 'lucide-react';

type Node = {
 Icon: LucideIcon;
 eyebrow: string;
 title: string;
 note: string;
 owner: string;
 licences: string[];
 detail: string;
 facts: [string, string][];
};

const foundation: Node = {
 Icon: HeartHandshake,
 eyebrow: '01 · Steward',
 title: 'SAGE Foundation',
 note: 'The steward: releases, signing trust and transparent reporting. In formation.',
 owner: 'The Foundation (in formation)',
 licences: ['Name and marks · trademark'],
 detail: 'The steward. It funds and holds the mission’s long-term home: releases, signing trust and transparent reporting. In formation. It does not own the commons, is not designed to read your private knowledge, and is not the network’s authority.',
 facts: [
  ['Holds', 'Releases, signing trust and transparent reporting'],
  ['Owner', 'The Foundation itself, in formation. The name and marks are held as a trademark, not licensed away.'],
  ['Boundary', 'Does not own the commons, and is not the network’s authority'],
 ],
};

const project: Node = {
 Icon: GraduationCap,
 eyebrow: '02 · Project',
 title: 'SAGE — the project',
 note: 'The papers, the protocol and the research the commons is built on.',
 owner: 'The founder, who wrote the papers',
 licences: ['Papers · published openly', 'Protocol and specs · Apache-2.0'],
 detail: 'The main project. SAGE is the research and engineering work behind the commons: its papers and the protocol they describe. Founded by the Foundation’s founder, who wrote the papers, and stewarded under the Foundation.',
 facts: [
  ['What it is', 'The research project: its papers and the protocol they describe'],
  ['Written by', 'The Foundation’s founder, sole author of the papers'],
  ['Owner', 'The founder holds the papers; normative specifications and interoperability fixtures are Apache-2.0.'],
 ],
};

const commons: Node = {
 Icon: BookOpen,
 eyebrow: '03 · Commons',
 title: 'SAGE Commons',
 note: 'The shared knowledge: reviewed, content-addressed and signed, so any copy can be checked offline.',
 owner: 'The people who contribute it',
 licences: ['Licence set per item · CC0 · CC BY · CC BY-SA'],
 detail: 'The shared knowledge. What people choose to publish, reviewed before it travels, content-addressed and signed so any copy can be checked without an account or a connection. Under development.',
 facts: [
  ['Grows from', 'What people choose to contribute, reviewed before it travels'],
  ['Checkable', 'Any copy can be verified offline, with no account and no connection'],
  ['Owner and licence', 'Each item stays the contributor’s, under the licence they chose when they published it. The manifest records that licence beside the source for every chunk.'],
 ],
};

const networks: Node = {
 Icon: RadioTower,
 eyebrow: '04 · Network',
 title: 'Community networks',
 note: 'MeshCore radio, community repeaters and gateways, with WiFi or satellite backhaul where available.',
 owner: 'Whoever runs the nodes',
 licences: ['MeshCore’s own licence and community'],
 detail: 'The way it travels. Local MeshCore radio, community repeaters and gateways with WiFi or satellite backhaul where available. Short messages and compact knowledge move even when the usual connections don’t.',
 facts: [
  ['Carries', 'Short messages and compact knowledge, between reachable powered peers'],
  ['Built by', 'Anyone who runs a node, holds a copy, extends coverage or hosts a gateway'],
  ['Owner', 'The radio standard is MeshCore’s, not ours — we build above it and contribute improvements upstream. Each repeater or gateway belongs to whoever runs it.'],
 ],
};

const lantern: Node = {
 Icon: AudioLines,
 eyebrow: '05 · Device',
 title: 'SAGE Lantern',
 note: 'The device at the edge: local AI that answers on the device. A working prototype.',
 owner: 'Yours once you have it',
 licences: ['Design · CERN-OHL-S 2.0', 'Firmware · GPL-3.0', 'App · Apache-2.0'],
 detail: 'The device at the edge. Local AI that answers on the device, private by default, holding the part of the commons that is useful to you. A working prototype.',
 facts: [
  ['Keeps', 'Your knowledge, private by default'],
  ['Keeps working', 'When the network is down, and can keep relaying what it holds'],
  ['Owner and licence', 'The unit is yours. The design is CERN-OHL-S 2.0, the firmware GPL-3.0 and the application layer Apache-2.0, so anyone may build, modify and share one.'],
  ['Private by default', 'Running the software does not license your knowledge to the project. Nothing leaves the device without a deliberate decision.'],
 ],
};

export function CommonsMap(){
 const [active, setActive] = useState<string | null>(null);
 const open = (title: string) => setActive(title);
 const nodes = [foundation, project, commons, networks, lantern];
 const current = nodes.find(node => node.title === active) ?? null;

 const card = (node: Node, className = '') => (
  <button
   type="button"
   className={`org-node${className}${active===node.title?' is-active':''}`}
   aria-expanded={active===node.title}
   aria-controls="map-detail"
   onMouseEnter={()=>open(node.title)}
   onFocus={()=>open(node.title)}
   onClick={()=>open(node.title)}
   onKeyDown={(event)=>{if(event.key==='Escape')setActive(null);}}
  >
   <node.Icon aria-hidden="true"/>
   <span className="org-node-copy">
    <span className="org-node-eyebrow">{node.eyebrow}</span>
    <strong>{node.title}</strong>
    <span className="org-node-note">{node.note}</span>
    <span className="org-chips">
     <span className="org-chip org-chip-owner">{node.owner}</span>
     {node.licences.map(licence=><span className="org-chip" key={licence}>{licence}</span>)}
    </span>
   </span>
   {node.title==='SAGE Lantern'&&<span className="map-private">
    <LockKeyhole aria-hidden="true"/>
    <span><strong>Your private knowledge.</strong> Questions, photos and notes stay on this device. Nothing leaves it without a deliberate decision.</span>
   </span>}
  </button>
 );

 // A hover detail panel has to watch the pointer leave the whole figure; the
 // boxes themselves are buttons, so the listener lives on the figure.
 /* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */
 return <figure className="commons-map" onMouseLeave={()=>setActive(null)}>
  <div className="map-body">
   <div className="org">
    <div className="org-single">{card(foundation)}</div>
    <div className="org-columns">
     <div className="org-column">{card(project)}</div>
     <div className="org-column">
      {card(commons, ' org-node-arch')}
      <span className="org-link" aria-hidden="true"/>
      {card(networks)}
      <span className="org-link" aria-hidden="true"/>
      {card(lantern, ' org-node-device')}
     </div>
    </div>
   </div>
   <aside className={`map-drawer${current?' is-open':''}`} id="map-detail" aria-live="polite">
    {current
     ? <div className="map-detail" key={current.title}>
        <button type="button" className="map-close" aria-label="Close detail" onClick={()=>setActive(null)}><X aria-hidden="true"/></button>
        <div className="map-detail-head"><current.Icon aria-hidden="true"/><div><span className="map-detail-eyebrow">{current.eyebrow}</span><h3>{current.title}</h3></div></div>
        <p>{current.detail}</p>
        <dl className="map-facts">{current.facts.map(([term,description])=><div key={term}>
         <dt>{term}</dt><dd>{description}</dd>
        </div>)}</dl>
       </div>
     : <p className="map-drawer-hint"><AudioLines aria-hidden="true"/>Hover or tap a box to open its detail</p>}
   </aside>
  </div>
  <figcaption>Stewardship at the top, the project and the commons beneath it, and the device at the edge. Published knowledge travels; your knowledge stays with you.</figcaption>
 </figure>;
}
