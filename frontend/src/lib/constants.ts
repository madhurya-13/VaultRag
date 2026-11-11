export const DEPARTMENTS = [
  'ADMIN',
  'ENGINEERING',
  'FINANCE',
  'HR',
  'IT',
  'LEGAL',
  'OPERATIONS',
  'SALES',
  'SECURITY',
] as const

export const CLASSIFICATIONS = [
  'PUBLIC',
  'INTERNAL',
  'CONFIDENTIAL',
  'RESTRICTED',
] as const

export const PROCESSING_STATUSES = [
  'UPLOADED',
  'PROCESSING',
  'COMPLETED',
  'FAILED',
  'ARCHIVED',
] as const

export const PERMISSION_TYPES = [
  'READ',
  'UPDATE',
  'DELETE',
  'MANAGE_PERMISSIONS',
] as const

export const ROLE_NAMES = [
  'ADMIN',
  'HR',
  'FINANCE',
  'ENGINEERING',
  'EMPLOYEE',
] as const

export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024
