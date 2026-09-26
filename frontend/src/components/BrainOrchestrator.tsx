import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { BadgeCheck, Brain, Circle, Clapperboard, Compass, LoaderCircle, Recycle, ShieldAlert } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '../lib/format'
import { BRAIN_STEPS, brainHeadline, brainStepState, isBrainActive } from '../lib/stages'
import type { AgentName, AgentState, AgentStatus, RepositoryStatus } from '../types'
import { AgentStateLabel } from './badges'

const CENTER = { x: 200, y: 150 }
const NODES: Record<AgentName, { x: number; y: number; label: string; icon: ReactNode }> = {
  security: { x: 200, y: 72, label: 'Security', icon: <ShieldAlert className="size-5" /> },
  explainer: { x: 350, y: 150, label: 'Explainer', icon: <Compass className="size-5" /> },
  walkthrough: { x: 200, y: 232, label: 'Walkthrough', icon: <Clapperboard className="size-5" /> },
  duplicate: { x: 50, y: 150, label: 'Duplicate', icon: <Recycle className="size-5" /> },
}

const LINE_STYLE: Record<AgentState, string> = {
  pending: 'stroke-zinc-700',
  queued: 'stroke-zinc-500',
  running: 'stroke-brand-400 flow-dash',
  complete: 'stroke-emerald-400/70',
  failed: 'stroke-amber-400',
  skipped: 'stroke-zinc-800',
}

const NODE_STYLE: Record<AgentState, string> = {
  pending: 'border-white/10 bg-ink-800 text-zinc-500',
  queued: 'border-white/15 bg-ink-800 text-zinc-300',
  running: 'border-brand-400/70 bg-brand-500/20 text-brand-100 shadow-[0_0_24px_-4px_rgba(139,92,246,0.8)]',
  complete: 'border-emerald-400/50 bg-emerald-500/10 text-emerald-200',
  failed: 'border-amber-400/60 bg-amber-500/10 text-amber-200',
  skipped: 'border-white/5 bg-ink-900 text-zinc-700',
}

function pct(value: number, total: number): string {
  return `${(value / total) * 100}%`
}

function Packet({ to }: { to: { x: number; y: number } }) {
  return (
    <motion.circle
      r={3.5}
      className="fill-cyan-300"
      initial={{ cx: CENTER.x, cy: CENTER.y, opacity: 0 }}
      animate={{ cx: [CENTER.x, to.x], cy: [CENTER.y, to.y], opacity: [0, 1, 0] }}
      transition={{ duration: 1.1, repeat: Infinity, ease: 'easeInOut' }}
    />
  )
}

export function BrainOrchestrator({ status }: { status: RepositoryStatus | null }) {
  const reduceMotion = useReducedMotion()
  const agents = new Map<AgentName, AgentStatus>((status?.agents ?? []).map((a) => [a.name, a]))
  const brainState = status?.brain.state
  const active = isBrainActive(brainState)
  const headline = brainHeadline(brainState)
  const message = status?.brain.message ?? 'Waiting for a repository…'

  return (
    <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_220px]">
      <div className="relative mx-auto aspect-[4/3] w-full max-w-[520px]" role="img" aria-label={`${headline}. ${message}`}>
        <svg viewBox="0 0 400 300" className="absolute inset-0 size-full" aria-hidden>
          <defs>
            <radialGradient id="brain-glow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#8b5cf6" stopOpacity="0.45" />
              <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0" />
            </radialGradient>
          </defs>
          <circle cx={CENTER.x} cy={CENTER.y} r={active ? 90 : 70} fill="url(#brain-glow)" className="transition-all duration-700" />
          {(Object.keys(NODES) as AgentName[]).map((name) => {
            const node = NODES[name]
            const state = agents.get(name)?.status ?? 'pending'
            return (
              <g key={name}>
                <line
                  x1={CENTER.x}
                  y1={CENTER.y}
                  x2={node.x}
                  y2={node.y}
                  strokeWidth={state === 'running' ? 2.5 : 1.5}
                  strokeDasharray={state === 'queued' || state === 'pending' || state === 'failed' ? '3 5' : undefined}
                  className={cn('transition-colors duration-500', LINE_STYLE[state])}
                />
                {state === 'running' && !reduceMotion && <Packet to={node} />}
              </g>
            )
          })}
        </svg>

        {/* Central Brain */}
        <div
          className="absolute -translate-x-1/2 -translate-y-1/2"
          style={{ left: pct(CENTER.x, 400), top: pct(CENTER.y, 300) }}
        >
          <div className="relative grid size-24 place-items-center">
            <motion.div
              className={cn(
                'absolute inset-0 rounded-full border-2 border-dashed',
                active ? 'border-brand-400/70' : brainState === 'complete' ? 'border-emerald-400/50' : 'border-white/10',
              )}
              animate={active && !reduceMotion ? { rotate: 360 } : { rotate: 0 }}
              transition={active ? { duration: 8, repeat: Infinity, ease: 'linear' } : { duration: 0.4 }}
            />
            <div
              className={cn(
                'grid size-[76px] place-items-center rounded-full border bg-ink-850 transition-colors duration-500',
                active && 'border-brand-400/60 text-brand-100 shadow-[0_0_40px_-6px_rgba(139,92,246,0.9)]',
                brainState === 'complete' && 'border-emerald-400/50 text-emerald-200',
                brainState === 'error' && 'border-amber-400/60 text-amber-200',
                !active && brainState !== 'complete' && brainState !== 'error' && 'border-white/10 text-zinc-400',
              )}
            >
              <Brain className={cn('size-9', active && 'pulse-soft')} aria-hidden />
            </div>
          </div>
        </div>

        {/* Agent nodes: the icon sits exactly on the line end; labels sit away from the line. */}
        {(Object.keys(NODES) as AgentName[]).map((name) => {
          const node = NODES[name]
          const agent = agents.get(name)
          const state = agent?.status ?? 'pending'
          const labelsAbove = node.y < CENTER.y
          return (
            <div key={name} className="absolute size-0" style={{ left: pct(node.x, 400), top: pct(node.y, 300) }}>
              <div className="absolute left-0 top-0 size-12 -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-ink-900">
                <div className={cn('grid size-12 place-items-center rounded-2xl border transition-all duration-500', NODE_STYLE[state])}>
                  {node.icon}
                </div>
              </div>
              <div
                className={cn(
                  'absolute left-0 flex w-max -translate-x-1/2 flex-col items-center rounded-md bg-ink-900/90 px-1.5 py-0.5',
                  labelsAbove ? 'bottom-[30px]' : 'top-[30px]',
                )}
              >
                <span className="text-[11px] font-semibold text-zinc-200">{node.label}</span>
                <AgentStateLabel state={state} />
              </div>
            </div>
          )
        })}
      </div>

      {/* Brain status + checklist */}
      <div className="flex flex-col justify-center gap-4">
        <div>
          <p className={cn('font-mono text-xs font-bold uppercase tracking-[0.25em]', active ? 'text-brand-300' : brainState === 'complete' ? 'text-emerald-300' : brainState === 'error' ? 'text-amber-300' : 'text-zinc-500')}>
            {headline}
          </p>
          <AnimatePresence mode="wait">
            <motion.p
              key={message}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.2 }}
              className="mt-1.5 text-sm text-zinc-300"
              aria-live="polite"
            >
              {message}
            </motion.p>
          </AnimatePresence>
        </div>
        <ul className="space-y-1.5">
          {BRAIN_STEPS.map((step) => {
            const state = brainStepState(step.stages, status)
            return (
              <li key={step.label} className={cn('flex items-center gap-2 text-xs', state === 'done' ? 'text-zinc-300' : state === 'active' ? 'text-brand-200' : 'text-zinc-600')}>
                {state === 'done' ? (
                  <BadgeCheck className="size-3.5 text-emerald-400" aria-hidden />
                ) : state === 'active' ? (
                  <LoaderCircle className="size-3.5 animate-spin text-brand-300" aria-hidden />
                ) : (
                  <Circle className="size-3.5" aria-hidden />
                )}
                {step.label}
                {state === 'active' ? '…' : ''}
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
