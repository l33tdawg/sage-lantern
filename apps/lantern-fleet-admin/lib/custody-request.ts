import { communityRequest } from './community.ts';
import { parseCustodyStatus, type CustodyStatus } from './custody-status.ts';

export async function requestCustodyStatus(signal?: AbortSignal): Promise<CustodyStatus> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timeout = setTimeout(abort, 30000);
  try {
    const value = parseCustodyStatus(await communityRequest('custody/status', {}, controller.signal));
    if (!value || controller.signal.aborted) throw new Error('Custody status unavailable');
    return value;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}
