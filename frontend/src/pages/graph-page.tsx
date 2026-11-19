import {
  ArrowRight,
  GitBranch,
  Loader2,
  Network,
  Search,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import { useState } from 'react'

import { PageHeader } from '@/components/common/page-header'
import { EmptyState, ErrorState } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useQuery } from '@tanstack/react-query'
import { api, getErrorMessage } from '@/lib/api'
import { pluralize, titleCase } from '@/lib/utils'
import type {
  KgConflictListResponse,
  KgQueryResponse,
  KgSearchMode,
} from '@/types/api'

const MODES: { value: KgSearchMode; label: string; hint: string }[] = [
  {
    value: 'graph',
    label: 'Graph only',
    hint: 'Only chunks that support visible graph evidence.',
  },
  {
    value: 'graph_hybrid',
    label: 'Graph + hybrid',
    hint: 'Blend graph evidence with semantic and keyword retrieval.',
  },
]

export function GraphPage() {
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<KgSearchMode>('graph')
  const [depth, setDepth] = useState('2')
  const [result, setResult] = useState<KgQueryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const conflicts = useQuery({
    queryKey: ['knowledge-graph', 'conflicts'],
    queryFn: async () => {
      const { data } = await api.get<KgConflictListResponse>(
        '/knowledge-graph/conflicts',
      )
      return data
    },
  })

  const submit = async () => {
    const trimmed = question.trim()
    if (!trimmed || loading) return
    setLoading(true)
    setError(null)
    try {
      const { data } = await api.post<KgQueryResponse>('/knowledge-graph/query', {
        question: trimmed,
        search_mode: mode,
        max_depth: Number(depth),
      })
      setResult(data)
    } catch (err) {
      setError(getErrorMessage(err))
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  const nodeName = (id: string) =>
    result?.nodes.find((node) => node.node_id === id)?.name ?? 'Unknown'

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Knowledge graph"
        title="Entity relationships"
        description="Explore the evidence-backed relationships extracted from your documents. Every node, edge and quote is filtered by your current permissions before it is shown."
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="size-4 text-primary" aria-hidden="true" />
            Ask the graph
          </CardTitle>
          <CardDescription>
            Seeds are matched by entity name and alias, then expanded hop by hop
            within a hard depth and size budget.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="e.g. Who reports to the Head of Security?"
            rows={3}
            maxLength={2000}
            aria-label="Graph question"
          />
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="w-full sm:w-56">
              <label
                htmlFor="graph-mode"
                className="mb-1.5 block text-xs text-muted-foreground"
              >
                Retrieval mode
              </label>
              <Select
                id="graph-mode"
                value={mode}
                onChange={(event) => setMode(event.target.value as KgSearchMode)}
              >
                {MODES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </div>
            <div className="w-full sm:w-40">
              <label
                htmlFor="graph-depth"
                className="mb-1.5 block text-xs text-muted-foreground"
              >
                Max depth
              </label>
              <Select
                id="graph-depth"
                value={depth}
                onChange={(event) => setDepth(event.target.value)}
              >
                {['1', '2', '3'].map((value) => (
                  <option key={value} value={value}>
                    {value} {value === '1' ? 'hop' : 'hops'}
                  </option>
                ))}
              </Select>
            </div>
            <Button onClick={submit} disabled={loading || !question.trim()}>
              {loading ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <Network aria-hidden="true" />
              )}
              Explore graph
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            {MODES.filter((option) => option.value === mode)[0]?.hint}
          </p>
        </CardContent>
      </Card>

      {error ? <ErrorState message={error} onRetry={submit} /> : null}

      {result ? (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {[
              { label: 'Seeds', value: result.seed_count },
              { label: 'Nodes', value: result.nodes.length },
              { label: 'Edges', value: result.edges.length },
              { label: 'Findings', value: result.findings.length },
              { label: 'Chunks', value: result.candidate_chunks },
              { label: 'Denied', value: result.denied_documents },
            ].map((stat) => (
              <div
                key={stat.label}
                className="rounded-xl border border-border/70 bg-card/60 px-3 py-2.5"
              >
                <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                  {stat.label}
                </p>
                <p className="mt-0.5 text-lg font-semibold text-foreground">
                  {stat.value}
                </p>
              </div>
            ))}
          </div>

          {result.truncated ? (
            <p className="flex items-center gap-2 rounded-lg border border-warning/25 bg-warning/10 px-3 py-2 text-xs text-warning">
              <TriangleAlert className="size-3.5" aria-hidden="true" />
              This graph exceeded the traversal budget and was truncated at depth{' '}
              {result.depth}. Narrow the question for a complete view.
            </p>
          ) : null}

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <GitBranch className="size-4 text-primary" aria-hidden="true" />
                  Findings
                </CardTitle>
                <CardDescription>
                  One statement per visible relationship, quoted from the source
                  document.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {result.findings.length === 0 ? (
                  <EmptyState
                    title="No relationships found"
                    description="No evidence-backed relationship matched this question within your access."
                    icon={<Network className="size-5" aria-hidden="true" />}
                  />
                ) : (
                  result.findings.map((finding, index) => (
                    <div
                      key={`${finding.chunk_id}-${index}`}
                      className="rounded-lg border border-border/70 bg-muted/20 p-3"
                    >
                      <p className="text-sm text-foreground">
                        <span className="font-medium">{finding.subject}</span>{' '}
                        <span className="text-primary">{finding.relation}</span>{' '}
                        <span className="font-medium">{finding.object}</span>
                      </p>
                      <blockquote className="mt-2 border-l-2 border-primary/30 pl-3 text-[13px] italic text-muted-foreground">
                        “{finding.quote}”
                      </blockquote>
                      <p className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                        <ShieldCheck className="size-3" aria-hidden="true" />
                        {finding.document_name} · page {finding.page_start}
                        {finding.page_end !== finding.page_start
                          ? `–${finding.page_end}`
                          : ''}
                        <Badge variant="secondary" className="normal-case">
                          {Math.round(finding.confidence * 100)}% confidence
                        </Badge>
                      </p>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Network className="size-4 text-primary" aria-hidden="true" />
                  Relationships
                </CardTitle>
                <CardDescription>
                  Visible edges only. A relationship between two visible nodes is
                  shown only when the edge itself has authorized evidence.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {result.edges.length === 0 ? (
                  <EmptyState
                    title="No visible edges"
                    description="Nothing in your authorized documents links these entities."
                    icon={<GitBranch className="size-5" aria-hidden="true" />}
                  />
                ) : (
                  result.edges.map((edge) => (
                    <div
                      key={edge.edge_id}
                      className="flex flex-wrap items-center gap-2 rounded-lg border border-border/70 bg-card/50 px-3 py-2 text-sm"
                    >
                      <span className="font-medium text-foreground">
                        {nodeName(edge.source_node_id)}
                      </span>
                      <ArrowRight
                        className="size-3.5 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <Badge variant="secondary" className="normal-case">
                        {titleCase(edge.relation_type)}
                      </Badge>
                      <ArrowRight
                        className="size-3.5 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <span className="font-medium text-foreground">
                        {nodeName(edge.target_node_id)}
                      </span>
                      <span className="ml-auto text-[11px] text-muted-foreground">
                        {pluralize(edge.evidence.length, 'source')}
                      </span>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>
          </div>

          {result.nodes.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>Entities</CardTitle>
                <CardDescription>
                  Mention counts and confidence are derived from authorized
                  evidence only.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                {result.nodes.map((node) => (
                  <span
                    key={node.node_id}
                    className="inline-flex items-center gap-2 rounded-full border border-border/70 bg-muted/20 px-3 py-1.5 text-xs text-foreground"
                  >
                    <span className="font-medium">{node.name}</span>
                    <span className="text-muted-foreground">
                      {titleCase(node.node_type)}
                    </span>
                    <span className="rounded-full bg-primary/10 px-1.5 text-[10px] text-primary">
                      {node.mention_count}
                    </span>
                  </span>
                ))}
              </CardContent>
            </Card>
          ) : null}
        </div>
      ) : null}

      {!result && !error && !loading ? (
        <EmptyState
          title="Nothing explored yet"
          description="Ask a question to see the evidence-backed subgraph your permissions allow."
          icon={<Network className="size-5" aria-hidden="true" />}
        />
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <TriangleAlert className="size-4 text-warning" aria-hidden="true" />
            Conflicting facts
          </CardTitle>
          <CardDescription>
            Documents that disagree on a single-valued relationship. Only
            conflicts with at least two sources you can read are listed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {conflicts.isError ? (
            <p className="text-sm text-muted-foreground">
              Conflicting facts are unavailable right now.
            </p>
          ) : (conflicts.data?.conflicts.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground">
              No conflicting facts are visible to you.
            </p>
          ) : (
            conflicts.data?.conflicts.map((conflict) => (
              <div
                key={conflict.conflict_id}
                className="rounded-lg border border-warning/25 bg-warning/5 p-3"
              >
                <p className="text-sm font-medium text-foreground">
                  {conflict.subject}{' '}
                  <span className="text-muted-foreground">
                    · {titleCase(conflict.relation_type)}
                  </span>
                </p>
                <ul className="mt-2 space-y-1.5">
                  {conflict.values.map((value) => (
                    <li
                      key={`${conflict.conflict_id}-${value.document_id}`}
                      className="text-[13px] text-muted-foreground"
                    >
                      <span className="font-medium text-foreground">
                        {value.value}
                      </span>{' '}
                      — {value.document_name}
                      <span className="block italic">“{value.quote}”</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  )
}
