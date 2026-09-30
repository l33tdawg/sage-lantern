import Link from 'next/link';
import type { Metadata } from 'next';
import Image from 'next/image';
import { ArrowUpRight, ArrowDown, MoveUpRight, Battery, AudioLines, ScanEye, Cpu, Wrench, RadioTower, Satellite, Keyboard } from 'lucide-react';
import { SiteHeader, SiteFooter } from '@/components/site-chrome';
import { LanternMark } from '@/components/lantern-mark';
import { pageMetadata } from '@/lib/metadata';
export const metadata: Metadata = pageMetadata(
 'SAGE Lantern — Intelligence in your hands',
 'Meet the next SAGE Lantern design: a 6.25-inch handheld AI terminal that runs on the USB-C supply you bring, with physical voice control and local intelligence. Explore the hardware concept.',
 '/hardware/',
 'hardware-20260930.jpg',
 'SAGE Lantern front and rear — AI-generated hardware concept.',
);
const specs=[
 ['Display','6.25-inch portrait touchscreen, 720 × 1560','Panel selected and in hand'],
 ['Processor','RK3588-class, 8 cores, with a 6 TOPS neural accelerator','Radxa ROCK 5B+; committed for the fleet'],
 ['Memory','12 GB RAM — 16 GB where available','Measured floor for the local model lanes'],
 ['Storage','512 GB NVMe, no SD card','Boots from internal storage'],
 ['Power','Bring your own USB-C supply (PD, or a fixed 5 V/4 A source)','No pack or charger is bundled'],
 ['Radio','Dual 915 MHz LoRa — one radio for the link, one for relay — with GNSS','Two external antennas; the relay antenna sits inside'],
 ['Wireless','Wi-Fi and Bluetooth on board','No add-in module needed'],
 ['Interaction','Physical hold-to-talk control','Voice input, on-screen answers'],
 ['Desktop mode','Connect a USB keyboard and mouse: a Linux desktop, screen turned to landscape','Working on the prototypes'],
 ['Audio','Onboard microphone, no built-in speaker','Headphone output is a future option, not a promise'],
 ['Camera','Internal camera on a CSI ribbon','Visual-question direction in development'],
 ['Enclosure','90 × 182 × 38 mm printed PETG shell, ten parts','v1.3; first-article print next'],
];
export default function Hardware(){return <>
 <Link prefetch={false} className="skip-link" href="#hardware-main">Skip to content</Link><SiteHeader/>
 <main id="hardware-main">
  <section className="hardware-hero wrap"><Link prefetch={false} className="hardware-back" href="/">SAGE / <span>LANTERN</span></Link><div className="hardware-title"><div><div className="eyebrow section-label">LOCAL INTELLIGENCE. REAL-WORLD CURIOSITY.</div><h1>A bigger world.<br/><span className="serif">In your hands.</span></h1></div><div className="hardware-hero-note"><LanternMark/><p>Meet the next chapter<br/>of SAGE Lantern.</p></div></div><div className="hardware-intro"><p>A handheld AI terminal for asking, exploring and making sense of the world around you. Designed to take useful intelligence beyond the places with reliable internet.</p><Link prefetch={false} className="text-link" href="#design">Inside the design <ArrowDown size={18}/></Link></div><figure className="hardware-product"><Image unoptimized priority src="/images/hardware-20260930.jpg" alt="Concept render of a rugged SAGE Lantern showing the 6.25-inch portrait screen, rear camera and two antennas" width={1536} height={1024}/><figcaption>Next-generation design direction · AI-generated concept, not a production photograph</figcaption></figure><div className="hardware-stats"><div><strong>6.25<span>″</span></strong><p>A taller screen to read and explore</p></div><div><Battery/><strong>USB-C</strong><p>Bring your own supply — no pack bundled</p></div><div><AudioLines/><strong>Hold. Ask. Explore.</strong><p>A physical button for your next question</p></div></div></section>
  <section className="section wrap" id="design"><div className="eyebrow section-label">DESIGNED AROUND EVERYDAY CURIOSITY</div><div className="hardware-design-head"><h2>Pick it up.<br/><span className="muted">Follow a question.</span></h2><p className="body-copy">We’re bringing the essentials into one handheld form: a readable screen, a tactile voice button, a camera and power you bring yourself.</p></div><div className="hardware-features">{[
   [AudioLines,'A button that invites a question.','Hold to speak, then read the answer on the screen. A simple physical action keeps the interface approachable.'],
   [ScanEye,'Bring the world into the conversation.','A rear camera supports the visual-question direction: show what you’re looking at, then ask about it.'],
   [Battery,'Bring your own power.','The Lantern runs on a standard USB-C supply — PD, or a fixed 5 V/4 A source — instead of a custom cell or a bundled pack, so power is something you already own.'],
   [Wrench,'Made to be understood.','A ventilated, serviceable enclosure puts assembly, maintenance and repair into the design from the beginning.'],
   [Keyboard,'Set it down. It becomes a desktop.','Plug in a USB keyboard and mouse and the screen turns to landscape: the companion app in a window beside a browser, terminal, files and maps.'],
  ].map(([Icon,title,body])=>{const FeatureIcon=Icon as typeof AudioLines;return <article key={title as string}><FeatureIcon/><h3>{title as string}</h3><p>{body as string}</p></article>})}</div></section>
  <section className="hardware-compute"><div className="wrap"><Cpu/><div><div className="eyebrow section-label">INTELLIGENCE CLOSE TO YOU</div><h2>The thinking happens<br/><span className="serif">right here.</span></h2><p className="body-copy">Lantern’s local AI runs on the device. The aim is to make everyday learning and problem-solving useful even when the internet isn’t available.</p><Link prefetch={false} className="text-link" href="/#mission">The mission behind the machine <ArrowUpRight size={18}/></Link></div></div></section>
  <section className="section wrap hardware-network"><div className="eyebrow section-label">A LANTERN IS ALSO A NETWORK PARTICIPANT</div><div className="hardware-design-head"><h2>Your device.<br/><span className="serif">Your community.</span></h2><p className="body-copy">Each Lantern is intended to join a local MeshCore radio network. Alongside the wider MeshCore community, we want to help more places build communications they can maintain themselves.</p></div><div className="hardware-features"><article><RadioTower/><h3>More people. Infrastructure that reaches further.</h3><p>Lanterns add participants. Dedicated repeaters and well-placed antennas extend coverage. Local operators and reliable power make a network useful in everyday life and during outages. Two radios per Lantern — one for your own messages, one for relay duty — mean passing a neighbour’s message on doesn’t block your next question.</p></article><article><Satellite/><h3>Local first. Backhaul when available.</h3><p>We plan to roll out community gateways using Starlink backhaul. They would support authorised online requests and wider SAGE connections through a separate gateway service. Local radio messages should remain possible between reachable, powered peers when backhaul is down.</p></article></div><Link prefetch={false} className="text-link" href="/#network">Explore the network vision <ArrowUpRight size={18}/></Link><p className="network-status">Network rollout and gateway integration are goals under development. This does not promise unlimited radio bandwidth, automatic coverage from every handset, or uninterrupted satellite service.</p></section>
  <section className="section wrap hardware-spec-section"><div><div className="eyebrow section-label">THE CURRENT DIRECTION</div><h2>What we’re<br/><span className="muted">building toward.</span></h2><p className="body-copy">This is the next enclosure concept, informed by our working prototypes. The design will evolve as we test fit, cooling, power and everyday use.</p></div><section className="spec-table" aria-label="Hardware design specifications"><table><caption className="sr-only">SAGE Lantern hardware direction and development status</caption><thead><tr><th>Component</th><th>Design direction</th></tr></thead><tbody>{specs.map(([name,value,status])=><tr key={name}><th scope="row">{name}</th><td>{value}<span>{status}</span></td></tr>)}</tbody></table><p>Final weight, printed-case fit and release pricing will be published after validation. Runtime depends on the supply you bring.</p></section></section>
  <section className="hardware-cta wrap"><div className="eyebrow">FROM A WORKING PROTOTYPE TO SOMETHING PEOPLE CAN USE.</div><h2>Help shape<br/>the next <span className="serif">Lantern.</span></h2><p>Hardware builders, educators and community partners: bring your perspective to the design.</p><Link prefetch={false} className="hardware-contact" href="mailto:dhillon@sage.ngo?subject=SAGE%20Lantern%20hardware">Talk hardware with us <MoveUpRight size={19}/></Link><Link prefetch={false} className="hardware-home" href="/#progress">See the roadmap <ArrowUpRight size={16}/></Link></section>
 </main><SiteFooter/>
 </>}
