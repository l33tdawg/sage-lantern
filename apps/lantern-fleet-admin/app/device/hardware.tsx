'use client';

/**
 * Hardware: what the device has actually reported about itself, and nothing more.
 *
 * The reference composition for this destination is a selectable overview with a
 * detail pane. This file keeps that relationship and drops the controls, because
 * there is nothing here to control: the device schema has no pin map, no pin mode
 * and no write permission, so a pin UI would have to invent both the numbering and
 * the effect of a write. The sentence at the top of the screen says controls are not
 * available, and the code below never renders a pin, a mode or a write.
 *
 * Everything shown comes from the validated `DeviceStatus` and only from it:
 * microphone presence and audio readiness, radio detail, the services the device
 * published, and the optional measurements it may have taken. Three rules follow.
 *
 *  - **Unknown stays unknown.** A field the device did not send reads as not
 *    reported, never as zero, false or "not present". `status === null` is the
 *    extreme case: the device has not reported at all, so every item on this screen
 *    reads unknown rather than empty or healthy.
 *  - **Current is not last-known.** A reading outlives the transport that carried
 *    it, so the screen distinguishes the device's current word from its last one and
 *    says so in as many words. A value offered as current when nothing is reporting
 *    is the same failure as "not present" for hardware nobody asked about.
 *  - **Readiness is not capture.** `audio.ready` describes a path that could record;
 *    only `capture_verified` says the device verified that a microphone really
 *    captured something. Until it does, the screen says capture is not verified.
 *  - **Service values are quoted, not translated.** The record is a map of strings
 *    the device published, so "active" stays "active" here; renaming it "Running"
 *    would put this screen's words in the device's mouth.
 */
import { useEffect, useRef, useState } from 'react';
import type { DeviceStatus } from '../../lib/device-state';
import { BackIcon, HardwareIcon } from './icons';

/** The one sentence this screen must never drop, whatever the device reports. */
const CONTROLS_UNAVAILABLE = 'Hardware controls are not available on this device.';

/** What an absent field reads as. Absence is a state, so it gets a word, not a zero. */
const NOT_REPORTED = 'Not reported';

type HardwareItemId = 'microphone' | 'radio' | 'services' | 'measurements';

/** The overview, in reading order: the parts of the device a person can point at. */
const ITEMS: { id: HardwareItemId; label: string }[] = [
  { id: 'microphone', label: 'Microphone' },
  { id: 'radio', label: 'Radio' },
  { id: 'services', label: 'Services' },
  { id: 'measurements', label: 'Measurements' },
];

/**
 * A boolean the device may not have answered at all.
 *
 * `false` is the device saying no and `undefined` is the device saying nothing, so
 * the two cannot share a word: collapsing them is how a screen ends up reporting
 * "not present" for hardware nobody has asked about yet.
 */
function tri(value: boolean | undefined, yes: string, no: string) {
  if (value === true) return yes;
  if (value === false) return no;
  return NOT_REPORTED;
}

/**
 * Uptime as days, hours and minutes.
 *
 * Seconds are left out on purpose: they move constantly, and a long-running device
 * should not read as "18743 minutes" or invite someone to watch a digit tick. A
 * reading that is absent, or negative and therefore not a duration at all, stays
 * unknown rather than being rounded into a number the device did not report.
 */
function uptimeLabel(seconds: number | undefined) {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return NOT_REPORTED;
  const total = Math.floor(seconds / 60);
  const days = Math.floor(total / 1440);
  const hours = Math.floor((total % 1440) / 60);
  const minutes = total % 60;
  const parts: string[] = [];
  if (days) parts.push(`${days} ${days === 1 ? 'day' : 'days'}`);
  if (hours) parts.push(`${hours} ${hours === 1 ? 'hour' : 'hours'}`);
  if (minutes) parts.push(`${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`);
  return parts.length ? parts.join(' ') : 'Less than a minute';
}

/** Bytes in the largest unit that keeps the number readable, or null when unknown. */
function readableBytes(value: number | undefined): string | null {
  if (value == null || !Number.isFinite(value) || value < 0) return null;
  if (value >= 1073741824) return `${(value / 1073741824).toFixed(1)} GiB`;
  if (value >= 1048576) return `${(value / 1048576).toFixed(1)} MiB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KiB`;
  return `${Math.round(value)} B`;
}

/**
 * Storage as free of total.
 *
 * The two figures are reported separately, so one of them can arrive without the
 * other. The pair is only stated as a pair when both are known; otherwise the screen
 * names the half it has and says the other was not reported, which is the only
 * wording that does not imply a total the device never sent.
 */
function storageLabel(free: number | undefined, total: number | undefined) {
  const freeLabel = readableBytes(free);
  const totalLabel = readableBytes(total);
  if (freeLabel && totalLabel) return `${freeLabel} free of ${totalLabel}`;
  if (freeLabel) return `${freeLabel} free; total not reported`;
  if (totalLabel) return `${totalLabel} total; free space not reported`;
  return NOT_REPORTED;
}

/** Millicelsius as degrees, or unknown. A null sensor is not zero degrees. */
function temperatureLabel(millicelsius: number | null | undefined) {
  if (millicelsius == null || !Number.isFinite(millicelsius)) return NOT_REPORTED;
  return `${(millicelsius / 1000).toFixed(1)} °C`;
}

export default function HardwareScreen({ close, status, connected }: { close: () => void; status: DeviceStatus | null; connected: boolean }) {
  const [selected, setSelected] = useState<HardwareItemId>(ITEMS[0].id);
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => { back.current?.focus(); }, []);
  const item = ITEMS.find((entry) => entry.id === selected) ?? ITEMS[0];
  /**
   * Whether what follows is the device's current word or its last one.
   *
   * `status` survives a dropped transport: the poll keeps the last reading so a
   * reconnect does not have to re-report everything, and only `connected` says
   * whether anything is still reporting. `status` alone therefore cannot tell the
   * two apart, and reading it as current is the one thing this screen's own rules
   * forbid — the same reading, still true when it was taken, offered as what the
   * device says now. The values stay on screen, because they are real and were
   * really reported; what changes is what the screen claims about them.
   */
  const lastReported = !!status && !connected;
  const reportedNote = lastReported ? ' · last reported' : '';
  /**
   * The one-line state each overview button carries, so the list answers "what is
   * this device" before anything is opened. A missing `status` is answered first:
   * with no reading there is nothing to summarise, and every item is unknown.
   */
  const summary = (id: HardwareItemId) => {
    if (!status) return NOT_REPORTED;
    if (id === 'microphone') {
      if (!status.audio.present) return 'Not present';
      if (status.audio.ready === true) return 'Present, audio ready';
      if (status.audio.ready === false) return 'Present, audio not ready';
      return 'Present, readiness not reported';
    }
    if (id === 'radio') {
      if (!status.radio.present) return 'Not present';
      return status.radio.name ? `Present, ${status.radio.name}` : 'Present, name not reported';
    }
    if (id === 'services') {
      const published = Object.keys(status.services).length;
      return published ? `${published} published` : 'None published';
    }
    // Count exactly what the detail formatters can state, by asking them.
    //
    // Counting non-null values instead let a rejected reading be reported twice: a
    // finite negative uptime or storage is not a measurement the screen will render
    // (the detail says Not reported), so the overview claiming "2 of 3 reported"
    // asserted readings the detail refused. Zero is a real reading and stays one —
    // "Less than a minute" and "0 B" are answers, not absences — and a temperature
    // below zero is a measurement too, so this asks each formatter rather than
    // testing the numbers itself.
    const known = [
      uptimeLabel(status.uptime_seconds),
      temperatureLabel(status.temperature_millicelsius),
      storageLabel(status.storage_free_bytes, status.storage_total_bytes),
    ].filter((value) => value !== NOT_REPORTED).length;
    return `${known} of 3 reported`;
  };
  return <section className="device-subscreen device-hardware-screen" aria-label="Hardware" onKeyDown={(event) => { if (event.key === 'Escape') close(); }}>
    <header className="device-subheader"><button ref={back} onClick={close}><BackIcon />Back</button><div className="device-page-title"><HardwareIcon /><h1>Hardware</h1></div></header>
    <div className="device-subcontent" tabIndex={0}>
      <section className="device-panel"><span className="device-section-label">Controls</span><p>{CONTROLS_UNAVAILABLE}</p></section>
      {!status && <p role="status">The device has not reported yet. Every item below is unknown, not empty and not zero.</p>}
      {lastReported && <p role="status">The device is not reporting. These are the last values it sent, and they may be out of date.</p>}
      {/* Overview and detail are one column: the buttons wrap above the detail
          panel, and the detail follows them in the same scroll container, so the
          selection/detail relationship survives a portrait screen without a second
          pane. The selected button is marked for assistive technology with
          `aria-current` and for the eye with the emphasised button style, because
          no existing rule reaches a plain button inside this panel and inventing a
          class or editing the stylesheet is outside this screen's scope. */}
      <section className="device-panel">
        <span className="device-section-label">{`Overview${reportedNote}`}</span>
        <ul className="device-actions" aria-label="Hardware overview">
          {ITEMS.map((entry) => {
            const showing = entry.id === item.id;
            return <li key={entry.id}><button className={showing ? 'device-action-primary' : undefined} aria-current={showing ? 'true' : undefined} onClick={() => setSelected(entry.id)}>{entry.label}: {summary(entry.id)}</button></li>;
          })}
        </ul>
      </section>
      <section className="device-panel">
        <span className="device-section-label">{`Selected item${reportedNote}`}</span>
        <h2>{item.label}</h2>
        {item.id === 'microphone' && <>
          <p><span className="device-section-label">Microphone:</span> {tri(status?.audio.present, 'Present', 'Not present')}</p>
          <p><span className="device-section-label">Audio ready:</span> {tri(status?.audio.ready, 'Ready', 'Not ready')}</p>
          <p><span className="device-section-label">Push-to-talk ready:</span> {tri(status?.audio.ptt_ready, 'Ready', 'Not ready')}</p>
          <p><span className="device-section-label">Physical capture:</span> {status?.audio.capture_verified === true ? 'Verified' : 'Not verified'}</p>
          <p className="device-note">Readiness is not proof of capture. {status?.audio.capture_verified === true ? 'The device reports a verified capture.' : 'The device has not verified that a microphone captured anything.'}</p>
          <p className="device-note">Reported detail: {status?.audio.detail || NOT_REPORTED}</p>
        </>}
        {item.id === 'radio' && <>
          <p><span className="device-section-label">Radio:</span> {tri(status?.radio.present, 'Present', 'Not present')}</p>
          <p><span className="device-section-label">Name:</span> {status?.radio.name || NOT_REPORTED}</p>
          <p><span className="device-section-label">Frequency:</span> {status?.radio.frequency_mhz != null ? `${status.radio.frequency_mhz} MHz` : NOT_REPORTED}</p>
          <p><span className="device-section-label">Firmware:</span> {status?.radio.firmware || NOT_REPORTED}</p>
          <p className="device-note">Reported detail: {status?.radio.detail || NOT_REPORTED}</p>
        </>}
        {item.id === 'services' && (status && Object.keys(status.services).length ? <>
          {Object.entries(status.services).map(([name, state]) => <p key={name}><span className="device-section-label">{name}:</span> {state}</p>)}
          <p className="device-note">Names and values are printed exactly as the device published them. This screen does not translate them.</p>
        </> : <p>{status ? 'The device published no services.' : NOT_REPORTED}</p>)}
        {item.id === 'measurements' && <>
          <p><span className="device-section-label">Uptime:</span> {uptimeLabel(status?.uptime_seconds)}</p>
          <p><span className="device-section-label">Temperature:</span> {temperatureLabel(status?.temperature_millicelsius)}</p>
          <p><span className="device-section-label">Storage:</span> {storageLabel(status?.storage_free_bytes, status?.storage_total_bytes)}</p>
          <p className="device-note">Measurements are optional. A device that reports none leaves them unknown, and a missing measurement is not zero.</p>
        </>}
      </section>
    </div>
  </section>;
}
