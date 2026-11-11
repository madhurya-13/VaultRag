import {
  Eye,
  FileSearch,
  Files,
  FilterX,
  Plus,
  Search,
  ShieldCheck,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  ClassificationBadge,
  StatusBadge,
} from '@/components/common/badges'
import { PageHeader } from '@/components/common/page-header'
import { EmptyState, ErrorState, LoadingPanel } from '@/components/common/states'
import { DocumentDetailDialog } from '@/components/documents/document-detail-dialog'
import { UploadDialog } from '@/components/documents/upload-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/contexts/auth-context'
import { useDocuments } from '@/hooks/queries'
import { CAP, hasCapability } from '@/lib/capabilities'
import {
  CLASSIFICATIONS,
  DEPARTMENTS,
  PROCESSING_STATUSES,
} from '@/lib/constants'
import {
  formatRelative,
  pluralize,
  titleCase,
} from '@/lib/utils'
import type { Document } from '@/types/api'

export function DocumentsPage() {
  const { user } = useAuth()
  const documentsQuery = useDocuments()
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [department, setDepartment] = useState('')
  const [classification, setClassification] = useState('')
  const [status, setStatus] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [selectedDocument, setSelectedDocument] = useState<Document | null>(null)
  const [focusPage, setFocusPage] = useState<number | null>(null)

  const documents = documentsQuery.data ?? []
  const canCreate = user ? hasCapability(user.roles, CAP.CREATE_DOCUMENT) : false
  const canProcess = user ? hasCapability(user.roles, CAP.PROCESS_DOCUMENT) : false
  const canViewChunks = user ? hasCapability(user.roles, CAP.VIEW_CHUNKS) : false
  const canManagePermissions = user
    ? hasCapability(user.roles, CAP.MANAGE_USERS)
    : false

  const documentParam = searchParams.get('document')
  const pageParam = Number(searchParams.get('page') || '')

  useEffect(() => {
    if (!documentParam || !documentsQuery.data) return
    const match = documentsQuery.data.find(
      (document) => document.document_id === documentParam,
    )
    if (match) {
      setSelectedDocument(match)
      setFocusPage(Number.isFinite(pageParam) && pageParam > 0 ? pageParam : null)
    }
  }, [documentParam, documentsQuery.data, pageParam])

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    return documents.filter((document) => {
      if (
        query &&
        !document.document_name.toLowerCase().includes(query) &&
        !document.department.toLowerCase().includes(query) &&
        !document.classification.toLowerCase().includes(query)
      ) {
        return false
      }
      if (department && document.department !== department) return false
      if (classification && document.classification !== classification) return false
      if (status && document.processing_status !== status) return false
      return true
    })
  }, [documents, search, department, classification, status])

  const filtersActive = Boolean(search || department || classification || status)

  const closeDetail = (open: boolean) => {
    if (open) return
    setSelectedDocument(null)
    setFocusPage(null)
    if (documentParam) {
      const next = new URLSearchParams(searchParams)
      next.delete('document')
      next.delete('page')
      setSearchParams(next, { replace: true })
    }
  }

  const openDocument = (document: Document) => {
    setSelectedDocument(document)
    setFocusPage(null)
  }

  const clearFilters = () => {
    setSearch('')
    setDepartment('')
    setClassification('')
    setStatus('')
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Document workspace"
        title="Documents"
        description="Browse the documents your session is authorized to read. Administrators can upload, process and manage exact-action access grants."
        actions={
          <>
            {canCreate ? (
              <Button onClick={() => setUploadOpen(true)}>
                <Plus aria-hidden="true" />
                Upload PDF
              </Button>
            ) : null}
            <Badge variant="outline" className="h-10 px-3 normal-case tracking-normal">
              <ShieldCheck className="size-3.5" aria-hidden="true" />
              Authorized view
            </Badge>
          </>
        }
      />

      <div className="grid gap-3 rounded-xl border border-border/60 bg-card/50 p-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="relative sm:col-span-2 lg:col-span-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search documents…"
            className="pl-9"
            aria-label="Search documents"
          />
        </div>
        <Select
          value={department}
          onChange={(event) => setDepartment(event.target.value)}
          aria-label="Filter by department"
        >
          <option value="">All departments</option>
          {DEPARTMENTS.map((value) => (
            <option key={value} value={value}>
              {titleCase(value)}
            </option>
          ))}
        </Select>
        <Select
          value={classification}
          onChange={(event) => setClassification(event.target.value)}
          aria-label="Filter by classification"
        >
          <option value="">All classifications</option>
          {CLASSIFICATIONS.map((value) => (
            <option key={value} value={value}>
              {titleCase(value)}
            </option>
          ))}
        </Select>
        <Select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {PROCESSING_STATUSES.map((value) => (
            <option key={value} value={value}>
              {titleCase(value)}
            </option>
          ))}
        </Select>
      </div>

      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0">
          <div>
            <CardTitle>Document inventory</CardTitle>
            <CardDescription>
              Showing {filtered.length} of {documents.length} accessible documents.
            </CardDescription>
          </div>
          {filtersActive ? (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              <FilterX aria-hidden="true" />
              Clear filters
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="px-0 pb-2">
          {documentsQuery.isLoading ? (
            <div className="px-5">
              <LoadingPanel rows={6} />
            </div>
          ) : documentsQuery.isError ? (
            <div className="px-5">
              <ErrorState
                message="The document inventory could not be loaded. Check that the backend is running and that your token is still valid."
                onRetry={() => void documentsQuery.refetch()}
              />
            </div>
          ) : documents.length === 0 ? (
            <div className="px-5">
              <EmptyState
                title="No accessible documents"
                description="There are no documents this session can read. Ask an administrator to grant access or upload the first PDF."
                icon={<Files className="size-5" aria-hidden="true" />}
                action={
                  canCreate ? (
                    <Button variant="outline" size="sm" onClick={() => setUploadOpen(true)}>
                      <Plus aria-hidden="true" />
                      Upload a PDF
                    </Button>
                  ) : undefined
                }
              />
            </div>
          ) : filtered.length === 0 ? (
            <div className="px-5">
              <EmptyState
                title="No documents match these filters"
                description="Try a broader search or reset the department, classification and status filters."
                icon={<FileSearch className="size-5" aria-hidden="true" />}
                action={
                  <Button variant="outline" size="sm" onClick={clearFilters}>
                    <FilterX aria-hidden="true" />
                    Clear filters
                  </Button>
                }
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Document</TableHead>
                  <TableHead className="hidden md:table-cell">Department</TableHead>
                  <TableHead>Classification</TableHead>
                  <TableHead className="hidden lg:table-cell">Chunks</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden text-right sm:table-cell">Updated</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((document) => (
                  <TableRow key={document.document_id}>
                    <TableCell>
                      <button
                        type="button"
                        className="block max-w-[280px] truncate text-left font-medium text-foreground hover:text-primary"
                        onClick={() => openDocument(document)}
                      >
                        {document.document_name}
                      </button>
                      <span className="mt-0.5 block text-[11px] text-muted-foreground">
                        {document.document_type} · {pluralize(document.total_pages, 'page')}
                        {document.required_clearance_level > 0
                          ? ` · clearance ${document.required_clearance_level}`
                          : ''}
                      </span>
                    </TableCell>
                    <TableCell className="hidden text-muted-foreground md:table-cell">
                      {titleCase(document.department)}
                    </TableCell>
                    <TableCell>
                      <ClassificationBadge classification={document.classification} />
                    </TableCell>
                    <TableCell className="hidden font-mono text-xs text-muted-foreground lg:table-cell">
                      {document.total_chunks}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={document.processing_status} />
                    </TableCell>
                    <TableCell className="hidden text-right text-xs text-muted-foreground sm:table-cell">
                      {formatRelative(document.updated_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openDocument(document)}
                      >
                        <Eye aria-hidden="true" />
                        Open
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <div className="flex items-start gap-2 rounded-lg border border-border/60 bg-muted/20 p-3 text-[11px] leading-5 text-muted-foreground">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden="true" />
        <p>
          This list is already filtered by the backend for READ access using your
          roles, attributes and active document grants. The UI does not infer or
          cache authorization decisions.
        </p>
      </div>

      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={(document) => {
          setSelectedDocument(document)
          setFocusPage(null)
        }}
      />

      <DocumentDetailDialog
        document={selectedDocument}
        open={selectedDocument !== null}
        onOpenChange={closeDetail}
        focusPage={focusPage}
        canProcess={canProcess}
        canViewChunks={canViewChunks}
        canManagePermissions={canManagePermissions}
        onChanged={() => void documentsQuery.refetch()}
      />
    </div>
  )
}
