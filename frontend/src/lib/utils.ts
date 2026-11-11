import { type ClassValue, clsx } from 'clsx'
import { format, formatDistanceToNow, isValid, parseISO } from 'date-fns'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

export function toDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const parsed = parseISO(value)
  return isValid(parsed) ? parsed : null
}

export function formatDate(value: string | null | undefined): string {
  const date = toDate(value)
  return date ? format(date, 'MMM d, yyyy') : '—'
}

export function formatDateTime(value: string | null | undefined): string {
  const date = toDate(value)
  return date ? format(date, 'MMM d, yyyy · HH:mm') : '—'
}

export function formatRelative(value: string | null | undefined): string {
  const date = toDate(value)
  if (!date) return '—'
  return formatDistanceToNow(date, { addSuffix: true })
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || bytes <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  )
  const value = bytes / 1024 ** index
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function initials(value: string): string {
  const parts = value.trim().split(/[\s._-]+/).filter(Boolean)
  if (parts.length === 0) return 'VR'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase()
}

export function truncateMiddle(value: string, front = 8, back = 4): string {
  if (value.length <= front + back + 1) return value
  return `${value.slice(0, front)}…${value.slice(-back)}`
}

export function pluralize(
  count: number,
  singular: string,
  plural = `${singular}s`,
): string {
  return `${count} ${count === 1 ? singular : plural}`
}

export function classForDecision(decision: string): string {
  return decision === 'ALLOW'
    ? 'border-success/30 bg-success/10 text-success'
    : 'border-destructive/30 bg-destructive/10 text-destructive'
}

export function classForStatus(status: string): string {
  switch (status) {
    case 'COMPLETED':
    case 'ALLOW':
    case 'ok':
    case 'ready':
      return 'border-success/30 bg-success/10 text-success'
    case 'PROCESSING':
    case 'PENDING':
    case 'configured':
      return 'border-warning/30 bg-warning/10 text-warning'
    case 'FAILED':
    case 'DENY':
    case 'unavailable':
      return 'border-destructive/30 bg-destructive/10 text-destructive'
    default:
      return 'border-border bg-muted/50 text-muted-foreground'
  }
}

export function classForClassification(classification: string): string {
  switch (classification) {
    case 'PUBLIC':
      return 'border-sky-500/30 bg-sky-500/10 text-sky-400'
    case 'INTERNAL':
      return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
    case 'CONFIDENTIAL':
      return 'border-amber-500/30 bg-amber-500/10 text-amber-400'
    case 'RESTRICTED':
      return 'border-rose-500/30 bg-rose-500/10 text-rose-400'
    default:
      return 'border-border bg-muted/50 text-muted-foreground'
  }
}
