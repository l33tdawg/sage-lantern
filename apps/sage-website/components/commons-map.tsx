import { AudioLines, BookOpen, HeartHandshake, LockKeyhole, RadioTower, type LucideIcon } from 'lucide-react';

type Step = { Icon: LucideIcon; title: string; note: string; edge?: string };

const steps: Step[] = [
 {
  Icon: HeartHandshake,
  title: 'SAGE Foundation',
  note: 'The steward: releases, signing trust and transparent reporting. In formation.',
  edge: 'signs and publishes',
 },
 {
  Icon: BookOpen,
  title: 'SAGE Commons',
  note: 'The shared knowledge: reviewed, content-addressed and signed, so any copy can be checked offline.',
  edge: 'carries',
 },
 {
  Icon: RadioTower,
  title: 'Community networks',
  note: 'MeshCore radio, community repeaters and gateways, with WiFi or satellite backhaul where available.',
  edge: 'reaches',
 },
 {
  Icon: AudioLines,
  title: 'SAGE Lantern',
  note: 'The device at the edge: local AI that answers on the device. A working prototype.',
 },
];

export function CommonsMap(){
 return <figure className="commons-map">
  <div className="map-flow">
   {steps.map(({Icon,title,note,edge},index)=><div key={title}>
    <div className={index===steps.length-1?'map-step map-step-device':'map-step'}>
     <Icon aria-hidden="true"/>
     <div><h3>{title}</h3><p>{note}</p></div>
     {index===steps.length-1&&<div className="map-private">
      <LockKeyhole aria-hidden="true"/>
      <p><strong>Your private knowledge.</strong> Questions, photos and notes stay on this device. Nothing leaves it without a deliberate decision.</p>
     </div>}
    </div>
    {edge&&<p className="map-edge">{edge}</p>}
   </div>)}
  </div>
  <figcaption>Each part has a job it can be held to. Published knowledge travels the stack; your knowledge stays at the bottom, with you.</figcaption>
 </figure>;
}
