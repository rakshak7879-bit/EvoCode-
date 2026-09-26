"""Memory retrieval over SQLite FTS5 (BM25) with identifier-aware boosting.

Supports exact identifiers (``API_KEY``, ``verifyToken``), natural-language
questions ("Where is authentication implemented?"), file names, symbols and
plain keywords. User input is reduced to alphanumeric terms and quoted before it
reaches the FTS5 MATCH expression, so it can never inject FTS operators.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from memory.database import Database
from memory.tokens import is_compound_identifier, split_identifier

STOPWORDS = frozenset(
    """a about all an and any are as at be been by can could code codebase do does doing done file
    files find for from function functions get handle handled handles happen happens have how i if
    implement implemented implementation implements in into is it its located location logic me my of
    on or our please repo repository show so some tell that the their them there these this those to
    use used uses using was what when where which who why will with work works would you your""".split()
)

SYNONYMS: dict[str, tuple[str, ...]] = {
    "auth": ("authentication", "authenticate", "login", "jwt", "token", "session"),
    "authentication": ("auth", "login", "jwt", "token", "session", "password"),
    "authenticate": ("auth", "login", "jwt", "token"),
    "authorization": ("auth", "permission", "role", "token"),
    "login": ("auth", "signin", "authenticate", "password"),
    "signin": ("login", "auth"),
    "database": ("db", "sql", "query", "pool", "collection"),
    "db": ("database", "sql", "query", "pool"),
    "sql": ("query", "database", "select"),
    "payment": ("payments", "checkout", "charge", "card", "billing"),
    "payments": ("payment", "checkout", "charge", "card"),
    "checkout": ("payment", "charge", "cart", "order"),
    "validation": ("validate", "valid", "check", "sanitize"),
    "validate": ("validation", "valid", "check"),
    "email": ("mail",),
    "password": ("passwd", "hash", "credential"),
    "secret": ("key", "token", "credential", "password"),
    "secrets": ("secret", "key", "token", "credential"),
    "security": ("secret", "vulnerability", "injection", "auth"),
    "vulnerability": ("security", "injection", "unsafe"),
    "config": ("configuration", "settings", "env"),
    "configuration": ("config", "settings", "env"),
    "api": ("route", "endpoint", "router"),
    "route": ("router", "endpoint", "api"),
    "routes": ("router", "endpoint", "api", "route"),
    "endpoint": ("route", "router", "api"),
    "user": ("users", "account", "profile"),
    "users": ("user", "account"),
    "error": ("exception", "catch", "throw"),
    "test": ("tests", "spec"),
    "duplicate": ("duplicated", "copy", "similar"),
    "history": ("legacy", "deprecated", "v1"),
    "legacy": ("deprecated", "old", "v1"),
}

_WORD = re.compile(r"[A-Za-z0-9_]+")
_BM25_WEIGHTS = (1.0, 1.2, 3.0, 5.0)  # content, terms, path, symbol


@dataclass(frozen=True)
class QueryPlan:
    raw: str
    terms: tuple[str, ...]
    expanded: tuple[str, ...]
    identifiers: tuple[str, ...]
    subject: str

    @property
    def all_terms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.terms, *self.expanded)))

    def match_expression(self) -> str:
        parts = []
        for term in self.all_terms:
            safe = re.sub(r"[^a-z0-9]", "", term.lower())
            if not safe:
                continue
            parts.append(f'"{safe}"*' if len(safe) >= 3 else f'"{safe}"')
        return " OR ".join(parts)


@dataclass
class SearchHit:
    memory_id: int
    path: str | None
    file_id: int | None
    memory_type: str
    symbol: str | None
    line_start: int | None
    line_end: int | None
    content: str
    sha256: str
    score: float
    matched: list[str] = field(default_factory=list)


def build_query(query: str) -> QueryPlan:
    raw = (query or "").strip()[:500]
    identifiers: list[str] = []
    terms: list[str] = []
    subject_words: list[str] = []
    for token in _WORD.findall(raw):
        lower = token.lower()
        if is_compound_identifier(token):
            identifiers.append(token)
            terms.append(lower)
            terms.extend(p for p in split_identifier(token) if len(p) > 1 and p not in STOPWORDS)
            subject_words.append(token)
        elif lower not in STOPWORDS and len(lower) > 1:
            terms.append(lower)
            subject_words.append(token)
    if not terms:  # e.g. "where is it?" - fall back to every word
        terms = [w.lower() for w in _WORD.findall(raw) if len(w) > 1]
    terms = list(dict.fromkeys(terms))
    expanded: list[str] = []
    for term in terms:
        for synonym in SYNONYMS.get(term, ()):
            if synonym not in terms and synonym not in expanded:
                expanded.append(synonym)
    subject = " ".join(subject_words[:4]) or raw
    return QueryPlan(raw, tuple(terms), tuple(expanded[:12]), tuple(dict.fromkeys(identifiers)), subject)


def _overlaps(a: SearchHit, b: SearchHit) -> bool:
    if a.path != b.path or a.line_start is None or b.line_start is None:
        return False
    a_end = a.line_end or a.line_start
    b_end = b.line_end or b.line_start
    intersection = min(a_end, b_end) - max(a.line_start, b.line_start) + 1
    if intersection <= 0:
        return False
    shorter = min(a_end - a.line_start + 1, b_end - b.line_start + 1)
    return intersection / max(shorter, 1) >= 0.6


class MemorySearch:
    def __init__(self, db: Database) -> None:
        self.db = db

    def search(
        self,
        repo_id: str,
        query: str,
        *,
        limit: int = 8,
        memory_types: Sequence[str] | None = None,
        per_file: int = 3,
    ) -> tuple[QueryPlan, list[SearchHit]]:
        plan = build_query(query)
        if not plan.terms:
            return plan, []
        candidates = self._fts_candidates(repo_id, plan, memory_types) if self.db.fts5_enabled else []
        if not candidates:
            candidates = self._like_candidates(repo_id, plan, memory_types)
        candidates.extend(self._identifier_candidates(repo_id, plan, memory_types, candidates))
        for hit in candidates:
            self._boost(hit, plan)
        candidates.sort(key=lambda h: h.score, reverse=True)
        selected: list[SearchHit] = []
        per_file_counts: dict[str, int] = {}
        for hit in candidates:
            key = hit.path or f"memory:{hit.memory_id}"
            if per_file_counts.get(key, 0) >= per_file:
                continue
            if any(h.memory_id == hit.memory_id or _overlaps(h, hit) for h in selected):
                continue
            selected.append(hit)
            per_file_counts[key] = per_file_counts.get(key, 0) + 1
            if len(selected) >= limit:
                break
        return plan, selected

    # ------------------------------------------------------------------ candidate sources
    def _type_filter(self, memory_types: Sequence[str] | None) -> tuple[str, list[str]]:
        if not memory_types:
            return "", []
        placeholders = ",".join("?" for _ in memory_types)
        return f" AND m.memory_type IN ({placeholders})", list(memory_types)

    def _fts_candidates(self, repo_id: str, plan: QueryPlan, memory_types: Sequence[str] | None) -> list[SearchHit]:
        expression = plan.match_expression()
        if not expression:
            return []
        type_sql, type_params = self._type_filter(memory_types)
        weights = ", ".join(str(w) for w in _BM25_WEIGHTS)
        sql = f"""
            SELECT m.*, bm25(memories_fts, {weights}) AS rank
            FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid
            WHERE memories_fts MATCH ? AND m.repository_id = ?{type_sql}
            ORDER BY rank LIMIT 80
        """
        try:
            with self.db.connect() as conn:
                rows = conn.execute(sql, (expression, repo_id, *type_params)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._hit(row, -float(row["rank"])) for row in rows]

    def _like_candidates(self, repo_id: str, plan: QueryPlan, memory_types: Sequence[str] | None) -> list[SearchHit]:
        type_sql, type_params = self._type_filter(memory_types)
        terms = list(plan.all_terms)[:10]
        if not terms:
            return []
        clauses = " OR ".join("lower(m.content) LIKE ? OR lower(coalesce(m.path, '')) LIKE ?" for _ in terms)
        params: list[object] = []
        for term in terms:
            like = f"%{term.replace('%', '').replace('_', '')}%"
            params.extend([like, like])
        sql = f"SELECT m.* FROM memories m WHERE m.repository_id = ?{type_sql} AND ({clauses}) LIMIT 200"
        with self.db.connect() as conn:
            rows = conn.execute(sql, (repo_id, *type_params, *params)).fetchall()
        hits = []
        for row in rows:
            text = (row["content"] or "").lower()
            score = sum(min(text.count(t), 5) for t in terms) * 0.8
            hits.append(self._hit(row, score))
        return hits

    def _identifier_candidates(
        self, repo_id: str, plan: QueryPlan, memory_types: Sequence[str] | None, existing: list[SearchHit]
    ) -> list[SearchHit]:
        """Exact (case-sensitive) identifier matches the tokenizer might rank poorly."""
        if not plan.identifiers:
            return []
        known = {h.memory_id for h in existing}
        type_sql, type_params = self._type_filter(memory_types)
        extra: list[SearchHit] = []
        with self.db.connect() as conn:
            for identifier in plan.identifiers[:4]:
                rows = conn.execute(
                    f"""SELECT m.* FROM memories m WHERE m.repository_id = ?{type_sql}
                        AND instr(m.content, ?) > 0 LIMIT 40""",
                    (repo_id, *type_params, identifier),
                ).fetchall()
                for row in rows:
                    if row["id"] not in known:
                        known.add(row["id"])
                        extra.append(self._hit(row, 1.0))
        return extra

    @staticmethod
    def _hit(row: sqlite3.Row, score: float) -> SearchHit:
        return SearchHit(
            memory_id=int(row["id"]),
            path=row["path"],
            file_id=row["file_id"],
            memory_type=row["memory_type"],
            symbol=row["symbol"],
            line_start=row["line_start"],
            line_end=row["line_end"],
            content=row["content"],
            sha256=row["sha256"],
            score=score,
        )

    @staticmethod
    def _boost(hit: SearchHit, plan: QueryPlan) -> None:
        content = hit.content or ""
        lowered = content.lower()
        occurrences = 0
        for identifier in plan.identifiers:
            count = content.count(identifier)
            if count:
                occurrences += min(count, 3)
                hit.matched.append(identifier)
        if occurrences:
            hit.score += 4.0 * occurrences
        elif plan.identifiers:
            hit.score *= 0.5  # exact-identifier query, but this passage does not contain it
        stem = PurePosixPath(hit.path).stem.lower() if hit.path else ""
        path_lower = (hit.path or "").lower()
        symbol_lower = (hit.symbol or "").lower()
        for term in plan.terms:
            if term == stem or (len(term) >= 4 and term in stem):
                hit.score += 1.5
            elif len(term) >= 3 and term in path_lower:
                hit.score += 0.6
            if len(term) >= 3 and term in symbol_lower:
                hit.score += 1.2
            if term in lowered and term not in hit.matched:
                hit.matched.append(term)
        if hit.memory_type == "symbol":
            hit.score += 0.6
        elif hit.memory_type == "doc":
            hit.score -= 0.2
        # Prefer passages that mention several distinct query terms.
        distinct = sum(1 for term in plan.all_terms if term in lowered)
        hit.score += min(distinct, 6) * 0.35
