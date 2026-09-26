"""Safe ZIP extraction and working-copy creation for untrusted repositories.

Protections:
- rejects absolute paths and ``..`` traversal (zip-slip)
- skips symlink entries entirely
- caps entry count, per-member size and total extracted size (zip bombs)
- skips ignored directories (node_modules, .git, ...) while extracting
- never executes anything from the archive
"""

from __future__ import annotations

import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from repo.filters import IGNORED_DIRS, is_sensitive_file
from repo.paths import UnsafePathError, normalize_relpath

_JUNK_NAMES = frozenset({".DS_Store", "Thumbs.db"})


class ArchiveError(ValueError):
    """The archive is invalid, unsafe or exceeds limits."""


@dataclass
class ExtractionReport:
    root: Path
    files: int = 0
    bytes: int = 0
    skipped_symlinks: int = 0
    skipped_ignored: int = 0
    skipped_large: int = 0


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _collapse_single_root(root: Path) -> Path:
    """GitHub-style archives wrap everything in one top-level folder; descend into it."""
    entries = [p for p in root.iterdir() if p.name not in _JUNK_NAMES and p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
        return entries[0]
    return root


def extract_zip(
    archive: Path,
    destination: Path,
    *,
    max_total_bytes: int,
    max_entries: int = 20_000,
    max_member_bytes: int = 25 * 1024 * 1024,
) -> ExtractionReport:
    if not zipfile.is_zipfile(archive):
        raise ArchiveError("The uploaded file is not a valid ZIP archive.")
    destination.mkdir(parents=True, exist_ok=True)
    dest_real = destination.resolve()
    report = ExtractionReport(root=dest_real)
    declared_total = 0
    try:
        with zipfile.ZipFile(archive) as zf:
            members = zf.infolist()
            if len(members) > max_entries:
                raise ArchiveError(
                    f"The archive has too many entries ({len(members)} > {max_entries})."
                )
            for info in members:
                name = info.filename
                if info.is_dir() or name.startswith("__MACOSX/"):
                    continue
                if PurePosixPath(name).name in _JUNK_NAMES:
                    continue
                if info.flag_bits & 0x1:
                    raise ArchiveError("Encrypted ZIP archives are not supported.")
                if _is_symlink(info):
                    report.skipped_symlinks += 1
                    continue
                try:
                    rel = normalize_relpath(name)
                except UnsafePathError as exc:
                    raise ArchiveError(f"Unsafe path in archive: {name!r} ({exc}).") from exc
                parts = rel.split("/")
                if any(part in IGNORED_DIRS for part in parts[:-1]):
                    report.skipped_ignored += 1
                    continue
                if info.file_size > max_member_bytes:
                    report.skipped_large += 1
                    continue
                declared_total += info.file_size
                if declared_total > max_total_bytes:
                    raise ArchiveError(
                        "The archive is too large when extracted "
                        f"(limit {max_total_bytes // (1024 * 1024)} MB)."
                    )
                target = dest_real.joinpath(*parts)
                if not target.resolve().is_relative_to(dest_real):
                    raise ArchiveError(f"Unsafe path in archive: {name!r}.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink, 1024 * 1024)
                report.files += 1
                report.bytes += info.file_size
    except zipfile.BadZipFile as exc:
        raise ArchiveError("The uploaded file is not a valid ZIP archive.") from exc
    except (NotImplementedError, RuntimeError, EOFError) as exc:
        raise ArchiveError(f"The archive could not be extracted: {exc}") from exc
    if report.files == 0:
        raise ArchiveError("The archive does not contain any files.")
    report.root = _collapse_single_root(dest_real)
    return report


def copy_working_copy(source: Path, destination: Path) -> Path:
    """Copy a local directory (the bundled demo repo) into an isolated working copy."""

    def _ignore(_directory: str, names: list[str]) -> set[str]:
        return {n for n in names if n in IGNORED_DIRS or is_sensitive_file(n)}

    shutil.copytree(source, destination, symlinks=True, ignore=_ignore)
    return destination.resolve()
