import type { ReactNode } from 'react'

import { cn } from '../lib/format'

export function MetricCard({
  label,
  value,
  hint,
  icon,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  icon: ReactNode
  tone?: 'default' | 'danger' | 'success' | 'warning'
}) {
  return (
    <div className="panel flex flex-col gap-2 p-4">
      <div className="flex items-center justify-between">
        <span className="eyebrow">{label}</span>
        <span
          className={cn(
            'text-zinc-500',
            tone === 'danger' && 'text-rose-300',
            tone === 'success' && 'text-emerald-300',
            tone === 'warning' && 'text-amber-300',
          )}
          aria-hidden
        >
          {icon}
        </span>
      </div>
      <p className="font-mono text-3xl font-semibold tracking-tight text-zinc-50 tabular-nums">{value}</p>
      {hint && <p className="text-xs text-zinc-500">{hint}</p>}
    </div>
  )
}
