from __future__ import annotations

from pathlib import Path

from memory.database import Database
from memory.search import MemorySearch, build_query
from memory.store import MemoryStore
from memory.tokens import expand_terms, split_identifier
from repo.text import hash_lines, split_lines

Indexed = tuple[MemoryStore, MemorySearch, str, Path]


def test_indexing_stores_files_and_hash_anchored_passages(indexed: Indexed) -> None:
    store, _, repo_id, root = indexed
    files = store.list_files(repo_id)
    assert len(files) == 9
    counts = store.memory_counts(repo_id)
    assert counts["symbol"] >= 15 and counts["code"] > 0 and counts["doc"] > 0
    with store.db.connect() as conn:
        rows = conn.execute("SELECT path, line_start, line_end, sha256 FROM memories WHERE repository_id = ?",
                            (repo_id,)).fetchall()
    for row in rows:
        lines = split_lines((root / row["path"]).read_text())
        assert 1 <= row["line_start"] <= row["line_end"] <= len(lines)
        assert row["sha256"] == hash_lines(lines, row["line_start"], row["line_end"])


def test_natural_language_question_finds_authentication(indexed: Indexed) -> None:
    _, search, repo_id, _ = indexed
    plan, hits = search.search(repo_id, "Where is authentication implemented?")
    assert "authentication" in plan.terms and "auth" in plan.expanded
    assert hits and hits[0].path == "backend/auth.js"
    assert {"backend/auth.js", "frontend/auth.js"} <= {h.path for h in hits}


def test_exact_identifier_search(indexed: Indexed) -> None:
    _, search, repo_id, _ = indexed
    plan, hits = search.search(repo_id, "API_KEY")
    assert plan.identifiers == ("API_KEY",)
    assert hits[0].path == "backend/payments.js"
    assert "API_KEY" in hits[0].content


def test_symbol_and_camelcase_search(indexed: Indexed) -> None:
    _, search, repo_id, _ = indexed
    _, hits = search.search(repo_id, "findUserByEmail")
    assert any(h.memory_type == "symbol" and h.symbol == "findUserByEmail" for h in hits)
    # Natural words match camelCase identifiers through the split-identifier terms column.
    _, hits = search.search(repo_id, "user by email lookup", memory_types=["symbol"])
    assert "findUserByEmail" in {h.symbol for h in hits}


def test_filename_and_keyword_search(indexed: Indexed) -> None:
    _, search, repo_id, _ = indexed
    _, hits = search.search(repo_id, "payments")
    assert hits[0].path == "backend/payments.js"
    _, hits = search.search(repo_id, "cors origin")
    assert hits[0].path == "backend/server.js"


def test_citations_are_valid_line_ranges(indexed: Indexed) -> None:
    _, search, repo_id, root = indexed
    _, hits = search.search(repo_id, "password hashing", limit=10)
    assert hits
    for hit in hits:
        lines = split_lines((root / hit.path).read_text())
        assert hit.sha256 == hash_lines(lines, hit.line_start, hit.line_end)


def test_fts_operators_in_queries_are_neutralized(indexed: Indexed) -> None:
    _, search, repo_id, _ = indexed
    for query in ['"auth" OR NEAR(', "auth* AND -", "';DROP TABLE memories;--", "()", "*"]:
        search.search(repo_id, query)  # must not raise
    assert build_query("NEAR(auth").match_expression() == '"near"* OR "auth"* OR "authentication"* OR "authenticate"* OR "login"* OR "jwt"* OR "token"* OR "session"*'


def test_like_fallback_when_fts5_unavailable(indexed: Indexed) -> None:
    store, search, repo_id, _ = indexed
    store.db.fts5_enabled = False
    try:
        _, hits = search.search(repo_id, "legacyFindUser")
        assert hits and hits[0].path == "backend/users.js"
    finally:
        store.db.fts5_enabled = True


def test_identifier_tokenization() -> None:
    assert split_identifier("findUserByEmail") == ["find", "user", "by", "email"]
    assert split_identifier("API_KEY") == ["api", "key"]
    assert split_identifier("postJSON") == ["post", "json"]
    assert expand_terms("const JWT_SECRET = verifyToken(x)") == "jwt secret verify token"


def test_corrupted_database_is_quarantined_and_recreated(tmp_path: Path) -> None:
    path = tmp_path / "evo.db"
    path.write_bytes(b"definitely not a sqlite database" * 100)
    db = Database(path)
    db.initialize()
    assert db.recovered_from is not None and Path(db.recovered_from).exists()
    store = MemoryStore(db)
    store.create_repository(repo_id="abc12345", name="x", path="/tmp", source="zip", source_ref=None, task="t")
    assert store.get_repository("abc12345")["name"] == "x"
