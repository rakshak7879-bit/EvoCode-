"""Read-only context handed to agents by the Brain."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Protocol

from repo.parser import ParsedFile, Symbol

if TYPE_CHECKING:
    from agents.base import AgentResult
    from llm.provider import LLMProvider
    from memory.search import SearchHit


class MemoryReader(Protocol):
    """Read-only memory access for agents (bound to one repository)."""

    def search(self, query: str, limit: int = 8, memory_types: Sequence[str] | None = None) -> list["SearchHit"]: ...


@dataclass(frozen=True)
class SourceFile:
    path: str
    language: str
    kind: str
    size: int
    sha256: str
    tags: tuple[str, ...]
    text: str
    lines: list[str]
    parsed: ParsedFile | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def top_dir(self) -> str:
        parts = PurePosixPath(self.path).parts
        return parts[0] if len(parts) > 1 else "(root)"


@dataclass(frozen=True)
class AgentContext:
    repository_id: str
    repository_name: str
    task: str
    files: tuple[SourceFile, ...]
    llm: "LLMProvider"
    memory: MemoryReader
    focus: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    upstream: Mapping[str, "AgentResult"] = field(default_factory=dict)
    prior_insights: tuple[str, ...] = ()

    @cached_property
    def by_path(self) -> dict[str, SourceFile]:
        return {f.path: f for f in self.files}

    def file(self, path: str) -> SourceFile | None:
        return self.by_path.get(path)

    @property
    def source_files(self) -> list[SourceFile]:
        return [f for f in self.files if f.kind == "source"]

    @cached_property
    def readme(self) -> SourceFile | None:
        readmes = [f for f in self.files if "readme" in f.tags]
        readmes.sort(key=lambda f: (f.path.count("/"), f.path.lower() != "readme.md", f.path))
        return readmes[0] if readmes else None

    @property
    def manifests(self) -> list[SourceFile]:
        return [f for f in self.files if f.kind == "manifest"]

    @cached_property
    def symbols(self) -> list[Symbol]:
        return [s for f in self.files if f.parsed for s in f.parsed.symbols]

    @cached_property
    def symbol_index(self) -> dict[str, list[Symbol]]:
        index: dict[str, list[Symbol]] = {}
        for symbol in self.symbols:
            index.setdefault(symbol.name, []).append(symbol)
        return index

    def with_upstream(self, upstream: Mapping[str, "AgentResult"]) -> "AgentContext":
        return replace(self, upstream=dict(upstream))
