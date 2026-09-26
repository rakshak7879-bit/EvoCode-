"""Identifier-aware tokenization shared by the indexer and the search engine.

FTS5's unicode61 tokenizer keeps ``verifyToken`` as one token and splits
``API_KEY`` into ``api`` + ``key``. To let natural-language queries such as
"token verification" match camelCase code, the indexer stores the split parts
of compound identifiers in a dedicated ``terms`` column.
"""

from __future__ import annotations

import re

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def split_identifier(name: str) -> list[str]:
    parts: list[str] = []
    for chunk in name.split("_"):
        if chunk:
            parts.extend(match.lower() for match in _CAMEL_PART.findall(chunk))
    return parts


def is_compound_identifier(token: str) -> bool:
    core = token.strip("_")
    if "_" in core:
        return True
    return not core.islower() and not core.isupper() and any(c.isupper() for c in core[1:])


def expand_terms(text: str, limit: int = 400) -> str:
    """Split parts of compound identifiers in ``text`` (deduplicated, space separated)."""
    seen: set[str] = set()
    out: list[str] = []
    for token in _IDENTIFIER.findall(text):
        if not is_compound_identifier(token):
            continue
        for part in split_identifier(token):
            if len(part) > 1 and part not in seen:
                seen.add(part)
                out.append(part)
                if len(out) >= limit:
                    return " ".join(out)
    return " ".join(out)
