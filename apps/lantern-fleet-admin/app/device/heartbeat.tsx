'use client';

/**
 * Heartbeat: the reference state/interval/Save arrangement, with no device behind it.
 *
 * This build exposes no heartbeat feature. There is no approved binding to read a
 * schedule from or to write one through, and the real app supplies no `binding` at
 * all, so the screen is inert on purpose: the composition was agreed as a
 * destination, and a destination is not a capability. The controls therefore say
 * why they are disabled instead of pretending a device answered.
 *
 * The rule this file exists to hold is that unknown is not Off. A device that cannot
 * answer has not answered "no", so the state card reads Unavailable when nothing is
 * supplied, the interval field stays empty rather than borrowing the reference
 * screen's default interval, and the word Off appears only where a supplied binding
 * itself reports `active: false`.
 *
 * Nothing here saves. There is no transport call in this file. A labelled fixture
 * may pass a `HeartbeatBinding` so the arrangement can be reviewed with the controls
 * live, and Save then answers with a note saying that no device setting was written.
 * A fixture is data for a review, never a write.
 */
import { useEffect, useRef, useState } from 'react';
import { BackIcon, HeartbeatIcon } from './icons';

/**
 * The reading a review fixture may supply.
 *
 * `exposed` is the literal `true` rather than a boolean, so this type cannot
 * describe a device that declined the feature: the only way to say "this device has
 * no heartbeat" is to supply no binding. The honesty rule thus lives in the type as
 * well as in the copy, because no caller can hand this screen a shape that would
 * make it render Off for a device that never answered.
 */
export type HeartbeatBinding = { exposed: true; active: boolean; intervalMinutes: number };

/**
 * The one reason this screen gives for being inert, held in a constant because the
 * disabled controls point at it with `aria-describedby`. A control that explains
 * itself in another element has to explain itself in exactly these words.
 */
const UNAVAILABLE_REASON = 'Heartbeat is not available on this device yet.';

/** What Save says instead of claiming success. */
const FIXTURE_NOTE = 'This is a synthetic fixture. No device setting was written.';

/** Minutes as a phrase, so a fixture interval of 1 does not read as a plural. */
function everyMinutes(value: number) {
  return `Every ${value} ${value === 1 ? 'minute' : 'minutes'}`;
}

export default function HeartbeatScreen({ close, binding }: { close: () => void; binding?: HeartbeatBinding | null }) {
  /**
   * A binding counts only when it attests exposure.
   *
   * The type already says `exposed` is `true`, but a JavaScript caller is not bound
   * by a type. Anything that does not attest the feature is treated as absent, which
   * is the same direction the copy errs in: rendering Off out of a reading that
   * never claimed to expose heartbeat is the exact failure this screen exists to
   * avoid, and a false "Off" is worse than a missing feature.
   */
  const reported = binding && binding.exposed === true ? binding : null;
  /**
   * The controls start from the reading the screen opened on, and after that they
   * are the user's own edits.
   *
   * They are deliberately not re-seeded from the binding: the reading a screen opens
   * on is the reading its controls start from, and re-seeding on every render would
   * silently undo an edit whenever a caller passed a fresh object of the same shape.
   *
   * They are also never *rendered* while the feature is unavailable: the interval
   * shows an empty field whenever `reported` is null, whatever this state holds. A
   * number on screen is a claim that a device answered with it, and an unavailable
   * device has answered nothing — so the held value waits rather than being shown.
   */
  const [active, setActive] = useState(reported?.active ?? false);
  const [minutes, setMinutes] = useState(reported ? String(reported.intervalMinutes) : '');
  const [saveNote, setSaveNote] = useState('');
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => { back.current?.focus(); }, []);
  const goBack = () => close();
  return <section className="device-subscreen device-heartbeat-screen" aria-label="Heartbeat" onKeyDown={(event) => { if (event.key === 'Escape') goBack(); }}>
    <header className="device-subheader"><button ref={back} onClick={goBack}><BackIcon />Back</button><div className="device-page-title"><HeartbeatIcon /><h1>Heartbeat</h1></div></header>
    <div className="device-subcontent" tabIndex={0}>
      <section className="device-panel">
        <span className="device-section-label">Device state</span>
        <h2>{reported ? reported.active ? 'Active' : 'Off' : 'Unavailable'}</h2>
        <p className="device-note" id={reported ? undefined : 'heartbeat-unavailable-reason'}>{reported ? 'A labelled fixture supplied this reading. It is not a device publication.' : UNAVAILABLE_REASON}</p>
        <div className="device-facts">{reported ? <><span>{reported.active ? 'Active' : 'Off'}</span><span>{everyMinutes(reported.intervalMinutes)}</span><span>Synthetic fixture</span></> : <><span>Feature not exposed</span><span>No state reported</span></>}</div>
      </section>
      <section className="device-panel">
        <span className="device-section-label">Schedule</span>
        <div className="device-history-setting"><h2>Active</h2>{reported ? <button role="switch" aria-checked={active} onClick={() => setActive(!active)}>{active ? 'On' : 'Off'}</button> : <button disabled aria-describedby="heartbeat-unavailable-reason">Unknown</button>}</div>
        <div className="device-history-setting heartbeat-interval-field"><label className="device-section-label" htmlFor="heartbeat-interval">Interval in minutes</label><input id="heartbeat-interval" inputMode="numeric" value={reported ? minutes : ''} disabled={!reported} placeholder={reported ? undefined : 'Unknown'} aria-describedby={reported ? undefined : 'heartbeat-unavailable-reason'} onChange={(event) => setMinutes(event.target.value)} /></div>
        <div className="device-actions"><button className="device-action-primary" disabled={!reported} aria-describedby={reported ? undefined : 'heartbeat-unavailable-reason'} onClick={() => setSaveNote(FIXTURE_NOTE)}>Save</button></div>
        {reported && <p className="device-note">The fixture enables these controls so the arrangement can be reviewed. Nothing chosen here is applied to a device.</p>}
        {saveNote && <p className="device-note" role="status">{saveNote}</p>}
      </section>
      <section className="device-panel">
        <span className="device-section-label">Unavailable is not Off</span>
        <p>Unavailable means the device does not expose the heartbeat feature, so it has never answered the question. There is no state to report, and none is invented.</p>
        <p>Off means the device exposes the feature and answered that its heartbeat is not running. Only a device that answers can be Off, and this screen never fills that silence in.</p>
      </section>
    </div>
  </section>;
}
