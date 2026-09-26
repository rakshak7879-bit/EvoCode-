"""Repository scanner: discovers, classifies and fingerprints source files.

The scanner walks a working copy without following symlinks, prunes ignored
directories (``.git``, ``node_modules``, virtualenvs, build output ...), skips
sensitive files such as ``.env`` without reading them, skips binaries and
oversized files, and computes a SHA-256 for every file it accepts.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from repo.filters import IGNORED_DIRS, classify_file, is_generated_file, is_sensitive_file
from repo.text import decode_source, is_probably_binary, sha256_bytes, split_lines


@dataclass(frozen=True)
class ScannedFile:
    path: str
    language: str
    kind: str
    size: int
    sha256: str
    line_count: int
    tags: tuple[str, ...] = ()


@dataclass
class ScanResult:
    root: Path
    files: list[ScannedFile] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)
    sensitive_files: list[str] = field(default_factory=list)
    large_files: list[str] = field(default_factory=list)

    @property
    def languages(self) -> dict[str, int]:
        counts = Counter(f.language for f in self.files)
        return dict(counts.most_common())

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def total_lines(self) -> int:
        return sum(f.line_count for f in self.files)

    def special(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {
            "readme": [],
            "manifests": [],
            "dockerfiles": [],
            "configs": [],
            "entrypoints": [],
            "tests": [],
        }
        tag_to_group = {
            "readme": "readme",
            "manifest": "manifests",
            "dockerfile": "dockerfiles",
            "compose": "dockerfiles",
            "config": "configs",
            "entrypoint": "entrypoints",
            "test": "tests",
        }
        for f in self.files:
            for tag in f.tags:
                group = tag_to_group.get(tag)
                if group and f.path not in groups[group]:
                    groups[group].append(f.path)
        return groups

    def to_stats(self) -> dict[str, object]:
        kinds = Counter(f.kind for f in self.files)
        return {
            "files": len(self.files),
            "source_files": kinds.get("source", 0),
            "doc_files": kinds.get("doc", 0),
            "config_files": kinds.get("config", 0) + kinds.get("manifest", 0),
            "total_bytes": self.total_bytes,
            "total_lines": self.total_lines,
            "languages": self.languages,
            "special": self.special(),
            "skipped": dict(self.skipped),
            "sensitive_files_skipped": self.sensitive_files[:50],
            "large_files_skipped": self.large_files[:50],
        }


class RepositoryScanner:
    def __init__(self, max_file_bytes: int = 512 * 1024, max_files: int = 3000) -> None:
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files

    def scan(self, root: Path) -> ScanResult:
        root = root.resolve()
        result = ScanResult(root=root)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(dirpath)
            kept: list[str] = []
            for name in sorted(dirnames):
                full = current / name
                if full.is_symlink():
                    result.skipped["symlinks"] += 1
                elif name in IGNORED_DIRS or (full / "pyvenv.cfg").exists():
                    result.skipped["ignored_dirs"] += 1
                else:
                    kept.append(name)
            dirnames[:] = kept
            for name in sorted(filenames):
                self._consider(root, current / name, result)
        result.files.sort(key=lambda f: f.path)
        return result

    def _consider(self, root: Path, full: Path, result: ScanResult) -> None:
        rel = full.relative_to(root).as_posix()
        if full.is_symlink():
            result.skipped["symlinks"] += 1
            return
        if is_sensitive_file(full.name):
            # Never read sensitive files; only record that they exist.
            result.sensitive_files.append(rel)
            result.skipped["sensitive"] += 1
            return
        if is_generated_file(full.name):
            result.skipped["generated"] += 1
            return
        classification = classify_file(rel)
        if classification is None:
            result.skipped["unsupported"] += 1
            return
        try:
            size = full.stat().st_size
        except OSError:
            result.skipped["unreadable"] += 1
            return
        if size > self.max_file_bytes:
            result.skipped["large"] += 1
            result.large_files.append(rel)
            return
        if len(result.files) >= self.max_files:
            result.skipped["file_limit"] += 1
            return
        try:
            data = full.read_bytes()
        except OSError:
            result.skipped["unreadable"] += 1
            return
        if is_probably_binary(data[:8192]):
            result.skipped["binary"] += 1
            return
        lines = split_lines(decode_source(data))
        result.files.append(
            ScannedFile(
                path=rel,
                language=classification.language,
                kind=classification.kind,
                size=size,
                sha256=sha256_bytes(data),
                line_count=len(lines),
                tags=classification.tags,
            )
        )
