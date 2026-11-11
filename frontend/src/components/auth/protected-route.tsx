import { Loader2 } from 'lucide-react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '@/contexts/auth-context'
import { hasAnyCapability, hasCapability } from '@/lib/capabilities'

export function ProtectedRoute() {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return (
      <div
        className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background text-muted-foreground"
        role="status"
      >
        <Loader2 className="size-6 animate-spin text-primary" aria-hidden="true" />
        <p className="text-sm">Establishing secure session…</p>
      </div>
    )
  }

  if (status !== 'authenticated') {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    )
  }

  return <Outlet />
}

export function CapabilityRoute({
  capability,
  anyOf,
}: {
  capability?: string
  anyOf?: string[]
}) {
  const { user } = useAuth()
  const location = useLocation()
  const allowed = user
    ? capability
      ? hasCapability(user.roles, capability)
      : anyOf
        ? hasAnyCapability(user.roles, anyOf)
        : true
    : false

  if (!allowed) {
    return (
      <Navigate
        to="/forbidden"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    )
  }

  return <Outlet />
}
