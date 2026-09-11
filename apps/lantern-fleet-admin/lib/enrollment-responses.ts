export type Enrollment = {
  enrollment_id: string;
  device_id: string;
  status: string;
  expires_at: string;
  comparison_code?: string | null;
  management_fingerprint?: string | null;
  approved_at?: string | null;
};

export type Session = {
  user: { userId: string; email: string; displayName: string };
  role: string | null;
  capabilities: { read: boolean; createEnrollment: boolean; approveEnrollment: boolean };
};

export type CreatedEnrollment = Enrollment & { setup_secret: string };

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid enrollment response');
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== 'string' || !value) throw new Error('Invalid enrollment response');
  return value;
}

function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('Invalid enrollment response');
  return value;
}

function timestamp(value: unknown): string {
  const result = text(value);
  if (!Number.isFinite(Date.parse(result))) throw new Error('Invalid enrollment response');
  return result;
}

export function parseEnrollmentSession(value: unknown): Session {
  const body = object(value);
  const user = object(body.user);
  const capabilities = object(body.capabilities);
  return {
    user: { userId: text(user.userId), email: text(user.email), displayName: text(user.displayName) },
    role: body.role === null ? null : text(body.role),
    capabilities: { read: boolean(capabilities.read), createEnrollment: boolean(capabilities.createEnrollment),
      approveEnrollment: boolean(capabilities.approveEnrollment) },
  };
}

function parseEnrollment(value: unknown): Enrollment {
  const body = object(value);
  const status = text(body.status);
  if (!['pending', 'claimed', 'approved', 'issued', 'expired'].includes(status)) throw new Error('Invalid enrollment response');
  const result: Enrollment = { enrollment_id: text(body.enrollment_id), device_id: text(body.device_id),
    status, expires_at: timestamp(body.expires_at) };
  for (const name of ['comparison_code', 'management_fingerprint', 'approved_at'] as const) {
    if (body[name] !== undefined) result[name] = body[name] === null ? null : text(body[name]);
  }
  if (result.comparison_code != null && !/^\d{6}$/.test(result.comparison_code)) throw new Error('Invalid enrollment response');
  if (result.approved_at != null) timestamp(result.approved_at);
  return result;
}

export function parseEnrollmentList(value: unknown): Enrollment[] {
  const body = object(value);
  if (!Array.isArray(body.enrollments) || body.enrollments.length > 500) throw new Error('Invalid enrollment response');
  const result = body.enrollments.map(parseEnrollment);
  if (new Set(result.map((entry) => entry.enrollment_id)).size !== result.length) throw new Error('Invalid enrollment response');
  return result;
}

export function parseCreatedEnrollment(value: unknown): CreatedEnrollment {
  const body = object(value);
  const enrollment = parseEnrollment(body);
  const secret = text(body.setup_secret);
  if (enrollment.status !== 'pending' || !/^[A-Za-z0-9_-]{43}$/.test(secret)) throw new Error('Invalid enrollment response');
  return { ...enrollment, setup_secret: secret };
}
