import { AnimatePresence, motion } from 'framer-motion'
import { BadgeCheck, Brain, CornerDownLeft, Database, Search, Sparkles, TriangleAlert } from 'lucide-react'
import { useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { api, errorMessage } from '../api'
import { ModeBadge, VerificationBadge } from '../components/badges'
import { RichText } from '../components/RichText'
import { Chip, ErrorBanner, Panel, PanelHeader, Skeleton } from '../components/ui'
import { cn, location } from '../lib/format'
import { highlightLine } from '../lib/highlight'
import type { MemoryCitation, MemorySearchResponse, SourceTarget } from '../types'

const SUGGESTIONS = [
  'Where is authentication implemented?',
  'API_KEY',
  'How are payments processed?',
  'What security issues exist?',
  'Is email validation already implemented?',
  'What legacy code still exists?',
]

function languageFor(path: string | null): string {
  if (!path) return 'text'
  if (path.endsWith('.py')) return 'python'
  if (path.endsWith('.md')) return 'markdown'
  if (path.endsWith('.json')) return 'json'
  return 'javascript'
}

const SOURCE_MEMORY_TYPES = new Set(['symbol', 'code', 'doc', 'config'])

function CitationCard({ citation, index, onOpen }: { citation: MemoryCitation; index: number; onOpen: (target: SourceTarget) => void }) {
  const clickable = Boolean(citation.file)
  const lines = citation.snippet.split('\n').slice(0, 8)
  const showCode = Boolean(citation.file && citation.line_start && SOURCE_MEMORY_TYPES.has(citation.memory_type))
  return (
    <article className={cn('panel overflow-hidden', citation.cited_by_answer && 'border-brand-400/30')}>
      <button
        type="button"
        disabled={!clickable}
        onClick={() =>
          citation.file &&
          onOpen({
            path: citation.file,
            lineStart: citation.line_start ?? undefined,
            lineEnd: citation.line_end ?? undefined,
            title: citation.symbol ?? `${citation.memory_type} memory`,
            verification: { status: citation.verification.status, message: citation.verification.message },
          })
        }
        className="flex w-full flex-wrap items-center justify-between gap-2 border-b border-white/5 px-4 py-2.5 text-left enabled:hover:bg-white/[0.03]"
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="font-mono text-[11px] text-zinc-500">[{index + 1}]</span>
          <span className="truncate font-mono text-xs text-brand-200">
            {citation.file ? location(citation.file, citation.line_start, citation.line_end) : 'Brain insight'}
          </span>
          {citation.symbol && citation.memory_type === 'symbol' && <span className="font-mono text-xs text-sky-300">{citation.symbol}</span>}
        </span>
        <span className="flex items-center gap-1.5">
          <Chip>{citation.memory_type}</Chip>
          <VerificationBadge status={citation.verification.status} />
        </span>
      </button>
      {showCode ? (
        <pre className="overflow-x-auto bg-[#070a10] py-2 font-mono text-[12px] leading-5">
          {lines.map((line, offset) => (
            <div key={offset} className="grid grid-cols-[3rem_minmax(0,1fr)] pr-3">
              <span className="select-none pr-3 text-right text-zinc-600">{(citation.line_start ?? 1) + offset}</span>
              <code className="whitespace-pre text-zinc-300">{highlightLine(line, languageFor(citation.file))}</code>
            </div>
          ))}
          {(citation.snippet_truncated || citation.snippet.split('\n').length > 8) && <div className="pl-12 text-zinc-600">…</div>}
        </pre>
      ) : (
        <p className="line-clamp-4 px-4 py-3 text-sm leading-relaxed text-zinc-300" title={citation.snippet}>{citation.snippet}</p>
      )}
    </article>
  )
}

export function MemoryTab({ repoId, ready, onOpen }: { repoId: string; ready: boolean; onOpen: (target: SourceTarget) => void }) {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<MemorySearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  async function ask(question: string) {
    const trimmed = question.trim()
    if (!trimmed) return
    setQuery(trimmed)
    setLoading(true)
    setError(null)
    try {
      setResult(await api.search(repoId, trimmed))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void ask(query)
  }

  const allVerified = result && result.verification.total > 0 && result.verification.verified === result.verification.total

  return (
    <div className="space-y-6">
      <Panel className="p-6">
        <div className="mb-4 flex items-center gap-2">
          <Database className="size-4 text-brand-300" aria-hidden />
          <h1 className="text-lg font-semibold text-zinc-50">Ask your codebase anything</h1>
        </div>
        <form onSubmit={submit} className="relative">
          <label htmlFor="memory-query" className="sr-only">Question for the codebase memory</label>
          <Search className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-zinc-500" aria-hidden />
          <input
            id="memory-query"
            ref={input}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={ready ? 'Where is authentication implemented?' : 'Memory becomes available once indexing finishes…'}
            disabled={!ready}
            className="h-14 w-full rounded-2xl border border-white/10 bg-ink-900 pl-12 pr-28 text-base text-zinc-100 placeholder:text-zinc-600 outline-none transition focus:border-brand-400/60 focus:ring-4 focus:ring-brand-500/10"
          />
          <button
            type="submit"
            disabled={!ready || loading || !query.trim()}
            className="absolute right-2 top-1/2 inline-flex h-10 -translate-y-1/2 items-center gap-1.5 rounded-xl bg-brand-500 px-4 text-sm font-medium text-white hover:bg-brand-400 disabled:opacity-40"
          >
            Ask <CornerDownLeft className="size-3.5" aria-hidden />
          </button>
        </form>
        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              disabled={!ready || loading}
              onClick={() => void ask(suggestion)}
              className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs text-zinc-400 transition hover:border-brand-400/40 hover:text-zinc-100 disabled:opacity-40"
            >
              {suggestion}
            </button>
          ))}
        </div>
      </Panel>

      {error && <ErrorBanner title="Memory search failed" message={error} />}
      {loading && (
        <div className="space-y-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-24" />
        </div>
      )}

      <AnimatePresence mode="wait">
        {result && !loading && (
          <motion.div key={result.query} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
            <div className="space-y-4">
              <Panel>
                <PanelHeader
                  icon={<Brain className="size-4" />}
                  title="Brain answer"
                  subtitle={result.mode === 'llm' ? 'LLM answer, allowed to cite retrieved sources only' : 'Local retrieval answer: extracted from indexed memory, no LLM'}
                  actions={<ModeBadge llm={result.llm} mode={result.mode} />}
                />
                <div className="space-y-4 p-5">
                  <RichText text={result.answer} onOpen={onOpen} />
                  <div
                    className={cn(
                      'flex items-center gap-2 rounded-lg border px-3 py-2 text-xs',
                      allVerified ? 'border-emerald-400/20 bg-emerald-400/[0.05] text-emerald-200' : 'border-amber-400/20 bg-amber-400/[0.05] text-amber-200',
                    )}
                  >
                    {allVerified ? <BadgeCheck className="size-4" aria-hidden /> : <TriangleAlert className="size-4" aria-hidden />}
                    {result.verification.verified}/{result.verification.total} cited sources verified against the current source (SHA-256)
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500">
                    <Sparkles className="size-3" aria-hidden /> intent: {result.intent} · search terms:
                    {result.terms.slice(0, 10).map((term) => (
                      <Chip key={term} className="font-mono">{term}</Chip>
                    ))}
                  </div>
                  {result.notes.map((note) => (
                    <p key={note} className="text-xs text-amber-300/80">{note}</p>
                  ))}
                </div>
              </Panel>
              {result.related.length > 0 && (
                <div className="space-y-2">
                  <h2 className="eyebrow">Related memory (findings & insights)</h2>
                  {result.related.map((citation, index) => (
                    <CitationCard key={citation.memory_id} citation={citation} index={result.citations.length + index} onOpen={onOpen} />
                  ))}
                </div>
              )}
            </div>
            <div className="space-y-2">
              <h2 className="eyebrow">Sources ({result.citations.length})</h2>
              {result.citations.length === 0 && <p className="text-sm text-zinc-500">No source passages matched.</p>}
              {result.citations.map((citation, index) => (
                <CitationCard key={citation.memory_id} citation={citation} index={index} onOpen={onOpen} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
