"""Safe path handling for untrusted repository content.

Repository-relative paths are normalized to POSIX form and resolved strictly
inside the repository root. Absolute paths, ``..`` traversal and symlinks are
rejected so an uploaded repository can never make Evo Code read or write
outside its own working copy.
"""

from __future__ import annotations

import re
from pathlib import Path

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class UnsafePathError(ValueError):
    """Raised when a path would escape the repository root."""


def normalize_relpath(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UnsafePathError("Empty path")
    if "\x00" in value:
        raise UnsafePathError("Null byte in path")
    candidate = value.replace("\\", "/").strip()
    if candidate.startswith("/") or _WINDOWS_DRIVE.match(candidate):
        raise UnsafePathError("Absolute paths are not allowed")
    parts: list[str] = []
    for part in candidate.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafePathError("Parent directory traversal is not allowed")
        parts.append(part)
    if not parts:
        raise UnsafePathError("Empty path")
    return "/".join(parts)


def resolve_in_root(root: Path, relpath: str) -> Path:
    """Resolve ``relpath`` inside ``root``; never follows symlinks."""
    rel = normalize_relpath(relpath)
    root_real = root.resolve(strict=True)
    current = root_real
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            raise UnsafePathError("Symlinks are not followed")
    resolved = current.resolve()
    if not resolved.is_relative_to(root_real):
        raise UnsafePathError("Path escapes the repository root")
    return resolved
