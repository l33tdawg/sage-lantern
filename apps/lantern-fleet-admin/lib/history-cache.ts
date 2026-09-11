import { parseDeviceHistory, type DeviceHistory } from './device-history.ts';
import { DeviceSessionError } from './device-session.ts';

export const HISTORY_REFRESH_MS = 30000;
export type HistorySnapshot = { history: DeviceHistory | null; refreshing: boolean; error: string };
export const EMPTY_HISTORY: HistorySnapshot = { history: null, refreshing: false, error: '' };

/** A single unlocked session's bounded text snapshot; never persisted. */
export class HistoryCache {
  private value = EMPTY_HISTORY;
  private listeners = new Set<() => void>();
  private generation = 0;
  private checkedAt = -Infinity;
  private changes = 0;
  private pending: { controller: AbortController; promise: Promise<DeviceHistory | null>; again: boolean } | null = null;
  private load: (signal: AbortSignal) => Promise<unknown>;
  private allowed: () => boolean;
  private now: () => number;
  constructor(load: (signal: AbortSignal) => Promise<unknown>, allowed: () => boolean, now = Date.now) {
    this.load = load; this.allowed = allowed; this.now = now;
  }

  getSnapshot = () => this.value;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private publish(value: HistorySnapshot) {
    this.value = value;
    for (const listener of this.listeners) listener();
  }
  clear = () => {
    this.generation++;
    this.pending?.controller.abort();
    this.pending = null;
    this.checkedAt = -Infinity;
    this.publish(EMPTY_HISTORY);
  };

  /** Even an uncertain delete/settings response requires a fresh read. */
  beginChange = () => {
    this.changes++;
    this.clear();
    let ended = false;
    return () => {
      if (ended) return;
      ended = true;
      this.changes--;
      this.clear();
      if (this.listeners.size) void this.refresh(true);
    };
  };

  refresh = (force = false): Promise<DeviceHistory | null> => {
    if (!this.allowed()) { this.clear(); return Promise.resolve(null); }
    if (this.changes) return Promise.resolve(null);
    if (this.pending) {
      if (force) this.pending.again = true;
      return this.pending.promise;
    }
    if (!force && this.now() - this.checkedAt < HISTORY_REFRESH_MS) return Promise.resolve(this.value.history);
    const generation = this.generation;
    const request = { controller: new AbortController(), promise: Promise.resolve<DeviceHistory | null>(null), again: false };
    this.pending = request;
    this.publish({ ...this.value, refreshing: true, error: '' });
    const current = () => this.pending === request && generation === this.generation && !request.controller.signal.aborted && this.allowed();
    request.promise = (async () => {
      try {
        const history = parseDeviceHistory(await this.load(request.controller.signal));
        if (!current()) return null;
        this.checkedAt = this.now();
        this.publish({ history, refreshing: false, error: '' });
        return history;
      } catch (error) {
        if (current()) {
          this.checkedAt = -Infinity;
          if (error instanceof DeviceSessionError) this.clear();
          else this.publish({ ...this.value, refreshing: false, error: 'Could not refresh saved conversations.' });
        }
        return null;
      } finally {
        if (this.pending === request) {
          this.pending = null;
          if (!this.allowed()) this.clear();
          else if (request.again) void this.refresh(true);
        }
      }
    })();
    return request.promise;
  };
}
