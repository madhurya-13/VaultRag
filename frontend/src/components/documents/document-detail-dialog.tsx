import { useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  FileSearch,
  KeyRound,
  Layers3,
  Loader2,
  Play,
  RefreshCw,
} from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import {
  ClassificationBadge,
  StatusBadge,
} from '@/components/common/badges'
import { PermissionsDialog } from '@/components/documents/permissions-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  useDocumentChunks,
  useDocumentStatus,
} from '@/hooks/queries'
import { api, getErrorMessage } from '@/lib/api'
import {
  formatBytes,
  formatDateTime,
  formatRelative,
  titleCase,
  truncateMiddle,
} from '@/lib/utils'
import type { Document } from '@/types/api'

function DetailItem({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1">
      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <div className="text-xs text-foreground">{children}</div>
    </div>
  )
}

export function DocumentDetailDialog({
  document,
  open,
  onOpenChange,
  focusPage,
  canProcess,
  canViewChunks,
  canManagePermissions,
  onChanged,
}: {
  document: Document | null
  open: boolean
  onOpenChange: (open: boolean) => void
  focusPage?: number | null
  canProcess: boolean
  canViewChunks: boolean
  canManagePermissions: boolean
  onChanged?: () => void
}) {
  const queryClient = useQueryClient()
  const [permissionsOpen, setPermissionsOpen] = useState(false)
  const [processing, setProcessing] = useState(false)
  const statusQuery = useDocumentStatus(document?.document_id ?? null, open)
  const chunksQuery = useDocumentChunks(
    document?.document_id ?? null,
    open && canViewChunks,
  )

  if (!document) {
    return null
  }

  const status = statusQuery.data
  const processable =
    status?.processing_status === 'UPLOADED' ||
    status?.processing_status === 'FAILED' ||
    document.processing_status === 'UPLOADED' ||
    document.processing_status === 'FAILED'

  const process = async () => {
    setProcessing(true)
    try {
      await api.post(`/documents/${document.document_id}/process`)
      await queryClient.invalidateQueries({ queryKey: ['documents'] })
      await statusQuery.refetch()
      await chunksQuery.refetch()
      toast.success('Processing started', {
        description: 'Extraction, chunking and embeddings are running.',
      })
      onChanged?.()
    } catch (error) {
      toast.error('Could not process document', {
        description: getErrorMessage(error),
      })
    } finally {
      setProcessing(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <div className="mb-2 flex size-10 items-center justify-center rounded-full border border-primary/25 bg-primary/10 text-primary">
              <FileSearch className="size-5" aria-hidden="true" />
            </div>
            <DialogTitle className="pr-8">{document.document_name}</DialogTitle>
            <DialogDescription>
              {document.document_type} · {titleCase(document.department)} ·{' '}
              {document.total_pages} pages
              {focusPage ? ` · cited from page ${focusPage}` : ''}
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={status?.processing_status ?? document.processing_status} />
            <ClassificationBadge classification={document.classification} />
            <Badge variant="outline">
              Clearance {document.required_clearance_level}
            </Badge>
            <Badge variant={document.is_active ? 'success' : 'destructive'}>
              {document.is_active ? 'Active' : 'Inactive'}
            </Badge>
            <div className="ml-auto flex gap-2">
              {canManagePermissions ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPermissionsOpen(true)}
                >
                  <KeyRound aria-hidden="true" />
                  Manage access
                </Button>
              ) : null}
              {canProcess ? (
                <Button
                  size="sm"
                  onClick={() => void process()}
                  disabled={!processable || processing}
                  title={
                    processable
                      ? 'Process this document'
                      : 'Only uploaded or failed documents can be processed'
                  }
                >
                  {processing ? (
                    <Loader2 className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Play aria-hidden="true" />
                  )}
                  {processing
                    ? 'Starting…'
                    : status?.processing_status === 'FAILED'
                      ? 'Retry processing'
                      : 'Process now'}
                </Button>
              ) : null}
            </div>
          </div>

          <div className="grid gap-4 rounded-xl border border-border/70 bg-muted/15 p-4 sm:grid-cols-2 lg:grid-cols-4">
            <DetailItem label="Uploaded">
              {formatDateTime(document.created_at)}
            </DetailItem>
            <DetailItem label="Last updated">
              {formatDateTime(document.updated_at)}
            </DetailItem>
            <DetailItem label="File size">
              {formatBytes(document.file_size)}
            </DetailItem>
            <DetailItem label="Embedding model">
              <span className="font-mono text-[11px]">
                {status?.embedding_model ?? document.embedding_model ?? 'Not processed'}
              </span>
            </DetailItem>
            <DetailItem label="Pages">
              {status?.total_pages ?? document.total_pages}
            </DetailItem>
            <DetailItem label="Chunks">
              {status?.total_chunks ?? document.total_chunks}
            </DetailItem>
            <DetailItem label="File hash">
              <span className="font-mono text-[10px]">
                {document.file_hash
                  ? truncateMiddle(document.file_hash, 14, 8)
                  : 'Not available'}
              </span>
            </DetailItem>
            <DetailItem label="Ingestion job">
              <span className="font-mono text-[10px]">
                {status?.job_id ? truncateMiddle(status.job_id, 8, 4) : '—'}
              </span>
            </DetailItem>
          </div>

          {status?.error_code ? (
            <div className="flex gap-2.5 rounded-lg border border-destructive/25 bg-destructive/10 p-3 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <div>
                <p className="font-medium">{status.error_code}</p>
                <p className="mt-0.5 leading-5 opacity-90">
                  {status.error_message ?? 'The ingestion job failed.'}
                </p>
              </div>
            </div>
          ) : null}

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Layers3 className="size-4 text-primary" aria-hidden="true" />
              <p className="text-sm font-medium text-foreground">
                Ingestion details
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                void statusQuery.refetch()
                void chunksQuery.refetch()
              }}
              disabled={statusQuery.isFetching}
            >
              <RefreshCw
                className={statusQuery.isFetching ? 'animate-spin' : undefined}
                aria-hidden="true"
              />
              Refresh
            </Button>
          </div>

          {statusQuery.isError ? (
            <p className="rounded-lg border border-destructive/25 bg-destructive/10 p-3 text-xs text-destructive">
              {getErrorMessage(statusQuery.error)}
            </p>
          ) : status?.job_status ? (
            <div className="grid gap-2 rounded-lg border border-border/60 bg-card/50 p-3 text-xs sm:grid-cols-3">
              <p>
                <span className="text-muted-foreground">Job status: </span>
                {status.job_status}
              </p>
              <p>
                <span className="text-muted-foreground">Started: </span>
                {formatRelative(status.started_at)}
              </p>
              <p>
                <span className="text-muted-foreground">Completed: </span>
                {formatRelative(status.completed_at)}
              </p>
            </div>
          ) : null}

          {canViewChunks ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-foreground">
                  Approved chunk inspection
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {chunksQuery.data
                    ? `${chunksQuery.data.length} chunks`
                    : chunksQuery.isLoading
                      ? 'Loading…'
                      : ''}
                </p>
              </div>
              {chunksQuery.isError ? (
                <p className="rounded-lg border border-warning/20 bg-warning/5 p-3 text-xs text-muted-foreground">
                  {getErrorMessage(chunksQuery.error)}
                </p>
              ) : chunksQuery.isLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }).map((_, index) => (
                    <div
                      key={index}
                      className="h-16 animate-pulse rounded-lg bg-muted/60"
                    />
                  ))}
                </div>
              ) : (chunksQuery.data ?? []).length === 0 ? (
                <p className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
                  No chunks stored yet. Process the document to create embeddings.
                </p>
              ) : (
                <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                  {(chunksQuery.data ?? []).map((chunk) => (
                    <div
                      key={chunk.chunk_id}
                      className="rounded-lg border border-border/60 bg-card/50 p-3"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <Badge variant="secondary" className="normal-case tracking-normal">
                          Chunk {chunk.chunk_index + 1}
                        </Badge>
                        <span className="text-[10px] text-muted-foreground">
                          Pages {chunk.page_start}
                          {chunk.page_end !== chunk.page_start
                            ? `–${chunk.page_end}`
                            : ''}
                        </span>
                      </div>
                      <p className="mt-2 line-clamp-4 text-xs leading-5 text-muted-foreground">
                        {chunk.chunk_text}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      {canManagePermissions ? (
        <PermissionsDialog
          document={document}
          open={permissionsOpen}
          onOpenChange={setPermissionsOpen}
        />
      ) : null}
    </>
  )
}
