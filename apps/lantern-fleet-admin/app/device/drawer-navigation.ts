'use client';
import { useEffect, useState, type RefObject } from 'react';

/**
 * Which open drawer owns the keyboard.
 *
 * Focus inside a drawer makes it the owner, and the innermost drawer wins when
 * they nest. A drawer keeps ownership when focus is dropped onto the body:
 * disabling or removing the control that held focus does exactly that, and no
 * focusin is fired anywhere for it, so focus that never moved to another
 * surface is not the user handing the keyboard over.
 */
const openDrawers = new Set<HTMLElement>();
let lastFocusedDrawer: HTMLElement | null = null;

const isOnScreen = (element: HTMLElement) =>
  element.getClientRects().length > 0 && getComputedStyle(element).visibility === 'visible';

const trackDrawerFocus = (event: FocusEvent) => {
  const target = event.target;
  if (!(target instanceof Node) || target === document.body || target === document.documentElement) return;
  const containing = [...openDrawers].filter((panel) => panel.isConnected && panel.contains(target));
  lastFocusedDrawer = containing.find((panel) => !containing.some((other) => other !== panel && panel.contains(other))) ?? null;
};

const registerDrawer = (panel: HTMLElement) => {
  if (!openDrawers.size) document.addEventListener('focusin', trackDrawerFocus);
  openDrawers.add(panel);
};

const unregisterDrawer = (panel: HTMLElement) => {
  openDrawers.delete(panel);
  if (lastFocusedDrawer === panel) lastFocusedDrawer = null;
  if (!openDrawers.size) document.removeEventListener('focusin', trackDrawerFocus);
};

export function useDrawerNavigation(ref: RefObject<HTMLElement | null>, open: boolean, close: () => void, modal = false) {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const query = matchMedia('(max-width: 760px), (orientation: portrait)');
    const update = () => setNarrow(query.matches);
    update(); query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if ((!modal && !narrow) || !open || !ref.current) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel = ref.current;
    registerDrawer(panel);
    const controls = () => [...panel.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)')];
    let pendingFocus = true;
    let focusFrame = 0;
    const finishFocus = () => {
      pendingFocus = false;
      cancelAnimationFrame(focusFrame);
      panel.removeEventListener('transitionend', ready);
      document.removeEventListener('focusin', moved);
    };
    const moved = () => { if (document.activeElement !== previous) finishFocus(); };
    const focusFirst = () => {
      if (!pendingFocus) return;
      if (ref.current !== panel || !panel.isConnected || panel.contains(document.activeElement) || document.activeElement !== previous) {
        finishFocus(); return;
      }
      // Animation frames can run before CSS makes the opening drawer focusable.
      const first = controls().find((item) => !item.closest('[inert]') && isOnScreen(item));
      first?.focus();
      if (panel.contains(document.activeElement)) finishFocus();
    };
    const ready = (event: TransitionEvent) => {
      if (event.target === panel && (event.propertyName === 'transform' || event.propertyName === 'visibility')) focusFirst();
    };
    panel.addEventListener('transitionend', ready);
    document.addEventListener('focusin', moved);
    focusFirst();
    if (pendingFocus) focusFrame = requestAnimationFrame(() => {
      focusFrame = requestAnimationFrame(focusFirst);
    });
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      if (event.key === 'Tab') {
        const items = controls(); const first = items[0]; const last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    panel.addEventListener('keydown', key);
    /**
     * Escape reaches a drawer through the panel while focus is inside it. A
     * drawer that still owns the keyboard after focus fell to the body would
     * otherwise have no handler left on the path the key travels, so the same
     * key is taken from the body — for the owning drawer only, and only while it
     * is really on screen and not inside an inert surface.
     */
    const ownEscapeFromBody = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.key !== 'Escape' || lastFocusedDrawer !== panel) return;
      if (event.target !== document.body && event.target !== document.documentElement) return;
      if (ref.current !== panel || !panel.isConnected || panel.closest('[inert]') || !isOnScreen(panel)) return;
      key(event);
    };
    document.addEventListener('keydown', ownEscapeFromBody);
    return () => {
      finishFocus();
      panel.removeEventListener('keydown', key);
      document.removeEventListener('keydown', ownEscapeFromBody);
      unregisterDrawer(panel);
      previous?.focus();
    };
  }, [modal, narrow, open, ref, close]);
  return narrow;
}
