'use client';

import { useEffect, useRef } from 'react';
import { LanternIcon, BackIcon, InfoIcon } from './icons';

export default function AboutScreen({ close }: { close: () => void }) {
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => { back.current?.focus(); }, []);
  return <section className="device-subscreen" onKeyDown={(event) => { if (event.key === 'Escape') close(); }} aria-label="About Lantern">
    <header className="device-subheader"><button ref={back} onClick={close}><BackIcon />Back</button><div className="device-page-title"><InfoIcon /><h1>About Lantern</h1></div></header>
    <div className="device-subcontent" tabIndex={0}>
      <section className="device-panel"><div className="device-about-brand"><LanternIcon /><h2>SAGE Lantern</h2></div><p>Ask by voice. Explore knowledge on this device.</p></section>
      <section className="device-panel"><h2>Your privacy</h2><p>The microphone starts only when you ask. Photos are taken only when you choose. Saved conversations are visible to anyone using this device. You can stop saving in Settings. Removing a conversation stops it being used in future answers; original records cannot be erased.</p></section>
      <section className="device-panel device-panel-caution"><h2>Check important answers</h2><p>AI can be mistaken. There is no live web search. Do not rely on a photo answer to decide whether a plant is edible or a situation is safe.</p></section>
      <p className="device-note">Public knowledge sharing is not enabled on this prototype.</p>
      <details className="device-panel device-details"><summary>Keyboard vocabulary credits</summary><p>English suggestions use FrequencyWords by Hermit Dave and contributors, based on OpenSubtitles2018. The adapted vocabulary is licensed under Creative Commons Attribution-ShareAlike 4.0 International.</p><p>Changes: lowercase alphabetic words, deduplicated in frequency order, with a minimum source frequency of 1,000. The list contains 17,306 words.</p><p><a href="https://github.com/hermitdave/FrequencyWords/blob/525f9b560de45753a5ea01069454e72e9aa541c6/content/2018/en/en_50k.txt" target="_blank" rel="noreferrer">Original vocabulary</a> · <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noreferrer">License</a></p></details>
      <details className="device-panel device-details"><summary>Version &amp; device details</summary><dl className="device-info-table"><dt>Interface release</dt><dd>Unit A · UX/vision 2026-09-05</dd><dt>Mode</dt><dd>Local prototype</dd><dt>Model version</dt><dd>See verified operator inventory</dd></dl></details>
    </div>
  </section>;
}
