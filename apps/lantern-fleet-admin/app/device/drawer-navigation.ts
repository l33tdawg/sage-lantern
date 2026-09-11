'use client';
import { useEffect, useState, type RefObject } from 'react';
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
    const controls = () => [...panel.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)')];
    let focusFrame = requestAnimationFrame(() => {
      focusFrame = requestAnimationFrame(() => controls()[0]?.focus());
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
    return () => { cancelAnimationFrame(focusFrame); panel.removeEventListener('keydown', key); previous?.focus(); };
  }, [modal, narrow, open, ref, close]);
  return narrow;
}
