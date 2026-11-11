import { ArrowLeft, Compass } from 'lucide-react'
import { Link } from 'react-router-dom'

import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

export function NotFoundPage() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <Card className="w-full max-w-lg">
        <CardContent className="flex flex-col items-center p-8 text-center">
          <div className="mb-4 flex size-14 items-center justify-center rounded-full border border-primary/25 bg-primary/10 text-primary">
            <Compass className="size-7" aria-hidden="true" />
          </div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-primary">
            404 · Not found
          </p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">
            That workspace view does not exist.
          </h1>
          <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
            The address may be outdated, or you may have followed a link from a
            different deployment.
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
