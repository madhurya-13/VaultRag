import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  CapabilityRoute,
  ProtectedRoute,
} from '@/components/auth/protected-route'
import { CAP } from '@/lib/capabilities'
import type { Me } from '@/types/api'

const auth = vi.hoisted(() => ({ useAuth: vi.fn() }))

vi.mock('@/contexts/auth-context', () => ({ useAuth: auth.useAuth }))

const adminUser: Me = {
  user_id: 'user-admin',
  username: 'admin',
  email: 'admin@example.com',
  tenant_id: 'tenant-1',
  organization_name: 'Acme',
  roles: ['ADMIN'],
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

const employeeUser: Me = {
  ...adminUser,
  user_id: 'user-employee',
  username: 'employee',
  email: 'employee@example.com',
  roles: ['EMPLOYEE'],
}

function renderSecurityRoute() {
  return render(
    <MemoryRouter initialEntries={['/security']}>
      <Routes>
        <Route path="/login" element={<div>Login screen</div>} />
        <Route path="/forbidden" element={<div>Forbidden screen</div>} />
        <Route element={<ProtectedRoute />}>
          <Route
            element={<CapabilityRoute capability={CAP.VIEW_AUDIT_LOGS} />}
          >
            <Route path="/security" element={<div>Security analytics</div>} />
          </Route>
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('route guards', () => {
  beforeEach(() => {
    auth.useAuth.mockReset()
  })

  it('shows a secure-session state while auth is resolving', () => {
    auth.useAuth.mockReturnValue({ status: 'loading', user: null })
    renderSecurityRoute()
    expect(screen.getByRole('status')).toHaveTextContent(
      'Establishing secure session',
    )
  })

  it('redirects unauthenticated visitors to login', async () => {
    auth.useAuth.mockReturnValue({ status: 'unauthenticated', user: null })
    renderSecurityRoute()
    expect(await screen.findByText('Login screen')).toBeInTheDocument()
    expect(screen.queryByText('Security analytics')).not.toBeInTheDocument()
  })

  it('renders the route when the role holds the capability', () => {
    auth.useAuth.mockReturnValue({ status: 'authenticated', user: adminUser })
    renderSecurityRoute()
    expect(screen.getByText('Security analytics')).toBeInTheDocument()
  })

  it('redirects authenticated users without the capability to forbidden', async () => {
    auth.useAuth.mockReturnValue({
      status: 'authenticated',
      user: employeeUser,
    })
    renderSecurityRoute()
    expect(await screen.findByText('Forbidden screen')).toBeInTheDocument()
    expect(screen.queryByText('Security analytics')).not.toBeInTheDocument()
  })
})
