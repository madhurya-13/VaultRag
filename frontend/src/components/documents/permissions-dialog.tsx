import { useQueryClient } from '@tanstack/react-query'
import { KeyRound, Loader2, ShieldPlus, ShieldX, Users } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import { ConfirmDialog } from '@/components/common/confirm-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  useDocumentPermissions,
  useRoles,
  useUsers,
} from '@/hooks/queries'
import { api, getErrorMessage } from '@/lib/api'
import { PERMISSION_LABELS } from '@/lib/capabilities'
import { PERMISSION_TYPES } from '@/lib/constants'
import {
  formatDateTime,
  formatRelative,
  titleCase,
  truncateMiddle,
} from '@/lib/utils'
import type {
  Document,
  DocumentPermission,
  PermissionType,
} from '@/types/api'

export function PermissionsDialog({
  document,
  open,
  onOpenChange,
}: {
  document: Document | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [includeRevoked, setIncludeRevoked] = useState(false)
  const [subjectType, setSubjectType] = useState<'role' | 'user'>('role')
  const [roleName, setRoleName] = useState('')
  const [userId, setUserId] = useState('')
  const [permissionType, setPermissionType] =
    useState<PermissionType>('READ')
  const [expiresAt, setExpiresAt] = useState('')
  const [crossDepartment, setCrossDepartment] = useState(false)
  const [busy, setBusy] = useState(false)
  const [revokeTarget, setRevokeTarget] = useState<DocumentPermission | null>(
    null,
  )
  const [revokeBusy, setRevokeBusy] = useState(false)

  const rolesQuery = useRoles(open)
  const usersQuery = useUsers(open)
  const permissionsQuery = useDocumentPermissions(
    document?.document_id ?? null,
    open,
    includeRevoked,
  )

  const roleMap = useMemo(
    () =>
      new Map(
        (rolesQuery.data ?? []).map((role) => [role.role_id, role.role_name]),
      ),
    [rolesQuery.data],
  )
  const userMap = useMemo(
    () =>
      new Map(
        (usersQuery.data ?? []).map((user) => [
          user.user_id,
          user.email || user.username,
        ]),
      ),
    [usersQuery.data],
  )

  const invalidatePermissions = () =>
    queryClient.invalidateQueries({
      queryKey: ['documents', document?.document_id, 'permissions'],
    })

  const grant = async () => {
    if (!document) return
    if (subjectType === 'role' && !roleName) {
      toast.error('Select a role')
      return
    }
    if (subjectType === 'user' && !userId) {
      toast.error('Select a user')
      return
    }
    if (expiresAt && new Date(expiresAt).getTime() <= Date.now()) {
      toast.error('Expiry must be in the future')
      return
    }

    setBusy(true)
    try {
      await api.post(`/documents/${document.document_id}/permissions`, {
        permission_type: permissionType,
        role_name: subjectType === 'role' ? roleName : null,
        user_id: subjectType === 'user' ? userId : null,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
        cross_department_allowed: crossDepartment,
      })
      await invalidatePermissions()
      toast.success('Permission granted')
      setExpiresAt('')
      setCrossDepartment(false)
    } catch (error) {
      toast.error('Could not grant permission', {
        description: getErrorMessage(error),
      })
    } finally {
      setBusy(false)
    }
  }

  const revoke = async () => {
    if (!document || !revokeTarget) return
    setRevokeBusy(true)
    try {
      await api.delete(
        `/documents/${document.document_id}/permissions/${revokeTarget.permission_id}`,
      )
      await invalidatePermissions()
      toast.success('Permission revoked')
      setRevokeTarget(null)
    } catch (error) {
      toast.error('Could not revoke permission', {
        description: getErrorMessage(error),
      })
    } finally {
      setRevokeBusy(false)
    }
  }

  const subjectLabel = (permission: DocumentPermission) => {
    if (permission.role_id) {
      return roleMap.get(permission.role_id) ?? `Role ${truncateMiddle(permission.role_id, 8, 4)}`
    }
    if (permission.user_id) {
      return userMap.get(permission.user_id) ?? `User ${truncateMiddle(permission.user_id, 8, 4)}`
    }
    return 'Unknown subject'
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <div className="mb-2 flex size-10 items-center justify-center rounded-full border border-primary/25 bg-primary/10 text-primary">
              <KeyRound className="size-5" aria-hidden="true" />
            </div>
            <DialogTitle>Document access</DialogTitle>
            <DialogDescription>
              {document?.document_name ?? 'Document'} · Exact-action grants for
              roles and individual users. Revocation is soft and effective
              immediately.
            </DialogDescription>
          </DialogHeader>

          <div className="rounded-xl border border-border/70 bg-muted/15 p-4">
            <div className="mb-3 flex items-center gap-2">
              <ShieldPlus className="size-4 text-primary" aria-hidden="true" />
              <p className="text-sm font-medium text-foreground">Grant access</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1.5">
                <Label htmlFor="subject_type">Subject type</Label>
                <Select
                  id="subject_type"
                  value={subjectType}
                  onChange={(event) => {
                    setSubjectType(event.target.value as 'role' | 'user')
                  }}
                >
                  <option value="role">Role</option>
                  <option value="user">User</option>
                </Select>
              </div>

              {subjectType === 'role' ? (
                <div className="space-y-1.5">
                  <Label htmlFor="role_name">Role</Label>
                  <Select
                    id="role_name"
                    value={roleName}
                    onChange={(event) => setRoleName(event.target.value)}
                    disabled={rolesQuery.isLoading}
                  >
                    <option value="">Select a role</option>
                    {(rolesQuery.data ?? []).map((role) => (
                      <option key={role.role_id} value={role.role_name}>
                        {role.role_name} ({role.user_count})
                      </option>
                    ))}
                  </Select>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="user_id">User</Label>
                  <Select
                    id="user_id"
                    value={userId}
                    onChange={(event) => setUserId(event.target.value)}
                    disabled={usersQuery.isLoading}
                  >
                    <option value="">Select a user</option>
                    {(usersQuery.data ?? []).map((user) => (
                      <option key={user.user_id} value={user.user_id}>
                        {user.email} ({user.username})
                      </option>
                    ))}
                  </Select>
                </div>
              )}

              <div className="space-y-1.5">
                <Label htmlFor="permission_type">Action</Label>
                <Select
                  id="permission_type"
                  value={permissionType}
                  onChange={(event) =>
                    setPermissionType(event.target.value as PermissionType)
                  }
                >
                  {PERMISSION_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {PERMISSION_LABELS[type]}
                    </option>
                  ))}
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="expires_at">Expiry (optional)</Label>
                <Input
                  id="expires_at"
                  type="datetime-local"
                  value={expiresAt}
                  onChange={(event) => setExpiresAt(event.target.value)}
                  className="text-xs"
                />
              </div>
            </div>

            <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={crossDepartment}
                  onChange={(event) => setCrossDepartment(event.target.checked)}
                  className="size-4 rounded border-border bg-background accent-[hsl(var(--primary))]"
                />
                Allow cross-department access
              </label>
              <Button onClick={() => void grant()} disabled={busy}>
                {busy ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <ShieldPlus aria-hidden="true" />
                )}
                Grant permission
              </Button>
            </div>
            {rolesQuery.isError || usersQuery.isError ? (
              <p className="mt-2 text-xs text-destructive">
                Role or user options could not be loaded. You can still review
                existing grants.
              </p>
            ) : null}
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Users className="size-4" aria-hidden="true" />
              {permissionsQuery.data
                ? `${permissionsQuery.data.length} shown`
                : 'Loading grants'}
            </div>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={includeRevoked}
                onChange={(event) => setIncludeRevoked(event.target.checked)}
                className="size-4 rounded border-border bg-background accent-[hsl(var(--primary))]"
              />
              Include revoked
            </label>
          </div>

          {permissionsQuery.isError ? (
            <p className="rounded-lg border border-destructive/25 bg-destructive/10 p-3 text-xs text-destructive">
              {getErrorMessage(permissionsQuery.error)}
            </p>
          ) : permissionsQuery.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, index) => (
                <div
                  key={index}
                  className="h-9 animate-pulse rounded-md bg-muted/60"
                />
              ))}
            </div>
          ) : (permissionsQuery.data ?? []).length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
              No {includeRevoked ? '' : 'active '}grants.
              {!includeRevoked ? ' Administrators retain implicit tenant-wide document access.' : ''}
            </p>
          ) : (
            <div className="max-h-72 overflow-y-auto rounded-lg border border-border/70">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Subject</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Granted</TableHead>
                    <TableHead className="hidden md:table-cell">Expiry</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Control</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(permissionsQuery.data ?? []).map((permission) => (
                    <TableRow
                      key={permission.permission_id}
                      className={!permission.is_active ? 'opacity-55' : undefined}
                    >
                      <TableCell>
                        <p className="text-xs font-medium text-foreground">
                          {subjectLabel(permission)}
                        </p>
                        <p className="mt-0.5 text-[10px] text-muted-foreground">
                          {permission.role_id ? 'Role grant' : 'Direct user grant'}
                        </p>
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary" className="normal-case tracking-normal">
                          {PERMISSION_LABELS[permission.permission_type] ??
                            titleCase(permission.permission_type)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatRelative(permission.granted_at)}
                      </TableCell>
                      <TableCell className="hidden text-xs text-muted-foreground md:table-cell">
                        {formatDateTime(permission.expires_at)}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={
                            permission.is_active
                              ? 'border-success/30 bg-success/10 text-success'
                              : 'border-border bg-muted/40 text-muted-foreground'
                          }
                        >
                          {permission.is_active ? 'Active' : 'Revoked'}
                        </Badge>
                        {permission.cross_department_allowed ? (
                          <p className="mt-1 text-[10px] text-warning">
                            Cross-department
                          </p>
                        ) : null}
                      </TableCell>
                      <TableCell className="text-right">
                        {permission.is_active ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-destructive hover:text-destructive"
                            onClick={() => setRevokeTarget(permission)}
                          >
                            <ShieldX aria-hidden="true" />
                            Revoke
                          </Button>
                        ) : (
                          <span className="text-[10px] text-muted-foreground">
                            {permission.revoked_at
                              ? formatRelative(permission.revoked_at)
                              : '—'}
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokeTarget(null)
        }}
        title="Revoke this permission?"
        description={
          revokeTarget
            ? `${PERMISSION_LABELS[revokeTarget.permission_type] ?? revokeTarget.permission_type} access for ${subjectLabel(revokeTarget)} will be removed immediately. A soft-revoked history row is retained.`
            : ''
        }
        confirmLabel="Revoke permission"
        destructive
        busy={revokeBusy}
        onConfirm={() => void revoke()}
      />
    </>
  )
}
