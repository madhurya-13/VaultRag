import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { RotateCcw, Save, Settings2 } from 'lucide-react'

import { PageHeader } from '@/components/common/page-header'
import { ErrorState, LoadingPanel } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { api, getErrorMessage } from '@/lib/api'
import { formatRelative, titleCase } from '@/lib/utils'
import type { TenantSettings } from '@/types/api'

interface HistoryResponse {
  history: Array<{ version: number; settings: Record<string, unknown>; changed_by: string; changed_at: string }>
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<Record<string, unknown>>({})
  const query = useQuery({
    queryKey: ['tenant-settings'],
    queryFn: async () => (await api.get<TenantSettings>('/admin/settings')).data,
  })
  const history = useQuery({
    queryKey: ['tenant-settings', 'history'],
    queryFn: async () => (await api.get<HistoryResponse>('/admin/settings/history')).data,
  })

  useEffect(() => {
    if (query.data) setForm(query.data.settings)
  }, [query.data])

  const save = useMutation({
    mutationFn: async () => {
      const payload = { ...form }
      delete payload.allowed_file_types
      return (await api.put<TenantSettings>('/admin/settings', payload)).data
    },
    onSuccess: () => {
      toast.success('Settings saved')
      void queryClient.invalidateQueries({ queryKey: ['tenant-settings'] })
      void queryClient.invalidateQueries({ queryKey: ['rag'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })
  const rollback = useMutation({
    mutationFn: async (version: number) =>
      (await api.post<TenantSettings>('/admin/settings/rollback', { version })).data,
    onSuccess: () => {
      toast.success('Settings rolled back')
      void queryClient.invalidateQueries({ queryKey: ['tenant-settings'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const number = (key: string, label: string, min = 0, max = 100, step = 1) => (
    <label className="space-y-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={String(form[key] ?? '')}
        onChange={(event) => setForm((current) => ({ ...current, [key]: Number(event.target.value) }))}
        className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
      />
    </label>
  )

  const toggle = (key: string, label: string) => (
    <label className="flex items-center justify-between gap-3 rounded-lg border border-border/60 px-3 py-2 text-xs">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={Boolean(form[key])}
        onChange={(event) => setForm((current) => ({ ...current, [key]: event.target.checked }))}
        className="size-4 accent-[hsl(var(--primary))]"
      />
    </label>
  )

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Administration"
        title="Tenant settings"
        description="Validated retrieval and product preferences. Authorization, RLS and citation validation are not configurable."
        actions={query.data ? <Badge variant="outline">Version {query.data.version}</Badge> : null}
      />

      {query.isLoading ? <LoadingPanel rows={6} /> : query.isError || !query.data ? (
        <ErrorState message={getErrorMessage(query.error)} onRetry={() => void query.refetch()} />
      ) : (
        <div className="grid gap-5 xl:grid-cols-[1.3fr_1fr]">
          <Card>
            <CardHeader>
              <CardTitle>Retrieval defaults</CardTitle>
              <CardDescription>Requests may not exceed the server maximum.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="space-y-1 text-xs">
                  <span className="text-muted-foreground">Default search mode</span>
                  <Select
                    value={String(form.default_search_mode ?? 'hybrid')}
                    onChange={(event) => setForm((current) => ({ ...current, default_search_mode: event.target.value }))}
                  >
                    {['semantic', 'keyword', 'hybrid', 'graph', 'graph_hybrid'].map((mode) => (
                      <option key={mode} value={mode}>{titleCase(mode.replace(/_/g, ' '))}</option>
                    ))}
                  </Select>
                </label>
                {number('default_top_k', 'Default top K', 1, 50)}
                {number('max_top_k', 'Maximum top K', 1, 50)}
                {number('semantic_candidates', 'Semantic candidates', 1, 100)}
                {number('keyword_candidates', 'Keyword candidates', 1, 100)}
                {number('hybrid_candidates', 'Hybrid candidates', 1, 200)}
                {number('min_similarity', 'Minimum similarity', 0, 1, 0.05)}
                {number('analytics_retention_days', 'Analytics retention (days)', 1, 365)}
                {number('max_upload_size_mb', 'Maximum upload size (MB)', 1, 100)}
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {toggle('rerank_enabled', 'Enable reranking')}
                {toggle('graph_enabled', 'Enable knowledge graph')}
                {toggle('feedback_enabled', 'Enable answer feedback')}
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                {number('evidence_strong_threshold', 'Strong evidence', 0, 1, 0.05)}
                {number('evidence_moderate_threshold', 'Moderate evidence', 0, 1, 0.05)}
                {number('evidence_limited_threshold', 'Limited evidence', 0, 1, 0.05)}
              </div>
              <Button onClick={() => save.mutate()} disabled={save.isPending}>
                <Save /> Save settings
              </Button>
              <p className="text-[10px] leading-4 text-muted-foreground">
                Only PDF is supported by ingestion. The allowed file type is not editable.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Version history</CardTitle>
              <CardDescription>Every change is appended and audited.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {history.isLoading ? <LoadingPanel rows={3} /> : (history.data?.history ?? []).length === 0 ? (
                <p className="text-xs text-muted-foreground">No saved versions yet.</p>
              ) : (history.data?.history ?? []).map((item) => (
                <div key={item.version} className="flex items-center justify-between rounded-lg border border-border/60 p-2.5 text-xs">
                  <div>
                    <p className="font-medium text-foreground">Version {item.version}</p>
                    <p className="text-[10px] text-muted-foreground">{formatRelative(item.changed_at)}</p>
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => rollback.mutate(item.version)} disabled={rollback.isPending}>
                    <RotateCcw /> Restore
                  </Button>
                </div>
              ))}
              <p className="flex items-center gap-1 pt-1 text-[10px] text-muted-foreground">
                <Settings2 className="size-3" /> Settings never weaken authorization or RLS.
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
