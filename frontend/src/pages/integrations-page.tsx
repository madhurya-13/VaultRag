import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  FolderSync,
  Loader2,
  Plug,
  Plus,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { PageHeader } from '@/components/common/page-header'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { api, getErrorMessage } from '@/lib/api'
import { titleCase } from '@/lib/utils'
import type {
  ExternalDocument,
  Integration,
  IntegrationProviderInfo,
  IntegrationRepository,
  IntegrationSyncJob,
  IntegrationIdentityMapping,
} from '@/types/api'

const DEPARTMENTS = [
  'ENGINEERING',
  'HR',
  'FINANCE',
  'IT',
  'LEGAL',
  'OPERATIONS',
  'SALES',
  'SECURITY',
]

const PERMISSION_MODES = [
  { value: 'VAULTRAG_MANAGED', label: 'VaultRAG managed' },
  { value: 'EXTERNAL_MIRROR', label: 'Mirror external ACL' },
  { value: 'ADMIN_MAPPED', label: 'Administrator mapped' },
]

function statusVariant(
  status: string,
): 'success' | 'warning' | 'destructive' | 'secondary' {
  if (status === 'CONNECTED' || status === 'COMPLETED' || status === 'SYNCED') {
    return 'success'
  }
  if (status === 'REAUTH_REQUIRED' || status === 'PARTIALLY_COMPLETED') {
    return 'warning'
  }
  if (status === 'FAILED' || status === 'ERROR') {
    return 'destructive'
  }
  return 'secondary'
}

function IntegrationCard({
  integration,
  onChanged,
}: {
  integration: Integration
  onChanged: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [token, setToken] = useState('')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [syncType, setSyncType] = useState('INCREMENTAL')

  const repositories = useQuery({
    queryKey: ['integrations', integration.integration_id, 'repositories'],
    queryFn: async () => {
      const { data } = await api.get<{ repositories: IntegrationRepository[] }>(
        `/integrations/${integration.integration_id}/repositories`,
        { params: { discover: false } },
      )
      return data.repositories
    },
  })

  const jobs = useQuery({
    queryKey: ['integrations', integration.integration_id, 'jobs'],
    queryFn: async () => {
      const { data } = await api.get<{ jobs: IntegrationSyncJob[] }>(
        `/integrations/${integration.integration_id}/jobs`,
      )
      return data.jobs
    },
  })

  const documents = useQuery({
    queryKey: ['integrations', integration.integration_id, 'documents'],
    queryFn: async () => {
      const { data } = await api.get<{ documents: ExternalDocument[] }>(
        `/integrations/${integration.integration_id}/documents`,
      )
      return data.documents
    },
  })

  const mappings = useQuery({
    queryKey: ['integrations', integration.integration_id, 'identities'],
    queryFn: async () => {
      const { data } = await api.get<{ mappings: IntegrationIdentityMapping[] }>(
        `/integrations/${integration.integration_id}/identities`,
      )
      return data.mappings
    },
  })

  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label)
    setError(null)
    try {
      await action()
      onChanged()
      await Promise.all([
        repositories.refetch(),
        jobs.refetch(),
        documents.refetch(),
        mappings.refetch(),
      ])
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const saveCredentials = () =>
    run('credentials', async () => {
      const credentials: Record<string, string> = {}
      if (token.trim()) credentials.access_token = token.trim()
      if (clientId.trim()) credentials.client_id = clientId.trim()
      if (clientSecret.trim()) credentials.client_secret = clientSecret.trim()
      if (Object.keys(credentials).length === 0) {
        throw new Error('Enter at least one credential value')
      }
      await api.post(`/integrations/${integration.integration_id}/credentials`, {
        credentials,
      })
      setToken('')
      setClientId('')
      setClientSecret('')
    })

  const startOAuth = () =>
    run('oauth', async () => {
      const { data } = await api.post<{ authorization_url: string }>(
        `/integrations/${integration.integration_id}/oauth/start`,
      )
      window.open(data.authorization_url, '_blank', 'noopener,noreferrer')
    })

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 text-base">
            <Plug className="size-4 text-primary" aria-hidden="true" />
            {integration.integration_name}
          </CardTitle>
          <CardDescription>
            {integration.provider_display_name} · {integration.permission_sync_mode}
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={statusVariant(integration.connection_status)}>
            {titleCase(integration.connection_status)}
          </Badge>
          {integration.credential?.configured ? (
            <Badge variant="secondary">
              <ShieldCheck className="mr-1 size-3" aria-hidden="true" />
              Credential stored
            </Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {error ? (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            {error}
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            onClick={() =>
              run('connect', () =>
                api.post(`/integrations/${integration.integration_id}/connect`),
              )
            }
            disabled={busy !== null}
          >
            {busy === 'connect' ? (
              <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" />
            ) : null}
            Validate connection
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              run('discover', async () => {
                const { data } = await api.get(
                  `/integrations/${integration.integration_id}/repositories`,
                  { params: { discover: true } },
                )
                return data
              })
            }
            disabled={busy !== null}
          >
            <FolderSync className="mr-2 size-4" aria-hidden="true" />
            Discover repositories
          </Button>
          {integration.supports_oauth ? (
            <Button
              size="sm"
              variant="outline"
              onClick={startOAuth}
              disabled={busy !== null}
            >
              <ExternalLink className="mr-2 size-4" aria-hidden="true" />
              Start OAuth
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="secondary"
            onClick={() =>
              run('disconnect', () =>
                api.delete(`/integrations/${integration.integration_id}`),
              )
            }
            disabled={busy !== null}
          >
            Disconnect
          </Button>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor={`mode-${integration.integration_id}`}>Sync mode</Label>
            <Select
              id={`mode-${integration.integration_id}`}
              value={syncType}
              onChange={(event) => setSyncType(event.target.value)}
            >
              <option value="INCREMENTAL">Incremental</option>
              <option value="FULL">Full</option>
            </Select>
          </div>
          <div className="flex items-end">
            <Button
              className="w-full"
              variant="outline"
              onClick={() =>
                run('sync', () =>
                  api.post(`/integrations/${integration.integration_id}/sync`, {
                    sync_type: syncType,
                  }),
                )
              }
              disabled={busy !== null || !integration.sync_enabled}
            >
              {busy === 'sync' ? (
                <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="mr-2 size-4" aria-hidden="true" />
              )}
              Synchronize
            </Button>
          </div>
          <div className="text-xs text-muted-foreground sm:pt-6">
            Last sync:{' '}
            {integration.last_sync_at
              ? new Date(integration.last_sync_at).toLocaleString()
              : 'never'}
            {integration.sync_enabled ? '' : ' · scheduling disabled'}
          </div>
        </div>

        <section aria-labelledby={`repos-${integration.integration_id}`}>
          <h3
            id={`repos-${integration.integration_id}`}
            className="mb-2 text-sm font-semibold"
          >
            Repositories
          </h3>
          {repositories.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading repositories…</p>
          ) : (repositories.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No repositories discovered yet. Store a credential, validate the
              connection, then discover sources.
            </p>
          ) : (
            <ul className="divide-y divide-border/60 rounded-lg border border-border/60">
              {(repositories.data ?? []).map((repository) => (
                <li
                  key={repository.repository_id}
                  className="flex flex-wrap items-center justify-between gap-3 p-3"
                >
                  <label className="flex items-center gap-3 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 rounded border-input"
                      checked={repository.is_selected}
                      onChange={(event) =>
                        run(`repo-${repository.repository_id}`, () =>
                          api.put(
                            `/integrations/${integration.integration_id}/repositories/${repository.repository_id}`,
                            {
                              selected: event.target.checked,
                              sync_mode: repository.sync_mode,
                            },
                          ),
                        )
                      }
                    />
                    <span className="font-medium">{repository.name}</span>
                    <Badge variant="secondary">{repository.repository_kind}</Badge>
                  </label>
                  <Select
                    className="h-8 w-36"
                    aria-label={`Sync mode for ${repository.name}`}
                    value={repository.sync_mode}
                    onChange={(event) =>
                      run(`repo-${repository.repository_id}`, () =>
                        api.put(
                          `/integrations/${integration.integration_id}/repositories/${repository.repository_id}`,
                          {
                            selected: repository.is_selected,
                            sync_mode: event.target.value,
                          },
                        ),
                      )
                    }
                  >
                    <option value="INCREMENTAL">Incremental</option>
                    <option value="FULL">Full</option>
                  </Select>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby={`creds-${integration.integration_id}`}>
          <h3
            id={`creds-${integration.integration_id}`}
            className="mb-2 text-sm font-semibold"
          >
            Credentials
          </h3>
          <p className="mb-2 text-xs text-muted-foreground">
            Values are encrypted before storage and are never returned by the API.
          </p>
          <div className="grid gap-2 sm:grid-cols-3">
            <Input
              type="password"
              autoComplete="off"
              placeholder="Access token"
              aria-label="Access token"
              value={token}
              onChange={(event) => setToken(event.target.value)}
            />
            <Input
              autoComplete="off"
              placeholder="Client id (OAuth)"
              aria-label="Client id"
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
            />
            <Input
              type="password"
              autoComplete="off"
              placeholder="Client secret (OAuth)"
              aria-label="Client secret"
              value={clientSecret}
              onChange={(event) => setClientSecret(event.target.value)}
            />
          </div>
          <Button
            className="mt-2"
            size="sm"
            variant="outline"
            onClick={saveCredentials}
            disabled={busy !== null}
          >
            Save credentials
          </Button>
        </section>

        <section aria-labelledby={`jobs-${integration.integration_id}`}>
          <h3
            id={`jobs-${integration.integration_id}`}
            className="mb-2 text-sm font-semibold"
          >
            Recent synchronizations
          </h3>
          {(jobs.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">No runs yet.</p>
          ) : (
            <ul className="space-y-2">
              {(jobs.data ?? []).slice(0, 3).map((job) => (
                <li
                  key={job.sync_job_id}
                  className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 p-3 text-sm"
                >
                  <Badge variant={statusVariant(job.sync_status)}>
                    {titleCase(job.sync_status)}
                  </Badge>
                  <span className="text-muted-foreground">
                    {job.sync_type} · {job.files_processed} processed ·{' '}
                    {job.files_failed} failed · {job.versions_created} versions
                  </span>
                  {job.error_code ? (
                    <span className="text-xs text-destructive">{job.error_code}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby={`docs-${integration.integration_id}`}>
          <h3
            id={`docs-${integration.integration_id}`}
            className="mb-2 text-sm font-semibold"
          >
            Synced documents
          </h3>
          {(documents.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing has been ingested from this source yet.
            </p>
          ) : (
            <ul className="space-y-1 text-sm">
              {(documents.data ?? []).slice(0, 5).map((document) => (
                <li
                  key={document.external_document_id}
                  className="flex flex-wrap items-center gap-2"
                >
                  <CheckCircle2
                    className="size-3.5 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <span>{document.external_file_id}</span>
                  <Badge variant={statusVariant(document.sync_status)}>
                    {titleCase(document.sync_status)}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    permissions: {titleCase(document.permission_state)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby={`maps-${integration.integration_id}`}>
          <h3
            id={`maps-${integration.integration_id}`}
            className="mb-2 text-sm font-semibold"
          >
            Identity mappings
          </h3>
          {(mappings.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Unmapped external principals receive no access. Create a mapping
              through the API before mirroring an external ACL.
            </p>
          ) : (
            <ul className="space-y-1 text-sm">
              {(mappings.data ?? [])
                .filter((mapping) => mapping.is_active)
                .map((mapping) => (
                  <li key={mapping.mapping_id}>
                    {mapping.external_principal_type} {mapping.external_principal_id}
                    {' → '}
                    {mapping.user_id ? `user ${mapping.user_id}` : `role ${mapping.role_id}`}
                  </li>
                ))}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  )
}

export function IntegrationsPage() {
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [provider, setProvider] = useState('local_storage')
  const [name, setName] = useState('')
  const [department, setDepartment] = useState('ENGINEERING')
  const [classification, setClassification] = useState('INTERNAL')
  const [permissionMode, setPermissionMode] = useState('VAULTRAG_MANAGED')
  const [root, setRoot] = useState('')

  const providers = useQuery({
    queryKey: ['integration-providers'],
    queryFn: async () => {
      const { data } = await api.get<{ providers: IntegrationProviderInfo[] }>(
        '/integrations/providers',
      )
      return data.providers
    },
  })

  const integrations = useQuery({
    queryKey: ['integrations'],
    queryFn: async () => {
      const { data } = await api.get<{ integrations: Integration[] }>(
        '/integrations',
      )
      return data.integrations
    },
  })

  const reload = () => {
    void queryClient.invalidateQueries({ queryKey: ['integrations'] })
  }

  const create = async () => {
    setCreating(true)
    setError(null)
    try {
      const configuration: Record<string, string> = { department, classification }
      if (provider === 'local_storage') {
        if (!root.trim()) throw new Error('A local source directory is required')
        configuration.root = root.trim()
      }
      await api.post('/integrations', {
        provider,
        integration_name: name.trim(),
        permission_sync_mode: permissionMode,
        configuration,
      })
      setName('')
      setRoot('')
      reload()
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Administration"
        title="Integrations"
        description="Connect an external knowledge source. Every synced file becomes an ordinary VaultRAG document, so the existing authorization engine still decides who may read it."
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Add a connection</CardTitle>
          <CardDescription>
            Credentials are encrypted with the deployment key; no provider secret
            is stored in plain text.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error ? <ErrorState message={error} /> : null}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="provider">Provider</Label>
              <Select
                id="provider"
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
              >
                {(providers.data ?? []).map((item) => (
                  <option
                    key={item.provider}
                    value={item.provider}
                    disabled={!item.available}
                  >
                    {item.display_name}
                    {item.available ? '' : ' (unavailable)'}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                value={name}
                placeholder="HR policies"
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="permission-mode">Permission mode</Label>
              <Select
                id="permission-mode"
                value={permissionMode}
                onChange={(event) => setPermissionMode(event.target.value)}
              >
                {PERMISSION_MODES.map((mode) => (
                  <option key={mode.value} value={mode.value}>
                    {mode.label}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="department">Document department</Label>
              <Select
                id="department"
                value={department}
                onChange={(event) => setDepartment(event.target.value)}
              >
                {DEPARTMENTS.map((value) => (
                  <option key={value} value={value}>
                    {titleCase(value)}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="classification">Classification</Label>
              <Select
                id="classification"
                value={classification}
                onChange={(event) => setClassification(event.target.value)}
              >
                {['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED'].map(
                  (value) => (
                    <option key={value} value={value}>
                      {titleCase(value)}
                    </option>
                  ),
                )}
              </Select>
            </div>
            {provider === 'local_storage' ? (
              <div className="space-y-1.5 sm:col-span-2 lg:col-span-1">
                <Label htmlFor="root">Source directory</Label>
                <Input
                  id="root"
                  value={root}
                  placeholder="D:\\shared\\policies"
                  onChange={(event) => setRoot(event.target.value)}
                />
              </div>
            ) : null}
          </div>
          <Button
            onClick={create}
            disabled={creating || !name.trim() || providers.isLoading}
          >
            {creating ? (
              <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Plus className="mr-2 size-4" aria-hidden="true" />
            )}
            Create connection
          </Button>
        </CardContent>
      </Card>

      {integrations.isLoading ? <LoadingPanel rows={3} /> : null}
      {integrations.isError ? (
        <ErrorState message={getErrorMessage(integrations.error)} />
      ) : null}
      {integrations.data && integrations.data.length === 0 ? (
        <EmptyState
          title="No integrations yet"
          description="Create a connection above, then store credentials and discover its repositories."
          icon={<Plug className="size-5" aria-hidden="true" />}
        />
      ) : null}

      <div className="space-y-4">
        {(integrations.data ?? []).map((integration) => (
          <IntegrationCard
            key={integration.integration_id}
            integration={integration}
            onChanged={reload}
          />
        ))}
      </div>
    </div>
  )
}
