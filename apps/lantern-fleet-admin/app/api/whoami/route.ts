import { NextResponse } from 'next/server';
import { getChatGPTUser } from '@/app/chatgpt-auth';
import { authorizeFleetCapability, parseRoleAssignments } from '@/lib/operator-authorization';

export const dynamic = 'force-dynamic';

export async function GET() {
  const user = await getChatGPTUser();
  if (!user) {
    return NextResponse.json({ error: 'authentication_required' }, { status: 401 });
  }
  const assignments = parseRoleAssignments(process.env.FLEET_ROLE_ASSIGNMENTS_JSON);
  const scope = { type: 'global' as const, id: '*' };
  const read = authorizeFleetCapability(user.userId, 'fleet.read', scope, assignments);
  const create = authorizeFleetCapability(user.userId, 'enrollment.create', scope, assignments);
  const approve = authorizeFleetCapability(user.userId, 'enrollment.approve', scope, assignments);

  return NextResponse.json({
    user: { userId: user.userId, email: user.email, displayName: user.displayName },
    role: read?.role ?? null,
    capabilities: {
      read: Boolean(read),
      createEnrollment: Boolean(create),
      approveEnrollment: Boolean(approve),
    },
  });
}
