from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

import pytest

from repo.archive import ArchiveError, extract_zip
from repo.github import GitHubError, parse_github_url
from repo.parser import parse_source
from repo.scanner import RepositoryScanner
from repo.text import split_lines


def write(path: Path, text: str = "print('hi')\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_discovers_demo_files_and_languages(demo_copy: Path) -> None:
    scan = RepositoryScanner().scan(demo_copy)
    paths = {f.path for f in scan.files}
    assert {"README.md", "package.json", "backend/auth.js", "backend/payments.js", "backend/users.js",
            "backend/server.js", "frontend/auth.js", "frontend/checkout.js", "utils/validation.js"} <= paths
    assert scan.languages["javascript"] == 7
    special = scan.special()
    assert special["readme"] == ["README.md"]
    assert special["manifests"] == ["package.json"]
    assert "backend/server.js" in special["entrypoints"]


def test_ignores_dependency_build_and_vcs_directories(tmp_path: Path) -> None:
    write(tmp_path / "src/app.py")
    for ignored in ("node_modules/lib/index.js", ".git/hooks/pre-commit.py", "venv/lib/site.py", "dist/bundle.js",
                    "build/out.js", "coverage/report.js", "__pycache__/app.py"):
        write(tmp_path / ignored)
    write(tmp_path / "myenv/pyvenv.cfg", "home = /usr/bin")
    write(tmp_path / "myenv/lib/thing.py")
    scan = RepositoryScanner().scan(tmp_path)
    assert [f.path for f in scan.files] == ["src/app.py"]
    assert scan.skipped["ignored_dirs"] >= 8


def test_sensitive_files_are_never_read(tmp_path: Path) -> None:
    write(tmp_path / "app.js", "console.log('x')\n")
    write(tmp_path / ".env", "OPENAI_API_KEY=sk-live-real\n")
    write(tmp_path / ".env.production", "SECRET=1\n")
    write(tmp_path / ".env.example", "OPENAI_API_KEY=\n")
    write(tmp_path / "certs/server.pem", "-----BEGIN PRIVATE KEY-----\n")
    scan = RepositoryScanner().scan(tmp_path)
    paths = {f.path for f in scan.files}
    assert ".env" not in paths and ".env.production" not in paths and "certs/server.pem" not in paths
    assert ".env.example" in paths
    assert set(scan.sensitive_files) == {".env", ".env.production", "certs/server.pem"}


def test_sha256_matches_file_bytes(demo_copy: Path) -> None:
    scan = RepositoryScanner().scan(demo_copy)
    for scanned in scan.files:
        assert scanned.sha256 == hashlib.sha256((demo_copy / scanned.path).read_bytes()).hexdigest()


def test_symlinks_binaries_and_large_files_are_skipped(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret")
    repo = tmp_path / "repo"
    write(repo / "ok.py")
    os.symlink(outside, repo / "link.txt")
    (repo / "image.js").write_bytes(b"\x00\x01binary")
    write(repo / "huge.js", "x" * 2048)
    scan = RepositoryScanner(max_file_bytes=1024).scan(repo)
    assert [f.path for f in scan.files] == ["ok.py"]
    assert scan.skipped["symlinks"] == 1
    assert scan.skipped["binary"] == 1
    assert scan.large_files == ["huge.js"]


def test_malformed_source_is_decoded_not_dropped(tmp_path: Path) -> None:
    (tmp_path / "legacy.py").write_bytes(b"def f():\n    return '\xff\xfe broken'\n")
    scan = RepositoryScanner().scan(tmp_path)
    assert [(f.path, f.line_count) for f in scan.files] == [("legacy.py", 2)]
    parsed = parse_source("legacy.py", "python", split_lines((tmp_path / "legacy.py").read_bytes().decode("utf-8", "replace")))
    assert [s.name for s in parsed.symbols] == ["f"]


def test_split_lines_handles_crlf_and_trailing_newline() -> None:
    assert split_lines("a\r\nb\rc\n") == ["a", "b", "c"]
    assert split_lines("") == []
    assert split_lines("one") == ["one"]


def test_zip_extraction_collapses_single_root(demo_zip: Path, tmp_path: Path) -> None:
    report = extract_zip(demo_zip, tmp_path / "out", max_total_bytes=10 * 1024 * 1024)
    assert report.root.name == "shoplite-main"
    assert (report.root / "backend/payments.js").is_file()


@pytest.mark.parametrize("member", ["../evil.js", "/etc/evil.js", "a/../../evil.js"])
def test_zip_extraction_rejects_path_traversal(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ok.js", "1")
        zf.writestr(member, "pwned")
    with pytest.raises(ArchiveError):
        extract_zip(archive, tmp_path / "out", max_total_bytes=1024 * 1024)
    assert not (tmp_path / "evil.js").exists()


def test_zip_extraction_rejects_invalid_and_oversized(tmp_path: Path) -> None:
    garbage = tmp_path / "bad.zip"
    garbage.write_bytes(b"this is not a zip")
    with pytest.raises(ArchiveError, match="not a valid ZIP"):
        extract_zip(garbage, tmp_path / "out", max_total_bytes=1024)
    big = tmp_path / "big.zip"
    with zipfile.ZipFile(big, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.js", "0" * 50_000)
    with pytest.raises(ArchiveError, match="too large"):
        extract_zip(big, tmp_path / "out2", max_total_bytes=10_000)


def test_zip_symlink_entries_are_skipped(tmp_path: Path) -> None:
    archive = tmp_path / "links.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("real.js", "1")
        info = zipfile.ZipInfo("link.js")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    report = extract_zip(archive, tmp_path / "out", max_total_bytes=1024 * 1024)
    assert report.skipped_symlinks == 1
    assert not (tmp_path / "out/link.js").exists()


def test_parser_extracts_symbols_routes_and_calls(demo_copy: Path) -> None:
    lines = split_lines((demo_copy / "backend/auth.js").read_text())
    parsed = parse_source("backend/auth.js", "javascript", lines)
    names = {s.name for s in parsed.symbols}
    assert {"hashPassword", "checkPasswordStrength", "issueToken", "requireAuth", "legacyVerify"} <= names
    require_auth = parsed.find_symbol("requireAuth")
    assert require_auth is not None and lines[require_auth.line_end - 1].strip() == "}"
    routes = {(r.method, r.path) for r in parsed.routes}
    assert {("POST", "/api/login"), ("POST", "/api/register"), ("GET", "/api/profile")} <= routes
    profile = next(r for r in parsed.routes if r.path == "/api/profile")
    assert profile.middlewares == ("requireAuth",)

    frontend = parse_source("frontend/auth.js", "javascript", split_lines((demo_copy / "frontend/auth.js").read_text()))
    assert [(c.method, c.target, c.via) for c in frontend.api_calls] == [("POST", "/api/login", "postJSON")]
    assert frontend.uses_dom


def test_parser_handles_python() -> None:
    source = [
        "from fastapi import FastAPI",
        "app = FastAPI()",
        "",
        "@app.get('/items/{item_id}')",
        "async def read_item(item_id: int):",
        "    return {'id': item_id}",
        "",
        "class Service:",
        "    def run(self):",
        "        return 1",
    ]
    parsed = parse_source("main.py", "python", source)
    assert [(s.name, s.kind, s.line_start, s.line_end) for s in parsed.symbols] == [
        ("read_item", "function", 5, 6), ("Service", "class", 8, 10), ("run", "method", 9, 10)]
    assert parsed.routes[0].method == "GET" and parsed.routes[0].handler == "read_item"


def test_parse_github_url() -> None:
    ref = parse_github_url("https://github.com/octocat/Hello-World")
    assert ref.full_name == "octocat/Hello-World" and ref.ref is None
    assert ref.archive_url() == "https://github.com/octocat/Hello-World/archive/HEAD.zip"
    assert ref.archive_url(authenticated=True) == "https://api.github.com/repos/octocat/Hello-World/zipball"
    assert parse_github_url("https://github.com/o/r/tree/main").ref == "main"
    assert parse_github_url("https://github.com/o/r.git").repo == "r"
    for bad in ("https://evil.com/o/r", "github.com/o/r", "https://github.com/o", "file:///etc/passwd",
                "https://github.com/o/r/../../x"):
        with pytest.raises(GitHubError):
            parse_github_url(bad)
