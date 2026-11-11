import { Badge } from '@/components/ui/badge'
import { CAPABILITY_LABELS, roleLabel } from '@/lib/capabilities'
import {
  classForClassification,
  classForDecision,
  classForStatus,
  titleCase,
} from '@/lib/utils'

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="outline" className={classForStatus(status)}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      {titleCase(status)}
    </Badge>
  )
}

export function DecisionBadge({ decision }: { decision: string }) {
  return (
    <Badge variant="outline" className={classForDecision(decision)}>
      {decision}
    </Badge>
  )
}

export function ClassificationBadge({
  classification,
}: {
  classification: string
}) {
  return (
    <Badge variant="outline" className={classForClassification(classification)}>
      {titleCase(classification)}
    </Badge>
  )
}

export function RoleBadge({ role }: { role: string }) {
  return (
    <Badge
      variant={role === 'ADMIN' ? 'default' : 'secondary'}
      className="normal-case tracking-normal"
    >
      {roleLabel(role)}
    </Badge>
  )
}

export function CapabilityBadge({ capability }: { capability: string }) {
  return (
    <Badge variant="secondary" className="normal-case tracking-normal">
      {CAPABILITY_LABELS[capability] ?? titleCase(capability)}
    </Badge>
  )
}
