import { BadgeCheck, ChevronRight, Clock, Compass, FileCode, Layers, Route, TriangleAlert } from 'lucide-react'

import { cn, location, titleCase } from '../lib/format'
import type { Architecture, Citation, SourceTarget, Verification } from '../types'
import { ModeBadge } from './badges'
import { Chip, Panel, PanelHeader } from './ui'

const LAYER_STYLE: Record<string, string> = {
  UI: 'border-sky-400/30 text-sky-200',
  'API call': 'border-cyan-400/30 text-cyan-200',
  Route: 'border-brand-400/30 text-brand-200',
  Auth: 'border-amber-400/30 text-amber-200',
  Middleware: 'border-amber-400/30 text-amber-200',
  Service: 'border-zinc-400/30 text-zinc-200',
  Data: 'border-emerald-400/30 text-emerald-200',
  Database: 'border-emerald-400/40 text-emerald-200',
  External: 'border-rose-400/30 text-rose-200',
}

function VerifiedTick({ verification }: { verification?: Verification }) {
  if (!verification) return null
  return verification.status === 'verified' ? (
    <BadgeCheck className="size-3 text-emerald-400" aria-label="verified" />
  ) : (
    <TriangleAlert className="size-3 text-amber-300" aria-label={verification.status} />
  )
}

function EvidenceLink({ citation, onOpen }: { citation: Citation; onOpen: (target: SourceTarget) => void }) {
  return (
    <button
      type="button"
      onClick={() =>
        onOpen({ path: citation.file, lineStart: citation.line_start, lineEnd: citation.line_end, title: citation.label ?? undefined })
      }
      className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.03] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300 hover:border-brand-400/40 hover:text-white"
      title={citation.label ?? undefined}
    >
      <VerifiedTick verification={citation.verification} />
      {location(citation.file, citation.line_start)}
    </button>
  )
}

export function ArchitecturePanel({ architecture, onOpen }: { architecture: Architecture; onOpen: (target: SourceTarget) => void }) {
  const stack = Object.entries(architecture.architecture ?? {})
  const flows = architecture.request_flows ?? []
  return (
    <Panel>
      <PanelHeader
        icon={<Compass className="size-4" />}
        title="Architecture · Explainer Agent"
        subtitle="What this repository does, with a citation behind every claim"
        actions={<ModeBadge mode={architecture.mode} />}
      />
      <div className="space-y-6 p-5">
        <div>
          <p className="text-[15px] leading-relaxed text-zinc-200">{architecture.summary}</p>
          <p className="mt-2 text-[11px] text-zinc-500">
            Source: {architecture.summary_source === 'llm' ? 'LLM summary of verified facts' : 'README + static analysis'}
          </p>
        </div>

        {stack.length > 0 && (
          <div>
            <h3 className="eyebrow mb-2 flex items-center gap-1.5"><Layers className="size-3" aria-hidden /> Stack</h3>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {stack.map(([layer, info]) => (
                <div key={layer} className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">{titleCase(layer)}</p>
                  <p className="mt-1 text-sm font-medium text-zinc-100">{info.value}</p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {info.evidence.slice(0, 3).map((citation) => (
                      <EvidenceLink key={`${citation.file}:${citation.line_start}`} citation={citation} onOpen={onOpen} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {flows.length > 0 && (
          <div>
            <h3 className="eyebrow mb-2 flex items-center gap-1.5"><Route className="size-3" aria-hidden /> Request flows (traced from code)</h3>
            <div className="space-y-3">
              {flows.map((flow) => (
                <div key={flow.trigger} className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
                  <p className="mb-2 flex items-center gap-2 text-sm font-medium text-zinc-100">
                    {flow.name}
                    <span className="font-mono text-[11px] text-zinc-500">{flow.trigger}</span>
                  </p>
                  <ol className="flex flex-wrap items-center gap-x-1 gap-y-2">
                    {flow.steps.map((step, index) => (
                      <li key={`${step.file}:${step.line}:${index}`} className="flex shrink-0 items-center gap-1">
                        {index > 0 && <ChevronRight className="size-3.5 text-zinc-600" aria-hidden />}
                        <button
                          type="button"
                          onClick={() => onOpen({ path: step.file, lineStart: step.line, title: `${step.layer}: ${step.label}` })}
                          className={cn(
                            'flex flex-col items-start rounded-lg border bg-ink-900 px-2.5 py-1.5 text-left hover:bg-white/[0.04]',
                            LAYER_STYLE[step.layer] ?? 'border-white/10 text-zinc-200',
                          )}
                        >
                          <span className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider">
                            {step.layer}
                            <VerifiedTick verification={step.verification} />
                          </span>
                          <span className="font-mono text-xs text-zinc-100">{step.label}</span>
                          <span className="font-mono text-[10px] text-zinc-500">{location(step.file, step.line)}</span>
                        </button>
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-2">
          {(architecture.important_files ?? []).length > 0 && (
            <div>
              <h3 className="eyebrow mb-2 flex items-center gap-1.5"><FileCode className="size-3" aria-hidden /> Important files</h3>
              <ul className="space-y-1.5">
                {(architecture.important_files ?? []).slice(0, 6).map((item) => (
                  <li key={item.file}>
                    <button
                      type="button"
                      onClick={() => onOpen({ path: item.file, title: item.reason })}
                      className="w-full rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2 text-left hover:border-white/15"
                    >
                      <p className="font-mono text-xs text-zinc-100">{item.file}</p>
                      <p className="text-[11px] text-zinc-500">{item.reason}</p>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(architecture.history ?? []).length > 0 && (
            <div>
              <h3 className="eyebrow mb-2 flex items-center gap-1.5"><Clock className="size-3" aria-hidden /> Engineering history (memory)</h3>
              <ul className="space-y-1.5">
                {(architecture.history ?? []).slice(0, 5).map((note) => (
                  <li key={`${note.source.file}:${note.source.line_start}`} className="rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2">
                    <p className="text-xs text-zinc-300">{note.note}</p>
                    <div className="mt-1.5 flex items-center gap-2">
                      <EvidenceLink citation={note.source} onOpen={onOpen} />
                      <Chip>{note.kind === 'code-marker' ? 'code marker' : 'documented'}</Chip>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </Panel>
  )
}
