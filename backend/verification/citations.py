"""Citation anchoring and SHA-256 source verification.

Two operations:

``anchor``  (analysis time) checks that a claimed file/line exists, that the
            claimed evidence really appears there (relocating it if the line
            number is off), that the file still matches the indexed SHA-256,
            and records the SHA-256 of the cited lines.

``verify``  (any later time) re-reads the file and compares the current
            SHA-256 of the file and of the cited lines with the recorded
            values. Cited lines unchanged -> VERIFIED, changed -> STALE.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from repo.paths import UnsafePathError, resolve_in_root
from repo.text import decode_source, hash_lines, sha256_bytes, split_lines

REDACTION_MARKER = "[REDACTED]"
_WHITESPACE = re.compile(r"\s+")


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    STALE = "stale"
    MISSING = "missing"
    INVALID_LINE = "invalid_line"
    UNSUPPORTED = "unsupported"
    UNSAFE = "unsafe_path"


STATUS_SEVERITY = {
    VerificationStatus.VERIFIED: 0,
    VerificationStatus.STALE: 1,
    VerificationStatus.INVALID_LINE: 2,
    VerificationStatus.MISSING: 3,
    VerificationStatus.UNSUPPORTED: 4,
    VerificationStatus.UNSAFE: 5,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def evidence_matches(evidence: str | None, cited_text: str) -> bool:
    """True when ``evidence`` appears in ``cited_text`` (whitespace-insensitive).

    Redacted secrets (``[REDACTED]``) act as wildcards and a trailing ellipsis
    from truncation is ignored.
    """
    if not evidence:
        return True
    normalized_evidence = _normalize(evidence).removesuffix("…").strip()
    haystack = _normalize(cited_text)
    parts = [p.strip() for p in normalized_evidence.split(REDACTION_MARKER) if p.strip()]
    if not parts:
        return True
    position = 0
    for part in parts:
        index = haystack.find(part, position)
        if index < 0:
            return False
        position = index + len(part)
    return True


@dataclass
class SourceSnapshot:
    path: str
    exists: bool
    sha256: str | None = None
    lines: list[str] | None = None
    error: str | None = None


@dataclass
class Anchor:
    status: VerificationStatus
    file: str
    line_start: int
    line_end: int
    evidence_sha256: str
    file_sha256: str
    relocated_from: int | None = None
    message: str = ""


@dataclass
class VerificationResult:
    status: VerificationStatus
    file: str
    line_start: int
    line_end: int
    file_sha256_indexed: str | None
    file_sha256_current: str | None
    evidence_sha256_expected: str | None
    evidence_sha256_current: str | None
    file_changed: bool
    lines_changed: bool
    relocated_line: int | None
    message: str
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = str(self.status)
        return data


def worst_status(statuses: list[VerificationStatus]) -> VerificationStatus:
    if not statuses:
        return VerificationStatus.VERIFIED
    return max(statuses, key=lambda s: STATUS_SEVERITY[s])


class SourceVerifier:
    """Reads repository files safely (with a per-pass cache) and verifies citations."""

    def __init__(self, root: Path, max_bytes: int = 5 * 1024 * 1024) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self._cache: dict[str, SourceSnapshot] = {}

    def snapshot(self, path: str) -> SourceSnapshot:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        try:
            resolved = resolve_in_root(self.root, path)
        except UnsafePathError as exc:
            snap = SourceSnapshot(path, False, error=f"unsafe: {exc}")
        except OSError as exc:
            snap = SourceSnapshot(path, False, error=str(exc))
        else:
            if not resolved.is_file():
                snap = SourceSnapshot(path, False, error="file not found")
            else:
                try:
                    data = resolved.read_bytes()[: self.max_bytes + 1]
                except OSError as exc:
                    snap = SourceSnapshot(path, False, error=str(exc))
                else:
                    snap = SourceSnapshot(path, True, sha256_bytes(data), split_lines(decode_source(data)))
        self._cache[path] = snap
        return snap

    # ------------------------------------------------------------------ analysis time
    def anchor(
        self,
        path: str,
        line_start: int,
        line_end: int | None = None,
        evidence: str | None = None,
        indexed_sha256: str | None = None,
    ) -> Anchor:
        line_end = max(line_end or line_start, line_start)
        snap = self.snapshot(path)
        if not snap.exists or snap.lines is None:
            status = VerificationStatus.UNSAFE if (snap.error or "").startswith("unsafe") else VerificationStatus.MISSING
            return Anchor(status, path, line_start, line_end, "", "", message=f"Cited file unavailable ({snap.error}).")
        lines = snap.lines
        span = line_end - line_start
        relocated_from: int | None = None
        in_range = 1 <= line_start <= len(lines)
        if in_range:
            line_end = min(line_end, len(lines))
        cited_ok = in_range and evidence_matches(evidence, "\n".join(lines[line_start - 1 : line_end]))
        if not cited_ok and evidence:
            found = self._relocate_by_evidence(lines, evidence, span + 1)
            if found is not None:
                relocated_from = line_start
                line_start, line_end = found, min(len(lines), found + span)
                cited_ok = True
        if not in_range and relocated_from is None:
            return Anchor(VerificationStatus.INVALID_LINE, path, line_start, line_end, "", snap.sha256 or "",
                          message=f"Line {line_start} is outside the file ({len(lines)} lines).")
        if not cited_ok:
            return Anchor(VerificationStatus.UNSUPPORTED, path, line_start, line_end, "", snap.sha256 or "",
                          message="The claimed evidence does not appear in the cited source.")
        evidence_sha = hash_lines(lines, line_start, line_end)
        if indexed_sha256 and snap.sha256 != indexed_sha256:
            return Anchor(VerificationStatus.STALE, path, line_start, line_end, evidence_sha, snap.sha256 or "",
                          relocated_from, "File changed after it was indexed. Re-run analysis.")
        message = "Evidence confirmed at the cited lines."
        if relocated_from is not None:
            message = f"Citation corrected from line {relocated_from} to line {line_start}."
        return Anchor(VerificationStatus.VERIFIED, path, line_start, line_end, evidence_sha, snap.sha256 or "",
                      relocated_from, message)

    @staticmethod
    def _relocate_by_evidence(lines: list[str], evidence: str, span: int) -> int | None:
        first_line = _normalize(evidence.splitlines()[0] if evidence.splitlines() else evidence)
        if len(first_line) < 6:
            return None
        for index in range(len(lines)):
            window = "\n".join(lines[index : index + max(span, 1)])
            if evidence_matches(evidence, window):
                return index + 1
        return None

    # ------------------------------------------------------------------ any time
    def verify(
        self,
        path: str,
        line_start: int,
        line_end: int | None,
        expected_evidence_sha256: str | None,
        indexed_file_sha256: str | None,
    ) -> VerificationResult:
        line_end = max(line_end or line_start, line_start)
        snap = self.snapshot(path)
        checked_at = utc_now()
        if not snap.exists or snap.lines is None:
            status = VerificationStatus.UNSAFE if (snap.error or "").startswith("unsafe") else VerificationStatus.MISSING
            return VerificationResult(status, path, line_start, line_end, indexed_file_sha256, None,
                                      expected_evidence_sha256, None, True, True, None,
                                      "Source file no longer exists in the repository.", checked_at)
        lines = snap.lines
        file_changed = bool(indexed_file_sha256) and snap.sha256 != indexed_file_sha256
        if line_start > len(lines):
            relocated = self._relocate_by_hash(lines, expected_evidence_sha256, line_end - line_start + 1)
            return VerificationResult(VerificationStatus.STALE, path, line_start, line_end, indexed_file_sha256,
                                      snap.sha256, expected_evidence_sha256, None, True, True, relocated,
                                      "Cited lines no longer exist. Source changed after this finding was generated.",
                                      checked_at)
        current_evidence = hash_lines(lines, line_start, line_end)
        lines_changed = bool(expected_evidence_sha256) and current_evidence != expected_evidence_sha256
        if lines_changed:
            relocated = self._relocate_by_hash(lines, expected_evidence_sha256, line_end - line_start + 1)
            message = "Source changed after this finding was generated. Re-run analysis."
            if relocated:
                message = f"Cited code moved to line {relocated}. Re-run analysis to refresh the citation."
            return VerificationResult(VerificationStatus.STALE, path, line_start, line_end, indexed_file_sha256,
                                      snap.sha256, expected_evidence_sha256, current_evidence, file_changed, True,
                                      relocated, message, checked_at)
        message = "Source confirmed against current repository."
        if file_changed:
            message = "Cited lines unchanged; other parts of this file changed since indexing."
        return VerificationResult(VerificationStatus.VERIFIED, path, line_start, line_end, indexed_file_sha256,
                                  snap.sha256, expected_evidence_sha256, current_evidence, file_changed, False,
                                  None, message, checked_at)

    @staticmethod
    def _relocate_by_hash(lines: list[str], expected: str | None, span: int) -> int | None:
        if not expected:
            return None
        for index in range(1, len(lines) - span + 2):
            if hash_lines(lines, index, index + span - 1) == expected:
                return index
        return None
