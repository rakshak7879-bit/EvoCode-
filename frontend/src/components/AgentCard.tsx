import { motion } from 'framer-motion'
import { Clapperboard, Compass, Recycle, ShieldAlert } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn, formatMs } from '../lib/format'
import type { AgentName, AgentStatus } from '../types'
import { AgentStateLabel } from './badges'

const ICONS: Record<AgentName, ReactNode> = {
  security: <ShieldAlert className="size-4" />,
  duplicate: <Recycle className="size-4" />,
  explainer: <Compass className="size-4" />,
  walkthrough: <Clapperboard className="size-4" />,
}

export function AgentCard({ agent }: { agent: AgentStatus }) {
  const running = agent.status === 'running'
  return (
    <motion.article
      layout
      className={cn(
        'panel relative overflow-hidden p-4',
        running && 'border-brand-400/40',
        agent.status === 'failed' && 'border-amber-400/30',
        agent.status === 'skipped' && 'opacity-60',
      )}
      aria-busy={running}
    >
      {running && (
        <motion.div
          className="absolute inset-x-0 top-0 h-px bg-linear-to-r from-transparent via-brand-400 to-transparent"
          animate={{ x: ['-100%', '100%'] }}
          transition={{ duration: 1.4, repeat: Infinity, ease: 'linear' }}
          aria-hidden
        />
      )}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              'grid size-8 place-items-center rounded-lg border',
              agent.status === 'complete' ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300' : 'border-white/10 bg-white/[0.04] text-zinc-300',
            )}
            aria-hidden
          >
            {ICONS[agent.name]}
          </span>
          <div>
            <h3 className="text-sm font-semibold text-zinc-100">{agent.title}</h3>
            <AgentStateLabel state={agent.status} />
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 text-[10px] uppercase tracking-wider text-zinc-500">
          {agent.mode && <span>{agent.mode === 'llm' ? 'LLM' : 'Local'}</span>}
          {agent.duration_ms != null && <span className="font-mono normal-case">{formatMs(agent.duration_ms)}</span>}
        </div>
      </div>
      <p className="mt-3 min-h-10 text-sm text-zinc-300">
        {agent.status === 'failed'
          ? 'Failed. The rest of the analysis is still available.'
          : agent.summary ?? (agent.status === 'pending' ? agent.description : 'Waiting for the Brain…')}
      </p>
      {agent.status === 'failed' && agent.error && <p className="mt-1 font-mono text-[11px] text-amber-300/80">{agent.error}</p>}
      {agent.reason && agent.status !== 'failed' && (
        <p className="mt-2 text-[11px] text-zinc-500">
          <span className="text-zinc-600">Routing:</span> {agent.reason}
        </p>
      )}
    </motion.article>
  )
}
