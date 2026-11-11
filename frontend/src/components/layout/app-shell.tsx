import {
  Activity,
  ChevronRight,
  Files,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  ShieldAlert,
  Sparkles,
  Sun,
  Users,
  X,
} from 'lucide-react'
import { Suspense, useEffect, useState } from 'react'
import {
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
} from 'react-router-dom'

import { Logo } from '@/components/layout/logo'
import { InlineLoader } from '@/components/common/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Separator } from '@/components/ui/separator'
import { useAuth } from '@/contexts/auth-context'
import { useTheme } from '@/contexts/theme-context'
import { useRagHealth } from '@/hooks/queries'
import { CAP, hasAnyCapability, hasCapability, roleLabel } from '@/lib/capabilities'
import { cn, initials, titleCase } from '@/lib/utils'

interface NavItem {
  to: string
  label: string
  icon: typeof LayoutDashboard
  capability?: string
}

const workspaceNav: NavItem[] = [
  { to: '/dashboard', label: 'Overview', icon: LayoutDashboard },
  { to: '/assistant', label: 'AI Assistant', icon: Sparkles },
  { to: '/documents', label: 'Documents', icon: Files },
]

const adminNav: NavItem[] = [
  {
    to: '/admin/users',
    label: 'Users & roles',
    icon: Users,
    capability: CAP.MANAGE_USERS,
  },
  {
    to: '/security',
    label: 'Security',
    icon: ShieldAlert,
    capability: CAP.VIEW_AUDIT_LOGS,
  },
]

const routeLabels: Record<string, string> = {
  '/dashboard': 'Overview',
  '/assistant': 'AI Assistant',
  '/documents': 'Documents',
  '/admin/users': 'Users & roles',
  '/security': 'Security',
  '/forbidden': 'Access denied',
}

function SidebarLink({
  item,
  onNavigate,
}: {
  item: NavItem
  onNavigate: () => void
}) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          'group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all',
          isActive
            ? 'bg-primary/10 text-primary shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.18)]'
            : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
        )
      }
    >
      {({ isActive }) => (
        <>
          <Icon
            className={cn(
              'size-4 shrink-0 transition-colors',
              isActive ? 'text-primary' : 'text-muted-foreground/80',
            )}
            aria-hidden="true"
          />
          <span className="truncate">{item.label}</span>
          {isActive ? (
            <span className="ml-auto size-1.5 rounded-full bg-primary" />
          ) : null}
        </>
      )}
    </NavLink>
  )
}

function Sidebar({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const allowedAdmin = user
    ? adminNav.filter(
        (item) =>
          !item.capability || hasCapability(user.roles, item.capability),
      )
    : []

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <>
      {open ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-background/75 backdrop-blur-sm lg:hidden"
          aria-label="Close navigation"
          onClick={onClose}
        />
      ) : null}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-72 flex-col border-r border-border/70 bg-sidebar px-4 py-5 transition-transform duration-200 lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
        aria-label="Primary navigation"
      >
        <div className="flex items-center justify-between px-1">
          <Logo />
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X aria-hidden="true" />
          </Button>
        </div>

        <div className="mt-7 flex-1 space-y-6 overflow-y-auto pb-4">
          <div>
            <p className="px-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground/70">
              Workspace
            </p>
            <nav className="mt-2 space-y-1" aria-label="Workspace">
              {workspaceNav.map((item) => (
                <SidebarLink key={item.to} item={item} onNavigate={onClose} />
              ))}
            </nav>
          </div>

          {allowedAdmin.length > 0 ? (
            <div>
              <p className="px-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground/70">
                Administration
              </p>
              <nav className="mt-2 space-y-1" aria-label="Administration">
                {allowedAdmin.map((item) => (
                  <SidebarLink key={item.to} item={item} onNavigate={onClose} />
                ))}
              </nav>
            </div>
          ) : null}

          <div className="rounded-xl border border-border/70 bg-muted/20 p-3">
            <div className="flex items-start gap-2.5">
              <Activity className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
              <div className="min-w-0">
                <p className="text-xs font-medium text-foreground">
                  Permission-aware RAG
                </p>
                <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                  Retrieval and citations are filtered by your current roles,
                  attributes and document grants.
                </p>
              </div>
            </div>
          </div>
        </div>

        <Separator />
        <div className="pt-4">
          <div className="flex items-center gap-2.5 rounded-lg border border-border/60 bg-card/60 p-2.5">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full border border-primary/20 bg-primary/10 text-xs font-semibold text-primary">
              {initials(user?.username ?? 'VaultRAG')}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">
                {user?.username}
              </p>
              <p className="truncate text-[11px] text-muted-foreground">
                {user?.email}
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
              aria-label="Sign out"
              title="Sign out"
            >
              <LogOut aria-hidden="true" />
            </Button>
          </div>
          <div className="mt-2 flex flex-wrap gap-1 px-1">
            {user?.roles.slice(0, 3).map((role) => (
              <Badge key={role} variant="secondary" className="normal-case tracking-normal">
                {roleLabel(role)}
              </Badge>
            ))}
          </div>
        </div>
      </aside>
    </>
  )
}

function Topbar({
  onOpenMenu,
}: {
  onOpenMenu: () => void
}) {
  const { user, logout } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const navigate = useNavigate()
  const location = useLocation()
  const health = useRagHealth()
  const label = routeLabels[location.pathname] ?? 'Workspace'
  const healthStatus = health.data?.status
  const isHealthy = healthStatus === 'ok'
  const isDegraded = healthStatus && healthStatus !== 'ok'

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border/60 bg-background/85 px-4 backdrop-blur-xl sm:px-6 lg:px-8">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        onClick={onOpenMenu}
        aria-label="Open navigation"
      >
        <Menu aria-hidden="true" />
      </Button>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <span>Workspace</span>
          <ChevronRight className="size-3" aria-hidden="true" />
          <span className="truncate text-foreground/80">{label}</span>
        </div>
        <p className="truncate text-[11px] text-muted-foreground/70">
          {user?.organization_name}
        </p>
      </div>

      <div className="hidden items-center gap-2 sm:flex">
        <span
          className={cn(
            'flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium',
            isHealthy
              ? 'border-success/25 bg-success/10 text-success'
              : isDegraded
                ? 'border-warning/25 bg-warning/10 text-warning'
                : 'border-border bg-muted/40 text-muted-foreground',
          )}
          title={
            health.isError
              ? 'RAG health endpoint unreachable'
              : `RAG status: ${healthStatus ?? 'checking'}`
          }
        >
          <span
            className={cn(
              'size-1.5 rounded-full bg-current',
              !healthStatus && 'animate-pulse-soft',
            )}
            aria-hidden="true"
          />
          {health.isLoading
            ? 'Checking RAG'
            : health.isError
              ? 'RAG unreachable'
              : `RAG ${titleCase(healthStatus ?? 'unknown')}`}
        </span>
      </div>

      <Button
        variant="ghost"
        size="icon"
        onClick={toggleTheme}
        aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
      >
        {theme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
      </Button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex items-center gap-2 rounded-full border border-border/70 bg-card/70 p-1 pr-2 transition-colors hover:border-primary/30 focus:outline-none focus:ring-2 focus:ring-ring"
            aria-label="Open account menu"
          >
            <span className="flex size-8 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
              {initials(user?.username ?? 'VR')}
            </span>
            <span className="hidden max-w-28 truncate text-xs font-medium text-foreground sm:block">
              {user?.username}
            </span>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          <DropdownMenuLabel>
            <span className="block truncate text-sm text-foreground">
              {user?.username}
            </span>
            <span className="mt-0.5 block truncate text-[11px] font-normal text-muted-foreground">
              {user?.email}
            </span>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <div className="px-2 py-1.5">
            <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
              Tenant
            </p>
            <p className="mt-0.5 truncate font-mono text-[11px] text-foreground/80">
              {user?.tenant_id}
            </p>
          </div>
          <div className="flex flex-wrap gap-1 px-2 pb-1.5">
            {user?.roles.map((role) => (
              <Badge key={role} variant="secondary" className="normal-case tracking-normal">
                {roleLabel(role)}
              </Badge>
            ))}
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => {
              logout()
              navigate('/login', { replace: true })
            }}
            className="text-destructive focus:text-destructive"
          >
            <LogOut className="size-4" aria-hidden="true" />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  )
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const { user } = useAuth()

  useEffect(() => {
    setMobileOpen(false)
  }, [location.pathname])

  const hasAdminAccess = user
    ? hasAnyCapability(user.roles, [CAP.MANAGE_USERS, CAP.VIEW_AUDIT_LOGS])
    : false

  return (
    <div className="min-h-screen bg-background">
      <Sidebar open={mobileOpen} onClose={() => setMobileOpen(false)} />
      <div className="lg:pl-72">
        <Topbar onOpenMenu={() => setMobileOpen(true)} />
        <main className="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <Suspense fallback={<InlineLoader label="Loading workspace…" />}>
            <Outlet />
          </Suspense>
          {!hasAdminAccess ? (
            <p className="mt-10 text-center text-[11px] text-muted-foreground/60">
              Need broader access? Contact an administrator in your organization.
            </p>
          ) : null}
        </main>
      </div>
    </div>
  )
}
