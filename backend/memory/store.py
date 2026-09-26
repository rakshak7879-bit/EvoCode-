"""Data access for repositories, files, memories, findings and agent runs.

All SQL is parameterized. Column names used in dynamic UPDATE statements come
from fixed allow-lists, never from callers' input.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from memory.database import Database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class FileRecord:
    id: int | None
    path: str
    language: str
    kind: str
    size: int
    line_count: int
    sha256: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryRow:
    file_id: int | None
    path: str | None
    content: str
    memory_type: str  # code | symbol | doc | config | finding | insight | history
    symbol: str | None
    line_start: int | None
    line_end: int | None
    sha256: str
    terms: str = ""


_REPO_JSON_FIELDS = frozenset({"stats", "report", "timeline"})
_REPO_FIELDS = frozenset(
    {
        "name", "path", "source", "source_ref", "task", "status", "stage", "brain_state",
        "brain_message", "error", "stats", "report", "timeline", "analysis_count",
    }
)
_RUN_FIELDS = frozenset(
    {"status", "mode", "summary", "reason", "error", "started_at", "completed_at", "duration_ms", "result"}
)


def _decode_repository(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in _REPO_JSON_FIELDS:
        data[key] = json.loads(data.get(key) or ("[]" if key == "timeline" else "{}"))
    return data


def _decode_finding(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["verification"] = json.loads(data.get("verification") or "{}")
    data["extra"] = json.loads(data.get("extra") or "{}")
    return data


class MemoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ repositories
    def create_repository(
        self, *, repo_id: str, name: str, path: str, source: str, source_ref: str | None, task: str
    ) -> None:
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO repositories
                   (id, name, path, source, source_ref, task, status, stage, brain_state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'queued', 'queued', 'idle', ?, ?)""",
                (repo_id, name, path, source, source_ref, task, now, now),
            )

    def get_repository(self, repo_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM repositories WHERE id = ?", (repo_id,)).fetchone()
        return _decode_repository(row) if row else None

    def list_repositories(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM repositories ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_decode_repository(r) for r in rows]

    def update_repository(self, repo_id: str, **fields: Any) -> None:
        unknown = set(fields) - _REPO_FIELDS
        if unknown:
            raise ValueError(f"Unknown repository fields: {sorted(unknown)}")
        assignments = [f"{key} = ?" for key in fields]
        values = [json.dumps(v) if k in _REPO_JSON_FIELDS else v for k, v in fields.items()]
        assignments.append("updated_at = ?")
        values.extend([utc_now(), repo_id])
        with self.db.connect() as conn:
            conn.execute(f"UPDATE repositories SET {', '.join(assignments)} WHERE id = ?", values)

    def append_timeline(self, repo_id: str, entry: dict[str, Any], max_entries: int = 250) -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT timeline FROM repositories WHERE id = ?", (repo_id,)).fetchone()
            if row is None:
                return
            timeline = json.loads(row["timeline"] or "[]")
            timeline.append(entry)
            conn.execute(
                "UPDATE repositories SET timeline = ?, updated_at = ? WHERE id = ?",
                (json.dumps(timeline[-max_entries:]), utc_now(), repo_id),
            )

    def begin_analysis(self, repo_id: str) -> int:
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE repositories SET analysis_count = analysis_count + 1, status = 'processing',
                   error = NULL, timeline = '[]', brain_state = 'idle', updated_at = ? WHERE id = ?""",
                (utc_now(), repo_id),
            )
            row = conn.execute("SELECT analysis_count FROM repositories WHERE id = ?", (repo_id,)).fetchone()
        return int(row["analysis_count"]) if row else 1

    # ------------------------------------------------------------------ files
    def replace_files(self, repo_id: str, files: Sequence[FileRecord]) -> dict[str, int]:
        """Replace the file index (and all memories) of a repository."""
        now = utc_now()
        ids: dict[str, int] = {}
        with self.db.connect() as conn:
            if self.db.fts5_enabled:
                conn.execute(
                    "DELETE FROM memories_fts WHERE rowid IN (SELECT id FROM memories WHERE repository_id = ?)",
                    (repo_id,),
                )
            conn.execute("DELETE FROM memories WHERE repository_id = ?", (repo_id,))
            conn.execute("DELETE FROM files WHERE repository_id = ?", (repo_id,))
            for f in files:
                cursor = conn.execute(
                    """INSERT INTO files (repository_id, path, language, kind, size, line_count, sha256, tags, indexed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (repo_id, f.path, f.language, f.kind, f.size, f.line_count, f.sha256, json.dumps(list(f.tags)), now),
                )
                ids[f.path] = int(cursor.lastrowid or 0)
        return ids

    def list_files(self, repo_id: str) -> list[FileRecord]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM files WHERE repository_id = ? ORDER BY path", (repo_id,)
            ).fetchall()
        return [
            FileRecord(r["id"], r["path"], r["language"], r["kind"], r["size"], r["line_count"], r["sha256"],
                       tuple(json.loads(r["tags"] or "[]")))
            for r in rows
        ]

    def get_file(self, repo_id: str, path: str) -> FileRecord | None:
        with self.db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM files WHERE repository_id = ? AND path = ?", (repo_id, path)
            ).fetchone()
        if r is None:
            return None
        return FileRecord(r["id"], r["path"], r["language"], r["kind"], r["size"], r["line_count"], r["sha256"],
                          tuple(json.loads(r["tags"] or "[]")))

    # ------------------------------------------------------------------ memories
    def insert_memories(self, repo_id: str, rows: Iterable[MemoryRow]) -> int:
        now = utc_now()
        count = 0
        with self.db.connect() as conn:
            for row in rows:
                cursor = conn.execute(
                    """INSERT INTO memories (repository_id, file_id, path, content, memory_type, symbol,
                       line_start, line_end, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (repo_id, row.file_id, row.path, row.content, row.memory_type, row.symbol,
                     row.line_start, row.line_end, row.sha256, now),
                )
                if self.db.fts5_enabled:
                    conn.execute(
                        """INSERT INTO memories_fts (rowid, content, terms, path, symbol, repository_id, memory_type)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (cursor.lastrowid, row.content, row.terms, row.path or "", row.symbol or "",
                         repo_id, row.memory_type),
                    )
                count += 1
        return count

    def delete_memories(self, repo_id: str, memory_types: Sequence[str]) -> None:
        if not memory_types:
            return
        placeholders = ",".join("?" for _ in memory_types)
        params = (repo_id, *memory_types)
        with self.db.connect() as conn:
            if self.db.fts5_enabled:
                conn.execute(
                    f"""DELETE FROM memories_fts WHERE rowid IN
                        (SELECT id FROM memories WHERE repository_id = ? AND memory_type IN ({placeholders}))""",
                    params,
                )
            conn.execute(
                f"DELETE FROM memories WHERE repository_id = ? AND memory_type IN ({placeholders})", params
            )

    def memory_counts(self, repo_id: str) -> dict[str, int]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT memory_type, COUNT(*) AS n FROM memories WHERE repository_id = ? GROUP BY memory_type",
                (repo_id,),
            ).fetchall()
        return {r["memory_type"]: int(r["n"]) for r in rows}

    def list_memories(self, repo_id: str, memory_types: Sequence[str], limit: int = 50) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in memory_types)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM memories WHERE repository_id = ? AND memory_type IN ({placeholders})
                    ORDER BY id LIMIT ?""",
                (repo_id, *memory_types, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ findings
    def replace_findings(self, repo_id: str, findings: Sequence[dict[str, Any]]) -> None:
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute("DELETE FROM findings WHERE repository_id = ?", (repo_id,))
            for f in findings:
                conn.execute(
                    """INSERT INTO findings (id, repository_id, agent, category, severity, title, description, file,
                       line, line_end, evidence, recommendation, confidence, source, rule_id, fingerprint, status,
                       sha256, file_sha256, verification, extra, created_at, verified_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f["id"], repo_id, f["agent"], f["category"], f["severity"], f["title"], f["description"],
                        f["file"], f["line"], f["line_end"], f.get("evidence"), f["recommendation"],
                        f["confidence"], f["source"], f.get("rule_id"), f["fingerprint"], f["status"],
                        f["sha256"], f["file_sha256"], json.dumps(f.get("verification", {})),
                        json.dumps(f.get("extra", {})), now, f.get("verified_at"),
                    ),
                )

    def list_findings(self, repo_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM findings WHERE repository_id = ? ORDER BY category, line", (repo_id,)
            ).fetchall()
        return [_decode_finding(r) for r in rows]

    def update_finding_verifications(self, updates: Sequence[tuple[str, str, dict[str, Any], dict[str, Any]]]) -> None:
        """Persist ``(finding_id, status, verification, extra)`` tuples."""
        now = utc_now()
        with self.db.connect() as conn:
            for finding_id, status, verification, extra in updates:
                conn.execute(
                    "UPDATE findings SET status = ?, verification = ?, extra = ?, verified_at = ? WHERE id = ?",
                    (status, json.dumps(verification), json.dumps(extra), now, finding_id),
                )

    # ------------------------------------------------------------------ agent runs
    def create_agent_run(
        self, repo_id: str, analysis_no: int, agent: str, status: str, reason: str | None = None
    ) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO agent_runs (repository_id, analysis_no, agent, status, reason) VALUES (?, ?, ?, ?, ?)",
                (repo_id, analysis_no, agent, status, reason),
            )
        return int(cursor.lastrowid or 0)

    def update_agent_run(self, run_id: int, **fields: Any) -> None:
        unknown = set(fields) - _RUN_FIELDS
        if unknown:
            raise ValueError(f"Unknown agent run fields: {sorted(unknown)}")
        assignments = [f"{key} = ?" for key in fields]
        values = [json.dumps(v) if k == "result" else v for k, v in fields.items()]
        values.append(run_id)
        with self.db.connect() as conn:
            conn.execute(f"UPDATE agent_runs SET {', '.join(assignments)} WHERE id = ?", values)

    def list_agent_runs(self, repo_id: str, analysis_no: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE repository_id = ? AND analysis_no = ? ORDER BY id",
                (repo_id, analysis_no),
            ).fetchall()
        out = []
        for r in rows:
            data = dict(r)
            data["result"] = json.loads(data.get("result") or "{}")
            out.append(data)
        return out
