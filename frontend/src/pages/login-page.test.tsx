import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LoginPage } from '@/pages/login-page'
import type { Me } from '@/types/api'

const mocks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  login: vi.fn(),
  toggleTheme: vi.fn(),
}))

vi.mock('@/contexts/auth-context', () => ({ useAuth: mocks.useAuth }))
vi.mock('@/contexts/theme-context', () => ({
  useTheme: () => ({ theme: 'dark', toggleTheme: mocks.toggleTheme }),
}))

const tenantId = 'ef0d6bb4-c88c-4c63-ab31-47f84370e3ac'
const adminUser: Me = {
  user_id: 'user-admin',
  username: 'admin',
  email: 'admin@example.com',
  tenant_id: tenantId,
  organization_name: 'Acme',
  roles: ['ADMIN'],
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/dashboard" element={<div>Dashboard destination</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function fillCredentials(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/organization id/i), tenantId)
  await user.type(screen.getByLabelText(/work email/i), 'admin@example.com')
  await user.type(screen.getByLabelText(/^password/i), 'SecurePassword123!')
}

describe('LoginPage', () => {
  beforeEach(() => {
    mocks.useAuth.mockReturnValue({
      status: 'unauthenticated',
      login: mocks.login,
      user: null,
    })
  })

  it('validates the tenant UUID, email and password before calling the API', async () => {
    const user = userEvent.setup()
    renderLogin()

    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(
      await screen.findByText('Organization ID is required'),
    ).toBeInTheDocument()
    expect(screen.getByText('Email is required')).toBeInTheDocument()
    expect(screen.getByText('Password is required')).toBeInTheDocument()
    expect(mocks.login).not.toHaveBeenCalled()
  })

  it('rejects a malformed organization identifier', async () => {
    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText(/organization id/i), 'not-a-uuid')
    await user.type(screen.getByLabelText(/work email/i), 'admin@example.com')
    await user.type(screen.getByLabelText(/^password/i), 'SecurePassword123!')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(
      await screen.findByText(
        'Enter the organization UUID provided by your administrator',
      ),
    ).toBeInTheDocument()
    expect(mocks.login).not.toHaveBeenCalled()
  })

  it('submits exact credentials and navigates to the dashboard', async () => {
    mocks.login.mockResolvedValue(adminUser)
    const user = userEvent.setup()
    renderLogin()

    await fillCredentials(user)
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() =>
      expect(mocks.login).toHaveBeenCalledWith({
        tenant_id: tenantId,
        email: 'admin@example.com',
        password: 'SecurePassword123!',
      }),
    )
    expect(await screen.findByText('Dashboard destination')).toBeInTheDocument()
  })

  it('surfaces backend authentication errors without navigating', async () => {
    mocks.login.mockRejectedValue(new Error('Invalid email or password'))
    const user = userEvent.setup()
    renderLogin()

    await fillCredentials(user)
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Invalid email or password',
    )
    expect(screen.queryByText('Dashboard destination')).not.toBeInTheDocument()
  })
})
