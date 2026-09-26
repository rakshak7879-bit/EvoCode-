// Types mirroring the FastAPI response models (see API.md).

export type AgentName = 'security' | 'duplicate' | 'explainer' | 'walkthrough'
export type AgentState = 'pending' | 'queued' | 'running' | 'complete' | 'failed' | 'skipped'
export type BrainState = 'idle' | 'thinking' | 'retrieving' | 'routing' | 'running' | 'verifying' | 'complete' | 'error'
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'
export type VerificationStatus =
  | 'verified'
  | 'stale'
  | 'missing'
  | 'invalid_line'
  | 'unsupported'
  | 'unsafe_path'
  | 'derived'

export interface LLMInfo {
  provider: string
  model: string
  available: boolean
  mode: 'llm' | 'local'
  label: string
}

export interface Health {
  status: string
  version: string
  llm: LLMInfo
  fts5: boolean
  database_recovered: boolean
  allow_source_edits: boolean
  demo_repo_available: boolean
  limits: Record<string, number>
}

export interface AnalyzeResponse {
  repository_id: string
  status: string
}

export interface AgentStatus {
  name: AgentName
  title: string
  description: string
  status: AgentState
  mode: string | null
  summary: string | null
  reason: string | null
  error: string | null
  duration_ms: number | null
  started_at: string | null
  completed_at: string | null
}

export interface TimelineEntry {
  ts: string
  elapsed_ms: number
  stage: string
  level: 'info' | 'success' | 'warning' | 'error'
  message: string
  agent?: string | null
}

export interface Metrics {
  files_analyzed: number
  memory_entries: number
  security_findings: number | null
  duplicate_clusters: number | null
  verified_findings: number | null
  stale_findings: number | null
  total_findings: number | null
}

export interface CrossValidation {
  candidates?: number
  accepted?: number
  merged?: Array<{ file: string; line: number; title: string; detectors: string[]; count: number }>
  conflicts?: Array<{ file: string; line: number; title: string; detail: string; resolution: string }>
  unsupported?: Array<{ agent: string; source: string; title: string; file: string; line: number; status: string; reason: string }>
  relocated?: Array<{ title: string; file: string; from: number; to: number }>
  annotations?: number
  checks?: Array<{ name: string; value: number; total?: number }>
}

export interface HistoryReport {
  analysis_no?: number
  previous_total?: number
  resolved?: Array<{ title: string; file: string; line: number; severity: Severity; category: string }>
  new?: Array<{ title: string; file: string; line: number }>
  unchanged?: number
}

export interface Report {
  task?: string
  plan?: {
    intents: string[]
    full_analysis: boolean
    agents: Array<{ name: string; reason: string }>
    skipped: Array<{ name: string; reason: string }>
  }
  context?: {
    files: number
    readme: string | null
    manifests: string[]
    memory_entries: number
    focus: Record<string, string[]>
  }
  cross_validation?: CrossValidation
  verification?: {
    findings_verified: number
    findings_total: number
    claims_verified: number
    claims_total: number
  }
  history?: HistoryReport
  llm?: LLMInfo
}

export interface RepositoryStatus {
  repository_id: string
  name: string
  source: string
  source_ref: string | null
  status: 'queued' | 'processing' | 'completed' | 'failed'
  stage: string | null
  brain: { state: BrainState; stage: string | null; message: string | null }
  files: number
  agents_completed: number
  agents_total: number
  findings: number
  error: string | null
  analysis_count: number
  created_at: string
  updated_at: string
  metrics: Metrics
  agents: AgentStatus[]
  timeline: TimelineEntry[]
  stats: {
    languages?: Record<string, number>
    total_lines?: number
    sensitive_files_skipped?: string[]
    skipped?: Record<string, number>
    memory?: { memories: number; symbols: number; by_type: Record<string, number> }
  }
  report: Report
  llm: LLMInfo
}

export interface RepositorySummary {
  repository_id: string
  name: string
  source: string
  status: string
  created_at: string
  updated_at: string
}

export interface Verification {
  status: VerificationStatus
  message?: string
  file_sha256_indexed?: string | null
  file_sha256_current?: string | null
  evidence_sha256_expected?: string | null
  evidence_sha256_current?: string | null
  evidence_sha256?: string
  file_sha256?: string
  file_changed?: boolean
  lines_changed?: boolean
  relocated_line?: number | null
  checked_at?: string
}

export interface FindingLocation {
  file: string
  line: number
  line_end: number | null
  symbol: string | null
  role: string | null
  evidence: string | null
  verification: { status: VerificationStatus; message: string }
}

export interface Finding {
  id: string
  agent: string
  category: 'security' | 'duplicate'
  severity: Severity
  title: string
  description: string
  file: string
  line: number
  line_end: number
  evidence: string | null
  recommendation: string
  confidence: number
  source: 'rule' | 'llm' | 'heuristic'
  rule_id: string | null
  status: VerificationStatus
  sha256: string
  file_sha256: string
  verification: Verification
  locations: FindingLocation[]
  similarity: number | null
  annotations: string[]
  detectors: string[]
  cwe: string | null
  family: string | null
  verified_at: string | null
}

export interface Citation {
  file: string
  line_start: number
  line_end: number
  label?: string | null
  verification?: Verification
}

export interface StackLayer {
  value: string
  values: string[]
  evidence: Citation[]
}

export interface FlowStep {
  layer: string
  label: string
  file: string
  line: number
  symbol: string | null
  verification?: Verification
}

export interface RequestFlow {
  name: string
  trigger: string
  resolved: boolean
  steps: FlowStep[]
}

export interface HistoryNote {
  note: string
  files: string[]
  symbols: string[]
  kind: string
  source: Citation
}

export interface Architecture {
  project_name?: string
  summary?: string
  summary_source?: string
  architecture?: Record<string, StackLayer>
  languages?: Record<string, number>
  modules?: Array<{ name: string; role: string; files: string[]; symbols: string[]; routes: number; description: string }>
  routes?: Array<{ method: string; path: string; file: string; line: number; middlewares: string[] }>
  request_flows?: RequestFlow[]
  important_files?: Array<{ file: string; score: number; reason: string }>
  history?: HistoryNote[]
  entrypoints?: string[]
  mode?: string
}

export interface FindingsResponse {
  repository_id: string
  mode: string
  llm: LLMInfo
  security: Finding[]
  duplicates: Finding[]
  architecture: Architecture
  verification: {
    total: number
    verified: number
    stale: number
    missing: number
    other: number
    checked_at: string
    live: boolean
  }
  cross_validation: CrossValidation
  history: HistoryReport
}

export interface MemoryCitation {
  memory_id: number
  file: string | null
  line_start: number | null
  line_end: number | null
  symbol: string | null
  memory_type: string
  score: number
  matched: string[]
  snippet: string
  snippet_truncated: boolean
  sha256: string
  verification: { status: VerificationStatus; message: string }
  cited_by_answer?: boolean
}

export interface MemorySearchResponse {
  repository_id: string
  query: string
  intent: string
  mode: 'llm' | 'local'
  answer: string
  terms: string[]
  citations: MemoryCitation[]
  related: MemoryCitation[]
  verification: { verified: number; total: number }
  notes: string[]
  llm: LLMInfo
}

export interface WalkthroughStep {
  index: number
  key: string
  title: string
  duration: number
  files: string[]
  citations: Citation[]
  talking_points: string[]
  narration: string
}

export interface WalkthroughResponse {
  repository_id: string
  mode: string
  question: string
  total_duration: number
  target_seconds: number
  steps: WalkthroughStep[]
  verification: { verified: number; total: number }
}

export interface SourceFile {
  repository_id: string
  path: string
  language: string
  content: string
  line_count: number
  size: number
  sha256_current: string
  sha256_indexed: string
  changed: boolean
  editable: boolean
}

export interface SourceEdit {
  path: string
  line: number
  previous_content: string
  sha256_before: string
  sha256_after: string
}

/** What the source viewer should open. */
export interface SourceTarget {
  path: string
  lineStart?: number
  lineEnd?: number
  title?: string
  findingId?: string
  verification?: Verification
}
