import {
  AlertTriangle,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  ChevronDown,
  FileText,
  History,
  Layers,
  Loader2,
  MessageSquareText,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react'
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'

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
import { useAuth } from '@/contexts/auth-context'
import { useConversations, useRagHealth } from '@/hooks/queries'
import { api, getErrorMessage } from '@/lib/api'
import { cn, formatRelative, pluralize, titleCase } from '@/lib/utils'
import type {
  Conversation,
  ConversationDetail,
  RagAskResponse,
  RagConflict,
  RagEvidenceLevel,
  RagFilters,
  RagSearchMode,
  RagSource,
  RagSourceGroup,
  RagTimings,
} from '@/types/api'

interface ChatEntry {
  id: string
  question: string
  answer?: string
  sources: RagSource[]
  sourceGroups?: RagSourceGroup[]
  retrievedChunks?: number
  model?: string | null
  evidenceLevel?: RagEvidenceLevel
  evidenceScore?: number
  conflicts?: RagConflict[]
  insufficientEvidence?: boolean
  timings?: RagTimings
  createdAt: string
  status: 'pending' | 'complete' | 'error'
  error?: string
}

const suggestions = [
  'Summarize the latest policy documents I can access.',
  'What does the security policy say about incident reporting?',
  'Which accessible document discusses data retention?',
]

const modes: Array<{ value: RagSearchMode; label: string }> = [
  { value: 'hybrid', label: 'Hybrid' },
  { value: 'semantic', label: 'Semantic' },
  { value: 'keyword', label: 'Keyword' },
]

const departments = [
  'ADMIN',
  'ENGINEERING',
  'FINANCE',
  'HR',
  'IT',
  'LEGAL',
  'OPERATIONS',
  'SALES',
  'SECURITY',
]

const classifications = ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED']

const evidenceStyles: Record<RagEvidenceLevel, string> = {
  strong: 'border-success/30 bg-success/10 text-success',
  moderate: 'border-primary/30 bg-primary/10 text-primary',
  limited: 'border-warning/30 bg-warning/10 text-warning',
  insufficient: 'border-destructive/30 bg-destructive/10 text-destructive',
}

function groupsFor(entry: ChatEntry): RagSourceGroup[] {
  if (entry.sourceGroups && entry.sourceGroups.length > 0) {
    return entry.sourceGroups
  }
  const groups = new Map<string, RagSourceGroup>()
  for (const source of entry.sources) {
    const existing = groups.get(source.document_id)
    if (existing) {
      existing.sources.push(source)
      continue
    }
    groups.set(source.document_id, {
      document_id: source.document_id,
      document_name: source.document_name,
      sources: [source],
    })
  }
  return Array.from(groups.values())
}

function entriesFromConversation(detail: ConversationDetail): ChatEntry[] {
  const entries: ChatEntry[] = []
  for (const message of detail.messages) {
    if (message.role === 'user') {
      entries.push({
        id: message.message_id,
        question: message.content,
        sources: [],
        createdAt: message.created_at,
        status: 'complete',
      })
      continue
    }
    if (message.role !== 'assistant' || entries.length === 0) continue
    const entry = entries[entries.length - 1]
    entry.answer = message.content
    entry.createdAt = message.created_at
    entry.evidenceLevel = message.evidence_level ?? undefined
    entry.sources = message.citations.map((citation) => ({
      document_id: citation.document_id,
      document_name: citation.document_name ?? 'Document',
      chunk_id: citation.chunk_id,
      page_start: citation.page_start,
      page_end: citation.page_end,
    }))
    entry.sourceGroups = undefined
    entry.retrievedChunks = message.citations.length
  }
  return entries
}

export function ChatPage() {
  const { user } = useAuth()
  const health = useRagHealth()
  const conversationsQuery = useConversations()
  const conversations: Conversation[] =
    conversationsQuery.data?.conversations ?? []

  const [conversationId, setConversationId] = useState<string | null>(null)
  const [entries, setEntries] = useState<ChatEntry[]>([])
  const [question, setQuestion] = useState('')
  const [topK, setTopK] = useState(10)
  const [mode, setMode] = useState<RagSearchMode>('hybrid')
  const [department, setDepartment] = useState('')
  const [classification, setClassification] = useState('')
  const [pending, setPending] = useState(false)
  const [loadingConversation, setLoadingConversation] = useState(false)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  const activeFilters: RagFilters | undefined =
    department || classification
      ? {
          ...(department ? { department } : {}),
          ...(classification ? { classification } : {}),
        }
      : undefined

  useEffect(() => {
    setConversationId(null)
    setEntries([])
    setQuestion('')
  }, [user?.user_id])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [entries, pending])

  const completedEntries = useMemo(
    () => entries.filter((entry) => entry.status === 'complete'),
    [entries],
  )

  const startNewConversation = () => {
    if (pending) return
    setConversationId(null)
    setEntries([])
    textareaRef.current?.focus()
  }

  const openConversation = async (id: string) => {
    if (pending || id === conversationId) return
    setLoadingConversation(true)
    try {
      const { data } = await api.get<ConversationDetail>(
        `/rag/conversations/${id}`,
      )
      setConversationId(id)
      setEntries(entriesFromConversation(data))
    } catch (error) {
      toast.error('Conversation could not be opened', {
        description: getErrorMessage(error),
      })
    } finally {
      setLoadingConversation(false)
    }
  }

  const submit = async (text: string, replaceId?: string) => {
    const trimmed = text.trim()
    if (!trimmed || pending || !user) return
    const entryId = replaceId ?? crypto.randomUUID()
    const entry: ChatEntry = {
      id: entryId,
      question: trimmed,
      sources: [],
      createdAt: new Date().toISOString(),
      status: 'pending',
    }
    setEntries((current) =>
      replaceId
        ? current.map((item) => (item.id === replaceId ? entry : item))
        : [...current, entry],
    )
    setQuestion('')
    setPending(true)
    try {
      let activeConversationId = conversationId
      if (!activeConversationId) {
        const created = await api.post<Conversation>('/rag/conversations', {})
        activeConversationId = created.data.conversation_id
        setConversationId(activeConversationId)
      }

      const payload: Record<string, unknown> = {
        question: trimmed,
        top_k: topK,
        search_mode: mode,
        conversation_id: activeConversationId,
      }
      if (activeFilters) payload.filters = activeFilters

      const { data } = await api.post<RagAskResponse>('/rag/ask', payload)
      setEntries((current) =>
        current.map((item) =>
          item.id === entryId
            ? {
                ...item,
                answer: data.answer,
                sources: data.sources,
                sourceGroups: data.source_groups,
                retrievedChunks: data.retrieved_chunks,
                model: data.model,
                evidenceLevel: data.evidence?.level,
                evidenceScore: data.evidence?.score,
                conflicts: data.conflicts,
                insufficientEvidence: data.insufficient_evidence,
                timings: data.timings,
                status: 'complete',
              }
            : item,
        ),
      )
      if (data.conversation_id) setConversationId(data.conversation_id)
      void conversationsQuery.refetch()
    } catch (error) {
      const message = getErrorMessage(error)
      setEntries((current) =>
        current.map((item) =>
          item.id === entryId
            ? { ...item, status: 'error', error: message }
            : item,
        ),
      )
      toast.error('The assistant could not answer', { description: message })
    } finally {
      setPending(false)
      textareaRef.current?.focus()
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void submit(question)
    }
  }

  const clearConversation = async () => {
    if (pending) return
    if (!conversationId) {
      setEntries([])
      return
    }
    try {
      await api.post(`/rag/conversations/${conversationId}/clear`)
      setEntries([])
      void conversationsQuery.refetch()
      toast.success('Conversation cleared')
    } catch (error) {
      toast.error('Conversation could not be cleared', {
        description: getErrorMessage(error),
      })
    }
  }

  const deleteConversation = async (id: string) => {
    if (pending) return
    try {
      await api.delete(`/rag/conversations/${id}`)
      if (id === conversationId) {
        setConversationId(null)
        setEntries([])
      }
      void conversationsQuery.refetch()
      toast.success('Conversation deleted')
    } catch (error) {
      toast.error('Conversation could not be deleted', {
        description: getErrorMessage(error),
      })
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Permission-aware RAG"
        title="AI Assistant"
        description="Ask questions against the documents this session is authorized to read. Conversations are saved per user, and citations are validated by the backend."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant="outline"
              className={cn(
                health.data?.status === 'ok'
                  ? 'border-success/30 bg-success/10 text-success'
                  : 'border-warning/30 bg-warning/10 text-warning',
              )}
            >
              <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
              {health.isLoading
                ? 'Checking dependencies'
                : health.isError
                  ? 'Health unavailable'
                  : `System ${titleCase(health.data?.status ?? 'unknown')}`}
            </Badge>
            <Button
              variant="outline"
              size="sm"
              onClick={startNewConversation}
              disabled={pending}
            >
              <Plus aria-hidden="true" />
              New conversation
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void clearConversation()}
              disabled={entries.length === 0 || pending}
            >
              <Trash2 aria-hidden="true" />
              Clear conversation
            </Button>
          </div>
        }
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Card className="flex min-h-[calc(100vh-15rem)] flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-border/60 px-5 py-3">
            <div className="flex items-center gap-2">
              <span className="flex size-8 items-center justify-center rounded-lg border border-primary/20 bg-primary/10 text-primary">
                <Bot className="size-4" aria-hidden="true" />
              </span>
              <div>
                <p className="text-sm font-medium text-foreground">VaultRAG assistant</p>
                <p className="text-[11px] text-muted-foreground">
                  Answers are limited to authorized context
                </p>
              </div>
            </div>
            <Badge variant="secondary" className="hidden normal-case tracking-normal sm:inline-flex">
              <ShieldCheck className="size-3" aria-hidden="true" />
              {conversationId ? 'Saved conversation' : 'New conversation'}
            </Badge>
          </div>

          <div className="scrollbar-thin flex-1 overflow-y-auto px-4 py-5 sm:px-6">
            {loadingConversation ? (
              <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                Loading conversation…
              </div>
            ) : entries.length === 0 ? (
              <div className="mx-auto max-w-2xl py-8">
                <EmptyState
                  title="Start with a question"
                  description="The assistant fuses semantic and keyword retrieval, re-checks document permissions, then generates an answer with validated citations."
                  icon={<MessageSquareText className="size-5" aria-hidden="true" />}
                  className="border-none bg-transparent py-6"
                />
                <div className="mt-2 grid gap-2 sm:grid-cols-3">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => {
                        setQuestion(suggestion)
                        textareaRef.current?.focus()
                      }}
                      className="rounded-lg border border-border/70 bg-card/50 p-3 text-left text-xs leading-5 text-muted-foreground transition-colors hover:border-primary/30 hover:bg-primary/5 hover:text-foreground"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mx-auto max-w-3xl space-y-5">
                {entries.map((entry) => {
                  const groups = groupsFor(entry)
                  return (
                    <div key={entry.id} className="space-y-3">
                      <div className="flex justify-end">
                        <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary/12 px-4 py-3 text-sm leading-6 text-foreground ring-1 ring-primary/15">
                          {entry.question}
                        </div>
                      </div>

                      <div className="flex gap-3">
                        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border border-primary/20 bg-primary/10 text-primary">
                          <Bot className="size-4" aria-hidden="true" />
                        </span>
                        <div className="min-w-0 flex-1">
                          {entry.status === 'pending' ? (
                            <div className="flex items-center gap-2 rounded-2xl rounded-tl-sm border border-border/60 bg-muted/25 px-4 py-3 text-sm text-muted-foreground">
                              <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" />
                              Retrieving authorized context…
                            </div>
                          ) : entry.status === 'error' ? (
                            <div className="space-y-2">
                              <div className="rounded-2xl rounded-tl-sm border border-destructive/25 bg-destructive/10 px-4 py-3 text-sm leading-6 text-destructive">
                                {entry.error}
                              </div>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => void submit(entry.question, entry.id)}
                                disabled={pending}
                              >
                                <RefreshCw aria-hidden="true" />
                                Retry question
                              </Button>
                            </div>
                          ) : (
                            <div className="space-y-3">
                              {entry.insufficientEvidence ? (
                                <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning">
                                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                                  There was not enough authorized evidence to answer confidently.
                                  Try a more specific question or another document set.
                                </div>
                              ) : null}

                              <div className="rounded-2xl rounded-tl-sm border border-border/60 bg-card/60 px-4 py-3 text-sm leading-7 text-foreground">
                                <p className="whitespace-pre-wrap">{entry.answer}</p>
                              </div>

                              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                                {entry.evidenceLevel ? (
                                  <span
                                    className={cn(
                                      'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-medium',
                                      evidenceStyles[entry.evidenceLevel],
                                    )}
                                    title="Qualitative evidence indicator, not a calibrated probability."
                                  >
                                    <ShieldCheck className="size-3" aria-hidden="true" />
                                    {titleCase(entry.evidenceLevel)} evidence
                                    {typeof entry.evidenceScore === 'number'
                                      ? ` · ${entry.evidenceScore.toFixed(2)}`
                                      : ''}
                                  </span>
                                ) : null}
                                {typeof entry.retrievedChunks === 'number' ? (
                                  <span>{pluralize(entry.retrievedChunks, 'chunk')} retrieved</span>
                                ) : null}
                                {entry.model ? (
                                  <>
                                    <span aria-hidden="true">·</span>
                                    <span className="font-mono">{entry.model}</span>
                                  </>
                                ) : null}
                                {groups.length > 0 ? (
                                  <>
                                    <span aria-hidden="true">·</span>
                                    <span>{pluralize(entry.sources.length, 'validated citation')}</span>
                                  </>
                                ) : null}
                                {entry.timings ? (
                                  <>
                                    <span aria-hidden="true">·</span>
                                    <span>{entry.timings.total_ms} ms</span>
                                  </>
                                ) : null}
                              </div>

                              {entry.conflicts && entry.conflicts.length > 0 ? (
                                <div className="rounded-lg border border-warning/30 bg-warning/10 p-3">
                                  <p className="flex items-center gap-2 text-xs font-medium text-warning">
                                    <Layers className="size-3.5" aria-hidden="true" />
                                    Conflicting evidence across documents
                                  </p>
                                  <ul className="mt-2 space-y-2">
                                    {entry.conflicts.map((conflict) => (
                                      <li key={conflict.topic} className="text-[11px] leading-5 text-foreground">
                                        <span className="font-medium">{titleCase(conflict.topic)}</span>
                                        {conflict.values.length > 0
                                          ? `: ${conflict.values.join(' vs ')}`
                                          : ''}
                                        <p className="text-muted-foreground">{conflict.description}</p>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              ) : null}

                              {groups.length > 0 ? (
                                <div className="space-y-2">
                                  {groups.map((group) => (
                                    <details
                                      key={group.document_id}
                                      className="group rounded-lg border border-border/70 bg-card/50"
                                    >
                                      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-foreground">
                                        <FileText className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
                                        <span className="truncate">{group.document_name}</span>
                                        <Badge variant="outline" className="ml-auto normal-case tracking-normal">
                                          {pluralize(group.sources.length, 'chunk')}
                                        </Badge>
                                        <ChevronDown
                                          className="size-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-180"
                                          aria-hidden="true"
                                        />
                                      </summary>
                                      <div className="space-y-2 border-t border-border/60 p-3">
                                        {group.sources.map((source) => (
                                          <div key={source.chunk_id} className="rounded-md bg-muted/20 p-2">
                                            <Link
                                              to={`/documents?document=${source.document_id}&page=${source.page_start}`}
                                              aria-label={`${group.document_name} page ${source.page_start}`}
                                              className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
                                            >
                                              {group.document_name}
                                              <span className="font-normal text-muted-foreground">
                                                Page {source.page_start}
                                                {source.page_end !== source.page_start
                                                  ? `–${source.page_end}`
                                                  : ''}
                                              </span>
                                              <ArrowUpRight className="size-3" aria-hidden="true" />
                                            </Link>
                                            {source.matched_by && source.matched_by.length > 0 ? (
                                              <p className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground/70">
                                                matched by {source.matched_by.join(' + ')}
                                              </p>
                                            ) : null}
                                            {source.excerpt ? (
                                              <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-muted-foreground">
                                                {source.excerpt}
                                              </p>
                                            ) : null}
                                          </div>
                                        ))}
                                      </div>
                                    </details>
                                  ))}
                                </div>
                              ) : (
                                <p className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
                                  No validated document citations were returned for this answer.
                                </p>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <div className="border-t border-border/60 bg-card/60 p-4 sm:p-5">
            <div className="mx-auto max-w-3xl">
              <div className="flex items-end gap-2 rounded-xl border border-border/80 bg-background/60 p-2 focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/15">
                <Textarea
                  ref={textareaRef}
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder="Ask about documents you are allowed to access…"
                  className="min-h-[44px] resize-none border-0 bg-transparent p-2 shadow-none focus:ring-0"
                  rows={1}
                  maxLength={4000}
                  disabled={pending}
                  aria-label="Question"
                />
                <Button
                  size="icon"
                  onClick={() => void submit(question)}
                  disabled={pending || !question.trim()}
                  aria-label="Send question"
                >
                  {pending ? (
                    <Loader2 className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Send aria-hidden="true" />
                  )}
                </Button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-muted-foreground">
                <span>Enter to send · Shift+Enter for a new line</span>
                <label className="flex items-center gap-2">
                  <span>Search mode</span>
                  <Select
                    value={mode}
                    onChange={(event) => setMode(event.target.value as RagSearchMode)}
                    className="h-7 w-28 px-2 text-xs"
                    aria-label="Search mode"
                    disabled={pending}
                  >
                    {modes.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                </label>
                <label className="flex items-center gap-2">
                  <span>Department</span>
                  <Select
                    value={department}
                    onChange={(event) => setDepartment(event.target.value)}
                    className="h-7 w-28 px-2 text-xs"
                    aria-label="Department filter"
                    disabled={pending}
                  >
                    <option value="">Any</option>
                    {departments.map((value) => (
                      <option key={value} value={value}>
                        {titleCase(value)}
                      </option>
                    ))}
                  </Select>
                </label>
                <label className="flex items-center gap-2">
                  <span>Classification</span>
                  <Select
                    value={classification}
                    onChange={(event) => setClassification(event.target.value)}
                    className="h-7 w-32 px-2 text-xs"
                    aria-label="Classification filter"
                    disabled={pending}
                  >
                    <option value="">Any</option>
                    {classifications.map((value) => (
                      <option key={value} value={value}>
                        {titleCase(value)}
                      </option>
                    ))}
                  </Select>
                </label>
                <label className="flex items-center gap-2">
                  <span>Candidate chunks</span>
                  <Select
                    value={String(topK)}
                    onChange={(event) => setTopK(Number(event.target.value))}
                    className="h-7 w-20 px-2 text-xs"
                    aria-label="Candidate chunks"
                    disabled={pending}
                  >
                    {[5, 10, 15, 20, 30].map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </Select>
                </label>
              </div>
              <p className="mt-2 text-[10px] leading-4 text-muted-foreground/80">
                Filters only narrow the documents you may already read. They never
                grant access, and the backend applies tenant and permission rules first.
              </p>
            </div>
          </div>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <History className="size-4 text-primary" aria-hidden="true" />
                  Conversations
                </CardTitle>
                <CardDescription>
                  {conversations.length === 0
                    ? 'Saved per user, server-side.'
                    : `${pluralize(conversations.length, 'conversation')} saved`}
                </CardDescription>
              </div>
              <Button
                variant="outline"
                size="icon"
                onClick={startNewConversation}
                aria-label="Start a new conversation"
                disabled={pending}
              >
                <Plus aria-hidden="true" />
              </Button>
            </CardHeader>
            <CardContent>
              {conversationsQuery.isLoading ? (
                <p className="text-xs text-muted-foreground">Loading conversations…</p>
              ) : conversations.length === 0 ? (
                <p className="text-xs leading-5 text-muted-foreground">
                  Ask a question to create your first saved conversation. Messages and
                  citations are stored for your user only; document text is never
                  persisted in conversation history.
                </p>
              ) : (
                <ul className="space-y-2">
                  {conversations.map((conversation) => (
                    <li
                      key={conversation.conversation_id}
                      className={cn(
                        'flex items-start gap-1 rounded-lg border p-2 transition-colors',
                        conversation.conversation_id === conversationId
                          ? 'border-primary/35 bg-primary/5'
                          : 'border-border/60 hover:border-primary/25 hover:bg-muted/20',
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => void openConversation(conversation.conversation_id)}
                        className="min-w-0 flex-1 text-left"
                      >
                        <p className="truncate text-xs font-medium text-foreground">
                          {conversation.title}
                        </p>
                        <p className="mt-1 text-[10px] text-muted-foreground">
                          {pluralize(conversation.message_count, 'message')} ·{' '}
                          {formatRelative(conversation.updated_at)}
                        </p>
                      </button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 shrink-0 text-muted-foreground hover:text-destructive"
                        onClick={() => void deleteConversation(conversation.conversation_id)}
                        aria-label={`Delete conversation ${conversation.title}`}
                        disabled={pending}
                      >
                        <Trash2 className="size-3.5" aria-hidden="true" />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-primary" aria-hidden="true" />
                Grounded by policy
              </CardTitle>
              <CardDescription>
                How this assistant handles your question.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {[
                {
                  icon: CheckCircle2,
                  title: 'Authorized retrieval',
                  text: 'Candidate chunks are filtered before context construction.',
                },
                {
                  icon: FileText,
                  title: 'Validated citations',
                  text: 'Sources are generated, checked and re-authorized by the backend.',
                },
                {
                  icon: History,
                  title: 'Saved conversations',
                  text: 'Conversation history is owner-scoped and can be cleared or deleted.',
                },
              ].map((item) => {
                const Icon = item.icon
                return (
                  <div key={item.title} className="flex gap-2.5">
                    <Icon className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
                    <div>
                      <p className="text-xs font-medium text-foreground">{item.title}</p>
                      <p className="mt-0.5 text-[11px] leading-5 text-muted-foreground">
                        {item.text}
                      </p>
                    </div>
                  </div>
                )
              })}
            </CardContent>
          </Card>

          {completedEntries.length > 0 ? (
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <div>
                  <CardTitle className="text-sm">This conversation</CardTitle>
                  <CardDescription>
                    {pluralize(completedEntries.length, 'answer')} in the current thread
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <ol className="space-y-2">
                  {completedEntries.slice(-5).reverse().map((entry) => (
                    <li key={entry.id} className="border-b border-border/50 pb-2 last:border-0 last:pb-0">
                      <p className="line-clamp-2 text-[11px] leading-5 text-foreground">
                        {entry.question}
                      </p>
                      <p className="mt-0.5 text-[10px] text-muted-foreground">
                        {entry.evidenceLevel ? `${titleCase(entry.evidenceLevel)} evidence · ` : ''}
                        {pluralize(entry.sources.length, 'citation')}
                      </p>
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          ) : null}

          {health.data ? (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="size-4 text-primary" aria-hidden="true" />
                  Dependencies
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {Object.entries(health.data.components).map(([name, component]) => (
                  <div key={name} className="flex items-center justify-between gap-2 text-[11px]">
                    <span className="text-muted-foreground">{titleCase(name)}</span>
                    <span className="flex items-center gap-1.5 font-medium text-foreground">
                      <span
                        className={cn(
                          'size-1.5 rounded-full',
                          ['ok', 'ready', 'configured'].includes(component.status)
                            ? 'bg-success'
                            : 'bg-destructive',
                        )}
                      />
                      {titleCase(component.status)}
                    </span>
                  </div>
                ))}
              </CardContent>
            </Card>
          ) : health.isError ? (
            <ErrorState
              title="Health unavailable"
              message="The RAG status endpoint could not be reached. Answers may still work if the backend recovers."
            />
          ) : null}
        </div>
      </div>
    </div>
  )
}
