"""SQLite connection management, schema and corruption recovery.

Each operation opens a short-lived connection (safe across threads). The
schema is created on startup. If the database file is corrupted it is moved
aside (``evo.db.corrupt-<timestamp>``) and a fresh database is created, so the
product keeps working instead of crashing.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("evo.memory.database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'zip',
    source_ref TEXT,
    task TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    brain_state TEXT NOT NULL DEFAULT 'idle',
    brain_message TEXT,
    error TEXT,
    stats TEXT NOT NULL DEFAULT '{}',
    report TEXT NOT NULL DEFAULT '{}',
    timeline TEXT NOT NULL DEFAULT '[]',
    analysis_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    line_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    indexed_at TEXT NOT NULL,
    UNIQUE (repository_id, path)
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    path TEXT,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    symbol TEXT,
    line_start INTEGER,
    line_end INTEGER,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_repository ON memories (repository_id, memory_type);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    evidence TEXT,
    recommendation TEXT NOT NULL,
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    rule_id TEXT,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    verification TEXT NOT NULL DEFAULT '{}',
    extra TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    verified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_repository ON findings (repository_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    analysis_no INTEGER NOT NULL DEFAULT 1,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT,
    summary TEXT,
    reason TEXT,
    error TEXT,
    started_at TEXT,
    completed_at TEXT,
    duration_ms INTEGER,
    result TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_repository ON agent_runs (repository_id, analysis_no);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    terms,
    path,
    symbol,
    repository_id UNINDEXED,
    memory_type UNINDEXED,
    tokenize = 'porter unicode61'
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fts5_enabled = False
        self.recovered_from: str | None = None

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._bootstrap()
        except sqlite3.DatabaseError as exc:
            self._quarantine(exc)
            self._bootstrap()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _bootstrap(self) -> None:
        conn = self._open()
        try:
            check = conn.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise sqlite3.DatabaseError(f"integrity check failed: {check[0] if check else 'unknown'}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            try:
                conn.executescript(FTS_SCHEMA)
                self.fts5_enabled = True
            except sqlite3.OperationalError as exc:
                self.fts5_enabled = False
                logger.warning("SQLite FTS5 unavailable; falling back to LIKE search", extra={"error": str(exc)})
            conn.commit()
        finally:
            conn.close()

    def _quarantine(self, exc: Exception) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = Path(f"{self.path}.corrupt-{stamp}")
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{self.path}{suffix}")
            if source.exists():
                source.replace(Path(f"{target}{suffix}"))
        self.recovered_from = str(target)
        logger.error(
            "Memory database was corrupted; moved aside and recreated",
            extra={"error": str(exc), "backup": str(target)},
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._open()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
