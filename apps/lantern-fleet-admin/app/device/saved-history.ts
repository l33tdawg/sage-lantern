'use client';
import { useSyncExternalStore } from 'react';
import { EMPTY_HISTORY } from '../../lib/history-cache';
import { savedHistory, subscribeSavedHistory } from '../../lib/saved-history';

export function useSavedHistory() {
  return useSyncExternalStore(subscribeSavedHistory, savedHistory.getSnapshot, () => EMPTY_HISTORY);
}
