/**
 * Ember — integrated Screen preview for the Home 2a wiring (disconnected).
 *
 * This renders the **real** `app/device/screen.tsx`: the real device status
 * pipeline, the real single `TalkControls` capture owner, the real Home, and the
 * real New chat reset path. It is not a mockup and not a reimplementation —
 * esbuild bundles the submitted source, so what appears in the browser is the
 * reviewed integration.
 *
 * What is synthetic is the *device service*. `window.fetch` is replaced before
 * anything renders, and it answers only the two loopback endpoints the device
 * app talks to (`127.0.0.1:3199/status` and `127.0.0.1:3197/*`) from an
 * in-page state machine, plus the camera preview endpoint in the device's own
 * length-prefixed JPEG framing. **Every other origin is refused**, so this preview
 * cannot reach a live service, a LAN peer or the public internet even by
 * accident, and the refusal is recorded rather than silently swallowed.
 *
 * Synthetic responses are visible as synthetic: the banner above the panel says
 * so, and `?log=1` shows the request trail. Nothing here claims a live device,
 * a real microphone, a model or a saved conversation.
 *
 * Query parameters:
 *   scenario    ready | complete | error | listening-physical | no-mic | stale | offline
 *               The status the synthetic service publishes. `listening-physical`
 *               is the case the contract calls out: the device is recording from
 *               the side button, so the screen may report it but must not offer
 *               a GUI stop.
 *   reset       ok | refuse | fail
 *               The New chat reset outcome. `refuse` is the 409 the real runtime
 *               returns when the device is busy; `fail` is a transport failure.
 *               Neither may be followed by a recording.
 *   startDelay  milliseconds before `/start` answers (default 0). A non-zero
 *               value is the asynchronous start a releasing gesture must be able
 *               to withdraw.
 *   clearDelay  milliseconds the queued clear worker holds the recording slot
 *               after acknowledging it (default 1200). During that window
 *               `/start` is refused with 409 `voice_busy`, exactly as the real
 *               `VoiceControl` does — the acknowledgement is not proof that a
 *               new capture may begin.
 *   saving      on | off (default off). With saving on, the exchanges a clear
 *               removed from the active thread are readable as saved history,
 *               which is how the preview shows that clearing the active thread
 *               and keeping history are two different things.
 *   photos      none | three | one | off | unsupported (default none). What the
 *               synthetic device retains. `off` keeps the photos and turns saving
 *               off, `unsupported` withdraws photo capability, `one` is the
 *               single-photo boundary case. Every retained record is served with
 *               real validated JPEG bytes through `history/photo`.
 *   photoFails  1 makes the first retained record's image read fail, so the
 *               missing-photo state is exercised with a record that is still
 *               listed rather than one that vanished.
 *   fault       none | disconnect-after-start | session-change | session-change-silent.
 *               The first three fire on a status read; the silent one takes the
 *               device over at dispatch so no poll can observe it first. They fire
 *               only while a `/start` is dispatched and unacknowledged, which is
 *               the window the reproduced capture-adoption defects live in: the
 *               first fails the status reads (the screen goes to Reconnecting),
 *               the second publishes a session this client never asked for.
 *   owner       1 renders `TalkControls` directly with no parent at all. The
 *               integrated preview renders `DeviceScreen`, which supplies both an
 *               admission callback and its own epoch fence over `/status`; those
 *               are separate defences and they mask whether the capture owner's
 *               own fence works. This mount has neither, so a refusal under it is
 *               the owner's own judgment and nothing else's.
 *   statusDelay milliseconds before `/status` answers (default 0). A non-zero
 *               value puts a reply in flight long enough for the authority it was
 *               built under to be replaced before it lands.
 *   tokenSwapAfter <n> replaces the device token when the n-th status request
 *               arrives, before its (delayed) reply is delivered. `session_ref`
 *               is derived from the epoch *and* the token, so the replacement
 *               necessarily changes the context: the reply is obsolete by the
 *               time it arrives, which is what an auth session replacement does
 *               to a request already in flight.
 *   typedDelay  milliseconds before `/question/start` answers (default 0), so a
 *               typed review can be caught by the same rotation a capture can.
 *   stateDelay  milliseconds before `/conversation/state` answers (default 0).
 *               The real runtime answers every protected POST with the context it
 *               captured at admission, so these replies are stamped too.
 *   hold        1 freezes the capture owner's reading at the first one that
 *               lands, which is the reachable window in which the broker has
 *               already rotated and the next poll has not arrived yet.
 *   attach      1 hands the capture owner a real attached photo, so the
 *               photo-saving policy decides whether original bytes travel.
 *   historyStamp missing|malformed answers the photo-policy read with no
 *               admission stamp, or with one that cannot be parsed. Both are
 *               replies the real runtime would not send; they exist to prove the
 *               client refuses a policy it cannot attribute.
 *   frame       1 scales the portrait panel to fit a desktop window (presentation
 *               only; the panel is 720×1560 at scale 1 either way)
 *   log         1 shows the transport log
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import DeviceScreen from '../../apps/lantern-fleet-admin/app/device/screen';
import TalkControls, { type TalkController, type TalkOwnership } from '../../apps/lantern-fleet-admin/app/device/talk';
import { conversationRequest } from '../../apps/lantern-fleet-admin/lib/conversation-threads';
import { parseDeviceStatus, type DeviceStatus } from '../../apps/lantern-fleet-admin/lib/device-state';
import type { PhotoAttachment } from '../../apps/lantern-fleet-admin/lib/photo-preview';
import { establishedSessionContext, getDeviceToken, observeSessionContext, setDeviceToken, subscribeDeviceSession } from '../../apps/lantern-fleet-admin/lib/device-session';
import './fixture-shell.css';
import './screen-preview-frame.css';

/**
 * The authority seam, exposed for the driver only.
 *
 * Retained protected content has to be shown to leave when the *authority* moves,
 * and two of the three ways it moves — a replaced token, and a reading another
 * surface could not compare — happen outside this screen's own poll, so a driver
 * that only clicked buttons could not reach them. This is the real module, not a
 * stub: `setDeviceToken` and `observeSessionContext` are the same functions the
 * screen and every other protected surface call, so what the driver measures is
 * the product's invalidation path and not a fixture's imitation of it.
 *
 * Synthetic like the rest of this preview: it contacts nothing, and nothing in
 * `apps/` reads it.
 */
(window as unknown as Record<string, unknown>).fixtureAuthority = {
  getDeviceToken, setDeviceToken, observeSessionContext, establishedSessionContext,
};

const PANEL_WIDTH = 720;
const PANEL_HEIGHT = 1560;
const QUESTION = 'How long does stored drinking water keep?';
const ANSWER = 'Sealed and cool, about six months; once opened, three days.';

type Scenario = 'ready' | 'complete' | 'error' | 'streaming' | 'listening-physical' | 'no-mic' | 'stale' | 'offline';
type ResetMode = 'ok' | 'refuse' | 'fail';
type Mic = 'on' | 'unready' | 'off';
/**
 * Whether the runtime attests the typed lane.
 *
 * The composer is required to state a visible reason when it cannot open, and
 * every other reading in this preview hardcodes `typed_ready: true`, which made
 * that state unreachable rather than merely untested.
 */
type Typed = 'on' | 'off';
/**
 * What the synthetic device retains. `off` keeps the photos and turns saving
 * off, because turning saving off must not read as an empty gallery.
 */
/** Transport faults injected while a delayed start acknowledgement is in flight. */
type Fault = 'none' | 'disconnect-after-start' | 'session-change' | 'session-change-silent';
/**
 * Whether the synthetic broker publishes a session context.
 *
 * `off` is the legacy unlocked device: no context anywhere, exactly what the
 * frozen records were produced against, so their scenarios stay comparable.
 */
type ContextMode = 'off' | 'on';
/**
 * Where the epoch rotates in a context-mode run.
 *
 * `status` and `status-held` are *reachable* rotations: the broker publishes a
 * new context for the same device token, on the second reading or once this
 * client holds a capture. `status-inflight` is the same reachable rotation
 * landing inside a pending `/start`: the request was dispatched under the old
 * epoch and admitted under the new one, so the delayed reply and every later
 * reading agree with each other and disagree only with the authority the user
 * actually activated — the case a reply cannot be asked to witness about itself.
 * `typed-inflight` and `state-inflight` are the same reachable rotation landing
 * inside a *read* — a typed review being opened, a conversation being read — so
 * the reply is built under the epoch that held when it was sent and delivered
 * after the epoch moved. `confirm`, `keepalive` and `stop` answer 200 with a context
 * the client never admitted under — a reply the real runtime would refuse to
 * send, used to prove the client's own gate rather than the server's.
 */
type Rotation = 'none' | 'status' | 'status-held' | 'status-inflight' | 'typed-inflight' | 'state-inflight' | 'photo-inflight' | 'confirm' | 'keepalive' | 'stop';

/**
 * Whether the photo-policy read carries the admission stamp the runtime attaches.
 *
 * `ok` is the contract: every successful protected reply is stamped through
 * `respond()`. `missing` and `malformed` are replies a locked device would never
 * send, and they are here to prove the *client* refuses a photo policy it cannot
 * attribute instead of trusting the server to be the only gate.
 */
type HistoryStamp = 'ok' | 'missing' | 'malformed';

const uuid = () => crypto.randomUUID();
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

/**
 * Synthetic retained photo bytes for the gallery scenarios.
 *
 * These are real, valid JPEGs rather than invented strings: `parseHistoryPhoto`
 * checks the JPEG magic, the base64 alphabet, the padding and a byte ceiling,
 * and `prepareHistoryPhoto` decodes them through an Image and re-encodes at the
 * device's own size policy. A placeholder would pass none of that, and the whole
 * point of the gallery is that a filename is not an image.
 */
const PHOTO_QUESTIONS: Record<string, string> = {
  reef: 'What kind of coral is this?',
  desk: 'What is on this desk?',
  leaf: 'Is this leaf healthy?',
};

const RETAINED_PHOTOS: Record<string, string> = {
  reef: '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCABIAGADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDjKKKfLI8sjySuzyOSzMxyWJ6kn1r3j48ZRTxI4jaMOwjYhiueCRnBx6jJ/M0ygAop8sjyyPJK7PI5LMzHJYnqSfWgSOI2jDsI2IYrngkZwceoyfzNGo9BlFFPlkeWR5JXZ5HJZmY5LE9ST60CGUU8SOI2jDsI2IYrngkZwceoyfzNMoAKKfLI8sjySuzyOSzMxyWJ6kn1plCBhT5QgkcRMzRgnazLtJHYkZOD7ZNMooAeAnlsSzeZkYXbwRzk5z16cY7npjllFFAD5QgkcRMzRgnazLtJHYkZOD7ZNACeWxLN5mRhdvBHOTnPXpxjuemOWUUWHcKfKEEjiJmaME7WZdpI7EjJwfbJplFAh4CeWxLN5mRhdvBHOTnPXpxjuemOWUUUAPlCCRxEzNGCdrMu0kdiRk4Ptk0yiihAwp8sjyyPJK7PI5LMzHJYnqSfWmU+UIJHETM0YJ2sy7SR2JGTg+2TR1H0ASOI2jDsI2IYrngkZwceoyfzNMp4CeWxLN5mRhdvBHOTnPXpxjuemOWUIGPlkeWR5JXZ5HJZmY5LE9ST60CRxG0YdhGxDFc8EjODj1GT+ZolCCRxEzNGCdrMu0kdiRk4Ptk0AJ5bEs3mZGF28Ec5Oc9enGO56Y5Wlg1uMp8sjyyPJK7PI5LMzHJYnqSfWmU+UIJHETM0YJ2sy7SR2JGTg+2TT6h0ASOI2jDsI2IYrngkZwceoyfzNMp4CeWxLN5mRhdvBHOTnPXpxjuemOWUIGPlkeWR5JXZ5HJZmY5LE9ST60ynyhBI4iZmjBO1mXaSOxIycH2yaZQttAe+oUUU+WR5ZHkldnkclmZjksT1JPrQIZRTxI4jaMOwjYhiueCRnBx6jJ/M0ygAop8sjyyPJK7PI5LMzHJYnqSfWgSOI2jDsI2IYrngkZwceoyfzNGo9BlFFPlkeWR5JXZ5HJZmY5LE9ST60CGUU8SOI2jDsI2IYrngkZwceoyfzNMoAKKfLI8sjySuzyOSzMxyWJ6kn1plCBhRRRTAKKKKACiiigAooooAKKKKACiiigD/2Q==',
  desk: '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCABIAGADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDl6KKdJI8sjSSszyOSzMxyST1JNfJn0I2inCRxG0YZhGxDFc8EjODj2yfzNNoAKKdJI8sjSSszyOSzMxyST1JNAkcRtGGYRsQxXPBIzg49sn8zQA2iinSSPLI0krM8jkszMckk9STQA2inCRxG0YZhGxDFc8EjODj2yfzNNoAKKdJI8sjSSszyOSzMxyST1JNNoAKdIEEjCJmaME7SwwSO2Rk4/M02igBwCeWxLN5mRgY4I5zzn6du56Y5bRRQA6QIJGETM0YJ2lhgkdsjJx+ZoATy2JZvMyMDHBHOec/Tt3PTHLaKACnSBBIwiZmjBO0sMEjtkZOPzNNooAcAnlsSzeZkYGOCOc85+nbuemOW0UUAOkCCRhEzNGCdpYYJHbIycfmabRRQAU6SR5ZGklZnkclmZjkknqSabTpAgkYRMzRgnaWGCR2yMnH5mgAEjiNowzCNiGK54JGcHHtk/mabTgE8tiWbzMjAxwRznnP07dz0xy2gB0kjyyNJKzPI5LMzHJJPUk0CRxG0YZhGxDFc8EjODj2yfzNEgQSMImZowTtLDBI7ZGTj8zQAnlsSzeZkYGOCOc85+nbuemOQBtOkkeWRpJWZ5HJZmY5JJ6kmm06QIJGETM0YJ2lhgkdsjJx+ZoABI4jaMMwjYhiueCRnBx7ZP5mm04BPLYlm8zIwMcEc55z9O3c9MctoAdJI8sjSSszyOSzMxyST1JNNp0gQSMImZowTtLDBI7ZGTj8zTaACiinSSPLI0krM8jkszMckk9STQA2inCRxG0YZhGxDFc8EjODj2yfzNNoAKKdJI8sjSSszyOSzMxyST1JNAkcRtGGYRsQxXPBIzg49sn8zQA2iinSSPLI0krM8jkszMckk9STQA2inCRxG0YZhGxDFc8EjODj2yfzNNoAKKdJI8sjSSszyOSzMxyST1JNNoAKKKKYBRRRQAUUUUAFFFFABRRRQAUUUUAf/2Q==',
  leaf: '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCABIAGADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDlKKKdJI8sjySuzyOSzMxyST1JNcZ8uNopwkcRtGHYRsQxXPBIzg49sn8zTaACinSSPLI8krs8jkszMckk9STQJHEbRh2EbEMVzwSM4OPbJ/M0D0G0UU6SR5ZHkldnkclmZjkknqSaBDaKcJHEbRh2EbEMVzwSM4OPbJ/M02gAop0kjyyPJK7PI5LMzHJJPUk02gAp0gQSOImZowTtLLtJHYkZOPzNNooAcAnlsSzeZkYG3gjnJzn6du56Y5bRRQA6QIJHETM0YJ2ll2kjsSMnH5mgBPLYlm8zIwNvBHOTnP07dz0xy2igdwp0gQSOImZowTtLLtJHYkZOPzNNooEOATy2JZvMyMDbwRzk5z9O3c9MctoooAdIEEjiJmaME7Sy7SR2JGTj8zTaKKACnSSPLI8krs8jkszMckk9STTadIEEjiJmaME7Sy7SR2JGTj8zQPoAkcRtGHYRsQxXPBIzg49sn8zTacAnlsSzeZkYG3gjnJzn6du56Y5bQDHSSPLI8krs8jkszMckk9STQJHEbRh2EbEMVzwSM4OPbJ/M0SBBI4iZmjBO0su0kdiRk4/M0AJ5bEs3mZGBt4I5yc5+nbuemOTSwa3G06SR5ZHkldnkclmZjkknqSabTpAgkcRMzRgnaWXaSOxIycfmaA6AJHEbRh2EbEMVzwSM4OPbJ/M02nAJ5bEs3mZGBt4I5yc5+nbuemOW0Ax0kjyyPJK7PI5LMzHJJPUk02nSBBI4iZmjBO0su0kdiRk4/M02hA99Qoop0kjyyPJK7PI5LMzHJJPUk0CG0U4SOI2jDsI2IYrngkZwce2T+ZptABRTpJHlkeSV2eRyWZmOSSepJoEjiNow7CNiGK54JGcHHtk/maB6DaKKdJI8sjySuzyOSzMxyST1JNAhtFOEjiNow7CNiGK54JGcHHtk/mabQAUU6SR5ZHkldnkclmZjkknqSabQAUUUUwCiiigAooooAKKKKACiiigAooooA//Z',
};

const PRIOR_EXCHANGE = {
  transcript: 'What should I check before a long walk?',
  response: 'Water, a charged radio and a dry layer. Tell someone your route.',
};
/**
 * A second saved conversation, so selecting a past chat is provable.
 *
 * With one thread `conversation/select` can only ever be a stub, and the one
 * acceptance question the chat surface cannot otherwise answer is whether
 * choosing the SECOND of two past chats loads that chat rather than the first or
 * a stale one. The wording is deliberately unlike the other thread's, so a
 * mix-up shows in the rendered text instead of being inferred from a count.
 */
const SECOND_THREAD_EXCHANGE = {
  transcript: 'Which saved chat is this one?',
  response: 'This is the second saved chat, the older of the two.',
};
const PARTIAL_ANSWER = 'Sealed and cool, about six months;';

/**
 * The synthetic device service.
 *
 * Deliberately a small state machine rather than a fixed script: the phase it
 * publishes is whatever the last command left behind, so the screen under test
 * reacts to a service that behaves like the real one instead of to canned JSON
 * per screenshot.
 */
function createService(scenario: Scenario, reset: ResetMode, startDelay: number, mic: Mic, clearDelay = 1200, saving = false, photoMode: PhotoMode = 'none', photoFails = false, fault: Fault = 'none', contextMode: ContextMode = 'off', rotation: Rotation = 'none', confirmDelay = 0, tokenSwapAfter = 0, typedDelay = 0, stateDelay = 0, historyStamp: HistoryStamp = 'ok', interruptAfter = 0, grow = false, typedReady = true) {
  const log: string[] = [];
  const blocked: string[] = [];
  /**
   * The broker's noncredential session context.
   *
   * The real one is derived from the access epoch *and* the device token, so it
   * is stable across every voice publication and changes only when the epoch
   * rotates. `contextEpoch` is the half a rotation moves and `sessionRef` stays
   * put, so a rotation is visible without the token changing — the case the
   * expectation counter could not see.
   */
  let sessionRef = uuid().replace(/-/g, '') + uuid().replace(/-/g, '');
  const FIRST_EPOCH = 'a'.repeat(32);
  const ROTATED_EPOCH = 'c'.repeat(32);
  let contextEpoch = FIRST_EPOCH;
  const contextAt = (epoch: string) => ({ schema: 'sage.lantern.session-context.v1', epoch, session_ref: sessionRef });
  /** The broker's current context: what `/status` publishes. */
  const sessionContext = () => contextAt(contextEpoch);
  const rotatedAtStatus = { done: false };
  const rotatedDuringStart = { done: false };
  const rotatedDuringPhotoRead = { done: false };
  /**
   * The epoch moving inside a pending `/start`.
   *
   * Reachable and unremarkable: the epoch rotates on lock, owner change or
   * revocation, and a request already on the wire is admitted after it. The
   * request was issued under the old authority; the reply is built under the new
   * one, and so is every reading after it. Nothing the client receives says the
   * authority changed — only what it captured before dispatch can.
   */
  const rotateDuringStart = () => {
    if (rotatedDuringStart.done) return;
    rotatedDuringStart.done = true;
    contextEpoch = ROTATED_EPOCH;
    record('epoch rotated: the broker moved to a new context while /start was in flight (same device token)');
  };
  /**
   * The epoch moving while a *read* is in flight.
   *
   * The reply is built first, under the authority the request arrived with, and
   * the epoch moves after it — which is what a rotation during the round trip
   * does to it. The reply is then delivered late, so the client's own reading has
   * already moved on: true when written, obsolete when it lands.
   */
  const rotateDuringRead = (point: 'typed-inflight' | 'state-inflight') => {
    if (rotation !== point || rotatedDuringStart.done) return;
    rotatedDuringStart.done = true;
    contextEpoch = ROTATED_EPOCH;
    record(`epoch rotated: the broker moved to a new context while /${point === 'typed-inflight' ? 'question/start' : 'conversation/state'} was in flight (same device token)`);
  };
  /**
   * The broker moving *before* a protected read is answered.
   *
   * Reachable on lock, owner change or revocation: the epoch has already moved
   * when the read arrives, so the reply is admitted and stamped under the new
   * context while the reading this client holds is still the one taken before it.
   * `/history/list` is where the photo-saving policy comes from, so a client that
   * uses that policy without attributing the reply decides how many original
   * bytes travel on the strength of an authority it was never admitted under.
   * `hold=1` keeps the client's reading at the old context: that window is the
   * state this exists to be tested in.
   */
  const rotateBeforePhotoRead = () => {
    if (rotation !== 'photo-inflight' || rotatedDuringPhotoRead.done) return;
    rotatedDuringPhotoRead.done = true;
    contextEpoch = ROTATED_EPOCH;
    record('epoch rotated before /history/list was answered: the policy this reply carries belongs to the new epoch (same device token)');
  };
  const rotatedOnDemand = { done: false };
  /**
   * Move the epoch when the driver asks, once.
   *
   * The same reachable rotation — lock, owner change, revocation — without a
   * timing race: the driver dispatches a read, moves the epoch while it is in
   * flight, waits for its own status poll to publish the new context, and only
   * then lets the reply land. Nothing here is a reply the real runtime would
   * refuse to send.
   */
  const rotateNow = () => {
    if (rotatedOnDemand.done) return;
    rotatedOnDemand.done = true;
    contextEpoch = ROTATED_EPOCH;
    record('epoch rotated on the device: the broker publishes a new context for the same device token');
  };
  /**
   * Replace the device token while a reply built under the old one is in flight.
   *
   * `session_ref` is derived from the epoch *and* the token, so an auth session
   * replacement necessarily produces a context that differs from the one the
   * in-flight reply carries. `setDeviceToken` is the app's own path — the broker
   * session Unit C installs calls it — and it resets the shared observer rather
   * than announcing a rotation, because there is no rotation to announce: the
   * authority the observation was taken under is simply gone.
   */
  const swapDeviceToken = () => {
    if (state.tokenSwapped) return;
    state.tokenSwapped = true;
    sessionRef = uuid().replace(/-/g, '') + uuid().replace(/-/g, '');
    setDeviceToken(('previewSwapped' + uuid().replace(/-/g, '')).slice(0, 43));
    record('device token replaced: a new auth session, so every context taken under the old one is obsolete');
  };
  /**
   * Rotate the broker's published context while the device token stays the same.
   *
   * This is a reachable runtime state — the epoch moves on lock, owner change or
   * revocation — and it is the whole reason the fence cannot be built from
   * `voice.session_id`.
   */
  const rotateStatus = () => {
    // Only once the client actually holds the capture: rotating earlier would
    // exercise the admission gate rather than the rendering fence.
    if (rotatedAtStatus.done) return;
    if (rotation === 'status') {
      // After the client has been given one reading under the first context, so
      // the protected content is genuinely on screen when the epoch moves.
      if (state.statusReads < 2) return;
    } else if (rotation === 'status-held') {
      // Or once this client holds a confirmed capture.
      if (!state.confirmed) return;
    } else return;
    rotatedAtStatus.done = true;
    contextEpoch = ROTATED_EPOCH;
    record('epoch rotated: /status publishes a new context for the same device token');
  };
  /**
   * A reply the real runtime would refuse to send.
   *
   * It answers 200 while carrying a context the client never admitted under.
   * That state is not reachable — the runtime re-checks `context_current` before
   * answering — and it is not meant to be: it exists to prove the *client*
   * refuses such a reply instead of trusting the server to be the only gate.
   */
  const replyContext = (point: Rotation | null) => (point !== null && rotation === point ? contextAt(ROTATED_EPOCH) : sessionContext());
  // `mic=off` is the microphone-removed case: the question service still runs,
  // which is exactly the combination that used to permit a spoken re-ask.
  const audioPresent = scenario !== 'no-mic' && mic !== 'off';
  const audioReady = audioPresent && mic !== 'unready';
  const threadId = uuid();
  const secondThreadId = uuid();
  /**
   * The second thread's record, seeded as saved history rather than as a second
   * active thread: a conversation has exactly one, so selecting this one has to
   * move the active thread rather than add to it.
   */
  const SECOND_THREAD_ITEM: Record<string, unknown> = {
    session_id: '22222222-2222-4222-8222-222222222222',
    thread_id: secondThreadId,
    created_at: new Date(Date.now() - 7_200_000).toISOString(),
    transcript: SECOND_THREAD_EXCHANGE.transcript,
    response: SECOND_THREAD_EXCHANGE.response,
    has_photo: false,
  };
  /**
   * A saved conversation, so a loaded thread and its reading position are real.
   *
   * The real `ActiveConversation.clear()` does **not** delete history: it moves
   * the active conversation to a new, empty thread and leaves saved history
   * alone. The preview models the same split — `activeItems` is the thread on
   * screen, `retiredItems` is what a clear took out of it — so "cleared" and
   * "the turn never happened" cannot be confused.
   */
  const PRIOR_ITEM: Record<string, unknown> = {
    session_id: '11111111-1111-4111-8111-111111111111',
    thread_id: threadId,
    created_at: new Date(Date.now() - 3_600_000).toISOString(),
    transcript: PRIOR_EXCHANGE.transcript,
    response: PRIOR_EXCHANGE.response,
    has_photo: false,
  };
  /** Retained records whose image read fails, filled in as they are built. */
  const photoFailures = new Set<string>();

  /**
   * The retained-photo records this preview serves.
   *
   * `off` keeps the photos and turns saving off: the decisions require that
   * turning saving off preserves existing retained items rather than emptying
   * the gallery, so the two states have to be distinguishable in the fixture.
   */
  function photoRecords() {
    if (photoMode === 'none' || photoMode === 'unsupported') return [];
    const wanted = photoMode === 'one' ? ['reef'] : photoMode === 'off' ? ['reef', 'desk'] : ['reef', 'desk', 'leaf'];
    const records = wanted.filter((key) => RETAINED_PHOTOS[key]).map((key, index) => ({
      session_id: `photo-${key}-0000-4000-8000-00000000000${index}`,
      created_at: new Date(Date.now() - (index + 1) * 600_000).toISOString(),
      transcript: PHOTO_QUESTIONS[key],
      response: 'Saved answer for this photo.',
      has_photo: true,
      thread_id: threadId,
    }));
    // The read that fails is a *listed* record whose bytes are gone, which is the
    // case the missing-photo state exists for — not a record that vanished.
    if (photoFails && records.length) photoFailures.add(records[0].session_id);
    return records;
  }

  const state = {
    scenario,
    phase: scenario === 'complete' ? 'complete'
      : scenario === 'error' ? 'error'
        : scenario === 'streaming' ? 'processing'
          : scenario === 'listening-physical' ? 'listening' : 'idle',
    sessionId: scenario === 'complete' || scenario === 'error' ? uuid() : uuid(),
    ownedSession: null as string | null,
    reviewId: null as string | null,
    inputSource: scenario === 'listening-physical' ? 'physical' as string | null : null as string | null,
    // A device answering a question also publishes the question it is answering,
    // which is what Home shows beside the answer while it streams.
    transcript: scenario === 'complete' || scenario === 'error' || scenario === 'streaming' ? QUESTION : '',
    response: scenario === 'complete' ? ANSWER : scenario === 'streaming' ? PARTIAL_ANSWER : '',
    progress: scenario === 'streaming' ? 'generating' : null as string | null,
    revision: 1,
    /**
     * True while a queued clear still owns the recording slot.
     *
     * The real `VoiceControl.clear()` answers `{"status": "clearing"}` and
     * reserves the slot with `source=clear, phase=queued`; `start()` then refuses
     * with 409 `voice_busy` until the worker finishes. A preview that answers a
     * plain `ok` and lets the next start succeed is testing a device that does
     * not exist, which is exactly what the review reproduced.
     */
    clearQueued: false,
    /** The active thread. A clear replaces it with a new, empty one. */
    threadId,
    /** The active thread's exchanges. */
    activeItems: [PRIOR_ITEM] as Record<string, unknown>[],
    /** The other saved thread. `conversation/select` swaps the two. */
    secondThreadId,
    secondItems: [SECOND_THREAD_ITEM] as Record<string, unknown>[],
    /**
     * Retained records that carry a photo. Each one is a real record with real
     * bytes behind it, removed by `history/delete` like any other saved record.
     */
    photos: photoRecords() as Record<string, unknown>[],
    /** Retained records whose image read fails, to exercise the missing state. */
    photoFailures,
    /** A `/start` request has been received and its acknowledgement is still out. */
    startDispatched: false,
    startAcknowledged: false,
    /** A session this client never asked for, published by the fault below. */
    foreignSessionId: uuid(),
    /** True once the device has actually moved onto that foreign session. */
    foreignAdopted: false,
    /**
     * The session `/start` just opened, and whether the takeover that follows it
     * is still being hidden from status reads.
     *
     * A takeover a status poll can see is not the case the runtime confirmation
     * exists for: the client's own admission check would refuse it first. To model
     * the real residual, the device must keep publishing the session it is about
     * to hand back until that hand-back has landed, and only then admit to the
     * foreign owner. Otherwise the test silently exercises the earlier, weaker
     * defence and its assertions about the runtime are never reached.
     */
    startSessionId: null as string | null,
    takeoverHidden: false,
    /**
     * The epoch the current voice state was published under.
     *
     * A publication is not a rotation: every start, stop, clear and finished
     * answer republishes the block under the same context. Only a rotation makes
     * the block stale, and that is what the status fence above compares.
     */
    voiceContext: contextEpoch,
    /** True once this client's confirmation was accepted: it holds the capture. */
    confirmed: false,
    /**
     * Status reads served, and how many had been served when the delayed start
     * was acknowledged and when a cancel arrived.
     *
     * These two numbers are the difference between *never adopted* and *adopted
     * then cancelled*: if the cancel is sent in the same turn as the
     * acknowledgement, no status read can have happened in between, so the
     * client decided before it took ownership. A late cancel always shows at
     * least one intervening read.
     */
    /** True when the last `/start` carried real image bytes with it. */
    lastStartHadImage: false,
    /**
     * True when the last `/start` carried the *original* photo bytes.
     *
     * This is the release the photo-saving policy gates, and the disclosure a
     * policy read from an unattributed reply would cause: it is recorded
     * separately from the bounded inference image because sending the question's
     * image is not the same act as retaining the original behind it.
     */
    lastStartHadOriginal: false,
    /** True once `/question/start` has been dispatched at all. */
    questionStartDispatched: false,
    /** The typed lane's equivalent of `lastStartHadOriginal`. */
    lastTypedHadOriginal: false,
    /** Photo-policy reads served, so a refusal can be shown to precede dispatch. */
    historyReads: 0,
    statusReads: 0,
    readsAtAck: null as number | null,
    readsAtCancel: null as number | null,
    /** True once the device token has been replaced under a live surface. */
    tokenSwapped: false,
    /** Exchanges a clear removed from the active thread — history, not deleted. */
    // Seeded with the second thread so `history/list` offers two conversations to
    // choose between - and only while saving is on, which is the documented trap.
    retiredItems: [SECOND_THREAD_ITEM] as Record<string, unknown>[],
    /**
     * The question text `/review/submit` actually carried.
     *
     * The log records method and path only, and the answer this preview returns
     * is a fixed string, so without this the driver could prove a submission was
     * dispatched but not that it carried what the keyboard typed.
     */
    submittedQuestion: '',
  };
  // A finished turn is part of the thread it finished in.
  if (scenario === 'complete') {
    state.activeItems.push({
      session_id: state.sessionId, thread_id: state.threadId, created_at: new Date().toISOString(),
      transcript: QUESTION, response: ANSWER, has_photo: false,
    });
  }

  const record = (entry: string) => { log.push(entry); if (log.length > 60) log.shift(); };

  const status = (foreignSession = false, pure = false) => {
    /**
     * A `pure` read reports the voice state without advancing anything.
     *
     * The app polls /voice far more often than /status, and these scripts are
     * counted in reads, so letting a /voice read increment `statusReads` would
     * run every scripted scenario (interruption, growth, rotation, token swap)
     * at a rate no real device produces.
     */
    if (!pure) state.statusReads += 1;
    /**
     * A normal interruption: the device stops answering mid-answer and publishes
     * no further words.
     *
     * The half-answer is then only on the client, which is the case the retention
     * rule exists for — and the case a screen that simply renders the reading
     * cannot cover, because there is nothing left on the wire to render.
     */
    if (!pure && interruptAfter > 0 && state.statusReads > interruptAfter && state.phase === 'processing') {
      state.phase = 'idle';
      state.response = '';
    }
    // Text that actually moves, so released-follow and no-duplication can be
    // measured rather than asserted against a frozen string. Each read appends a
    // numbered sentence, so a duplicated or replayed reading shows up as a number
    // appearing twice rather than as a length that merely looks wrong.
    if (!pure && grow && state.phase === 'processing') {
      state.response = `${state.response} Sentence ${state.statusReads} of the growing answer, which exists so the reader has something that moves.`;
    }
    if (!pure) rotateStatus();
    const context = sessionContext();
    // The reply is built from the authority this request was issued under and
    // delivered after the transport delay. An auth replacement inside that window
    // is what makes it obsolete by the time it lands.
    if (!pure && tokenSwapAfter > 0 && state.statusReads === tokenSwapAfter) swapDeviceToken();
    /**
     * The voice block belongs to the context the *voice state* was published
     * under.
     *
     * The real status server fences it: unless the state's own context parses and
     * equals the broker's current one, the whole block is replaced with the empty
     * unavailable shape. Modelling that here is what makes a rotation visible as
     * protected content disappearing, rather than as text that stays on screen
     * under a new authority.
     */
    const fencedVoice = contextMode === 'on' && state.voiceContext !== contextEpoch;
    const voice = {
      phase: state.phase,
      session_id: foreignSession
        ? state.foreignSessionId
        // While the takeover is hidden, the device publishes the session it is
        // about to hand back. A poll therefore cannot reveal the foreign owner
        // before the client has taken delivery of its own credentials.
        : (state.takeoverHidden && state.startDispatched && !state.startAcknowledged && state.startSessionId)
          ? state.startSessionId
          : state.sessionId,
      transcript: state.transcript,
      response: state.response,
      // The touch lane is the control under review; the microphone cases above
      // are what withdraw it.
      touch_ready: true,
      typed_ready: typedReady,
      // Ask about this photo is gated on the device attesting that a photo can
      // travel with a question. Without this the control is (correctly) refused
      // with a reason, which is its own case — but it is not the gallery flow.
      photo_ready: photoMode !== 'unsupported',
      review_ready: state.phase === 'review',
      review_id: state.reviewId,
      input_source: state.inputSource,
      progress_stage: state.progress,
      ...(contextMode === 'on' ? { session_context: contextAt(state.voiceContext) } : {}),
    };
    return {
      schema: 'sage.lantern.local-status.v1',
      observed_at: new Date(Date.now() - (scenario === 'stale' ? 60_000 : 0)).toISOString(),
      local_voice_ready: audioReady,
      audio: {
        present: audioPresent, detail: 'synthetic preview audio',
        ready: audioReady, capture_verified: false, ptt_ready: true,
      },
      radio: { present: true, name: 'MeshCore USB', detail: 'synthetic preview radio' },
      voice: fencedVoice
        ? { phase: 'unavailable', session_id: null, transcript: '', response: '', detail: '', touch_ready: false, correction_ready: false, review_ready: false, photo_ready: false, has_photo: false, ...(contextMode === 'on' ? { session_context: contextAt(state.voiceContext) } : {}) }
        : voice,
      services: { voice_loop: 'active', ptt: 'active', llm: 'active', personal_sage: 'active' },
      ...(contextMode === 'on' ? { session_context: context } : {}),
    };
  };

  /** Resolve a device command. Returns null for an action this preview does not serve. */
  const command = (action: string, body: Record<string, unknown>): { body: unknown; status?: number; delay?: number } | null => {
    /**
     * Attach the context this reply was admitted under.
     *
     * The real runtime attaches the context it captured at admission to every
     * successful protected reply, and re-checks that it is still current before
     * answering. `point` is the knob that answers with a context the client never
     * admitted under, on purpose.
     */
    const protectedReply = (point: Rotation | null, payload: Record<string, unknown>) => (contextMode === 'on' ? { ...payload, session_context: replyContext(point) } : payload);
    /** A publication of the voice state: same context, new session or phase. */
    const publishedVoice = () => { state.voiceContext = contextEpoch; };
    switch (action) {
      case 'start': {
        state.startDispatched = true;
        state.startAcknowledged = false;
        // A photo question is only a photo question if the bytes travelled: a
        // session id or a filename is not a vision payload.
        state.lastStartHadImage = typeof body.image_base64 === 'string' && body.image_base64.startsWith('/9j/');
        state.lastStartHadOriginal = typeof body.original_base64 === 'string' && body.original_base64.startsWith('/9j/');
        // The queued clear owns the slot until its worker finishes. This is the
        // refusal the real runtime returns, and the reason a start may not be
        // dispatched on the strength of the clear's HTTP acknowledgement.
        if (state.clearQueued) { record('start → refused (409 voice_busy: clear still queued)'); return { body: { error: 'voice_busy' }, status: 409 }; }
        if (state.phase === 'listening') return { body: { error: 'busy' }, status: 409 };
        // The rotation lands *here*, between the client's dispatch and this
        // admission, so the credentials this reply carries belong to the new
        // authority while the activation that asked for them was issued under the
        // old one.
        if (rotation === 'status-inflight') rotateDuringStart();
        state.sessionId = uuid();
        state.ownedSession = state.sessionId;
        state.phase = 'listening';
        state.inputSource = 'touch';
        state.reviewId = null;
        state.transcript = '';
        state.response = '';
        state.progress = null;
        state.revision += 1;
        publishedVoice();
        const started = { session_id: state.sessionId, token: `preview-${uuid()}`, lease_seconds: 4 };
        // A takeover that happens *before any status read observes it*: the
        // client's freshest reading is still the pre-start baseline, so an
        // identity check against the last poll cannot see it. This is the case
        // the runtime's own confirmation exists for.
        state.startSessionId = started.session_id;
        if (fault === 'session-change-silent') adoptForeignSession();
        return { body: protectedReply(null, started), delay: startDelay };
      }
      case 'keepalive':
        return body.session_id === state.ownedSession
          ? { body: protectedReply('keepalive', { ok: true }) }
          : { body: { error: 'unknown session' }, status: 409 };
      case 'stop':
        state.phase = 'review';
        state.reviewId = uuid();
        state.inputSource = 'touch';
        publishedVoice();
        return { body: protectedReply('stop', { ok: true }) };
      case 'cancel':
        state.readsAtCancel = state.statusReads;
        state.phase = 'idle';
        state.ownedSession = null;
        state.reviewId = null;
        state.inputSource = null;
        state.transcript = '';
        state.response = '';
        publishedVoice();
        return { body: protectedReply(null, { ok: true }) };
      case 'clear': {
        if (reset === 'refuse') { record('clear → refused (409)'); return { body: { error: 'busy' }, status: 409 }; }
        if (reset === 'fail') { record('clear → failed (500)'); return { body: { error: 'unavailable' }, status: 500 }; }
        // Already clearing: the real runtime answers the same acknowledgement
        // again rather than starting a second reset.
        if (state.clearQueued) return { body: { status: 'clearing' } };
        // The acknowledgement is the *acceptance* of the clear, not its
        // completion. The slot stays reserved until the worker below finishes,
        // and `start` refuses in the meantime.
        state.clearQueued = true;
        state.phase = 'clearing';
        state.ownedSession = null;
        state.reviewId = null;
        state.inputSource = null;
        state.transcript = '';
        state.response = '';
        state.progress = null;
        publishedVoice();
        record('clear → 200 {status:clearing} (worker still holds the slot)');
        setTimeout(() => {
          if (!state.clearQueued) return;
          state.clearQueued = false;
          // A clear moves the active thread to a new, empty one. History is a
          // separate thing and is not deleted: the exchanges that were on screen
          // move to `retiredItems` rather than disappearing.
          state.retiredItems.push(...state.activeItems);
          state.activeItems = [];
          state.threadId = uuid();
          state.phase = 'idle';
          state.sessionId = uuid();
          state.revision += 1;
          publishedVoice();
          record('clear worker finished → idle on a new empty active thread');
        }, clearDelay);
        return { body: protectedReply(null, { status: 'clearing' }) };
      }
      case 'review/submit':
        state.submittedQuestion = String((body as { question?: unknown })?.question ?? '');
        state.phase = 'processing';
        state.progress = 'generating';
        publishedVoice();
        // The answer arrives shortly after; it then belongs to the thread.
        setTimeout(() => {
          state.phase = 'complete';
          state.progress = null;
          state.response = ANSWER;
          state.transcript = QUESTION;
          state.revision += 1;
          publishedVoice();
          state.activeItems.push({
            session_id: state.sessionId, thread_id: state.threadId, created_at: new Date().toISOString(),
            transcript: QUESTION, response: ANSWER, has_photo: false,
          });
        }, 700);
        return { body: protectedReply(null, { ok: true }) };
      case 'review/cancel':
        state.phase = 'idle';
        state.reviewId = null;
        publishedVoice();
        return { body: protectedReply(null, { ok: true }) };
      case 'question/start': {
        state.questionStartDispatched = true;
        state.lastTypedHadOriginal = typeof body.original_base64 === 'string' && body.original_base64.startsWith('/9j/');
        state.phase = 'review';
        state.reviewId = uuid();
        state.inputSource = 'typed';
        /**
         * A typed question has no recording to transcribe, so the review opens
         * empty and the keyboard is what fills it.
         *
         * Leaving the previous exchange's transcript in place modelled a review
         * the real lane never opens: it made the review start already non-empty,
         * which put the keyboard behind an editor the user had to tap first and
         * left the composer's inline presentation unreachable in this preview.
         */
        state.transcript = '';
        publishedVoice();
        const typed = protectedReply(null, { session_id: state.sessionId, token: `preview-${uuid()}`, lease_seconds: 4 });
        // Built under the epoch that held when this request arrived; the epoch
        // moves before the reply is delivered.
        rotateDuringRead('typed-inflight');
        return { body: typed, delay: typedDelay };
      }
      case 'session/link':
        return { body: { ok: true } };
      case 'start/confirm': {
        // The runtime is asked whether this exact request is still the current
        // owner. Anything else — a takeover, a superseded request, a released
        // session — is a refusal, and the client cancels the credentials it holds.
        const wanted = String(body.session_id ?? '');
        const requestId = String(body.request_id ?? '');
        if (!state.ownedSession || state.ownedSession !== wanted) {
          record('start/confirm → 409 (the runtime no longer owns this request)');
          return { body: { error: 'not_current' }, status: 409, delay: confirmDelay };
        }
        state.confirmed = true;
        return { body: protectedReply('confirm', { status: 'current', session_id: wanted, request_id: requestId }), delay: confirmDelay };
      }
      case 'history/list':
        // Saved history, which the clear does not touch. Retained photo records
        // are real saved records here, not a separate store the gallery invents.
        {
          state.historyReads += 1;
          // The broker may already have moved when this read arrives, in which
          // case the policy below belongs to the new epoch and only the reply's
          // own stamp can say so.
          rotateBeforePhotoRead();
          const listing = {
            enabled: saving,
            saving_off_keeps_items: true,
            photo_saving_supported: photoMode !== 'unsupported',
            photos_enabled: photoMode !== 'off' && photoMode !== 'unsupported',
            items: [...state.photos, ...(saving ? state.retiredItems : [])],
          };
          // Declared unreachable: a locked device stamps every successful
          // protected reply or answers nothing at all.
          if (historyStamp === 'missing') {
            record(`history/list → 200 with a ${historyStamp} admission stamp (a reply the runtime would not send)`);
            return { body: listing, delay: stateDelay };
          }
          if (historyStamp === 'malformed') {
            record(`history/list → 200 with a ${historyStamp} admission stamp (a reply the runtime would not send)`);
            return {
              body: { ...listing, session_context: { schema: 'sage.lantern.session-context.v1', epoch: 'not-an-epoch', session_ref: 'not-a-session-ref' } },
              delay: stateDelay,
            };
          }
          return { body: protectedReply(null, listing), delay: stateDelay };
        }
      case 'history/photo': {
        const wanted = String(body.session_id ?? '');
        const record = state.photos.find((item) => item.session_id === wanted);
        if (!record || state.photoFailures.has(wanted)) {
          record('history/photo → 404 (no retained bytes for this record)');
          return { body: { error: 'no_photo' }, status: 404 };
        }
        const key = wanted.split('-')[1];
        return { body: { image_base64: RETAINED_PHOTOS[key] } };
      }
      case 'history/delete': {
        const wanted = String(body.session_id ?? '');
        const before = state.photos.length;
        state.photos = state.photos.filter((item) => item.session_id !== wanted);
        record(`history/delete → removed ${wanted} (${before} → ${state.photos.length} retained)`);
        return { body: { ok: true } };
      }
      case 'conversation/state': {
        // The real runtime publishes the session the device is currently showing
        // as the active conversation, which is how the screen decides a finished
        // turn belongs to the chat you are looking at. Without it the preview
        // would silently test the "different chat" path instead.
        const snapshot = protectedReply(null, { thread_id: state.threadId, revision: state.revision, active_session_id: state.phase === 'idle' || state.clearQueued ? null : state.sessionId, items: state.activeItems, context_turn_limit: 4 });
        rotateDuringRead('state-inflight');
        return { body: snapshot, delay: stateDelay };
      }
      case 'conversation/select': {
        /**
         * Move the active thread, which is what the runtime does.
         *
         * The stub this replaces answered `{ok:true}`, which
         * `parseConversationState` rejects outright - no thread_id, no revision,
         * no items - so every selection in the preview failed with "Could not
         * open this chat" and the second-chat path was untestable rather than
         * merely untested.
         */
        const wanted = String(body.thread_id ?? '');
        if (wanted !== state.threadId && wanted !== state.secondThreadId) {
          // Refused rather than invented, so a chat id the device does not have
          // stays a usable negative control.
          record(`conversation/select refused -> ${wanted || '(empty)'} is not a saved thread`);
          return null;
        }
        const leaving = state.activeItems;
        const arriving = state.secondItems;
        // The thread being left takes the place of the one being opened, and its id
        // has to move with it: without this the fixture forgets the conversation it
        // just left and REFUSES to switch back to it, which looks exactly like the
        // app failing to load a chat it was told to open.
        const previousThreadId = state.threadId;
        state.threadId = wanted;
        state.secondThreadId = previousThreadId;
        state.activeItems = arriving;
        state.secondItems = leaving;
        state.retiredItems = state.retiredItems.filter((item) => String(item.thread_id) !== wanted);
        if (leaving.length) state.retiredItems.push(...leaving);
        state.revision += 1;
        record(`conversation/select -> ${wanted} (${arriving.length} item(s) loaded, ${state.retiredItems.length} still saved)`);
        return { body: protectedReply(null, {
          thread_id: state.threadId,
          revision: state.revision,
          active_session_id: state.phase === 'idle' || state.clearQueued ? null : state.sessionId,
          items: state.activeItems,
          context_turn_limit: 4,
        }) };
      }
      default:
        return null;
    }
  };

  /**
   * What the status endpoint should do right now, given the injected fault.
   *
   * Both faults fire only while a start is dispatched and unacknowledged: that
   * is the window the reproduced defects live in, and firing earlier would test
   * something else.
   */
  const statusFault = (): Fault | null => {
    if (!state.startDispatched || state.startAcknowledged) return null;
    return fault === 'none' ? null : fault;
  };

  /**
   * Another client takes the device over.
   *
   * The fault has to be *persistent* to be the case it claims to be: a decoy that
   * disappears the moment our own acknowledgement lands would leave the device
   * legitimately on the session we asked for, and there would be nothing to
   * detect. What is modelled is the device moving to a session this client never
   * requested and staying there, so the session we are later handed is genuinely
   * orphaned.
   */
  const adoptForeignSession = () => {
    if (state.foreignAdopted) return;
    state.foreignAdopted = true;
    state.takeoverHidden = true;
    state.sessionId = state.foreignSessionId;
    state.ownedSession = null;
    state.phase = 'listening';
    state.inputSource = 'touch';
    record('another client took the device: it is listening on a session we never asked for');
  };

  /**
   * A synthetic camera in the device's own wire format.
   *
   * `CameraFrameDecoder` parses repeated `[uint32 big-endian length][JPEG]` and
   * validates each frame's SOI/EOI markers and size bounds, so a stub string
   * would be rejected exactly where a real frame is required. These are the same
   * real JPEGs the gallery uses, streamed a few times a second like the preview
   * endpoint does.
   */
  const cameraFrame = (key: string): Uint8Array => {
    const binary = atob(RETAINED_PHOTOS[key]);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  };

  const cameraStream = (signal?: AbortSignal): Response => {
    const frames = ['reef', 'desk', 'leaf'].map(cameraFrame);
    let emitted = 0;
    let timer: ReturnType<typeof setInterval> | undefined;
    let stopped = false;
    const stop = () => { stopped = true; if (timer) clearInterval(timer); };
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const emit = () => {
          if (stopped) return;
          const frame = frames[emitted % frames.length];
          const chunk = new Uint8Array(4 + frame.length);
          new DataView(chunk.buffer).setUint32(0, frame.length);
          chunk.set(frame, 4);
          try { controller.enqueue(chunk); } catch { stop(); return; }
          emitted += 1;
          // A finite stream keeps a headless run from holding an interval open
          // forever; the screen's own watchdog then reports the camera as paused.
          if (emitted >= 120) { stop(); try { controller.close(); } catch { /* already closed */ } }
        };
        emit();
        timer = setInterval(emit, 250);
      },
      cancel() { stop(); },
    });
    signal?.addEventListener('abort', stop);
    record('POST /preview → synthetic camera stream (real JPEG frames)');
    return new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-lantern-jpeg-stream' } });
  };

  return {
    log, blocked, status, statusFault, command, state, adoptForeignSession, cameraStream, rotateNow,
    /** The broker's current context epoch, for a driver proving the client lagged it. */
    brokerEpoch: () => contextEpoch,
    acknowledgeStart: () => { state.startAcknowledged = true; },
    note: (entry: string) => record(entry),
    snapshot: () => ({ ...state, log: [...log], blocked: [...blocked] }),
  };
}

type Service = ReturnType<typeof createService>;
/**
 * Install the synthetic transport. Called once, before the first render, so the
 * app's very first status poll is already answered here.
 */
function installTransport(service: Service, statusDelay = 0) {
  const real = window.fetch.bind(window);
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, location.href);

    // The preview's own assets stay real.
    if (url.origin === location.origin) return real(input as RequestInfo, init);

    const respond = (body: unknown, status = 200, delay = 0) => new Promise<Response>((resolve, reject) => {
      const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
      const onAbort = () => { clearTimeout(timer); reject(Object.assign(new Error('Aborted'), { name: 'AbortError' })); };
      const timer = setTimeout(() => {
        signal?.removeEventListener('abort', onAbort);
        resolve(json(body, status));
      }, delay);
      if (signal?.aborted) { onAbort(); return; }
      signal?.addEventListener('abort', onAbort, { once: true });
    });

    if (url.origin === 'http://127.0.0.1:3199' && url.pathname === '/status') {
      const fault = service.statusFault();
      // A status read that fails is a *transport* failure, exactly as the real
      // poll sees it: no response, the screen goes to Reconnecting, and the
      // parent's freshness fence runs.
      if (fault === 'disconnect-after-start') {
        service.log.push('GET /status → failed (fault: disconnect after /start)');
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      // An unrelated session appearing while our acknowledgement is still out is
      // an authority change the activation was never authorised for.
      if (fault === 'session-change') {
        service.adoptForeignSession();
        return respond(service.status(), 200, statusDelay);
      }
      return respond(service.status(), 200, statusDelay);
    }
    /**
     * The live voice channel.
     *
     * The real service serves this beside `/status`: scripts/lantern/unit-a-status-server.py
     * answers /status, /voice and /health, with /voice returning collect_voice_status().
     * It is also the ONLY channel that establishes the session authority every protected
     * surface - chats, gallery, saved history - is judged against: in
     * app/device/screen.tsx the /voice reply is what feeds observeSessionContext. With no
     * /voice reply the client's authority stays 'unknown', lib/conversation-threads.ts
     * refuses every protected read, and the gallery reports "Saved photos unavailable" -
     * which looks exactly like a product failure and is not one.
     */
    if (url.origin === 'http://127.0.0.1:3199' && url.pathname === '/voice') {
      /**
       * One service, one transport: a fault that takes /status down takes the
       * live voice channel with it. Without this the screen keeps believing it
       * is connected while every telemetry read fails, which is not a state the
       * real device can produce.
       */
      const fault = service.statusFault();
      if (fault === 'disconnect-after-start') {
        service.log.push('GET /voice -> failed (fault: disconnect after /start)');
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      if (fault === 'session-change') service.adoptForeignSession();
      const full = service.status(false, true) as Record<string, unknown>;
      return respond({
        schema: 'sage.lantern.voice-status.v1',
        observed_at: full.observed_at,
        ...(full.session_context ? { session_context: full.session_context } : {}),
        voice: full.voice,
      }, 200, statusDelay);
    }
    if (url.origin === 'http://127.0.0.1:3197') {
      const action = url.pathname.replace(/^\//, '');
      let body: Record<string, unknown> = {};
      try { body = init?.body ? JSON.parse(String(init.body)) : {}; } catch { body = {}; }
      const answer = service.command(action, body);
      if (!answer) {
        service.log.push(`POST /${action} → not served by this preview (404)`);
        return respond({ error: 'not_served' }, 404);
      }
      service.log.push(`POST /${action} → ${answer.status ?? 200}`);
      return respond(answer.body, answer.status ?? 200, answer.delay ?? 0).then((response) => {
        // The acknowledgement landing is what ends the fault window.
        if (action === 'start') {
          service.state.readsAtAck = service.state.statusReads;
          service.acknowledgeStart();
          // Deliberately not shaped like a transport line: a note that begins
          // "POST /start →" is indistinguishable from a second dispatch to any
          // driver counting them, and that is exactly the false positive this
          // wording used to produce.
          service.note('start acknowledgement delivered (not a dispatch)');
        }
        return response;
      });
    }

    // The camera preview endpoint, in the device's own framing.
    if (url.origin === 'http://127.0.0.1:3198' && url.pathname === '/preview') {
      return Promise.resolve(service.cameraStream(init?.signal ?? undefined));
    }

    // Everything else is refused. A preview must not be able to reach a live
    // device service, a LAN peer or the public internet, and a silent success
    // would be worse than a failure because nobody would notice it.
    service.blocked.push(url.origin + url.pathname);
    if (service.blocked.length > 20) service.blocked.shift();
    return Promise.reject(new TypeError(`Blocked by the disconnected preview: ${url.origin}${url.pathname}`));
  }) as typeof window.fetch;
}

/**
 * The capture owner with no parent.
 *
 * `DeviceScreen` supplies two defences this harness deliberately withholds: an
 * admission callback (`admit`) and its own epoch fence over `/status`. Both are
 * real, and both are *other* objects' judgments. A rotation the parent has
 * already fenced is refused twice over, which is exactly why the integrated
 * preview cannot show whether the owner's own fence works. Here the device's
 * readings go straight to the owner and its callbacks are recorded, so what it
 * does with a `/start` it issued under an authority that has since moved is its
 * own answer and nothing else's.
 */
function OwnerHarness({ service, hold = false, photo }: { service: Service; hold?: boolean; photo?: PhotoAttachment }) {
  const [status, setStatus] = useState<DeviceStatus | null>(null);
  const controller = useRef<TalkController>(null);
  const trail = useRef({ started: [] as string[], typed: [] as string[], cancelled: 0, ownership: null as TalkOwnership | null });
  const seenEpoch = useRef<string | null>(null);
  const notedRotation = useRef(false);
  /** The context of the reading this harness last published, and whether it is held. */
  const held = useRef<{ context: string | null; frozen: boolean }>({ context: null, frozen: false });
  useEffect(() => {
    const tick = () => {
      // `hold` models the reachable window between the broker rotating and this
      // client's next poll landing. Without it the window is a race against the
      // 200ms tick below, which would make the probe a coin toss rather than a
      // test: what the client holds is deliberately the reading taken *before*
      // the rotation for as long as the scenario needs it to be.
      if (hold && held.current.frozen) return;
      let next: DeviceStatus;
      try { next = parseDeviceStatus(service.status()); } catch { return; }
      const reading = next.session_context;
      const epoch = reading && reading !== 'invalid' ? reading.epoch : null;
      if (epoch && seenEpoch.current && seenEpoch.current !== epoch && !notedRotation.current) {
        notedRotation.current = true;
        // The reading the owner is given *after* the rotation, published while
        // the delayed `/start` reply is still out. This is what makes "the device
        // now says B" a fact the owner must weigh against what it captured before
        // dispatch, rather than one only the reply could have told it.
        service.note('owner harness published the rotated context while the start reply was still out');
      }
      if (epoch) seenEpoch.current = epoch;
      held.current = { context: epoch, frozen: true };
      setStatus(next);
    };
    tick();
    const timer = setInterval(tick, 200);
    return () => clearInterval(timer);
  }, [service, hold]);
  useEffect(() => {
    (window as unknown as Record<string, unknown>).previewOwner = {
      probe: () => ({
        ...service.snapshot(),
        started: [...trail.current.started],
        typed: [...trail.current.typed],
        cancelled: trail.current.cancelled,
        ownership: trail.current.ownership,
        // The two epochs side by side: what this client holds, and what the
        // broker publishes. A policy read from an unattributed reply is only a
        // defect when those disagree, so the driver asserts both.
        heldEpoch: held.current.context,
        brokerEpoch: service.brokerEpoch(),
      }),
    };
  }, [service]);
  return <div className="owner-harness">
    <TalkControls
      status={status}
      connected
      photo={photo}
      controllerRef={controller}
      onStarted={(sessionId) => { trail.current.started.push(sessionId); }}
      onTypedSession={(sessionId) => { trail.current.typed.push(sessionId); }}
      onCancelled={() => { trail.current.cancelled += 1; }}
      onOwnership={(ownership) => { trail.current.ownership = ownership; }}
    />
  </div>;
}

function Preview() {
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const scenario = (params.get('scenario') ?? 'ready') as Scenario;
  const reset = (params.get('reset') ?? 'ok') as ResetMode;
  const mic = (params.get('mic') ?? 'on') as Mic;
  const typed = (params.get('typed') ?? 'on') as Typed;
  const startDelay = Number(params.get('startDelay') ?? 0);
  const clearDelay = Number(params.get('clearDelay') ?? 1200);
  const saving = params.get('saving') === 'on';
  const photoMode = (params.get('photos') ?? 'none') as PhotoMode;
  const photoFails = params.get('photoFails') === '1';
  const fault = (params.get('fault') ?? 'none') as Fault;
  const contextMode = (params.get('context') ?? 'off') as ContextMode;
  const rotation = (params.get('rotation') ?? 'none') as Rotation;
  const confirmDelay = Number(params.get('confirmDelay') ?? 0);
  const owner = params.get('owner') === '1';
  const statusDelay = Number(params.get('statusDelay') ?? 0);
  const tokenSwapAfter = Number(params.get('tokenSwapAfter') ?? 0);
  const interruptAfter = Number(params.get('interrupt') ?? 0);
  const grow = params.get('grow') === '1';
  const typedDelay = Number(params.get('typedDelay') ?? 0);
  const stateDelay = Number(params.get('stateDelay') ?? 0);
  const hold = params.get('hold') === '1';
  const attach = params.get('attach') === '1';
  const historyStamp = (params.get('historyStamp') ?? 'ok') as HistoryStamp;
  const frame = params.get('frame') === '1';
  const showLog = params.get('log') === '1';

  const service = useMemo(
    () => createService(scenario, reset, Number.isFinite(startDelay) ? startDelay : 0, mic,
      Number.isFinite(clearDelay) ? clearDelay : 1200, saving, photoMode, photoFails, fault,
      contextMode, rotation, Number.isFinite(confirmDelay) ? confirmDelay : 0,
      Number.isFinite(tokenSwapAfter) ? tokenSwapAfter : 0,
      Number.isFinite(typedDelay) ? typedDelay : 0, Number.isFinite(stateDelay) ? stateDelay : 0, historyStamp,
      Number.isFinite(interruptAfter) ? interruptAfter : 0, grow, typed !== 'off'),
    [scenario, reset, startDelay, mic, clearDelay, saving, photoMode, photoFails, fault, contextMode, rotation, confirmDelay, tokenSwapAfter, typedDelay, stateDelay, historyStamp, interruptAfter, grow, typed],
  );
  /**
   * The attachment the capture owner is holding, when the scenario asks for one.
   *
   * The real shape rather than a placeholder: both lanes pass it through as raw
   * base64, so the policy under test is the one that actually decides whether the
   * *original* bytes travel behind the bounded inference image.
   */
  const ownerPhoto = useMemo<PhotoAttachment | undefined>(
    () => (attach ? { image: RETAINED_PHOTOS.reef, preview: RETAINED_PHOTOS.reef } : undefined),
    [attach],
  );
  const installed = useRef(false);
  /**
   * How many times the shared session seam has told its subscribers that the
   * authority moved.
   *
   * This is the witness for the poll's *ordering*, not for its rendering: an
   * obsolete reply that reaches `observeSessionContext` establishes an authority
   * that is already gone, and the next current reading then looks like a rotation
   * to every protected surface. Counting the notifications distinguishes that from
   * a reply that was refused before it could touch the seam.
   */
  const notifications = useRef(0);
  /** The in-flight result of the driver's own `conversationRequest` probe. */
  const probe = useRef<Promise<{ outcome: string; keys?: string[]; reason?: string }> | null>(null);
  if (!installed.current) {
    setDeviceToken('previewSyntheticToken'.padEnd(43, '0'));
    installTransport(service, Number.isFinite(statusDelay) ? statusDelay : 0);
    subscribeDeviceSession(() => { notifications.current += 1; });
    installed.current = true;
  }

  const [scale, setScale] = useState(1);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((value) => value + 1), 250);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    (window as unknown as Record<string, unknown>).preview = {
      scenario, reset, startDelay, mic, clearDelay, saving, photoMode, photoFails, fault, contextMode, rotation, confirmDelay,
      snapshot: service.snapshot,
      // The driver reads this rather than scraping the DOM: it is the service's
      // own account of what it was asked, not the screen's rendering of it.
      probe: () => ({ ...service.snapshot(), tick }),
      notifications: () => notifications.current,
      /**
       * The real `conversationRequest`, driven in two phases through the real
       * transport.
       *
       * The integrated screen also fences a late reply with its own generation
       * counter, so watching the chats pane cannot show whether the *helper*
       * refuses one — and the helper is what a cache with no generation (saved
       * history) adopts from. Phase one dispatches; the driver then moves the
       * epoch itself, waits for a status poll to publish it, and reads phase two.
       * Nothing here is on a timer, so the rotation is inside the round trip by
       * construction rather than by luck.
       */
      beginConversationProbe: (action = 'conversation/state') => {
        probe.current = conversationRequest(action as 'conversation/state')
          .then((value) => ({ outcome: 'adopted', keys: Object.keys(value as Record<string, unknown>).sort() }))
          .catch((failure) => ({ outcome: 'refused', reason: failure instanceof Error ? `${failure.name}: ${failure.message}` : String(failure) }));
        return true;
      },
      rotateAuthority: () => { service.rotateNow(); return true; },
      conversationProbe: () => probe.current,
    };
  }, [scenario, reset, startDelay, mic, clearDelay, saving, photoMode, photoFails, fault, contextMode, rotation, confirmDelay, service, tick]);

  useEffect(() => {
    if (!frame) return;
    const fit = () => setScale(Math.min(1, (window.innerHeight - 90) / PANEL_HEIGHT, (window.innerWidth - 40) / PANEL_WIDTH) || 0.1);
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [frame]);

  const screen = owner
    ? <OwnerHarness service={service} hold={hold} photo={ownerPhoto} />
    : <div className={`device-frame${frame ? '' : ' device-frame-viewport'}`}><DeviceScreen homeEnabled /></div>;
  const stage = frame
    ? <div className="preview-stage">
      <div className="preview-fit" style={{ width: PANEL_WIDTH * scale, height: PANEL_HEIGHT * scale }}>
        <div style={{ transform: `scale(${scale})`, transformOrigin: 'top left' }}>{screen}</div>
      </div>
    </div>
    : <div className="preview-stage">{screen}</div>;

  return <div className="preview-shell">
    <output className="preview-notice">
      <strong>SYNTHETIC PREVIEW</strong>
      <span>Home 2a wiring · <code>homeEnabled</code> · scenario <code>{scenario}</code> · mic <code>{mic}</code> · typed <code>{typed}</code> · reset <code>{reset}</code> · start delay <code>{startDelay}ms</code> · clear delay <code>{clearDelay}ms</code> · saving <code>{saving ? 'on' : 'off'}</code> · photos <code>{photoMode}</code>{photoFails ? ' (a read fails)' : ''} · fault <code>{fault}</code> · context <code>{contextMode}</code> · rotation <code>{rotation}</code> · confirm delay <code>{confirmDelay}ms</code> · status delay <code>{statusDelay}ms</code> · typed delay <code>{typedDelay}ms</code> · state delay <code>{stateDelay}ms</code> · token swap after <code>{tokenSwapAfter || 'never'}</code>{owner ? ' · capture owner mounted directly' : ''}</span>
      {interruptAfter > 0 && <span>The answer stops after <code>{interruptAfter}</code> status reads and the device publishes no further words.</span>}
      <span>Device service is simulated in-page; all non-loopback traffic refused.</span>
      {owner && <span>Capture owner: reading {hold ? 'held at the pre-rotation context' : 'following the device'} · attachment {attach ? 'real photo bytes' : 'none'} · photo-policy read {historyStamp}.</span>}
    </output>
    {stage}
    {showLog && <output className="preview-log">
      <strong>synthetic device service</strong>
      <em>{service.log.slice(-8).join(' · ') || 'no requests yet'}</em>
      <em>blocked: {service.blocked.length === 0 ? 'none' : service.blocked.join(', ')}</em>
    </output>}
  </div>;
}

createRoot(document.getElementById('root')!).render(<Preview />);
