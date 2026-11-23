import { useEffect, useState } from 'react'
import { Compass, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useAuth } from '@/contexts/auth-context'
import { hasAnyCapability, CAP } from '@/lib/capabilities'

const EMPLOYEE_STEPS = [
  'Ask questions in AI Assistant and open the cited pages.',
  'Browse only the documents your permissions allow.',
  'Use Helpful / Not helpful on an answer to report a problem.',
]

const ADMIN_STEPS = [
  'Upload and process documents; grant exact document actions.',
  'Use Analytics, Feedback and Knowledge gaps to spot quality issues.',
  'Compare RAG configurations in Evaluation and tune defaults in Settings.',
]

export function Onboarding() {
  const { user } = useAuth()
  const [visible, setVisible] = useState(false)
  const key = user ? `vaultrag-onboarding-dismissed:${user.user_id}` : ''

  useEffect(() => {
    if (!user || !key) return
    try {
      setVisible(localStorage.getItem(key) !== '1')
    } catch {
      setVisible(false)
    }
  }, [key, user])

  if (!visible || !user) return null

  const isAdmin = hasAnyCapability(user.roles, [
    CAP.VIEW_ANALYTICS,
    CAP.MANAGE_USERS,
    CAP.MANAGE_EVALUATION,
  ])
  const steps = isAdmin ? ADMIN_STEPS : EMPLOYEE_STEPS

  const dismiss = () => {
    try {
      localStorage.setItem(key, '1')
    } catch {
      // Local storage may be unavailable in hardened browsers; dismissal is
      // still honored for this session.
    }
    setVisible(false)
  }

  return (
    <aside
      aria-label="Welcome guide"
      className="fixed bottom-4 right-4 z-40 w-[min(360px,calc(100vw-2rem))] rounded-xl border border-primary/25 bg-card p-4 shadow-xl"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Compass className="size-4" aria-hidden="true" />
          </span>
          <div>
            <p className="text-sm font-semibold text-foreground">Welcome to VaultRAG</p>
            <p className="text-[11px] text-muted-foreground">
              A short guide for your {isAdmin ? 'administrator' : 'employee'} workspace.
            </p>
          </div>
        </div>
        <Button variant="ghost" size="icon" onClick={dismiss} aria-label="Dismiss welcome guide">
          <X aria-hidden="true" />
        </Button>
      </div>
      <ol className="mt-3 space-y-2 text-[11px] leading-5 text-muted-foreground">
        {steps.map((step, index) => (
          <li key={step} className="flex gap-2">
            <span className="font-mono text-primary">{index + 1}.</span>
            <span>{step}</span>
          </li>
        ))}
      </ol>
      <Button size="sm" className="mt-3 w-full" onClick={dismiss}>
        Got it
      </Button>
    </aside>
  )
}
