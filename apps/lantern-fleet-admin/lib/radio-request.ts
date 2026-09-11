import { communityRequest } from './community.ts';
import { parseRadioStatus } from './radio-status.ts';
import { getDeviceToken, subscribeDeviceSession } from './device-session.ts';
import { clearRadioSample, observeRadioSample } from './radio-sample.ts';

subscribeDeviceSession(clearRadioSample);

export async function requestRadioStatus(signal?: AbortSignal) {
  const token = getDeviceToken();
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timeout = setTimeout(abort, 30000);
  try {
    const value = parseRadioStatus(await communityRequest('radio/status', {}, controller.signal));
    if (!value || controller.signal.aborted) {
      throw new Error('Radio status unavailable');
    }
    const startedAt = performance.now();
    if (token && token === getDeviceToken()) observeRadioSample(value, token, startedAt);
    return { value, startedAt };
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}
