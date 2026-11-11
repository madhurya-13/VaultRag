import {
  Check,
  CircleOff,
  FilterX,
  Minus,
  Search,
  ShieldCheck,
  UserCheck,
  UserPlus,
  Users,
  UserX,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import {
  RoleBadge,
} from '@/components/common/badges'
import { ConfirmDialog } from '@/components/common/confirm-dialog'
import { PageHeader } from '@/components/common/page-header'
import { StatCard } from '@/components/common/stat-card'
import { EmptyState, ErrorState, LoadingPanel } from '@/components/common/states'
import { CreateUserDialog } from '@/components/users/create-user-dialog'
import { ManageRolesDialog } from '@/components/users/manage-roles-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuth } from '@/contexts/auth-context'
import { useRoles, useUsers } from '@/hooks/queries'
import { api, getErrorMessage } from '@/lib/api'
import {
  CAPABILITY_LABELS,
  roleLabel,
} from '@/lib/capabilities'
import {
  formatDate,
  formatRelative,
  initials,
  pluralize,
  titleCase,
} from '@/lib/utils'
import type { User } from '@/types/api'

export function UsersPage() {
  const { user: currentUser } = useAuth()
  const usersQuery = useUsers()
  const rolesQuery = useRoles()
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [rolesTarget, setRolesTarget] = useState<User | null>(null)
  const [statusTarget, setStatusTarget] = useState<User | null>(null)
  const [statusBusy, setStatusBusy] = useState(false)

  const users = usersQuery.data ?? []
  const roles = rolesQuery.data ?? []
  const roleNames = roles.map((role) => role.role_name)
  const activeCount = users.filter((user) => user.is_active).length
  const adminCount = users.filter((user) => user.roles.includes('ADMIN')).length
  const unassignedCount = users.filter((user) => user.roles.length === 0).length

  const filteredUsers = useMemo(() => {
    const query = search.trim().toLowerCase()
    return users.filter((user) => {
      if (
        query &&
        !user.username.toLowerCase().includes(query) &&
        !user.email.toLowerCase().includes(query)
      ) {
        return false
      }
      if (roleFilter && !user.roles.includes(roleFilter)) return false
      if (statusFilter === 'active' && !user.is_active) return false
      if (statusFilter === 'inactive' && user.is_active) return false
      return true
    })
  }, [users, search, roleFilter, statusFilter])

  const capabilityKeys = useMemo(
    () =>
      Array.from(new Set(roles.flatMap((role) => role.capabilities))).sort(),
    [roles],
  )

  const toggleStatus = async () => {
    if (!statusTarget) return
    if (statusTarget.user_id === currentUser?.user_id) {
      toast.error('You cannot deactivate your own account')
      setStatusTarget(null)
      return
    }
    setStatusBusy(true)
    try {
      await api.patch(`/users/${statusTarget.user_id}/status`, {
        is_active: !statusTarget.is_active,
      })
      await usersQuery.refetch()
      toast.success(
        statusTarget.is_active ? 'User deactivated' : 'User activated',
        { description: statusTarget.email },
      )
      setStatusTarget(null)
    } catch (error) {
      toast.error('Could not update user status', {
        description: getErrorMessage(error),
      })
    } finally {
      setStatusBusy(false)
    }
  }

  const filtersActive = Boolean(search || roleFilter || statusFilter)

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Administration"
        title="Users & roles"
        description="Provision employees, assign tenant-owned roles and review the capability model. Document access remains grant-based and is evaluated separately for every request."
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <UserPlus aria-hidden="true" />
            Create user
          </Button>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Tenant users"
          value={usersQuery.isLoading ? '—' : users.length}
          detail="Loaded from the current tenant"
          icon={Users}
        />
        <StatCard
          label="Active accounts"
          value={usersQuery.isLoading ? '—' : activeCount}
          detail={pluralize(users.length - activeCount, 'inactive account')}
          icon={UserCheck}
          tone="success"
        />
        <StatCard
          label="Administrators"
          value={usersQuery.isLoading ? '—' : adminCount}
          detail="Hold all tenant capabilities"
          icon={ShieldCheck}
        />
        <StatCard
          label="Role-less accounts"
          value={usersQuery.isLoading ? '—' : unassignedCount}
          detail="May require approval"
          icon={UserX}
          tone={unassignedCount > 0 ? 'warning' : 'default'}
        />
      </div>

      <Tabs defaultValue="users" className="space-y-4">
        <TabsList>
          <TabsTrigger value="users">
            <Users className="size-4" aria-hidden="true" />
            Users
          </TabsTrigger>
          <TabsTrigger value="roles">
            <ShieldCheck className="size-4" aria-hidden="true" />
            Roles & permissions
          </TabsTrigger>
        </TabsList>

        <TabsContent value="users" className="space-y-4">
          <div className="grid gap-3 rounded-xl border border-border/60 bg-card/50 p-3 sm:grid-cols-3">
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search users…"
                className="pl-9"
                aria-label="Search users"
              />
            </div>
            <Select
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value)}
              aria-label="Filter by role"
            >
              <option value="">All roles</option>
              {(roleNames.length > 0 ? roleNames : ['ADMIN', 'HR', 'FINANCE', 'ENGINEERING', 'EMPLOYEE']).map(
                (role) => (
                  <option key={role} value={role}>
                    {roleLabel(role)}
                  </option>
                ),
              )}
            </Select>
            <div className="flex gap-2">
              <Select
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
                aria-label="Filter by account status"
              >
                <option value="">All statuses</option>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </Select>
              {filtersActive ? (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => {
                    setSearch('')
                    setRoleFilter('')
                    setStatusFilter('')
                  }}
                  aria-label="Clear filters"
                  title="Clear filters"
                >
                  <FilterX aria-hidden="true" />
                </Button>
              ) : null}
            </div>
          </div>

          <Card>
            <CardHeader className="flex-row items-start justify-between space-y-0">
              <div>
                <CardTitle>Tenant directory</CardTitle>
                <CardDescription>
                  Showing {filteredUsers.length} of {users.length} users. The
                  endpoint returns up to 200 records per request.
                </CardDescription>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void usersQuery.refetch()}
                disabled={usersQuery.isFetching}
              >
                Refresh
              </Button>
            </CardHeader>
            <CardContent className="px-0 pb-2">
              {usersQuery.isLoading ? (
                <div className="px-5">
                  <LoadingPanel rows={6} />
                </div>
              ) : usersQuery.isError ? (
                <div className="px-5">
                  <ErrorState
                    message={getErrorMessage(usersQuery.error)}
                    onRetry={() => void usersQuery.refetch()}
                  />
                </div>
              ) : filteredUsers.length === 0 ? (
                <div className="px-5">
                  <EmptyState
                    title={users.length === 0 ? 'No users yet' : 'No users match'}
                    description={
                      users.length === 0
                        ? 'Provision the first user with the Create user button.'
                        : 'Try a broader search or reset the role and status filters.'
                    }
                    icon={<Users className="size-5" aria-hidden="true" />}
                    action={
                      filtersActive ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setSearch('')
                            setRoleFilter('')
                            setStatusFilter('')
                          }}
                        >
                          <FilterX aria-hidden="true" />
                          Clear filters
                        </Button>
                      ) : (
                        <Button size="sm" onClick={() => setCreateOpen(true)}>
                          <UserPlus aria-hidden="true" />
                          Create user
                        </Button>
                      )
                    }
                  />
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>User</TableHead>
                      <TableHead>Roles</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="hidden md:table-cell">Created</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredUsers.map((user) => {
                      const isSelf = user.user_id === currentUser?.user_id
                      return (
                        <TableRow key={user.user_id}>
                          <TableCell>
                            <div className="flex items-center gap-2.5">
                              <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-muted/50 text-[10px] font-semibold text-muted-foreground">
                                {initials(user.username)}
                              </span>
                              <div className="min-w-0">
                                <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                                  <span className="truncate">{user.username}</span>
                                  {isSelf ? (
                                    <span className="rounded border border-primary/25 bg-primary/10 px-1 py-0.5 text-[9px] text-primary">
                                      You
                                    </span>
                                  ) : null}
                                </p>
                                <p className="truncate text-[11px] text-muted-foreground">
                                  {user.email}
                                </p>
                                <p className="mt-0.5 truncate font-mono text-[9px] text-muted-foreground/60">
                                  {user.user_id}
                                </p>
                              </div>
                            </div>
                          </TableCell>
                          <TableCell>
                            {user.roles.length === 0 ? (
                              <span className="text-[11px] text-warning">No roles</span>
                            ) : (
                              <div className="flex max-w-[260px] flex-wrap gap-1">
                                {user.roles.map((role) => (
                                  <RoleBadge key={role} role={role} />
                                ))}
                              </div>
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge variant={user.is_active ? 'success' : 'destructive'}>
                              {user.is_active ? 'Active' : 'Inactive'}
                            </Badge>
                          </TableCell>
                          <TableCell className="hidden text-xs text-muted-foreground md:table-cell">
                            <span title={formatDate(user.created_at)}>
                              {formatRelative(user.created_at)}
                            </span>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-wrap justify-end gap-1">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setRolesTarget(user)}
                              >
                                <ShieldCheck aria-hidden="true" />
                                Roles
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setStatusTarget(user)}
                                disabled={isSelf && user.is_active}
                                title={
                                  isSelf && user.is_active
                                    ? 'You cannot deactivate your own account'
                                    : undefined
                                }
                                className={
                                  user.is_active
                                    ? 'text-destructive hover:text-destructive'
                                    : 'text-success hover:text-success'
                                }
                              >
                                {user.is_active ? (
                                  <UserX aria-hidden="true" />
                                ) : (
                                  <UserCheck aria-hidden="true" />
                                )}
                                {user.is_active ? 'Deactivate' : 'Activate'}
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="roles" className="space-y-4">
          {rolesQuery.isLoading ? (
            <LoadingPanel rows={5} />
          ) : rolesQuery.isError ? (
            <ErrorState
              message={getErrorMessage(rolesQuery.error)}
              onRetry={() => void rolesQuery.refetch()}
            />
          ) : roles.length === 0 ? (
            <EmptyState
              title="No roles found"
              description="The tenant's role catalogue could not be loaded."
              icon={<CircleOff className="size-5" aria-hidden="true" />}
            />
          ) : (
            <>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {roles.map((role) => (
                  <Card key={role.role_id}>
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <CardTitle className="flex items-center gap-2">
                            <ShieldCheck className="size-4 text-primary" aria-hidden="true" />
                            {roleLabel(role.role_name)}
                          </CardTitle>
                          <CardDescription className="mt-1">
                            {role.description ?? titleCase(role.role_name)}
                          </CardDescription>
                        </div>
                        <Badge variant="secondary" className="normal-case tracking-normal">
                          {pluralize(role.user_count, 'user')}
                        </Badge>
                      </div>
                    </CardHeader>
                    <CardContent className="pt-0">
                      <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                        Tenant capabilities
                      </p>
                      {role.capabilities.length === 0 ? (
                        <p className="text-xs leading-5 text-muted-foreground">
                          No tenant-wide capabilities. Access depends on explicit
                          document permission grants and ABAC attributes.
                        </p>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {role.capabilities.map((capability) => (
                            <Badge
                              key={capability}
                              variant="secondary"
                              className="normal-case tracking-normal"
                            >
                              {CAPABILITY_LABELS[capability] ?? capability}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ))}
              </div>

              <Card>
                <CardHeader>
                  <CardTitle>Capability matrix</CardTitle>
                  <CardDescription>
                    Code-defined tenant capabilities returned by the role catalogue.
                    Document actions are intentionally absent: they require exact
                    document grants.
                  </CardDescription>
                </CardHeader>
                <CardContent className="px-0 pb-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Capability</TableHead>
                        {roles.map((role) => (
                          <TableHead key={role.role_id} className="text-center">
                            {role.role_name}
                          </TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {capabilityKeys.map((capability) => (
                        <TableRow key={capability}>
                          <TableCell className="font-medium">
                            {CAPABILITY_LABELS[capability] ?? titleCase(capability)}
                          </TableCell>
                          {roles.map((role) => {
                            const held = role.capabilities.includes(capability)
                            return (
                              <TableCell key={role.role_id} className="text-center">
                                {held ? (
                                  <Check
                                    className="mx-auto size-4 text-success"
                                    aria-label="Granted"
                                  />
                                ) : (
                                  <Minus
                                    className="mx-auto size-4 text-muted-foreground/40"
                                    aria-label="Not granted"
                                  />
                                )}
                              </TableCell>
                            )
                          })}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {capabilityKeys.length === 0 ? (
                    <p className="p-5 text-center text-sm text-muted-foreground">
                      No tenant capabilities are currently assigned to any role.
                    </p>
                  ) : null}
                </CardContent>
              </Card>

              <div className="rounded-lg border border-border/60 bg-muted/20 p-3 text-[11px] leading-5 text-muted-foreground">
                Role and permission changes are audited server-side. Revoked roles
                take effect on the next request; existing JWTs do not preserve old
                authorization.
              </div>
            </>
          )}
        </TabsContent>
      </Tabs>

      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} />
      <ManageRolesDialog
        user={rolesTarget}
        open={rolesTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRolesTarget(null)
        }}
        isSelf={rolesTarget?.user_id === currentUser?.user_id}
      />
      <ConfirmDialog
        open={statusTarget !== null}
        onOpenChange={(open) => {
          if (!open) setStatusTarget(null)
        }}
        title={statusTarget?.is_active ? 'Deactivate this user?' : 'Activate this user?'}
        description={
          statusTarget
            ? statusTarget.is_active
              ? `${statusTarget.email} will lose access immediately. Existing tokens stop working on the next request.`
              : `${statusTarget.email} will be able to sign in again, subject to role and document permissions.`
            : ''
        }
        confirmLabel={statusTarget?.is_active ? 'Deactivate user' : 'Activate user'}
        destructive={Boolean(statusTarget?.is_active)}
        busy={statusBusy}
        onConfirm={() => void toggleStatus()}
      />
    </div>
  )
}
