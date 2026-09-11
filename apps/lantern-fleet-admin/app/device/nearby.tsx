'use client';

import { useState } from 'react';
import type { DeviceStatus } from '../../lib/device-state';
import { BackIcon, NetworkIcon } from './icons';
import StatusScreen from './status';
import RadioObservations from './radio-observations';
import NearbyDirectory from './nearby-directory';

export default function NearbyScreen({ status, connected, close }: { status: DeviceStatus | null; connected: boolean; close: () => void }) {
  const [details, setDetails] = useState(false);
  const [people, setPeople] = useState(false);
  if (people) return <NearbyDirectory close={() => setPeople(false)} />;
  if (details) return <StatusScreen status={status} connected={connected} close={() => setDetails(false)} />;
  return <section className="device-subscreen" aria-label="Nearby">
    <header className="device-subheader"><button onClick={close}><BackIcon />Back</button><div className="device-page-title"><NetworkIcon /><h1>Radio overview</h1></div></header>
    <div className="device-subcontent">
      <RadioObservations />
      <div className="radio-navigation-actions">
        <button onClick={() => setPeople(true)}><NetworkIcon />Find people</button>
        <button onClick={() => setDetails(true)}>Device details</button>
      </div>
    </div>
  </section>;
}
