"""The agent contract: structured, validated JSON in and out.

Random prose never becomes an internal API. Every agent returns an
``AgentResult`` whose findings are ``FindingDraft`` objects. Drafts are only
*claims*; the Brain cross-validates and verifies them against the source before
they become findings.

Orchestration has three levels: the Brain (level 0) routes work to lead agents
(level 1, subclasses of ``BaseAgent``), and each lead delegates to a team of
specialist sub-agents (level 2, described by ``SubAgentSpec`` and reported as
``SubAgentRun``). See ``agents/team.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agents.context import AgentContext

Severity = Literal["critical", "high", "medium", "low", "info"]
SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class Citation(BaseModel):
    """A pointer to source lines that supports a claim."""

    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    label: str | None = None
    verification: dict[str, Any] | None = None


class Location(BaseModel):
    file: str
    line: int = Field(ge=1)
    line_end: int | None = None
    symbol: str | None = None
    role: str | None = None  # e.g. "canonical" | "copy"
    evidence: str | None = None


class FindingDraft(BaseModel):
    agent: str
    category: Literal["security", "duplicate"]
    severity: Severity
    title: str
    description: str
    file: str
    line: int = Field(ge=1)
    line_end: int | None = None
    evidence: str | None = None
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["rule", "llm", "heuristic"]
    rule_id: str | None = None
    family: str | None = None
    cwe: str | None = None
    locations: list[Location] = Field(default_factory=list)
    similarity: float | None = None
    annotations: list[str] = Field(default_factory=list)
    detectors: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SubAgentSpec:
    """Static description of a level-2 specialist sub-agent owned by a lead agent."""

    key: str
    title: str
    description: str
    #: Hard dependencies: these members must complete, otherwise this member is skipped.
    depends_on: tuple[str, ...] = ()
    #: Soft dependencies: waited for when planned; their failure is tolerated.
    uses: tuple[str, ...] = ()
    #: Only runs when an LLM is configured (skipped, and labeled, in local mode).
    requires_llm: bool = False
    #: Task words that select this member when the Brain routed a focused task.
    keywords: tuple[str, ...] = ()
    #: What the lead does without this member (appended to failure notes).
    fallback: str = ""


class SubAgentRun(BaseModel):
    """Execution record of one level-2 sub-agent (persisted and shown in the CLI tree)."""

    name: str  # "<lead>.<key>", e.g. "security.secrets"
    key: str
    parent: str
    title: str
    description: str = ""
    status: Literal["queued", "running", "complete", "failed", "skipped"] = "queued"
    reason: str = ""
    wave: int = 0
    depends_on: list[str] = Field(default_factory=list)
    mode: Literal["llm", "local"] = "local"
    summary: str = ""
    findings: int = 0
    metrics: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class AgentResult(BaseModel):
    agent: str
    status: Literal["complete", "failed", "skipped"] = "complete"
    mode: Literal["llm", "local"] = "local"
    summary: str = ""
    findings: list[FindingDraft] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0
    #: Level-2 execution records of the lead's specialist team.
    subagents: list[SubAgentRun] = Field(default_factory=list)


class BaseAgent(ABC):
    """Common interface for all lead agents (orchestration level 1)."""

    name: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    #: Upstream agent results the Brain must pass in (via ``context.upstream``).
    requires: ClassVar[tuple[str, ...]] = ()
    #: Optional upstream results used when available.
    consumes: ClassVar[tuple[str, ...]] = ()
    #: Level-2 roster: the specialist sub-agents this lead can delegate to.
    team: ClassVar[tuple[SubAgentSpec, ...]] = ()

    @abstractmethod
    async def run(self, context: "AgentContext") -> AgentResult:
        raise NotImplementedError
