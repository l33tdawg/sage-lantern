export type FleetRole =
  | 'viewer'
  | 'technician'
  | 'fleet_operator'
  | 'release_manager'
  | 'auditor';

export type FleetCapability =
  | 'fleet.read'
  | 'enrollment.create'
  | 'enrollment.approve';

export type FleetScope = {
  type: 'global' | 'organization' | 'cluster';
  id: string;
};

export type RoleAssignment = {
  authenticatedUserId: string;
  role: FleetRole;
  scopeType: FleetScope['type'];
  scopeId: string;
  expiresAt: string | null;
};

const CAPABILITIES: Record<FleetRole, readonly FleetCapability[]> = {
  viewer: ['fleet.read'],
  technician: ['fleet.read', 'enrollment.create'],
  fleet_operator: ['fleet.read', 'enrollment.create', 'enrollment.approve'],
  release_manager: ['fleet.read'],
  auditor: ['fleet.read'],
};

const ROLES = new Set<FleetRole>([
  'viewer',
  'technician',
  'fleet_operator',
  'release_manager',
  'auditor',
]);

const SCOPE_TYPES = new Set<FleetScope['type']>([
  'global',
  'organization',
  'cluster',
]);

export function parseRoleAssignments(raw: string | undefined): RoleAssignment[] {
  if (!raw) return [];

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(value)) return [];

  return value.filter(isRoleAssignment);
}

export function authorizeFleetCapability(
  authenticatedUserId: string,
  capability: FleetCapability,
  scope: FleetScope,
  assignments: readonly RoleAssignment[],
  now = new Date(),
): RoleAssignment | null {
  return assignments.find((assignment) => {
    if (assignment.authenticatedUserId !== authenticatedUserId) return false;
    if (assignment.expiresAt && Date.parse(assignment.expiresAt) <= now.getTime()) {
      return false;
    }
    if (!CAPABILITIES[assignment.role].includes(capability)) return false;
    if (assignment.scopeType === 'global') return assignment.scopeId === '*';
    return assignment.scopeType === scope.type && assignment.scopeId === scope.id;
  }) ?? null;
}

function isRoleAssignment(value: unknown): value is RoleAssignment {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.authenticatedUserId === 'string' &&
    item.authenticatedUserId.length > 0 &&
    typeof item.role === 'string' &&
    ROLES.has(item.role as FleetRole) &&
    typeof item.scopeType === 'string' &&
    SCOPE_TYPES.has(item.scopeType as FleetScope['type']) &&
    typeof item.scopeId === 'string' &&
    item.scopeId.length > 0 &&
    (item.expiresAt === null ||
      (typeof item.expiresAt === 'string' &&
        Number.isFinite(Date.parse(item.expiresAt))))
  );
}
