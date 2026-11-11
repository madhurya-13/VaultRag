import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Eye,
  FilterX,
  History,
  Search,
  ShieldAlert,
  ShieldCheck,
  UserRound,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { format, subDays } from 'date-fns'

import {
  DecisionBadge,
} from '@/components/common/badges'
import { PageHeader } from '@/components/common/page-header'
import { StatCard } from '@/components/common/stat-card'
import { EmptyState, ErrorState, LoadingPanel } from '@/components/common/states'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Select } from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  useAuditLogs,
  useSecurityAnalytics,
  useUsers,
} from '@/hooks/queries'
import { getErrorMessage } from '@/lib/api'
import {
  formatDateTime,
  formatRelative,
  pluralize,
  titleCase,
} from '@/lib/utils'
import type { AuditLog } from '@/types/api'

const CHART_COLORS = ['#22d3ee', '#34d399', '#fbbf24', '#fb7185', '#818cf8', '#38bdf8']

const tooltipStyle = {
  backgroundColor: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: '8px',
  color: 'hsl(var(--foreground))',
  fontSize: '12px',
}

export function SecurityPage() {
  const [days, setDays] = useState(7)
  const analyticsQuery = useSecurityAnalytics(days)
  const usersQuery = useUsers()
  const [decision, setDecision] = useState('')
  const [action, setAction] = useState('')
  const [resourceType, setResourceType] = useState('')
  const [userId, setUserId] = useState('')
  const [offset, setOffset] = useState(0)
  const [selectedEvent, setSelectedEvent] = useState<AuditLog | null>(null)
  const limit = 50

  const filters = {
    limit,
    offset,
    ...(decision ? { decision } : {}),
    ...(action ? { action } : {}),
    ...(resourceType ? { resource_type: resourceType } : {}),
    ...(userId ? { user_id: userId } : {}),
  }
  const auditQuery = useAuditLogs(filters)
  const analytics = analyticsQuery.data
  const users = usersQuery.data ?? []
  const userMap = useMemo(
    () =>
      new Map(
        users.map((user) => [user.user_id, user.email || user.username]),
      ),
    [users],
  )

  const actionOptions = useMemo(() => {
    const values = new Set(
      (analytics?.by_action ?? []).map((item) => item.key),
    )
    if (action) values.add(action)
    return Array.from(values).sort()
  }, [analytics, action])

  const resourceOptions = useMemo(() => {
    const values = new Set(
      (analytics?.by_resource_type ?? []).map((item) => item.key),
    )
    if (resourceType) values.add(resourceType)
    return Array.from(values).sort()
  }, [analytics, resourceType])

  const dailyData = useMemo(() => {
    const existing = new Map(
      (analytics?.daily ?? []).map((point) => [point.date, point]),
    )
    const today = new Date()
    return Array.from({ length: days }).map((_, index) => {
      const date = subDays(today, days - 1 - index)
      const key = format(date, 'yyyy-MM-dd')
      const point = existing.get(key)
      return {
        date: format(date, 'MMM d'),
        allowed: point?.allowed ?? 0,
        denied: point?.denied ?? 0,
      }
    })
  }, [analytics, days])

  const topActions = (analytics?.by_action ?? []).slice(0, 7)
  const resourceData = (analytics?.by_resource_type ?? []).slice(0, 6)
  const denialRate =
    analytics && analytics.total_events > 0
      ? (analytics.denied_events / analytics.total_events) * 100
      : 0

  const resetAuditFilters = () => {
    setDecision('')
    setAction('')
    setResourceType('')
    setUserId('')
    setOffset(0)
  }

  const updateFilter = (setter: (value: string) => void, value: string) => {
    setter(value)
    setOffset(0)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Security monitoring"
        title="Security"
        description="Live tenant-scoped analytics and the append-only audit trail. Every retrieval, generation, permission and administration decision is recorded server-side."
        actions={
          <div className="flex items-center gap-2">
            <label htmlFor="analytics-days" className="text-xs text-muted-foreground">
              Window
            </label>
            <Select
              id="analytics-days"
              value={String(days)}
              onChange={(event) => setDays(Number(event.target.value))}
              className="w-28"
            >
              <option value="7">7 days</option>
              <option value="30">30 days</option>
              <option value="90">90 days</option>
            </Select>
          </div>
        }
      />

      <Tabs defaultValue="overview" className="space-y-4">
        <TabsList>
          <TabsTrigger value="overview">
            <ShieldCheck className="size-4" aria-hidden="true" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="audit">
            <History className="size-4" aria-hidden="true" />
            Audit log
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-5">
          {analyticsQuery.isError ? (
            <ErrorState
              message={getErrorMessage(analyticsQuery.error)}
              onRetry={() => void analyticsQuery.refetch()}
            />
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard
                  label="Audit events"
                  value={analyticsQuery.isLoading ? '—' : analytics?.total_events ?? 0}
                  detail={`Last ${days} days`}
                  icon={Activity}
                />
                <StatCard
                  label="Allowed decisions"
                  value={analyticsQuery.isLoading ? '—' : analytics?.allowed_events ?? 0}
                  detail="Policy permitted the action"
                  icon={CheckCircle2}
                  tone="success"
                />
                <StatCard
                  label="Denied decisions"
                  value={analyticsQuery.isLoading ? '—' : analytics?.denied_events ?? 0}
                  detail="Authorization rejected"
                  icon={ShieldAlert}
                  tone={(analytics?.denied_events ?? 0) > 0 ? 'danger' : 'default'}
                />
                <StatCard
                  label="Denial rate"
                  value={
                    analyticsQuery.isLoading
                      ? '—'
                      : `${denialRate.toFixed(1)}%`
                  }
                  detail={`${pluralize(analytics?.by_action.length ?? 0, 'action')} observed`}
                  icon={AlertTriangle}
                  tone={denialRate > 10 ? 'warning' : 'default'}
                />
              </div>

              <div className="grid gap-5 xl:grid-cols-[1.5fr_1fr]">
                <Card>
                  <CardHeader>
                    <CardTitle>Decision activity</CardTitle>
                    <CardDescription>
                      Allowed and denied audit decisions per day over the selected window.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    {analyticsQuery.isLoading ? (
                      <div className="h-[260px] animate-pulse rounded-lg bg-muted/50" />
                    ) : (
                      <div className="h-[260px] w-full">
                        <ResponsiveContainer width="100%" height="100%">
                          <AreaChart data={dailyData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                            <defs>
                              <linearGradient id="allowedGradient" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.35} />
                                <stop offset="95%" stopColor="#22d3ee" stopOpacity={0.02} />
                              </linearGradient>
                              <linearGradient id="deniedGradient" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#fb7185" stopOpacity={0.35} />
                                <stop offset="95%" stopColor="#fb7185" stopOpacity={0.02} />
                              </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.6} />
                            <XAxis
                              dataKey="date"
                              tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 10 }}
                              tickLine={false}
                              axisLine={false}
                              minTickGap={18}
                            />
                            <YAxis
                              allowDecimals={false}
                              tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 10 }}
                              tickLine={false}
                              axisLine={false}
                            />
                            <Tooltip contentStyle={tooltipStyle} />
                            <Area
                              type="monotone"
                              dataKey="allowed"
                              name="Allowed"
                              stroke="#22d3ee"
                              fill="url(#allowedGradient)"
                              strokeWidth={2}
                            />
                            <Area
                              type="monotone"
                              dataKey="denied"
                              name="Denied"
                              stroke="#fb7185"
                              fill="url(#deniedGradient)"
                              strokeWidth={2}
                            />
                          </AreaChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Resource distribution</CardTitle>
                    <CardDescription>Audit events by protected resource type.</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {analyticsQuery.isLoading ? (
                      <div className="h-[260px] animate-pulse rounded-lg bg-muted/50" />
                    ) : resourceData.length === 0 ? (
                      <EmptyState
                        title="No events in this window"
                        description="Security activity will appear after the next audited request."
                        className="border-none bg-transparent py-8"
                      />
                    ) : (
                      <div className="flex flex-col items-center gap-2">
                        <div className="h-[170px] w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                              <Pie
                                data={resourceData}
                                dataKey="count"
                                nameKey="key"
                                innerRadius={48}
                                outerRadius={72}
                                paddingAngle={3}
                                stroke="transparent"
                              >
                                {resourceData.map((entry, index) => (
                                  <Cell
                                    key={entry.key}
                                    fill={CHART_COLORS[index % CHART_COLORS.length]}
                                  />
                                ))}
                              </Pie>
                              <Tooltip contentStyle={tooltipStyle} />
                            </PieChart>
                          </ResponsiveContainer>
                        </div>
                        <div className="grid w-full grid-cols-2 gap-1.5">
                          {resourceData.map((entry, index) => (
                            <div key={entry.key} className="flex items-center gap-1.5 text-[10px]">
                              <span
                                className="size-1.5 rounded-full"
                                style={{
                                  backgroundColor: CHART_COLORS[index % CHART_COLORS.length],
                                }}
                              />
                              <span className="truncate text-muted-foreground">
                                {titleCase(entry.key)}
                              </span>
                              <span className="ml-auto font-medium text-foreground">
                                {entry.count}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              <div className="grid gap-5 xl:grid-cols-[1.5fr_1fr]">
                <Card>
                  <CardHeader>
                    <CardTitle>Most frequent actions</CardTitle>
                    <CardDescription>Audited actions ranked by event count.</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {analyticsQuery.isLoading ? (
                      <div className="h-[250px] animate-pulse rounded-lg bg-muted/50" />
                    ) : topActions.length === 0 ? (
                      <EmptyState
                        title="No actions recorded"
                        description="The selected window contains no audit events."
                        className="border-none bg-transparent py-8"
                      />
                    ) : (
                      <div className="h-[250px] w-full">
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart
                            data={topActions}
                            layout="vertical"
                            margin={{ top: 0, right: 12, left: 8, bottom: 0 }}
                          >
                            <CartesianGrid horizontal={false} stroke="hsl(var(--border))" opacity={0.5} />
                            <XAxis
                              type="number"
                              allowDecimals={false}
                              tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 10 }}
                              tickLine={false}
                              axisLine={false}
                            />
                            <YAxis
                              type="category"
                              dataKey="key"
                              width={118}
                              tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 10 }}
                              tickLine={false}
                              axisLine={false}
                            />
                            <Tooltip contentStyle={tooltipStyle} />
                            <Bar dataKey="count" name="Events" fill="#22d3ee" radius={[0, 4, 4, 0]} />
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Top actors</CardTitle>
                    <CardDescription>Users generating the most audit events.</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {analyticsQuery.isLoading ? (
                      <div className="space-y-2">
                        {Array.from({ length: 5 }).map((_, index) => (
                          <div key={index} className="h-8 animate-pulse rounded-md bg-muted/60" />
                        ))}
                      </div>
                    ) : (analytics?.top_actors ?? []).length === 0 ? (
                      <p className="py-6 text-center text-xs text-muted-foreground">
                        No actor activity in this window.
                      </p>
                    ) : (
                      <ol className="space-y-3">
                        {(analytics?.top_actors ?? []).map((actor, index) => {
                          const max = analytics?.top_actors[0]?.count ?? 1
                          return (
                            <li key={actor.user_id} className="space-y-1.5">
                              <div className="flex items-center justify-between gap-2 text-xs">
                                <span className="flex min-w-0 items-center gap-1.5">
                                  <span className="w-4 text-right font-mono text-[10px] text-muted-foreground">
                                    {index + 1}
                                  </span>
                                  <UserRound className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
                                  <span className="truncate text-foreground">
                                    {actor.username ?? userMap.get(actor.user_id) ?? actor.user_id}
                                  </span>
                                </span>
                                <span className="font-mono text-[10px] text-muted-foreground">
                                  {actor.count}
                                </span>
                              </div>
                              <div className="h-1 overflow-hidden rounded-full bg-muted">
                                <div
                                  className="h-full rounded-full bg-primary"
                                  style={{ width: `${Math.max(6, (actor.count / max) * 100)}%` }}
                                />
                              </div>
                            </li>
                          )
                        })}
                      </ol>
                    )}
                  </CardContent>
                </Card>
              </div>
            </>
          )}
        </TabsContent>

        <TabsContent value="audit" className="space-y-4">
          <div className="grid gap-3 rounded-xl border border-border/60 bg-card/50 p-3 sm:grid-cols-2 lg:grid-cols-5">
            <div className="space-y-1.5">
              <label htmlFor="audit-decision" className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Decision
              </label>
              <Select
                id="audit-decision"
                value={decision}
                onChange={(event) => updateFilter(setDecision, event.target.value)}
              >
                <option value="">All decisions</option>
                <option value="ALLOW">Allow</option>
                <option value="DENY">Deny</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="audit-action" className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Action
              </label>
              <Select
                id="audit-action"
                value={action}
                onChange={(event) => updateFilter(setAction, event.target.value)}
              >
                <option value="">All actions</option>
                {actionOptions.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="audit-resource" className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Resource
              </label>
              <Select
                id="audit-resource"
                value={resourceType}
                onChange={(event) => updateFilter(setResourceType, event.target.value)}
              >
                <option value="">All resources</option>
                {resourceOptions.map((value) => (
                  <option key={value} value={value}>
                    {titleCase(value)}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="audit-user" className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Actor
              </label>
              <Select
                id="audit-user"
                value={userId}
                onChange={(event) => updateFilter(setUserId, event.target.value)}
              >
                <option value="">All actors</option>
                {users.map((user) => (
                  <option key={user.user_id} value={user.user_id}>
                    {user.email}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex items-end gap-2">
              <Button variant="outline" className="flex-1" onClick={resetAuditFilters}>
                <FilterX aria-hidden="true" />
                Reset
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => void auditQuery.refetch()}
                disabled={auditQuery.isFetching}
                aria-label="Refresh audit log"
                title="Refresh audit log"
              >
                <Search className={auditQuery.isFetching ? 'animate-pulse' : undefined} aria-hidden="true" />
              </Button>
            </div>
          </div>

          <Card>
            <CardHeader className="flex-row items-start justify-between space-y-0">
              <div>
                <CardTitle>Audit trail</CardTitle>
                <CardDescription>
                  Newest first. {auditQuery.data ? `${auditQuery.data.length} events on this page.` : 'Tenant-scoped only.'}
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0 || auditQuery.isFetching}
                  onClick={() => setOffset((current) => Math.max(0, current - limit))}
                >
                  <ArrowLeft aria-hidden="true" />
                  Previous
                </Button>
                <span className="text-[11px] text-muted-foreground">
                  {offset / limit + 1}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={
                    auditQuery.isFetching ||
                    (auditQuery.data?.length ?? 0) < limit
                  }
                  onClick={() => setOffset((current) => current + limit)}
                >
                  Next
                  <ArrowRight aria-hidden="true" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="px-0 pb-2">
              {auditQuery.isLoading ? (
                <div className="px-5">
                  <LoadingPanel rows={7} />
                </div>
              ) : auditQuery.isError ? (
                <div className="px-5">
                  <ErrorState
                    message={getErrorMessage(auditQuery.error)}
                    onRetry={() => void auditQuery.refetch()}
                  />
                </div>
              ) : (auditQuery.data ?? []).length === 0 ? (
                <div className="px-5">
                  <EmptyState
                    title="No matching events"
                    description="No audit events match the current filters."
                    icon={<History className="size-5" aria-hidden="true" />}
                  />
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Time</TableHead>
                      <TableHead>Actor</TableHead>
                      <TableHead>Action</TableHead>
                      <TableHead className="hidden md:table-cell">Resource</TableHead>
                      <TableHead>Decision</TableHead>
                      <TableHead className="hidden lg:table-cell">Reason</TableHead>
                      <TableHead className="text-right">Detail</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(auditQuery.data ?? []).map((event) => (
                      <TableRow key={event.event_id}>
                        <TableCell className="whitespace-nowrap text-xs text-muted-foreground" title={formatDateTime(event.timestamp)}>
                          {formatRelative(event.timestamp)}
                        </TableCell>
                        <TableCell className="max-w-[160px] text-xs">
                          <span className="block truncate text-foreground">
                            {event.user_id
                              ? userMap.get(event.user_id) ?? event.user_id
                              : 'System'}
                          </span>
                          <span className="mt-0.5 block truncate font-mono text-[9px] text-muted-foreground/70">
                            {event.ip_address ?? 'no ip'}
                          </span>
                        </TableCell>
                        <TableCell className="font-mono text-[11px]">
                          {event.action}
                        </TableCell>
                        <TableCell className="hidden text-xs text-muted-foreground md:table-cell">
                          {event.resource_type}
                        </TableCell>
                        <TableCell>
                          <DecisionBadge decision={event.decision} />
                        </TableCell>
                        <TableCell className="hidden max-w-[280px] truncate text-xs text-muted-foreground lg:table-cell" title={event.reason}>
                          {event.reason}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setSelectedEvent(event)}
                            aria-label="Open audit event detail"
                          >
                            <Eye aria-hidden="true" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <p className="text-[11px] leading-5 text-muted-foreground">
            The audit endpoint supports actor, resource type, action and decision
            filters. Date-range and department filters are not exposed by the
            backend, so this view does not fabricate them.
          </p>
        </TabsContent>
      </Tabs>

      <Dialog open={selectedEvent !== null} onOpenChange={(open) => !open && setSelectedEvent(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <div className="mb-2 flex size-10 items-center justify-center rounded-full border border-primary/25 bg-primary/10 text-primary">
              <History className="size-5" aria-hidden="true" />
            </div>
            <DialogTitle>Audit event detail</DialogTitle>
            <DialogDescription>
              {selectedEvent?.event_id}
            </DialogDescription>
          </DialogHeader>
          {selectedEvent ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                ['Timestamp', formatDateTime(selectedEvent.timestamp)],
                ['Decision', selectedEvent.decision],
                ['Action', selectedEvent.action],
                ['Policy', selectedEvent.policy],
                ['Resource type', selectedEvent.resource_type],
                ['Resource ID', selectedEvent.resource_id ?? '—'],
                ['Actor', selectedEvent.user_id ? userMap.get(selectedEvent.user_id) ?? selectedEvent.user_id : 'System'],
                ['IP address', selectedEvent.ip_address ?? '—'],
                ['Request ID', selectedEvent.request_id],
                ['Tenant ID', selectedEvent.tenant_id],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-border/60 bg-muted/15 p-3">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                    {label}
                  </p>
                  <p className="mt-1 break-all text-xs text-foreground">{value}</p>
                </div>
              ))}
              <div className="sm:col-span-2 rounded-lg border border-border/60 bg-muted/15 p-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Reason
                </p>
                <p className="mt-1 text-xs leading-5 text-foreground">
                  {selectedEvent.reason}
                </p>
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}
