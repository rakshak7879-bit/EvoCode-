import { AnimatePresence, motion } from 'framer-motion'
import { BadgeCheck, FileCode, Hash, Pencil, RotateCcw, TriangleAlert, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { api, errorMessage } from '../api'
import { cn, location, shortHash } from '../lib/format'
import { highlightLine } from '../lib/highlight'
import { invalidateSource, loadSource } from '../lib/sourceCache'
import type { FindingsResponse, SourceFile, SourceTarget, Verification } from '../types'
import { VerificationBadge } from './badges'
import { Button, ErrorBanner, Spinner } from './ui'

interface SourceViewerProps {
  repoId: string
  target: SourceTarget | null
  onClose: () => void
  onSourceChanged: () => Promise<FindingsResponse | null>
}

function findVerification(findings: FindingsResponse | null, id: string | undefined): Verification | undefined {
  if (!findings || !id) return undefined
  return [...findings.security, ...findings.duplicates].find((f) => f.id === id)?.verification
}

function HashRow({ label, value, match }: { label: string; value: string | null | undefined; match?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 font-mono text-[11px]">
      <span className="text-zinc-500">{label}</span>
      <span className={cn('truncate', match === false ? 'text-amber-300' : 'text-zinc-300')} title={value ?? undefined}>
        {shortHash(value, 16)}
        {value ? '…' : ''}
      </span>
    </div>
  )
}

export function SourceViewer({ repoId, target, onClose, onSourceChanged }: SourceViewerProps) {
  const [source, setSource] = useState<SourceFile | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [verification, setVerification] = useState<Verification | undefined>(target?.verification)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [editMessage, setEditMessage] = useState<string | null>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const highlighted = useRef<HTMLDivElement>(null)

  const lineStart = target?.lineStart
  const lineEnd = target?.lineEnd ?? target?.lineStart

  const load = useCallback(
    async (fresh: boolean) => {
      if (!target) return
      setError(null)
      try {
        const data = await loadSource(repoId, target.path, fresh)
        setSource(data)
        if (lineStart && lineStart <= data.line_count) setDraft(data.content.split('\n')[lineStart - 1] ?? '')
      } catch (err) {
        setError(errorMessage(err))
      }
    },
    [repoId, target, lineStart],
  )

  useEffect(() => {
    setSource(null)
    setEditMessage(null)
    setVerification(target?.verification)
    void load(false)
  }, [load, target])

  useEffect(() => {
    if (!target) return
    const previous = document.activeElement as HTMLElement | null
    closeButton.current?.focus()
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [target, onClose])

  useEffect(() => {
    highlighted.current?.scrollIntoView({ block: 'center' })
  }, [source])

  async function applyEdit(event: FormEvent) {
    event.preventDefault()
    if (!target || !lineStart) return
    setSaving(true)
    setEditMessage(null)
    try {
      const result = await api.editSource(repoId, target.path, lineStart, draft)
      invalidateSource(repoId, target.path)
      await load(true)
      const refreshed = await onSourceChanged()
      const updated = findVerification(refreshed, target.findingId)
      if (updated) setVerification(updated)
      setEditMessage(`Working copy updated · SHA-256 ${shortHash(result.sha256_before, 8)}… → ${shortHash(result.sha256_after, 8)}…`)
    } catch (err) {
      setEditMessage(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const lines = source ? source.content.split('\n') : []
  const fileMatches = source ? source.sha256_current === source.sha256_indexed : undefined

  return (
    <AnimatePresence>
      {target && (
        <motion.div
          className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-0 backdrop-blur-sm sm:items-center sm:p-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onMouseDown={(event) => event.target === event.currentTarget && onClose()}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="source-viewer-title"
            className="panel flex max-h-[92vh] w-full max-w-6xl flex-col overflow-hidden bg-ink-900 sm:rounded-2xl"
            initial={{ y: 24, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 24, opacity: 0 }}
            transition={{ type: 'spring', damping: 26, stiffness: 260 }}
          >
            <header className="flex items-center justify-between gap-3 border-b border-white/5 px-5 py-3">
              <div className="flex min-w-0 items-center gap-3">
                <FileCode className="size-4 shrink-0 text-brand-300" aria-hidden />
                <div className="min-w-0">
                  <h2 id="source-viewer-title" className="truncate font-mono text-sm font-semibold text-zinc-100">
                    {location(target.path, lineStart, lineEnd)}
                  </h2>
                  {target.title && <p className="truncate text-xs text-zinc-500">{target.title}</p>}
                </div>
              </div>
              <button
                ref={closeButton}
                type="button"
                onClick={onClose}
                className="rounded-lg p-2 text-zinc-400 hover:bg-white/5 hover:text-white"
                aria-label="Close source viewer"
              >
                <X className="size-4" />
              </button>
            </header>

            <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_300px]">
              <div className="min-h-0 overflow-auto bg-[#070a10] py-3 font-mono text-[12.5px] leading-6">
                {error && <div className="p-4"><ErrorBanner title="Could not load source" message={error} /></div>}
                {!source && !error && <div className="p-6"><Spinner label="Reading source from the working copy…" /></div>}
                {source &&
                  lines.map((line, index) => {
                    const number = index + 1
                    const inRange = lineStart != null && number >= lineStart && number <= (lineEnd ?? lineStart)
                    return (
                      <div
                        key={number}
                        ref={inRange && number === lineStart ? highlighted : undefined}
                        className={cn('grid grid-cols-[3.5rem_minmax(0,1fr)] pr-4', inRange && 'bg-brand-500/15 shadow-[inset_3px_0_0_#a78bfa]')}
                      >
                        <span className={cn('select-none pr-4 text-right tabular-nums', inRange ? 'text-brand-200' : 'text-zinc-600')}>
                          {number}
                        </span>
                        <code className="whitespace-pre text-zinc-300">{highlightLine(line, source.language)}</code>
                      </div>
                    )
                  })}
              </div>

              <aside className="flex min-h-0 flex-col gap-4 overflow-y-auto border-t border-white/5 p-5 lg:border-l lg:border-t-0">
                <section aria-labelledby="verification-heading" className="space-y-3">
                  <h3 id="verification-heading" className="eyebrow">Source verification</h3>
                  {verification ? (
                    <div className="space-y-2">
                      <VerificationBadge status={verification.status} size="lg" />
                      {verification.message && <p className="text-xs text-zinc-400">{verification.message}</p>}
                    </div>
                  ) : source ? (
                    <div className="flex items-center gap-2 text-xs">
                      {fileMatches ? (
                        <BadgeCheck className="size-4 text-emerald-400" aria-hidden />
                      ) : (
                        <TriangleAlert className="size-4 text-amber-300" aria-hidden />
                      )}
                      <span className={fileMatches ? 'text-emerald-200' : 'text-amber-200'}>
                        {fileMatches ? 'File matches the indexed SHA-256' : 'File changed since it was indexed'}
                      </span>
                    </div>
                  ) : null}
                  {source && (
                    <div className="code-surface space-y-1.5 p-3">
                      <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                        <Hash className="size-3" aria-hidden /> SHA-256
                      </div>
                      <HashRow label="indexed" value={source.sha256_indexed} />
                      <HashRow label="current" value={source.sha256_current} match={fileMatches} />
                      {verification?.evidence_sha256_expected && (
                        <HashRow label="cited lines" value={verification.evidence_sha256_expected} match={!verification.lines_changed} />
                      )}
                    </div>
                  )}
                </section>

                {source?.editable && lineStart && lineStart <= (source?.line_count ?? 0) && (
                  <form onSubmit={applyEdit} className="space-y-2" aria-labelledby="simulate-heading">
                    <h3 id="simulate-heading" className="eyebrow flex items-center gap-1.5">
                      <Pencil className="size-3" aria-hidden /> Simulate a code change
                    </h3>
                    <p className="text-xs text-zinc-500">
                      Edit line {lineStart} to see stale-citation detection. Changes apply only to Evo Code's isolated working copy.
                    </p>
                    <label htmlFor="line-edit" className="sr-only">Replacement for line {lineStart}</label>
                    <input
                      id="line-edit"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      className="code-surface w-full px-3 py-2 font-mono text-xs text-zinc-100 outline-none focus:border-brand-400/60"
                      spellCheck={false}
                    />
                    <div className="flex gap-2">
                      <Button type="submit" size="sm" variant="danger" loading={saving} icon={<Pencil className="size-3.5" />}>
                        Apply edit
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => void load(true)} icon={<RotateCcw className="size-3.5" />}>
                        Reload
                      </Button>
                    </div>
                    {editMessage && <p className="font-mono text-[11px] text-zinc-400" aria-live="polite">{editMessage}</p>}
                  </form>
                )}

                {source && (
                  <dl className="mt-auto grid grid-cols-2 gap-2 text-[11px] text-zinc-500">
                    <dt>Language</dt>
                    <dd className="text-right text-zinc-300">{source.language}</dd>
                    <dt>Lines</dt>
                    <dd className="text-right text-zinc-300">{source.line_count}</dd>
                  </dl>
                )}
              </aside>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
