"""Builds the read-only AgentContext from the index, the working copy and memory."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agents.context import AgentContext, SourceFile
from llm.provider import LLMProvider
from memory.search import MemorySearch, SearchHit
from memory.store import MemoryStore
from repo.parser import parse_source
from repo.paths import UnsafePathError, resolve_in_root
from repo.text import decode_source, sha256_bytes, split_lines

logger = logging.getLogger("evo.brain.context")

FOCUS_QUERIES: dict[str, str] = {
    "security": "auth password token secret key jwt query sql exec eval cors env credential",
    "duplicate": "validate check format parse request fetch helper util",
    "explainer": "main app server route router api database config readme",
}
MAX_CONTEXT_BYTES = 40 * 1024 * 1024


class RepositoryMemory:
    """Read-only memory reader bound to one repository (what agents receive)."""

    def __init__(self, search: MemorySearch, repo_id: str) -> None:
        self._search = search
        self._repo_id = repo_id

    def search(self, query: str, limit: int = 8, memory_types: Sequence[str] | None = None) -> list[SearchHit]:
        _, hits = self._search.search(self._repo_id, query, limit=limit, memory_types=memory_types)
        return hits


@dataclass
class ContextSummary:
    files: int
    readme: str | None
    manifests: list[str]
    memory_entries: int
    focus: dict[str, tuple[str, ...]] = field(default_factory=dict)
    prior_insights: int = 0
    drifted_files: list[str] = field(default_factory=list)


class ContextBuilder:
    def __init__(self, store: MemoryStore, search: MemorySearch, llm: LLMProvider) -> None:
        self.store = store
        self.search = search
        self.llm = llm

    def build(self, repo_id: str, repo_name: str, root: Path, task: str) -> tuple[AgentContext, ContextSummary]:
        files: list[SourceFile] = []
        drifted: list[str] = []
        budget = MAX_CONTEXT_BYTES
        for record in self.store.list_files(repo_id):
            try:
                data = resolve_in_root(root, record.path).read_bytes()
            except (OSError, UnsafePathError):
                logger.warning("Indexed file unavailable while building context", extra={"path": record.path})
                continue
            if len(data) > budget:
                logger.warning("Context budget exhausted", extra={"repository_id": repo_id})
                break
            budget -= len(data)
            if sha256_bytes(data) != record.sha256:
                drifted.append(record.path)
            lines = split_lines(decode_source(data))
            parsed = parse_source(record.path, record.language, lines) if record.kind == "source" else None
            files.append(
                SourceFile(
                    path=record.path, language=record.language, kind=record.kind, size=len(data),
                    sha256=record.sha256, tags=record.tags, text="\n".join(lines), lines=lines, parsed=parsed,
                )
            )
        memory = RepositoryMemory(self.search, repo_id)
        focus = {
            domain: tuple(dict.fromkeys(h.path for h in memory.search(query, limit=12) if h.path))
            for domain, query in FOCUS_QUERIES.items()
        }
        prior = tuple(
            row["content"][:400] for row in self.store.list_memories(repo_id, ("finding", "insight", "history"), 40)
        )
        context = AgentContext(
            repository_id=repo_id,
            repository_name=repo_name,
            task=task,
            files=tuple(files),
            llm=self.llm,
            memory=memory,
            focus=focus,
            prior_insights=prior,
        )
        counts = self.store.memory_counts(repo_id)
        summary = ContextSummary(
            files=len(files),
            readme=context.readme.path if context.readme else None,
            manifests=[m.path for m in context.manifests],
            memory_entries=sum(counts.values()),
            focus=focus,
            prior_insights=len(prior),
            drifted_files=drifted,
        )
        return context, summary
