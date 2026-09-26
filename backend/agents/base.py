"""The agent contract: structured, validated JSON in and out.

Random prose never becomes an internal API. Every agent returns an
``AgentResult`` whose findings are ``FindingDraft`` objects. Drafts are only
*claims*; the Brain cross-validates and verifies them against the source before
they become findings.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
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


class BaseAgent(ABC):
    """Common interface for all specialist agents."""

    name: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    #: Upstream agent results the Brain must pass in (via ``context.upstream``).
    requires: ClassVar[tuple[str, ...]] = ()
    #: Optional upstream results used when available.
    consumes: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    async def run(self, context: "AgentContext") -> AgentResult:
        raise NotImplementedError
