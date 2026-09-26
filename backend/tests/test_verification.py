from __future__ import annotations

from pathlib import Path

import pytest

from repo.text import sha256_bytes
from verification.citations import SourceVerifier, VerificationStatus, evidence_matches

SOURCE = "const a = 1;\nconst API_KEY = \"sk-demo-secret\";\nfunction f() {\n  return a;\n}\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/config.js").write_text(SOURCE)
    return tmp_path


def anchored(repo: Path):  # type: ignore[no-untyped-def]
    indexed = sha256_bytes((repo / "src/config.js").read_bytes())
    anchor = SourceVerifier(repo).anchor("src/config.js", 2, 2, 'const API_KEY = "sk-d[REDACTED]";', indexed)
    return anchor, indexed


def test_valid_source_is_verified(repo: Path) -> None:
    anchor, indexed = anchored(repo)
    assert anchor.status == VerificationStatus.VERIFIED
    result = SourceVerifier(repo).verify("src/config.js", 2, 2, anchor.evidence_sha256, indexed)
    assert result.status == VerificationStatus.VERIFIED
    assert result.file_sha256_current == indexed and not result.file_changed


def test_modified_cited_line_is_stale(repo: Path) -> None:
    anchor, indexed = anchored(repo)
    path = repo / "src/config.js"
    path.write_text(path.read_text().replace('"sk-demo-secret"', "process.env.API_KEY"))
    result = SourceVerifier(repo).verify("src/config.js", 2, 2, anchor.evidence_sha256, indexed)
    assert result.status == VerificationStatus.STALE
    assert result.lines_changed and result.file_changed
    assert result.evidence_sha256_current != anchor.evidence_sha256


def test_change_elsewhere_keeps_citation_verified(repo: Path) -> None:
    anchor, indexed = anchored(repo)
    path = repo / "src/config.js"
    path.write_text(path.read_text().replace("return a;", "return a + 1;"))
    result = SourceVerifier(repo).verify("src/config.js", 2, 2, anchor.evidence_sha256, indexed)
    assert result.status == VerificationStatus.VERIFIED
    assert result.file_changed and not result.lines_changed


def test_moved_code_is_stale_with_relocation_hint(repo: Path) -> None:
    anchor, indexed = anchored(repo)
    path = repo / "src/config.js"
    path.write_text("// new header\n" + path.read_text())
    result = SourceVerifier(repo).verify("src/config.js", 2, 2, anchor.evidence_sha256, indexed)
    assert result.status == VerificationStatus.STALE
    assert result.relocated_line == 3


def test_invalid_line_and_missing_file(repo: Path) -> None:
    verifier = SourceVerifier(repo)
    assert verifier.anchor("src/config.js", 99).status == VerificationStatus.INVALID_LINE
    assert verifier.anchor("src/missing.js", 1).status == VerificationStatus.MISSING
    stale = verifier.verify("src/config.js", 99, 99, "abc", None)
    assert stale.status == VerificationStatus.STALE
    assert verifier.verify("src/missing.js", 1, 1, "abc", "def").status == VerificationStatus.MISSING


def test_stale_hash_between_indexing_and_analysis(repo: Path) -> None:
    anchor = SourceVerifier(repo).anchor("src/config.js", 1, 1, "const a = 1;", indexed_sha256="0" * 64)
    assert anchor.status == VerificationStatus.STALE


def test_unsupported_evidence_is_rejected_and_wrong_line_is_relocated(repo: Path) -> None:
    verifier = SourceVerifier(repo)
    assert verifier.anchor("src/config.js", 1, 1, "eval(userInput)").status == VerificationStatus.UNSUPPORTED
    relocated = verifier.anchor("src/config.js", 1, 1, "return a;")
    assert relocated.status == VerificationStatus.VERIFIED
    assert relocated.relocated_from == 1 and relocated.line_start == 4


def test_path_traversal_is_unsafe(repo: Path) -> None:
    verifier = SourceVerifier(repo)
    assert verifier.anchor("../outside.js", 1).status == VerificationStatus.UNSAFE
    assert verifier.verify("/etc/passwd", 1, 1, None, None).status == VerificationStatus.UNSAFE


def test_evidence_matching_rules() -> None:
    assert evidence_matches('const KEY = "ab[REDACTED]";', 'const KEY = "abcdef";')
    assert evidence_matches("return   a;", "  return a;  ")
    assert evidence_matches("const a = 1…", "const a = 1;")
    assert not evidence_matches("exec(cmd)", "const a = 1;")
