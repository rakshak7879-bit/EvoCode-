import type { ReactNode } from 'react'

import type { SourceTarget } from '../types'

// Renders plain-text answers with inline `code` and clickable file:line references.
const TOKEN = /(`[^`]+`)|((?:[\w.-]+\/)*[\w.-]+\.(?:js|jsx|ts|tsx|mjs|cjs|py|go|rb|java|php|cs|rs|vue|svelte|md|json|ya?ml|toml))(?::(\d+)(?:-(\d+))?)?/g

const ROOT_FILES = /^(readme[\w.-]*\.md|package\.json|pyproject\.toml|requirements\.txt|dockerfile)$/i

function isLikelyPath(path: string): boolean {
  return path.includes('/') || ROOT_FILES.test(path)
}

function renderInline(text: string, onOpen: (target: SourceTarget) => void): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  let match: RegExpExecArray | null
  TOKEN.lastIndex = 0
  while ((match = TOKEN.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const [full, code, path, start, end] = match
    if (code) {
      nodes.push(
        <code key={match.index} className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[0.85em] text-emerald-200">
          {code.slice(1, -1)}
        </code>,
      )
    } else if (path && !isLikelyPath(path)) {
      nodes.push(full)
    } else if (path) {
      const lineStart = start ? Number(start) : undefined
      nodes.push(
        <button
          key={match.index}
          type="button"
          onClick={() => onOpen({ path, lineStart, lineEnd: end ? Number(end) : lineStart })}
          className="font-mono text-[0.9em] text-brand-300 underline decoration-brand-400/30 underline-offset-2 hover:text-brand-200"
        >
          {full}
        </button>,
      )
    }
    last = match.index + full.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

export function RichText({ text, onOpen }: { text: string; onOpen: (target: SourceTarget) => void }) {
  return (
    <div className="space-y-1.5 text-[15px] leading-relaxed text-zinc-200">
      {text.split('\n').map((line, index) => {
        if (!line.trim()) return <div key={index} className="h-2" aria-hidden />
        const bullet = line.match(/^\s*(•|-|\d+\.)\s+(.*)$/)
        if (bullet) {
          return (
            <div key={index} className="flex gap-2 pl-1">
              <span className="w-5 shrink-0 text-right font-mono text-sm text-zinc-500">{bullet[1]}</span>
              <span className="min-w-0">{renderInline(bullet[2], onOpen)}</span>
            </div>
          )
        }
        return <p key={index}>{renderInline(line, onOpen)}</p>
      })}
    </div>
  )
}
