import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Database,
  FlaskConical,
  Loader2,
  Play,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { PageHeader } from '@/components/common/page-header'
import { StatCard } from '@/components/common/stat-card'
import { EmptyState, ErrorState, LoadingPanel } from '@/components/common/states'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { api, getErrorMessage } from '@/lib/api'
import { formatRelative, titleCase } from '@/lib/utils'
import type {
  EvaluationConfiguration,
  EvaluationConfigurationInfo,
  EvaluationDataset,
  EvaluationDatasetDetail,
  EvaluationMetrics,
  EvaluationResult,
  EvaluationRun,
} from '@/types/api'

const tooltipStyle = {
  backgroundColor: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: '8px',
  color: 'hsl(var(--foreground))',
  fontSize: '12px',
}

function metricNumber(
  metrics: Record<string, number | string | boolean | null>,
  key: string,
): number | null {
  const value = metrics[key]
  return typeof value === 'number' ? value : null
}

function percent(value: number | null | undefined): string {
  return value === null || value === undefined
    ? '—'
    : `${(value * 100).toFixed(1)}%`
}

function RunBadge({ status }: { status: EvaluationRun['status'] }) {
  const variant =
    status === 'COMPLETED'
      ? 'success'
      : status === 'FAILED'
        ? 'destructive'
        : 'warning'
  return <Badge variant={variant}>{titleCase(status)}</Badge>
}

export function EvaluationPage() {
  const queryClient = useQueryClient()
  const [datasetName, setDatasetName] = useState('')
  const [configuration, setConfiguration] =
    useState<EvaluationConfiguration>('hybrid')
  const [answerMode, setAnswerMode] = useState<'retrieval' | 'generated'>(
    'retrieval',
  )
  const [topK, setTopK] = useState('5')
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [failuresOnly, setFailuresOnly] = useState(false)

  const datasets = useQuery({
    queryKey: ['evaluation', 'datasets'],
    queryFn: async () => {
      const { data } = await api.get<{ datasets: EvaluationDataset[] }>(
        '/evaluation/datasets',
      )
      return data.datasets
    },
  })
  const runs = useQuery({
    queryKey: ['evaluation', 'runs'],
    queryFn: async () => {
      const { data } = await api.get<{ runs: EvaluationRun[] }>(
        '/evaluation/runs',
        { params: { limit: 25 } },
      )
      return data.runs
    },
  })
  const metrics = useQuery({
    queryKey: ['evaluation', 'metrics'],
    queryFn: async () => {
      const { data } = await api.get<EvaluationMetrics>('/evaluation/metrics')
      return data
    },
  })
  const configurations = useQuery({
    queryKey: ['evaluation', 'configurations'],
    queryFn: async () => {
      const { data } = await api.get<{
        configurations: EvaluationConfigurationInfo[]
      }>('/evaluation/configurations')
      return data.configurations
    },
  })

  const selectedDataset = useMemo(() => {
    const list = datasets.data ?? []
    return list.find((item) => item.name === datasetName) ?? list[0] ?? null
  }, [datasets.data, datasetName])

  const datasetDetail = useQuery({
    queryKey: ['evaluation', 'dataset', selectedDataset?.dataset_id],
    enabled: Boolean(selectedDataset),
    queryFn: async () => {
      const { data } = await api.get<EvaluationDatasetDetail>(
        `/evaluation/datasets/${selectedDataset?.dataset_id}`,
      )
      return data
    },
  })

  const results = useQuery({
    queryKey: ['evaluation', 'results', selectedRunId, failuresOnly],
    enabled: Boolean(selectedRunId),
    queryFn: async () => {
      const { data } = await api.get<{ results: EvaluationResult[] }>(
        `/evaluation/runs/${selectedRunId}/results`,
        { params: failuresOnly ? { failures_only: true } : {} },
      )
      return data.results
    },
  })

  const importSynthetic = useMutation({
    mutationFn: async (createDocuments: boolean) => {
      const { data } = await api.post<EvaluationDatasetDetail>(
        '/evaluation/datasets/synthetic',
        { create_documents: createDocuments },
      )
      return data
    },
    onSuccess: (data) => {
      toast.success(`Imported ${data.query_count} synthetic evaluation cases`)
      setDatasetName(data.name)
      void queryClient.invalidateQueries({ queryKey: ['evaluation'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const runEvaluation = useMutation({
    mutationFn: async () => {
      if (!selectedDataset) throw new Error('Choose a dataset first')
      const { data } = await api.post<EvaluationRun>('/evaluation/runs', {
        dataset_id: selectedDataset.dataset_id,
        configuration,
        top_k: Number(topK),
        answer_mode: answerMode,
        judge_enabled: false,
      })
      return data
    },
    onSuccess: (run) => {
      toast.success(`Run completed: ${run.completed_cases}/${run.case_count} cases`)
      setSelectedRunId(run.run_id)
      void queryClient.invalidateQueries({ queryKey: ['evaluation'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const chartData = (metrics.data?.by_configuration ?? []).map((row) => ({
    configuration: titleCase(row.configuration.replace(/_/g, ' ')),
    recall: row.recall_at_5,
    ndcg: row.ndcg_at_5,
    mrr: row.mrr,
  }))
  const selectedRun = (runs.data ?? []).find((run) => run.run_id === selectedRunId)

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="RAG quality"
        title="Evaluation"
        description="Measure retrieval and answer quality against labeled synthetic cases using the real permission-aware pipeline. Authorization is always enforced; scores come from persisted results, not estimates."
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Evaluation runs"
          value={metrics.data?.total_runs ?? 0}
          detail={`${metrics.data?.completed_runs ?? 0} completed`}
          icon={FlaskConical}
        />
        <StatCard
          label="Cases passed"
          value={metrics.data?.passed_cases ?? 0}
          detail={`${metrics.data?.failed_cases ?? 0} failed`}
          icon={CheckCircle2}
          tone="success"
        />
        <StatCard
          label="Authorization violations"
          value={metrics.data?.authorization_violations ?? 0}
          detail="Must remain zero"
          icon={ShieldAlert}
          tone={
            (metrics.data?.authorization_violations ?? 0) > 0 ? 'danger' : 'default'
          }
        />
        <StatCard
          label="Average latency"
          value={`${(metrics.data?.average_latency_ms ?? 0).toFixed(0)} ms`}
          detail="Per evaluation case"
          icon={BarChart3}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.3fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Run an evaluation</CardTitle>
            <CardDescription>
              Runs execute synchronously and can take from seconds to a few
              minutes depending on configuration and corpus size.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <label htmlFor="eval-dataset" className="text-xs text-muted-foreground">
                  Dataset
                </label>
                <Select
                  id="eval-dataset"
                  value={selectedDataset?.name ?? ''}
                  onChange={(event) => setDatasetName(event.target.value)}
                  disabled={datasets.isLoading || (datasets.data ?? []).length === 0}
                >
                  {(datasets.data ?? []).length === 0 ? (
                    <option value="">No datasets yet</option>
                  ) : null}
                  {(datasets.data ?? []).map((dataset) => (
                    <option key={dataset.dataset_id} value={dataset.name}>
                      {dataset.name} v{dataset.version} ({dataset.query_count} cases)
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="eval-config" className="text-xs text-muted-foreground">
                  Configuration
                </label>
                <Select
                  id="eval-config"
                  value={configuration}
                  onChange={(event) =>
                    setConfiguration(event.target.value as EvaluationConfiguration)
                  }
                >
                  {(configurations.data ?? []).map((item) => (
                    <option key={item.configuration} value={item.configuration}>
                      {titleCase(item.configuration.replace(/_/g, ' '))}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="eval-mode" className="text-xs text-muted-foreground">
                  Answer mode
                </label>
                <Select
                  id="eval-mode"
                  value={answerMode}
                  onChange={(event) =>
                    setAnswerMode(event.target.value as 'retrieval' | 'generated')
                  }
                >
                  <option value="retrieval">Retrieval only (no LLM)</option>
                  <option value="generated">Generated answers (uses Ollama)</option>
                </Select>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="eval-k" className="text-xs text-muted-foreground">
                  Top K
                </label>
                <Select
                  id="eval-k"
                  value={topK}
                  onChange={(event) => setTopK(event.target.value)}
                >
                  {[3, 5, 10].map((value) => (
                    <option key={value} value={value}>
                      K = {value}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => runEvaluation.mutate()}
                disabled={!selectedDataset || runEvaluation.isPending}
              >
                {runEvaluation.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <Play aria-hidden="true" />
                )}
                {runEvaluation.isPending ? 'Running…' : 'Run evaluation'}
              </Button>
              <Button
                variant="outline"
                onClick={() => importSynthetic.mutate(false)}
                disabled={importSynthetic.isPending}
              >
                {importSynthetic.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <Database aria-hidden="true" />
                )}
                Import synthetic dataset
              </Button>
              <Button
                variant="ghost"
                onClick={() => importSynthetic.mutate(true)}
                disabled={importSynthetic.isPending}
                title="Creates the eight fictional PDFs through the normal ingestion pipeline when missing."
              >
                Import + ingest corpus
              </Button>
            </div>
            <p className="text-[11px] leading-5 text-muted-foreground">
              Dataset cases reference documents by name. If the corpus is not
              ingested, those cases fail with <code>MISSING_DOCUMENTS</code> and
              are reported rather than silently skipped.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Configuration comparison</CardTitle>
            <CardDescription>
              Average measured recall@5, NDCG@5 and MRR across completed runs.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {metrics.isLoading ? (
              <div className="h-[230px] animate-pulse rounded-lg bg-muted/50" />
            ) : chartData.length === 0 ? (
              <EmptyState
                title="No completed runs"
                description="Run the same dataset under different configurations to compare retrieval quality."
                icon={<BarChart3 className="size-5" aria-hidden="true" />}
                className="border-none bg-transparent py-8"
              />
            ) : (
              <div className="h-[230px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.6} />
                    <XAxis dataKey="configuration" tick={{ fontSize: 9 }} interval={0} angle={-12} textAnchor="end" height={54} />
                    <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} allowDecimals />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Bar dataKey="recall" name="Recall@5" fill="#22d3ee" radius={[3, 3, 0, 0]} />
                    <Bar dataKey="ndcg" name="NDCG@5" fill="#818cf8" radius={[3, 3, 0, 0]} />
                    <Bar dataKey="mrr" name="MRR" fill="#34d399" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {datasets.isError ? (
        <ErrorState
          message={getErrorMessage(datasets.error)}
          onRetry={() => void datasets.refetch()}
        />
      ) : null}

      <div className="grid gap-5 xl:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardHeader className="flex-row items-start justify-between space-y-0">
            <div>
              <CardTitle>Runs</CardTitle>
              <CardDescription>Newest first, tenant-scoped.</CardDescription>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => void runs.refetch()}
              aria-label="Refresh runs"
            >
              <RefreshCw className={runs.isFetching ? 'animate-spin' : undefined} aria-hidden="true" />
            </Button>
          </CardHeader>
          <CardContent className="px-0 pb-2">
            {runs.isLoading ? (
              <div className="px-5">
                <LoadingPanel rows={4} />
              </div>
            ) : (runs.data ?? []).length === 0 ? (
              <div className="px-5">
                <EmptyState
                  title="No evaluation runs"
                  description="Import the synthetic dataset, then run a configuration."
                  icon={<FlaskConical className="size-5" aria-hidden="true" />}
                />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Run</TableHead>
                    <TableHead>Config</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Cases</TableHead>
                    <TableHead className="text-right">Recall@5</TableHead>
                    <TableHead className="text-right">Violations</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(runs.data ?? []).map((run) => (
                    <TableRow
                      key={run.run_id}
                      className="cursor-pointer"
                      onClick={() => setSelectedRunId(run.run_id)}
                    >
                      <TableCell className="text-xs">
                        <span className="block font-medium text-foreground">
                          {run.dataset_name ?? 'Dataset'}
                        </span>
                        <span className="text-[10px] text-muted-foreground">
                          {formatRelative(run.created_at)}
                        </span>
                      </TableCell>
                      <TableCell className="text-xs">
                        {titleCase(run.configuration.replace(/_/g, ' '))}
                      </TableCell>
                      <TableCell>
                        <RunBadge status={run.status} />
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {run.completed_cases}/{run.case_count}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {percent(metricNumber(run.metrics, 'recall_at_5'))}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {run.authorization_violations}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Dataset</CardTitle>
            <CardDescription>
              {datasetDetail.data
                ? `${datasetDetail.data.name} v${datasetDetail.data.version} · ${datasetDetail.data.query_count} cases · ${datasetDetail.data.source}`
                : 'Select or import a dataset.'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {datasetDetail.isLoading ? (
              <LoadingPanel rows={4} />
            ) : datasetDetail.data ? (
              <div className="space-y-2">
                {datasetDetail.data.cases.slice(0, 8).map((item) => (
                  <div
                    key={item.case_id}
                    className="rounded-lg border border-border/60 bg-muted/10 p-2.5"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-xs font-medium text-foreground">{item.question}</p>
                      <Badge variant="outline">{titleCase(item.category.replace(/_/g, ' '))}</Badge>
                    </div>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      {item.allowed_document_names.length} allowed ·{' '}
                      {item.expected_answerable ? 'answerable' : 'must abstain'}
                    </p>
                  </div>
                ))}
                {datasetDetail.data.cases.length > 8 ? (
                  <p className="text-[11px] text-muted-foreground">
                    Showing 8 of {datasetDetail.data.cases.length} cases.
                  </p>
                ) : null}
              </div>
            ) : (
              <EmptyState
                title="No dataset selected"
                description="Import the bundled synthetic dataset to get started."
                icon={<Database className="size-5" aria-hidden="true" />}
                className="border-none bg-transparent py-8"
              />
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0">
          <div>
            <CardTitle>Case results</CardTitle>
            <CardDescription>
              {selectedRun
                ? `${titleCase(selectedRun.configuration.replace(/_/g, ' '))} · ${selectedRun.answer_mode} · ${formatRelative(selectedRun.created_at)}`
                : 'Select a run above to inspect per-case outcomes.'}
            </CardDescription>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={failuresOnly}
              onChange={(event) => setFailuresOnly(event.target.checked)}
              className="size-3.5 accent-[hsl(var(--primary))]"
            />
            Failures only
          </label>
        </CardHeader>
        <CardContent className="px-0 pb-2">
          {!selectedRunId ? (
            <div className="px-5">
              <EmptyState
                title="No run selected"
                description="Select a run to inspect retrieved evidence, citations and failure reasons."
                icon={<FlaskConical className="size-5" aria-hidden="true" />}
              />
            </div>
          ) : results.isLoading ? (
            <div className="px-5">
              <LoadingPanel rows={5} />
            </div>
          ) : results.isError ? (
            <div className="px-5">
              <ErrorState
                message={getErrorMessage(results.error)}
                onRetry={() => void results.refetch()}
              />
            </div>
          ) : (results.data ?? []).length === 0 ? (
            <div className="px-5">
              <EmptyState
                title={failuresOnly ? 'No failing cases' : 'No results'}
                description={
                  failuresOnly
                    ? 'Every case in this run passed its deterministic checks.'
                    : 'This run has no stored case results.'
                }
                icon={<CheckCircle2 className="size-5" aria-hidden="true" />}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Case</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead className="text-right">Recall@5</TableHead>
                  <TableHead className="text-right">Citation</TableHead>
                  <TableHead className="text-right">Latency</TableHead>
                  <TableHead>Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(results.data ?? []).map((result) => (
                  <TableRow key={result.result_id}>
                    <TableCell className="max-w-[300px]">
                      <p className="truncate text-xs font-medium text-foreground">
                        {result.question}
                      </p>
                      <p className="text-[10px] text-muted-foreground">
                        {result.case_key}
                      </p>
                    </TableCell>
                    <TableCell className="text-[11px] text-muted-foreground">
                      {titleCase(result.category.replace(/_/g, ' '))}
                    </TableCell>
                    <TableCell>
                      {result.passed ? (
                        <Badge variant="success">
                          <CheckCircle2 aria-hidden="true" /> Pass
                        </Badge>
                      ) : (
                        <Badge variant="destructive">
                          <XCircle aria-hidden="true" /> Fail
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {percent(metricNumber(result.metrics, 'recall_at_5'))}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {percent(metricNumber(result.metrics, 'citation_validity'))}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {result.latency_ms} ms
                    </TableCell>
                    <TableCell className="max-w-[240px] text-[11px] text-muted-foreground">
                      {result.error_code ? (
                        <span className="flex items-center gap-1 text-destructive">
                          <AlertTriangle className="size-3" aria-hidden="true" />
                          {result.error_code}
                        </span>
                      ) : result.citations.length > 0 ? (
                        <span className="truncate">
                          {result.citations.map((citation) => citation.document_name).join(', ')}
                        </span>
                      ) : (
                        <span>No citations</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
