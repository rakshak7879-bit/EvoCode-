"""Shared text + hashing helpers.

Every component (scanner, memory indexer, verification, source viewer) uses the
same decoding, line splitting and hashing rules so that line numbers and
SHA-256 values always agree with each other.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def decode_source(data: bytes) -> str:
    """Decode source bytes as UTF-8, tolerating malformed input."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def split_lines(text: str) -> list[str]:
    """Split text into lines the way editors number them (LF, CRLF or CR)."""
    if not text:
        return []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def hash_lines(lines: Sequence[str], start: int, end: int) -> str:
    """SHA-256 of the 1-based inclusive line range (trailing whitespace ignored)."""
    segment = [line.rstrip() for line in lines[max(start, 1) - 1 : end]]
    return sha256_text("\n".join(segment))


def is_probably_binary(sample: bytes) -> bool:
    return b"\x00" in sample
