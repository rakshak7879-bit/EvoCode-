import { BadgeCheck, CircleX, GitCompare, GitMerge, Undo2 } from 'lucide-react'

import { location } from '../lib/format'
import type { CrossValidation, Report } from '../types'
import { Panel, PanelHeader } from './ui'

export function CrossValidationPanel({ crossValidation, verification }: { crossValidation: CrossValidation; verification?: Report['verification'] }) {
  const checks = crossValidation.checks ?? []
  const unsupported = crossValidation.unsupported ?? []
  const merged = crossValidation.merged ?? []
  const relocated = crossValidation.relocated ?? []
  return (
    <Panel>
      <PanelHeader
        icon={<GitCompare className="size-4" />}
        title="Cross-validation & verification"
        subtitle="The Brain checks every agent claim before it becomes a finding"
      />
      <div className="space-y-5 p-5">
        {verification && (
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.05] p-3">
              <p className="font-mono text-2xl font-semibold text-emerald-200 tabular-nums">
                {verification.findings_verified}/{verification.findings_total}
              </p>
              <p className="text-[11px] text-emerald-200/70">findings verified against source</p>
            </div>
            <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.05] p-3">
              <p className="font-mono text-2xl font-semibold text-emerald-200 tabular-nums">
                {verification.claims_verified}/{verification.claims_total}
              </p>
              <p className="text-[11px] text-emerald-200/70">architecture & walkthrough citations</p>
            </div>
          </div>
        )}
        <ul className="divide-y divide-white/5 rounded-xl border border-white/5">
          {checks.map((check) => (
            <li key={check.name} className="flex items-center justify-between px-3 py-2 text-sm">
              <span className="text-zinc-400">{check.name}</span>
              <span className="font-mono text-zinc-100 tabular-nums">
                {check.value}
                {check.total != null ? `/${check.total}` : ''}
              </span>
            </li>
          ))}
        </ul>
        {unsupported.length > 0 && (
          <div>
            <h3 className="eyebrow mb-2">Rejected claims</h3>
            <ul className="space-y-1.5">
              {unsupported.slice(0, 6).map((claim) => (
                <li key={`${claim.file}:${claim.line}:${claim.title}`} className="flex gap-2 rounded-lg border border-rose-400/15 bg-rose-400/[0.05] px-3 py-2 text-xs">
                  <CircleX className="mt-0.5 size-3.5 shrink-0 text-rose-300" aria-hidden />
                  <span className="text-rose-100/90">
                    <strong className="font-semibold">{claim.title}</strong> ({claim.source}) at {location(claim.file, claim.line)}: {claim.reason}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {merged.length > 0 && (
          <div>
            <h3 className="eyebrow mb-2">Merged detections</h3>
            <ul className="space-y-1.5">
              {merged.map((item) => (
                <li key={`${item.file}:${item.line}`} className="flex gap-2 text-xs text-zinc-400">
                  <GitMerge className="mt-0.5 size-3.5 shrink-0 text-brand-300" aria-hidden />
                  {item.title} · {location(item.file, item.line)} · {item.detectors.join(' + ')}
                </li>
              ))}
            </ul>
          </div>
        )}
        {relocated.length > 0 && (
          <div>
            <h3 className="eyebrow mb-2">Corrected citations</h3>
            <ul className="space-y-1.5">
              {relocated.map((item) => (
                <li key={`${item.file}:${item.from}`} className="flex gap-2 text-xs text-zinc-400">
                  <Undo2 className="mt-0.5 size-3.5 shrink-0 text-cyan-300" aria-hidden />
                  {item.title}: {item.file} line {item.from} → {item.to}
                </li>
              ))}
            </ul>
          </div>
        )}
        {unsupported.length === 0 && (
          <p className="flex items-center gap-2 text-xs text-zinc-500">
            <BadgeCheck className="size-3.5 text-emerald-400" aria-hidden />
            No unsupported claims: every accepted finding has evidence at its cited line.
          </p>
        )}
      </div>
    </Panel>
  )
}
