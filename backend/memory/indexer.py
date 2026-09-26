"""Memory indexer: turns scanned files into searchable, hash-anchored passages.

For every file the indexer re-reads the bytes, recomputes the file SHA-256 (the
value stored in the index is always the hash of exactly what was indexed) and
creates passages:

- ``symbol``  one passage per function / class / method (from the parser)
- ``code``    overlapping line windows covering the whole source file
- ``doc``     one passage per Markdown section
- ``config``  line windows for configuration and manifest files

Each passage stores its line range and the SHA-256 of those lines so that any
citation produced later can be verified against the current source.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from memory.store import FileRecord, MemoryRow, MemoryStore
from memory.tokens import expand_terms
from repo.parser import ParsedFile, parse_source
from repo.paths import UnsafePathError, resolve_in_root
from repo.scanner import ScanResult
from repo.text import decode_source, hash_lines, sha256_bytes, split_lines

logger = logging.getLogger("evo.memory.indexer")

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")


@dataclass
class IndexStats:
    files: int = 0
    memories: int = 0
    symbols: int = 0
    by_type: Counter[str] = field(default_factory=Counter)

    def to_dict(self) -> dict[str, object]:
        return {
            "files": self.files,
            "memories": self.memories,
            "symbols": self.symbols,
            "by_type": dict(self.by_type),
        }


class MemoryIndexer:
    WINDOW = 40
    STEP = 30
    CONFIG_WINDOW = 60
    MAX_SYMBOL_LINES = 160
    DOC_SECTION_MAX = 60
    MAX_PASSAGES_PER_FILE = 400

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def index(self, repo_id: str, root: Path, scan: ScanResult) -> tuple[IndexStats, dict[str, ParsedFile]]:
        stats = IndexStats()
        records: list[FileRecord] = []
        contents: dict[str, list[str]] = {}
        for scanned in scan.files:
            try:
                data = resolve_in_root(root, scanned.path).read_bytes()
            except (OSError, UnsafePathError):
                logger.warning("Skipping unreadable file during indexing", extra={"path": scanned.path})
                continue
            lines = split_lines(decode_source(data))
            contents[scanned.path] = lines
            records.append(
                FileRecord(None, scanned.path, scanned.language, scanned.kind, len(data), len(lines),
                           sha256_bytes(data), scanned.tags)
            )

        file_ids = self.store.replace_files(repo_id, records)
        rows: list[MemoryRow] = []
        parsed_files: dict[str, ParsedFile] = {}
        for record in records:
            lines = contents[record.path]
            file_id = file_ids.get(record.path)
            if record.kind == "source":
                parsed = parse_source(record.path, record.language, lines)
                parsed_files[record.path] = parsed
                stats.symbols += len(parsed.symbols)
                passages = self._code_passages(record, file_id, lines, parsed)
            elif record.kind == "doc":
                passages = self._doc_passages(record, file_id, lines)
            else:
                passages = self._windows(record, file_id, lines, "config", self.CONFIG_WINDOW, self.CONFIG_WINDOW)
            passages = passages[: self.MAX_PASSAGES_PER_FILE]
            for passage in passages:
                stats.by_type[passage.memory_type] += 1
            rows.extend(passages)
        stats.files = len(records)
        stats.memories = self.store.insert_memories(repo_id, rows)
        return stats, parsed_files

    # ------------------------------------------------------------------ passage builders
    def _row(self, record: FileRecord, file_id: int | None, lines: list[str], start: int, end: int,
             memory_type: str, symbol: str | None, extra_terms: str = "") -> MemoryRow:
        content = "\n".join(lines[start - 1 : end])
        terms = " ".join(t for t in (expand_terms(content), expand_terms(record.path), extra_terms) if t)
        return MemoryRow(
            file_id=file_id,
            path=record.path,
            content=content,
            memory_type=memory_type,
            symbol=symbol,
            line_start=start,
            line_end=end,
            sha256=hash_lines(lines, start, end),
            terms=terms,
        )

    def _windows(self, record: FileRecord, file_id: int | None, lines: list[str], memory_type: str,
                 window: int, step: int, parsed: ParsedFile | None = None) -> list[MemoryRow]:
        rows: list[MemoryRow] = []
        total = len(lines)
        start = 1
        while start <= total:
            end = min(total, start + window - 1)
            if any(line.strip() for line in lines[start - 1 : end]):
                names = ""
                if parsed is not None:
                    overlapping = [s.name for s in parsed.symbols if s.line_start <= end and s.line_end >= start]
                    names = " ".join(dict.fromkeys(overlapping))
                rows.append(self._row(record, file_id, lines, start, end, memory_type, None, names))
            if end >= total:
                break
            start += step
        return rows

    def _code_passages(self, record: FileRecord, file_id: int | None, lines: list[str],
                       parsed: ParsedFile) -> list[MemoryRow]:
        rows: list[MemoryRow] = []
        for symbol in parsed.symbols:
            end = min(symbol.line_end, symbol.line_start + self.MAX_SYMBOL_LINES - 1)
            rows.append(
                self._row(record, file_id, lines, symbol.line_start, end, "symbol", symbol.qualified_name,
                          " ".join(p for p in (symbol.kind, symbol.name) if p))
            )
        rows.extend(self._windows(record, file_id, lines, "code", self.WINDOW, self.STEP, parsed))
        return rows

    def _doc_passages(self, record: FileRecord, file_id: int | None, lines: list[str]) -> list[MemoryRow]:
        if not lines:
            return []
        boundaries: list[tuple[int, str | None]] = []
        for index, line in enumerate(lines, start=1):
            match = _HEADING.match(line)
            if match:
                boundaries.append((index, match.group(1).strip()[:120]))
        if not boundaries or boundaries[0][0] != 1:
            boundaries.insert(0, (1, None))
        rows: list[MemoryRow] = []
        for position, (start, heading) in enumerate(boundaries):
            end = boundaries[position + 1][0] - 1 if position + 1 < len(boundaries) else len(lines)
            if end < start:
                continue
            chunk_start = start
            while chunk_start <= end:
                chunk_end = min(end, chunk_start + self.DOC_SECTION_MAX - 1)
                if any(line.strip() for line in lines[chunk_start - 1 : chunk_end]):
                    rows.append(self._row(record, file_id, lines, chunk_start, chunk_end, "doc", heading))
                chunk_start = chunk_end + 1
        return rows
