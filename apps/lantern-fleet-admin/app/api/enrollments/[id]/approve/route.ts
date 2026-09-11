import { NextResponse } from 'next/server';
import { fleetAuthorityRequest, FleetAuthorityError } from '@/lib/fleet-authority-client';
import { isAuthorizationResponse, requireFleetCapability, requireSameOrigin } from '@/lib/fleet-api-auth';

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const originError = requireSameOrigin(request);
  if (originError) return originError;
  const auth = await requireFleetCapability('enrollment.approve');
  if (isAuthorizationResponse(auth)) return auth;

  const { id } = await context.params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)) {
    return NextResponse.json({ error: 'invalid_enrollment_id' }, { status: 400 });
  }
  const body = await request.json().catch(() => null) as Record<string, unknown> | null;
  const comparisonCode = typeof body?.comparison_code === 'string'
    ? body.comparison_code.trim()
    : '';
  if (!/^[0-9]{6}$/.test(comparisonCode)) {
    return NextResponse.json({ error: 'invalid_comparison_code' }, { status: 400 });
  }

  try {
    return NextResponse.json(await fleetAuthorityRequest(
      `/v1/admin/enrollments/${encodeURIComponent(id)}/approve`,
      {
        method: 'POST',
        headers: { 'X-Fleet-Operator-Id': auth.user.userId },
        body: JSON.stringify({ comparison_code: comparisonCode }),
      },
    ));
  } catch (error) {
    if (error instanceof FleetAuthorityError) {
      return NextResponse.json(
        { error: error.message, authority: error.body },
        { status: error.status },
      );
    }
    return NextResponse.json({ error: 'internal_error' }, { status: 500 });
  }
}
