"""Engineering history: what was implemented before, what is legacy, what replaced it.

Sources are documentation (README "history"/"legacy" sections, lines mentioning
deprecated code) and code markers (``// DEPRECATED``, ``@deprecated``,
``LEGACY``). Notes are kept only when they reference real files or symbols.
"""

from __future__ import annotations

import re
from typing import Any

from agents.context import AgentContext

_KEYWORDS = re.compile(
    r"(?i)\b(legacy|deprecated|obsolete|predate[sd]?|replaced|scheduled for removal|no longer|superseded|"
    r"migrat\w*|older|v1)\b"
)
_HISTORY_HEADING = re.compile(r"(?i)history|changelog|legacy|migration|deprecat|decision")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_PATH = re.compile(r"[\w./-]+\.(?:js|jsx|ts|tsx|mjs|cjs|py|go|rb|java|php|cs|rs|vue|svelte)\b")
_SYMBOL = re.compile(r"`([A-Za-z_$][\w$]*)(?:\(\))?`")


def _clean(line: str) -> str:
    return re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", line).strip()[:300]


def extract_history(context: AgentContext, limit: int = 20) -> list[dict[str, Any]]:
    known = set(context.by_path)
    symbols = set(context.symbol_index)
    notes: list[dict[str, Any]] = []
    docs = [f for f in context.files if f.kind == "doc"]
    docs.sort(key=lambda f: ("readme" not in f.tags, f.path))
    for doc in docs[:6]:
        in_history = False
        for number, line in enumerate(doc.lines, start=1):
            heading = _HEADING.match(line)
            if heading:
                in_history = bool(_HISTORY_HEADING.search(heading.group(1)))
                continue
            if not line.strip() or not (in_history or _KEYWORDS.search(line)):
                continue
            files = [p for p in dict.fromkeys(_PATH.findall(line)) if p in known]
            names = [s for s in dict.fromkeys(_SYMBOL.findall(line)) if s in symbols]
            if not files and not names:
                continue
            notes.append(
                {
                    "note": _clean(line),
                    "files": files,
                    "symbols": names,
                    "kind": "documented",
                    "source": {"file": doc.path, "line_start": number, "line_end": number},
                }
            )
    for file in context.source_files:
        if not file.parsed:
            continue
        for marker in file.parsed.markers:
            symbol = next(
                (s for s in file.parsed.symbols if marker.line <= s.line_start <= marker.line + 3),
                file.parsed.symbol_at(marker.line),
            )
            notes.append(
                {
                    "note": marker.text.lstrip("/#* ").strip(),
                    "files": [file.path],
                    "symbols": [symbol.name] if symbol else [],
                    "kind": "code-marker",
                    "source": {"file": file.path, "line_start": marker.line, "line_end": marker.line},
                }
            )
    return notes[:limit]


def legacy_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Notes that describe legacy / superseded code (used by cross-validation)."""
    return [note for note in notes if _KEYWORDS.search(note["note"])]


def legacy_symbols(notes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Symbol name -> first legacy note that mentions it."""
    by_symbol: dict[str, dict[str, Any]] = {}
    for note in legacy_notes(notes):
        for name in note["symbols"]:
            by_symbol.setdefault(name, note)
    return by_symbol
