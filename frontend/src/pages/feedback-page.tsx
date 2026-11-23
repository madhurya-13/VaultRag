import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { MessageSquareWarning, ThumbsDown, ThumbsUp } from 'lucide-react'

import { PageHeader } from '@/components/common/page-header'
import { StatCard } from '@/components/common/stat-card'
import { EmptyState, ErrorState, LoadingPanel } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { api, getErrorMessage } from '@/lib/api'
import { formatRelative, titleCase } from '@/lib/utils'
import type { AnswerFeedback, AnswerFeedbackDetail, FeedbackSummary } from '@/types/api'

export function FeedbackPage() {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState('')
  const [category, setCategory] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [reviewStatus, setReviewStatus] = useState('RESOLVED')

  const summary = useQuery({
    queryKey: ['feedback', 'summary'],
    queryFn: async () => (await api.get<FeedbackSummary>('/admin/feedback/summary')).data,
  })
  const list = useQuery({
    queryKey: ['feedback', 'list', status, category],
    queryFn: async () => {
      const params: Record<string, string> = {}
      if (status) params.status = status
      if (category) params.category = category
      return (await api.get<{ feedback: AnswerFeedback[]; total: number }>('/admin/feedback', { params })).data
    },
  })
  const detail = useQuery({
    queryKey: ['feedback', 'detail', selected],
    enabled: Boolean(selected),
    queryFn: async () => (await api.get<AnswerFeedbackDetail>(`/admin/feedback/${selected}`)).data,
  })

  const review = useMutation({
    mutationFn: async () => {
      if (!selected) return
      await api.post(`/admin/feedback/${selected}/review`, {
        status: reviewStatus,
        review_notes: notes || null,
      })
    },
    onSuccess: () => {
      toast.success('Feedback reviewed')
      setNotes('')
      void queryClient.invalidateQueries({ queryKey: ['feedback'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const categories = useMemo(
    () => Array.from(new Set((list.data?.feedback ?? []).map((item) => item.category))),
    [list.data],
  )

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Answer quality"
        title="Feedback review"
        description="Review answer feedback from your organization. Citations are re-authorized for you; no document text is shown here."
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Feedback" value={summary.data?.total ?? 0} detail={`${summary.data?.unreviewed ?? 0} unreviewed`} icon={MessageSquareWarning} />
        <StatCard label="Positive" value={summary.data?.positive ?? 0} detail={`${((summary.data?.positive_rate ?? 0) * 100).toFixed(1)}% positive`} icon={ThumbsUp} tone="success" />
        <StatCard label="Negative" value={summary.data?.negative ?? 0} detail="Needs review" icon={ThumbsDown} tone="danger" />
        <StatCard label="Window" value={`${summary.data?.days ?? 30} days`} detail="Summary window" icon={MessageSquareWarning} />
      </div>

      <Card>
        <CardHeader className="flex-row flex-wrap items-end justify-between gap-3 space-y-0">
          <div>
            <CardTitle>Feedback</CardTitle>
            <CardDescription>Filter and open an item to review it.</CardDescription>
          </div>
          <div className="flex gap-2">
            <Select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Review status filter">
              <option value="">All statuses</option>
              {['UNREVIEWED', 'IN_REVIEW', 'RESOLVED', 'DISMISSED'].map((item) => (
                <option key={item} value={item}>{titleCase(item)}</option>
              ))}
            </Select>
            <Select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Category filter">
              <option value="">All categories</option>
              {categories.map((item) => (
                <option key={item} value={item}>{titleCase(item.replace(/_/g, ' '))}</option>
              ))}
            </Select>
          </div>
        </CardHeader>
        <CardContent className="px-0 pb-2">
          {list.isLoading ? (
            <div className="px-5"><LoadingPanel rows={5} /></div>
          ) : list.isError ? (
            <div className="px-5"><ErrorState message={getErrorMessage(list.error)} onRetry={() => void list.refetch()} /></div>
          ) : (list.data?.feedback ?? []).length === 0 ? (
            <div className="px-5">
              <EmptyState title="No feedback matches" description="Answer feedback from employees will appear here." icon={<MessageSquareWarning className="size-5" />} />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Feedback</TableHead>
                  <TableHead>Sentiment</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(list.data?.feedback ?? []).map((item) => (
                  <TableRow key={item.feedback_id} className="cursor-pointer" onClick={() => { setSelected(item.feedback_id); setNotes(item.review_notes ?? '') }}>
                    <TableCell>
                      <p className="text-xs font-medium text-foreground">{titleCase(item.category.replace(/_/g, ' '))}</p>
                      <p className="max-w-[420px] truncate text-[11px] text-muted-foreground">{item.comment || 'No comment'}</p>
                    </TableCell>
                    <TableCell>
                      <Badge variant={item.sentiment === 'POSITIVE' ? 'success' : 'destructive'}>{titleCase(item.sentiment)}</Badge>
                    </TableCell>
                    <TableCell><Badge variant="outline">{titleCase(item.review_status)}</Badge></TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatRelative(item.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {selected ? (
        <Card>
          <CardHeader>
            <CardTitle>Review detail</CardTitle>
            <CardDescription>Only citations you may currently read are shown.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {detail.isLoading ? <LoadingPanel rows={4} /> : detail.data ? (
              <>
                <div className="rounded-lg border border-border/60 bg-muted/20 p-3 text-sm">
                  <p className="text-xs font-medium text-muted-foreground">Question</p>
                  <p className="mt-1 text-foreground">{detail.data.question ?? 'Question unavailable'}</p>
                  <p className="mt-3 text-xs font-medium text-muted-foreground">Answer</p>
                  <p className="mt-1 whitespace-pre-wrap text-foreground">{detail.data.answer ?? 'Answer unavailable'}</p>
                </div>
                <div className="flex flex-wrap gap-2 text-[11px]">
                  {detail.data.citations.map((citation) => (
                    <Badge key={`${citation.document_id}-${citation.page_start}`} variant="outline">
                      {citation.document_name} · p.{citation.page_start}
                    </Badge>
                  ))}
                  {detail.data.hidden_citations > 0 ? (
                    <Badge variant="warning">{detail.data.hidden_citations} citation(s) hidden</Badge>
                  ) : null}
                </div>
                <div className="grid gap-3 sm:grid-cols-[180px_1fr]">
                  <Select value={reviewStatus} onChange={(event) => setReviewStatus(event.target.value)} aria-label="Review status">
                    {['IN_REVIEW', 'RESOLVED', 'DISMISSED'].map((item) => (
                      <option key={item} value={item}>{titleCase(item)}</option>
                    ))}
                  </Select>
                  <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Internal review notes" rows={3} />
                </div>
                <div className="flex gap-2">
                  <Button onClick={() => review.mutate()} disabled={review.isPending}>Save review</Button>
                  <Button variant="ghost" onClick={() => setSelected(null)}>Close</Button>
                </div>
              </>
            ) : <ErrorState message={getErrorMessage(detail.error)} onRetry={() => void detail.refetch()} />}
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
