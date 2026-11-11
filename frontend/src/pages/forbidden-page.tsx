import { ArrowLeft, ShieldX } from 'lucide-react'
import { Link } from 'react-router-dom'

import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

export function ForbiddenPage() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <Card className="w-full max-w-lg">
        <CardContent className="flex flex-col items-center p-8 text-center">
          <div className="mb-4 flex size-14 items-center justify-center rounded-full border border-destructive/25 bg-destructive/10 text-destructive">
            <ShieldX className="size-7" aria-hidden="true" />
          </div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-destructive">
            Access denied
          </p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">
            This view requires different permissions.
          </h1>
          <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
            Your current roles do not include the capability required for this
            page. Ask an administrator in your organization to review your roles.
          </p>
          <Link
            to="/dashboard"
            className={buttonVariants({ variant: 'outline', className: 'mt-5' })}
          >
            <ArrowLeft aria-hidden="true" />
            Return to overview
          </Link>
        </CardContent>
      </Card>
    </div>
  )
}
