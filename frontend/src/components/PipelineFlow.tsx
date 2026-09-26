import { motion } from 'framer-motion'
import { BadgeCheck, Bot, Brain, ChevronRight, Database, FolderGit2, GitCompare, ScanSearch, Sparkles } from 'lucide-react'
import { Fragment } from 'react'
import type { ReactNode } from 'react'

import { cn } from '../lib/format'
import { PIPELINE_STEPS } from '../lib/stages'

const ICONS: Record<(typeof PIPELINE_STEPS)[number]['key'], ReactNode> = {
  repository: <FolderGit2 className="size-4" />,
  scanner: <ScanSearch className="size-4" />,
  memory: <Database className="size-4" />,
  brain: <Brain className="size-4" />,
  agents: <Bot className="size-4" />,
  crossval: <GitCompare className="size-4" />,
  verify: <BadgeCheck className="size-4" />,
  intelligence: <Sparkles className="size-4" />,
}

/** Repository → Scanner → Memory → Brain → Agents → Cross-validation → Verification → Intelligence */
export function PipelineFlow({
  active,
  failed = false,
  compact = false,
  wrap = false,
}: {
  active: number
  failed?: boolean
  compact?: boolean
  wrap?: boolean
}) {
  return (
    <ol
      className={cn('flex items-center gap-1', wrap ? 'flex-wrap justify-center gap-y-2' : 'overflow-x-auto pb-1')}
      aria-label="Analysis pipeline"
    >
      {PIPELINE_STEPS.map((step, index) => {
        const state = index < active ? 'done' : index === active ? (failed ? 'failed' : 'active') : 'pending'
        return (
          <Fragment key={step.key}>
            {index > 0 && (
              <ChevronRight
                className={cn('size-3.5 shrink-0', index <= active ? 'text-brand-400/70' : 'text-zinc-700')}
                aria-hidden
              />
            )}
            <li
              aria-current={state === 'active' ? 'step' : undefined}
              className={cn(
                'relative flex shrink-0 items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors',
                state === 'done' && 'border-emerald-400/25 bg-emerald-400/[0.06] text-emerald-200',
                state === 'active' && 'border-brand-400/50 bg-brand-500/15 text-brand-100',
                state === 'failed' && 'border-amber-400/40 bg-amber-400/10 text-amber-200',
                state === 'pending' && 'border-white/5 bg-white/[0.02] text-zinc-500',
              )}
            >
              {state === 'active' && (
                <motion.span
                  className="absolute inset-0 rounded-lg ring-1 ring-brand-400/60"
                  animate={{ opacity: [0.2, 0.9, 0.2] }}
                  transition={{ duration: 1.6, repeat: Infinity }}
                  aria-hidden
                />
              )}
              <span aria-hidden>{ICONS[step.key]}</span>
              {!compact && <span>{step.label}</span>}
              <span className="sr-only">{`: ${state}`}</span>
            </li>
          </Fragment>
        )
      })}
    </ol>
  )
}
