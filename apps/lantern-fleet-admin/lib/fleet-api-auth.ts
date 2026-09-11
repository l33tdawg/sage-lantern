import { NextResponse } from 'next/server';
import { getChatGPTUser, type ChatGPTUser } from '@/app/chatgpt-auth';
import {
  authorizeFleetCapability,
  parseRoleAssignments,
  type FleetCapability,
  type RoleAssignment,
} from './operator-authorization';

export type AuthorizedOperator = {
  user: ChatGPTUser;
  assignment: RoleAssignment;
};

export async function requireFleetCapability(
  capability: FleetCapability,
): Promise<AuthorizedOperator | NextResponse> {
  const user = await getChatGPTUser();
  if (!user) {
    return NextResponse.json({ error: 'authentication_required' }, { status: 401 });
  }

  const assignments = parseRoleAssignments(process.env.FLEET_ROLE_ASSIGNMENTS_JSON);
  const assignment = authorizeFleetCapability(
    user.userId,
    capability,
    { type: 'global', id: '*' },
    assignments,
  );
  if (!assignment) {
    return NextResponse.json(
      { error: 'fleet_authorization_required', user_id: user.userId },
      { status: 403 },
    );
  }

  return { user, assignment };
}

export function isAuthorizationResponse(
  value: AuthorizedOperator | NextResponse,
): value is NextResponse {
  return value instanceof NextResponse;
}

export function requireSameOrigin(request: Request): NextResponse | null {
  const origin = request.headers.get('origin');
  const forwardedHost = request.headers.get('x-forwarded-host');
  const host = forwardedHost ?? request.headers.get('host');
  if (!origin || !host) {
    return NextResponse.json({ error: 'same_origin_required' }, { status: 403 });
  }

  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return NextResponse.json({ error: 'invalid_origin' }, { status: 403 });
  }
  if (originHost.toLowerCase() !== host.toLowerCase()) {
    return NextResponse.json({ error: 'cross_origin_rejected' }, { status: 403 });
  }
  return null;
}
