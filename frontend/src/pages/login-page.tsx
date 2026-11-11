import { zodResolver } from '@hookform/resolvers/zod'
import {
  ArrowRight,
  Fingerprint,
  KeyRound,
  LockKeyhole,
  Moon,
  ScanSearch,
  ShieldCheck,
  Sun,
} from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { z } from 'zod'

import { Logo } from '@/components/layout/logo'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { FormField } from '@/components/ui/form-field'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/contexts/auth-context'
import { useTheme } from '@/contexts/theme-context'
import { getErrorMessage } from '@/lib/api'

const loginSchema = z.object({
  tenant_id: z
    .string()
    .trim()
    .min(1, 'Organization ID is required')
    .uuid('Enter the organization UUID provided by your administrator'),
  email: z.string().trim().min(1, 'Email is required').email('Enter a valid email'),
  password: z.string().min(1, 'Password is required'),
})

type LoginValues = z.infer<typeof loginSchema>

const assurances = [
  {
    icon: ScanSearch,
    title: 'Permission-aware retrieval',
    description:
      'Every candidate chunk is authorized before it reaches the language model.',
  },
  {
    icon: Fingerprint,
    title: 'Role and attribute policy',
    description:
      'RBAC and ABAC decisions are evaluated per request by the backend.',
  },
  {
    icon: ShieldCheck,
    title: 'Auditable by design',
    description:
      'Retrieval, generation and citation decisions are recorded in the audit trail.',
  },
]

export function LoginPage() {
  const { status, login } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const navigate = useNavigate()
  const location = useLocation()
  const [serverError, setServerError] = useState<string | null>(null)
  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: {
      tenant_id: '',
      email: '',
      password: '',
    },
  })

  if (status === 'authenticated') {
    return <Navigate to="/dashboard" replace />
  }

  const fromState = location.state as { from?: string } | null
  const redirectTo =
    fromState?.from && fromState.from.startsWith('/')
      ? fromState.from
      : '/dashboard'

  const onSubmit = form.handleSubmit(async (values) => {
    setServerError(null)
    try {
      await login(values)
      navigate(redirectTo, { replace: true })
    } catch (error) {
      setServerError(getErrorMessage(error))
    }
  })

  return (
    <div className="relative min-h-screen overflow-hidden bg-background">
      <div className="surface-grid pointer-events-none absolute inset-0 opacity-60" />
      <div className="pointer-events-none absolute -left-40 top-1/4 size-[34rem] rounded-full bg-primary/10 blur-[120px]" />
      <div className="pointer-events-none absolute -right-32 bottom-0 size-[28rem] rounded-full bg-accent/10 blur-[100px]" />

      <div className="relative mx-auto flex min-h-screen max-w-[1400px] flex-col px-5 py-6 sm:px-8 lg:px-10">
        <header className="flex items-center justify-between">
          <Logo />
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
          >
            {theme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
          </Button>
        </header>

        <div className="grid flex-1 items-center gap-10 py-10 lg:grid-cols-[1.05fr_0.95fr] lg:gap-20 lg:py-16">
          <section className="hidden max-w-xl lg:block">
            <p className="text-[11px] font-semibold uppercase tracking-[0.26em] text-primary">
              Secure knowledge intelligence
            </p>
            <h1 className="mt-5 text-balance text-5xl font-semibold leading-[1.08] tracking-tight text-foreground">
              Knowledge, without the blind spots.
            </h1>
            <p className="mt-5 max-w-lg text-base leading-7 text-muted-foreground">
              VaultRAG connects employees to the documents they are allowed to
              see — with tenant isolation, permission-aware retrieval and
              citations that are validated before they are shown.
            </p>

            <div className="mt-10 space-y-4">
              {assurances.map((item) => {
                const Icon = item.icon
                return (
                  <div
                    key={item.title}
                    className="flex gap-3 rounded-xl border border-border/60 bg-card/45 p-4 backdrop-blur-sm"
                  >
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-primary/20 bg-primary/10 text-primary">
                      <Icon className="size-4" aria-hidden="true" />
                    </span>
                    <div>
                      <p className="text-sm font-medium text-foreground">
                        {item.title}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        {item.description}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          <section className="mx-auto w-full max-w-md">
            <div className="mb-6 flex items-center gap-2 lg:hidden">
              <span className="rounded-full border border-primary/20 bg-primary/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-primary">
                Secure sign-in
              </span>
            </div>
            <Card className="border-border/80 bg-card/85 shadow-[0_40px_120px_-60px_hsl(var(--primary)/0.55)]">
              <CardHeader className="space-y-2 p-6 sm:p-8">
                <div className="mb-2 flex size-11 items-center justify-center rounded-xl border border-primary/25 bg-primary/10 text-primary">
                  <LockKeyhole className="size-5" aria-hidden="true" />
                </div>
                <CardTitle className="text-xl">Sign in to your workspace</CardTitle>
                <CardDescription>
                  Your organization, role and document grants determine what this
                  session can see.
                </CardDescription>
              </CardHeader>
              <CardContent className="p-6 pt-0 sm:p-8 sm:pt-0">
                <form className="space-y-5" onSubmit={onSubmit} noValidate>
                  <FormField
                    id="tenant_id"
                    label="Organization ID"
                    description="The tenant UUID issued by your administrator."
                    error={form.formState.errors.tenant_id?.message}
                    required
                  >
                    <Input
                      id="tenant_id"
                      placeholder="00000000-0000-0000-0000-000000000000"
                      className="font-mono text-xs"
                      autoComplete="organization"
                      spellCheck={false}
                      aria-invalid={Boolean(form.formState.errors.tenant_id)}
                      aria-describedby={
                        form.formState.errors.tenant_id
                          ? 'tenant_id-error'
                          : 'tenant_id-description'
                      }
                      {...form.register('tenant_id')}
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
                      placeholder="you@company.com"
                      autoComplete="email"
                      aria-invalid={Boolean(form.formState.errors.email)}
                      aria-describedby={
                        form.formState.errors.email ? 'email-error' : undefined
                      }
                      {...form.register('email')}
                    />
                  </FormField>

                  <FormField
                    id="password"
                    label="Password"
                    error={form.formState.errors.password?.message}
                    required
                  >
                    <Input
                      id="password"
                      type="password"
                      placeholder="Enter your password"
                      autoComplete="current-password"
                      aria-invalid={Boolean(form.formState.errors.password)}
                      aria-describedby={
                        form.formState.errors.password
                          ? 'password-error'
                          : undefined
                      }
                      {...form.register('password')}
                    />
                  </FormField>

                  {serverError ? (
                    <div
                      role="alert"
                      className="rounded-lg border border-destructive/25 bg-destructive/10 px-3 py-2.5 text-xs leading-5 text-destructive"
                    >
                      {serverError}
                    </div>
                  ) : null}

                  <Button
                    type="submit"
                    className="w-full"
                    size="lg"
                    disabled={form.formState.isSubmitting}
                  >
                    {form.formState.isSubmitting ? (
                      'Verifying credentials…'
                    ) : (
                      <>
                        Sign in
                        <ArrowRight aria-hidden="true" />
                      </>
                    )}
                  </Button>
                </form>

                <div className="mt-5 flex items-start gap-2 rounded-lg border border-border/60 bg-muted/20 p-3">
                  <KeyRound
                    className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <p className="text-[11px] leading-5 text-muted-foreground">
                    No account yet? An administrator in your organization must
                    create it and assign at least one role. Public signup accounts
                    remain inactive until approved.
                  </p>
                </div>
              </CardContent>
            </Card>
            <p className="mt-5 text-center text-[11px] leading-5 text-muted-foreground/70">
              Access tokens expire after the configured interval. Every
              authorization decision is re-evaluated against the current backend
              state.
            </p>
          </section>
        </div>
      </div>
    </div>
  )
}
