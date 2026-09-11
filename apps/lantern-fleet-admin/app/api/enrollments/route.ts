import { NextResponse } from 'next/server';
import { fleetAuthorityRequest, FleetAuthorityError } from '@/lib/fleet-authority-client';
import { isAuthorizationResponse, requireFleetCapability, requireSameOrigin } from '@/lib/fleet-api-auth';

export const dynamic = 'force-dynamic';

export async function GET() {
  const auth = await requireFleetCapability('fleet.read');
  if (isAuthorizationResponse(auth)) return auth;
  try {
    return NextResponse.json(await fleetAuthorityRequest('/v1/admin/enrollments'));
  } catch (error) {
    return authorityError(error);
  }
}

export async function POST(request: Request) {
  const originError = requireSameOrigin(request);
  if (originError) return originError;
  const auth = await requireFleetCapability('enrollment.create');
  if (isAuthorizationResponse(auth)) return auth;

  const body = await request.json().catch(() => null) as Record<string, unknown> | null;
  const deviceId = typeof body?.device_id === 'string' ? body.device_id.trim() : '';
  const expires = body?.expires_in_seconds;
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$/.test(deviceId)) {
    return NextResponse.json({ error: 'invalid_device_id' }, { status: 400 });
  }
  if (expires !== undefined && (!Number.isInteger(expires) || Number(expires) < 60 || Number(expires) > 86_400)) {
    return NextResponse.json({ error: 'invalid_expiry' }, { status: 400 });
  }

  try {
    const response = await fleetAuthorityRequest('/v1/admin/enrollments', {
      method: 'POST',
      headers: { 'X-Fleet-Operator-Id': auth.user.userId },
      body: JSON.stringify({
        device_id: deviceId,
        ...(expires === undefined ? {} : { expires_in_seconds: expires }),
      }),
    });
    return NextResponse.json(response, { status: 201 });
  } catch (error) {
    return authorityError(error);
  }
}

function authorityError(error: unknown) {
  if (error instanceof FleetAuthorityError) {
    return NextResponse.json(
      { error: error.message, authority: error.body },
      { status: error.status },
    );
  }
  return NextResponse.json({ error: 'internal_error' }, { status: 500 });
}
