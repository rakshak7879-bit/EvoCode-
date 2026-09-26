import { AnimatePresence, motion } from 'framer-motion'
import { BadgeCheck, ChevronLeft, ChevronRight, Clapperboard, Pause, Play, Timer, TriangleAlert } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { ModeBadge } from '../components/badges'
import { Button, EmptyState, Panel, Skeleton } from '../components/ui'
import type { RepositoryState } from '../hooks/useRepository'
import { cn, formatSeconds, location } from '../lib/format'
import { highlightLine } from '../lib/highlight'
import { loadSource } from '../lib/sourceCache'
import type { Citation, SourceFile, SourceTarget } from '../types'

function CodePreview({ repoId, citation, onOpen }: { repoId: string; citation: Citation; onOpen: (target: SourceTarget) => void }) {
  const [source, setSource] = useState<SourceFile | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let cancelled = false
    setSource(null)
    setFailed(false)
    loadSource(repoId, citation.file)
      .then((data) => !cancelled && setSource(data))
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
    }
  }, [repoId, citation.file])

  const start = Math.max(1, citation.line_start - 2)
  const end = Math.min(source?.line_count ?? citation.line_end + 3, Math.max(citation.line_end, citation.line_start + 5) + 2)
  const lines = source ? source.content.split('\n').slice(start - 1, end) : []

  return (
    <button
      type="button"
      onClick={() => onOpen({ path: citation.file, lineStart: citation.line_start, lineEnd: citation.line_end, title: citation.label ?? undefined })}
      className="code-surface block w-full overflow-hidden text-left transition hover:border-brand-400/40"
    >
      <div className="flex items-center justify-between border-b border-white/5 px-3 py-1.5">
        <span className="font-mono text-[11px] text-brand-200">{location(citation.file, citation.line_start, citation.line_end)}</span>
        {citation.verification && (
          <span className={cn('flex items-center gap-1 text-[10px] font-bold uppercase', citation.verification.status === 'verified' ? 'text-emerald-300' : 'text-amber-300')}>
            {citation.verification.status === 'verified' ? <BadgeCheck className="size-3" aria-hidden /> : <TriangleAlert className="size-3" aria-hidden />}
            {citation.verification.status}
          </span>
        )}
      </div>
      <pre className="overflow-x-auto py-2 font-mono text-[12px] leading-5">
        {failed && <div className="px-3 text-zinc-500">Source unavailable.</div>}
        {!source && !failed && <div className="px-3 text-zinc-600">Loading…</div>}
        {lines.map((line, offset) => {
          const number = start + offset
          const inRange = number >= citation.line_start && number <= citation.line_end
          return (
            <div key={number} className={cn('grid grid-cols-[3rem_minmax(0,1fr)] pr-3', inRange && 'bg-brand-500/15')}>
              <span className="select-none pr-3 text-right text-zinc-600">{number}</span>
              <code className="whitespace-pre text-zinc-300">{highlightLine(line, source?.language ?? 'javascript')}</code>
            </div>
          )
        })}
      </pre>
    </button>
  )
}

export function WalkthroughTab({ repoId, state, onOpen }: { repoId: string; state: RepositoryState; onOpen: (target: SourceTarget) => void }) {
  const { walkthrough, walkthroughError, status } = state
  const [current, setCurrent] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const steps = walkthrough?.steps ?? []
  const step = steps[current]

  const go = useCallback(
    (index: number) => {
      setCurrent(Math.max(0, Math.min(steps.length - 1, index)))
      setElapsed(0)
    },
    [steps.length],
  )

  useEffect(() => {
    if (!playing) return
    const timer = window.setInterval(() => setElapsed((value) => value + 0.25), 250)
    return () => window.clearInterval(timer)
  }, [playing])

  useEffect(() => {
    if (!playing || !step || elapsed < step.duration) return
    if (current < steps.length - 1) {
      setCurrent(current + 1)
      setElapsed(0)
    } else {
      setPlaying(false)
      setElapsed(0)
    }
  }, [elapsed, playing, step, current, steps.length])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return
      if (event.key === 'ArrowRight') go(current + 1)
      if (event.key === 'ArrowLeft') go(current - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current, go])

  if (status && status.status !== 'completed') {
    return <EmptyState icon={<Clapperboard className="size-5" />} title="Walkthrough is generated after analysis" message="The Walkthrough Agent runs once the Brain passes it the Explainer, Security and Duplicate results." />
  }
  if (walkthroughError) {
    return (
      <EmptyState
        icon={<TriangleAlert className="size-5" />}
        title="Walkthrough unavailable"
        message={`${walkthroughError} The rest of the analysis is still available.`}
      />
    )
  }
  if (!walkthrough || !step) return <Skeleton className="h-96" />

  const elapsedTotal = steps.slice(0, current).reduce((sum, s) => sum + s.duration, 0) + elapsed

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Walkthrough Agent</p>
          <h1 className="mt-1 max-w-3xl text-xl font-semibold text-zinc-50">"{walkthrough.question}"</h1>
          <p className="mt-1 flex items-center gap-2 text-sm text-zinc-400">
            <Timer className="size-4" aria-hidden /> {formatSeconds(walkthrough.total_duration)} · {steps.length} steps ·{' '}
            {walkthrough.verification.verified}/{walkthrough.verification.total} citations verified
          </p>
        </div>
        <ModeBadge mode={walkthrough.mode} />
      </div>

      <div className="h-1.5 overflow-hidden rounded-full bg-white/5" role="progressbar" aria-valuemin={0} aria-valuemax={walkthrough.total_duration} aria-valuenow={Math.round(elapsedTotal)} aria-label="Walkthrough progress">
        <div className="h-full rounded-full bg-linear-to-r from-brand-500 to-cyan-400 transition-all duration-300" style={{ width: `${(elapsedTotal / walkthrough.total_duration) * 100}%` }} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
        <ol className="space-y-1.5">
          {steps.map((item, index) => (
            <li key={item.index}>
              <button
                type="button"
                onClick={() => go(index)}
                aria-current={index === current ? 'step' : undefined}
                className={cn(
                  'flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition',
                  index === current ? 'border-brand-400/50 bg-brand-500/10' : 'border-white/5 bg-white/[0.02] hover:border-white/15',
                )}
              >
                <span className={cn('grid size-6 shrink-0 place-items-center rounded-md font-mono text-[11px]', index < current ? 'bg-emerald-400/15 text-emerald-300' : index === current ? 'bg-brand-500/30 text-brand-100' : 'bg-white/5 text-zinc-500')}>
                  {item.index}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm text-zinc-200">{item.title}</span>
                <span className="font-mono text-[11px] text-zinc-500">{item.duration}s</span>
              </button>
            </li>
          ))}
        </ol>

        <Panel className="p-6">
          <AnimatePresence mode="wait">
            <motion.div key={step.index} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }} transition={{ duration: 0.2 }} className="space-y-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="font-mono text-xs text-brand-300">
                    Step {step.index}/{steps.length} · {step.duration}s
                  </p>
                  <h2 className="mt-1 text-2xl font-semibold text-zinc-50">{step.title}</h2>
                </div>
                <div className="flex items-center gap-1.5">
                  <Button size="sm" variant="ghost" onClick={() => go(current - 1)} disabled={current === 0} aria-label="Previous step" icon={<ChevronLeft className="size-4" />} />
                  <Button size="sm" variant="primary" onClick={() => setPlaying((value) => !value)} icon={playing ? <Pause className="size-4" /> : <Play className="size-4" />}>
                    {playing ? `${Math.ceil(step.duration - elapsed)}s` : 'Present'}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => go(current + 1)} disabled={current === steps.length - 1} aria-label="Next step" icon={<ChevronRight className="size-4" />} />
                </div>
              </div>
              <p className="text-base leading-relaxed text-zinc-300">{step.narration}</p>
              {step.talking_points.length > 0 && (
                <ul className="space-y-1.5">
                  {step.talking_points.map((point) => (
                    <li key={point} className="flex gap-2 text-sm text-zinc-200">
                      <span className="mt-2 size-1.5 shrink-0 rounded-full bg-brand-400" aria-hidden />
                      {point}
                    </li>
                  ))}
                </ul>
              )}
              {step.citations.length > 0 && (
                <div className="space-y-2">
                  <p className="eyebrow">Show this code</p>
                  <CodePreview repoId={repoId} citation={step.citations[0]} onOpen={onOpen} />
                  {step.citations.length > 1 && (
                    <div className="flex flex-wrap gap-1.5">
                      {step.citations.slice(1).map((citation) => (
                        <button
                          key={`${citation.file}:${citation.line_start}`}
                          type="button"
                          onClick={() => onOpen({ path: citation.file, lineStart: citation.line_start, lineEnd: citation.line_end, title: citation.label ?? undefined })}
                          className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 font-mono text-[11px] text-zinc-300 hover:border-brand-400/40"
                          title={citation.label ?? undefined}
                        >
                          {citation.verification?.status === 'verified' && <BadgeCheck className="size-3 text-emerald-400" aria-hidden />}
                          {location(citation.file, citation.line_start)}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </Panel>
      </div>
    </div>
  )
}
