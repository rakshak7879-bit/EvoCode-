import type { Severity } from '../types'

export const SEVERITY_ORDER: Record<Severity, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 }

export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

export function shortHash(hash: string | null | undefined, length = 10): string {
  return hash ? hash.slice(0, length) : '—'
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return ''
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

export function formatSeconds(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = Math.max(0, Math.round(seconds % 60))
  return `${minutes}:${rest.toString().padStart(2, '0')}`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return ''
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export function location(file: string, line?: number | null, lineEnd?: number | null): string {
  if (!line) return file
  return lineEnd && lineEnd !== line ? `${file}:${line}-${lineEnd}` : `${file}:${line}`
}

export function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
