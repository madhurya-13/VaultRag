import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPage } from '@/pages/chat-page'
import type { Me, RagAskResponse } from '@/types/api'

const mocks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  useRagHealth: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/contexts/auth-context', () => ({ useAuth: mocks.useAuth }))
vi.mock('@/hooks/queries', () => ({ useRagHealth: mocks.useRagHealth }))
vi.mock('@/lib/api', () => ({
  api: { post: mocks.post },
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
    },
  ],
  retrieved_chunks: 3,
  model: 'llama3.2:3b',
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
          ollama: { status: 'ok', detail: null },
        },
      },
      isLoading: false,
      isError: false,
    })
  })

  it('renders a grounded answer with validated citation links', async () => {
    mocks.post.mockResolvedValue({ data: answer })
    const userEventInstance = userEvent.setup()
    renderChat()

    await askQuestion(userEventInstance)

    expect(mocks.post).toHaveBeenCalledWith('/rag/ask', {
      question: 'What does the security policy say about incident reporting?',
      top_k: 10,
    })
    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
    expect(screen.getByText('3 chunks retrieved')).toBeInTheDocument()
    expect(screen.getByText('llama3.2:3b')).toBeInTheDocument()
    expect(screen.getByText('1 validated citation')).toBeInTheDocument()

    const citation = screen.getByRole('link', {
      name: /Security Policy 2026/i,
    })
    expect(citation).toHaveAttribute(
      'href',
      '/documents?document=doc-1&page=2',
    )
    expect(screen.getByText('Page 2–4')).toBeInTheDocument()
  })

  it('shows backend failures in the conversation and keeps the session usable', async () => {
    mocks.post.mockRejectedValue(
      new Error('The embedding model is unavailable'),
    )
    const userEventInstance = userEvent.setup()
    renderChat()

    await askQuestion(userEventInstance)

    expect(
      await screen.findByText('The embedding model is unavailable'),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Question')).toBeEnabled()
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
})
