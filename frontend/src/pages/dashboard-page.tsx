import {
  Activity,
  ArrowUpRight,
  FileCheck2,
  FileClock,
  Files,
  Fingerprint,
  Layers3,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'
import { Link } from 'react-router-dom'

import {
  ClassificationBadge,
  DecisionBadge,
  StatusBadge,
} from '@/components/common/badges'
import { PageHeader } from '@/components/common/page-header'
import { StatCard } from '@/components/common/stat-card'
import { EmptyState, ErrorState, LoadingPanel } from '@/components/common/states'
import { buttonVariants } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/contexts/auth-context'
import {
  useAuditLogs,
  useDocuments,
  useRagHealth,
  useUserAttributes,
} from '@/hooks/queries'
import { CAP, hasCapability } from '@/lib/capabilities'
import { cn, formatRelative, pluralize, titleCase } from '@/lib/utils'

const attributeLabels: Record<string, string> = {
  department: 'Department',
  clearance_level: 'Clearance level',
  employment_status: 'Employment status',
  location: 'Location',
  designation: 'Designation',
}

function greeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

export function DashboardPage() {
  const { user } = useAuth()
  const documentsQuery = useDocuments()
  const attributesQuery = useUserAttributes(user?.user_id ?? null)
  const healthQuery = useRagHealth()
  const isAdmin = user ? hasCapability(user.roles, CAP.VIEW_AUDIT_LOGS) : false
  const auditQuery = useAuditLogs({ limit: 6, offset: 0 }, isAdmin)

  const documents = documentsQuery.data ?? []
  const completed = documents.filter(
    (document) => document.processing_status === 'COMPLETED',
  ).length
  const awaiting = documents.filter((document) =>
    ['UPLOADED', 'FAILED'].includes(document.processing_status),
  ).length
  const departments = new Set(documents.map((document) => document.department))
  const chunks = documents.reduce(
    (total, document) => total + document.total_chunks,
    0,
  )
  const recentDocuments = [...documents]
    .sort(
      (a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    )
    .slice(0, 5)
  const attributes = attributesQuery.data?.attributes ?? []
  const attributeMap = new Map(
    attributes.map((attribute) => [
      attribute.attribute_name,
      attribute.attribute_value,
    ]),
  )

  return (
    <div className="space-y-7">
      <PageHeader
        eyebrow="Workspace overview"
        title={`${greeting()}, ${user?.username ?? 'there'}.`}
        description="A live view of the documents, access attributes and retrieval services available to this session."
        actions={
          <>
            <Link
              to="/assistant"
              className={buttonVariants({ variant: 'default' })}
            >
              <Sparkles aria-hidden="true" />
              Ask the knowledge base
            </Link>
            <Link
              to="/documents"
              className={buttonVariants({ variant: 'outline' })}
            >
              Browse documents
            </Link>
          </>
        }
      />

      {documentsQuery.isError ? (
        <ErrorState
          message="Your accessible documents could not be loaded. The backend may be unavailable."
          onRetry={() => void documentsQuery.refetch()}
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Accessible documents"
              value={documentsQuery.isLoading ? '—' : documents.length}
              detail={`${pluralize(departments.size, 'department')} represented`}
              icon={Files}
            />
            <StatCard
              label="Ready for retrieval"
              value={documentsQuery.isLoading ? '—' : completed}
              detail={`${pluralize(chunks, 'indexed chunk')} available`}
              icon={FileCheck2}
              tone="success"
            />
            <StatCard
              label="Awaiting processing"
              value={documentsQuery.isLoading ? '—' : awaiting}
              detail="Uploaded or previously failed"
              icon={FileClock}
              tone={awaiting > 0 ? 'warning' : 'default'}
            />
            <StatCard
              label="Access attributes"
              value={attributesQuery.isLoading ? '—' : attributes.length}
              detail={
                attributes.length > 0
                  ? 'Policy context loaded'
                  : 'No attributes configured'
              }
              icon={Fingerprint}
              tone={attributes.length > 0 ? 'default' : 'warning'}
            />
          </div>

          <div className="grid gap-5 xl:grid-cols-[1.55fr_1fr]">
            <Card>
              <CardHeader className="flex-row items-start justify-between space-y-0">
                <div>
                  <CardTitle>Recent documents</CardTitle>
                  <CardDescription>
                    Only documents this session is authorized to read.
                  </CardDescription>
                </div>
                <Link
                  to="/documents"
                  className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                >
                  View all
                  <ArrowUpRight className="size-3" aria-hidden="true" />
                </Link>
              </CardHeader>
              <CardContent className="px-0 pb-1">
                {documentsQuery.isLoading ? (
                  <div className="px-5">
                    <LoadingPanel rows={4} />
                  </div>
                ) : recentDocuments.length === 0 ? (
                  <div className="px-5">
                    <EmptyState
                      title="No documents available"
                      description="There are no documents this session can read. Ask an administrator to grant access or upload a document."
                      icon={<Files className="size-5" aria-hidden="true" />}
                      action={
                        <Link
                          to="/documents"
                          className={buttonVariants({ variant: 'outline', size: 'sm' })}
                        >
                          Open document workspace
                        </Link>
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
                        <TableHead>Status</TableHead>
                        <TableHead className="hidden text-right sm:table-cell">Updated</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recentDocuments.map((document) => (
                        <TableRow key={document.document_id}>
                          <TableCell>
                            <Link
                              to={`/documents?document=${document.document_id}`}
                              className="block max-w-[240px] truncate font-medium text-foreground hover:text-primary"
                            >
                              {document.document_name}
                            </Link>
                            <span className="mt-0.5 block text-[11px] text-muted-foreground">
                              {document.document_type} · {pluralize(document.total_pages, 'page')}
                            </span>
                          </TableCell>
                          <TableCell className="hidden text-muted-foreground md:table-cell">
                            {titleCase(document.department)}
                          </TableCell>
                          <TableCell>
                            <ClassificationBadge
                              classification={document.classification}
                            />
                          </TableCell>
                          <TableCell>
                            <StatusBadge status={document.processing_status} />
                          </TableCell>
                          <TableCell className="hidden text-right text-xs text-muted-foreground sm:table-cell">
                            {formatRelative(document.updated_at)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>

            <div className="space-y-5">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Fingerprint className="size-4 text-primary" aria-hidden="true" />
                    Your access profile
                  </CardTitle>
                  <CardDescription>
                    ABAC attributes used in document authorization.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {attributesQuery.isLoading ? (
                    <div className="space-y-2">
                      {Array.from({ length: 4 }).map((_, index) => (
                        <div
                          key={index}
                          className="h-8 animate-pulse rounded-md bg-muted/60"
                        />
                      ))}
                    </div>
                  ) : attributes.length === 0 ? (
                    <p className="rounded-lg border border-warning/20 bg-warning/5 p-3 text-xs leading-5 text-muted-foreground">
                      No attributes are configured for your account. Documents
                      with attribute requirements may remain inaccessible until
                      an administrator sets them.
                    </p>
                  ) : (
                    <>
                      {['department', 'clearance_level', 'employment_status', 'location'].map(
                        (name) =>
                          attributeMap.has(name) ? (
                            <div
                              key={name}
                              className="flex items-center justify-between gap-3 border-b border-border/50 pb-2 last:border-0 last:pb-0"
                            >
                              <span className="text-xs text-muted-foreground">
                                {attributeLabels[name]}
                              </span>
                              <span className="text-right text-xs font-medium text-foreground">
                                {titleCase(attributeMap.get(name) ?? '')}
                              </span>
                            </div>
                          ) : null,
                      )}
                      {attributes
                        .filter(
                          (attribute) =>
                            ![
                              'department',
                              'clearance_level',
                              'employment_status',
                              'location',
                            ].includes(attribute.attribute_name),
                        )
                        .map((attribute) => (
                          <div
                            key={attribute.attribute_name}
                            className="flex items-center justify-between gap-3 border-b border-border/50 pb-2 last:border-0 last:pb-0"
                          >
                            <span className="text-xs text-muted-foreground">
                              {attributeLabels[attribute.attribute_name] ??
                                titleCase(attribute.attribute_name)}
                            </span>
                            <span className="text-right text-xs font-medium text-foreground">
                              {titleCase(attribute.attribute_value)}
                            </span>
                          </div>
                        ))}
                    </>
                  )}
                  <div className="flex flex-wrap gap-1 pt-1">
                    {user?.roles.map((role) => (
                      <span
                        key={role}
                        className="rounded-md border border-border bg-muted/40 px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                      >
                        {titleCase(role)}
                      </span>
                    ))}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Activity className="size-4 text-primary" aria-hidden="true" />
                    Retrieval health
                  </CardTitle>
                  <CardDescription>
                    Live dependency status reported by the backend.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {healthQuery.isLoading ? (
                    <div className="space-y-2">
                      {Array.from({ length: 4 }).map((_, index) => (
                        <div
                          key={index}
                          className="h-7 animate-pulse rounded-md bg-muted/60"
                        />
                      ))}
                    </div>
                  ) : healthQuery.isError || !healthQuery.data ? (
                    <p className="text-xs text-destructive">
                      The RAG health endpoint is unreachable.
                    </p>
                  ) : (
                    <div className="space-y-2.5">
                      {Object.entries(healthQuery.data.components).map(
                        ([name, component]) => (
                          <div
                            key={name}
                            className="flex items-center justify-between gap-3"
                          >
                            <span className="text-xs text-muted-foreground">
                              {titleCase(name)}
                            </span>
                            <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                              <span
                                className={cn(
                                  'size-1.5 rounded-full',
                                  ['ok', 'ready', 'configured'].includes(
                                    component.status,
                                  )
                                    ? 'bg-success'
                                    : 'bg-destructive',
                                )}
                              />
                              {titleCase(component.status)}
                            </span>
                          </div>
                        ),
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}

      {isAdmin ? (
        <Card>
          <CardHeader className="flex-row items-start justify-between space-y-0">
            <div>
              <CardTitle className="flex items-center gap-2">
                <ShieldAlert className="size-4 text-primary" aria-hidden="true" />
                Recent security activity
              </CardTitle>
              <CardDescription>
                Latest tenant-scoped events from the append-only audit trail.
              </CardDescription>
            </div>
            <Link
              to="/security"
              className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              Open security
              <ArrowUpRight className="size-3" aria-hidden="true" />
            </Link>
          </CardHeader>
          <CardContent>
            {auditQuery.isLoading ? (
              <LoadingPanel rows={3} />
            ) : auditQuery.isError ? (
              <ErrorState
                message="Security events could not be loaded."
                onRetry={() => void auditQuery.refetch()}
              />
            ) : (auditQuery.data ?? []).length === 0 ? (
              <EmptyState
                title="No audit events yet"
                description="Authorization and administration events will appear here as activity occurs."
                icon={<Layers3 className="size-5" aria-hidden="true" />}
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead className="hidden md:table-cell">Resource</TableHead>
                    <TableHead>Decision</TableHead>
                    <TableHead className="hidden lg:table-cell">Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(auditQuery.data ?? []).map((event) => (
                    <TableRow key={event.event_id}>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                        {formatRelative(event.timestamp)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {event.action}
                      </TableCell>
                      <TableCell className="hidden text-xs text-muted-foreground md:table-cell">
                        {event.resource_type}
                      </TableCell>
                      <TableCell>
                        <DecisionBadge decision={event.decision} />
                      </TableCell>
                      <TableCell className="hidden max-w-[320px] truncate text-xs text-muted-foreground lg:table-cell">
                        {event.reason}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
