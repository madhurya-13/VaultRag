import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'
import type {
  AuditLog,
  Document,
  DocumentChunk,
  DocumentPermission,
  DocumentStatus,
  RagHealth,
  Role,
  SecurityAnalytics,
  User,
  UserAttributesResponse,
} from '@/types/api'

export const queryKeys = {
  documents: ['documents'] as const,
  documentStatus: (id: string) => ['documents', id, 'status'] as const,
  documentChunks: (id: string) => ['documents', id, 'chunks'] as const,
  documentPermissions: (id: string, includeRevoked: boolean) =>
    ['documents', id, 'permissions', includeRevoked] as const,
  roles: ['roles'] as const,
  users: ['users'] as const,
  userAttributes: (id: string) => ['users', id, 'attributes'] as const,
  auditLogs: (filters: object) =>
    ['security', 'audit-logs', filters] as const,
  securityAnalytics: (days: number) =>
    ['security', 'analytics', days] as const,
  ragHealth: ['rag', 'health'] as const,
}

export function useDocuments() {
  return useQuery({
    queryKey: queryKeys.documents,
    queryFn: async () => {
      const { data } = await api.get<Document[]>('/documents')
      return data
    },
  })
}

export function useDocumentStatus(
  documentId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.documentStatus(documentId ?? ''),
    queryFn: async () => {
      const { data } = await api.get<DocumentStatus>(
        `/documents/${documentId}/status`,
      )
      return data
    },
    enabled: Boolean(documentId) && enabled,
    refetchInterval: (query) =>
      query.state.data?.processing_status === 'PROCESSING' ? 4000 : false,
  })
}

export function useDocumentChunks(
  documentId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.documentChunks(documentId ?? ''),
    queryFn: async () => {
      const { data } = await api.get<DocumentChunk[]>(
        `/documents/${documentId}/chunks`,
      )
      return data
    },
    enabled: Boolean(documentId) && enabled,
    retry: false,
  })
}

export function useDocumentPermissions(
  documentId: string | null,
  enabled: boolean,
  includeRevoked: boolean,
) {
  return useQuery({
    queryKey: queryKeys.documentPermissions(
      documentId ?? '',
      includeRevoked,
    ),
    queryFn: async () => {
      const { data } = await api.get<DocumentPermission[]>(
        `/documents/${documentId}/permissions`,
        { params: { include_revoked: includeRevoked } },
      )
      return data
    },
    enabled: Boolean(documentId) && enabled,
  })
}

export function useRoles(enabled = true) {
  return useQuery({
    queryKey: queryKeys.roles,
    queryFn: async () => {
      const { data } = await api.get<Role[]>('/roles')
      return data
    },
    enabled,
    staleTime: 60_000,
  })
}

export function useUsers(enabled = true) {
  return useQuery({
    queryKey: queryKeys.users,
    queryFn: async () => {
      const { data } = await api.get<User[]>('/users', {
        params: { limit: 200, offset: 0 },
      })
      return data
    },
    enabled,
    staleTime: 30_000,
  })
}

export function useUserAttributes(userId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.userAttributes(userId ?? ''),
    queryFn: async () => {
      const { data } = await api.get<UserAttributesResponse>(
        `/users/${userId}/attributes`,
      )
      return data
    },
    enabled: Boolean(userId) && enabled,
    retry: false,
  })
}

export interface AuditLogFilters {
  limit: number
  offset: number
  decision?: string
  action?: string
  resource_type?: string
  user_id?: string
}

export function useAuditLogs(filters: AuditLogFilters, enabled = true) {
  return useQuery({
    queryKey: queryKeys.auditLogs(filters),
    queryFn: async () => {
      const { data } = await api.get<AuditLog[]>('/security/audit-logs', {
        params: filters,
      })
      return data
    },
    enabled,
    placeholderData: keepPreviousData,
  })
}

export function useSecurityAnalytics(days: number, enabled = true) {
  return useQuery({
    queryKey: queryKeys.securityAnalytics(days),
    queryFn: async () => {
      const { data } = await api.get<SecurityAnalytics>(
        '/security/analytics',
        { params: { days } },
      )
      return data
    },
    enabled,
    placeholderData: keepPreviousData,
  })
}

export function useRagHealth(enabled = true) {
  return useQuery({
    queryKey: queryKeys.ragHealth,
    queryFn: async () => {
      const { data } = await api.get<RagHealth>('/rag/health')
      return data
    },
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}
