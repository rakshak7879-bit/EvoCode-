import { ArrowRight, BadgeCheck, Brain, Database, FileCode, RefreshCw, Recycle, ShieldAlert } from 'lucide-react'

import { AgentCard } from '../components/AgentCard'
import { ArchitecturePanel } from '../components/ArchitecturePanel'
import { BrainOrchestrator } from '../components/BrainOrchestrator'
import { CrossValidationPanel } from '../components/CrossValidationPanel'
import { FindingCard } from '../components/FindingCard'
import { MetricCard } from '../components/MetricCard'
import { PipelineFlow } from '../components/PipelineFlow'
import { Timeline } from '../components/Timeline'
import { Button, ErrorBanner, Panel, PanelHeader, Skeleton } from '../components/ui'
import type { RepositoryState } from '../hooks/useRepository'
import { routeHref } from '../hooks/useHashRoute'
import { SEVERITY_ORDER } from '../lib/format'
import { activeStep } from '../lib/stages'
import type { SourceTarget } from '../types'

interface OverviewProps {
  repoId: string
  state: RepositoryState
  onOpen: (target: SourceTarget) => void
  onReanalyze: () => void
  reanalyzing: boolean
}

function dash(value: number | null | undefined) {
  return value == null ? '—' : value
}

export function OverviewTab({ repoId, state, onOpen, onReanalyze, reanalyzing }: OverviewProps) {
  const { status, findings } = state
  const processing = status?.status === 'queued' || status?.status === 'processing'
  const metrics = status?.metrics
  const verified = metrics?.verified_findings
  const total = metrics?.total_findings
  const topFindings = findings
    ? [...findings.security].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]).slice(0, 2)
    : []

  return (
    <div className="space-y-6">
      {status?.status === 'failed' && (
        <ErrorBanner
          title="Analysis failed"
          message={status.error}
          action={<Button size="sm" onClick={onReanalyze} loading={reanalyzing} icon={<RefreshCw className="size-3.5" />}>Re-run</Button>}
        />
      )}

      <Panel className="p-4">
        <PipelineFlow active={activeStep(status)} failed={status?.status === 'failed'} />
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <Panel>
          <PanelHeader
            icon={<Brain className="size-4" />}
            title="Brain orchestration"
            subtitle="Central orchestrator: agents never talk to each other directly"
          />
          <div className="p-5">
            <BrainOrchestrator status={status} />
          </div>
        </Panel>
        <div className="grid content-start gap-3 sm:grid-cols-2">
          {(status?.agents ?? []).map((agent) => (
            <AgentCard key={agent.name} agent={agent} />
          ))}
          {!status && [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-36" />)}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <MetricCard label="Files analyzed" value={dash(metrics?.files_analyzed)} icon={<FileCode className="size-4" />}
          hint={status?.stats.total_lines ? `${status.stats.total_lines.toLocaleString()} lines` : undefined} />
        <MetricCard label="Security findings" value={dash(metrics?.security_findings)} tone="danger" icon={<ShieldAlert className="size-4" />}
          hint={processing ? 'Analyzing…' : 'with line-level evidence'} />
        <MetricCard label="Duplicate clusters" value={dash(metrics?.duplicate_clusters)} tone="warning" icon={<Recycle className="size-4" />}
          hint={processing ? 'Comparing functions…' : 'repeated implementations'} />
        <MetricCard label="Memory entries" value={dash(metrics?.memory_entries || null)} icon={<Database className="size-4" />}
          hint="SQLite FTS5 passages + insights" />
        <MetricCard
          label="Verified findings"
          value={verified == null || total == null ? '—' : `${verified}/${total}`}
          tone={metrics?.stale_findings ? 'warning' : 'success'}
          icon={<BadgeCheck className="size-4" />}
          hint={metrics?.stale_findings ? `${metrics.stale_findings} stale: source changed` : 'SHA-256 checked against source'}
        />
      </div>

      {status?.status === 'completed' && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          {findings ? (
            <ArchitecturePanel architecture={findings.architecture} onOpen={onOpen} />
          ) : (
            <Skeleton className="h-96" />
          )}
          <div className="space-y-6">
            <CrossValidationPanel crossValidation={status.report.cross_validation ?? {}} verification={status.report.verification} />
            {topFindings.length > 0 && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="eyebrow">Top verified findings</h2>
                  <a href={routeHref({ page: 'repo', id: repoId, tab: 'findings' })} className="inline-flex items-center gap-1 text-xs text-brand-300 hover:text-brand-200">
                    All findings <ArrowRight className="size-3" aria-hidden />
                  </a>
                </div>
                {topFindings.map((finding) => (
                  <FindingCard key={finding.id} finding={finding} onOpen={onOpen} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <Timeline entries={status?.timeline ?? []} live={processing} />
    </div>
  )
}
