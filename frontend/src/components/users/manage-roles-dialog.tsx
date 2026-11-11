import { useQueryClient } from '@tanstack/react-query'
import { Loader2, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { queryKeys, useRoles } from '@/hooks/queries'
import { api, getErrorMessage } from '@/lib/api'
import { CAPABILITY_LABELS, roleLabel } from '@/lib/capabilities'
import { titleCase } from '@/lib/utils'
import type { User } from '@/types/api'

export function ManageRolesDialog({
  user,
  open,
  onOpenChange,
  isSelf,
}: {
  user: User | null
  open: boolean
  onOpenChange: (open: boolean) => void
  isSelf: boolean
}) {
  const queryClient = useQueryClient()
  const rolesQuery = useRoles(open)
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open && user) {
      setSelected([...user.roles])
    }
  }, [open, user])

  if (!user) return null

  const toggle = (roleName: string) => {
    if (isSelf && roleName === 'ADMIN' && selected.includes('ADMIN')) {
      toast.error('You cannot remove your own administrator role')
      return
    }
    setSelected((current) =>
      current.includes(roleName)
        ? current.filter((role) => role !== roleName)
        : [...current, roleName],
    )
  }

  const save = async () => {
    const current = new Set(user.roles)
    const next = new Set(selected)
    const toAdd = [...next].filter((role) => !current.has(role))
    const toRemove = [...current].filter((role) => !next.has(role))

    if (toAdd.length === 0 && toRemove.length === 0) {
      onOpenChange(false)
      return
    }

    setBusy(true)
    try {
      for (const roleName of toAdd) {
        await api.post(`/users/${user.user_id}/roles`, { role_name: roleName })
      }
      for (const roleName of toRemove) {
        await api.delete(`/users/${user.user_id}/roles/${roleName}`)
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.users })
      await queryClient.invalidateQueries({ queryKey: queryKeys.roles })
      toast.success('Roles updated', {
        description: `${user.email} now holds ${selected.length} role${selected.length === 1 ? '' : 's'}.`,
      })
      onOpenChange(false)
    } catch (error) {
      toast.error('Could not update roles', {
        description: getErrorMessage(error),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <div className="mb-2 flex size-10 items-center justify-center rounded-full border border-primary/25 bg-primary/10 text-primary">
            <ShieldCheck className="size-5" aria-hidden="true" />
          </div>
          <DialogTitle>Manage roles</DialogTitle>
          <DialogDescription>
            {user.username} · {user.email}. Changes take effect on the next
            request because the backend reloads roles from PostgreSQL.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          {(rolesQuery.data ?? []).map((role) => {
            const checked = selected.includes(role.role_name)
            const locked = isSelf && role.role_name === 'ADMIN' && checked
            return (
              <label
                key={role.role_id}
                className="flex cursor-pointer items-start gap-3 rounded-lg border border-border/70 bg-background/40 p-3 transition-colors hover:border-primary/30 has-[:checked]:border-primary/40 has-[:checked]:bg-primary/5"
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={locked || busy}
                  onChange={() => toggle(role.role_name)}
                  className="mt-0.5 size-4 rounded border-border bg-background accent-[hsl(var(--primary))] disabled:opacity-50"
                />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-foreground">
                      {roleLabel(role.role_name)}
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      {role.user_count} assigned
                    </span>
                    {locked ? (
                      <span className="text-[10px] text-warning">Locked for self</span>
                    ) : null}
                  </span>
                  <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">
                    {role.description ?? titleCase(role.role_name)}
                  </span>
                  <span className="mt-1 flex flex-wrap gap-1">
                    {role.capabilities.length === 0 ? (
                      <span className="text-[10px] text-muted-foreground/70">
                        No tenant-wide capabilities; document access is grant-based.
                      </span>
                    ) : (
                      role.capabilities.map((capability) => (
                        <span
                          key={capability}
                          className="rounded border border-border bg-muted/40 px-1.5 py-0.5 text-[9px] text-muted-foreground"
                        >
                          {CAPABILITY_LABELS[capability] ?? capability}
                        </span>
                      ))
                    )}
                  </span>
                </span>
              </label>
            )
          })}
          {rolesQuery.isError ? (
            <p className="text-xs text-destructive">
              Roles could not be loaded: {getErrorMessage(rolesQuery.error)}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={busy}>
            {busy ? (
              <>
                <Loader2 className="animate-spin" aria-hidden="true" />
                Saving…
              </>
            ) : (
              'Save role changes'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
