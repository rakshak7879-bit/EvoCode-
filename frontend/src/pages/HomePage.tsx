import { motion } from 'framer-motion'
import { ArrowRight, BadgeCheck, Brain, FileArchive, FlaskConical, GitBranch, Lock, Recycle, ShieldAlert, Upload, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { DragEvent, FormEvent, ReactNode } from 'react'

import { api, errorMessage } from '../api'
import { ModeBadge } from '../components/badges'
import { Logo } from '../components/Logo'
import { PipelineFlow } from '../components/PipelineFlow'
import { Button, ErrorBanner } from '../components/ui'
import { useHealth } from '../hooks/useHealth'
import { navigate, routeHref } from '../hooks/useHashRoute'
import { cn, formatBytes, timeAgo } from '../lib/format'
import type { RepositorySummary } from '../types'

const DEFAULT_TASK = 'Analyze this repository for security issues, duplicate logic, architecture and generate a walkthrough.'

const FEATURES: Array<{ icon: ReactNode; title: string; text: string }> = [
  { icon: <Brain className="size-5" />, title: 'Persistent Memory', text: 'SQLite FTS5 memory of code, docs, findings and history.' },
  { icon: <ShieldAlert className="size-5" />, title: 'Security Analysis', text: 'Secrets, injection, JWT, CORS and XSS with line evidence.' },
  { icon: <Recycle className="size-5" />, title: 'Duplicate Detection', text: 'Finds repeated logic and the copy that already exists.' },
  { icon: <BadgeCheck className="size-5" />, title: 'Source Verification', text: 'Every citation re-checked against SHA-256 of the source.' },
]

export function HomePage() {
  const { health, error: healthError } = useHealth()
  const [githubUrl, setGithubUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [task, setTask] = useState('')
  const [dragging, setDragging] = useState(false)
  const [submitting, setSubmitting] = useState<'analyze' | 'demo' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [recent, setRecent] = useState<RepositorySummary[]>([])
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.repositories().then(setRecent).catch(() => setRecent([]))
  }, [])

  function pickFile(candidate: File | undefined | null) {
    if (!candidate) return
    if (!candidate.name.toLowerCase().endsWith('.zip')) {
      setError('Please choose a .zip archive of the repository.')
      return
    }
    setError(null)
    setFile(candidate)
    setGithubUrl('')
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault()
    setDragging(false)
    pickFile(event.dataTransfer.files?.[0])
  }

  async function start(kind: 'analyze' | 'demo') {
    setSubmitting(kind)
    setError(null)
    try {
      const response =
        kind === 'demo'
          ? await api.analyze({ demo: true, task })
          : await api.analyze({ file, githubUrl: file ? undefined : githubUrl.trim(), task })
      navigate({ page: 'repo', id: response.repository_id, tab: 'overview' })
    } catch (err) {
      setError(errorMessage(err))
      setSubmitting(null)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (file || githubUrl.trim()) void start('analyze')
  }

  const canAnalyze = Boolean(file || githubUrl.trim())

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-5">
      <header className="flex items-center justify-between py-5">
        <Logo />
        <div className="flex items-center gap-3 text-xs text-zinc-500">
          {health ? (
            <>
              <span className="hidden items-center gap-1.5 sm:inline-flex">
                <span className="size-1.5 rounded-full bg-emerald-400" aria-hidden /> Backend online
              </span>
              <ModeBadge llm={health.llm} />
            </>
          ) : healthError ? (
            <span className="inline-flex items-center gap-1.5 text-amber-300">
              <span className="size-1.5 rounded-full bg-amber-400" aria-hidden /> Backend offline
            </span>
          ) : null}
        </div>
      </header>

      <main className="flex flex-1 flex-col items-center pb-16 pt-10 md:pt-16">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }} className="text-center">
          <p className="font-mono text-xs font-bold tracking-[0.4em] text-brand-300">EVO CODE</p>
          <h1 className="text-gradient mt-4 text-5xl font-semibold tracking-tight md:text-7xl">Your AI Code Brain.</h1>
          <div className="mx-auto mt-6 max-w-xl space-y-1 text-lg text-zinc-400">
            <p>Understand your codebase.</p>
            <p>Remember engineering context.</p>
            <p className="text-zinc-200">Verify every important conclusion.</p>
          </div>
        </motion.div>

        {healthError && (
          <div className="mt-8 w-full max-w-2xl">
            <ErrorBanner title="The backend is not reachable" message="Start it with: cd backend && uvicorn main:app --reload" />
          </div>
        )}

        <motion.form
          onSubmit={submit}
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.1 }}
          className="panel mt-10 w-full max-w-2xl space-y-4 p-5 md:p-6"
        >
          <label htmlFor="github-url" className="eyebrow block">GitHub repository URL</label>
          <div className="relative">
            <GitBranch className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-zinc-500" aria-hidden />
            <input
              id="github-url"
              type="url"
              inputMode="url"
              placeholder="https://github.com/owner/repository"
              value={githubUrl}
              onChange={(event) => {
                setGithubUrl(event.target.value)
                if (event.target.value) setFile(null)
              }}
              className="h-12 w-full rounded-xl border border-white/10 bg-ink-900 pl-10 pr-3 text-sm text-zinc-100 placeholder:text-zinc-600 outline-none transition focus:border-brand-400/60 focus:ring-4 focus:ring-brand-500/10"
            />
          </div>

          <div className="flex items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.2em] text-zinc-600">
            <span className="h-px flex-1 bg-white/10" /> or <span className="h-px flex-1 bg-white/10" />
          </div>

          <label
            htmlFor="zip-input"
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={cn(
              'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-7 text-center transition',
              dragging ? 'border-brand-400 bg-brand-500/10' : 'border-white/15 bg-white/[0.02] hover:border-white/25',
            )}
          >
            {file ? (
              <span className="flex items-center gap-3 text-sm text-zinc-200">
                <FileArchive className="size-5 text-brand-300" aria-hidden />
                <span className="font-mono">{file.name}</span>
                <span className="text-zinc-500">{formatBytes(file.size)}</span>
                <button
                  type="button"
                  onClick={(event) => {
                    event.preventDefault()
                    setFile(null)
                    if (fileInput.current) fileInput.current.value = ''
                  }}
                  className="rounded-md p-1 text-zinc-400 hover:bg-white/10 hover:text-white"
                  aria-label="Remove selected file"
                >
                  <X className="size-4" />
                </button>
              </span>
            ) : (
              <>
                <Upload className="size-5 text-zinc-400" aria-hidden />
                <span className="text-sm text-zinc-300">Drop a repository ZIP here, or click to browse</span>
                <span className="text-xs text-zinc-600">Up to {health?.limits.max_upload_mb ?? 50} MB · extracted safely · code is never executed</span>
              </>
            )}
            <input
              id="zip-input"
              ref={fileInput}
              type="file"
              accept=".zip,application/zip"
              className="sr-only"
              onChange={(event) => pickFile(event.target.files?.[0])}
            />
          </label>

          <details className="group rounded-xl border border-white/5 bg-white/[0.015] px-3 py-2">
            <summary className="cursor-pointer text-xs text-zinc-400 marker:text-zinc-600">Brain task (optional)</summary>
            <label htmlFor="task" className="sr-only">Task for the Brain</label>
            <input
              id="task"
              value={task}
              onChange={(event) => setTask(event.target.value)}
              placeholder={DEFAULT_TASK}
              className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-ink-900 px-3 text-xs text-zinc-200 placeholder:text-zinc-600 outline-none focus:border-brand-400/60"
            />
            <p className="mt-1.5 text-[11px] text-zinc-600">The Brain routes agents based on this task, e.g. "Only run a security audit".</p>
          </details>

          {error && <ErrorBanner title="Could not start the analysis" message={error} />}

          <div className="flex flex-col gap-3 sm:flex-row">
            <Button type="submit" variant="primary" size="lg" className="flex-1" disabled={!canAnalyze} loading={submitting === 'analyze'}>
              Analyze Codebase <ArrowRight className="size-4" aria-hidden />
            </Button>
            <Button
              size="lg"
              onClick={() => void start('demo')}
              loading={submitting === 'demo'}
              disabled={health ? !health.demo_repo_available : false}
              icon={<FlaskConical className="size-4" />}
            >
              Try the demo repo
            </Button>
          </div>
        </motion.form>

        <div className="mt-10 grid w-full max-w-4xl gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((feature, index) => (
            <motion.div
              key={feature.title}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 + index * 0.06 }}
              className="panel p-4"
            >
              <span className="text-brand-300" aria-hidden>{feature.icon}</span>
              <h2 className="mt-3 text-sm font-semibold text-zinc-100">{feature.title}</h2>
              <p className="mt-1 text-xs leading-relaxed text-zinc-500">{feature.text}</p>
            </motion.div>
          ))}
        </div>

        <div className="mt-10 w-full max-w-4xl">
          <p className="eyebrow mb-3 text-center">How Evo Code thinks</p>
          <PipelineFlow active={-1} wrap />
        </div>

        {recent.length > 0 && (
          <section className="mt-12 w-full max-w-2xl" aria-labelledby="recent-heading">
            <h2 id="recent-heading" className="eyebrow mb-3">Recent analyses</h2>
            <ul className="panel divide-y divide-white/5">
              {recent.slice(0, 5).map((repo) => (
                <li key={repo.repository_id}>
                  <a href={routeHref({ page: 'repo', id: repo.repository_id, tab: 'overview' })} className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-white/[0.03]">
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-zinc-200">{repo.name}</span>
                      <span className="font-mono text-[11px] text-zinc-600">{repo.source} · {timeAgo(repo.created_at)}</span>
                    </span>
                    <span
                      className={cn(
                        'text-xs font-medium',
                        repo.status === 'completed' ? 'text-emerald-300' : repo.status === 'failed' ? 'text-amber-300' : 'text-brand-300',
                      )}
                    >
                      {repo.status}
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>

      <footer className="flex flex-wrap items-center justify-center gap-2 border-t border-white/5 py-5 text-[11px] text-zinc-600">
        <Lock className="size-3" aria-hidden /> Runs locally · uploaded repositories are treated as untrusted data and never executed
      </footer>
    </div>
  )
}
