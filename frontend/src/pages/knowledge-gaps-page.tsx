import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Lightbulb, SearchX, ShieldQuestion } from 'lucide-react'

import { PageHeader } from '@/components/common/page-header'
import { StatCard } from '@/components/common/stat-card'
import { EmptyState, ErrorState, LoadingPanel } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { api, getErrorMessage } from '@/lib/api'
import { formatRelative, titleCase } from '@/lib/utils'
import type { GapSummary, KnowledgeGap, KnowledgeGapDetail } from '@/types/api'

export function KnowledgeGapsPage() {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [nextStatus, setNextStatus] = useState('IN_PROGRESS')

  const summary = useQuery({
    queryKey: ['gaps', 'summary'],
    queryFn: async () => (await api.get<GapSummary>('/admin/knowledge-gaps/summary')).data,
  })
  const list = useQuery({
    queryKey: ['gaps', 'list', status],
    queryFn: async () =>
      (await api.get<{ gaps: KnowledgeGap[]; total: number }>('/admin/knowledge-gaps', {
        params: status ? { status } : {},
      })).data,
  })
  const detail = useQuery({
    queryKey: ['gaps', 'detail', selected],
    enabled: Boolean(selected),
    queryFn: async () => (await api.get<KnowledgeGapDetail>(`/admin/knowledge-gaps/${selected}`)).data,
  })

  const updateStatus = useMutation({
    mutationFn: async () => {
      if (!selected) return
      await api.post(`/admin/knowledge-gaps/${selected}/status`, { status: nextStatus, notes: notes || null })
    },
    onSuccess: () => {
      toast.success('Gap updated')
      void queryClient.invalidateQueries({ queryKey: ['gaps'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })
  const reevaluate = useMutation({
    mutationFn: async () => {
      if (!selected) return null
      return (await api.post(`/admin/knowledge-gaps/${selected}/re-evaluate`, {})).data as {
        answerable: boolean
        retrieved_chunks: number
      }
    },
    onSuccess: (result) => {
      if (!result) return
      toast[result.answerable ? 'success' : 'warning'](
        result.answerable
          ? `Answerable now with ${result.retrieved_chunks} source(s)`
          : 'Still not answerable from the current corpus',
      )
      void queryClient.invalidateQueries({ queryKey: ['gaps'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Knowledge intelligence"
        title="Knowledge gaps"
        description="Repeated unanswered questions grouped by topic. Permission denials are excluded from gap counts, and questions are redacted before storage."
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Visible gaps" value={summary.data?.visible_gaps ?? 0} detail={`Minimum ${summary.data?.minimum_group_size ?? 3} occurrences`} icon={Lightbulb} />
        <StatCard label="Unresolved" value={summary.data?.unresolved_gaps ?? 0} detail="Open or in progress" icon={SearchX} tone="warning" />
        <StatCard label="Signals" value={summary.data?.total_signals ?? 0} detail="In the selected window" icon={ShieldQuestion} />
        <StatCard label="Queries" value={summary.data?.total_queries ?? 0} detail={`${summary.data?.retention_days ?? 180}-day retention`} icon={Lightbulb} />
      </div>

      <Card>
        <CardHeader className="flex-row items-end justify-between space-y-0">
          <div>
            <CardTitle>Gap clusters</CardTitle>
            <CardDescription>Classification is deterministic; no individual user is identified.</CardDescription>
          </div>
          <Select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Gap status filter">
            <option value="">All statuses</option>
            {['OPEN', 'ACKNOWLEDGED', 'IN_PROGRESS', 'RESOLVED', 'DISMISSED'].map((item) => (
              <option key={item} value={item}>{titleCase(item)}</option>
            ))}
          </Select>
        </CardHeader>
        <CardContent className="space-y-2">
          {list.isLoading ? <LoadingPanel rows={4} /> : list.isError ? (
            <ErrorState message={getErrorMessage(list.error)} onRetry={() => void list.refetch()} />
          ) : (list.data?.gaps ?? []).length === 0 ? (
            <EmptyState
              title="No visible gaps"
              description={`Gaps appear after at least ${summary.data?.minimum_group_size ?? 3} similar insufficient-evidence queries.`}
              icon={<Lightbulb className="size-5" />}
            />
          ) : (
            (list.data?.gaps ?? []).map((gap) => (
              <button
                key={gap.gap_id}
                type="button"
                onClick={() => setSelected(gap.gap_id)}
                className="flex w-full flex-wrap items-center justify-between gap-2 rounded-lg border border-border/60 bg-card/50 p-3 text-left transition-colors hover:border-primary/30"
              >
                <div>
                  <p className="text-xs font-medium text-foreground">{gap.question}</p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    {titleCase(gap.topic_key)} · {gap.occurrence_count} occurrences · last {formatRelative(gap.last_seen_at)}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Badge variant={gap.severity === 'HIGH' ? 'destructive' : gap.severity === 'MEDIUM' ? 'warning' : 'outline'}>{titleCase(gap.severity)}</Badge>
                  <Badge variant="outline">{titleCase(gap.classification.replace(/_/g, ' '))}</Badge>
                </div>
              </button>
            ))
          )}
        </CardContent>
      </Card>

      {selected && detail.data ? (
        <Card>
          <CardHeader>
            <CardTitle>{detail.data.topic_key}</CardTitle>
            <CardDescription>{titleCase(detail.data.classification.replace(/_/g, ' '))} · {detail.data.occurrence_count} occurrences</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="rounded-lg border border-border/60 bg-muted/20 p-3 text-sm text-foreground">{detail.data.question}</p>
            <div className="grid gap-3 sm:grid-cols-[180px_1fr]">
              <Select value={nextStatus} onChange={(event) => setNextStatus(event.target.value)} aria-label="New gap status">
                {['ACKNOWLEDGED', 'IN_PROGRESS', 'RESOLVED', 'DISMISSED'].map((item) => (
                  <option key={item} value={item}>{titleCase(item)}</option>
                ))}
              </Select>
              <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={2} placeholder="Resolution or assignment notes" />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => updateStatus.mutate()} disabled={updateStatus.isPending}>Update status</Button>
              <Button variant="outline" onClick={() => reevaluate.mutate()} disabled={reevaluate.isPending}>Re-evaluate with RAG</Button>
              {detail.data.resolution_verified !== null ? (
                <Badge variant={detail.data.resolution_verified ? 'success' : 'warning'}>
                  {detail.data.resolution_verified ? 'Answerable after change' : 'Still unanswered'}
                </Badge>
              ) : null}
            </div>
            <div className="space-y-1">
              {detail.data.events.map((event) => (
                <p key={event.event_id} className="text-[11px] text-muted-foreground">
                  {formatRelative(event.created_at)} · {titleCase(event.action.replace(/_/g, ' '))}
                  {event.detail ? ` · ${event.detail}` : ''}
                </p>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
