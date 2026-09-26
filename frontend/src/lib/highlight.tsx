import type { ReactNode } from 'react'

// A tiny, dependency-free syntax highlighter for the source viewer. It works
// line by line (multi-line strings/comments are approximated) and returns React
// nodes, so repository content is never injected as HTML.

const KEYWORDS = new Set(
  (
    'async await break case catch class const continue default def delete do elif else except export extends ' +
    'false finally for from function if import in instanceof is lambda let new None not null of or pass raise ' +
    'require return self static super switch this throw True False true try typeof undefined var void while with yield'
  ).split(' '),
)
const HASH_COMMENT_LANGUAGES = new Set(['python', 'shell', 'yaml', 'toml', 'ruby', 'dockerfile', 'makefile', 'ini', 'dotenv'])

const SLASH_TOKENS =
  /(\/\/.*$|\/\*.*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_$][\w$]*)(?=\s*\()|([A-Za-z_$][\w$]*)/g
const HASH_TOKENS =
  /(#.*$)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][\w]*)(?=\s*\()|([A-Za-z_][\w]*)/g

export function highlightLine(line: string, language: string): ReactNode[] {
  if (language === 'markdown' || language === 'text') {
    if (/^\s{0,3}#{1,6}\s/.test(line)) return [<span key="h" className="font-semibold text-brand-300">{line}</span>]
    return inlineCode(line)
  }
  const pattern = HASH_COMMENT_LANGUAGES.has(language) ? HASH_TOKENS : SLASH_TOKENS
  pattern.lastIndex = 0
  const nodes: ReactNode[] = []
  let last = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(line)) !== null) {
    if (match[0] === '') {
      pattern.lastIndex += 1
      continue
    }
    if (match.index > last) nodes.push(line.slice(last, match.index))
    const [text, comment, string, number, call, identifier] = match
    const key = `${match.index}`
    if (comment) nodes.push(<span key={key} className="italic text-zinc-500">{text}</span>)
    else if (string) nodes.push(<span key={key} className="text-emerald-300">{text}</span>)
    else if (number) nodes.push(<span key={key} className="text-amber-300">{text}</span>)
    else if (call) nodes.push(<span key={key} className={KEYWORDS.has(call) ? 'text-brand-300' : 'text-sky-300'}>{text}</span>)
    else if (identifier && KEYWORDS.has(identifier)) nodes.push(<span key={key} className="text-brand-300">{text}</span>)
    else nodes.push(text)
    last = match.index + text.length
  }
  if (last < line.length) nodes.push(line.slice(last))
  return nodes
}

function inlineCode(line: string): ReactNode[] {
  return line.split(/(`[^`]+`)/g).map((part, index) =>
    part.startsWith('`') && part.endsWith('`') && part.length > 1 ? (
      <span key={index} className="text-emerald-300">{part}</span>
    ) : (
      part
    ),
  )
}
