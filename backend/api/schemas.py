"""Pydantic request/response models for the public API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

REPOSITORY_ID_PATTERN = r"^[a-f0-9]{8,32}$"


class LLMInfo(BaseModel):
    provider: str
    model: str
    available: bool
    mode: str
    label: str


class HealthResponse(BaseModel):
    status: str
    version: str
    llm: LLMInfo
    fts5: bool
    database_recovered: bool
    allow_source_edits: bool
    demo_repo_available: bool
    limits: dict[str, int]


class AnalyzeResponse(BaseModel):
    repository_id: str
    status: str


class ReanalyzeRequest(BaseModel):
    task: str | None = Field(default=None, max_length=500)


class BrainStatus(BaseModel):
    state: str
    stage: str | None
    message: str | None


class AgentStatus(BaseModel):
    name: str
    title: str
    description: str
    status: str
    mode: str | None = None
    summary: str | None = None
    reason: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    started_at: str | None = None
    completed_at: str | None = None


class TimelineEntry(BaseModel):
    ts: str
    elapsed_ms: int = 0
    stage: str
    level: str = "info"
    message: str
    agent: str | None = None


class Metrics(BaseModel):
    files_analyzed: int
    memory_entries: int
    security_findings: int | None
    duplicate_clusters: int | None
    verified_findings: int | None
    stale_findings: int | None
    total_findings: int | None


class RepositoryStatus(BaseModel):
    repository_id: str
    name: str
    source: str
    source_ref: str | None
    status: str
    stage: str | None
    brain: BrainStatus
    files: int
    agents_completed: int
    agents_total: int
    findings: int
    error: str | None
    analysis_count: int
    created_at: str
    updated_at: str
    metrics: Metrics
    agents: list[AgentStatus]
    timeline: list[TimelineEntry]
    stats: dict[str, Any]
    report: dict[str, Any]
    llm: LLMInfo


class RepositorySummary(BaseModel):
    repository_id: str
    name: str
    source: str
    status: str
    created_at: str
    updated_at: str


class FindingOut(BaseModel):
    id: str
    agent: str
    category: str
    severity: str
    title: str
    description: str
    file: str
    line: int
    line_end: int
    evidence: str | None
    recommendation: str
    confidence: float
    source: str
    rule_id: str | None
    status: str
    sha256: str
    file_sha256: str
    verification: dict[str, Any]
    locations: list[dict[str, Any]] = Field(default_factory=list)
    similarity: float | None = None
    annotations: list[str] = Field(default_factory=list)
    detectors: list[str] = Field(default_factory=list)
    cwe: str | None = None
    family: str | None = None
    verified_at: str | None = None


class FindingsResponse(BaseModel):
    repository_id: str
    mode: str
    llm: LLMInfo
    security: list[FindingOut]
    duplicates: list[FindingOut]
    architecture: dict[str, Any]
    verification: dict[str, Any]
    cross_validation: dict[str, Any]
    history: dict[str, Any]


class MemorySearchRequest(BaseModel):
    repository_id: str = Field(pattern=REPOSITORY_ID_PATTERN)
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=6, ge=1, le=20)


class MemorySearchResponse(BaseModel):
    repository_id: str
    query: str
    intent: str
    mode: str
    answer: str
    terms: list[str]
    citations: list[dict[str, Any]]
    related: list[dict[str, Any]]
    verification: dict[str, int]
    notes: list[str]
    llm: LLMInfo


class WalkthroughResponse(BaseModel):
    repository_id: str
    mode: str
    question: str
    total_duration: int
    target_seconds: int
    steps: list[dict[str, Any]]
    verification: dict[str, int]


class SourceFileResponse(BaseModel):
    repository_id: str
    path: str
    language: str
    content: str
    line_count: int
    size: int
    sha256_current: str
    sha256_indexed: str
    changed: bool
    editable: bool


class SourceEditRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    line: int = Field(ge=1, le=1_000_000)
    content: str = Field(max_length=2000)


class SourceEditResponse(BaseModel):
    path: str
    line: int
    previous_content: str
    sha256_before: str
    sha256_after: str
