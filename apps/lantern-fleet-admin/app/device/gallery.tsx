'use client';

/**
 * Gallery: a separate full-screen destination for photos kept with a question.
 *
 * It is deliberately not part of Camera. Camera shows what the lens sees right
 * now and explicitly says a fresh snapshot is not saved; the gallery shows what
 * was actually retained, read back through the existing protected history
 * binding. Merging them would make "not saved" and "saved" the same screen.
 *
 * What this file is careful about:
 *
 *  - **Bytes, not filenames.** A record with `has_photo` is a promise of an
 *    image. Nothing here calls it a photo until `history/photo` returned bytes
 *    that `parseHistoryPhoto` validated as a bounded JPEG.
 *  - **Protected state.** Image bytes live in component state only — never
 *    localStorage, IndexedDB or a cache — and are dropped on lock/owner change
 *    (the device session), a hidden document, unmount and deletion. Every async
 *    read re-checks a generation counter before it is allowed to render, because
 *    `voiceCommand` does not verify the token again after its response.
 *  - **Honest deletion.** `history/delete` removes the saved question and its
 *    answer; whether the photo bytes survive is the runtime's answer to give, so
 *    the confirmation is worded from `photo_removal` and never claims an outcome
 *    the reply did not state. There is no photo-only command, so there is no
 *    photo-only button and no wording that implies one.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Image from 'next/image';
import { parseHistoryPhoto, type HistoryItem } from '../../lib/device-history';
import { voiceCommand } from '../../lib/voice-control';
import { prepareHistoryPhoto } from '../../lib/history-retry';
import { savedHistory } from '../../lib/saved-history';
import { subscribeDeviceSession } from '../../lib/device-session';
import { useSavedHistory } from './saved-history';
import {
  askAboutPhoto, clampSelection, deleteEffect, galleryState, imageUnavailableReason,
  positionLabel, stepSelection, thumbnailBatches, withoutMissing,
} from '../../lib/gallery-navigation';
import { BackIcon, CameraIcon, GalleryIcon } from './icons';
import type { PhotoAttachment } from '../../lib/photo-preview';
import './gallery.css';

/** How many thumbnails may be in flight at once; the rest wait their turn. */
const THUMBNAIL_BATCH = 6;

/** One shared empty list, so "no photos" does not allocate a new array per render. */
const NO_ITEMS: HistoryItem[] = [];

export default function GalleryScreen({ close, photoReady, connected, busy, onAskAboutPhoto, onOpenCamera }: {
  close: () => void;
  /** The device attests that a photo can travel with a question. */
  photoReady: boolean;
  connected: boolean;
  /** A question turn is live; staging another attachment would fight it. */
  busy: boolean;
  onAskAboutPhoto: (photo: PhotoAttachment, item: HistoryItem) => void;
  onOpenCamera?: () => void;
}) {
  const { history, error, refreshing } = useSavedHistory();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sources, setSources] = useState<Record<string, string>>({});
  const [missing, setMissing] = useState<Record<string, true>>({});
  const [confirming, setConfirming] = useState(false);
  /**
   * The snapshot never arrived and the screen has stopped waiting for it.
   *
   * Only ever written from a timer callback, a click, or a boundary handler —
   * never synchronously in an effect body, which would cascade renders.
   */
  const [gaveUp, setGaveUp] = useState(false);
  const [working, setWorking] = useState(false);
  const [actionError, setActionError] = useState('');
  const mounted = useRef(false);
  /**
   * Bumped whenever the images on screen stop being ours to show: a new
   * authenticated session, a hidden document, unmount, or a deletion. An image
   * read that finishes after this no longer belongs to the screen the user is
   * looking at, and is dropped rather than rendered.
   */
  const generation = useRef(0);
  const sourcesRef = useRef(sources);
  const back = useRef<HTMLButtonElement>(null);
  /** The open photo's own focus target, so keys do not depend on where a click left focus. */
  const viewer = useRef<HTMLDivElement>(null);
  /**
   * The scrollable grid the viewer opens over.
   *
   * It used to share `viewer`: one ref object attached to two different
   * elements, so only one of them could ever hold it and the effect that means
   * to focus the viewer on open could not be relied on to reach the viewer at
   * all. Separate refs make each element's owner unambiguous.
   */
  const content = useRef<HTMLDivElement>(null);
  /** The thumbnail to return focus to when the viewer closes. */
  const thumbRefs = useRef(new Map<string, HTMLButtonElement>());
  /** The control that opened the removal confirmation, and the dialog's own first stop. */
  const removeButton = useRef<HTMLButtonElement>(null);
  const confirmCancel = useRef<HTMLButtonElement>(null);
  /** True once the confirmation has been opened, so a mount is not read as an exit. */
  const confirmOpened = useRef(false);

  const state = useMemo(
    // While it is still trying, it is loading. Once it has given up, it is
    // unavailable with a reason — never an empty grid and never an endless wait.
    () => galleryState(history, { failed: !history && (gaveUp || !!error), loading: !history && !gaveUp }),
    [history, error, gaveUp],
  );
  // Stable empty array: a fresh `[]` on every render would make every consumer
  // of `items` re-run, and the loading path renders far more often than the ready
  // one.
  const items = useMemo(() => (state.phase === 'ready' ? state.items : NO_ITEMS), [state]);
  const selected = clampSelection(items, selectedId);
  const source = selected ? sources[selected.session_id] : undefined;
  const gone = selected ? missing[selected.session_id] === true : false;

  useEffect(() => { sourcesRef.current = sources; }, [sources]);

  /**
   * A snapshot that never arrives is a state, not a wait.
   *
   * The cache can be emptied while this screen is open — a lock or owner change
   * does exactly that — and only the cache's own subscriber repopulates it, on a
   * thirty-second interval. Without this the screen sits on "Checking saved
   * photos…" forever, which a user cannot tell apart from having lost their
   * photos. So it re-asks on a bounded interval, and if nothing arrives it says
   * so and offers a retry instead of pretending to still be checking.
   */
  useEffect(() => {
    if (history) return;
    // The retry keeps running either way, so data returning still recovers on its
    // own; giving up only changes what the screen SAYS. A later empty period may
    // therefore read as unavailable one cycle early, with a working Try again —
    // the honest direction to err in when the alternative is claiming to still be
    // checking.
    const retry = setInterval(() => { void savedHistory.refresh(true); }, 1500);
    const giveUp = setTimeout(() => setGaveUp(true), 6000);
    return () => { clearInterval(retry); clearTimeout(giveUp); };
  }, [history]);

  const forget = useCallback(() => {
    generation.current += 1;
    sourcesRef.current = {};
    setSources({});
    setMissing({});
  }, []);

  useEffect(() => {
    mounted.current = true;
    back.current?.focus();
    void savedHistory.refresh();
    // A lock, an unlock or a new owner ends this screen's right to show the
    // previous session's images.
    const unsubscribe = subscribeDeviceSession(() => { forget(); setSelectedId(null); setConfirming(false); });
    const hide = () => { if (document.hidden) { forget(); setSelectedId(null); } };
    document.addEventListener('visibilitychange', hide);
    return () => {
      mounted.current = false;
      document.removeEventListener('visibilitychange', hide);
      unsubscribe();
      forget();
    };
  }, [forget]);

  // Read the retained bytes for the grid, in bounded batches. A thumbnail is a
  // separate authenticated read per record, so asking for every one at once is
  // how a full grid stalls.
  useEffect(() => {
    if (state.phase !== 'ready') return;
    const start = generation.current;
    void (async () => {
      for (const batch of thumbnailBatches(state.items, THUMBNAIL_BATCH)) {
        const results = await Promise.all(batch.map(async (item) => {
          if (sourcesRef.current[item.session_id]) return null;
          try {
            return { id: item.session_id, source: parseHistoryPhoto(await voiceCommand('history/photo', { session_id: item.session_id })) };
          } catch {
            return { id: item.session_id, failed: true as const };
          }
        }));
        if (!mounted.current || document.hidden || generation.current !== start) return;
        const loaded: Record<string, string> = {};
        const failed: Record<string, true> = {};
        for (const result of results) {
          if (!result) continue;
          if ('source' in result && result.source) loaded[result.id] = result.source;
          else failed[result.id] = true;
        }
        if (Object.keys(loaded).length) {
          sourcesRef.current = { ...sourcesRef.current, ...loaded };
          setSources(sourcesRef.current);
          // Bytes that arrived now are not missing, whatever an earlier read said.
          // Without this the grid kept the failure label — and the viewer kept
          // saying the photo could not be loaded — over a picture it was showing.
          setMissing((current) => withoutMissing(current, Object.keys(loaded)));
        }
        if (Object.keys(failed).length) setMissing((current) => ({ ...current, ...failed }));
      }
    })();
  }, [state]);

  // A photo can disappear underneath the viewer: another screen deletes it, or
  // the refresh that follows a deletion drops it. `selected` is *derived* from
  // the current list rather than stored, so the viewer closes by itself on the
  // next render — no effect, and no window in which a stale id renders a
  // neighbour's picture in place of the one the user opened.
  const availability = askAboutPhoto({
    hasItem: !!selected, hasBytes: !!source, photoReady, connected, busy: busy || working,
  });

  /**
   * Close the viewer and put focus back on the photo it was opened from.
   *
   * The screen's keys live on the section, so they only fire while focus is
   * inside it. A pointer click does not always leave focus there — a trusted
   * click can end on the document body — and Escape or an arrow key then does
   * nothing at all. Focusing the viewer on open and returning focus on close
   * makes the keys work wherever the click landed, and gives a keyboard user a
   * visible place to be.
   *
   * `removed` is for the one close where returning to that photo is wrong: the
   * record's own thumbnail is still in the grid at this moment, because the
   * refresh that drops it has not landed yet, so focusing it puts focus on an
   * element the next render unmounts — and focus then falls to the document body.
   */
  const closeViewer = (removed = false) => {
    const returning = selectedId;
    setActionError('');
    setSelectedId(null);
    setConfirming(false);
    requestAnimationFrame(() => {
      const target = removed ? back.current : (returning && thumbRefs.current.get(returning)) || back.current;
      target?.focus({ preventScroll: true });
    });
  };

  useEffect(() => {
    if (selectedId) viewer.current?.focus({ preventScroll: true });
  }, [selectedId]);

  // Focus travels with the confirmation, the way it does in Settings: into the
  // dialog on entry, and back to the control that opened it if it is still there.
  // `role="alertdialog"` on its own announces nothing, so without this a keyboard
  // or AT user activates Remove… and is left on the button behind the dialog.
  // A successful removal closes the viewer as well, so the opener is gone and both
  // this and `closeViewer(true)` land on Back.
  useEffect(() => {
    if (confirming) { confirmCancel.current?.focus({ preventScroll: true }); return; }
    if (!confirmOpened.current) return;
    confirmOpened.current = false;
    (removeButton.current ?? back.current)?.focus({ preventScroll: true });
  }, [confirming]);

  const goBack = () => {
    if (working) return;
    if (confirming) { setActionError(''); setConfirming(false); return; }
    if (selectedId) { closeViewer(); return; }
    setActionError('');
    close();
  };

  const move = (delta: number) => {
    const next = stepSelection(items, selectedId, delta);
    if (next) { setActionError(''); setSelectedId(next); }
  };

  const remove = async () => {
    if (!selected || working) return;
    const item = selected;
    setWorking(true); setActionError('');
    try {
      await voiceCommand('history/delete', { session_id: item.session_id });
      // The removed record's bytes must not survive anywhere on this screen.
      forget();
      closeViewer(true);
      await savedHistory.refresh(true);
    } catch {
      setActionError('Could not remove it. Nothing was changed. Please try again.');
    } finally { setWorking(false); }
  };

  const ask = async () => {
    if (!selected || !source || !availability.allowed) return;
    const start = generation.current;
    setWorking(true); setActionError('');
    try {
      const attachment = await prepareHistoryPhoto(source);
      if (!mounted.current || document.hidden || generation.current !== start) return;
      onAskAboutPhoto(attachment, selected);
    } catch {
      if (mounted.current) setActionError('Could not prepare this photo for a question. Please try again.');
    } finally { if (mounted.current) setWorking(false); }
  };

  // The runtime's own account of what deletion did to the bytes, when it gave
  // one, is what the confirmation is allowed to promise. `sage_backed` alone
  // said the photo was gone while the same reply reported it retained.
  const effect = selected ? deleteEffect(selected, history?.sage_backed === true, history?.photo_removal) : null;

  return <section className="device-subscreen device-gallery" aria-label="Gallery"
    onKeyDown={(event) => {
      if (event.key === 'Escape') { event.stopPropagation(); goBack(); return; }
      // Arrow keys step through the viewer only while it is open, so they never
      // steal the grid's own scrolling.
      if (!selectedId) return;
      if (event.key === 'ArrowRight') { event.preventDefault(); move(1); }
      if (event.key === 'ArrowLeft') { event.preventDefault(); move(-1); }
    }}>
    <header className="device-subheader">
      <button ref={back} disabled={working} onClick={goBack}><BackIcon />Back</button>
      <div className="device-page-title"><GalleryIcon /><h1>{selected ? 'Photo' : 'Gallery'}</h1></div>
      {!selectedId && onOpenCamera && <button className="device-gallery-camera" disabled={busy} onClick={onOpenCamera}><CameraIcon />Camera</button>}
    </header>

    <div className="device-subcontent device-gallery-content" tabIndex={0} ref={content}>
      {actionError && <p className="device-gallery-error" role="alert">{actionError}</p>}

      {state.phase === 'loading' && <p className="device-gallery-state" role="status">{refreshing ? 'Loading saved photos…' : 'Checking saved photos…'}</p>}

      {state.phase === 'unavailable' && <div className="device-gallery-state" role="status">
        <GalleryIcon />
        <h2>Saved photos unavailable</h2>
        <p>{state.reason}</p>
        <div className="device-gallery-actions">
          <button onClick={() => { setGaveUp(false); setActionError(''); void savedHistory.refresh(true); }}>Try again</button>
          {onOpenCamera && <button onClick={onOpenCamera}><CameraIcon />Open camera</button>}
        </div>
      </div>}

      {state.phase === 'empty' && <div className="device-gallery-state" role="status">
        <GalleryIcon />
        <h2>No photos to show</h2>
        <p>{state.reason}</p>
      </div>}

      {selected && <div className="device-gallery-viewer" ref={viewer} tabIndex={-1} aria-label={`Photo ${positionLabel(items, selectedId)}`}>
        <div className="device-gallery-frame">
          {source && !gone
            ? <Image src={source} width={1920} height={1080} unoptimized alt={`Photo saved with the question: ${selected.transcript}`} />
            : <p role="status">{gone ? imageUnavailableReason() : 'Loading photo…'}</p>}
        </div>
        <p className="device-gallery-question">“{selected.transcript}”</p>
        <p className="device-gallery-position" role="status">{positionLabel(items, selectedId)}</p>
        <div className="device-gallery-actions">
          <button disabled={working || !stepSelection(items, selectedId, -1)} onClick={() => move(-1)} aria-label="Previous photo">Previous</button>
          <button disabled={working || !stepSelection(items, selectedId, 1)} onClick={() => move(1)} aria-label="Next photo">Next</button>
        </div>
        <div className="device-gallery-actions device-gallery-primary">
          <button className="device-action-primary" disabled={!availability.allowed || working} aria-describedby={availability.reason ? 'gallery-ask-reason' : undefined} onClick={() => void ask()}>
            {working ? 'Preparing…' : 'Ask about this photo'}
          </button>
          <button ref={removeButton} className="device-action-danger" disabled={working}
            onClick={() => { confirmOpened.current = true; setConfirming(true); }}>Remove…</button>
        </div>
        {availability.reason && <p id="gallery-ask-reason" className="device-gallery-note" role="status">{availability.reason}</p>}
      </div>}

      {!selected && state.phase === 'ready' && <>
        {state.note && <p className="device-gallery-note" role="status">{state.note}</p>}
        <ul className="device-gallery-grid" aria-label="Saved photos">
          {items.map((item) => {
            const image = sources[item.session_id];
            const failed = missing[item.session_id] === true;
            return <li key={item.session_id}>
              <button
                ref={(element) => {
                  if (element) thumbRefs.current.set(item.session_id, element);
                  else thumbRefs.current.delete(item.session_id);
                }}
                className="device-gallery-thumb"
                data-session={item.session_id}
                onClick={() => { setActionError(''); setSelectedId(item.session_id); }}
                aria-label={`Open photo saved with: ${item.transcript}`}
              >
                <span className="device-gallery-thumb-frame">
                  {image ? <Image src={image} width={256} height={192} unoptimized alt="" /> : <span className="device-gallery-thumb-placeholder" aria-hidden="true">{failed ? '!' : '…'}</span>}
                </span>
                <span className="device-gallery-thumb-label">{item.transcript}</span>
                {failed && <span className="device-gallery-thumb-missing">Photo missing</span>}
              </button>
            </li>;
          })}
        </ul>
      </>}
    </div>

    {confirming && selected && effect && <div className="device-gallery-confirm" role="alertdialog" aria-label="Confirm removal">
      <h2>{effect.title}</h2>
      <p>{effect.body}</p>
      <div className="device-actions">
        <button ref={confirmCancel} disabled={working} onClick={() => setConfirming(false)}>Cancel</button>
        <button className="device-action-danger" disabled={working} onClick={() => void remove()}>{working ? 'Removing…' : effect.confirmLabel}</button>
      </div>
    </div>}
  </section>;
}
