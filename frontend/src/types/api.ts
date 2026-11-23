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

export type RagSearchMode = 'semantic' | 'keyword' | 'hybrid'
export type RagEvidenceLevel =
  | 'strong'
  | 'moderate'
  | 'limited'
  | 'insufficient'

export interface RagFilters {
  department?: string
  document_type?: string
  classification?: string | string[]
  status?: string
  uploaded_by?: string
  date_from?: string
  date_to?: string
}

export interface RagSource {
  document_id: string
  document_name: string
  chunk_id: string
  page_start: number
  page_end: number
  excerpt?: string | null
  document_version_id?: string | null
  version_number?: number | null
  citation_verified?: boolean
  similarity?: number | null
  rerank_score?: number | null
  matched_by?: string[]
}

export interface RagSourceGroup {
  document_id: string
  document_name: string
  sources: RagSource[]
}

export interface RagEvidence {
  level: RagEvidenceLevel
  score: number
  signals: Record<string, number | string | boolean>
}

export interface RagConflict {
  topic: string
  description: string
  values: string[]
  sources: RagSource[]
}

export interface RagTimings {
  retrieval_ms: number
  rerank_ms: number
  llm_ms: number
  total_ms: number
}

export interface RagRetrievalExplanation {
  mode: string
  semantic_contribution: boolean
  keyword_contribution: boolean
  graph_contribution: boolean
  reranked: boolean
  filters: Record<string, string>
  authorized_candidates: number
  denied_candidates: number
  evidence_rationale: string | null
}

export interface RagAskResponse {
  answer: string
  sources: RagSource[]
  source_groups?: RagSourceGroup[]
  retrieved_chunks: number
  model: string | null
  search_mode?: RagSearchMode
  evidence?: RagEvidence | null
  conflicts?: RagConflict[]
  insufficient_evidence?: boolean
  timings?: RagTimings
  explanation?: RagRetrievalExplanation | null
  feedback_enabled?: boolean
  conversation_id?: string | null
  message_id?: string | null
}

export interface RagHistoryResponse {
  items: Array<{
    question: string | null
    answer: string | null
    sources: RagSource[]
    conversation_id: string | null
    evidence_level: RagEvidenceLevel | null
    created_at: string | null
  }>
  persistent: boolean
}

export interface Conversation {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface ConversationCitation {
  document_id: string
  chunk_id: string
  page_start: number
  page_end: number
  document_name: string | null
  document_version_id?: string | null
  version_number?: number | null
  citation_verified?: boolean
}

export interface ConversationMessage {
  message_id: string
  role: string
  content: string
  citations: ConversationCitation[]
  evidence_level: RagEvidenceLevel | null
  created_at: string
}

export interface ConversationDetail {
  conversation: Conversation
  messages: ConversationMessage[]
}

export interface RetrievalModeCount {
  mode: string
  count: number
}

export interface RetrievalDailyPoint {
  date: string
  queries: number
  errors: number
}

export interface RetrievalAnalytics {
  days: number
  since: string
  total_queries: number
  by_search_mode: RetrievalModeCount[]
  average_retrieval_ms: number
  average_total_ms: number
  p95_total_ms: number
  citation_validation_rate: number
  insufficient_evidence_rate: number
  retrieval_error_rate: number
  daily: RetrievalDailyPoint[]
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

// ── Knowledge graph (Milestone 8) ──────────────────────────────────────────

export type KgSearchMode = 'graph' | 'graph_hybrid'

export interface KgEvidence {
  evidence_id: string
  document_id: string
  document_name: string
  chunk_id: string
  page_start: number
  page_end: number
  quote: string
  confidence: number
}

export interface KgNode {
  node_id: string
  node_type: string
  name: string
  description: string | null
  confidence: number
  mention_count: number
  evidence: KgEvidence[]
}

export interface KgEdge {
  edge_id: string
  source_node_id: string
  target_node_id: string
  relation_type: string
  confidence: number
  evidence: KgEvidence[]
}

export interface KgFinding {
  subject: string
  relation: string
  object: string
  quote: string
  document_id: string
  document_name: string
  chunk_id: string
  page_start: number
  page_end: number
  confidence: number
}

export interface KgQueryResponse {
  search_mode: string
  seed_count: number
  denied_documents: number
  truncated: boolean
  depth: number
  traversal_ms: number
  nodes: KgNode[]
  edges: KgEdge[]
  findings: KgFinding[]
  candidate_chunks: number
}

export interface KgConflictValue {
  value: string
  document_id: string
  document_name: string
  quote: string
}

export interface KgConflict {
  conflict_id: string
  subject: string
  relation_type: string
  description: string
  status: string
  values: KgConflictValue[]
  detected_at: string | null
}

export interface KgConflictListResponse {
  conflicts: KgConflict[]
}

// ── Enterprise integrations (Milestone 9) ──────────────────────────────────

export interface IntegrationProviderInfo {
  provider: string
  display_name: string
  available: boolean
  unavailable_reason: string | null
  capabilities: string[]
  supports_oauth: boolean
}

export interface IntegrationCredentialSummary {
  configured: boolean
  scopes: string | null
  token_expires_at: string | null
  updated_at: string
  revoked_at: string | null
}

export interface Integration {
  integration_id: string
  provider: string
  provider_display_name: string
  integration_name: string
  connection_status: string
  permission_sync_mode: string
  sync_enabled: boolean
  sync_interval_minutes: number
  next_sync_at: string | null
  last_sync_at: string | null
  configuration: Record<string, unknown>
  credential: IntegrationCredentialSummary | null
  supports_oauth: boolean
  webhook_configured: boolean
  created_at: string
  updated_at: string
}

export interface IntegrationRepository {
  repository_id: string
  external_repository_id: string
  name: string
  repository_kind: string
  is_selected: boolean
  sync_mode: 'FULL' | 'INCREMENTAL'
  last_synced_at: string | null
}

export interface IntegrationSyncJob {
  sync_job_id: string
  integration_id: string
  sync_type: string
  sync_status: string
  files_discovered: number
  files_processed: number
  files_failed: number
  versions_created: number
  documents_deleted: number
  attempt_count: number
  started_at: string | null
  completed_at: string | null
  next_attempt_at: string | null
  error_code: string | null
  error_summary: string | null
  created_at: string
}

export interface ExternalDocument {
  external_document_id: string
  internal_document_id: string
  external_file_id: string
  external_repository_id: string
  external_version_id: string | null
  source_url: string | null
  source_path: string | null
  content_checksum: string | null
  file_size: number | null
  sync_status: string
  permission_state: string
  last_modified_at: string | null
  last_synced_at: string | null
  deleted_at: string | null
}

export interface IntegrationIdentityMapping {
  mapping_id: string
  external_principal_id: string
  external_principal_type: string
  user_id: string | null
  role_id: string | null
  is_active: boolean
  created_at: string
}

export interface DocumentVersion {
  document_version_id: string
  document_id: string
  version_number: number
  external_version_id: string | null
  content_checksum: string
  file_size: number
  ingestion_status: string
  is_current: boolean
  source_modified_at: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// Milestone 10: RAG evaluation
// ---------------------------------------------------------------------------

export type EvaluationCategory =
  | 'single_document'
  | 'multi_document'
  | 'comparison'
  | 'numeric'
  | 'temporal'
  | 'conflicting'
  | 'graph_relationship'
  | 'insufficient_evidence'
  | 'unanswerable'
  | 'restricted_document'
  | 'metadata_filter'
  | 'document_version'
  | 'permission_change'
  | 'citation'

export type EvaluationConfiguration =
  | 'semantic'
  | 'keyword'
  | 'hybrid'
  | 'hybrid_rerank'
  | 'hybrid_graph'
  | 'hybrid_graph_rerank'

export interface EvaluationDataset {
  dataset_id: string
  name: string
  version: number
  description: string | null
  source: string
  query_count: number
  created_by: string
  created_at: string
}

export interface EvaluationCase {
  case_id: string
  case_key: string
  category: EvaluationCategory
  question: string
  expected_answer: string | null
  expected_answerable: boolean
  allowed_document_names: string[]
  gold_document_names: string[]
  gold_chunks: Array<{ document_name: string; chunk_index: number }>
  expected_citation_document_names: string[]
  restricted_document_names: string[]
  filters: Record<string, string>
  notes: string | null
}

export interface EvaluationDatasetDetail extends EvaluationDataset {
  cases: EvaluationCase[]
}

export interface EvaluationRun {
  run_id: string
  dataset_id: string
  dataset_name: string | null
  configuration: EvaluationConfiguration
  top_k: number
  answer_mode: 'retrieval' | 'generated'
  judge_enabled: boolean
  viewer_user_id: string | null
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'
  case_count: number
  completed_cases: number
  failed_cases: number
  authorization_violations: number
  metrics: Record<string, number | string | boolean | null>
  error_code: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export interface EvaluationResult {
  result_id: string
  run_id: string
  case_id: string
  case_key: string
  category: EvaluationCategory
  question: string
  expected_answer: string | null
  expected_answerable: boolean
  answer: string | null
  retrieved_chunk_ids: string[]
  retrieved_document_ids: string[]
  citations: Array<{
    domain_source_id: string
    document_id: string
    document_name: string
    chunk_id: string
    page_start: number
    page_end: number
  }>
  metrics: Record<string, number | string | boolean | null>
  passed: boolean
  error_code: string | null
  latency_ms: number
  created_at: string
}

export interface EvaluationMetrics {
  total_runs: number
  completed_runs: number
  failed_runs: number
  total_cases: number
  passed_cases: number
  failed_cases: number
  authorization_violations: number
  average_latency_ms: number
  by_configuration: Array<{
    configuration: string
    runs: number
    recall_at_5: number
    mrr: number
    ndcg_at_5: number
    citation_validity: number
    average_latency_ms: number
  }>
  recent_runs: EvaluationRun[]
}

export interface EvaluationConfigurationInfo {
  configuration: EvaluationConfiguration
  search_mode: string
  rerank: boolean
  graph: boolean
  description: string
}

// Milestone 10 phases 3-7: feedback, gaps, settings
export interface AnswerFeedback {
  feedback_id: string
  message_id: string
  conversation_id: string
  category: string
  sentiment: string
  comment: string | null
  review_status: string
  review_notes: string | null
  reviewer_id: string | null
  reviewed_at: string | null
  created_at: string
  updated_at: string
}

export interface AnswerFeedbackDetail extends AnswerFeedback {
  question: string | null
  answer: string | null
  citations: Array<{
    document_id: string
    document_name: string
    page_start: number
    page_end: number
    authorized: boolean
  }>
  hidden_citations: number
}

export interface FeedbackSummary {
  days: number
  total: number
  positive: number
  negative: number
  positive_rate: number
  unreviewed: number
  by_category: Array<{ category: string; count: number }>
  by_status: Array<{ status: string; count: number }>
  daily: Array<{ date: string; count: number }>
}

export interface KnowledgeGap {
  gap_id: string
  question: string
  topic_key: string
  classification: string
  severity: string
  status: string
  occurrence_count: number
  first_seen_at: string
  last_seen_at: string
  assigned_to: string | null
  resolution_notes: string | null
  resolution_document_id: string | null
  resolution_verified: boolean | null
  resolved_at: string | null
}

export interface KnowledgeGapDetail extends KnowledgeGap {
  events: Array<{
    event_id: string
    action: string
    actor_id: string | null
    from_status: string | null
    to_status: string | null
    detail: string | null
    created_at: string
  }>
}

export interface GapSummary {
  days: number
  total_queries: number
  total_signals: number
  visible_gaps: number
  unresolved_gaps: number
  by_classification: Array<{ classification: string; count: number }>
  by_status: Array<{ status: string; count: number }>
  minimum_group_size: number
  retention_days: number
}

export interface TenantSettings {
  settings: Record<string, unknown>
  version: number
}

export interface AnalyticsOverview {
  days: number
  documents: number
  documents_completed: number
  queries: number
  feedback: number
  knowledge_gaps: number
  evaluation_runs: number
  connected_integrations: number
}
