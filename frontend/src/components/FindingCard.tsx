import { motion } from 'framer-motion'
import { ArrowUpRight, Hash, Info, Lightbulb, Star } from 'lucide-react'

import { cn, location, shortHash } from '../lib/format'
import type { Finding, SourceTarget } from '../types'
import { SeverityBadge, VerificationBadge } from './badges'
import { Chip } from './ui'

export function FindingCard({ finding, onOpen }: { finding: Finding; onOpen: (target: SourceTarget) => void }) {
  const open = (file = finding.file, line = finding.line, lineEnd = finding.line_end) =>
    onOpen({ path: file, lineStart: line, lineEnd, title: finding.title, findingId: finding.id, verification: finding.verification })
  const stale = finding.status !== 'verified'
  const sources = finding.detectors.length ? finding.detectors : [finding.source]

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn('panel flex flex-col gap-3 p-5', stale && 'border-amber-400/30')}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <SeverityBadge severity={finding.severity} />
          <Chip>{finding.category === 'security' ? 'Security' : 'Duplicate'}</Chip>
          {finding.cwe && <Chip>{finding.cwe}</Chip>}
          {sources.map((source) => (
            <Chip key={source} className={source === 'llm' ? 'border-brand-400/30 text-brand-200' : undefined}>
              {source === 'llm' ? 'LLM' : source.replace('rule:', 'rule · ')}
            </Chip>
          ))}
        </div>
        <VerificationBadge status={finding.status} />
      </div>

      <div>
        <h3 className="text-base font-semibold text-zinc-50">{finding.title}</h3>
        <button
          type="button"
          onClick={() => open()}
          className="mt-1 inline-flex items-center gap-1 font-mono text-xs text-brand-300 hover:text-brand-200 hover:underline"
        >
          {location(finding.file, finding.line, finding.line_end)}
          <ArrowUpRight className="size-3" aria-hidden />
        </button>
      </div>

      <p className="text-sm leading-relaxed text-zinc-400">{finding.description}</p>

      {finding.evidence && (
        <pre className="code-surface overflow-x-auto px-3 py-2 font-mono text-xs text-zinc-200">
          <span className="select-none pr-3 text-zinc-600">{finding.line}</span>
          {finding.evidence}
        </pre>
      )}

      {finding.locations.length > 0 && (
        <div className="space-y-1.5">
          <p className="eyebrow">
            {finding.locations.length} locations{finding.similarity != null ? ` · ${Math.round(finding.similarity * 100)}% similar` : ''}
          </p>
          <ul className="space-y-1">
            {finding.locations.map((loc) => (
              <li key={`${loc.file}:${loc.line}`}>
                <button
                  type="button"
                  onClick={() => open(loc.file, loc.line, loc.line_end ?? loc.line)}
                  className="flex w-full items-center justify-between gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-1.5 text-left hover:border-white/15"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    {loc.role === 'canonical' ? (
                      <Star className="size-3.5 shrink-0 text-amber-300" aria-label="Existing shared implementation" />
                    ) : (
                      <span className="size-3.5 shrink-0" aria-hidden />
                    )}
                    <span className="truncate font-mono text-xs text-zinc-200">{location(loc.file, loc.line)}</span>
                    {loc.symbol && <span className="truncate font-mono text-xs text-sky-300">{loc.symbol}()</span>}
                    {loc.role === 'canonical' && <span className="text-[10px] font-semibold uppercase text-amber-200">already implemented</span>}
                  </span>
                  <VerificationBadge status={loc.verification.status} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {finding.annotations.length > 0 && (
        <ul className="space-y-1.5">
          {finding.annotations.map((note) => (
            <li key={note} className="flex gap-2 rounded-lg border border-sky-400/15 bg-sky-400/[0.05] px-3 py-2 text-xs text-sky-100/90">
              <Info className="mt-0.5 size-3.5 shrink-0 text-sky-300" aria-hidden />
              <span>{note}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="rounded-xl border border-emerald-400/15 bg-emerald-400/[0.05] px-3 py-2.5">
        <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-emerald-300">
          <Lightbulb className="size-3.5" aria-hidden /> Recommendation
        </p>
        <p className="mt-1 text-sm text-zinc-200">{finding.recommendation}</p>
      </div>

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-white/5 pt-3 font-mono text-[11px] text-zinc-500">
        <span className="inline-flex items-center gap-1" title={`Cited-lines SHA-256: ${finding.sha256}`}>
          <Hash className="size-3" aria-hidden />
          SHA-256 {shortHash(finding.sha256, 12)}…
        </span>
        <span>{stale ? finding.verification.message : `confidence ${Math.round(finding.confidence * 100)}%`}</span>
      </footer>
    </motion.article>
  )
}
