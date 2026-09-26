import { BadgeCheck, CircleX, Cpu, FileX, Info, LoaderCircle, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '../lib/format'
import type { AgentState, LLMInfo, Severity, VerificationStatus } from '../types'

const SEVERITY_STYLES: Record<Severity, string> = {
  critical: 'border-fuchsia-400/40 bg-fuchsia-500/15 text-fuchsia-200',
  high: 'border-rose-400/40 bg-rose-500/15 text-rose-200',
  medium: 'border-amber-400/40 bg-amber-500/15 text-amber-200',
  low: 'border-sky-400/40 bg-sky-500/15 text-sky-200',
  info: 'border-zinc-400/30 bg-zinc-500/15 text-zinc-300',
}
const SEVERITY_DOT: Record<Severity, string> = {
  critical: 'bg-fuchsia-400',
  high: 'bg-rose-400',
  medium: 'bg-amber-400',
  low: 'bg-sky-400',
  info: 'bg-zinc-400',
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider', SEVERITY_STYLES[severity])}>
      <span className={cn('size-1.5 rounded-full', SEVERITY_DOT[severity])} aria-hidden />
      {severity}
    </span>
  )
}

const VERIFICATION: Record<VerificationStatus, { label: string; className: string; icon: ReactNode }> = {
  verified: { label: 'Verified', className: 'border-emerald-400/35 bg-emerald-500/10 text-emerald-300', icon: <BadgeCheck className="size-3.5" aria-hidden /> },
  stale: { label: 'Stale', className: 'border-amber-400/40 bg-amber-500/10 text-amber-300', icon: <TriangleAlert className="size-3.5" aria-hidden /> },
  missing: { label: 'Missing', className: 'border-rose-400/40 bg-rose-500/10 text-rose-300', icon: <FileX className="size-3.5" aria-hidden /> },
  invalid_line: { label: 'Invalid line', className: 'border-rose-400/40 bg-rose-500/10 text-rose-300', icon: <CircleX className="size-3.5" aria-hidden /> },
  unsupported: { label: 'Unsupported', className: 'border-rose-400/40 bg-rose-500/10 text-rose-300', icon: <CircleX className="size-3.5" aria-hidden /> },
  unsafe_path: { label: 'Unsafe path', className: 'border-rose-400/40 bg-rose-500/10 text-rose-300', icon: <CircleX className="size-3.5" aria-hidden /> },
  derived: { label: 'Derived', className: 'border-sky-400/30 bg-sky-500/10 text-sky-300', icon: <Info className="size-3.5" aria-hidden /> },
}

export function VerificationBadge({ status, size = 'sm' }: { status: VerificationStatus | string; size?: 'sm' | 'lg' }) {
  const config = VERIFICATION[status as VerificationStatus] ?? VERIFICATION.unsupported
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md border font-bold uppercase tracking-wider',
        size === 'lg' ? 'px-2.5 py-1 text-xs' : 'px-1.5 py-0.5 text-[10px]',
        config.className,
      )}
    >
      {config.icon}
      {config.label}
    </span>
  )
}

export function ModeBadge({ llm, mode }: { llm?: LLMInfo | null; mode?: string }) {
  const isLlm = (mode ?? llm?.mode) === 'llm'
  return (
    <span
      title={isLlm ? `Analysis enriched by ${llm?.model ?? 'an LLM'}` : 'No LLM configured: deterministic local analysis. Nothing was sent to an external model.'}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider',
        isLlm ? 'border-brand-400/40 bg-brand-500/15 text-brand-200' : 'border-cyan-400/30 bg-cyan-500/10 text-cyan-200',
      )}
    >
      <Cpu className="size-3" aria-hidden />
      {isLlm ? `LLM · ${llm?.model ?? 'model'}` : 'Demo / local analysis'}
    </span>
  )
}

const AGENT_STATE: Record<AgentState, { label: string; className: string }> = {
  pending: { label: 'Pending', className: 'text-zinc-500' },
  queued: { label: 'Queued', className: 'text-zinc-400' },
  running: { label: 'Running', className: 'text-brand-300' },
  complete: { label: 'Complete', className: 'text-emerald-300' },
  failed: { label: 'Failed', className: 'text-amber-300' },
  skipped: { label: 'Skipped', className: 'text-zinc-600' },
}

export function AgentStateLabel({ state }: { state: AgentState }) {
  const config = AGENT_STATE[state]
  return (
    <span className={cn('inline-flex items-center gap-1 text-xs font-medium', config.className)}>
      {state === 'running' && <LoaderCircle className="size-3 animate-spin" aria-hidden />}
      {state === 'complete' && <BadgeCheck className="size-3.5" aria-hidden />}
      {state === 'failed' && <TriangleAlert className="size-3.5" aria-hidden />}
      {config.label}
    </span>
  )
}
