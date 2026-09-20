import Link from 'next/link';
import type { Metadata } from 'next';
import { ArrowUpRight, ArrowDown, MoveUpRight, HeartHandshake, BookOpen, RadioTower, AudioLines, LockKeyhole, Globe2, Code2 } from 'lucide-react';
import { SiteHeader, SiteFooter } from '@/components/site-chrome';
import { LanternMark } from '@/components/lantern-mark';
import { CommonsMap } from '@/components/commons-map';
import { pageMetadata } from '@/lib/metadata';
export const metadata: Metadata = pageMetadata(
 'SAGE Commons — the foundation, the network, the device',
 'How SAGE fits together: a steward in formation, a shared commons of knowledge, community MeshCore networks with planned backhaul, and the Lantern at the edge. The distinction, plainly.',
 '/commons/',
 'connectivity.jpg',
 'A family sharing a laptop at a remote hillside home, with a satellite dish overlooking the valley — AI illustration of community connectivity.',
);
const parts = [
  [HeartHandshake, 'SAGE Foundation', 'The steward. It funds and holds the mission’s long-term home: releases, signing trust and transparent reporting. In formation. It does not own the commons, is not designed to read your private knowledge, and is not the network’s authority.'],
  [BookOpen, 'SAGE Commons', 'The shared knowledge. What people choose to publish, reviewed before it travels, content-addressed and signed so any copy can be checked without an account or a connection. Under development.'],
  [RadioTower, 'Community networks', 'The way it travels. Local MeshCore radio, community repeaters and gateways with WiFi or satellite backhaul where available. Short messages and compact knowledge move even when the usual connections don’t.'],
  [AudioLines, 'SAGE Lantern', 'The device at the edge. Local AI that answers on the device, private by default, holding the part of the commons that is useful to you. A working prototype.'],
];
const ownership = [
  ['Your private knowledge', 'Stays on your Lantern', 'Questions, photos and notes are yours. Nothing leaves without a deliberate decision.'],
  ['The shared commons', 'Grows from contribution', 'Only what people choose to share is considered, and it is reviewed before it becomes available to others.'],
  ['The radio standard', 'MeshCore’s', 'We build above it, contribute improvements upstream and do not fork it. The standard stays with its community.'],
  ['Signed releases and packs', 'Verified by anyone', 'The Foundation signs what it publishes. A pack can be checked without an account, a server or a connection.'],
  ['The device in your hands', 'Yours', 'It keeps answering, and can keep relaying what it holds, when the network is down.'],
];
const reasons = [
  [LockKeyhole, 'A steward, not an owner.', 'The Foundation’s job is to hold trust — releases, keys, transparency — and hand it on. It is not the commons, and it is not consensus.'],
  [Globe2, 'A network, not a service.', 'Anyone can join, hold a copy, extend coverage or run a gateway. No central server is required for a copy of shared knowledge to stay useful.'],
  [Code2, 'A participant, not a terminal.', 'A Lantern answers locally, keeps working offline and can pass on what it holds. The network gets stronger with every participant rather than every purchase.'],
];
export default function Commons(){return <>
 <Link prefetch={false} className="skip-link" href="#commons-main">Skip to content</Link><SiteHeader/>
 <main id="commons-main">
  <section className="hardware-hero wrap"><Link prefetch={false} className="hardware-back" href="/">SAGE / <span>THE COMMONS</span></Link><div className="hardware-title"><div><div className="eyebrow section-label">FOUNDATION · COMMONS · NETWORK · LANTERN</div><h1>One commons.<br/><span className="serif">Four parts.</span></h1></div><div className="hardware-hero-note"><LanternMark/><p>Stewardship, shared knowledge,<br/>community networks, and a device<br/>that holds its own.</p></div></div><div className="hardware-intro"><p>The SAGE Commons is bigger than any one device: a shared body of knowledge, carried over community-owned networks, with clear boundaries about who holds what. Here is the distinction, plainly.</p><Link prefetch={false} className="text-link" href="#parts">How the pieces fit <ArrowDown size={18}/></Link></div><div className="principle-strip"><span>A steward, not an owner</span><span>Private by default</span><span>Open to build on</span><span>Made to keep working <ArrowUpRight size={17}/></span></div></section>
  <section className="section wrap" id="parts"><div className="eyebrow section-label">FOUR PARTS, ONE SYSTEM</div><div className="hardware-design-head"><h2>Different jobs.<br/><span className="muted">One direction.</span></h2><p className="body-copy">Each part has a job it can be held to, and a boundary it must not cross. That is what keeps a shared network from quietly becoming somebody’s platform.</p></div><CommonsMap/><div className="network-diagram" aria-label="The four parts of the SAGE commons: the foundation, the shared knowledge, the community networks and the Lantern">{parts.map(([Icon,title,body])=>{const PartIcon=Icon as typeof HeartHandshake;return <div className="network-node" key={title as string}><div className="network-icon"><PartIcon aria-hidden="true"/></div><h3>{title as string}</h3><p>{body as string}</p></div>})}</div></section>
  <section className="network-section" id="belongs"><div className="section wrap"><div className="eyebrow section-label">WHAT BELONGS TO WHOM</div><div className="network-heading"><h2>Clear lines.<br/><span className="serif">Held in the open.</span></h2><p className="body-copy">A commons only works if people can see where their part ends and someone else’s begins. These are the lines we are building to.</p></div><section className="spec-table" aria-label="Ownership and boundaries across the foundation, the commons, the network and the Lantern"><table><caption className="sr-only">What belongs to whom in the SAGE commons</caption><thead><tr><th>Part</th><th>Whose it is</th></tr></thead><tbody>{ownership.map(([name,value,status])=><tr key={name}><th scope="row">{name}</th><td>{value}<span>{status}</span></td></tr>)}</tbody></table><p>Written as design intentions. Legal formation, review arrangements and the public commons are still being built, and this page will change as they do.</p></section></div></section>
  <section className="section wrap" id="why"><div className="eyebrow section-label">WHY THE DISTINCTION MATTERS</div><div className="hardware-design-head"><h2>The alternative is<br/><span className="serif">a familiar story.</span></h2><p className="body-copy">Knowledge that dies with a company. A device that quietly phones home. A network whose rules are set by whoever holds the servers. The separation is the point.</p></div><div className="hardware-features">{reasons.map(([Icon,title,body])=>{const ReasonIcon=Icon as typeof LockKeyhole;return <article key={title as string}><ReasonIcon/><h3>{title as string}</h3><p>{body as string}</p></article>})}</div><p className="network-status"><strong>Where this stands:</strong> two working prototype Lanterns run local AI and voice on the device; signed knowledge packs verify offline; MeshCore integration is being tested. The public commons, its review process, the public-domain policy on the device and gateway backhaul are still being built. The Foundation is an initiative in formation. Nothing on this page is a live service, and no coverage, bandwidth or permanent availability is promised.</p></section>
  <section className="hardware-cta wrap"><div className="eyebrow">A COMMONS IS BUILT BY THE PEOPLE WHO USE IT.</div><h2>Help build<br/>the <span className="serif">commons.</span></h2><p>Run a node, write a knowledge pack, host a gateway, translate a page or help a community get started. There’s more than one way in.</p><Link prefetch={false} className="hardware-contact" href="mailto:dhillon@sage.ngo?subject=SAGE%20Commons">Build with us <MoveUpRight size={19}/></Link><Link prefetch={false} className="hardware-home" href="/#involve">Find your part <ArrowUpRight size={16}/></Link></section>
 </main><SiteFooter/>
 </>}
