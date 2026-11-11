import {
  ArrowUpRight,
  Bot,
  CheckCircle2,
  FileText,
  History,
  Loader2,
  MessageSquareText,
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
import { useRagHealth } from '@/hooks/queries'
import { api, getErrorMessage } from '@/lib/api'
import { cn, formatRelative, pluralize, titleCase } from '@/lib/utils'
import type { RagAskResponse, RagSource } from '@/types/api'

interface ChatEntry {
  id: string
  question: string
  answer?: string
  sources: RagSource[]
  retrievedChunks?: number
  model?: string | null
  createdAt: string
  status: 'pending' | 'complete' | 'error'
  error?: string
}

const suggestions = [
  'Summarize the latest policy documents I can access.',
  'What does the security policy say about incident reporting?',
  'Which accessible document discusses data retention?',
]

function historyKey(userId: string) {
  return `vaultrag.chat.${userId}`
}

function loadHistory(userId: string): ChatEntry[] {
  try {
    const raw = window.sessionStorage.getItem(historyKey(userId))
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as ChatEntry[]) : []
  } catch {
    return []
  }
}

function saveHistory(userId: string, entries: ChatEntry[]) {
  try {
    window.sessionStorage.setItem(historyKey(userId), JSON.stringify(entries))
  } catch {
    // Session storage may be unavailable in hardened browsers. Chat still works.
  }
}

export function ChatPage() {
  const { user } = useAuth()
  const health = useRagHealth()
  const [entries, setEntries] = useState<ChatEntry[]>(() =>
    user ? loadHistory(user.user_id) : [],
  )
  const [question, setQuestion] = useState('')
  const [topK, setTopK] = useState(10)
  const [pending, setPending] = useState(false)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    if (user) {
      setEntries(loadHistory(user.user_id))
    }
  }, [user])

  useEffect(() => {
    if (user) saveHistory(user.user_id, entries)
  }, [entries, user])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [entries, pending])

  const completedEntries = useMemo(
    () => entries.filter((entry) => entry.status === 'complete'),
    [entries],
  )

  const ask = async () => {
    const trimmed = question.trim()
    if (!trimmed || pending || !user) return
    const entryId = crypto.randomUUID()
    const entry: ChatEntry = {
      id: entryId,
      question: trimmed,
      sources: [],
      createdAt: new Date().toISOString(),
      status: 'pending',
    }
    setEntries((current) => [...current, entry])
    setQuestion('')
    setPending(true)
    try {
      const { data } = await api.post<RagAskResponse>('/rag/ask', {
        question: trimmed,
        top_k: topK,
      })
      setEntries((current) =>
        current.map((item) =>
          item.id === entryId
            ? {
                ...item,
                answer: data.answer,
                sources: data.sources,
                retrievedChunks: data.retrieved_chunks,
                model: data.model,
                status: 'complete',
              }
            : item,
        ),
      )
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
      void ask()
    }
  }

  const clearHistory = () => {
    setEntries([])
    if (user) saveHistory(user.user_id, [])
    toast.success('Session history cleared')
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Permission-aware RAG"
        title="AI Assistant"
        description="Ask questions against the documents this session is authorized to read. Citations are returned by the backend and can be traced to their source pages."
        actions={
          <div className="flex items-center gap-2">
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
              onClick={clearHistory}
              disabled={entries.length === 0 || pending}
            >
              <Trash2 aria-hidden="true" />
              Clear session
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
              Session only
            </Badge>
          </div>

          <div className="scrollbar-thin flex-1 overflow-y-auto px-4 py-5 sm:px-6">
            {entries.length === 0 ? (
              <div className="mx-auto max-w-2xl py-8">
                <EmptyState
                  title="Start with a question"
                  description="The assistant retrieves candidate chunks, checks document permissions and attributes, then generates an answer with validated citations."
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
                {entries.map((entry) => (
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
                          <div className="rounded-2xl rounded-tl-sm border border-destructive/25 bg-destructive/10 px-4 py-3 text-sm leading-6 text-destructive">
                            {entry.error}
                          </div>
                        ) : (
                          <div className="space-y-3">
                            <div className="rounded-2xl rounded-tl-sm border border-border/60 bg-card/60 px-4 py-3 text-sm leading-7 text-foreground">
                              <p className="whitespace-pre-wrap">{entry.answer}</p>
                            </div>

                            <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                              {typeof entry.retrievedChunks === 'number' ? (
                                <span>{pluralize(entry.retrievedChunks, 'chunk')} retrieved</span>
                              ) : null}
                              {entry.model ? (
                                <>
                                  <span aria-hidden="true">·</span>
                                  <span className="font-mono">{entry.model}</span>
                                </>
                              ) : null}
                              {entry.sources.length > 0 ? (
                                <>
                                  <span aria-hidden="true">·</span>
                                  <span>{pluralize(entry.sources.length, 'validated citation')}</span>
                                </>
                              ) : null}
                            </div>

                            {entry.sources.length > 0 ? (
                              <div className="grid gap-2 sm:grid-cols-2">
                                {entry.sources.map((source) => (
                                  <Link
                                    key={`${entry.id}-${source.chunk_id}`}
                                    to={`/documents?document=${source.document_id}&page=${source.page_start}`}
                                    className="group rounded-lg border border-border/70 bg-card/50 p-3 transition-colors hover:border-primary/30 hover:bg-primary/5"
                                  >
                                    <div className="flex items-start gap-2">
                                      <FileText className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden="true" />
                                      <div className="min-w-0">
                                        <p className="truncate text-xs font-medium text-foreground group-hover:text-primary">
                                          {source.document_name}
                                        </p>
                                        <p className="mt-1 text-[11px] text-muted-foreground">
                                          Page {source.page_start}
                                          {source.page_end !== source.page_start
                                            ? `–${source.page_end}`
                                            : ''}
                                        </p>
                                        <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground/70">
                                          chunk {source.chunk_id.slice(0, 8)}
                                        </p>
                                      </div>
                                      <ArrowUpRight className="ml-auto size-3 shrink-0 text-muted-foreground/60" aria-hidden="true" />
                                    </div>
                                  </Link>
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
                ))}
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
                  onClick={() => void ask()}
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
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
                <span>Enter to send · Shift+Enter for a new line</span>
                <label className="flex items-center gap-2">
                  <span>Candidate chunks</span>
                  <Select
                    value={String(topK)}
                    onChange={(event) => setTopK(Number(event.target.value))}
                    className="h-7 w-20 px-2 text-xs"
                    aria-label="Candidate chunks"
                  >
                    {[5, 10, 15, 20, 30].map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </Select>
                </label>
              </div>
            </div>
          </div>
        </Card>

        <div className="space-y-5">
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
                  text: 'Sources are generated and checked by the backend before display.',
                },
                {
                  icon: History,
                  title: 'Session-only history',
                  text: 'Conversation state lives in this browser tab and is not persisted server-side.',
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

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <History className="size-4 text-primary" aria-hidden="true" />
                  Session history
                </CardTitle>
                <CardDescription>
                  {completedEntries.length === 0
                    ? 'No completed answers yet.'
                    : `${pluralize(completedEntries.length, 'answer')} this session`}
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {completedEntries.length === 0 ? (
                <p className="text-xs leading-5 text-muted-foreground">
                  Completed exchanges appear here for this browser tab. Refresh the page to keep the session; close the tab to discard it.
                </p>
              ) : (
                <ol className="space-y-3">
                  {completedEntries.slice(-6).reverse().map((entry) => (
                    <li key={entry.id} className="border-b border-border/50 pb-3 last:border-0 last:pb-0">
                      <p className="line-clamp-2 text-xs leading-5 text-foreground">
                        {entry.question}
                      </p>
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        {formatRelative(entry.createdAt)} ·{' '}
                        {pluralize(entry.sources.length, 'citation')}
                      </p>
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>

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
