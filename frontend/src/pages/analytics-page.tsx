import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Activity, FileText, Lightbulb, MessageSquareWarning, ShieldAlert } from 'lucide-react'

import { PageHeader } from '@/components/common/page-header'
import { StatCard } from '@/components/common/stat-card'
import { ErrorState, LoadingPanel } from '@/components/common/states'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { api, getErrorMessage } from '@/lib/api'
import { titleCase } from '@/lib/utils'
import type { AnalyticsOverview } from '@/types/api'

const tooltipStyle = {
  backgroundColor: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: '8px',
  fontSize: '12px',
}

interface Usage {
  total_queries: number
  daily: Array<{ date: string; queries: number; errors: number }>
  by_mode: Array<{ key: string; count: number }>
}
interface Documents {
  total: number
  by_status: Array<{ key: string; count: number }>
  by_classification: Array<{ key: string; count: number }>
  by_department: Array<{ key: string; count: number }>
}
interface Retrieval {
  total_queries: number
  average_total_ms: number
  p95_total_ms: number
  citation_validation_rate: number
  insufficient_evidence_rate: number
  retrieval_error_rate: number
}
interface Feedback {
  total: number
  positive: number
  negative: number
  positive_rate: number
}
interface Gaps {
  visible_gaps: number
  unresolved_gaps: number
  total_signals: number
  by_classification: Array<{ classification: string; count: number }>
}
interface Security {
  events: number
  denials: number
  by_action: Array<{ key: string; count: number }>
}

export function AnalyticsPage() {
  const [days, setDays] = useState('30')
  const query = useQuery({
    queryKey: ['analytics', days],
    queryFn: async () => {
      const params = { days: Number(days) }
      const [overview, usage, documents, retrieval, feedback, gaps, security] = await Promise.all([
        api.get<AnalyticsOverview>('/analytics/overview', { params }),
        api.get<Usage>('/analytics/usage', { params }),
        api.get<Documents>('/analytics/documents', { params }),
        api.get<Retrieval>('/analytics/retrieval', { params }),
        api.get<Feedback>('/analytics/feedback', { params }),
        api.get<Gaps>('/analytics/knowledge-gaps', { params }),
        api.get<Security>('/analytics/security', { params }),
      ])
      return {
        overview: overview.data,
        usage: usage.data,
        documents: documents.data,
        retrieval: retrieval.data,
        feedback: feedback.data,
        gaps: gaps.data,
        security: security.data,
      }
    },
  })

  if (query.isLoading) {
    return <div className="space-y-4"><PageHeader eyebrow="Operations" title="Analytics" description="Loading measured tenant analytics." /><LoadingPanel rows={8} /></div>
  }
  if (query.isError || !query.data) {
    return (
      <div className="space-y-4">
        <PageHeader eyebrow="Operations" title="Analytics" description="Measured tenant analytics." />
        <ErrorState message={getErrorMessage(query.error)} onRetry={() => void query.refetch()} />
      </div>
    )
  }

  const { overview, usage, documents, retrieval, feedback, gaps, security } = query.data
  const usageData = usage.daily.map((point) => ({
    date: new Date(point.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    queries: point.queries,
    errors: point.errors,
  }))
  const securityData = security.by_action.slice(0, 6).map((item) => ({
    action: titleCase(item.key.replace(/_/g, ' ')),
    count: item.count,
  }))

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Operations"
        title="Analytics"
        description="Tenant-scoped, content-free measures from persisted tables. Document analytics are aggregate-only and never list a document title."
        actions={
          <Select value={days} onChange={(event) => setDays(event.target.value)} aria-label="Analytics window">
            {[7, 30, 90, 365].map((value) => (
              <option key={value} value={value}>{value} days</option>
            ))}
          </Select>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Documents" value={overview.documents} detail={`${overview.documents_completed} processed`} icon={FileText} />
        <StatCard label="Queries" value={overview.queries} detail={`${retrieval.average_total_ms.toFixed(0)} ms average`} icon={Activity} />
        <StatCard label="Feedback" value={feedback.total} detail={`${(feedback.positive_rate * 100).toFixed(1)}% positive`} icon={MessageSquareWarning} />
        <StatCard label="Knowledge gaps" value={gaps.visible_gaps} detail={`${gaps.total_signals} signals`} icon={Lightbulb} tone="warning" />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Query volume</CardTitle>
            <CardDescription>Daily retrieval queries and failures.</CardDescription>
          </CardHeader>
          <CardContent>
            {usageData.length === 0 ? (
              <p className="py-16 text-center text-sm text-muted-foreground">No queries in this window.</p>
            ) : (
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={usageData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.6} />
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Area type="monotone" dataKey="queries" stroke="#22d3ee" fill="#22d3ee33" />
                    <Area type="monotone" dataKey="errors" stroke="#f43f5e" fill="#f43f5e22" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Security events</CardTitle>
            <CardDescription>{security.events} events · {security.denials} denials. Raw logs stay on the Security page.</CardDescription>
          </CardHeader>
          <CardContent>
            {securityData.length === 0 ? (
              <p className="py-16 text-center text-sm text-muted-foreground">No audit events in this window.</p>
            ) : (
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={securityData} layout="vertical" margin={{ left: 12, right: 12 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.5} />
                    <XAxis type="number" allowDecimals={false} tick={{ fontSize: 10 }} />
                    <YAxis type="category" dataKey="action" width={130} tick={{ fontSize: 9 }} />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Bar dataKey="count" fill="#818cf8" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Documents</CardTitle><CardDescription>Aggregate-only status counts.</CardDescription></CardHeader>
          <CardContent className="space-y-2 text-xs">
            {documents.by_status.length === 0 ? <p className="text-muted-foreground">No documents.</p> : documents.by_status.map((item) => (
              <div key={item.key} className="flex justify-between border-b border-border/40 pb-1">
                <span className="text-muted-foreground">{titleCase(item.key)}</span><span className="font-mono">{item.count}</span>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Retrieval quality</CardTitle><CardDescription>Measured over the same window.</CardDescription></CardHeader>
          <CardContent className="space-y-2 text-xs">
            <div className="flex justify-between"><span className="text-muted-foreground">Citation validation</span><span className="font-mono">{(retrieval.citation_validation_rate * 100).toFixed(1)}%</span></div>
            <div className="flex justify-between"><span className="text-muted-foreground">Insufficient evidence</span><span className="font-mono">{(retrieval.insufficient_evidence_rate * 100).toFixed(1)}%</span></div>
            <div className="flex justify-between"><span className="text-muted-foreground">Retrieval errors</span><span className="font-mono">{(retrieval.retrieval_error_rate * 100).toFixed(1)}%</span></div>
            <div className="flex justify-between"><span className="text-muted-foreground">p95 latency</span><span className="font-mono">{retrieval.p95_total_ms.toFixed(0)} ms</span></div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Gap classification</CardTitle><CardDescription>Permission denials are excluded.</CardDescription></CardHeader>
          <CardContent className="space-y-2 text-xs">
            {gaps.by_classification.length === 0 ? <p className="text-muted-foreground">No visible gaps.</p> : gaps.by_classification.map((item) => (
              <div key={item.classification} className="flex justify-between border-b border-border/40 pb-1">
                <span className="text-muted-foreground">{titleCase(item.classification.replace(/_/g, ' '))}</span><span className="font-mono">{item.count}</span>
              </div>
            ))}
            <p className="flex items-center gap-1 pt-2 text-[10px] text-muted-foreground"><ShieldAlert className="size-3" /> {gaps.unresolved_gaps} unresolved</p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
