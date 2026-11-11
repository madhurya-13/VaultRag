import { zodResolver } from '@hookform/resolvers/zod'
import { useQueryClient } from '@tanstack/react-query'
import { Loader2, UserPlus } from 'lucide-react'
import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { FormField } from '@/components/ui/form-field'
import { Input } from '@/components/ui/input'
import { queryKeys, useRoles } from '@/hooks/queries'
import { api, getErrorMessage } from '@/lib/api'
import { roleLabel } from '@/lib/capabilities'
import { titleCase } from '@/lib/utils'
import type { User } from '@/types/api'

const createUserSchema = z.object({
  username: z
    .string()
    .trim()
    .min(3, 'Username must be at least 3 characters')
    .max(100, 'Username must be 100 characters or fewer')
    .regex(
      /^[A-Za-z0-9][A-Za-z0-9_.-]{2,99}$/,
      'Use letters, digits, underscore, dot or hyphen, starting with a letter or digit',
    ),
  email: z.string().trim().min(1, 'Email is required').email('Enter a valid email'),
  password: z
    .string()
    .min(12, 'Password must be at least 12 characters')
    .max(128, 'Password must be 128 characters or fewer')
    .refine((value) => /[A-Za-z]/.test(value), 'Include at least one letter')
    .refine((value) => /\d/.test(value), 'Include at least one digit'),
  role_names: z.array(z.string()).min(1, 'Assign at least one role'),
})

type CreateUserValues = z.infer<typeof createUserSchema>

export function CreateUserDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const rolesQuery = useRoles(open)
  const form = useForm<CreateUserValues>({
    resolver: zodResolver(createUserSchema),
    defaultValues: {
      username: '',
      email: '',
      password: '',
      role_names: [],
    },
  })

  useEffect(() => {
    if (!open) form.reset()
  }, [open, form])

  const selectedRoles = form.watch('role_names')

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await api.post<User>('/users', values)
      await queryClient.invalidateQueries({ queryKey: queryKeys.users })
      toast.success('User created', {
        description: `${values.email} can sign in with the assigned roles.`,
      })
      onOpenChange(false)
    } catch (error) {
      toast.error('Could not create user', {
        description: getErrorMessage(error),
      })
    }
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <div className="mb-2 flex size-10 items-center justify-center rounded-full border border-primary/25 bg-primary/10 text-primary">
            <UserPlus className="size-5" aria-hidden="true" />
          </div>
          <DialogTitle>Create a user</DialogTitle>
          <DialogDescription>
            The account is active immediately. The backend normalizes the email
            and username and resolves roles inside this tenant only.
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={onSubmit} noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              id="username"
              label="Username"
              error={form.formState.errors.username?.message}
              required
            >
              <Input
                id="username"
                placeholder="jane.doe"
                autoComplete="off"
                aria-invalid={Boolean(form.formState.errors.username)}
                {...form.register('username')}
              />
            </FormField>
            <FormField
              id="email"
              label="Work email"
              error={form.formState.errors.email?.message}
              required
            >
              <Input
                id="email"
                type="email"
                placeholder="jane@company.com"
                autoComplete="off"
                aria-invalid={Boolean(form.formState.errors.email)}
                {...form.register('email')}
              />
            </FormField>
          </div>

          <FormField
            id="password"
            label="Temporary password"
            description="At least 12 characters with one letter and one digit. Share it through a secure channel."
            error={form.formState.errors.password?.message}
            required
          >
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              aria-invalid={Boolean(form.formState.errors.password)}
              {...form.register('password')}
            />
          </FormField>

          <FormField
            id="role_names"
            label="Roles"
            error={form.formState.errors.role_names?.message}
            description="Roles are tenant-owned. Document access still requires explicit grants unless the role is ADMIN."
            required
          >
            <div className="grid gap-2 sm:grid-cols-2">
              {(rolesQuery.data ?? []).map((role) => {
                const checked = selectedRoles.includes(role.role_name)
                return (
                  <label
                    key={role.role_id}
                    className="flex cursor-pointer items-start gap-2 rounded-lg border border-border/70 bg-background/40 p-3 text-xs transition-colors hover:border-primary/30 has-[:checked]:border-primary/40 has-[:checked]:bg-primary/5"
                  >
                    <input
                      type="checkbox"
                      value={role.role_name}
                      className="mt-0.5 size-4 rounded border-border bg-background accent-[hsl(var(--primary))]"
                      {...form.register('role_names')}
                    />
                    <span>
                      <span className="block font-medium text-foreground">
                        {roleLabel(role.role_name)}
                      </span>
                      <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">
                        {role.description ?? titleCase(role.role_name)}
                      </span>
                      <span className="mt-1 block text-[10px] text-muted-foreground/80">
                        {role.user_count} assigned
                      </span>
                    </span>
                    {checked ? (
                      <span className="ml-auto text-[10px] text-primary">Selected</span>
                    ) : null}
                  </label>
                )
              })}
            </div>
            {rolesQuery.isError ? (
              <p className="text-xs text-destructive">
                Roles could not be loaded: {getErrorMessage(rolesQuery.error)}
              </p>
            ) : null}
          </FormField>

          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={form.formState.isSubmitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={form.formState.isSubmitting}>
              {form.formState.isSubmitting ? (
                <>
                  <Loader2 className="animate-spin" aria-hidden="true" />
                  Creating…
                </>
              ) : (
                <>
                  <UserPlus aria-hidden="true" />
                  Create user
                </>
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
