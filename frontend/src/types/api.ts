export type Decision = 'ALLOW' | 'DENY'
export type PermissionType = 'READ' | 'UPDATE' | 'DELETE' | 'MANAGE_PERMISSIONS'
export type Classification = 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL' | 'RESTRICTED'
export type ProcessingStatus =
  | 'UPLOADED'
  | 'PROCESSING'
  | 'COMPLETED'
  | 'FAILED'
  | 'ARCHIVED'

export interface LoginRequest {
  tenant_id: string
  email: string
  password: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export interface Me {
  user_id: string
  username: string
  email: string
  tenant_id: string
  organization_name: string
  roles: string[]
  is_active: boolean
  created_at: string
}

export interface Document {
  document_id: string
  tenant_id: string
  document_name: string
  document_type: string
  department: string
  classification: Classification
  required_clearance_level: number
  uploaded_by: string
  is_active: boolean
  created_at: string
  updated_at: string
  file_hash: string | null
  file_size: number | null
  processing_status: ProcessingStatus
  total_pages: number
  total_chunks: number
  embedding_model: string | null
}

export interface DocumentStatus {
  document_id: string
  processing_status: ProcessingStatus
  total_pages: number
  total_chunks: number
  embedding_model: string | null
  file_size: number | null
  job_id: string | null
  job_status: string | null
  error_code: string | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  updated_at: string
}

export interface DocumentChunk {
  chunk_id: string
  document_id: string
  chunk_index: number
  chunk_text: string
  page_start: number
  page_end: number
  metadata: Record<string, unknown>
  created_at: string
}

export interface DocumentPermission {
  permission_id: string
  tenant_id: string
  document_id: string
  role_id: string | null
  user_id: string | null
  permission_type: PermissionType
  granted_by: string
  granted_at: string
  expires_at: string | null
  is_active: boolean
  revoked_at: string | null
  revoked_by: string | null
  cross_department_allowed: boolean
}

export interface Role {
  role_id: string
  role_name: string
  description: string | null
  capabilities: string[]
  user_count: number
}

export interface User {
  user_id: string
  tenant_id: string
  username: string
  email: string
  is_active: boolean
  created_at: string
  roles: string[]
}

export interface UserAttribute {
  attribute_name: string
  attribute_value: string
  updated_at: string
}

export interface UserAttributesResponse {
  tenant_id: string
  user_id: string
  attributes: UserAttribute[]
}

export interface AuditLog {
  event_id: string
  tenant_id: string
  user_id: string | null
  resource_type: string
  resource_id: string | null
  action: string
  decision: Decision
  reason: string
  policy: string
  ip_address: string | null
  request_id: string
  timestamp: string
}

export interface SecurityCount {
  key: string
  count: number
}

export interface SecurityDailyCount {
  date: string
  allowed: number
  denied: number
  total: number
}

export interface SecurityActorCount {
  user_id: string
  username: string | null
  count: number
}

export interface SecurityAnalytics {
  days: number
  since: string
  total_events: number
  allowed_events: number
  denied_events: number
  by_action: SecurityCount[]
  by_resource_type: SecurityCount[]
  daily: SecurityDailyCount[]
  top_actors: SecurityActorCount[]
}

export interface RagSource {
  document_id: string
  document_name: string
  chunk_id: string
  page_start: number
  page_end: number
}

export interface RagAskResponse {
  answer: string
  sources: RagSource[]
  retrieved_chunks: number
  model: string | null
}

export interface RagHealthComponent {
  status: string
  detail: string | null
}

export interface RagHealth {
  status: string
  components: Record<string, RagHealthComponent>
}

export interface ApiErrorBody {
  detail?: string | Array<{ msg?: string; loc?: Array<string | number> }>
}
