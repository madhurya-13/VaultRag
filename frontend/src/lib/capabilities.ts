export const CAP = {
  MANAGE_USERS: 'MANAGE_USERS',
  MANAGE_ROLES: 'MANAGE_ROLES',
  CREATE_DOCUMENT: 'CREATE_DOCUMENT',
  PROCESS_DOCUMENT: 'PROCESS_DOCUMENT',
  VIEW_CHUNKS: 'VIEW_CHUNKS',
  VIEW_AUDIT_LOGS: 'VIEW_AUDIT_LOGS',
  MANAGE_ATTRIBUTES: 'MANAGE_ATTRIBUTES',
  MANAGE_INTEGRATIONS: 'MANAGE_INTEGRATIONS',
  MANAGE_EVALUATION: 'MANAGE_EVALUATION',
  VIEW_FEEDBACK: 'VIEW_FEEDBACK',
  MANAGE_KNOWLEDGE_GAPS: 'MANAGE_KNOWLEDGE_GAPS',
  VIEW_ANALYTICS: 'VIEW_ANALYTICS',
  MANAGE_TENANT_SETTINGS: 'MANAGE_TENANT_SETTINGS',
} as const

export type Capability = (typeof CAP)[keyof typeof CAP]

// Mirrors backend/app/core/security_constants.py. The backend remains the
// authority for every request; this map only controls navigation and affordances.
const ROLE_CAPABILITIES: Record<string, readonly Capability[]> = {
  ADMIN: [
    CAP.MANAGE_USERS,
    CAP.MANAGE_ROLES,
    CAP.CREATE_DOCUMENT,
    CAP.PROCESS_DOCUMENT,
    CAP.VIEW_CHUNKS,
    CAP.VIEW_AUDIT_LOGS,
    CAP.MANAGE_ATTRIBUTES,
    CAP.MANAGE_INTEGRATIONS,
    CAP.MANAGE_EVALUATION,
    CAP.VIEW_FEEDBACK,
    CAP.MANAGE_KNOWLEDGE_GAPS,
    CAP.VIEW_ANALYTICS,
    CAP.MANAGE_TENANT_SETTINGS,
  ],
  HR: [],
  FINANCE: [],
  ENGINEERING: [],
  EMPLOYEE: [],
}

export const CAPABILITY_LABELS: Record<string, string> = {
  MANAGE_USERS: 'Manage users',
  MANAGE_ROLES: 'Manage roles',
  CREATE_DOCUMENT: 'Upload documents',
  PROCESS_DOCUMENT: 'Process documents',
  VIEW_CHUNKS: 'Inspect chunks',
  VIEW_AUDIT_LOGS: 'View audit logs',
  MANAGE_ATTRIBUTES: 'Manage attributes',
  MANAGE_INTEGRATIONS: 'Manage integrations',
  MANAGE_EVALUATION: 'Manage RAG evaluation',
  VIEW_FEEDBACK: 'Review answer feedback',
  MANAGE_KNOWLEDGE_GAPS: 'Manage knowledge gaps',
  VIEW_ANALYTICS: 'View enterprise analytics',
  MANAGE_TENANT_SETTINGS: 'Manage tenant settings',
}

export const PERMISSION_LABELS: Record<string, string> = {
  READ: 'Read',
  UPDATE: 'Update',
  DELETE: 'Delete',
  MANAGE_PERMISSIONS: 'Manage permissions',
}

export function capabilitiesForRoles(roles: readonly string[]): Set<string> {
  const result = new Set<string>()
  roles.forEach((role) => {
    ROLE_CAPABILITIES[role]?.forEach((capability) => result.add(capability))
  })
  return result
}

export function hasCapability(
  roles: readonly string[],
  capability: string,
): boolean {
  return capabilitiesForRoles(roles).has(capability)
}

export function hasAnyCapability(
  roles: readonly string[],
  capabilities: readonly string[],
): boolean {
  const held = capabilitiesForRoles(roles)
  return capabilities.some((capability) => held.has(capability))
}

export function roleLabel(role: string): string {
  switch (role) {
    case 'ADMIN':
      return 'Administrator'
    case 'HR':
      return 'Human Resources'
    case 'FINANCE':
      return 'Finance'
    case 'ENGINEERING':
      return 'Engineering'
    case 'EMPLOYEE':
      return 'Employee'
    default:
      return role
  }
}
