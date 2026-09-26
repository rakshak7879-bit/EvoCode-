import type {
  AnalyzeResponse,
  FindingsResponse,
  Health,
  MemorySearchResponse,
  RepositoryStatus,
  RepositorySummary,
  SourceEdit,
  SourceFile,
  WalkthroughResponse,
} from './types'

const BASE = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')
const JSON_HEADERS = { 'Content-Type': 'application/json' }

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function describeDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === 'object' && 'msg' in item ? String(item.msg) : null))
      .filter(Boolean)
    return messages.length ? messages.join('; ') : null
  }
  return null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, init)
  } catch {
    throw new ApiError(0, 'Cannot reach the Evo Code backend. Is it running on http://127.0.0.1:8000?')
  }
  if (!response.ok) {
    let message = `Request failed (HTTP ${response.status})`
    try {
      const body: unknown = await response.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        message = describeDetail((body as { detail: unknown }).detail) ?? message
      }
    } catch {
      // Non-JSON error body: keep the generic message.
    }
    throw new ApiError(response.status, message)
  }
  return (await response.json()) as T
}

export interface AnalyzeInput {
  file?: File | null
  githubUrl?: string
  demo?: boolean
  task?: string
}

export const api = {
  health: () => request<Health>('/api/health'),
  repositories: () => request<RepositorySummary[]>('/api/repositories'),

  analyze(input: AnalyzeInput) {
    const form = new FormData()
    if (input.file) form.append('file', input.file)
    if (input.githubUrl) form.append('github_url', input.githubUrl)
    if (input.demo) form.append('use_demo', 'true')
    if (input.task?.trim()) form.append('task', input.task.trim())
    return request<AnalyzeResponse>('/api/repository/analyze', { method: 'POST', body: form })
  },

  status: (id: string) => request<RepositoryStatus>(`/api/repository/${id}`),
  reanalyze: (id: string) =>
    request<AnalyzeResponse>(`/api/repository/${id}/reanalyze`, { method: 'POST', headers: JSON_HEADERS, body: '{}' }),
  findings: (id: string) => request<FindingsResponse>(`/api/findings/${id}`),
  walkthrough: (id: string) => request<WalkthroughResponse>(`/api/walkthrough/${id}`),

  search: (id: string, query: string) =>
    request<MemorySearchResponse>('/api/memory/search', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ repository_id: id, query, limit: 6 }),
    }),

  source: (id: string, path: string) =>
    request<SourceFile>(`/api/source/${id}?path=${encodeURIComponent(path)}`),

  editSource: (id: string, path: string, line: number, content: string) =>
    request<SourceEdit>(`/api/source/${id}/edit`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ path, line, content }),
    }),
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return 'Something went wrong.'
}
