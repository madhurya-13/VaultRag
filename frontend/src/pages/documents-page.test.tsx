import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DocumentsPage } from '@/pages/documents-page'
import type { Document, Me } from '@/types/api'

const mocks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  useDocuments: vi.fn(),
  useDocumentStatus: vi.fn(),
  useDocumentChunks: vi.fn(),
}))

vi.mock('@/contexts/auth-context', () => ({ useAuth: mocks.useAuth }))
vi.mock('@/hooks/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/queries')>()
  return {
    ...actual,
    useDocuments: mocks.useDocuments,
    useDocumentStatus: mocks.useDocumentStatus,
    useDocumentChunks: mocks.useDocumentChunks,
  }
})

const baseUser: Me = {
  user_id: 'user-1',
  username: 'jane.doe',
  email: 'jane.doe@example.com',
  tenant_id: 'tenant-1',
  organization_name: 'Acme',
  roles: ['EMPLOYEE'],
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

const document: Document = {
  document_id: 'doc-1',
  tenant_id: 'tenant-1',
  document_name: 'Information Security Policy 2026',
  document_type: 'PDF',
  department: 'SECURITY',
  classification: 'INTERNAL',
  required_clearance_level: 1,
  uploaded_by: 'user-admin',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  file_hash: null,
  file_size: 1923,
  processing_status: 'COMPLETED',
  total_pages: 4,
  total_chunks: 12,
  embedding_model: 'nomic-embed-text',
}

function renderDocuments() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DocumentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('DocumentsPage', () => {
  beforeEach(() => {
    mocks.useAuth.mockReturnValue({ user: baseUser })
    mocks.useDocuments.mockReturnValue({
      data: [document],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })
    mocks.useDocumentStatus.mockReturnValue({ data: undefined })
    mocks.useDocumentChunks.mockReturnValue({ data: undefined })
  })

  it('shows only backend-authorized documents to employees without admin actions', () => {
    renderDocuments()

    expect(
      screen.getByText('Information Security Policy 2026'),
    ).toBeInTheDocument()
    expect(screen.getByText('Showing 1 of 1 accessible documents.')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Internal' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Completed' })).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /upload pdf/i }),
    ).not.toBeInTheDocument()
  })

  it('exposes upload and management affordances to administrators', () => {
    mocks.useAuth.mockReturnValue({
      user: { ...baseUser, roles: ['ADMIN'] },
    })
    renderDocuments()

    expect(
      screen.getByRole('button', { name: /upload pdf/i }),
    ).toBeInTheDocument()
  })

  it('filters the authorized inventory locally and offers a reset', async () => {
    const user = userEvent.setup()
    renderDocuments()

    await user.type(screen.getByLabelText('Search documents'), 'finance')

    expect(
      screen.getByText('No documents match these filters'),
    ).toBeInTheDocument()
    await user.click(
      screen.getAllByRole('button', { name: /clear filters/i })[0],
    )

    expect(
      screen.getByText('Information Security Policy 2026'),
    ).toBeInTheDocument()
  })

  it('renders an empty state when the backend returns no accessible documents', () => {
    mocks.useDocuments.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })
    renderDocuments()

    expect(screen.getByText('No accessible documents')).toBeInTheDocument()
    expect(
      screen.getByText(/There are no documents this session can read/),
    ).toBeInTheDocument()
  })
})
