import type { BrainState, RepositoryStatus } from '../types'

export const PIPELINE_STEPS = [
  { key: 'repository', label: 'Repository' },
  { key: 'scanner', label: 'Scanner' },
  { key: 'memory', label: 'Memory' },
  { key: 'brain', label: 'Brain' },
  { key: 'agents', label: 'Agents' },
  { key: 'crossval', label: 'Cross-validation' },
  { key: 'verify', label: 'Verification' },
  { key: 'intelligence', label: 'Intelligence' },
] as const

const STAGE_TO_STEP: Record<string, number> = {
  queued: 0,
  downloading: 0,
  extracting: 0,
  scanning: 1,
  indexing: 2,
  understanding: 3,
  retrieving: 3,
  routing: 3,
  running: 4,
  aggregating: 5,
  cross_validating: 5,
  verifying: 6,
  memorizing: 7,
  completed: 8,
}

/** Index of the active pipeline step (8 = everything complete). */
export function activeStep(status: RepositoryStatus | null): number {
  if (!status) return 0
  if (status.status === 'completed') return PIPELINE_STEPS.length
  return STAGE_TO_STEP[status.stage ?? 'queued'] ?? 0
}

/** The Brain checklist shown under the animated Brain. */
export const BRAIN_STEPS = [
  { label: 'Understanding repository', stages: ['understanding'] },
  { label: 'Retrieving memory', stages: ['retrieving'] },
  { label: 'Routing agents', stages: ['routing'] },
  { label: 'Running specialist agents', stages: ['running'] },
  { label: 'Cross-validating', stages: ['aggregating', 'cross_validating'] },
  { label: 'Verifying sources', stages: ['verifying'] },
  { label: 'Updating memory', stages: ['memorizing'] },
] as const

const BRAIN_ORDER = ['understanding', 'retrieving', 'routing', 'running', 'aggregating', 'cross_validating', 'verifying', 'memorizing', 'completed']

export function brainStepState(stepStages: readonly string[], status: RepositoryStatus | null): 'done' | 'active' | 'pending' {
  if (!status) return 'pending'
  if (status.status === 'completed') return 'done'
  const current = BRAIN_ORDER.indexOf(status.stage ?? '')
  if (current < 0) return 'pending'
  const indices = stepStages.map((stage) => BRAIN_ORDER.indexOf(stage))
  if (indices.includes(current)) return 'active'
  return Math.max(...indices) < current ? 'done' : 'pending'
}

export function brainHeadline(state: BrainState | undefined): string {
  switch (state) {
    case 'complete':
      return 'Brain complete'
    case 'error':
      return 'Brain error'
    case 'thinking':
    case 'retrieving':
    case 'routing':
    case 'running':
    case 'verifying':
      return 'Brain active'
    default:
      return 'Brain idle'
  }
}

export function isBrainActive(state: BrainState | undefined): boolean {
  return state === 'thinking' || state === 'retrieving' || state === 'routing' || state === 'running' || state === 'verifying'
}
