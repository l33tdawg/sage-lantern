export type SetupNetwork = { ssid: string; signal: number; security: 'open' | 'wpa-psk' | 'unsupported' };
export type SetupStatus = { unit_id: string; interface: string; connected: boolean; ssid: string | null };

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid setup response');
  return value as Record<string, unknown>;
}

function ssid(value: unknown): value is string {
  return typeof value === 'string' && new TextEncoder().encode(value).length <= 32 && value.length > 0 && !/[\u0000-\u001f\u007f]/.test(value);
}

export function parseSetupStatus(value: unknown): SetupStatus {
  const result = object(value);
  if (typeof result.unit_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(result.unit_id)
      || typeof result.interface !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$/.test(result.interface)
      || typeof result.connected !== 'boolean' || !(result.ssid === null || ssid(result.ssid))
      || (result.connected && result.ssid === null)) throw new Error('Invalid setup status');
  return result as SetupStatus;
}

export function parseSetupNetworks(value: unknown): SetupNetwork[] {
  const result = object(value);
  if (!Array.isArray(result.networks) || result.networks.length > 128) throw new Error('Invalid network list');
  return result.networks.map((item) => {
    const network = object(item);
    if (!ssid(network.ssid) || !Number.isInteger(network.signal) || Number(network.signal) < 0 || Number(network.signal) > 100
        || !['open', 'wpa-psk', 'unsupported'].includes(String(network.security))) throw new Error('Invalid network');
    return network as SetupNetwork;
  });
}

export async function networkSetupRequest(action: 'status' | 'scan' | 'connect' | 'finish', token: string,
  payload: Record<string, unknown> = {}, signal?: AbortSignal): Promise<unknown> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('Setup code required');
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timeout = setTimeout(abort, action === 'connect' ? 180000 : 45000);
  try {
    const response = await fetch(`http://127.0.0.1:3195/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lantern-Setup': token },
      body: JSON.stringify(payload), credentials: 'omit', redirect: 'error', cache: 'no-store', signal: controller.signal,
    });
    if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? 'Setup code expired or unavailable' : 'Setup operation could not be confirmed');
    if (response.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') throw new Error('Invalid setup response');
    const reader = response.body?.getReader();
    if (!reader) throw new Error('Invalid setup response');
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.length;
        if (size > 32768) throw new Error('Invalid setup response');
        chunks.push(chunk.value);
      }
    } finally { await reader.cancel(); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } finally { clearTimeout(timeout); signal?.removeEventListener('abort', abort); }
}
