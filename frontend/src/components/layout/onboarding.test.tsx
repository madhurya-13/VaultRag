import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Onboarding } from '@/components/layout/onboarding'
import type { Me } from '@/types/api'

const mocks = vi.hoisted(() => ({ useAuth: vi.fn() }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: mocks.useAuth }))

const employee: Me = {
  user_id: 'employee-1',
  username: 'jane',
  email: 'jane@example.com',
  tenant_id: 'tenant-1',
  organization_name: 'Acme',
  roles: ['EMPLOYEE'],
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

describe('Onboarding', () => {
  it('shows employee guidance, dismisses durably and never exposes admin steps', async () => {
    mocks.useAuth.mockReturnValue({ user: employee })
    const user = userEvent.setup()
    render(<Onboarding />)

    expect(screen.getByLabelText('Welcome guide')).toBeInTheDocument()
    expect(screen.getByText(/AI Assistant/)).toBeInTheDocument()
    expect(screen.queryByText(/Compare RAG configurations/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Got it' }))
    expect(screen.queryByLabelText('Welcome guide')).not.toBeInTheDocument()
    expect(window.localStorage.getItem(`vaultrag-onboarding-dismissed:${employee.user_id}`)).toBe('1')
  })

  it('offers administrator guidance to an administrator', () => {
    mocks.useAuth.mockReturnValue({ user: { ...employee, roles: ['ADMIN'] } })
    render(<Onboarding />)
    expect(screen.getByText(/Compare RAG configurations/)).toBeInTheDocument()
    expect(screen.queryByText(/Helpers|Helpful/)).not.toBeInTheDocument()
  })
})
