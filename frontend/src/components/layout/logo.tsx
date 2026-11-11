import { ShieldCheck } from 'lucide-react'

import { cn } from '@/lib/utils'

export function Logo({
  compact = false,
  className,
}: {
  compact?: boolean
  className?: string
}) {
  return (
    <div className={cn('flex items-center gap-2.5', className)}>
      <span className="relative flex size-9 shrink-0 items-center justify-center rounded-xl border border-primary/30 bg-primary/10 text-primary shadow-[0_0_28px_-10px_hsl(var(--primary)/0.9)]">
        <ShieldCheck className="size-5" aria-hidden="true" />
        <span className="absolute inset-0 rounded-xl bg-gradient-to-br from-primary/20 to-transparent" />
      </span>
      {!compact ? (
        <span className="flex flex-col leading-none">
          <span className="text-[15px] font-semibold tracking-[0.18em] text-foreground">
            VAULTRAG
          </span>
          <span className="mt-1 text-[9px] font-medium uppercase tracking-[0.24em] text-muted-foreground">
            Secure knowledge
          </span>
        </span>
      ) : null}
    </div>
  )
}
