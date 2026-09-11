import { conversationRequest } from './conversation-threads.ts';
import { getDeviceToken, subscribeDeviceSession } from './device-session.ts';
import { HistoryCache, HISTORY_REFRESH_MS } from './history-cache.ts';

const allowed = () => !!getDeviceToken() && typeof document !== 'undefined' && !document.hidden;
export const savedHistory = new HistoryCache((signal) => conversationRequest('history/list', {}, signal), allowed);
let subscribers = 0;
let timer: ReturnType<typeof setInterval> | undefined;
subscribeDeviceSession(() => {
  savedHistory.clear();
  if (subscribers) void savedHistory.refresh();
});
const visibility = () => {
  if (document.hidden) savedHistory.clear();
  else void savedHistory.refresh();
};

export function subscribeSavedHistory(listener: () => void) {
  const unsubscribe = savedHistory.subscribe(listener);
  if (++subscribers === 1) {
    document.addEventListener('visibilitychange', visibility);
    timer = setInterval(() => { void savedHistory.refresh(); }, HISTORY_REFRESH_MS);
    void savedHistory.refresh();
  }
  return () => {
    unsubscribe();
    if (--subscribers === 0) {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', visibility);
      savedHistory.clear();
    }
  };
}
