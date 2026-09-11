const DEFAULT_TIMEOUT_MS = 10_000;

export class FleetAuthorityError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: unknown,
  ) {
    super(message);
  }
}

export async function fleetAuthorityRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const baseUrl = process.env.FLEET_AUTHORITY_URL;
  const adminToken = process.env.FLEET_AUTHORITY_ADMIN_TOKEN;
  if (!baseUrl || !adminToken) {
    throw new FleetAuthorityError('Fleet Authority is not configured', 503, null);
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  try {
    const response = await fetch(new URL(path, `${baseUrl.replace(/\/$/, '')}/`), {
      ...init,
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${adminToken}`,
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers,
      },
      signal: controller.signal,
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      throw new FleetAuthorityError('Fleet Authority rejected the request', response.status, body);
    }
    return body as T;
  } catch (error) {
    if (error instanceof FleetAuthorityError) throw error;
    if (error instanceof Error && error.name === 'AbortError') {
      throw new FleetAuthorityError('Fleet Authority timed out', 504, null);
    }
    throw new FleetAuthorityError('Fleet Authority is unavailable', 502, null);
  } finally {
    clearTimeout(timeout);
  }
}
