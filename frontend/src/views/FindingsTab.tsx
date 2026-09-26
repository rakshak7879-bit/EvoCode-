import { BadgeCheck, CircleCheck, RefreshCw, SearchX, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useMemo, useState } from 'react'

import { FindingCard } from '../components/FindingCard'
import { Button, EmptyState, ErrorBanner, Skeleton } from '../components/ui'
import type { RepositoryState } from '../hooks/useRepository'
import { cn, location, SEVERITY_ORDER, timeAgo } from '../lib/format'
import type { Finding, Severity, SourceTarget } from '../types'

type CategoryFilter = 'all' | 'security' | 'duplicate'
type StatusFilter = 'all' | 'verified' | 'stale'

function FilterButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'rounded-lg border px-3 py-1.5 text-xs font-medium transition',
        active ? 'border-brand-400/50 bg-brand-500/15 text-brand-100' : 'border-white/10 bg-white/[0.02] text-zinc-400 hover:text-zinc-200',
      )}
    >
      {children}
    </button>
  )
}

interface FindingsTabProps {
  state: RepositoryState
  onOpen: (target: SourceTarget) => void
  onReanalyze: () => void
  reanalyzing: boolean
}

export function FindingsTab({ state, onOpen, onReanalyze, reanalyzing }: FindingsTabProps) {
  const { findings, status, findingsError } = state
  const [category, setCategory] = useState<CategoryFilter>('all')
  const [severity, setSeverity] = useState<Severity | 'all'>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [verifying, setVerifying] = useState(false)

  const all = useMemo<Finding[]>(() => (findings ? [...findings.security, ...findings.duplicates] : []), [findings])
  const visible = all
    .filter((f) => category === 'all' || f.category === category)
    .filter((f) => severity === 'all' || f.severity === severity)
    .filter((f) => statusFilter === 'all' || (statusFilter === 'verified' ? f.status === 'verified' : f.status !== 'verified'))
    .sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || a.file.localeCompare(b.file) || a.line - b.line)

  if (status && status.status !== 'completed') {
    return (
      <EmptyState
        icon={<ShieldCheck className="size-5" />}
        title={status.status === 'failed' ? 'Analysis failed' : 'The Brain is still analyzing'}
        message={status.status === 'failed' ? status.error : 'Findings appear here after cross-validation and source verification.'}
      />
    )
  }
  if (findingsError) return <ErrorBanner title="Could not load findings" message={findingsError} />
  if (!findings) return <div className="grid gap-4 lg:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-64" />)}</div>

  const stale = findings.verification.stale + findings.verification.missing + findings.verification.other
  const resolved = findings.history.resolved ?? []

  async function reverify() {
    setVerifying(true)
    await state.refreshFindings()
    setVerifying(false)
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-zinc-50">Verified findings</h1>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-zinc-400">
            <BadgeCheck className="size-4 text-emerald-400" aria-hidden />
            {findings.verification.verified}/{findings.verification.total} verified against the current source · checked{' '}
            {timeAgo(findings.verification.checked_at)}
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={reverify} loading={verifying} icon={<RefreshCw className="size-4" />}>Re-verify sources</Button>
          <Button variant={stale ? 'primary' : 'secondary'} onClick={onReanalyze} loading={reanalyzing}>Re-run analysis</Button>
        </div>
      </div>

      {stale > 0 && (
        <ErrorBanner
          title={`${stale} finding${stale === 1 ? '' : 's'} went stale`}
          message="The cited source changed after the analysis. Evo Code no longer vouches for these citations. Re-run the analysis to refresh them."
        />
      )}

      {resolved.length > 0 && (
        <div className="panel flex flex-wrap items-center gap-3 border-emerald-400/20 px-4 py-3 text-sm">
          <CircleCheck className="size-4 text-emerald-300" aria-hidden />
          <span className="text-emerald-100">Memory: resolved since the previous analysis:</span>
          {resolved.map((item) => (
            <span key={`${item.file}:${item.title}`} className="rounded-md border border-emerald-400/20 bg-emerald-400/10 px-2 py-0.5 text-xs text-emerald-200 line-through decoration-emerald-300/60">
              {item.title} ({location(item.file, item.line)})
            </span>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter findings">
        <FilterButton active={category === 'all'} onClick={() => setCategory('all')}>{`All (${all.length})`}</FilterButton>
        <FilterButton active={category === 'security'} onClick={() => setCategory('security')}>{`Security (${findings.security.length})`}</FilterButton>
        <FilterButton active={category === 'duplicate'} onClick={() => setCategory('duplicate')}>{`Duplicates (${findings.duplicates.length})`}</FilterButton>
        <span className="mx-1 h-5 w-px bg-white/10" aria-hidden />
        {(['all', 'critical', 'high', 'medium', 'low'] as const).map((level) => (
          <FilterButton key={level} active={severity === level} onClick={() => setSeverity(level)}>
            {level === 'all' ? 'Any severity' : level}
          </FilterButton>
        ))}
        <span className="mx-1 h-5 w-px bg-white/10" aria-hidden />
        <FilterButton active={statusFilter === 'all'} onClick={() => setStatusFilter('all')}>Any status</FilterButton>
        <FilterButton active={statusFilter === 'verified'} onClick={() => setStatusFilter('verified')}>Verified</FilterButton>
        <FilterButton active={statusFilter === 'stale'} onClick={() => setStatusFilter('stale')}>Stale</FilterButton>
      </div>

      {visible.length === 0 ? (
        <EmptyState icon={<SearchX className="size-5" />} title="No findings match these filters" />
      ) : (
        <div className="gap-4 lg:columns-2">
          {visible.map((finding) => (
            <div key={finding.id} className="mb-4 break-inside-avoid">
              <FindingCard finding={finding} onOpen={onOpen} />
            </div>
          ))}
        </div>
      )}

      {stale > 0 && (
        <p className="flex items-center gap-2 text-xs text-amber-300/80">
          <TriangleAlert className="size-3.5" aria-hidden /> Stale findings keep their original evidence hash so you can see exactly what changed.
        </p>
      )}
    </div>
  )
}
