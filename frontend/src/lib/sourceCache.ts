import { api } from '../api'
import type { SourceFile } from '../types'

// Small in-memory cache so walkthrough previews and the source viewer do not
// refetch the same file repeatedly. Invalidated after a simulated edit.
const cache = new Map<string, Promise<SourceFile>>()

export function loadSource(repoId: string, path: string, fresh = false): Promise<SourceFile> {
  const key = `${repoId}:${path}`
  if (fresh) cache.delete(key)
  let entry = cache.get(key)
  if (!entry) {
    entry = api.source(repoId, path)
    entry.catch(() => cache.delete(key))
    cache.set(key, entry)
  }
  return entry
}

export function invalidateSource(repoId: string, path?: string): void {
  for (const key of [...cache.keys()]) {
    if (key.startsWith(`${repoId}:`) && (!path || key === `${repoId}:${path}`)) cache.delete(key)
  }
}
