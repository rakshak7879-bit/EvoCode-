"""Safe source-file access shared by the HTTP API and command-line interface.

Only files already present in the repository index can be read or edited. Every
path is resolved through ``resolve_in_root`` and symlinks are refused. Edits are
single-line, UTF-8-only operations on Evo Code's isolated working copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from memory.store import FileRecord, MemoryStore
from repo.paths import UnsafePathError, normalize_relpath, resolve_in_root
from repo.text import decode_source, sha256_bytes, split_lines


class SourceAccessError(ValueError):
    """A user-facing source access error with a corresponding HTTP status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SourceDocument:
    record: FileRecord
    path: Path
    data: bytes
    content: str
    lines: list[str]
    sha256_current: str

    @property
    def changed(self) -> bool:
        return self.sha256_current != self.record.sha256


@dataclass(frozen=True)
class SourceEditResult:
    path: str
    line: int
    previous_content: str
    sha256_before: str
    sha256_after: str


def resolve_indexed_file(store: MemoryStore, repo_id: str, root: Path, path: str) -> tuple[FileRecord, Path]:
    try:
        relpath = normalize_relpath(path)
    except UnsafePathError as exc:
        raise SourceAccessError(f"Invalid path: {exc}") from exc
    record = store.get_file(repo_id, relpath)
    if record is None:
        raise SourceAccessError("File is not part of the indexed repository.", 404)
    try:
        resolved = resolve_in_root(root, record.path)
    except (UnsafePathError, OSError) as exc:
        raise SourceAccessError("Unsafe path.") from exc
    if not resolved.is_file():
        raise SourceAccessError("File no longer exists in the working copy.", 404)
    return record, resolved


def read_indexed_source(
    store: MemoryStore,
    repo_id: str,
    root: Path,
    path: str,
    *,
    max_bytes: int = 2 * 1024 * 1024,
) -> SourceDocument:
    record, resolved = resolve_indexed_file(store, repo_id, root, path)
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise SourceAccessError(f"Could not read source: {exc}", 404) from exc
    if len(data) > max_bytes:
        raise SourceAccessError("File is too large to display.", 413)
    lines = split_lines(decode_source(data))
    return SourceDocument(record, resolved, data, "\n".join(lines), lines, sha256_bytes(data))


def replace_indexed_lines(
    store: MemoryStore,
    repo_id: str,
    root: Path,
    path: str,
    line_start: int,
    expected: list[str],
    replacement: list[str],
) -> SourceEditResult:
    """Replace ``expected`` lines starting at ``line_start`` with ``replacement`` (same count).

    Used to apply reviewed Fixer patches. Raises a 409 ``SourceAccessError`` if
    the current lines no longer match ``expected`` (the file changed meanwhile).
    """
    if len(expected) != len(replacement) or not expected:
        raise SourceAccessError("A patch must replace the same number of lines.")
    if any("\n" in line or "\r" in line for line in replacement):
        raise SourceAccessError("Replacement lines must not contain line breaks.")
    record, resolved = resolve_indexed_file(store, repo_id, root, path)
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise SourceAccessError(f"Could not read source: {exc}", 404) from exc
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = (data[3:] if bom else data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceAccessError("Only UTF-8 files can be patched.") from exc
    lines = split_lines(text)
    end = line_start - 1 + len(expected)
    if line_start < 1 or end > len(lines) or lines[line_start - 1 : end] != expected:
        raise SourceAccessError(f"{record.path}:{line_start} changed since the patch was proposed.", 409)
    newline = "\r\n" if "\r\n" in text else "\n"
    previous = lines[line_start - 1]
    lines[line_start - 1 : end] = replacement
    updated = newline.join(lines) + (newline if text.endswith(("\n", "\r")) else "")
    payload = (b"\xef\xbb\xbf" if bom else b"") + updated.encode("utf-8")
    try:
        resolved.write_bytes(payload)
    except OSError as exc:
        raise SourceAccessError(f"Could not write source: {exc}") from exc
    return SourceEditResult(record.path, line_start, previous, sha256_bytes(data), sha256_bytes(payload))


def edit_indexed_source(
    store: MemoryStore,
    repo_id: str,
    root: Path,
    path: str,
    line: int,
    content: str,
) -> SourceEditResult:
    if "\n" in content or "\r" in content:
        raise SourceAccessError("Replacement must be a single line.")
    if line < 1:
        raise SourceAccessError("Line number must be at least 1.")
    record, resolved = resolve_indexed_file(store, repo_id, root, path)
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise SourceAccessError(f"Could not read source: {exc}", 404) from exc
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = (data[3:] if bom else data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceAccessError("Only UTF-8 files can be edited.") from exc
    lines = split_lines(text)
    if line > len(lines):
        raise SourceAccessError(f"Line {line} is outside the file ({len(lines)} lines).")
    newline = "\r\n" if "\r\n" in text else "\n"
    previous = lines[line - 1]
    lines[line - 1] = content
    updated = newline.join(lines) + (newline if text.endswith(("\n", "\r")) else "")
    payload = (b"\xef\xbb\xbf" if bom else b"") + updated.encode("utf-8")
    try:
        resolved.write_bytes(payload)
    except OSError as exc:
        raise SourceAccessError(f"Could not write source: {exc}") from exc
    return SourceEditResult(record.path, line, previous, sha256_bytes(data), sha256_bytes(payload))
