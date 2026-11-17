import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from '@/pages/chat-page'
import type { Me, RagAskResponse } from '@/types/api'

const mocks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  useRagHealth: vi.fn(),
  useConversations: vi.fn(),
  post: vi.fn(),
  get: vi.fn(),
  del: vi.fn(),
  refetch: vi.fn(),
}))

vi.mock('@/contexts/auth-context', () => ({ useAuth: mocks.useAuth }))
vi.mock('@/hooks/queries', () => ({
  useRagHealth: mocks.useRagHealth,
  useConversations: mocks.useConversations,
}))
vi.mock('@/lib/api', () => ({
  api: { post: mocks.post, get: mocks.get, delete: mocks.del },
  getErrorMessage: (error: unknown) =>
    error instanceof Error ? error.message : 'Unknown error',
}))

const user: Me = {
  user_id: 'user-employee',
  username: 'jane.doe',
  email: 'jane.doe@example.com',
  tenant_id: 'tenant-1',
  organization_name: 'Acme',
  roles: ['EMPLOYEE'],
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

const conversation = {
  conversation_id: 'conv-1',
  title: 'Security questions',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  message_count: 2,
}

const answer: RagAskResponse = {
  answer:
    'The security policy requires incidents to be reported within one hour.',
  sources: [
    {
      document_id: 'doc-1',
      document_name: 'Security Policy 2026',
      chunk_id: 'abcdef1234567890',
      page_start: 2,
      page_end: 4,
      excerpt: 'All suspected incidents must be reported within 1 hour.',
      matched_by: ['semantic', 'keyword'],
      similarity: 0.82,
    },
  ],
  source_groups: [
    {
      document_id: 'doc-1',
      document_name: 'Security Policy 2026',
      sources: [
        {
          document_id: 'doc-1',
          document_name: 'Security Policy 2026',
          chunk_id: 'abcdef1234567890',
          page_start: 2,
          page_end: 4,
          excerpt: 'All suspected incidents must be reported within 1 hour.',
          matched_by: ['semantic', 'keyword'],
          similarity: 0.82,
        },
      ],
    },
  ],
  retrieved_chunks: 3,
  model: 'llama3.2:3b',
  search_mode: 'hybrid',
  evidence: {
    level: 'strong',
    score: 0.91,
    signals: { distinct_documents: 1, citation_coverage: 1 },
  },
  conflicts: [],
  insufficient_evidence: false,
  timings: { retrieval_ms: 12, rerank_ms: 4, llm_ms: 240, total_ms: 256 },
  conversation_id: 'conv-1',
  message_id: 'msg-2',
}

function renderChat() {
  return render(
    <MemoryRouter>
      <ChatPage />
    </MemoryRouter>,
  )
}

async function askQuestion(userEventInstance: ReturnType<typeof userEvent.setup>) {
  await userEventInstance.type(
    screen.getByLabelText('Question'),
    'What does the security policy say about incident reporting?',
  )
  await userEventInstance.click(
    screen.getByRole('button', { name: 'Send question' }),
  )
}

describe('ChatPage', () => {
  beforeEach(() => {
    mocks.useAuth.mockReturnValue({ user })
    mocks.useRagHealth.mockReturnValue({
      data: {
        status: 'ok',
        components: {
          postgresql: { status: 'ok', detail: null },
          pgvector: { status: 'ok', detail: null },
          embedding_model: { status: 'configured', detail: null },
          full_text_search: { status: 'ready', detail: null },
          ollama: { status: 'ok', detail: null },
        },
      },
      isLoading: false,
      isError: false,
    })
    mocks.useConversations.mockReturnValue({
      data: { conversations: [] },
      isLoading: false,
      isError: false,
      refetch: mocks.refetch,
    })
    mocks.post.mockReset()
    mocks.post.mockImplementation((url: string) => {
      if (url === '/rag/conversations') {
        return Promise.resolve({ data: conversation })
      }
      return Promise.resolve({ data: answer })
    })
    mocks.refetch.mockReset()
  })

  it('renders a grounded answer with evidence, grouped citations and timings', async () => {
    const userEventInstance = userEvent.setup()
    renderChat()

    await askQuestion(userEventInstance)

    expect(mocks.post).toHaveBeenCalledWith('/rag/conversations', {})
    expect(mocks.post).toHaveBeenCalledWith('/rag/ask', {
      question: 'What does the security policy say about incident reporting?',
      top_k: 10,
      search_mode: 'hybrid',
      conversation_id: 'conv-1',
    })
    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
    expect(screen.getByText('3 chunks retrieved')).toBeInTheDocument()
    expect(screen.getByText('llama3.2:3b')).toBeInTheDocument()
    expect(screen.getByText('1 validated citation')).toBeInTheDocument()
    expect(screen.getByText('Strong evidence · 0.91')).toBeInTheDocument()
    expect(screen.getByText(/256 ms/)).toBeInTheDocument()
    expect(
      screen.getByText('All suspected incidents must be reported within 1 hour.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/matched by semantic \+ keyword/i)).toBeInTheDocument()

    const citation = screen.getByRole('link', {
      name: /Security Policy 2026/i,
    })
    expect(citation).toHaveAttribute(
      'href',
      '/documents?document=doc-1&page=2',
    )
    expect(screen.getByText('Page 2–4')).toBeInTheDocument()
    expect(mocks.refetch).toHaveBeenCalled()
  })

  it('sends the selected search mode and metadata filters', async () => {
    const userEventInstance = userEvent.setup()
    renderChat()

    await userEventInstance.selectOptions(
      screen.getByLabelText('Search mode'),
      'keyword',
    )
    await userEventInstance.selectOptions(
      screen.getByLabelText('Department filter'),
      'HR',
    )
    await userEventInstance.selectOptions(
      screen.getByLabelText('Classification filter'),
      'INTERNAL',
    )
    await askQuestion(userEventInstance)

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith('/rag/ask', {
        question:
          'What does the security policy say about incident reporting?',
        top_k: 10,
        search_mode: 'keyword',
        conversation_id: 'conv-1',
        filters: { department: 'HR', classification: 'INTERNAL' },
      }),
    )
  })

  it('renders conflicting evidence without declaring a winner', async () => {
    mocks.post.mockImplementation((url: string) => {
      if (url === '/rag/conversations') {
        return Promise.resolve({ data: conversation })
      }
      return Promise.resolve({
        data: {
          ...answer,
          conflicts: [
            {
              topic: 'annual leave',
              description:
                'Two authorized documents state different annual leave allowances.',
              values: ['18 days', '25 days'],
              sources: answer.sources,
            },
          ],
        },
      })
    })
    const userEventInstance = userEvent.setup()
    renderChat()

    await askQuestion(userEventInstance)

    expect(
      await screen.findByText('Conflicting evidence across documents'),
    ).toBeInTheDocument()
    expect(screen.getByText(/18 days vs 25 days/)).toBeInTheDocument()
  })

  it('shows backend failures in the conversation and offers a retry', async () => {
    mocks.post.mockImplementation((url: string) => {
      if (url === '/rag/conversations') {
        return Promise.resolve({ data: conversation })
      }
      return Promise.reject(new Error('The embedding model is unavailable'))
    })
    const userEventInstance = userEvent.setup()
    renderChat()

    await askQuestion(userEventInstance)

    expect(
      await screen.findByText('The embedding model is unavailable'),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Question')).toBeEnabled()
    expect(
      screen.getByRole('button', { name: 'Retry question' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Send question' }),
    ).toBeDisabled()
  })

  it('does not send an empty question', async () => {
    const userEventInstance = userEvent.setup()
    renderChat()

    expect(
      screen.getByRole('button', { name: 'Send question' }),
    ).toBeDisabled()
    await userEventInstance.click(
      screen.getByRole('button', { name: 'Send question' }),
    )
    await waitFor(() => expect(mocks.post).not.toHaveBeenCalled())
  })

  it('renders model supplied HTML as inert text', async () => {
    const hostileAnswer = '<img src=x onerror="window.__xss=1">'
    mocks.post.mockImplementation((url: string) => {
      if (url === '/rag/conversations') {
        return Promise.resolve({ data: conversation })
      }
      return Promise.resolve({ data: { ...answer, answer: hostileAnswer } })
    })
    const userEventInstance = userEvent.setup()
    const { container } = renderChat()

    await askQuestion(userEventInstance)

    expect(await screen.findByText(hostileAnswer)).toBeInTheDocument()
    expect(container.querySelector('img')).not.toBeInTheDocument()
    expect(window).not.toHaveProperty('__xss')
  })
})
