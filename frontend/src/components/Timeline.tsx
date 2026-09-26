import { Terminal } from 'lucide-react'
import { useEffect, useRef } from 'react'

import { cn } from '../lib/format'
import type { TimelineEntry } from '../types'
import { Panel, PanelHeader } from './ui'

const LEVEL_STYLE: Record<TimelineEntry['level'], string> = {
  info: 'text-zinc-300',
  success: 'text-emerald-300',
  warning: 'text-amber-300',
  error: 'text-rose-300',
}

export function Timeline({ entries, live }: { entries: TimelineEntry[]; live: boolean }) {
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const element = scroller.current
    if (!element) return
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 80
    if (live || nearBottom) element.scrollTop = element.scrollHeight
  }, [entries.length, live])

  return (
    <Panel>
      <PanelHeader
        icon={<Terminal className="size-4" />}
        title="Brain log"
        subtitle="Every orchestration step, recorded as it happened"
        actions={live ? <span className="pulse-soft font-mono text-[11px] text-brand-300">● live</span> : undefined}
      />
      <div ref={scroller} className="max-h-80 overflow-y-auto px-5 py-3 font-mono text-xs" aria-live={live ? 'polite' : 'off'}>
        {entries.length === 0 ? (
          <p className="py-6 text-center text-zinc-600">Waiting for the first event…</p>
        ) : (
          <ol className="space-y-1">
            {entries.map((entry, index) => (
              <li key={`${entry.ts}-${index}`} className="grid grid-cols-[64px_118px_minmax(0,1fr)] gap-2 leading-5">
                <span className="text-zinc-600 tabular-nums">+{(entry.elapsed_ms / 1000).toFixed(2)}s</span>
                <span className="truncate text-brand-300/80">{entry.stage}</span>
                <span className={cn('break-words', LEVEL_STYLE[entry.level] ?? 'text-zinc-300')}>{entry.message}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </Panel>
  )
}
