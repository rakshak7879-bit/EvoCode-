import { Clapperboard, Database, LayoutDashboard, Plus, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useCallback, useState } from 'react'
import type { ReactNode } from 'react'

import { errorMessage } from '../api'
import { ModeBadge } from '../components/badges'
import { Logo } from '../components/Logo'
import { SourceViewer } from '../components/SourceViewer'
import { Button, EmptyState, ErrorBanner } from '../components/ui'
import { routeHref } from '../hooks/useHashRoute'
import type { Tab } from '../hooks/useHashRoute'
import { useRepository } from '../hooks/useRepository'
import { cn } from '../lib/format'
import type { SourceTarget } from '../types'
import { FindingsTab } from '../views/FindingsTab'
import { MemoryTab } from '../views/MemoryTab'
import { OverviewTab } from '../views/OverviewTab'
import { WalkthroughTab } from '../views/WalkthroughTab'

const TAB_META: Record<Tab, { label: string; icon: ReactNode }> = {
  overview: { label: 'Overview', icon: <LayoutDashboard className="size-4" /> },
  findings: { label: 'Findings', icon: <ShieldCheck className="size-4" /> },
  memory: { label: 'Memory', icon: <Database className="size-4" /> },
  walkthrough: { label: 'Walkthrough', icon: <Clapperboard className="size-4" /> },
}

export function DashboardPage({ id, tab }: { id: string; tab: Tab }) {
  const state = useRepository(id)
  const [target, setTarget] = useState<SourceTarget | null>(null)
  const [reanalyzing, setReanalyzing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const { status } = state

  const reanalyze = useCallback(async () => {
    setReanalyzing(true)
    setActionError(null)
    try {
      await state.reanalyze()
      window.location.hash = routeHref({ page: 'repo', id, tab: 'overview' })
    } catch (err) {
      setActionError(errorMessage(err))
    } finally {
      setReanalyzing(false)
    }
  }, [state, id])

  const closeViewer = useCallback(() => setTarget(null), [])
  const findingsCount = status?.metrics.total_findings
  const stale = status?.metrics.stale_findings ?? 0
  const memoryReady = Boolean(status && status.metrics.memory_entries > 0 && status.stage !== 'indexing' && status.stage !== 'scanning')

  if (state.notFound) {
    return (
      <div className="mx-auto max-w-lg pt-32">
        <EmptyState
          icon={<TriangleAlert className="size-5" />}
          title="Repository not found"
          message="This analysis does not exist on the connected backend (it may have been created with a different data directory)."
          action={<a href="#/" className="text-sm text-brand-300 hover:underline">Start a new analysis</a>}
        />
      </div>
    )
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-white/5 bg-ink-950/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3">
          <Logo />
          <nav aria-label="Dashboard sections" className="order-3 flex w-full gap-1 overflow-x-auto md:order-none md:w-auto">
            {(Object.keys(TAB_META) as Tab[]).map((key) => (
              <a
                key={key}
                href={routeHref({ page: 'repo', id, tab: key })}
                aria-current={tab === key ? 'page' : undefined}
                className={cn(
                  'inline-flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition',
                  tab === key ? 'bg-white/[0.08] text-white' : 'text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-200',
                )}
              >
                {TAB_META[key].icon}
                {TAB_META[key].label}
                {key === 'findings' && findingsCount != null && (
                  <span className={cn('rounded-md px-1.5 font-mono text-[11px]', stale ? 'bg-amber-400/15 text-amber-200' : 'bg-white/10 text-zinc-300')}>
                    {findingsCount}
                  </span>
                )}
              </a>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {status && (
              <div className="hidden min-w-0 text-right sm:block">
                <p className="max-w-56 truncate text-sm font-medium text-zinc-100">{status.name}</p>
                <p className="font-mono text-[11px] text-zinc-500">
                  {status.source} · analysis #{Math.max(1, status.analysis_count)}
                </p>
              </div>
            )}
            <ModeBadge llm={status?.llm} />
            <a href="#/" className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm text-zinc-200 hover:bg-white/[0.08]">
              <Plus className="size-4" aria-hidden /> New
            </a>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] space-y-5 px-5 py-6">
        {state.error && <ErrorBanner title="Lost connection to the backend" message={state.error} />}
        {actionError && <ErrorBanner title="Action failed" message={actionError} />}
        {status?.status === 'completed' && stale > 0 && tab !== 'findings' && (
          <ErrorBanner
            title={`${stale} finding${stale === 1 ? '' : 's'} are stale`}
            message="Source changed after analysis."
            action={<Button size="sm" onClick={() => void reanalyze()} loading={reanalyzing}>Re-run analysis</Button>}
          />
        )}

        <div role="tabpanel" aria-label={TAB_META[tab].label}>
          {tab === 'overview' && (
            <OverviewTab repoId={id} state={state} onOpen={setTarget} onReanalyze={() => void reanalyze()} reanalyzing={reanalyzing} />
          )}
          {tab === 'findings' && <FindingsTab state={state} onOpen={setTarget} onReanalyze={() => void reanalyze()} reanalyzing={reanalyzing} />}
          {tab === 'memory' && <MemoryTab repoId={id} ready={memoryReady} onOpen={setTarget} />}
          {tab === 'walkthrough' && <WalkthroughTab repoId={id} state={state} onOpen={setTarget} />}
        </div>
      </main>

      <SourceViewer repoId={id} target={target} onClose={closeViewer} onSourceChanged={state.refreshFindings} />
    </div>
  )
}
